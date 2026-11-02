import re
import sys
from pathlib import Path

import pytest
import yaml

FW = Path(__file__).resolve().parents[2] / "plugins" / "devices" / "panels" / "firmware"
sys.path.insert(0, str(FW))
import gen  # noqa: E402
import gen_knob  # noqa: E402

NAME = "knob-1"
CYR = re.compile("[\u0400-\u04ff]")   # i18n: the language check
ALLOWED = ("home/state/", "home/weather", "home/clock/", f"panel/{NAME}/")
KEY = "k" * 24


class EspLoader(yaml.SafeLoader):
    pass


def _tagged(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


EspLoader.add_multi_constructor("!", _tagged)


def row(kind, i):
    st, cmd = f"home/state/dev_{i}", f"panel/{NAME}/cmd/dev_{i}"
    if kind == "toggle":
        return {"id": f"r{i}", "type": "toggle", "text": "Lamp", "state_topic": f"{st}/state",
                "cmd_topic": f"{cmd}/state", "state_on": "true", "cmd_on": "true", "cmd_off": "false"}
    if kind == "dimmer":
        return {"id": f"r{i}", "type": "dimmer", "state_topic": f"{st}/brightness_pct", "onoff_topic": f"{st}/state",
                "cmd_topic": f"{cmd}/state", "dim_topic": f"{cmd}/brightness", "state_max": 100, "cmd_max": 100}
    return {"id": f"r{i}", "type": "climate", "state_topic": f"{st}/setpoint", "onoff_topic": f"{st}/power",
            "cmd_topic": f"{cmd}/power", "dim_topic": f"{cmd}/setpoint", "cmd_on": "ON", "cmd_off": "OFF"}


def knob(rows):
    return {"hw": "knob128", "w": 240, "h": 240, "widgets": rows}


def build(lay, **kw):
    files = gen.generate(NAME if not kw.get("name") else kw.pop("name"), lay, **kw)
    text = files["main.yaml"].decode("utf-8")
    return files, text, yaml.load(text, Loader=EspLoader)


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
    found += re.findall(r'"((?:home|panel)/[^"]*)"', text)   # string literals in lambdas and tables
    return [t.replace("${name}", NAME) for t in found]


@pytest.mark.parametrize("kind", ["toggle", "dimmer", "climate"])
@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_rows_generate(kind, n):
    files, text, doc = build(knob([row(kind, i) for i in range(n)]))
    assert doc["mqtt"]["topic_prefix"] == "panel/${name}"
    assert doc["substitutions"]["name"] == NAME
    assert doc["wifi"]["ssid"] == "wifi_ssid" and doc["wifi"]["ap"]["password"] == "ap_password"
    assert doc["mqtt"]["broker"] == "mqtt_host" and doc["ota"][0]["password"] == "ota_password"
    assert "packages" not in doc and "api" not in doc
    ts = topics(doc, text)
    assert f"panel/{NAME}/ip" in ts and "home/clock/time" in ts
    for t in ts:
        assert t.startswith(ALLOWED), t
    for i in range(n):
        assert f"panel/{NAME}/cmd/dev_{i}/" in text and f"home/state/dev_{i}/" in text
    arcs = [w for w in doc["lvgl"]["pages"][0]["widgets"] if "arc" in w]
    assert len(arcs) == n * gen_knob.ticks_per_row(n)
    assert doc["font"][2]["glyphs"] == "".join(str(i + 1) for i in range(n))
    assert files["icons/yash-knob.png"][:4] == b"\x89PNG"
    assert "file: icons/yash-knob.png" in text
    buttons = {b["id"]: b for b in doc["button"]}
    assert buttons["btn_factory_reset"]["internal"] is True
    assert buttons["btn_restart"]["command_topic"] == "panel/${name}/restart/command"


def test_mixed_rows_and_presses():
    _, text, doc = build(knob([row("toggle", 0), row("dimmer", 1), row("climate", 2)]))
    clicks = doc["binary_sensor"][0]["on_click"]
    assert "% 3" in str(clicks[0]["then"]) and clicks[1]["then"] == [{"script.execute": "toggle_sel"}]
    assert "tmin[3] = {0, 0, 16}" in text


def test_mqtt_publishes_after_a_delay():
    _, _, doc = build(knob([row("dimmer", 0)]))
    conn = doc["mqtt"]["on_connect"]["then"]
    d = next(i for i, step in enumerate(conn) if "delay" in step)
    assert any("publish" in str(s) for s in conn[d + 1:]) and not any("publish" in str(s) for s in conn[:d])


@pytest.mark.parametrize("bad", [
    [row("toggle", i) for i in range(5)],
    [{"id": "c", "type": "clock"}],
    [{"id": "s", "type": "sensor", "state_topic": "home/state/x/y"}],
])
def test_bad_rows(bad):
    with pytest.raises(ValueError):
        gen.generate(NAME, knob(bad))


def test_foreign_topic_rejected():
    r = row("toggle", 0)
    r["cmd_topic"] = "home/cmd/dev_0/state"
    with pytest.raises(ValueError, match="cmd_topic"):
        gen.generate(NAME, knob([r]))


def test_the_node_name_rule():
    with pytest.raises(ValueError):
        gen.generate("knob_1", knob([row("toggle", 0)]))


def test_starter():
    files, text, doc = build({"hw": "knob128", "widgets": [row("dimmer", 0)]}, starter=True)
    assert doc["esphome"]["name_add_mac_suffix"] is True
    assert '"panel/_new/" + flat' in text
    for key in ("hw", "code", "mac", "ip", "node"):
        assert f'\\"{key}\\":' in text
    assert '\\"hw\\":\\"knob128\\"' in text
    assert '"panel/_newcmd/" + flat + "/safe_mode"' in text
    assert "if (p == code) id(btn_safe_mode).press();" in text
    buttons = {b["id"]: b for b in doc["button"]}
    assert all(b.get("internal") and "command_topic" not in b for b in buttons.values())
    assert "dev_0" not in text and [m["topic"] for m in doc["mqtt"]["on_message"]] == [gen.WORDS_TOPIC]   # only the house's words
    for t in topics(doc, text):
        assert t.startswith(ALLOWED + ("panel/_new/", "panel/_newcmd/")), t
    assert "New panel" in text and not any(CYR.search(x) for x in text.splitlines() if "glyphs:" not in x)
    assert "icons/yash-knob.png" in files


def test_service_key():
    _, text, doc = build(knob([row("toggle", 0)]), service_key=KEY)
    buttons = {b["id"]: b for b in doc["button"]}
    assert buttons["btn_restart"]["internal"] is True and buttons["btn_safe_mode"]["internal"] is True
    msgs = {m["topic"]: str(m["then"]) for m in doc["mqtt"]["on_message"]}
    for what in ("restart", "safe_mode"):
        assert f'if (x == "{KEY}") id(btn_{what}).press();' in msgs[f"panel/{NAME}/{what}/command"]
    with pytest.raises(ValueError):
        gen.generate(NAME, knob([row("toggle", 0)]), service_key="short")


def test_no_language_the_house_sends_its_words():
    _, text, doc = build(knob([row("dimmer", 0), row("climate", 1)]))
    assert not any(CYR.search(x) for x in text.splitlines() if "glyphs:" not in x)
    assert "GF_Cyrillic_Core" in text and set(gen_knob.tr("ru", "knob_off")) <= set(doc["font"][1]["glyphs"])
    on_msg = {m["topic"]: m for m in doc["mqtt"]["on_message"]}
    assert gen.WORDS_TOPIC in on_msg and "id(refresh_ui).execute();" in str(on_msg[gen.WORDS_TOPIC])
    assert set(gen_knob.words("ru")) == set(gen_knob.KNOB_WORDS) and gen_knob.words("en")["knob_off"] == "off"


def test_profiles():
    p = gen.profiles()
    assert p["knob128"]["chip"] == "ESP32-C3" and p["knob128"]["kind"] == "list"
    assert (p["knob128"]["w"], p["knob128"]["h"]) == (240, 240)
    assert set(p["knob128"]["can"]) == {"toggle", "dimmer", "climate"}


def test_ascii_source_and_strings():
    assert Path(gen_knob.__file__).read_text(encoding="utf-8").isascii()
    for key in gen_knob.EN:
        assert gen.STRINGS["ru"][key]


def test_a_dimmer_without_a_switch_channel_takes_on_off_from_its_level():
    r = row("dimmer", 1)
    del r["onoff_topic"]
    _, text, doc = build(knob([r]))
    body = [m for m in doc["mqtt"]["on_message"] if m["topic"] == r["state_topic"]]
    assert len(body) == 1 and "id(g_on)[0] = id(g_lvl)[0] > 0" in str(body[0]) and "g_lvl)[0] = (int)" in str(body[0])


def test_scaling_floor_and_the_one_row_globals():
    r = dict(row("dimmer", 1), state_max=255, cmd_max=255)
    _, text, doc = build(knob([r]))
    assert "* 100.0f / 255" in text and "cmax[1]  = {255}" in text and "base <= 1" in text
    assert "lroundf(p * cmax[s] / 100.0f)" in text
    assert "initial_value: '{false, false}'" in text or "bool[2]" in text


def test_the_link_banner_follows_wifi_and_the_broker():
    _, text, doc = build(knob([row("toggle", 1)]))
    assert {"script.execute": "net_status"} in doc["wifi"]["on_connect"]
    assert "net_status" in str(doc["mqtt"]["on_connect"]) and "net_status" in str(doc["mqtt"]["on_disconnect"])


def test_an_empty_knob_is_logo_clock_and_status():
    _, text, doc = build(knob([]))
    assert "yash-knob.png" in text and "home/clock/time" in text and "name_add_mac_suffix" not in text


def test_a_builder_made_switchless_dimmer_is_switched_by_its_level():
    r = row("dimmer", 1)
    for k in ("onoff_topic", "cmd_topic"):
        del r[k]
    _, text, doc = build(knob([r, row("toggle", 2)]))
    assert "const bool lvlsw[2] = {true, false};" in text
    assert 'if (lvlsw[s]) id(mqtt_client).publish(cmd[s], std::string("0"));' in text
    assert "int p = id(g_last)[s] > 0 ? id(g_last)[s] : 100;" in text and "id(g_last)[s] = p;" in text
    with pytest.raises(ValueError):
        t2 = row("toggle", 3)
        del t2["cmd_topic"]
        build(knob([t2]))
