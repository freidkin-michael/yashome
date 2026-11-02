"""The panel generators' output, file by file, against tests/plugins/data/panels_golden.json.
After a change to the output on purpose: python tests/plugins/test_panels_golden.py --update,
then read the diff of the generated YAML (--dump DIR writes it) before committing."""
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "plugins" / "devices" / "panels" / "firmware"))
import gen  # noqa: E402
import test_panels_gen as tg  # noqa: E402
import test_panels_knob as tk  # noqa: E402

GOLDEN = HERE / "data" / "panels_golden.json"


def cases():
    out = {}
    for hw in tg.SCREENS:
        full = tg.layout(hw)
        out[f"{hw}.full"] = (tg.NAME, full, {})
        out[f"{hw}.starter"] = (tg.NAME, {"hw": hw, "widgets": []}, {"starter": True})
        out[f"{hw}.service_key"] = (tg.NAME, full, {"service_key": "k" * 32})
    hw = tg.SCREENS[0]
    for rot in (90, 270):
        out[f"{hw}.rot{rot}"] = (tg.NAME, dict(copy.deepcopy(tg.layout(hw)), rotation=rot), {})
    out[f"{hw}.ntp_timezone"] = (tg.NAME, dict(copy.deepcopy(tg.layout(hw)), timezone="Europe/Paris",
                                     ntp_servers=["a.example", "b.example", "c.example", "d.example"]), {})
    for big in tg.SCREENS[1:]:
        out[f"{big}.rot180"] = (tg.NAME, dict(copy.deepcopy(tg.layout(big)), rotation=180), {})
    extra = copy.deepcopy(tg.layout(hw))
    extra["widgets"] += [{"id": "tmp", "type": "temp", "x": 0, "y": 0, "w": 80, "h": 30, "suffix": "C",
                          "state_topic": "home/state/t_1/temperature"},
                         {"id": "plain", "type": "toggle", "x": 80, "y": 0, "w": 80, "h": 30, "text": "Fan",
                          "state_topic": "home/state/f_1/state", "cmd_topic": f"panel/{tg.NAME}/cmd/f_1/state"}]
    out[f"{hw}.temp_and_plain_toggle"] = (tg.NAME, extra, {})
    lone = copy.deepcopy(tg.layout(hw))
    lone["widgets"] = [dict(w, onoff_topic=None) if w["type"] == "dimmer" else w for w in lone["widgets"]]
    lone["widgets"] = [{k: v for k, v in w.items() if v is not None} for w in lone["widgets"]]
    out[f"{hw}.dimmer_without_onoff"] = (tg.NAME, lone, {})
    for kinds in (("toggle",), ("dimmer",), ("climate",), ("toggle", "dimmer", "climate", "dimmer")):
        out["knob." + "-".join(kinds)] = (tk.NAME, tk.knob([tk.row(k, i) for i, k in enumerate(kinds)]), {})
    out["knob.starter"] = (tk.NAME, {"hw": "knob128", "widgets": []}, {"starter": True})
    mix = tk.knob([tk.row(k, i) for i, k in enumerate(("toggle", "dimmer", "climate"))])
    out["knob.mix.service_key"] = (tk.NAME, mix, {"service_key": "k" * 32})
    out["knob.mix.rot180_ntp_tz"] = (tk.NAME, dict(copy.deepcopy(mix), rotation=180, timezone="Europe/Paris",
                                                   ntp_servers=["a.example", "b.example", "c.example", "d.example"]), {})
    return out


def digests(dump=None):
    res = {}
    for case, (name, lay, kw) in sorted(cases().items()):
        for fn, data in sorted(gen.generate(name, copy.deepcopy(lay), **kw).items()):
            res[f"{case}/{fn}"] = hashlib.sha256(data).hexdigest()
            if dump and fn.endswith(".yaml"):
                Path(dump, f"{case}.{fn}").write_bytes(data)
    return res


def test_the_generated_files_are_the_golden_ones():
    want = json.loads(GOLDEN.read_text())
    got = digests()
    assert sorted(got) == sorted(want), "cases changed: --update"
    assert [k for k in sorted(got) if got[k] != want[k]] == [], "output changed: read it (--dump), then --update"


if __name__ == "__main__":
    if "--dump" in sys.argv:
        Path(sys.argv[sys.argv.index("--dump") + 1]).mkdir(parents=True, exist_ok=True)
    d = digests(sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None)
    if "--update" in sys.argv:
        GOLDEN.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    print(len(d), "files")
