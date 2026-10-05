"""Parity: the same bridge/devices list and frames through the old core (Zigbee built in) and the
new core + zigbee plug-in. Prints a JSON snapshot of the z2m devices and their cached state."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types

core_dir, plugins_dir, devices_json = sys.argv[1], sys.argv[2], sys.argv[3]
fix = tempfile.mkdtemp()
shutil.copytree(pathlib.Path(core_dir) / "tests" / "fixtures", fix, dirs_exist_ok=True)
os.environ["HOME_STACK_ROOT"] = fix
sys.path.insert(0, core_dir)
import app  # noqa: E402  (after HOME_STACK_ROOT)
devs = json.load(open(devices_json))
sent = []
app._mqtt_client = types.SimpleNamespace(publish=lambda t, p=None, **k: sent.append(t) or types.SimpleNamespace(rc=0),
                                         is_connected=lambda: True)
app._bg.submit = lambda fn, *a: sent.append(("fire",) + a)
if plugins_dir != "-":
    app._load_plugins(pathlib.Path(plugins_dir), top="yashome_plugins")
    zb = sys.modules["yashome_plugins.zigbee"]
    handle = lambda parts, payload, retain: zb.on_message(parts, payload, retain)
else:
    def handle(parts, payload, retain):
        msg = types.SimpleNamespace(topic="/".join(parts), payload=payload, retain=retain)
        app._mqtt_on_message(None, None, msg)
handle(["zigbee2mqtt", "bridge", "state"], b'{"state":"online"}', True)
handle(["zigbee2mqtt", "bridge", "devices"], json.dumps(devs).encode(), True)
for d in list(app.DEVICES.values()):
    if d.get("transport") != "z2m":
        continue
    frame = {}
    for c in d["controls"]:
        k = c["kind"]
        frame[c["code"]] = "ON" if k == "switch" else 77 if k in ("bright", "sensor") and c.get("vtype") != "bool" else True
    frame.pop("action", None)
    handle(["zigbee2mqtt", d["id"]], json.dumps(frame).encode(), False)
    if any(c["kind"] == "trigger_text" for c in d["controls"]):
        handle(["zigbee2mqtt", d["id"]], b'{"action":"single"}', False)
first = next((k for k, d in app.DEVICES.items() if d.get("transport") == "z2m"), None)
if first:
    handle(["zigbee2mqtt", first, "availability"], b'{"state":"offline"}', False)
handle(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}', False)
off = {k: app._status_cache.get(k, {}).get("online") for k, d in app.DEVICES.items() if d.get("transport") == "z2m"}
handle(["zigbee2mqtt", "bridge", "state"], b'{"state":"online"}', False)
out = {}
for k, d in sorted(app.DEVICES.items()):
    if d.get("transport") != "z2m":
        continue
    st = dict(app._status_cache.get(k) or {})
    st.pop("last_event_ts", None)
    out[k] = {"device": {x: d.get(x) for x in ("id", "name", "category", "ieee", "model", "unsupported", "_skip_get")},
              "controls": [{x: v for x, v in c.items()} for c in d["controls"]],
              "state": st, "offline_when_bridge_down": off[k]}
out["_fired"] = sorted(str(x) for x in sent if isinstance(x, tuple))
out["_topics"] = sorted(set(x for x in sent if isinstance(x, str) and not x.startswith("home/state/")))
print(json.dumps(out, sort_keys=True, indent=1, default=str))
