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
_REAL_RUN = core._safe_execute_binding
UNLOCK_UID, LOCK_UID = "04a1b2c3d4e5f6", "04010203040506"
me._CARDS.clear()
me._CARDS.update({UNLOCK_UID: "unlock", LOCK_UID: "lock"})       # the house's list (Settings -> Devices -> NFC tags)


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
    core._safe_execute_binding = lambda *a: fired.append(a)
    me.fired = fired


def teardown_function(_):
    core._safe_execute_binding = _REAL_RUN


LOCK = {"id": "door", "name": "Door", "availability_topic": "door/availability",
        "_ctl": {"locked": {"kind": "switch", "code": "locked", "confirm": True, "text_on": "Locked"}}}


def test_a_lock_rule_waits_for_the_lock_and_says_what_it_reported(monkeypatch):
    monkeypatch.setitem(core.DEVICES, "door", LOCK)
    monkeypatch.setattr(me, "CONFIRM_S", 1)
    monkeypatch.setattr(me, "CONFIRM_POLL_S", 0.02)
    states = iter([{"values": {"locked": False}}, {"values": {"locked": True}}])
    monkeypatch.setattr(core, "get_status", lambda d: next(states, {"values": {"locked": True}}))
    core._safe_execute_binding = lambda *a: {"target": "door", "code": "locked", "value": True}
    r = call(*tap(LOCK_UID, 11), lang="ru-RU,ru")
    assert r["ok"] and "Door: " + me._I18N["ru"]["Locked"] in r["body"]
    monkeypatch.setattr(core, "get_status", lambda d: {"values": {"locked": False}})
    r = call(*tap(LOCK_UID, 12))
    assert r["status"] == 504 and "Door did not confirm in 1 s" in r["body"]
    core._safe_execute_binding = lambda *a: {"target": "door", "code": "locked", "value": True, "error": "offline"}
    r = call(*tap(LOCK_UID, 13))
    assert r["status"] == 503 and "Door is offline" in r["body"]


def test_a_dimmer_switch_rule_through_a_transport_payload_is_accepted_not_504(monkeypatch):
    lamp = {"id": "lamp_z", "name": "Lamp", "_ctl": {"state": {"kind": "switch", "code": "state", "confirm": True}}}
    monkeypatch.setitem(core.DEVICES, "lamp_z", lamp)
    monkeypatch.setattr(me, "CONFIRM_S", 1)
    core._safe_execute_binding = lambda *a: {"target": "lamp_z", "code": "state", "value": {"state": "ON"}}
    r = call(*tap(LOCK_UID, 21))
    assert r["status"] == 200 and "Accepted" in r["body"]


def test_the_tap_page_says_waiting_or_gone(monkeypatch):
    for why, text in (("waiting", "is waiting for its report"), ("gone", "is gone")):
        core._safe_execute_binding = lambda *a, w=why: {"target": "door", "code": "locked", "error": "offline", "why": w}
        r = call(*tap(LOCK_UID, 31 if why == "waiting" else 32))
        assert r["status"] == 503 and text in r["body"], (why, r["body"])


def test_a_rule_whose_command_did_not_go_out_is_not_done(monkeypatch):
    monkeypatch.setitem(core.DEVICES, "door", LOCK)
    core._safe_execute_binding = lambda *a: {"target": "door", "code": "locked", "error": "publish failed"}
    r = call(*tap(LOCK_UID, 41), lang="ru-RU,ru")
    assert r["status"] == 502 and me._I18N["ru"]["Not done"] in r["body"] \
        and me._I18N["ru"]["{name}: the command did not go out"].format(name="Door") in r["body"], r["body"]


def test_a_failed_rule_without_a_target_names_the_card(monkeypatch):
    core._safe_execute_binding = lambda *a: {"error": "publish failed"}
    r = call(*tap(LOCK_UID, 42))
    assert r["status"] == 502 and "None" not in r["body"] and "card " in r["body"], r["body"]


def test_a_rule_action_that_raises_is_not_accepted(monkeypatch):
    def tv_off(*a):
        raise RuntimeError("TV is off")
    monkeypatch.setattr(core, "_execute_binding", tv_off)
    core._safe_execute_binding = _REAL_RUN
    r = call(*tap(LOCK_UID, 43))
    assert r["status"] == 502 and "Accepted" not in r["body"], r["body"]
    assert core._events["nfc"][-1]["fired"] == "failed: TV is off"


