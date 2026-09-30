"""nfc: a genuine tap is the event "card = <name>" of the nfc device, handed to the rules; forged,
foreign, replayed taps and any doubt about the counters are refused; a plain GET never acts."""
import asyncio
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from starlette.requests import Request

import app as core

me = sys.modules["yashome_plugins.nfc"]
META = bytes.fromhex(os.environ["NFC_SDM_META_KEY"])
FILE = bytes.fromhex(os.environ["NFC_SDM_FILE_KEY"])
_REAL_SUBMIT = core._bg.submit


def tap(uid_hex: str, ctr: int, forge: bool = False) -> tuple:
    uid = bytes.fromhex(uid_hex)
    plain = bytes([me._sdm.PICC_TAG_UID_CTR]) + uid + ctr.to_bytes(3, "little") + b"\x00" * 5
    enc = Cipher(algorithms.AES(META), modes.CBC(b"\x00" * 16)).encryptor()
    picc = enc.update(plain) + enc.finalize()
    mac = me._sdm._truncate(me._sdm._cmac(me._sdm.session_mac_key(FILE, uid, ctr), b""))
    if forge:
        mac = bytes([mac[0] ^ 1]) + mac[1:]
    return picc.hex(), mac.hex()


def req(lang="en"):
    return Request({"type": "http", "headers": [(b"accept-language", lang.encode())]})


def call(picc, cmac, lang="en"):
    r = asyncio.run(me._tap(req(lang), picc, cmac))
    return {"ok": r.status_code == 200, "status": r.status_code, "body": r.body.decode()}


def setup_function(_):
    me.COUNTERS_PATH.write_text("{}")
    me._seen.clear()
    me._fails.clear()
    fired = []
    core._bg.submit = lambda fn, *a: fired.append(a)
    me.fired = fired


def teardown_function(_):
    core._bg.submit = _REAL_SUBMIT


def test_open_path_and_the_device():
    assert "/nfc" in core.OPEN_PATHS
    d = core.DEVICES["nfc"]
    assert d["transport"] == "nfc" and d["_ctl"]["card"]["kind"] == "trigger_text"
    assert set(d["_ctl"]["card"]["values"]) >= {"unlock", "lock"}


def test_genuine_tap_is_an_event_for_the_rules_then_replay_is_refused():
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 7)
    r = call(p, c)
    assert r["ok"] and me.fired == [("nfc", "card", "unlock")]
    assert core._events["nfc"][-1]["value"] == "unlock"
    r = call(p, c, lang="ru-RU,ru")
    assert r["status"] == 403 and me._I18N["ru"]["Refused"] in r["body"] and len(me.fired) == 1


def test_the_other_card_is_its_own_event():
    assert call(*tap(os.environ["NFC_UID_LOCK"], 1))["ok"] and me.fired == [("nfc", "card", "lock")]


def test_lost_counters_file_does_not_reopen_old_links():
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 9)
    assert call(p, c)["ok"]
    me.COUNTERS_PATH.unlink()
    assert not call(p, c)["ok"] and len(me.fired) == 1
    me._seen.clear()                                    # a restart: memory is gone too
    assert not call(p, c)["ok"] and len(me.fired) == 1
    assert not call(*tap(os.environ["NFC_UID_UNLOCK"], 10))["ok"] and len(me.fired) == 1


def test_a_flood_of_forgeries_does_not_lock_the_owner_out():
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 40, forge=True)
    for _ in range(50):
        assert not call(p, c)["ok"]
    assert call(*tap(os.environ["NFC_UID_UNLOCK"], 41))["ok"] and me.fired == [("nfc", "card", "unlock")]


def test_unreadable_counters_refuse_everything():
    me.COUNTERS_PATH.write_text("{corrupt")
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 11)
    assert not call(p, c)["ok"] and me.fired == []
    me.COUNTERS_PATH.write_text('{"x": "not a number"}')
    assert not call(p, c)["ok"] and me.fired == []


def test_forged_and_foreign_are_refused_and_mismatch_is_not_logged(caplog):
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 3, forge=True)
    assert not call(p, c)["ok"]
    good = tap(os.environ["NFC_UID_UNLOCK"], 3)[1]
    assert good not in caplog.text
    r = call(*tap("04ffffffffffff", 3))
    assert r["status"] == 403 and "not our card" in r["body"] and me.fired == []


def test_plain_get_only_posts_back():
    page = asyncio.run(me.nfc_get(req()))
    assert b"fetch(location.pathname" in page.body and me.fired == []


def test_lost_counters_log_warns_against_an_empty_file(caplog):
    p, c = tap(os.environ["NFC_UID_UNLOCK"], 50)
    assert call(p, c)["ok"]
    me.COUNTERS_PATH.unlink()
    assert not call(*tap(os.environ["NFC_UID_UNLOCK"], 51))["ok"]
    un, lk = os.environ["NFC_UID_UNLOCK"].lower(), os.environ["NFC_UID_LOCK"].lower()
    assert "only for NEW stickers" in caplog.text and f"not tapped yet: {lk}" in caplog.text
    assert "tap EACH card yourself now" in caplog.text
    assert not call(*tap(os.environ["NFC_UID_LOCK"], 5))["ok"]
    assert "so far: " + json.dumps({un: 51, lk: 5}) + ";" in caplog.text


def test_a_mistyped_key_is_unset_not_a_crash(monkeypatch):
    for bad in ("00112233zz", "0011"):
        monkeypatch.setenv("T_NFC_KEY", bad)
        assert me._key("T_NFC_KEY") == b""
    monkeypatch.setenv("T_NFC_KEY", "00112233445566778899aabbccddeeff")
    assert len(me._key("T_NFC_KEY")) == 16
    monkeypatch.delenv("T_NFC_KEY")
    assert me._key("T_NFC_KEY") == b""


def test_the_post_back_is_an_open_route_the_get_an_open_page():
    scope = {"type": "http", "method": "POST", "path": "/nfc", "root_path": ""}
    assert id(core._matched_route(scope)) in core.OPEN_ROUTES and "/nfc" in core.OPEN_PATHS
