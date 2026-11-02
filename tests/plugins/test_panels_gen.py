import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "devices" / "panels" / "firmware"))
import gen  # noqa: E402

NAME = "panel-1"
CYR = re.compile("[\u0400-\u04ff]")   # i18n: the language check


class EspLoader(yaml.SafeLoader):
    pass


def _tagged(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


EspLoader.add_multi_constructor("!", _tagged)


def layout(hw):
    w, h = (800, 480) if hw == "waveshare7" else (480, 320)
    st, cmd = "home/state/lamp_1", f"panel/{NAME}/cmd/lamp_1"
    return {
        "hw": hw, "w": w, "h": h, "i2c_sensors": True, "pir": True,
        "widgets": [
            {"id": "clk", "type": "clock", "x": 0, "y": 0, "w": 300, "h": 100, "fit": True},
            {"id": "dt", "type": "date", "x": 0, "y": 100, "w": 300, "h": 40, "size": 24,
             "state_topic": "home/state/clock/date"},
            {"id": "lbl", "type": "label", "x": 0, "y": 140, "w": 150, "h": 40, "text": "Room", "size": 20},
            {"id": "temp", "type": "sensor", "x": 150, "y": 140, "w": 150, "h": 40, "suffix": "\u00b0C",
             "decimals": 0, "fit": True, "state_topic": "home/state/sensor_1/temperature"},
            {"id": "occ", "type": "sensor", "x": 150, "y": 180, "w": 150, "h": 40, "text_on": "yes",
             "text_off": "no", "state_topic": "home/state/sensor_1/occupancy"},
            {"id": "tg", "type": "toggle", "x": 0, "y": 180, "w": 100, "h": 40, "text": "Lamp",
             "state_topic": f"{st}/state", "cmd_topic": f"{cmd}/state", "cmd_payload": "toggle"},
            {"id": "tg2", "type": "toggle", "x": 0, "y": 220, "w": 100, "h": 40, "text": "Lock",
             "state_topic": "home/state/lock_1/state", "cmd_topic": f"panel/{NAME}/cmd/lock_1/state",
             "cmd_on": "true", "cmd_off": "false"},
            {"id": "dim", "type": "dimmer", "x": 0, "y": 260, "w": 200, "h": 50, "text": "Lamp",
             "state_topic": f"{st}/brightness_pct", "onoff_topic": f"{st}/state",
             "cmd_topic": f"{cmd}/state", "dim_topic": f"{cmd}/brightness"},
            {"id": "btn", "type": "button", "x": 200, "y": 260, "w": 100, "h": 50, "text": "Go",
             "cmd_topic": f"panel/{NAME}/cmd/scene_1/run", "cmd_payload": "true"},
            {"id": "wx", "type": "weather", "x": 300, "y": 0, "w": 180, "h": 160},
            {"id": "ac", "type": "ac", "x": 300, "y": 160, "w": 180, "h": 160, "text": "AC",
             "power_cmd": f"panel/{NAME}/cmd/ac_1/power", "power_state": "home/state/ac_1/power",
             "temp_cmd": f"panel/{NAME}/cmd/ac_1/setpoint", "temp_state": "home/state/ac_1/setpoint",
             "mode_cmd": f"panel/{NAME}/cmd/ac_1/mode", "mode_state": "home/state/ac_1/mode",
             "fan_cmd": f"panel/{NAME}/cmd/ac_1/fan", "fan_state": "home/state/ac_1/fan",
             "room_state": "home/state/ac_1/room", "modes": [["cool", "Cool"], ["heat", "Heat"]],
             "fans": [["low", "Low"], ["max", "Max"]]},
            {"id": "rc", "type": "remote", "x": 0, "y": 320, "w": 200, "h": 160, "device": "tv_1",
             "buttons": [{"id": k, "label": k} for k in
                         ("power", "up", "down", "left", "right", "ok", "vol_up", "vol_down", "red", "Netflix")]},
        ],
    }


def topics(doc, text):
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("topic", "state_topic", "command_topic") and isinstance(v, str):
                    found.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    found += re.findall(r'publish\(\s*(?:std::string\()?"([^"]+)"', text)
    return [t.replace("${name}", NAME) for t in found]


SCREENS = [hw for hw, p in gen.profiles().items() if p["kind"] == "canvas"]   # the knob has tests of its own


def no_words(text):
    """A firmware has no language: Cyrillic only in a font's glyphs (the house's words come over MQTT)."""
    return not any(CYR.search(line) for line in text.splitlines() if "glyphs:" not in line)


@pytest.mark.parametrize("hw", SCREENS)
def test_full_layout(hw):
    files = gen.generate(NAME, layout(hw))
    text = files["main.yaml"].decode("utf-8")
    doc = yaml.load(text, Loader=EspLoader)
    assert "packages" not in doc and "api" not in doc
    assert doc["mqtt"]["topic_prefix"] == "panel/${name}"
    assert doc["substitutions"]["name"] == NAME
    assert doc["wifi"]["ap"]["password"] == "ap_password"
    ts = topics(doc, text)
    assert ts
    for t in ts:
        assert t.startswith(("home/state/", "home/weather", "home/clock/", f"panel/{NAME}/")), t
    assert f"panel/{NAME}/ip" in ts
    for f in re.findall(r"file: (icons/\S+\.png)", text):
        assert f in files and files[f][:4] == b"\x89PNG"
    assert no_words(text) and "GF_Cyrillic_Core" in text              # any house language shows
    assert gen.WORDS_TOPIC in ts and "id(g_wx).find(c)" in text
    assert "wx_sun" in text and "ic_ac_cool" in text and "ic_ir_ok" in text


def test_profiles():
    p = gen.profiles()
    assert {"waveshare7", "onx35"} <= set(p) and p["knob128"]["kind"] == "list"
    assert (p["onx35"]["w"], p["onx35"]["h"]) == (480, 320)
    assert p["waveshare7"]["kind"] == "canvas" and p["waveshare7"]["can"] == []


def test_unknown_hw():
    with pytest.raises(ValueError, match="waveshare7"):
        gen.generate(NAME, {"hw": "nope", "widgets": []})


def test_foreign_topic_rejected():
    lay = {"hw": "waveshare7", "widgets": [
        {"id": "t", "type": "toggle", "x": 0, "y": 0, "w": 10, "h": 10, "cmd_topic": "other/lamp/set"}]}
    with pytest.raises(ValueError, match="cmd_topic"):
        gen.generate(NAME, lay)


@pytest.mark.parametrize("hw", gen.list_profiles())
def test_starter(hw):
    files = gen.generate(NAME, {"hw": hw, "widgets": []}, starter=True)
    text = files["main.yaml"].decode("utf-8")
    doc = yaml.load(text, Loader=EspLoader)
    assert doc["esphome"]["name_add_mac_suffix"] is True
    assert '"panel/_new/" + flat' in text
    for key in ("hw", "code", "mac", "ip", "node"):
        assert f'\\"{key}\\":' in text
    for t in topics(doc, text):
        assert t.startswith(("home/state/", "home/weather", "home/clock/", f"panel/{NAME}/", "panel/_new/")), t
    assert no_words(text)
    assert "New panel" in text and "new_title" in text


def test_ascii_source():
    assert Path(gen.__file__).read_text(encoding="utf-8").isascii()
    assert gen.STRINGS["ru"]["net_wifi"] and gen.STRINGS["ru"]["wx"][0] and gen.STRINGS["ru"]["remote"]


def test_the_node_name_has_no_underscore():
    with pytest.raises(ValueError):
        gen.generate("panel_1", layout("waveshare7"))


def test_mqtt_hooks_publish_after_a_delay_and_drive_the_link_banner():
    doc = yaml.load(gen.generate(NAME, layout("waveshare7"))["main.yaml"].decode(), Loader=EspLoader)
    conn = doc["mqtt"]["on_connect"]["then"]
    text = yaml.dump(conn)
    d = next(i for i, step in enumerate(conn) if "delay" in step)
    assert not str(conn[d]["delay"]).startswith("0")
    assert any("publish" in str(step) for step in conn[d + 1:]) and not any("publish" in str(step) for step in conn[:d])
    assert "LV_OBJ_FLAG_HIDDEN" in text and "net_ban" in text
    assert {"script.execute": "net_status"} in doc["mqtt"]["on_disconnect"]["then"]
    script = yaml.dump(next(s for s in doc["script"] if s["id"] == "net_status"))
    assert "mqtt_client).is_connected()" in script and "lv_obj_clear_flag(id(net_ban)" in script
    assert gen.tr("en", "net_home") in script


def test_a_starter_takes_safe_mode_only_on_its_own_mac_topic():
    text = gen.generate("panel-waveshare7", {"hw": "waveshare7", "widgets": []}, starter=True)["main.yaml"].decode()
    doc = yaml.load(text, Loader=EspLoader)
    buttons = {b["id"]: b for b in doc["button"]}
    assert all(b.get("internal") and "command_topic" not in b for b in buttons.values())
    assert '"panel/_newcmd/" + flat + "/safe_mode"' in text and "id(btn_safe_mode).press()" in text


def _main(lay):
    return yaml.load(gen.generate(NAME, lay)["main.yaml"].decode(), Loader=EspLoader)


def _txt(steps):
    return "\n".join(x["lambda"] if isinstance(x, dict) and "lambda" in x else str(x) for x in steps)


def _on(doc, topic):
    return "\n".join(_txt(m["then"]) for m in doc["mqtt"]["on_message"] if m["topic"] == topic)


def test_one_away_topic_greys_the_widgets_of_a_device_that_is_not_live():
    doc = _main(layout("onx35"))
    topics = [m["topic"] for m in doc["mqtt"]["on_message"]]
    assert topics.count("panel/${name}/away") == 1 and not any(t.endswith("/online") for t in topics)
    assert len(topics) == len(set(topics))          # the toggle and the dimmer share lamp_1/state: one subscription
    away = _on(doc, "panel/${name}/away")
    for dev in ("sensor_1", "lamp_1", "lock_1"):
        assert f'life_("{dev}")' in away
    assert 'life_("ac_1")' not in away and 'life_("scene_1")' not in away     # not greyed: AC, a button
    assert "lv_obj_set_style_opa(id(w_dim_clipb), o_, 0);" in away and "id(g_dim_off1) = c_ == 'o';" in away
    assert "lv_obj_clear_flag(id(w_tg), LV_OBJ_FLAG_CLICKABLE)" in away
    bare = layout("onx35")
    bare["widgets"] = [w for w in bare["widgets"] if w["type"] in ("clock", "label", "button")]
    assert "panel/${name}/away" not in [m["topic"] for m in _main(bare)["mqtt"]["on_message"]]


def test_a_dimmer_tap_sends_and_waits_for_the_report():
    doc = _main(layout("onx35"))
    tap = _txt(doc["touchscreen"][0]["on_touch"])
    assert "g_dim_lvl1) =" not in tap and "g_dim_on1) =" not in tap and "lvgl.bar.update" not in tap
    assert "if (id(g_dim_off1)) return;" in tap
    assert "int cur = wt ? id(g_dim_wl1) : id(g_dim_lvl1);" in tap      # a second tap builds on the first
    assert "LV_OPA_40, LV_PART_INDICATOR" in tap
    for topic in ("home/state/lamp_1/brightness_pct", "home/state/lamp_1/state"):
        assert "id(g_dim_t1) = 0; lv_obj_set_style_bg_opa(id(w_dim), LV_OPA_COVER, LV_PART_INDICATOR);" in _on(doc, topic)
    ivl = _txt(doc["interval"][0]["then"])
    assert f"now_ - id(g_dim_t1) > {gen.CONFIRM_WAIT_MS}" in ivl and gen.CONFIRM_WAIT_MS == 5000


def test_the_device_of_a_widget():
    assert gen.device_of({"state_topic": "home/state/a/x", "cmd_topic": "home/cmd/b/x"}) == "a"
    assert gen.device_of({"cmd_topic": "home/cmd/b/x"}) == "b"
    assert gen.device_of({"state_topic": "home/weather"}) is None and gen.device_of({}) is None


def test_a_profiles_own_interval_joins_the_dimmers_wait(monkeypatch):
    real = gen.load_profile

    def with_interval(hw):
        prof = dict(real(hw))
        prof["lists"] = dict(prof.get("lists") or {}, interval="- interval: 30s\n  then:\n    - logger.log: tick\n")
        return prof
    monkeypatch.setattr(gen, "load_profile", with_interval)
    ivl = _main(layout("onx35"))["interval"]
    assert [i["interval"] for i in ivl] == ["250ms", "30s"]


def test_the_device_of_a_widget_in_the_builds_own_command_topics():
    assert gen.device_of({"cmd_topic": "panel/p/cmd/lamp 2/state"}) == "lamp 2"
    assert gen.device_of({"state_topic": "home/state/a"}) is None              # no channel: not a device topic
    assert gen.device_of({"cmd_topic": "panel/p/cmd/a"}) is None


def test_the_away_lambda_on_ids_inside_other_ids(tmp_path):
    """The firmware's own C++ (compiled with the host's g++) against the plug-in's payload."""
    import shutil
    import subprocess
    gxx = shutil.which("g++")
    if not gxx:
        print("SKIPPED: no g++ - the away lambda was not compiled")
        pytest.skip("no g++: the away lambda was not compiled")
    devs = ["lamp", "desk lamp", "sensor", "sensor:1", "o", "w"]
    lay = {"hw": "onx35", "widgets": [{"id": f"t{i}", "type": "toggle", "x": 0, "y": 0, "w": 50, "h": 30, "text": "T",
                                       "state_topic": f"home/state/{d}/state"} for i, d in enumerate(devs)]}
    body = _on(_main(lay), "panel/${name}/away")
    life = body[:body.index("};") + 2]
    src = tmp_path / "a.cpp"
    src.write_text("#include <string>\n#include <cstdio>\nint main(int c, char **v) { std::string x = v[1];\n" + life
                   + "\nfor (int i = 2; i < c; i++) printf(\"%c\", life_(v[i]));\nreturn 0; }\n")
    subprocess.run([gxx, "-std=c++17", "-o", str(tmp_path / "a"), str(src)], check=True)

    def chars(payload):
        return subprocess.run([str(tmp_path / "a"), payload, *devs], capture_output=True, text=True, check=True).stdout
    assert chars("/desk lamp#o/sensor:1#w/") == "lolwll"         # lamp and sensor stay live
    assert chars("/o#w/w#o/") == "llllwo"                        # ids that look like the states
    assert chars("/") == "llllll"


def test_the_away_key_cannot_match_inside_another_id_without_a_compiler():
    """Pinned where g++ is absent: the firmware's key is "/<id>#" and the plug-in writes "/<id>#o/",
    so "/lamp#" is not in the payload of an offline "desk lamp"."""
    lay = {"hw": "onx35", "widgets": [{"id": "t1", "type": "toggle", "x": 0, "y": 0, "w": 50, "h": 30, "text": "T",
                                       "state_topic": "home/state/lamp/state"}]}
    body = _on(_main(lay), "panel/${name}/away")
    assert 'std::string k_ = std::string("/") + d + "#";' in body and 'life_("lamp")' in body


def test_the_ac_card_has_one_power_toggle_that_waits_for_the_answer():
    lines = []
    w = {"id": "ac", "type": "ac", "x": 0, "y": 0, "w": 300, "h": 200, "power_cmd": "home/cmd/ac/power", "power_state": "home/state/ac/power"}
    gen.ac_emit(lines.append, w, "0xFFFFFF", 1)
    out = "\n".join(lines)
    assert "id: w_ac_pw" in out and "w_ac_pon" not in out and "w_ac_poff" not in out
    assert "on ? " in out and "LV_OPA_50" in out and "lv_timer_create" in out              # opposite of the reported, dimmed until it answers
    msg = "\n".join(l for _t, ls in gen.ac_msgs(w, 1, "en") for l in ls)
    assert "id(g_ac1_p) = on ? 1 : 0; id(g_ac1_w) = 0;" in msg and "LV_OPA_COVER" in msg


def test_the_ac_toggle_sends_on_or_off_from_the_reported_state_and_any_report_ends_the_wait():
    lines = []
    w = {"id": "ac", "type": "ac", "x": 0, "y": 0, "w": 300, "h": 200, "power_cmd": "c", "power_state": "s", "power_on": "ON", "power_off": "OFF"}
    gen.ac_emit(lines.append, w, "0xFFFFFF", 2)
    out = "\n".join(lines)
    assert "bool on = id(g_ac2_p) == 1;" in out and "std::string(on ? \"OFF\" : \"ON\")" in out       # explicit, from what the AC said last
    msg = "\n".join(l for _t, ls in gen.ac_msgs(w, 2, "en") for l in ls)
    assert "id(g_ac2_w) = 0;" in msg                       # a report from anyone (the AC remote too) settles it


def test_a_temperature_or_humidity_label_is_held_like_every_screen_and_decimals_of_its_own_win():
    held = gen.value_expr({"type": "temp"})
    assert "static int shown" in held and "fabsf(v - shown) >= 0.6f" in held
    assert "static int shown" in gen.value_expr({"type": "sensor", "state_topic": "home/state/hall/humidity"})
    assert gen.value_expr({"type": "sensor", "state_topic": "home/state/x/battery"}) == "std::string(x)"
    assert "static int shown" in gen.value_expr({"type": "temp", "decimals": 2})          # an older layout's decimals: ignored
    assert "%.1f" not in gen.value_expr({"type": "temp"})                                  # always whole, never tenths


def test_the_house_sends_the_words_a_firmware_shows():
    """WORDS_TOPIC: the screen's and knob's words and the weather's, in the house language; English is the fallback."""
    ru, en = gen.words("ru"), gen.words("en")
    assert set(ru) == set(gen.SCREEN_WORDS) | {"wx"} and en["net_wifi"] == "No Wi-Fi" and ru["net_wifi"] != en["net_wifi"]
    assert ru["wx"]["0"] and en["wx"]["0"] == "Clear"
    text = gen.generate(NAME, layout("waveshare7"))["main.yaml"].decode()
    for k in gen.SCREEN_WORDS:
        assert f"g_w_{k}" in text
    assert 'id(g_w_net_wifi)[0] ? (const char *) id(g_w_net_wifi).data() : "No Wi-Fi"' in text
    assert "restore_value: yes" in text


def test_an_ac_card_needs_the_power_state_or_its_button_could_only_send_on():
    w = {"id": "ac", "type": "ac", "x": 0, "y": 0, "w": 300, "h": 200, "power_cmd": "home/cmd/ac/power"}
    with pytest.raises(ValueError, match="no power_state"):
        gen.check_layout("p1", {"widgets": [w]})
    gen.check_layout("p1", {"widgets": [{**w, "power_state": "home/state/ac/power"}]})
