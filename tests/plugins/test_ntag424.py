"""nfc/_ntag424: the card writer. Crypto against NXP AN12196's published example; write_card driven against a
simulated NTAG 424 DNA (its own crypto, written apart from the writer's, checking every MAC) from a factory card
and from a card the house wrote before; then a tap the simulated card mirrors passes the plug-in's own verifier."""
import json
import os
import sys
import urllib.parse
import zlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

import app  # noqa: F401  the plug-ins load with the core

me = sys.modules["yashome_plugins.nfc"]
N = me._ntag424
SDM = me._sdm
URL = "https://house.example.org/nfc?picc={PICC}&cmac={MAC}"
PKG = "com.yash.home"
KEYS = {0: bytes.fromhex("0f" * 16), 1: bytes.fromhex(os.environ["NFC_SDM_META_KEY"]),
        2: bytes.fromhex(os.environ["NFC_SDM_FILE_KEY"])}


# ---- (a) AN12196 (rev. 2.0), section 5: AuthenticateEV2First with the factory key ----
def test_an12196_ev2first_session_keys():
    rnd_a = bytes.fromhex("13C5DB8A5930439FC3DEF9A4C675360F")
    rnd_b = bytes.fromhex("B9E2FC789B64BF237CCCAA20EC7E6E48")
    assert N.aes_cbc(bytes(16), rnd_b).hex().upper() == "A04C124213C186F22399D33AC2A30215"
    enc, mac = N.session_keys(bytes(16), rnd_a, rnd_b)
    assert enc.hex().upper() == "1309C877509E5A215007FF0ED19CA564"
    assert mac.hex().upper() == "4C6626F5E72EA694202139295C7A7FC7"


def test_an12196_sdm_example_verifies_with_the_tap_verifier():
    uid, ctr = SDM.verify(bytes(16), bytes(16), bytes.fromhex("EF963FF7828658A599F3041510671E88"),
                          bytes.fromhex("94EED9EE65337086"))
    assert uid.hex().upper() == "04DE5F1EACC040" and ctr == 61


def test_crc_and_padding():
    assert N.jam_crc32(b"123456789") == (0xCBF43926 ^ 0xFFFFFFFF).to_bytes(4, "little")
    assert N.pad(bytes(15)) == bytes(15) + b"\x80" and len(N.pad(bytes(16))) == 32
    assert N.unpad(N.pad(b"abc")) == b"abc"


# ---- (b) a simulated NTAG 424 DNA ----
def _aes(key, data, iv=bytes(16), dec=False):
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = c.decryptor() if dec else c.encryptor()
    return op.update(data) + op.finalize()


def _cmac(key, msg):
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def _sv(p, a, b):
    return p + b"\x00\x01\x00\x80" + a[:2] + bytes(x ^ y for x, y in zip(a[2:8], b[:6])) + b[6:] + a[8:]


