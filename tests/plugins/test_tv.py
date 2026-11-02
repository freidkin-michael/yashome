"""tv: dashboard-only plug-in, it loads and ships its ui.js."""
import app as core


def test_loaded_with_ui():
    m = next(p for p in core.PLUGINS if p["id"] == "tv")
    assert m["ui"] == "/plugins/tv/ui.js" and "ru" in m["i18n"]


def test_tv_key_is_a_stateless_kind_that_goes_to_the_tv_s_own_transport(monkeypatch):
    me = next(m for n, m in __import__("sys").modules.items() if n.endswith(".tv") and hasattr(m, "key_controls"))
    assert [c["code"] for c in me.key_controls(["ok", "up", "nope"])] == ["key_up", "key_ok"]       # the order of a remote
    sent = []
    monkeypatch.setitem(core.TRANSPORTS, "fake_tv", lambda cfg, code, val, kind: sent.append((cfg["id"], code, val, kind)))
    ctl = me.key_controls(["back"])[0]
    assert core.CONTROL_KINDS["tv_key"]({"id": "t", "transport": "fake_tv"}, ctl, None) == {"key": "back"}
    assert sent == [("t", "key_back", True, "tv_key")]