def test_an_action_that_raises_offline_is_502_not_the_offline_503(monkeypatch):
    def raises(*a):
        raise RuntimeError("offline")
    monkeypatch.setattr(core, "_execute_binding", raises)
    core._safe_execute_binding = _REAL_RUN
    r = call(*tap(LOCK_UID, 44))
    assert r["status"] == 502, r["body"]


def test_open_path_and_the_device():
    assert "/nfc" in core.OPEN_PATHS
    d = core.DEVICES["nfc"]
    assert d["transport"] == "nfc" and d["_ctl"]["card"]["kind"] == "trigger_text"
    assert set(me._device()["controls"][0]["values"]) >= {"unlock", "lock"}             # its gestures: the cards on the list


def test_genuine_tap_is_an_event_for_the_rules_then_replay_is_refused():
    p, c = tap(UNLOCK_UID, 7)
    r = call(p, c)
    assert r["ok"] and me.fired == [("nfc", "card", "unlock")]
    assert core._events["nfc"][-1]["value"] == "unlock"
    r = call(p, c, lang="ru-RU,ru")
    assert r["status"] == 403 and me._I18N["ru"]["Refused"] in r["body"] and len(me.fired) == 1


def test_the_other_card_is_its_own_event():
    assert call(*tap(LOCK_UID, 1))["ok"] and me.fired == [("nfc", "card", "lock")]


def test_lost_counters_file_does_not_reopen_old_links():
    p, c = tap(UNLOCK_UID, 9)
    assert call(p, c)["ok"]
    me.COUNTERS_PATH.unlink()
    assert not call(p, c)["ok"] and len(me.fired) == 1
    me._seen.clear()                                    # a restart: memory is gone too
    assert not call(p, c)["ok"] and len(me.fired) == 1
    assert not call(*tap(UNLOCK_UID, 10))["ok"] and len(me.fired) == 1


def test_a_flood_of_forgeries_does_not_lock_the_owner_out():
    p, c = tap(UNLOCK_UID, 40, forge=True)
    for _ in range(50):
        assert not call(p, c)["ok"]
    assert call(*tap(UNLOCK_UID, 41))["ok"] and me.fired == [("nfc", "card", "unlock")]


def test_unreadable_counters_refuse_everything():
    me.COUNTERS_PATH.write_text("{corrupt")
    p, c = tap(UNLOCK_UID, 11)
    assert not call(p, c)["ok"] and me.fired == []
    me.COUNTERS_PATH.write_text('{"x": "not a number"}')
    assert not call(p, c)["ok"] and me.fired == []


def test_forged_and_foreign_are_refused_and_mismatch_is_not_logged(caplog):
    p, c = tap(UNLOCK_UID, 3, forge=True)
    assert not call(p, c)["ok"]
    good = tap(UNLOCK_UID, 3)[1]
    assert good not in caplog.text
    r = call(*tap("04ffffffffffff", 3))
    assert r["status"] == 403 and "not our card" in r["body"] and me.fired == []


def test_plain_get_only_posts_back():
    page = asyncio.run(me.nfc_get(req()))
    assert b"fetch(location.pathname" in page.body and me.fired == []


def test_lost_counters_log_warns_against_an_empty_file(caplog):
    p, c = tap(UNLOCK_UID, 50)
    assert call(p, c)["ok"]
    me.COUNTERS_PATH.unlink()
    assert not call(*tap(UNLOCK_UID, 51))["ok"]
    un, lk = UNLOCK_UID.lower(), LOCK_UID.lower()
    assert "only for NEW stickers" in caplog.text and f"not tapped yet: {lk}" in caplog.text
    assert "tap EACH card yourself now" in caplog.text
    assert not call(*tap(LOCK_UID, 5))["ok"]
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


def test_the_counters_live_in_plugin_state_and_two_copies_keep_the_higher():
    assert me.COUNTERS_PATH.parent.name == "nfc" and me.COUNTERS_PATH.parent.parent.name == "plugin-state"
    merged = json.loads(me._max_counters(b'{"a": 5, "b": 1}', b'{"a": 3, "b": 9, "c": 2}'))
    assert merged == {"a": 5, "b": 9, "c": 2}
    assert me._max_counters(b'{"a": 5}', b"{broken") == b'{"a": 5}'


