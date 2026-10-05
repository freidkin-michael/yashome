"""Run by the core's tools/mkpasswd.py inside the home container with `env` (the parsed .env) and
ROOT (the checkout): zigbee2mqtt's secret.yaml, and its configuration.yaml from the template on
the first run. JSON strings are YAML double-quoted scalars: any password stays one string."""
import json
import os
import pathlib

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
serial = (env.get("Z2M_SERIAL") or "").strip().lower()
if not (data / "configuration.yaml").exists() and serial not in ("", "none", "external"):
    adapter = env.get("Z2M_ADAPTER") or "ember"
    text = pathlib.Path(__file__).with_name("configuration.example.yaml").read_text(encoding="utf-8")
    (data / "configuration.yaml").write_text(text.replace("adapter: ember", f"adapter: {adapter}", 1), encoding="utf-8")
    print(f"zigbee2mqtt: configuration.yaml created from the template (adapter: {adapter})")
print(f"zigbee2mqtt: secret.yaml written in {env.get('Z2M_DATA_DIR')}")
