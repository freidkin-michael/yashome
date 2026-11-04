"""HA-discovery onboarding skips the nodes that describe themselves (core.DISCOVERY_IGNORE)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_pure  # noqa: E402,F401  (sets HOME_STACK_ROOT to a private copy of the fixtures)
import app  # noqa: E402


def _found(*topics):
    return {"controls": [{"state_topic": t} for t in topics], "availability_topic": None, "skipped": [], "config_topics": [], "model": None, "title": None}


def test_a_described_node_is_not_offered_but_a_plain_one_is(monkeypatch):
    nodes = {"plain": _found("plain/sensor/t/state"), "air-3f2a1c": _found("air-3f2a1c/sensor/t/state"), "espname": _found("air_x/sensor/t/state")}
    monkeypatch.setattr(app, "_esphome_discover", lambda timeout=6.0, ignore=True: dict(nodes))
    monkeypatch.setattr(app, "DISCOVERY_IGNORE", [lambda names: bool(names & {"air-3f2a1c", "air_x"})])
    assert set(app._discovered()) == {"plain"}                       # by the node in the topic and by the state topic's prefix
    monkeypatch.setattr(app, "DISCOVERY_IGNORE", [])                 # no description (yet): onboarded as before
    assert set(app._discovered()) == {"plain", "air-3f2a1c", "espname"}


def test_a_failing_hook_never_hides_a_node(monkeypatch):
    def boom(names):
        raise RuntimeError("x")
    monkeypatch.setattr(app, "_esphome_discover", lambda timeout=6.0, ignore=True: {"n": _found("n/a/state")})
    monkeypatch.setattr(app, "DISCOVERY_IGNORE", [boom])
    assert set(app._discovered()) == {"n"}


def test_a_nodes_own_setting_is_marked_aux_and_a_real_control_is_not():
    """ent_cat config/diagnostic (a status LED, a recovery counter) is aux: the add dialog does not make the node a light."""
    led = app._esphome_control_from_ha("number", "status_led_brightness", {"ent_cat": "config", "stat_t": "n/s", "cmd_t": "n/c"})
    rec = app._esphome_control_from_ha("sensor", "recoveries", {"entity_category": "diagnostic", "stat_t": "n/r"})
    lamp = app._esphome_control_from_ha("light", "lamp", {"stat_t": "n/l", "cmd_t": "n/lc"})
    assert [c.get("aux") for c in led + rec] == [True, True] and all("aux" not in c for c in lamp)
    assert (led[0]["kind"], led[0]["advanced"], rec[0]["kind"]) == ("setting_int", True, "sensor")     # a folded setting, no dimmer
