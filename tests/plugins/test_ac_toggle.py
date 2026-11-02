"""The air conditioner tile has ONE power button that shows the state (aria-pressed, title) and flips it; the view waits for
the device's answer (dimmed) instead of flipping at once."""
import pathlib
import re

STATIC = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "devices" / "panels" / "static"


def test_the_ac_tile_has_one_power_toggle_with_its_state():
    js = (STATIC / "widgets.js").read_text(encoding="utf-8")
    markup = re.search(r"function acMarkup\(w\) \{(.*?)\n\}", js, re.S).group(1)
    assert markup.count('<button class="pw" aria-pressed=') == 1 and "pon" not in markup and "poff" not in markup
    apply = re.search(r"function acApply\(.*?\n\}", js, re.S).group(0)
    assert "aria-pressed" in apply and "classList.remove('wait')" in apply


def test_the_view_flips_the_reported_state_and_waits_for_the_answer():
    view = (STATIC / "view.html").read_text(encoding="utf-8")
    power = re.search(r"function acPower\(w, pw\) \{(.*?)\n\}", view, re.S).group(1)
    assert "classList.add('wait')" in power and "acSend(w, 'power', !on, pw)" in power
    assert "if (!wait && L && L.values)" in view                       # no optimistic flip while it waits


def test_the_tap_sends_an_explicit_value_from_the_state_reported_at_tap_time_and_any_report_settles_it():
    view = (STATIC / "view.html").read_text(encoding="utf-8")
    power = re.search(r"function acPower\(w, pw\) \{(.*?)\n\}", view, re.S).group(1)
    assert "L.values.power === true" in power and "!on" in power and "toggle" not in power      # ON or OFF, never a flip key
    js = (STATIC / "widgets.js").read_text(encoding="utf-8")
    apply = re.search(r"function acApply\(.*?\n\}", js, re.S).group(0)
    assert "String(v.power === true) === want" in apply      # whoever made the change (the panel, the AC's own remote), the wait ends