def test_the_plugin_merges_two_copies_of_its_counters():
    import inspect
    assert "merge=_max_counters" in inspect.getsource(me)


def test_the_app_asks_for_json_and_gets_the_answer_as_data():
    p, c = tap(UNLOCK_UID, 21)
    r = asyncio.run(me._tap(req(), p, c, True))
    assert r.status_code == 200 and r.media_type == "application/json"
    assert json.loads(r.body) == {"ok": True, "head": "Accepted", "note": "card unlock"}
    r = asyncio.run(me._tap(req(), p, c, True))                                  # replayed
    assert r.status_code == 403 and json.loads(r.body)["ok"] is False and json.loads(r.body)["head"] == "Refused"


# ---- Settings -> Devices -> NFC tags ----
from fastapi.testclient import TestClient  # noqa: E402

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
NEW_UID = "04777777777777"


def _cards_back(monkeypatch):
    """Each test on its own copy of the list and the file."""
    monkeypatch.setattr(me, "_CARDS", dict(me._CARDS))
    monkeypatch.setattr(me, "_new", {})
    monkeypatch.setattr(me, "_last", {})
    keep = me.CARDS_FILE.read_text() if me.CARDS_FILE.exists() else None
    monkeypatch.setattr(me, "_write_cards", lambda cards: None)
    return keep


def test_the_list_is_the_file_and_none_before_the_first_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "CARDS_FILE", tmp_path / "cards.json")
    assert me._load_cards() == {}
    (tmp_path / "cards.json").write_text(json.dumps({"cards": {NEW_UID: "Gate"}}))
    assert me._load_cards() == {NEW_UID: "Gate"}
    (tmp_path / "cards.json").write_text("not json")
    assert me._load_cards() == {}


def test_a_tag_tapped_but_not_listed_is_offered_then_added_with_its_counter(monkeypatch):
    _cards_back(monkeypatch)
    monkeypatch.setattr(core, "update_device", lambda d: d)
    c = TestClient(core.app)
    r = call(*tap(NEW_UID, 40))
    assert r["status"] == 403 and not me.fired                     # not on the list: refused, no rule runs
    v = c.get("/api/nfc", headers=HOUSE).json()
    assert [n["uid"] for n in v["new"]] == [NEW_UID] and v["ready"]
    v = c.post("/api/nfc/cards", headers=HOUSE, json={"uid": NEW_UID, "name": " Garden  gate "}).json()
    assert {x["uid"]: x["name"] for x in v["cards"]}[NEW_UID] == "Garden gate" and not v["new"]
    assert call(*tap(NEW_UID, 40))["status"] == 403                # the link the phone saw never counts later
    assert call(*tap(NEW_UID, 41))["ok"] and me.fired[-1][1:] == ("card", "Garden gate")
    assert c.get("/api/nfc", headers=HOUSE).json()["cards"][0]["last"]


def test_adding_and_removing_check_what_they_are_given(monkeypatch):
    _cards_back(monkeypatch)
    monkeypatch.setattr(core, "update_device", lambda d: d)
    c = TestClient(core.app)
    post = lambda uid, name: c.post("/api/nfc/cards", headers=HOUSE, json={"uid": uid, "name": name})
    assert post("04zz", "x").status_code == 400
    assert post(NEW_UID, "a|b").status_code == 400
    assert post(LOCK_UID, "again").status_code == 409
    assert post(NEW_UID, "LOCK").status_code == 409                # names are unique, in any case
    assert post("04 77 77 77 77 77 78", "Typed").status_code == 200   # a UID typed with spaces
    assert c.post("/api/nfc/cards", json={"uid": NEW_UID, "name": "x"}).status_code in (401, 403)
    assert c.delete("/api/nfc/cards/04777777777778", headers=HOUSE).status_code == 200
    assert c.delete("/api/nfc/cards/04777777777778", headers=HOUSE).status_code == 404
    assert call(*tap("04777777777778", 5))["status"] == 403        # off the list: refused


def test_the_device_offers_the_tags_to_the_rules():
    vals = me._device()["controls"][0]["values"]
    assert vals == sorted(set(me._CARDS.values())) and me._device()["virtual"] == "tag"


