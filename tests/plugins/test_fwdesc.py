"""fwdesc: the reader (valid, invalid, oversized, gzip, secret payloads), the three cases, a hash mismatch, the rate limit,
removal clearing the retained topics, and build registration recomputing the hash."""
import base64
import gzip
import json
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.fwdesc"]


def desc(node="radar-1", extra=None):
    d = {"schema": 1, "fwgen": {"version": "0.4"}, "build": {"hash": "x", "date": "2026-10-18"},
         "device": {"name": node, "topic_prefix": node, "chip": "esp32-c3"},
         "entities": [{"domain": "sensor", "name": "T", "role": "temperature", "state_topic": f"{node}/sensor/t/state"}]}
    d.update(extra or {})
    return d


def wire(d, packed=False):
    raw = json.dumps(d).encode()
    if packed:
        return json.dumps({"enc": "gzip+base64", "data": base64.b64encode(gzip.compress(raw)).decode()}).encode()
    return raw


@pytest.fixture
def fw(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "DATA", tmp_path)
    for d in (me._pending, me._want, me._seen):
        d.clear()
    me._warned.clear()
    monkeypatch.setattr(me, "_house_secrets", lambda: {"hunter2-secret-value"})
    yield tmp_path


def feed(node, d, h=None, packed=False):
    me.on_node([node, "fwgen", "description"], wire(d, packed), True)
    me.on_node([node, "fwgen", "hash"], (h or me.canonical_hash(d)).encode(), True)


def test_the_hash_is_the_canonical_one_without_build_hash_and_date():
    a = desc()
    b = desc()
    b["build"] = {"hash": "other", "date": "2027-01-01"}
    assert me.canonical_hash(a) == me.canonical_hash(b) and len(me.canonical_hash(a)) == 12
    b["device"]["chip"] = "esp32"
    assert me.canonical_hash(a) != me.canonical_hash(b)
    c = desc()
    del c["build"]["hash"], c["build"]["date"]               # an empty build stays: the same bytes
    assert me.canonical_hash(c) == me.canonical_hash(a)


def test_case_two_then_case_one_and_the_order_of_the_two_topics_does_not_matter(fw):
    d = desc()
    h = me.canonical_hash(d)
    me.on_node(["radar-1", "fwgen", "hash"], h.encode(), True)           # the hash first
    assert not (fw / "devices" / "radar-1.json").exists()
    me.on_node(["radar-1", "fwgen", "description"], wire(d), True)
    rec = json.loads((fw / "devices" / "radar-1.json").read_text())
    assert rec["hash"] == h and rec["state"] == "yash" and rec["build_known"] is False and rec["history"] == []
    me._seen.clear()
    feed("radar-1", d)                                                    # the same hash again: nothing is replaced
    assert json.loads((fw / "devices" / "radar-1.json").read_text())["history"] == []


def test_an_old_pending_description_is_not_paired_with_a_new_hash(fw, monkeypatch):
    old, new = desc(), desc(extra={"modules": [{"uid": "a1"}]})
    me.on_node(["radar-1", "fwgen", "description"], wire(old), True)
    monkeypatch.setattr(me.time, "time", lambda: 1e10)                   # long after: a later connect
    me.on_node(["radar-1", "fwgen", "hash"], me.canonical_hash(new).encode(), True)   # the new hash first
    assert not (fw / "devices" / "radar-1.json").exists()
    me.on_node(["radar-1", "fwgen", "description"], wire(new), True)
    rec = json.loads((fw / "devices" / "radar-1.json").read_text())
    assert rec["hash"] == me.canonical_hash(new) and rec["description"] == new and rec["mismatch"] is False
    assert "radar-1" not in me._pending


def test_a_new_hash_replaces_the_copy_and_keeps_the_old_one_in_the_history(fw):
    feed("radar-1", desc())
    first = me.canonical_hash(desc())
    me._seen.clear()
    d2 = desc(extra={"modules": [{"uid": "a1"}]})
    feed("radar-1", d2)
    rec = json.loads((fw / "devices" / "radar-1.json").read_text())
    assert rec["hash"] == me.canonical_hash(d2) and rec["history"][0]["hash"] == first


def test_gzip_wrapper_is_read_and_a_bomb_is_not(fw):
    feed("radar-1", desc(), packed=True)
    assert (fw / "devices" / "radar-1.json").exists()
    big = {"enc": "gzip+base64", "data": base64.b64encode(gzip.compress(b'{"a":"' + b"0" * 200000 + b'"}')).decode()}
    assert len(json.dumps(big)) < me.MAX_WIRE
    with pytest.raises(ValueError):
        me.decode(json.dumps(big).encode())


