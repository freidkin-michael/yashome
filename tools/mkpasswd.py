#!/usr/bin/env python3
"""Write config/mosquitto/passwd (PBKDF2-SHA512, the $7$ format mosquitto 2 uses) and
config/zigbee2mqtt/secret.yaml from the values in .env. Runs inside the home container,
so passwords stay in files and environment, never on a command line."""
import base64
import hashlib
import os
import pathlib
import secrets
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")


def entry(user, password, iterations=101):
    salt = secrets.token_bytes(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations)
    return f"{user}:$7${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


users = [(env.get("MQTT_USER", "home"), env.get("MQTT_PASSWORD", "")),
         (env.get("MQTT_Z2M_USER", "z2m"), env.get("MQTT_Z2M_PASSWORD", ""))]
missing = [u for u, p in users if not p]
if missing:
    raise SystemExit(f"empty password for {missing} in .env")
pw = ROOT / "config/mosquitto/passwd"
tmp = pw.with_suffix(".tmp")
tmp.write_text("\n".join(entry(u, p) for u, p in users) + "\n", encoding="utf-8")
os.chmod(tmp, 0o644)        # mosquitto drops to its own uid before reading it; the entries are PBKDF2 hashes
tmp.replace(pw)             # new inode: the read-only bind mount of the directory sees it
print(f"passwd: {', '.join(u for u, _ in users)}")

# ACL: zigbee2mqtt may touch only its own tree; the backend (and your nodes, on the same
# account) everything. Without it any account could drive home/cmd/# or forge the device list.
acl = ROOT / "config/mosquitto/acl"
(home_u, _), (z2m_u, _) = users
tmp = acl.with_suffix(".tmp")
tmp.write_text(f"user {home_u}\ntopic readwrite #\ntopic read $SYS/#\n\nuser {z2m_u}\ntopic readwrite zigbee2mqtt/#\n",
               encoding="utf-8")
os.chmod(tmp, 0o644)
tmp.replace(acl)
print("acl: written")

z2m = ROOT / "config/zigbee2mqtt"
(z2m / "secret.yaml").write_text(
    f"mqtt_user: {env.get('MQTT_Z2M_USER', 'z2m')}\nmqtt_password: {env.get('MQTT_Z2M_PASSWORD')}\n"
    f"auth_token: {env.get('Z2M_AUTH_TOKEN', '')}\n", encoding="utf-8")
os.chmod(z2m / "secret.yaml", 0o600)
if not (z2m / "configuration.yaml").exists() and (z2m / "configuration.example.yaml").exists():
    shutil.copy(z2m / "configuration.example.yaml", z2m / "configuration.yaml")
    print("zigbee2mqtt: configuration.yaml created from the example (adapter: ember - change it for a non-Silicon-Labs stick)")
print("zigbee2mqtt: secret.yaml written")
