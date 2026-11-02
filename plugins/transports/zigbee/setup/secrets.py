"""Run by the core's tools/mkpasswd.py inside the home container with `env` (the parsed .env) and
ROOT (the checkout): zigbee2mqtt's secret.yaml, its configuration.yaml from the template on the
first run, and the serial block of configuration.yaml in step with Z2M_SERIAL on every run.
JSON strings are YAML double-quoted scalars: any password stays one string."""
import json
import os
import pathlib
import re
import sys

ADAPTERS = ("ember", "zstack", "deconz", "zigate", "ezsp")

env = globals()["env"]
ROOT = globals()["ROOT"]
if not env.get("Z2M_DATA_DIR"):
    raise SystemExit("zigbee2mqtt: Z2M_DATA_DIR is empty - setup/secrets.sh sets it; nothing written")
data = ROOT / env["Z2M_DATA_DIR"]
data.mkdir(parents=True, exist_ok=True)
body = (f"mqtt_user: {json.dumps(env.get('MQTT_Z2M_USER', 'z2m'))}\n"
        f"mqtt_password: {json.dumps(env.get('MQTT_Z2M_PASSWORD'))}\n"
        f"auth_token: {json.dumps(env.get('Z2M_AUTH_TOKEN', ''))}\n")
secret = data / "secret.yaml"
if not secret.exists() or secret.read_text(encoding="utf-8") != body:
    if secret.exists():
        (data / ".secret-changed").touch()
    secret.write_text(body, encoding="utf-8")
os.chmod(secret, 0o600)
raw = (env.get("Z2M_SERIAL") or "").strip().strip("\"'")
serial = raw.lower()
conf = data / "configuration.yaml"
if serial not in ("", "none", "external"):
    adapter = env.get("Z2M_ADAPTER") or ""
    if adapter and adapter not in ADAPTERS:
        sys.exit(f"zigbee2mqtt: Z2M_ADAPTER={adapter!r} is not {', '.join(ADAPTERS)}")
    existed = conf.exists()
    if not existed:
        text = pathlib.Path(__file__).with_name("configuration.example.yaml").read_text(encoding="utf-8")
        conf.write_text(text, encoding="utf-8")
        print(f"zigbee2mqtt: configuration.yaml created from the template (adapter: {adapter or 'ember'})")
    text = conf.read_text(encoding="utf-8")
    m = re.search(r"^serial:[^\n]*\n(?:(?:[ \t]+[^\n]*|[ \t]*)\n)*", text, re.M)
    old = m.group(0) if m else ""
    # an empty Z2M_ADAPTER keeps the adapter the network runs on
    had = re.search(r"^[ \t]+adapter:[ \t]*([A-Za-z]+)", old, re.M)
    adapter = adapter or (had.group(1) if had else "ember")
    network = serial.startswith("tcp://")
    want = {"port": raw if network else "/dev/ttyUSB0", "adapter": adapter}
    if network:      # SMLIGHT / ZigStar socket: no hardware flow control
        want.update(baudrate="115200", rtscts="false")
    have = {k: v.strip("\"'") for k, v in
            re.findall(r"^[ \t]+(\w+):[ \t]*([^#\n]*?)[ \t]*(?:#[^\n]*)?$", old, re.M)}
    # values compared, not text: zigbee2mqtt rewrites the file in its own key order
    stale = any(have.get(k) != v for k, v in want.items()) or (
        not network and ("baudrate" in have or "rtscts" in have))
    if stale:
        keep = [ln for ln in old.splitlines()[1:]
                if ln.strip() and not re.match(r"[ \t]+(port|adapter|baudrate|rtscts):", ln)]
        new = "\n".join(["serial:"] + [f"  {k}: {v}" for k, v in want.items()] + keep) + "\n"
        tail = old[len(old.rstrip("\n")) + 1:]
        text = text.replace(old, new + tail, 1) if old else text.rstrip("\n") + "\n" + new
        conf.write_text(text, encoding="utf-8")
        if existed:
            (data / ".serial-changed").touch()
        print(f"zigbee2mqtt: serial -> {raw if network else 'the stick'} (adapter: {adapter})")
print(f"zigbee2mqtt: secret.yaml written in {env.get('Z2M_DATA_DIR')}")