@pytest.mark.parametrize("bad", [b"not json", b"[1]", b'{"schema": "1"}', b"x" * 30000,
                                 json.dumps({"enc": "gzip+base64", "data": "!!"}).encode()])
def test_invalid_payloads_store_nothing(fw, bad):
    me.on_node(["radar-1", "fwgen", "description"], bad, True)
    me.on_node(["radar-1", "fwgen", "hash"], b"3f9a1c07be52", True)
    assert not (fw / "devices" / "radar-1.json").exists()


def test_another_nodes_prefix_a_bad_hash_and_a_foreign_topic_are_refused(fw):
    d = desc("radar-1")
    me.on_node(["radar-2", "fwgen", "description"], wire(d), True)       # names radar-1, arrives under radar-2
    me.on_node(["radar-2", "fwgen", "hash"], me.canonical_hash(d).encode(), True)
    me.on_node(["radar-1", "fwgen", "hash"], b"NOT-A-HASH", True)
    me.on_node(["Bad Node", "fwgen", "hash"], b"3f9a1c07be52", True)
    me.on_node(["radar-1", "fwgen", "other"], b"x", True)
    assert not list((fw / "devices").glob("*.json")) if (fw / "devices").exists() else True


def test_the_secrets_file_counts_by_key_name_and_a_missing_one_is_said(tmp_path, monkeypatch):
    f = tmp_path / "secrets.yaml"
    f.write_text("wifi_password: \"correct-horse-battery\"\nmqtt_host: 192.0.2.20\nota_password: abcdef123\n# api_key: commentedout\n")
    monkeypatch.setattr(me, "SECRETS_FILE", str(f))
    vals, why = me._secrets_from_file()
    assert vals == {"correct-horse-battery", "abcdef123"} and why == ""
    assert me.secret_in({"broker": "192.0.2.20"}, vals) == ""            # not a secret: allowed
    assert "house secret" in me.secret_in({"x": "abcdef123"}, vals)
    monkeypatch.setattr(me, "SECRETS_FILE", str(tmp_path / "missing.yaml"))
    vals2, why2 = me._secrets_from_file()
    assert vals2 == set() and "FileNotFoundError" in why2
    assert me._health()                                                     # the health page says it


def test_mac_suffix_accepts_the_name_with_its_hex_tail_and_nothing_else():
    d = {"schema": 1, "device": {"name": "air", "mac_suffix": True}}
    for ok in ("air", "air-3f2a1c"):
        me.check(d, ok)
    for bad in ("air-3F2A1C", "air-3f2a1", "air-3f2a1cd", "airx-3f2a1c", "other", "air-3f2a1c-1"):
        with pytest.raises(ValueError):
            me.check(d, bad)
    for no in ({"name": "air"}, {"name": "air", "mac_suffix": False}, {"name": "air", "mac_suffix": "yes"}):
        with pytest.raises(ValueError):                                   # no topic_prefix and no mac_suffix: refused as before
            me.check({"schema": 1, "device": no}, "air")
    me.check({"schema": 1, "device": {"name": "air", "topic_prefix": "air-7"}}, "air-7")


def test_with_mac_suffix_the_state_topics_start_with_tilde_and_are_expanded():
    d = {"schema": 1, "device": {"name": "air", "mac_suffix": True},
         "entities": [{"name": "CO2", "state_topic": "~/sensor/co2/state"}, {"name": "No topic"}]}
    me.check(d, "air-3f2a1c")
    assert me._topics_of(d, "air-3f2a1c") == {"air-3f2a1c/sensor/co2/state"}
    for bad in ("air/sensor/co2/state", "sensor/co2/state", "~sensor/co2/state", 5):
        with pytest.raises(ValueError):
            me.check({**d, "entities": [{"name": "CO2", "state_topic": bad}]}, "air")
    plain = {"schema": 1, "device": {"name": "air", "topic_prefix": "air"}, "entities": [{"state_topic": "air/sensor/x/state"}]}
    me.check(plain, "air")                                                 # without mac_suffix topics stay full
    assert me._topics_of(plain, "air") == {"air/sensor/x/state"}


