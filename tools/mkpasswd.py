#!/usr/bin/env python3
"""Write config/mosquitto/dynamic-security.json (the broker's accounts and what each may do,
PBKDF2-SHA512 hashes) from the values in .env, with the accounts the plug-ins ask for in
setup/broker.json, then run each plug-in's setup/secrets.py (files it derives from .env). Runs
inside the home container, so passwords stay in files and environment, never on a command line."""
import base64
import hashlib
import json
import os
import re
import pathlib
import runpy
import secrets

ROOT = pathlib.Path(__file__).resolve().parent.parent
env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
# inside `docker compose run` the environment holds these values as compose parsed them from
# the same .env (quotes, inline comments) - exactly what the backend sees; the file is a fallback
_raw = re.sub(r"\s#.*$", "", os.environ.get("PLUGINS") or env.get("PLUGINS", ""))
_on = {x.strip().strip("\"'") for x in _raw.split(",")} - {""}
PLUGINS, _seen = [], set()
for base in ("plugins", "plugins-extra"):              # like the backend: a shipped name wins
    for d in sorted((ROOT / base).glob("*/")):
        if (d / "__init__.py").is_file() and not d.name.startswith("_") and d.name not in _seen \
                and ("*" in _on or d.name in _on):
            _seen.add(d.name)
            PLUGINS.append(d)
BROKER = {}
for d in PLUGINS:
    if (d / "setup/broker.json").is_file():
        try:
            BROKER[d.name] = json.loads((d / "setup/broker.json").read_text(encoding="utf-8"))
        except ValueError as e:
            raise SystemExit(f"{d.name}/setup/broker.json: {e}")
keys = {a.get(k) for b in BROKER.values() for a in b.get("accounts", []) for k in ("user_key", "password_key")}
env.update({k: v for k, v in os.environ.items() if k.startswith("MQTT_") or k in keys or k in env})   # as compose parsed them


OUT = ROOT / "config/mosquitto/dynamic-security.json"
try:
    _before = {c["username"]: c for c in json.loads(OUT.read_text(encoding="utf-8")).get("clients", [])}
except (OSError, ValueError, KeyError, TypeError):
    _before = {}


def client(user, password, role, iterations=20000):
    """A dynamic-security client: PBKDF2-SHA512 hash + salt, never the password. An unchanged
    password keeps its salt, so an unchanged .env writes the same file (no broker restart)."""
    old = _before.get(user) or {}
    try:
        if int(old["iterations"]) < iterations:
            raise ValueError("weaker than today's hashes: re-hashed once")
        salt = base64.b64decode(old["salt"])
        if base64.b64encode(hashlib.pbkdf2_hmac("sha512", password.encode(), salt, int(old["iterations"]))).decode() != old["password"]:
            raise ValueError("password changed")
        iterations = int(old["iterations"])
    except (KeyError, ValueError, TypeError):
        salt = secrets.token_bytes(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations)
    return {"username": user, "password": base64.b64encode(dk).decode(), "salt": base64.b64encode(salt).decode(),
            "iterations": iterations, "roles": [{"rolename": role}]}


def acl(kind, topic, allow=True, priority=0):
    return {"acltype": kind, "topic": topic, "allow": allow, "priority": priority}


def everything(topic="#"):
    return [acl(k, topic) for k in ("publishClientSend", "publishClientReceive", "subscribePattern", "unsubscribePattern")]


users = [(env.get("MQTT_USER", "home"), env.get("MQTT_PASSWORD", ""), "backend")]
plugin_roles, device_readonly = [], []
for name, b in BROKER.items():
    for a in b.get("accounts", []):
        role = str(a.get("role") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", role) or role in ("backend", "devices"):
            raise SystemExit(f"{name}/setup/broker.json: role {role!r} is not a plain name of its own")
        users.append((env.get(a.get("user_key", ""), a.get("user_default", "")), env.get(a.get("password_key", ""), ""), role))
        plugin_roles.append({"rolename": role, "acls": [x for t in a.get("topics", []) for x in everything(t)]})
    device_readonly += [t for t in b.get("device_readonly", []) if isinstance(t, str) and t]
if len({r["rolename"] for r in plugin_roles}) != len(plugin_roles):
    raise SystemExit("two plug-ins ask for the same broker role")
# device accounts (MQTT_DEVICE_USERS=esp,android; password in MQTT_<NAME>_PASSWORD): everything,
# except publishing to the filters in MQTT_BACKEND_ONLY - those only the backend may send
for name in [x.strip() for x in env.get("MQTT_DEVICE_USERS", "").split(",") if x.strip()]:
    users.append((name, env.get(f"MQTT_{re.sub('[^A-Z0-9]', '_', name.upper())}_PASSWORD", ""), "devices"))
missing = [u for u, p, _ in users if not p]
if missing:
    raise SystemExit(f"empty password for {missing} in .env")
if len({u for u, _, _ in users}) != len(users):
    raise SystemExit("two MQTT accounts with the same name in .env")
backend_only = [t for t in re.split(r"[\s,]+", env.get("MQTT_BACKEND_ONLY", "")) if t]
for t in backend_only:
    lv = t.split("/")
    if any(("+" in x or "#" in x) and x not in ("+", "#") for x in lv) or "#" in lv[:-1] or t.startswith("$"):
        raise SystemExit(f"MQTT_BACKEND_ONLY: {t!r} is not a valid topic filter (+ and # only as a whole level, # last)")
dynsec = {
    "defaultACLAccess": {"publishClientSend": False, "publishClientReceive": False, "subscribe": False, "unsubscribe": True},
    "clients": [client(u, p, r) for u, p, r in users],
    "roles": [
        {"rolename": "backend", "acls": everything() + everything("$SYS/#")},
        # devices read everything; they never write the state mirror nor a plug-in's bus it marks
        # read-only (zigbee2mqtt/#); home/cmd and homeassistant/ (discovery) stay open
        {"rolename": "devices", "acls": everything() + [acl("publishClientSend", t, False, 10)
                                                        for t in ["home/state/#"] + device_readonly + backend_only]},
    ] + plugin_roles,
    "groups": [],
}
tmp = OUT.with_suffix(".tmp")
tmp.write_text(json.dumps(dynsec, indent=1) + "\n", encoding="utf-8")
os.chmod(tmp, 0o644)        # mosquitto drops to its own uid before reading it; the entries are PBKDF2 hashes
tmp.replace(OUT)            # new inode: the read-only bind mount of the directory sees it
for old in ("passwd", "acl"):                 # what earlier versions wrote; the broker no longer reads them
    (ROOT / "config/mosquitto" / old).unlink(missing_ok=True)
print(f"broker accounts: {', '.join(f'{u} ({r})' for u, _, r in users)}"
      + (f"; only the backend publishes {' '.join(backend_only)}" if backend_only else ""))

for d in PLUGINS:            # the plug-ins' own derived files (a secret.yaml), with the parsed .env
    if (d / "setup/secrets.py").is_file():
        runpy.run_path(str(d / "setup/secrets.py"), init_globals={"env": dict(env), "ROOT": ROOT}, run_name="__setup__")
