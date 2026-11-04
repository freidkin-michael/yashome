"""pair: a device's own broker account with a narrow ACL, the OTA password kept, revoke (also on removal) - against a fake broker."""
import json
import stat
import sys
import time

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.pair"]


@pytest.fixture
def pr(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "ACCOUNTS", tmp_path / "mqtt_accounts.json")
    monkeypatch.setattr(me, "PAIRINGS", tmp_path / "pairings.json")
    sent, pub = [], []
    monkeypatch.setattr(me, "_broker", lambda commands, wait=5.0: sent.append(commands) or True)
    monkeypatch.setattr(me, "_persist", lambda: sent.append("persist"))
    monkeypatch.setattr(me, "UNPAIR_WAIT_S", 0)
    monkeypatch.setattr(core, "_mqtt_pub", lambda cli, topic, payload=None, retain=False: pub.append((topic, payload, retain)))
    monkeypatch.setattr(core, "_mqtt_client", object())
    yield sent, pub


def test_the_account_is_narrow_and_made_once(pr):
    sent, _ = pr
    user, pw = me.make_account("air-3f2a1c", ("home/clock/#",))
    assert user == "dev-air-3f2a1c" and len(pw) >= 24
    cmds = sent[0]
    role = next(c for c in cmds if c["command"] == "createRole")
    topics = {(a["acltype"], a["topic"]) for a in role["acls"]}
    assert ("publishClientSend", "air-3f2a1c/#") in topics and ("subscribePattern", "air-3f2a1c/#") in topics
    assert ("subscribePattern", "home/clock/#") in topics and ("publishClientSend", "home/clock/#") not in topics     # read only
    assert not any(t.startswith(("home/state", "home/cmd", "#")) for _, t in topics)                              # nothing of the house bus
    assert me.make_account("air-3f2a1c", ("home/clock/#",)) == (user, pw)                                         # the same account again
    assert len([c for c in sent if c != "persist" and any(x["command"] == "createClient" for x in c)]) == 1
    mode = stat.S_IMODE(me.ACCOUNTS.stat().st_mode)
    assert mode == 0o600


@pytest.mark.parametrize("node", ["", "Air", "1air", "air x", "a" * 29, "../x", "air/#"])
def test_a_bad_node_name_and_a_wide_read_are_refused(pr, node):
    with pytest.raises(HTTPException) as e:
        me.make_account(node)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        me.make_account("air", ("home/state/#",))


def test_a_broker_that_does_not_answer_leaves_no_record(monkeypatch, pr):
    monkeypatch.setattr(me, "_broker", lambda commands, wait=5.0: False)
    with pytest.raises(HTTPException) as e:
        me.make_account("air")
    assert e.value.status_code == 503
    assert (json.loads(me.ACCOUNTS.read_text()) if me.ACCOUNTS.exists() else []) == []


def test_prepare_keeps_the_ota_password_and_revoke_takes_everything_away(pr):
    sent, pub = pr
    rec = me.prepare("air", "house-1")
    assert rec["mqtt_user"] == "dev-air" and rec["house_id"] == "house-1" and len(rec["ota_password"]) >= 24
    assert me.prepare("air")["ota_password"] == rec["ota_password"]                    # the same one at a second call
    assert me.ota_password("air") == rec["ota_password"]
    assert stat.S_IMODE(me.PAIRINGS.stat().st_mode) == 0o600
    assert me.revoke("air") is True
    assert ("air/yash/unpair", "1", False) in pub                                       # the device is told to wipe its record
    last = [c for c in sent if c != "persist"][-1]
    assert {c["command"] for c in last} == {"deleteClient", "deleteRole"}
    assert me.ota_password("air") is None and json.loads(me.ACCOUNTS.read_text()) == []
    assert me.revoke("air") is False                                                    # nothing left to revoke


def test_removing_the_device_of_a_paired_node_revokes_it(pr):
    me.prepare("air")
    me.on_removed({"id": "air1", "transport": "mqtt", "controls": [{"code": "t", "state_topic": "air/sensor/t/state"}]})
    assert me.ota_password("air") is None
    me.prepare("other")
    me.on_removed({"id": "x", "transport": "mqtt", "controls": [{"code": "t", "state_topic": "unpaired/sensor/t/state"}]})
    assert me.ota_password("other") is not None                                          # an unpaired device's removal touches nothing