class FakeNtag424:
    """The card side: the NDEF application, file 02 with its access rights and SDM settings, five keys, EV2First,
    CommMode Plain / MAC / Full with a strict MAC check (a wrong MAC or any error ends the session)."""

    def __init__(self, keys=None, uid=bytes.fromhex("04a1b2c3d4e5f6")):
        self.keys = dict(keys or {k: bytes(16) for k in range(5)})
        self.vers = {k: 0 for k in range(5)}
        self.uid, self.sdm_ctr = uid, 0
        self.file = bytearray(256)
        self.fs = {"comm": 0, "rw": 0xE, "change": 0, "read": 0xE, "write": 0xE, "sdm": None}   # factory: free NDEF
        self.selected, self.sess, self.pending, self.log = False, None, None, []

    # secure messaging, card side
    def _ctr(self):
        return self.sess["ctr"].to_bytes(2, "little")

    def _mac(self, head, data):
        return _cmac(self.sess["mac"], head + self._ctr() + self.sess["ti"] + data)[1::2]

    def _err(self, sw2):
        self.sess = None
        return bytes([0x91, sw2])

    def _ok(self, data=b"", mode=1):
        """Success: the counter moves on; MAC (and Full: encrypted data) over the answer."""
        self.sess["ctr"] += 1
        if mode == 0:
            return b"\x91\x00"
        if mode == 3 and data:
            iv = _aes(self.sess["enc"], b"\x5a\xa5" + self.sess["ti"] + self._ctr() + bytes(8))
            p = data + b"\x80"
            data = _aes(self.sess["enc"], p + bytes(-len(p) % 16), iv)
        return data + self._mac(b"\x00", data) + b"\x91\x00"

    def _unwrap(self, ins, hdr_len, body, mode):
        """The command data of a MAC / Full command after its MAC is checked; None = refused (session gone)."""
        if mode == 0:
            return body[hdr_len:]
        if len(body) < hdr_len + 8 or self._mac(bytes([ins]), body[:-8]) != body[-8:]:
            return None
        data = body[hdr_len:-8]
        if mode == 3 and data:
            iv = _aes(self.sess["enc"], b"\xa5\x5a" + self.sess["ti"] + self._ctr() + bytes(8))
            plain = _aes(self.sess["enc"], data, iv, dec=True).rstrip(b"\x00")
            if not plain.endswith(b"\x80"):
                return None
            data = plain[:-1]
        return data

    def _may(self, right):
        return right == 0xE or (self.sess is not None and self.sess["key"] == right)

    def _settings_bytes(self):
        fs, out = self.fs, bytearray([0x00, (0x40 if self.fs["sdm"] else 0) | self.fs["comm"],
                                      self.fs["rw"] << 4 | self.fs["change"], self.fs["read"] << 4 | self.fs["write"]])
        out += len(self.file).to_bytes(3, "little")
        if fs["sdm"]:
            s = fs["sdm"]
            out += bytes([s["opts"], s["r2"], s["r1"]]) + s["picc"].to_bytes(3, "little") \
                + s["mac_in"].to_bytes(3, "little") + s["mac"].to_bytes(3, "little")
        return bytes(out)

    def _parse_change(self, d):
        """ChangeFileSettings data: only the shape the house writes is taken (key-read PICC, MAC, no file encryption)."""
        fs = {"comm": d[0] & 3, "rw": d[1] >> 4, "change": d[1] & 15, "read": d[2] >> 4, "write": d[2] & 15, "sdm": None}
        if d[0] & 0x40:
            opts, r2, r1 = d[3], d[4], d[5]
            assert opts == 0xC1 and r1 >> 4 in range(5) and r1 & 15 in range(5) and len(d) == 15, d.hex()
            off = [int.from_bytes(d[i:i + 3], "little") for i in (6, 9, 12)]
            assert off[1] <= off[2] and off[0] + 32 <= len(self.file) and off[2] + 16 <= len(self.file)
            fs["sdm"] = {"opts": opts, "r2": r2, "r1": r1, "picc": off[0], "mac_in": off[1], "mac": off[2]}
        return fs

    def transceive(self, a: bytes) -> bytes:
        a = bytes(a)
        self.log.append(a)
        assert a[-1] == 0 and a[4] == len(a) - 6, f"bad APDU framing {a.hex()}"
        cla, ins, body = a[0], a[1], a[5:-1]
        if cla == 0x00 and ins == 0xA4:
            self.selected = body == b"\xe1\x10"
            return b"\x90\x00" if self.selected else b"\x6a\x82"
        assert cla == 0x90 and self.selected, "native command before the application was selected"
        if ins == 0x71:                                    # AuthenticateEV2First, part 1
            self.sess, k = None, body[0]
            if k > 4:
                return b"\x91\x40"
            self.pending = (k, os.urandom(16))
            return _aes(self.keys[k], self.pending[1]) + b"\x91\xaf"
        if ins == 0xAF:                                    # part 2
            if not self.pending:
                return b"\x91\xca"
            (k, rnd_b), self.pending = self.pending, None
            p = _aes(self.keys[k], body, dec=True)
            if p[16:] != rnd_b[1:] + rnd_b[:1]:
                return b"\x91\xae"
            rnd_a, ti = p[:16], os.urandom(4)
            self.sess = {"key": k, "ti": ti, "ctr": 0, "enc": _cmac(self.keys[k], _sv(b"\xa5\x5a", rnd_a, rnd_b)),
                         "mac": _cmac(self.keys[k], _sv(b"\x5a\xa5", rnd_a, rnd_b))}
            return _aes(self.keys[k], ti + rnd_a[1:] + rnd_a[:1] + bytes(12)) + b"\x91\x00"
        if self.sess is None:
            return b"\x91\xae"                             # the house writer always works inside a session
        if ins == 0xF5:                                    # GetFileSettings (MAC)
            if self._unwrap(ins, 1, body, 1) is None or body[0] != 2:
                return self._err(0x1E)
            return self._ok(self._settings_bytes())
        if ins == 0x8D:                                    # WriteData, in the file's comm mode
            if not (self._may(self.fs["write"]) or self._may(self.fs["rw"])):
                return self._err(0x9D)
            data = self._unwrap(ins, 7, body, self.fs["comm"])
            if data is None:
                return self._err(0x1E)
            off, n = int.from_bytes(body[1:4], "little"), int.from_bytes(body[4:7], "little")
            assert body[0] == 2 and n == len(data) and off + n <= len(self.file)
            self.file[off:off + n] = data
            return self._ok(mode=self.fs["comm"])
        if ins == 0x5F:                                    # ChangeFileSettings (Full)
            if not self._may(self.fs["change"]):
                return self._err(0x9D)
            data = self._unwrap(ins, 1, body, 3)
            if data is None:
                return self._err(0x1E)
            self.fs = self._parse_change(data)
            return self._ok()
        if ins == 0xC4:                                    # ChangeKey (Full), from key 0 only
            data, k = self._unwrap(ins, 1, body, 3), body[0]
            if self.sess["key"] != 0:
                return self._err(0x9D)
            if data is None:
                return self._err(0x1E)
            if k == 0:
                self.keys[0], self.vers[0] = data[:16], data[16]
                self.sess = None                           # the session key changed: the session ends, no MAC back
                return b"\x91\x00"
            new = bytes(x ^ y for x, y in zip(data[:16], self.keys[k]))
            if (zlib.crc32(new) ^ 0xFFFFFFFF).to_bytes(4, "little") != data[17:21]:
                return self._err(0x1E)                     # a wrong old key: the CRC of the "new" key does not match
            self.keys[k], self.vers[k] = new, data[16]
            return self._ok()
        if ins == 0x51:                                    # GetCardUID (Full)
            if self._unwrap(ins, 0, body, 3) is None:
                return self._err(0x1E)
            return self._ok(self.uid, mode=3)
        return self._err(0x1C)

    def tap(self) -> str:
        """An unauthenticated NDEF read: the PICC data and the CMAC mirrored where the settings point; the URL back."""
        s = self.fs["sdm"]
        assert s, "SDM is off"
        self.sdm_ctr += 1
        out = bytearray(self.file)
        meta, fkey = self.keys[s["r1"] >> 4], self.keys[s["r1"] & 15]
        picc = _aes(meta, b"\xc7" + self.uid + self.sdm_ctr.to_bytes(3, "little") + os.urandom(5))
        out[s["picc"]:s["picc"] + 32] = picc.hex().upper().encode()
        ses = _cmac(fkey, b"\x3c\xc3\x00\x01\x00\x80" + self.uid + self.sdm_ctr.to_bytes(3, "little"))
        mac = _cmac(ses, bytes(out[s["mac_in"]:s["mac"]]))[1::2]
        out[s["mac"]:s["mac"] + 16] = mac.hex().upper().encode()
        nlen = int.from_bytes(out[0:2], "big")
        msg = bytes(out[2:2 + nlen])
        assert msg[0] & 0x10 and msg[1] == 1 and msg[3:5] == b"U\x00", msg[:6].hex()
        return msg[4 + 1:4 + msg[2]].decode()


