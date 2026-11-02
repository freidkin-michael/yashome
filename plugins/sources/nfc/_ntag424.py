"""NTAG 424 DNA personalisation (NXP NT4H2421Gx datasheet, AN12196): AES EV2First authentication,
secure messaging (CommMode Plain / MAC / Full), the commands that write a door card, the NDEF
template with the SDM placeholders. Pure: no I/O, no logging. write_card() is a generator that
yields command APDUs and is sent the card's answers, so the house can drive a card one APDU at a
time through a phone; the keys never leave the house (only encrypted or MACed APDUs do).
A port of the flow of the Android writer (ntag424 library by bplearning, EncryptedSunActivity)."""
import os
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

FACTORY_KEY = bytes(16)
KEY_VERSION = 1                     # the version every house key gets (APPLICATION_KEY_VERSION_NEW)
NDEF_FILE = 0x02
DF_FILE_ID = b"\xe1\x10"            # the NDEF application, selected first
KEY0, KEY1, KEY2 = 0, 1, 2
EVERYONE, NOBODY = 0xE, 0xF
PLAIN, MAC, FULL = 0, 1, 3
PICC_PLACEHOLDER, MAC_PLACEHOLDER = b"{PICC}", b"{MAC}"
PICC_LEN, MAC_LEN = 32, 16          # their ASCII hex mirrors: 16 and 8 bytes
AAR_TYPE = b"android.com:pkg"
MAX_LC = 255                        # a short APDU: the card and IsoDep take no extended length here

OK = (0x91, 0x00)
MORE = (0x91, 0xAF)
SW_TEXT = {0x1E: "integrity error", 0x40: "no such key", 0x7E: "length error", 0x9D: "permission denied",
           0x9E: "parameter error", 0xAD: "authentication delay", 0xAE: "authentication error",
           0xBE: "boundary error", 0xCA: "command aborted", 0xF0: "file not found"}


class CardError(Exception):
    """A step that cannot go on; the text is for people (no key, no secret in it)."""


# ---- crypto ----
def aes_cbc(key: bytes, data: bytes, iv: bytes = bytes(16), decrypt: bool = False) -> bytes:
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = c.decryptor() if decrypt else c.encryptor()
    return op.update(data) + op.finalize()