def test_a_mac_suffix_description_matches_its_card_and_the_window_sees_real_topics(fw, monkeypatch):
    d = {"schema": 1, "device": {"name": "air", "mac_suffix": True},
         "entities": [{"name": "CO2", "state_topic": "~/sensor/co2/state"}]}
    card = {"id": "air1", "transport": "mqtt", "controls": [{"code": "co2", "state_topic": "air-3f2a1c/sensor/co2/state"}]}
    monkeypatch.setitem(core.DEVICES, "air1", card)
    feed("air-3f2a1c", d)
    rec = json.loads((fw / "devices" / "air-3f2a1c.json").read_text())
    assert rec["changes"] == {} and rec["card_gap"]["missing"] == []          # a first description: no change; nothing the card lacks once "~" is the prefix
    out = me.describe(card)
    assert out["description"]["entities"][0]["state_topic"] == "air-3f2a1c/sensor/co2/state"
    assert rec["description"]["entities"][0]["state_topic"] == "~/sensor/co2/state"   # the stored copy keeps what the hash covers


def test_secrets_are_refused_by_key_and_by_value(fw):
    for extra in ({"wifi": {"password": "abcdef"}}, {"mqtt": {"api_key": "zzzzzzzz"}}, {"note": "hunter2-secret-value"},
                  {"net": {"ssid": "home"}}):
        me._pending.clear()
        me.on_node(["radar-1", "fwgen", "description"], wire(desc(extra=extra)), True)
        assert "radar-1" not in me._pending
    assert me.secret_in(desc()) == ""


def test_a_hash_that_does_not_match_its_description_is_stored_but_flagged(fw):
    feed("radar-1", desc(), h="0" * 12)
    assert json.loads((fw / "devices" / "radar-1.json").read_text())["mismatch"] is True


def test_the_rate_limit_ignores_a_repeat_within_ten_seconds(fw):
    d = desc()
    feed("radar-1", d)
    before = (fw / "devices" / "radar-1.json").read_text()
    me.on_node(["radar-1", "fwgen", "hash"], me.canonical_hash(d).encode(), True)
    assert (fw / "devices" / "radar-1.json").read_text() == before


def test_the_window_cases(fw, monkeypatch):
    dev = {"id": "r1", "transport": "mqtt", "esphome_node": "radar-1", "controls": []}
    assert me.describe(dev)["case"] == 3                                  # no hash topic ever seen
    d = desc()
    feed("radar-1", d)
    out = me.describe(dev)
    assert out["case"] == 2 and out["build"] == {"known": False} and out["hash"] == me.canonical_hash(d)
    monkeypatch.setattr(me.time, "time", lambda: 9e12)                    # long after: the plain "known build" line
    assert me.describe(dev)["case"] == 1


def test_removal_clears_both_retained_topics_and_the_copy(fw, monkeypatch):
    sent = []
    monkeypatch.setattr(core, "_mqtt_pub", lambda cli, topic, payload=None, retain=False: sent.append((topic, payload, retain)))
    monkeypatch.setattr(core, "_mqtt_client", object())
    ha = ["homeassistant/sensor/radar-1/temp/config", "homeassistant/switch/radar-1/relay/config", "homeassistant/sensor/radar-10/temp/config"]
    monkeypatch.setattr(core, "_retained_topics", lambda filters, listen=2.5: list(ha))        # what the broker replays for the filter
    feed("radar-1", desc())
    me.on_removed({"id": "r1", "transport": "mqtt", "esphome_node": "radar-1", "controls": []})
    assert ("radar-1/fwgen/description", None, True) in sent and ("radar-1/fwgen/hash", None, True) in sent
    assert {t for t, p, r in sent if t.startswith("homeassistant/")} == set(ha[:2])            # its own HA configs, not radar-10's
    assert not (fw / "devices" / "radar-1.json").exists()