def drive(card, keys=KEYS, url=URL, pkg=PKG, state=None):
    gen = N.write_card(keys, url, pkg, state)
    try:
        cmd = next(gen)
        while True:
            cmd = gen.send(card.transceive(cmd))
    except StopIteration as e:
        return e.value


def tap_ok(card, keys=KEYS):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(card.tap()).query)
    uid, ctr = SDM.verify(keys[1], keys[2], bytes.fromhex(q["picc"][0]), bytes.fromhex(q["cmac"][0]))
    return uid, ctr


def test_a_factory_card_is_written_and_its_tap_passes_the_house_verifier():
    card, state = FakeNtag424(), {}
    r = drive(card, state=state)
    assert r == {"ok": True, "step": "done", "uid": card.uid.hex(), "keys": "factory"} and state["step"] == "done"
    assert card.keys[0] == KEYS[0] and card.keys[1] == KEYS[1] and card.keys[2] == KEYS[2]
    assert card.vers[0] == card.vers[1] == card.vers[2] == N.KEY_VERSION and card.keys[3] == bytes(16)
    assert (card.fs["rw"], card.fs["change"], card.fs["read"], card.fs["write"]) == (2, 0, 2, 2)
    assert tap_ok(card) == (card.uid, 1) and tap_ok(card) == (card.uid, 2)
    url = card.tap()
    assert url.startswith("https://house.example.org/nfc?picc=") and "&cmac=" in url