@pytest.mark.parametrize("node", ["home", "homeassistant", "zigbee2mqtt", "panel", "provision"])
def test_a_namespace_of_the_house_is_not_a_device_name(pr, node):
    with pytest.raises(HTTPException) as e:
        me.make_account(node)
    assert e.value.status_code == 409


def test_a_name_a_plugin_or_a_device_already_uses_is_refused_but_the_same_device_may_pair_again(pr, monkeypatch):
    plug = sorted({t.split("/", 1)[0] for t in core.MQTT_SUBSCRIPTIONS if t.split("/", 1)[0][:1].isalpha()})
    assert plug, "the harness loads plug-ins with their own topics"
    with pytest.raises(HTTPException) as e:
        me.make_account(plug[0])
    assert e.value.status_code == 409
    monkeypatch.setitem(core.DEVICES, "ac-node-card", {"id": "ac-node-card", "transport": "mqtt", "controls": [
        {"code": "power", "state_topic": "ac-node/ac/power/state", "command_topic": "ac-node/ac/power/command"}]})
    monkeypatch.setitem(core.DEVICES, "room_ac", {"id": "room_ac", "transport": "mqtt", "controls": []})
    for node in ("ac-node", "room_ac"):                  # a topic level of an existing card, and a card's id
        with pytest.raises(HTTPException) as e:
            me.make_account(node)
        assert e.value.status_code == 409
    user, pw = me.make_account("airnew")
    monkeypatch.setitem(core.DEVICES, "airnew", {"id": "airnew", "transport": "mqtt", "controls": []})   # its own card appears
    assert me.make_account("airnew") == (user, pw)          # the same node again: the same record, no 409


# ---- step B: the pair request through the host relay, the card from the description ----
from types import SimpleNamespace  # noqa: E402


@pytest.fixture
def start(monkeypatch, pr):
    monkeypatch.setenv("HOUSE_WIFI_SSID", "house-net")
    monkeypatch.setenv("HOUSE_WIFI_PASSWORD", "wifi-pw-not-real")
    monkeypatch.setattr(core, "_house_token_only", lambda request: None)
    monkeypatch.setattr(core, "node_broker_host", lambda request: ("192.0.2.5", ""))
    monkeypatch.setattr(core, "node_broker_port", lambda: 1883)
    monkeypatch.setattr(me, "_finish", lambda node: None)
    calls = []
    monkeypatch.setattr(me, "_host_call", lambda body, timeout: calls.append(body) or {"ok": True, "reply": {"ok": True, "node": "air", "hash": "3f9a1c07be52"}, "steps": ["x"]})
    return calls


def test_pair_start_sends_the_form_through_the_relay_and_keeps_the_ota_password(start):
    out = me.pair_start(me.StartBody(ap="air-setup", code="abcd2345"), SimpleNamespace(headers={}))
    assert out["ok"] is True and out["hash"] == "3f9a1c07be52"
    body = start[0]
    assert body["ap"] == "air-setup" and body["ap_password"] == "ABCD2345"                # the code is the AP key
    f = body["form"]
    assert f["code"] == "ABCD2345" and f["mqtt_user"] == "dev-air" and f["ssid"] == "house-net" and f["mqtt_host"] == "192.0.2.5" and f["mqtt_port"] == "1883"
    assert f["ota_password"] == me.ota_password("air") and len(f["mqtt_password"]) >= 24 and len(f["house_id"]) <= 23
    assert me._load(me.PAIRINGS)["air"]["state"] == "sent"


def test_a_refused_or_unreachable_pairing_leaves_no_account(monkeypatch, start):
    monkeypatch.setattr(me, "_host_call", lambda body, timeout: {"ok": False, "error": "already paired", "steps": ["wlan0 was on: home", "joined air-setup"]})
    with pytest.raises(HTTPException) as e:
        me.pair_start(me.StartBody(ap="air-setup", code="ABCD2345"), SimpleNamespace(headers={}))
    assert e.value.status_code == 409 and me.ota_password("air") is None and me._load(me.ACCOUNTS) == []
    assert "already paired" in e.value.detail and "joined air-setup" in e.value.detail          # the relay's steps help to see where it stopped

    def down(body, timeout):
        raise RuntimeError("no relay")
    monkeypatch.setattr(me, "_host_call", down)
    with pytest.raises(HTTPException) as e:
        me.pair_start(me.StartBody(ap="air-setup", code="ABCD2345"), SimpleNamespace(headers={}))
    assert e.value.status_code == 502 and me.ota_password("air") is None