def test_add_a_tag_waits_for_its_tap_on_a_phone_and_names_it(monkeypatch):
    _cards_back(monkeypatch)
    monkeypatch.setattr(me, "_enroll", {})
    monkeypatch.setattr(me, "_added", {})
    monkeypatch.setattr(core, "update_device", lambda d: d)
    c = TestClient(core.app)
    assert c.post("/api/nfc/enroll", headers=HOUSE, json={"name": "lock"}).status_code == 409    # a name in use
    v = c.post("/api/nfc/enroll", headers=HOUSE, json={"name": " Gate "}).json()
    assert v["enroll"]["name"] == "Gate" and 0 < v["enroll"]["left"] <= me.ENROLL_S
    assert call(*tap(LOCK_UID, 60))["ok"] and me.fired[-1][2] == "lock"      # a listed tag still does its job
    assert c.get("/api/nfc", headers=HOUSE).json()["enroll"]
    r = asyncio.run(me._tap(req(), *tap(NEW_UID, 70), True))
    assert r.status_code == 200 and json.loads(r.body)["head"] == "Added" and "Gate" in json.loads(r.body)["note"]
    v = c.get("/api/nfc", headers=HOUSE).json()
    assert {x["uid"]: x["name"] for x in v["cards"]}[NEW_UID] == "Gate" and v["enroll"] is None
    assert v["added"]["uid"] == NEW_UID and not v["new"]
    assert call(*tap(NEW_UID, 70))["status"] == 403                 # the link the enrolling tap made never counts
    assert call(*tap(NEW_UID, 71))["ok"] and me.fired[-1][2] == "Gate"
    assert c.post("/api/nfc/enroll", json={"name": "x"}).status_code in (401, 403)


def test_the_wait_for_a_new_tag_ends_and_can_be_cancelled(monkeypatch):
    _cards_back(monkeypatch)
    monkeypatch.setattr(me, "_enroll", {})
    monkeypatch.setattr(me, "_added", {})
    c = TestClient(core.app)
    c.post("/api/nfc/enroll", headers=HOUSE, json={"name": "Gate"})
    assert c.delete("/api/nfc/enroll", headers=HOUSE).json()["enroll"] is None
    assert call(*tap(NEW_UID, 80))["status"] == 403 and NEW_UID not in me._CARDS
    c.post("/api/nfc/enroll", headers=HOUSE, json={"name": "Gate"})
    me._enroll["until"] = 0                                          # its time is up
    assert call(*tap(NEW_UID, 81))["status"] == 403 and NEW_UID not in me._CARDS
    assert c.get("/api/nfc", headers=HOUSE).json()["enroll"] is None


def test_a_renamed_tag_keeps_its_rules_and_their_switch(monkeypatch):
    _cards_back(monkeypatch)
    monkeypatch.setattr(core, "update_device", lambda d: d)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setitem(core._bindings, "nfc", {"card": {"lock": {"target": "door", "code": "locked", "action": "on"}}})
    monkeypatch.setattr(core, "_rules_disabled", {"b|nfc|card|lock"})
    c = TestClient(core.app)
    uid = LOCK_UID.lower()
    assert c.patch(f"/api/nfc/cards/{uid}", headers=HOUSE, json={"name": "unlock"}).status_code == 409
    assert c.patch(f"/api/nfc/cards/{uid}", headers=HOUSE, json={"name": "a/b"}).status_code == 400
    assert c.patch("/api/nfc/cards/04000000000000", headers=HOUSE, json={"name": "x"}).status_code == 404
    assert c.patch(f"/api/nfc/cards/{uid}", headers=HOUSE, json={"name": "LOCK"}).status_code == 200   # its own name, another case
    v = c.patch(f"/api/nfc/cards/{uid}", headers=HOUSE, json={"name": "Front door"}).json()
    assert {x["uid"]: x["name"] for x in v["cards"]}[uid] == "Front door"
    assert list(core._bindings["nfc"]["card"]) == ["Front door"]
    assert core._rules_disabled == {"b|nfc|card|Front door"}
    assert "Front door" in me._device()["controls"][0]["values"]
    assert c.patch(f"/api/nfc/cards/{uid}", json={"name": "x"}).status_code in (401, 403)