def test_registration_recomputes_the_hash_and_is_idempotent(fw, monkeypatch):
    monkeypatch.setattr(core, "is_house_token", lambda headers: True)
    from types import SimpleNamespace
    req = SimpleNamespace(headers={}, method="POST", url=SimpleNamespace(path="/api/fwgen/builds"))
    d = desc()
    h = me.canonical_hash(d)
    ok = me.register_build(me.BuildBody(hash=h, date="2026-10-18", fwgen={"version": "0.4"}, description=d, svg="<svg></svg>"), req)
    assert ok == {"ok": True, "known": False}
    assert me.register_build(me.BuildBody(hash=h, description=d), req) == {"ok": True, "known": True}
    assert me.get_build(h, req)["svg"] == "<svg></svg>"
    with pytest.raises(HTTPException) as e:
        me.register_build(me.BuildBody(hash="0" * 12, description=d), req)
    assert e.value.status_code == 400
    for bad in ("<svg><script>x</script></svg>", '<svg><a href="http://x"/></svg>', "<svg onload=\"x()\"></svg>"):
        with pytest.raises(HTTPException):
            me.register_build(me.BuildBody(hash=h, description=desc(extra={"n": bad[:3]}), svg=bad), req)
    with pytest.raises(HTTPException):
        me.register_build(me.BuildBody(hash=me.canonical_hash(desc(extra={"wifi": {"password": "abcdef"}})),
                                       description=desc(extra={"wifi": {"password": "abcdef"}})), req)


def test_only_the_house_or_the_builder_may_register(fw, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("FWGEN_BUILDER_TOKEN", "builder-token-0123456789")
    assert me._builder_token("builder-token-0123456789", "POST", "/api/fwgen/builds")
    assert me._builder_token("builder-token-0123456789", "GET", "/api/fwgen/builds/3f9a1c07be52")
    assert not me._builder_token("builder-token-0123456789", "POST", "/api/devices/x/control")
    assert not me._builder_token("wrong", "POST", "/api/fwgen/builds")
    monkeypatch.setattr(core, "is_house_token", lambda headers: False)
    req = SimpleNamespace(headers={"authorization": "Bearer nope"}, method="POST", url=SimpleNamespace(path="/api/fwgen/builds"))
    with pytest.raises(HTTPException) as e:
        me.get_build("3f9a1c07be52", req)
    assert e.value.status_code == 403


def test_a_hand_written_card_gets_a_calm_gap_and_only_a_real_change_an_alarm(fw, monkeypatch):
    card = {"id": "r1", "transport": "mqtt", "controls": [{"code": "t", "state_topic": "radar-1/sensor/t/state"}]}
    monkeypatch.setitem(core.DEVICES, "r1", card)
    d1 = desc("radar-1")
    d1["entities"] = [{"state_topic": "radar-1/sensor/t/state"}, {"state_topic": "radar-1/sensor/u/state"}]
    feed("radar-1", d1)
    rec = json.loads((fw / "devices" / "radar-1.json").read_text())
    assert rec["changes"] == {} and rec["card_gap"]["missing"] == ["radar-1/sensor/u/state"]      # an offer, no alarm
    me._seen.clear()
    d2 = copy_of(d1)
    d2["entities"].append({"state_topic": "radar-1/sensor/v/state"})
    d2["modules"] = [{"uid": "x"}]
    feed("radar-1", d2)
    rec = json.loads((fw / "devices" / "radar-1.json").read_text())
    assert rec["changes"] == {"added": ["radar-1/sensor/v/state"], "removed": []}


def copy_of(d):
    return json.loads(json.dumps(d))


def test_an_old_record_and_the_gap_on_read(fw, monkeypatch):
    card = {"id": "r1", "transport": "mqtt", "esphome_node": "radar-1", "controls": [{"code": "t", "state_topic": "radar-1/sensor/t/state"}]}
    monkeypatch.setitem(core.DEVICES, "r1", card)
    d = desc("radar-1")
    d["entities"] = [{"state_topic": "radar-1/sensor/t/state"}, {"state_topic": "radar-1/sensor/u/state"}]
    old = {"node": "radar-1", "state": "yash", "hash": me.canonical_hash(d), "description": d, "received": 1.0, "seen": 1.0, "mismatch": False,
           "build_known": True, "history": [], "changes": {"added": ["x"] * 7, "removed": ["y"] * 18, "device": "r1"}}     # before the rule: no "v"
    (fw / "devices").mkdir(parents=True, exist_ok=True)
    (fw / "devices" / "radar-1.json").write_text(json.dumps(old))
    out = me.describe(card)
    assert out["changes"] == {} and out["card_gap"]["missing"] == ["radar-1/sensor/u/state"]


def test_a_node_with_a_description_is_ignored_by_discovery(monkeypatch):
    monkeypatch.setattr(me, "_load", lambda kind, name: {"v": 2} if (kind, name) == ("devices", "air-3f2a1c") else None)
    assert me.discovery_ignore({"espname", "air-3f2a1c"}) and not me.discovery_ignore({"plain", "Bad Name"})
    assert me.discovery_ignore in core.DISCOVERY_IGNORE