@pytest.mark.parametrize("ap,code", [("air", "ABCD2345"), ("-setup", "ABCD2345"), ("Air-setup", "ABCD2345"), ("air-setup", "SHORT"), ("air-setup", "ABCD2341")])
def test_a_bad_ap_or_code_is_a_400_before_anything_is_made(start, ap, code):
    with pytest.raises(HTTPException) as e:
        me.pair_start(me.StartBody(ap=ap, code=code), SimpleNamespace(headers={}))
    assert e.value.status_code == 400 and not start and me._load(me.ACCOUNTS) is None


DESC = {"schema": 1, "device": {"name": "air", "friendly": "Air", "mac_suffix": True, "board": {"id": "esp32-c3-supermini"}},
        "entities": [{"domain": "sensor", "name": "CO2", "role": "co2", "unit": "ppm", "state_topic": "~/sensor/co2/state"},
                     {"domain": "binary_sensor", "name": "Motion", "state_topic": "~/binary_sensor/motion/state"},
                     {"domain": "text_sensor", "name": "Mode", "state_topic": "~/text_sensor/mode/state"},
                     {"domain": "sensor", "name": "WiFi", "role": "rssi", "state_topic": "~/sensor/wifi/state"},
                     {"domain": "switch", "name": "Relay", "state_topic": "~/switch/relay/state"},
                     {"domain": "number", "name": "Gate", "state_topic": "~/number/gate/state"}]}


def test_the_card_is_made_from_the_description_with_the_nodes_own_prefix():
    card = me.card_from_description("air-3f2a1c", {"description": DESC})
    assert card["id"] == "air-3f2a1c" and card["name"] == "Air" and card["category"] == "switch" and card["model"] == "esp32-c3-supermini"
    by = {c["code"]: c for c in card["controls"]}
    assert by["co2"]["state_topic"] == "air-3f2a1c/sensor/co2/state" and by["co2"]["unit"] == "ppm"
    assert by["motion"]["vtype"] == "bool" and by["mode"]["kind"] == "sensor_text" and by["wifi"].get("advanced") is True
    assert by["relay"]["command_topic"] == "air-3f2a1c/switch/relay/command" and by["relay"]["payload_on"] == "ON"
    assert "gate" not in by and card["_skipped"] == ["number:gate"] and card["availability_topic"] == "air-3f2a1c/status"
    with pytest.raises(ValueError):
        me.card_from_description("air", {"description": {"device": {}, "entities": [{"domain": "number", "state_topic": "air/number/x/state"}]}})


def test_make_card_registers_it_once_and_only_for_a_paired_node(monkeypatch, pr, tmp_path):
    made = []
    monkeypatch.setattr(core, "_mqtt_devices_upsert", lambda e: made.append(("upsert", e["id"])))
    monkeypatch.setattr(core, "_register_mqtt_device", lambda e: made.append(("register", e["id"])))
    monkeypatch.setattr(core, "_mqtt_resubscribe_generic", lambda *a, **k: made.append(("sub",)))
    monkeypatch.setattr(me, "_description", lambda node: {"description": DESC} if node == "air-3f2a1c" else None)
    with pytest.raises(ValueError):
        me.make_card("air-3f2a1c")                                                        # not paired through the house
    me.prepare("air-3f2a1c")
    card = me.make_card("air-3f2a1c")
    assert card["id"] == "air-3f2a1c" and made == [("upsert", "air-3f2a1c"), ("register", "air-3f2a1c"), ("sub",)]
    with pytest.raises(ValueError):
        me.make_card("nodesc")