def cmac(key: bytes, msg: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def mact(mac: bytes) -> bytes:
    """The 8-byte MAC on the wire: the odd-indexed bytes of the full CMAC."""
    return mac[1::2]


def rotl(b: bytes) -> bytes:
    return b[1:] + b[:1]


def jam_crc32(b: bytes) -> bytes:
    """ChangeKey's CRC of the new key: CRC-32 without the final inversion, little endian."""
    return (zlib.crc32(b) ^ 0xFFFFFFFF).to_bytes(4, "little")


def pad(data: bytes) -> bytes:
    """ISO/IEC 9797-1 method 2: 80h always, then zeros to a whole block."""
    data = data + b"\x80"
    return data + bytes(-len(data) % 16)


def unpad(data: bytes) -> bytes:
    i = data.rstrip(b"\x00")
    if not i or i[-1] != 0x80:
        raise CardError("the card's encrypted answer has no valid padding")
    return i[:-1]


def session_vector(purpose: bytes, rnd_a: bytes, rnd_b: bytes) -> bytes:
    """SV1 (purpose A5 5A) / SV2 (5A A5): RndA[15..14] || RndA[13..8] xor RndB[15..10] || RndB[9..0] || RndA[7..0]."""
    mix = bytes(x ^ y for x, y in zip(rnd_a[2:8], rnd_b[0:6]))
    return purpose + b"\x00\x01\x00\x80" + rnd_a[0:2] + mix + rnd_b[6:16] + rnd_a[8:16]


def session_keys(key: bytes, rnd_a: bytes, rnd_b: bytes) -> tuple:
    """(SesAuthENCKey, SesAuthMACKey) of an EV2First authentication."""
    return cmac(key, session_vector(b"\xa5\x5a", rnd_a, rnd_b)), cmac(key, session_vector(b"\x5a\xa5", rnd_a, rnd_b))


class Session:
    """An authenticated session: TI, the session keys, the command counter, the key number."""

    def __init__(self, key_no: int, ti: bytes, enc: bytes, mac: bytes):
        self.key_no, self.ti, self.enc, self.mac, self.ctr = key_no, ti, enc, mac, 0

    def _ctr(self) -> bytes:
        return (self.ctr & 0xFFFF).to_bytes(2, "little")

    def cmd_mac(self, ins: int, hdr: bytes, data: bytes) -> bytes:
        return mact(cmac(self.mac, bytes([ins]) + self._ctr() + self.ti + hdr + data))

    def resp_mac(self, sw2: int, data: bytes) -> bytes:
        """Over the answer, with the counter already incremented."""
        return mact(cmac(self.mac, bytes([sw2]) + self._ctr() + self.ti + data))

    def encrypt(self, data: bytes) -> bytes:
        iv = aes_cbc(self.enc, b"\xa5\x5a" + self.ti + self._ctr() + bytes(8))
        return aes_cbc(self.enc, pad(data), iv)

    def decrypt(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise CardError("the card's encrypted answer is not whole blocks")
        iv = aes_cbc(self.enc, b"\x5a\xa5" + self.ti + self._ctr() + bytes(8))
        return unpad(aes_cbc(self.enc, data, iv, decrypt=True))


def apdu(ins: int, data: bytes, cla: int = 0x90, p1: int = 0, p2: int = 0) -> bytes:
    """A short ISO 7816-4 APDU with Le = 00 (the native commands are wrapped with CLA 90)."""
    if not data or len(data) > MAX_LC:
        raise CardError("the card address is too long for one command")
    return bytes([cla, ins, p1, p2, len(data)]) + data + b"\x00"


def select_apdu() -> bytes:
    return apdu(0xA4, DF_FILE_ID, cla=0x00, p1=0x00, p2=0x0C)


# ---- file settings (datasheet: GetFileSettings / ChangeFileSettings) ----
def _off(b: bytes, i: int) -> int:
    return int.from_bytes(b[i:i + 3], "little")


def decode_file_settings(data: bytes) -> dict:
    """A GetFileSettings answer: file type, options, access rights, size, the SDM part."""
    if len(data) < 7:
        raise CardError("the card's file settings are too short")
    opt = data[1]
    fs = {"comm": opt & 0x03, "rw": data[2] >> 4, "change": data[2] & 0xF, "read": data[3] >> 4,
          "write": data[3] & 0xF, "size": _off(data, 4), "sdm": None}
    if opt & 0x40 and len(data) >= 10:
        o, r2, r1 = data[7], data[8], data[9]
        sdm, i = {"uid": bool(o & 0x80), "ctr": bool(o & 0x40), "ctr_limit": bool(o & 0x20), "enc_file": bool(o & 0x10),
                  "ascii": bool(o & 0x01), "meta": r1 >> 4, "file": r1 & 0xF, "ctr_ret": r2 & 0xF}, 10
        if sdm["meta"] == EVERYONE:
            for k in ("uid", "ctr"):
                if sdm[k]:
                    sdm[k + "_off"], i = _off(data, i), i + 3
        elif sdm["meta"] <= 4:
            sdm["picc_off"], i = _off(data, i), i + 3
        if sdm["file"] != NOBODY:
            sdm["mac_in_off"], i = _off(data, i), i + 3
            if sdm["enc_file"]:
                sdm["enc_off"], sdm["enc_len"], i = _off(data, i), _off(data, i + 3), i + 6
            sdm["mac_off"], i = _off(data, i), i + 3
        fs["sdm"] = sdm
    return fs


def encode_file_settings(fs: dict) -> bytes:
    """ChangeFileSettings data, as FileSettings.encodeToData builds it (encrypted file data is not used here)."""
    sdm = fs.get("sdm")
    out = bytes([(0x40 if sdm else 0) | fs["comm"], fs["rw"] << 4 | fs["change"], fs["read"] << 4 | fs["write"]])
    if not sdm:
        return out
    t = lambda v: v.to_bytes(3, "little")                    # noqa: E731
    opts = 0x80 * sdm["uid"] | 0x40 * sdm["ctr"] | 0x01 * sdm["ascii"]
    out += bytes([opts, 0xF0 | (sdm["ctr_ret"] if sdm["ctr"] else NOBODY), sdm["meta"] << 4 | sdm["file"]])
    if sdm["meta"] == EVERYONE:
        out += (t(sdm["uid_off"]) if sdm["uid"] else b"") + (t(sdm["ctr_off"]) if sdm["ctr"] else b"")
    elif sdm["meta"] != NOBODY:
        out += t(sdm["picc_off"])
    if sdm["file"] != NOBODY:
        out += t(sdm["mac_in_off"]) + t(sdm["mac_off"])
    return out


# ---- the NDEF file ----
def ndef_template(url: str) -> tuple:
    """(file bytes, PICC offset, MAC offset) of a URL with {PICC} and {MAC}: one short URI record, no prefix
    code, each placeholder replaced by '*' of its mirror's length, offsets counted in the file (NLEN included);
    NdefTemplateMaster.generateNdefTemplateFromUrlString with the PICC and MAC placeholders."""
    try:
        raw = url.encode("ascii")
    except UnicodeEncodeError:
        raise CardError("the card address must be plain ASCII") from None
    if raw.count(PICC_PLACEHOLDER) != 1 or raw.count(MAC_PLACEHOLDER) != 1:
        raise CardError("the card address needs {PICC} and {MAC} once each")
    rec = bytearray(b"\x00\x00\xd1\x01\x00\x55\x00" + raw)
    offs = {}
    for name, ph, n in (("picc", PICC_PLACEHOLDER, PICC_LEN), ("mac", MAC_PLACEHOLDER, MAC_LEN)):
        at = bytes(rec).find(ph)
        rec[at:at + len(ph)] = b"*" * n
        offs = {k: v + n - len(ph) if v >= at else v for k, v in offs.items()}
        offs[name] = at
    if len(rec) - 2 > 255:
        raise CardError("the card address is too long for a card")
    rec[1], rec[4] = len(rec) - 2, len(rec) - 6
    return bytes(rec), offs["picc"], offs["mac"]


def with_aar(file: bytes, pkg: str) -> bytes:
    """The URL record, then an Android Application Record (the app opens the card, never a browser); the URL
    stays first and unchanged, so its SDM offsets hold. The same bytes as withYashAar."""
    name = pkg.encode("ascii")
    out = bytearray(file + bytes([0x54, len(AAR_TYPE), len(name)]) + AAR_TYPE + name)
    out[2] &= ~0x40 & 0xFF                                    # the URL record is not the last one any more
    n = len(out) - 2
    out[0], out[1] = n >> 8, n & 0xFF
    return bytes(out)


def card_file(url: str, aar_pkg: str = "") -> tuple:
    """(NDEF file, PICC offset, MAC offset) the card gets."""
    file, picc, mac = ndef_template(url)
    return (with_aar(file, aar_pkg) if aar_pkg else file), picc, mac


# ---- the exchange: each helper is a generator, `resp = yield apdu` ----
def _xfer(cmd: bytes):
    resp = yield cmd
    if not isinstance(resp, (bytes, bytearray)) or len(resp) < 2:
        raise CardError("no answer from the card (it was moved away?)")
    resp = bytes(resp)
    return resp[:-2], resp[-2], resp[-1]


def _sw(sw1: int, sw2: int) -> str:
    return f"{sw1:02X}{sw2:02X}" + (f" ({SW_TEXT[sw2]})" if sw1 == 0x91 and sw2 in SW_TEXT else "")


def authenticate(key_no: int, key: bytes, rnd=os.urandom):
    """AuthenticateEV2First -> a Session, or None when the card refuses this key."""
    data, sw1, sw2 = yield from _xfer(apdu(0x71, bytes([key_no, 0x00])))
    if (sw1, sw2) == (0x91, 0xAD):
        raise CardError("the card delays authentication after failed tries: wait and try again")
    if (sw1, sw2) != MORE or len(data) != 16:
        if sw1 == 0x91 and sw2 in (0x40, 0x9D, 0xAE):
            return None
        raise CardError(f"the card refused to authenticate: {_sw(sw1, sw2)}")
    rnd_b = aes_cbc(key, data, decrypt=True)
    rnd_a = rnd(16)
    data, sw1, sw2 = yield from _xfer(apdu(0xAF, aes_cbc(key, rnd_a + rotl(rnd_b))))
    if (sw1, sw2) != OK or len(data) != 32:
        if (sw1, sw2) == (0x91, 0xAE):
            return None
        raise CardError(f"the card broke off authentication: {_sw(sw1, sw2)}")
    plain = aes_cbc(key, data, decrypt=True)
    if plain[4:20] != rotl(rnd_a):
        return None                                           # not our RndA back: not this key
    return Session(key_no, plain[0:4], *session_keys(key, rnd_a, rnd_b))


def auth_door(key_no: int, house_key: bytes, rnd=os.urandom):
    """The house's key first, then the factory key (a card is rewritten any number of times) -> (Session, which)."""
    for which, key in (("house", house_key), ("factory", FACTORY_KEY)):
        if which == "factory" and key == house_key:
            continue
        s = yield from authenticate(key_no, key, rnd)
        if s is not None:
            return s, which
    raise CardError(f"the card refused the house key and the factory key (key {key_no}): it belongs to another system")


def _check(sw1: int, sw2: int, what: str):
    if (sw1, sw2) != OK:
        raise CardError(f"{what}: the card answered {_sw(sw1, sw2)}")


def cmd_plain(s: Session, ins: int, hdr: bytes, data: bytes, what: str):
    _, sw1, sw2 = yield from _xfer(apdu(ins, hdr + data))
    s.ctr += 1                                                # counted in Plain too, inside a session
    _check(sw1, sw2, what)
    return b""


def cmd_mac(s: Session, ins: int, hdr: bytes, data: bytes, what: str, ok_only: bool = False):
    """CommMode.MAC: CMACt over cmd || CmdCtr || TI || header || data; the answer's MAC checked. With
    ok_only a refusal is returned (None) instead of raised."""
    resp, sw1, sw2 = yield from _xfer(apdu(ins, hdr + data + s.cmd_mac(ins, hdr, data)))
    s.ctr += 1
    if (sw1, sw2) != OK:
        if ok_only:
            return None
        _check(sw1, sw2, what)
    if len(resp) < 8 or s.resp_mac(sw2, resp[:-8]) != resp[-8:]:
        raise CardError(f"{what}: the card's answer has a wrong MAC")
    return resp[:-8]


def cmd_full(s: Session, ins: int, hdr: bytes, data: bytes, what: str, ok_only: bool = False):
    """CommMode.Full: the data encrypted (IV from TI and CmdCtr), then MACed; the answer decrypted."""
    enc = s.encrypt(data) if data else b""
    resp = yield from cmd_mac(s, ins, hdr, enc, what, ok_only)
    return s.decrypt(resp) if resp else resp


def cmd_switched(s: Session, comm: int, ins: int, hdr: bytes, data: bytes, what: str):
    if comm == FULL:
        return (yield from cmd_full(s, ins, hdr, data, what))
    if comm == MAC:
        return (yield from cmd_mac(s, ins, hdr, data, what))
    return (yield from cmd_plain(s, ins, hdr, data, what))


def get_file_settings(s: Session, file_no: int = NDEF_FILE):
    return decode_file_settings((yield from cmd_mac(s, 0xF5, bytes([file_no]), b"", "reading the file settings")))


def write_data(s: Session, comm: int, data: bytes, file_no: int = NDEF_FILE, offset: int = 0):
    hdr = bytes([file_no]) + offset.to_bytes(3, "little") + len(data).to_bytes(3, "little")
    yield from cmd_switched(s, comm, 0x8D, hdr, data, "writing the link")


def change_key(s: Session, key_no: int, old: bytes, new: bytes, version: int = KEY_VERSION):
    """ChangeKey (Full): key 0 = new || version, the session ends (no MAC back); another key = (old xor new) ||
    version || CRC32(new). True when the card took it, False when it refused (a wrong old key)."""
    if key_no == s.key_no:
        hdr = bytes([key_no])
        enc = s.encrypt(new + bytes([version]))
        _, sw1, sw2 = yield from _xfer(apdu(0xC4, hdr + enc + s.cmd_mac(0xC4, hdr, enc)))
        s.ctr += 1
        _check(sw1, sw2, f"changing key {key_no}")
        return True
    body = bytes(a ^ b for a, b in zip(old, new)) + bytes([version]) + jam_crc32(new)
    return (yield from cmd_full(s, 0xC4, bytes([key_no]), body, f"changing key {key_no}", ok_only=True)) is not None


def get_card_uid(s: Session):
    uid = yield from cmd_full(s, 0x51, b"", b"", "reading the UID")
    if len(uid) < 7:
        raise CardError("the card's UID answer is too short")
    return uid[:7]


def sdm_settings(fs: dict, picc_off: int, mac_off: int) -> dict:
    """The door card's file: SDM with encrypted PICC data (UID + counter) under key 1, the CMAC under key 2, ASCII
    mirrors, the MAC over nothing but the PICC (MAC input offset = MAC offset); file access as the writer sets it."""
    sdm = {"uid": True, "ctr": True, "ascii": True, "meta": KEY1, "file": KEY2, "ctr_ret": NOBODY,
           "picc_off": picc_off, "mac_in_off": mac_off, "mac_off": mac_off}
    return {**fs, "sdm": sdm, "rw": KEY2, "change": KEY0, "read": KEY2, "write": KEY2}


def write_card(keys: dict, url: str, aar_pkg: str = "", state: dict = None, rnd=os.urandom):
    """Generator: yields APDUs, is sent each answer (bytes). Returns {ok, step, uid?, keys?, error?}.
    keys = {0: master, 1: SDM meta read, 2: SDM file read}. `state["step"]` names the step under way."""
    st = state if state is not None else {}

    def step(name: str):
        st["step"] = name

    try:
        if any(len(keys.get(k) or b"") != 16 for k in (KEY0, KEY1, KEY2)):
            raise CardError("the house keys are not three AES-128 keys")
        file, picc_off, mac_off = card_file(url, aar_pkg)
        step("select")
        _, sw1, sw2 = yield from _xfer(select_apdu())
        if (sw1, sw2) != (0x90, 0x00):
            raise CardError(f"this is not an NTAG 424 DNA card (select answered {_sw(sw1, sw2)})")
        step("auth key 0")
        s, was = yield from auth_door(KEY0, keys[KEY0], rnd)
        step("file settings")
        fs = yield from get_file_settings(s)
        if len(file) > fs["size"]:
            raise CardError(f"the link is {len(file)} bytes, the card's file only {fs['size']}")
        rw, car = fs["rw"], fs["change"]
        if rw not in (EVERYONE, KEY0):                        # the write key, unless free or the one we hold
            step(f"auth key {rw}")
            s, _ = yield from auth_door(rw, keys.get(rw, FACTORY_KEY), rnd)
        step("write the link")
        now = yield from get_file_settings(s)               # WriteData.run reads the comm mode first
        yield from write_data(s, now["comm"], file)
        if car != rw:
            step(f"auth key {car}")
            s, _ = yield from auth_door(car, keys.get(car, FACTORY_KEY), rnd)
        step("SDM settings")
        yield from cmd_full(s, 0x5F, bytes([NDEF_FILE]), encode_file_settings(sdm_settings(fs, picc_off, mac_off)),
                            "changing the file settings")
        step("auth key 0")
        s, _ = yield from auth_door(KEY0, keys[KEY0], rnd)
        for slot in (KEY1, KEY2, KEY0):                       # the master last: once it is ours, factory auth is gone
            step(f"key {slot}")
            done = yield from change_key(s, slot, FACTORY_KEY, keys[slot])
            if not done:                                      # it holds the house key already: re-auth, set it again
                s, _ = yield from auth_door(KEY0, keys[KEY0], rnd)
                done = yield from change_key(s, slot, keys[slot], keys[slot])
            if not done:
                raise CardError(f"key {slot} holds neither the factory key nor the house key: it belongs to another system")
            if slot != KEY0:
                s, _ = yield from auth_door(KEY0, keys[KEY0], rnd)
        step("check")
        s = yield from authenticate(KEY0, keys[KEY0], rnd)
        if s is None:
            raise CardError("the card does not take the new master key")
        step("uid")
        uid = yield from get_card_uid(s)
        step("done")
        return {"ok": True, "step": "done", "uid": uid.hex(), "keys": was}
    except CardError as e:
        return {"ok": False, "step": st.get("step", "start"), "error": str(e)}