def test_a_card_the_house_wrote_is_written_again():
    card = FakeNtag424()
    assert drive(card)["ok"]
    tap_ok(card)
    r = drive(card, url=URL.replace("house.example.org", "door.example.org"))
    assert r["ok"] and r["keys"] == "house" and r["uid"] == card.uid.hex()
    assert card.keys[0] == KEYS[0] and card.keys[1] == KEYS[1] and card.keys[2] == KEYS[2]
    assert card.tap().startswith("https://door.example.org/nfc?") and tap_ok(card)[1] == 3


def test_a_half_written_card_is_finished():
    """Keys 1 and 2 already the house's, key 0 still the factory one (a write broken off before the master)."""
    card = FakeNtag424({0: bytes(16), 1: KEYS[1], 2: KEYS[2], 3: bytes(16), 4: bytes(16)})
    r = drive(card)
    assert r["ok"] and r["keys"] == "factory" and card.keys == {**card.keys, **KEYS}
    assert tap_ok(card)[0] == card.uid


def test_a_card_of_another_system_is_refused_untouched():
    other = {k: bytes([0x5A] * 16) for k in range(5)}
    card = FakeNtag424(other)
    r = drive(card)
    assert not r["ok"] and r["step"] == "auth key 0"
    assert "refused the house key and the factory key" in r["error"] and "another system" in r["error"]
    assert card.keys == other and card.fs["sdm"] is None and not any(card.file)


def test_a_lost_card_and_a_wrong_answer_end_with_an_error():
    card = FakeNtag424()
    gen = N.write_card(KEYS, URL, PKG)
    cmd = next(gen)
    cmd = gen.send(card.transceive(cmd))                     # selected; the first auth command is out
    with pytest.raises(StopIteration) as e:
        gen.send(b"")
    assert e.value.value["ok"] is False and "no answer" in e.value.value["error"]
    gen = N.write_card(KEYS, URL, PKG)
    next(gen)
    with pytest.raises(StopIteration) as e:
        gen.send(b"\x6a\x82")
    assert "not an NTAG 424 DNA" in e.value.value["error"]


def test_a_forged_answer_mac_is_caught():
    card = FakeNtag424()
    real = card.transceive

    def evil(a):
        r = real(a)
        return bytes([r[0] ^ 1]) + r[1:] if a[1] == 0xF5 else r     # GetFileSettings answer tampered
    card.transceive = evil
    r = drive(card)
    assert not r["ok"] and "wrong MAC" in r["error"] and r["step"] == "file settings"


def test_bad_inputs_never_reach_the_card():
    card = FakeNtag424()
    assert "{PICC} and {MAC}" in drive(card, url="https://house.example.org/nfc")["error"]
    assert "AES-128" in drive(card, keys={0: b"x", 1: KEYS[1], 2: KEYS[2]})["error"]
    assert "too long" in drive(card, url=URL + "&x=" + "a" * 260)["error"]
    assert card.log == []