def test_a_moved_device_is_unpaired_revoked_and_marked_and_pairing_it_again_clears_the_mark(pr, monkeypatch):
    sent, pub = pr
    monkeypatch.setattr(me, "MOVED", me.PAIRINGS.with_name("moved.json"))
    monkeypatch.setattr(core, "_house_token_only", lambda request: None)
    assert me.mark_moved("air") is False                                   # never paired: nothing to do
    me.prepare("air")                                                       # (paired before its card exists, as in life)
    monkeypatch.setitem(core.DEVICES, "air", {"id": "air", "transport": "mqtt", "esphome_node": "air", "controls": [{"code": "t", "state_topic": "air/sensor/t/state"}]})
    listed = me.pair_list(SimpleNamespace(headers={}))
    assert listed["paired"][0]["device"] == "air" and listed["moved"] == []
    assert me.mark_moved("air") is True
    assert ("air/yash/unpair", "1", False) in pub and me.ota_password("air") is None
    listed = me.pair_list(SimpleNamespace(headers={}))
    assert listed["paired"] == [] and listed["moved"][0]["node"] == "air" and listed["moved"][0]["device"] == "air"   # the card stays, marked
    assert "air" in core.DEVICES
    monkeypatch.delitem(core.DEVICES, "air")                                # the card goes: the same node may pair afresh
    me.prepare("air")
    assert me.pair_list(SimpleNamespace(headers={}))["moved"] == []


def test_a_panel_account_reads_the_house_state_and_writes_only_its_own_topics(pr, monkeypatch):
    sent, _ = pr
    with pytest.raises(HTTPException) as e:
        me.make_account("panel9", kind="panel")                                 # no such panel layout
    assert e.value.status_code == 409
    monkeypatch.setattr(me, "_panel_exists", lambda node: node == "panel9")
    user, _pw = me.make_account("panel9", kind="panel")
    role = next(c for cmds in sent if cmds != "persist" for c in cmds if c["command"] == "createRole")
    acl = {(a["acltype"], a["topic"]) for a in role["acls"]}
    assert ("publishClientSend", "panel/panel9/#") in acl and ("subscribePattern", "panel/panel9/#") in acl
    assert ("subscribePattern", "home/state/#") in acl and ("publishClientSend", "home/state/#") not in acl   # reads the house state, never writes it
    assert ("publishClientSend", "panel9/#") not in acl
    with pytest.raises(HTTPException) as e:
        me.make_account("panel9", kind="device")                                # the same node as another kind: refused
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        me.make_account("x1", kind="tablet")


def test_a_paired_panel_is_waited_for_on_its_own_topic_and_gets_no_card(pr, monkeypatch):
    monkeypatch.setattr(me, "_panel_exists", lambda node: True)
    me.make_account("knob9", kind="panel")
    me.prepare("knob9", "yashome", (), "panel")
    asked, cards = [], []
    monkeypatch.setattr(core, "_retained_values", lambda topics, timeout=3.0: asked.append(topics[0]) or {topics[0]: "online"})
    monkeypatch.setattr(me, "make_card", lambda node: cards.append(node))
    me._finish("knob9")
    assert asked == ["panel/knob9/status"] and cards == []
    assert me._load(me.PAIRINGS)["knob9"]["state"] == "paired"


def test_a_restart_resumes_the_wait_of_a_sent_record(pr, monkeypatch):
    me._save(me.PAIRINGS, {"a": {"state": "sent"}, "b": {"state": "paired"}})
    done = []
    monkeypatch.setattr(me, "_finish", lambda node: done.append(node))
    me._resume()
    for _ in range(50):
        if done:
            break
        time.sleep(0.02)
    assert done == ["a"] and me._resume in core.STARTUP_TASKS


def test_a_revoked_node_leaves_no_retained_state_and_other_nodes_keep_theirs(pr, monkeypatch):
    broker = {"air/status": "online", "air/sensor/pairing/state": "paired", "air/fwgen/hash": "abc", "air/fwgen/description": "x",
              "homeassistant/sensor/air/temp/config": "{}", "homeassistant/switch/air/relay/config": "{}",
              "homeassistant/sensor/air2/temp/config": "{}", "air2/status": "online", "panel/air/status": "online", "other/status": "online"}
    monkeypatch.setattr(me, "_retained_under", lambda prefix, listen=2.5, extra=(): sorted(t for t in broker if t.startswith(prefix) or (extra and t.startswith("homeassistant/"))))
    me.make_account("air")
    me.prepare("air", "yashome", (), "device")
    sent, pub = pr
    assert me.revoke("air") is True
    emptied = {t for t, payload, retain in pub if payload is None and retain}
    assert emptied == {"air/status", "air/sensor/pairing/state", "air/fwgen/hash", "air/fwgen/description",
                       "homeassistant/sensor/air/temp/config", "homeassistant/switch/air/relay/config"}     # air2/, panel/air/, other/ stay


