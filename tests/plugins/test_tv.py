"""tv: dashboard-only plug-in, it loads and ships its ui.js."""
import app as core


def test_loaded_with_ui():
    m = next(p for p in core.PLUGINS if p["id"] == "tv")
    assert m["ui"] == "/plugins/tv/ui.js" and "ru" in m["i18n"]