# ---- (c) the NDEF file ----
def test_the_ndef_file_is_the_url_record_then_the_aar():
    url_only, picc, mac = N.ndef_template(URL)
    full, picc2, mac2 = N.card_file(URL, PKG)
    assert (picc2, mac2) == (picc, mac) and full[3:len(url_only)] == url_only[3:]
    # the URL record: NLEN, D1 (MB ME SR TNF 1) 01 len 'U' 00 url; the placeholders become '*' runs at the offsets
    assert url_only[2] == 0xD1 and full[2] == 0x91                # ME off: the AAR follows
    assert url_only[5:7] == b"U\x00" and url_only[4] == len(url_only) - 6
    text = url_only[7:].decode()
    assert text == URL.replace("{PICC}", "*" * 32).replace("{MAC}", "*" * 16)
    assert full[picc:picc + 32] == b"*" * 32 and full[mac:mac + 16] == b"*" * 16
    assert full[picc - 5:picc] == b"picc=" and full[mac - 5:mac] == b"cmac="
    aar = full[len(url_only):]
    assert aar == bytes([0x54, 15, len(PKG)]) + b"android.com:pkg" + PKG.encode()
    assert int.from_bytes(full[0:2], "big") == len(full) - 2 == len(url_only) - 2 + len(aar)


def test_the_sdm_settings_bytes():
    fs = N.sdm_settings({"comm": 0, "rw": 0xE, "change": 0, "read": 0xE, "write": 0xE, "size": 256}, 0x20, 0x4C)
    assert N.encode_file_settings(fs).hex() == "402022c1ff12" + "200000" + "4c0000" + "4c0000"
    back = N.decode_file_settings(b"\x00" + N.encode_file_settings(fs)[:3] + b"\x00\x01\x00"
                                  + N.encode_file_settings(fs)[3:])
    assert back["sdm"]["picc_off"] == 0x20 and back["sdm"]["mac_off"] == back["sdm"]["mac_in_off"] == 0x4C


# ---- the write session over the API ----
from fastapi.testclient import TestClient  # noqa: E402

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(me, "_MASTER_KEY", KEYS[0])
    monkeypatch.setitem(me._settings, "card_url", URL)
    monkeypatch.setattr(me, "_CARDS", {})
    monkeypatch.setattr(me, "_write_cards", lambda cards: None)
    monkeypatch.setattr(me.core, "update_device", lambda d: None)
    monkeypatch.setattr(me.core, "save_settings", lambda: None)
    told = []
    monkeypatch.setattr(me.core, "NFC_WRITE", [told.append])
    me._w.clear()
    c = TestClient(me.core.app)
    c.told = told
    yield c
    me._w.clear()


def run_session(c, card, sid, headers=HOUSE):
    r = c.post(f"/api/nfc/write/{sid}/step", json={}, headers=headers).json()
    n = 0
    while "apdu_hex" in r:
        n += 1
        r = c.post(f"/api/nfc/write/{sid}/step", headers=headers,
                   json={"resp_hex": card.transceive(bytes.fromhex(r["apdu_hex"])).hex()}).json()
    assert n > 10, r
    return r


def test_the_house_writes_a_card_through_the_step_loop_and_lists_it(api):
    r = api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE)
    assert r.status_code == 200, r.text
    sid = r.json()["sid"]
    assert api.told == [sid]
    assert api.post("/api/nfc/write/start", json={"name": "Other"}, headers=HOUSE).status_code == 409   # one at a time
    card = FakeNtag424()
    r = run_session(api, card, sid)
    assert r["done"] and r["result"]["ok"] and r["result"]["listed"] == "Gate"
    assert me._CARDS == {card.uid.hex(): "Gate"} and tap_ok(card)[0] == card.uid
    v = api.get(f"/api/nfc/write/{sid}", headers=HOUSE).json()
    assert v["done"] and v["step"] == "done" and v["result"]["uid"] == card.uid.hex()
    text = json.dumps(v) + json.dumps(api.get("/api/nfc", headers=HOUSE).json())
    assert all(k.hex() not in text.lower() for k in KEYS.values())                 # nothing secret comes back