def test_a_revoked_panel_clears_its_own_prefix_but_keeps_the_layout_the_house_publishes(pr, monkeypatch):
    broker = {"panel/knob9/status": "online", "panel/knob9/layout": "{}", "panel/knob9/uptime": "5", "knob9/status": "x"}
    monkeypatch.setattr(me, "_retained_under", lambda prefix, listen=2.5, extra=(): sorted(t for t in broker if t.startswith(prefix)))
    monkeypatch.setattr(me, "_panel_exists", lambda node: True)
    me.make_account("knob9", kind="panel")
    me.prepare("knob9", "yashome", (), "panel")
    _, pub = pr
    me.revoke("knob9")
    assert {t for t, payload, retain in pub if payload is None and retain} == {"panel/knob9/status", "panel/knob9/uptime"}


def test_a_paired_node_is_not_onboarded_through_discovery_and_an_existing_card_is_kept(pr, monkeypatch):
    me._save(me.PAIRINGS, {"air": {"state": "paired"}})
    assert me.discovery_ignore({"air", "x"}) and not me.discovery_ignore({"lamp"})
    monkeypatch.setattr(me, "_description", lambda node: {"description": {}})
    monkeypatch.setitem(core.DEVICES, "air_card", {"id": "air_card", "esphome_node": "air", "controls": []})      # a card made by discovery earlier
    made = []
    monkeypatch.setattr(core, "_mqtt_devices_upsert", lambda card: made.append(card))
    assert me.make_card("air")["id"] == "air_card" and made == []                                                   # no second card


def test_pair_with_keeps_how_the_device_was_paired_and_a_refusal_leaves_no_account(pr, monkeypatch):
    """pair_with over any channel: the record says via (the Unpair text follows it); a refused request revokes the account."""
    monkeypatch.setattr(me, "_finish", lambda node: None)
    out = me.pair_with("node-a", lambda rec: {"ok": True, "reply": {"node": "node-a"}}, via="thread")
    assert out["node"] == "node-a" and me._load(me.PAIRINGS)["node-a"]["via"] == "thread"
    with pytest.raises(me.HTTPException) as e:
        me.pair_with("node-b", lambda rec: {"ok": False, "error": "already paired"})
    assert e.value.status_code == 409 and "node-b" not in (me._load(me.PAIRINGS) or {})
    assert not any(a["user"] == "dev-node-b" for a in me._load(me.ACCOUNTS) or [])


def test_its_own_card_takes_the_new_channels_and_keeps_the_persons_name(monkeypatch, pr):
    """A card the pairing made is renewed from a new description (a new build, a new pairing): the channels and the model
    change, the name the person gave and the room stay; a card from discovery is never touched."""
    made = []
    monkeypatch.setattr(core, "_mqtt_devices_upsert", lambda e: made.append(e))
    monkeypatch.setattr(core, "_register_mqtt_device", lambda e: None)
    monkeypatch.setattr(core, "_mqtt_resubscribe_generic", lambda *a, **k: None)
    monkeypatch.setattr(me, "_description", lambda node: {"description": DESC})
    me.prepare("air-3f2a1c")
    own = {"id": "air-3f2a1c", "esphome_node": "air-3f2a1c", "made_by": "pair", "name": "Kitchen air", "room": "kitchen", "controls": []}
    monkeypatch.setitem(core.DEVICES, "air-3f2a1c", own)
    card = me.make_card("air-3f2a1c")
    assert (card["name"], card["room"]) == ("Kitchen air", "kitchen") and card["controls"] and made
    made.clear()
    monkeypatch.setitem(core.DEVICES, "air-3f2a1c", {**own, "made_by": None})
    assert me.make_card("air-3f2a1c")["controls"] == [] and made == []



def test_an_unpaired_device_is_paired_again_under_its_name_and_a_strange_card_still_blocks_it(pr, monkeypatch):
    """Unpair keeps the card: that card (made by the pairing, or marked moved) does not take the name; another device's
    card of that name still does."""
    monkeypatch.setattr(me, "MOVED", me.PAIRINGS.with_name("moved.json"))
    monkeypatch.setitem(core.DEVICES, "node-a", {"id": "node-a", "made_by": "pair", "controls": []})
    assert me._taken("node-a") == ""
    monkeypatch.setitem(core.DEVICES, "node-b", {"id": "node-b", "controls": []})
    assert me._taken("node-b") != ""
    me._save(me.MOVED, {"node-b": {"ts": 1}})
    assert me._taken("node-b") == ""