def test_only_an_administrator_starts_a_write(api, monkeypatch):
    phone = {"Authorization": "Bearer yfd_full_access_phone"}
    fam = sys.modules["yashome_plugins.family"]
    monkeypatch.setattr(fam, "_uid_of", lambda tok: "kid" if tok == "yfd_full_access_phone" else None)
    monkeypatch.setattr(fam, "_rights", lambda uid: None)                          # full access, not an administrator
    monkeypatch.delattr(me.core, "is_admin_request", raising=False)
    r = api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=phone)
    assert r.status_code == 403 and me._w == {}
    guest = {"Authorization": "Bearer yg_guest"}
    assert api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=guest).status_code in (401, 403)
    monkeypatch.setattr(me.core, "is_admin_request", lambda req: me.core.request_token(req.headers) != "yfd_full_access_phone",
                        raising=False)
    assert api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=phone).status_code == 403
    assert api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE).status_code == 200
    monkeypatch.setattr(me.core, "is_admin_request", lambda req: True, raising=False)   # an admin phone drives it
    assert run_session(api, FakeNtag424(), me._w["sid"], headers=phone)["result"]["ok"]


def test_no_master_key_no_address_and_the_idle_timeout(api, monkeypatch):
    monkeypatch.setattr(me, "_MASTER_KEY", b"")
    r = api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE)
    assert r.status_code == 503 and "NFC_MASTER_KEY" in r.json()["detail"]
    monkeypatch.setattr(me, "_MASTER_KEY", KEYS[0])
    monkeypatch.delitem(me._settings, "card_url")
    monkeypatch.setenv("DASHBOARD_ORIGINS", "http://192.0.2.1:8765, https://home.example.org/")
    assert me.card_url() == "https://home.example.org/nfc?picc={PICC}&cmac={MAC}"
    monkeypatch.setenv("DASHBOARD_ORIGINS", "")
    assert api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE).status_code == 409
    monkeypatch.setitem(me._settings, "card_url", URL)
    sid = api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE).json()["sid"]
    assert "apdu_hex" in api.post(f"/api/nfc/write/{sid}/step", json={}, headers=HOUSE).json()
    me._w["last"] -= me.WRITE_IDLE_S + 1
    r = api.post(f"/api/nfc/write/{sid}/step", json={"resp_hex": "9000"}, headers=HOUSE).json()
    assert r["done"] and not r["result"]["ok"] and "timed out" in r["result"]["error"]
    assert api.post("/api/nfc/write/start", json={"name": "Gate"}, headers=HOUSE).status_code == 200   # a new one may start
    assert api.post("/api/nfc/write/nope/step", json={}, headers=HOUSE).status_code == 404


def test_the_card_address_setting(api):
    r = api.put("/api/nfc/settings", json={"card_url": "https://door.example.org/nfc?picc={PICC}&cmac={MAC}"}, headers=HOUSE)
    assert r.status_code == 200 and r.json()["card_url"].startswith("https://door.")
    assert api.put("/api/nfc/settings", json={"card_url": "https://door.example.org/nfc"}, headers=HOUSE).status_code == 400
    assert api.put("/api/nfc/settings", json={"card_url": "http://x/nfc?picc={PICC}&cmac={MAC}"}, headers=HOUSE).status_code == 400


def _run(card, gen):
    try:
        cmd = next(gen)
        while True:
            cmd = gen.send(card.transceive(cmd))
    except StopIteration as e:
        return e.value


def test_the_counter_moves_through_plain_mac_and_full_commands_in_one_session():
    """The write flow re-authenticates often (the counter back at 0): here one session runs on to counter 4."""
    card = FakeNtag424()
    card.transceive(N.select_apdu())
    s = _run(card, N.authenticate(0, bytes(16)))
    assert s is not None and s.ctr == 0
    _run(card, N.get_file_settings(s))
    _run(card, N.write_data(s, N.PLAIN, b"\x00\x03abc"))                  # Plain inside a session: counted too
    _run(card, N.get_file_settings(s))
    assert s.ctr == 3 and card.sess["ctr"] == 3
    assert _run(card, N.get_card_uid(s)) == card.uid                       # Full at counter 3: the IV carries it
    assert _run(card, N.change_key(s, 3, bytes(16), bytes([7] * 16)))
    assert card.keys[3] == bytes([7] * 16) and s.ctr == 5
    assert _run(card, N.authenticate(0, bytes([1] * 16))) is None          # a wrong key: refused, not raised
