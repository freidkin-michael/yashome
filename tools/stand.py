#!/usr/bin/env python3
"""A throwaway stand for the device-state behaviour (docs/device-state.md): its own mosquitto in docker
on 127.0.0.1:18830 and this checkout's backend on 127.0.0.1:18766, with a house of made-up devices in
a temporary directory. It never touches a running house: other ports, other container, its own token.
STAND_PLUGINS (default none) names the plug-ins it loads, e.g. panels.

  tools/stand.py up | down | restart          start, stop, restart the backend (the broker stays)
  tools/stand.py pub TOPIC PAYLOAD [-r]       publish as a device (-r: retained)
  tools/stand.py status ID                    the backend's status of a device
  tools/stand.py cmd ID CODE VALUE            POST /api/devices/ID/control
  tools/stand.py scenario NAME                back-different, back-same, report-first, restart-offline, all
"""
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

CORE = pathlib.Path(__file__).resolve().parent.parent
STAND = pathlib.Path(os.environ.get("STAND_DIR") or pathlib.Path(tempfile.gettempdir()) / "yashome-stand")
BROKER, PORT, API = "yashome-stand-mqtt", 18830, 18766
DEVICES = [   # a dimmer with an LWT, a sensor without one, a lock (a confirm channel)
    {"id": "dim1", "name": "Dimmer", "category": "switch", "availability_topic": "stand/dim1/status",
     "controls": [{"kind": "switch", "code": "power", "state_topic": "stand/dim1/power", "command_topic": "stand/dim1/power/set"},
                  {"kind": "bright", "code": "brightness", "min": 0, "max": 255, "state_topic": "stand/dim1/brightness",
                   "command_topic": "stand/dim1/brightness/set"}]},
    {"id": "door1", "name": "Door", "category": "sensor",
     "controls": [{"kind": "sensor", "code": "contact", "vtype": "bool", "state_topic": "stand/door1/contact"}]},
    {"id": "lock1", "name": "Lock", "category": "switch", "availability_topic": "stand/lock1/status",
     "controls": [{"kind": "switch", "code": "locked", "confirm": True, "state_topic": "stand/lock1/locked",
                   "command_topic": "stand/lock1/locked/set"}]},
]


def _sh(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _token() -> str:
    return (STAND / "token").read_text().strip()


def _api(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{API}{path}", method="POST" if body is not None else "GET",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def pub(topic, payload, retain=False):
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.connect("127.0.0.1", PORT)
    c.loop_start()
    c.publish(topic, payload, retain=retain).wait_for_publish(5)
    c.loop_stop()
    c.disconnect()


def _backend_start():
    env = {"PATH": "/usr/bin:/bin", "HOME": str(STAND / "home"), "HOUSE_ROOT": str(STAND / "house"),
           "DASHBOARD_TOKEN": _token(), "PLUGINS": os.environ.get("STAND_PLUGINS", ""),
           "MQTT_HOST": "127.0.0.1", "MQTT_PORT": str(PORT), "HOST_SOCKETS_DIR": str(STAND / "no-sockets")}   # no real wake, no tablet
    log = open(STAND / "backend.log", "a")
    p = subprocess.Popen([str(CORE / ".venv" / "bin" / "uvicorn"), "app:app", "--host", "127.0.0.1", "--port", str(API)],
                         cwd=CORE, env=env, stdout=log, stderr=log)
    (STAND / "backend.pid").write_text(str(p.pid))
    for _ in range(60):
        try:
            if _api("/api/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    sys.exit(f"the backend did not start: {STAND / 'backend.log'}")


def _backend_stop():
    pid = STAND / "backend.pid"
    if pid.exists():
        try:
            os.kill(int(pid.read_text()), 15)
        except (ProcessLookupError, ValueError):
            pass
        pid.unlink()
        time.sleep(1)


def up():
    if _sh("docker", "ps", "-q", "-f", f"name=^{BROKER}$").stdout.strip():
        sys.exit(f"{BROKER} runs already: tools/stand.py down first")
    shutil.rmtree(STAND, ignore_errors=True)
    (STAND / "home").mkdir(parents=True)
    shutil.copytree(CORE / "tests" / "fixtures", STAND / "house")
    (STAND / "house" / "mqtt_devices.json").write_text(json.dumps(DEVICES, indent=1))
    (STAND / "token").write_text(secrets.token_urlsafe(24))
    _sh("docker", "run", "--rm", "-d", "--name", BROKER, "-p", f"127.0.0.1:{PORT}:1883",
        "eclipse-mosquitto:2", "mosquitto", "-c", "/mosquitto-no-auth.conf")
    time.sleep(1)
    _backend_start()
    print(f"stand up: broker 127.0.0.1:{PORT}, backend http://127.0.0.1:{API}, token in {STAND / 'token'}")


def down():
    _backend_stop()
    _sh("docker", "rm", "-f", BROKER, check=False)
    print("stand down")


def status(dev):
    code, st = _api(f"/api/devices/{dev}/status")
    keep = {k: st.get(k) for k in ("online", "waiting", "offline_since", "values")} if isinstance(st, dict) else st
    return code, keep


def _show(label, dev):
    print(f"  {label:<38} {status(dev)[1]}")


def scenario(name):
    steps = {
        "back-different": [("stand/dim1/status", "online"), ("stand/dim1/power", "ON"), ("stand/dim1/status", "offline"),
                           ("stand/dim1/status", "online"), ("stand/dim1/power", "OFF")],
        "back-same": [("stand/dim1/status", "online"), ("stand/dim1/power", "ON"), ("stand/dim1/status", "offline"),
                      ("stand/dim1/status", "online"), ("stand/dim1/power", "ON")],
        "report-first": [("stand/dim1/status", "online"), ("stand/dim1/power", "ON"), ("stand/dim1/status", "offline"),
                         ("stand/dim1/power", "OFF"), ("stand/dim1/status", "online")],
    }
    names = list(steps) + ["restart-offline"] if name == "all" else [name]
    for n in names:
        print(f"== {n}")
        if n == "restart-offline":
            for t, p in (("stand/dim1/status", "online"), ("stand/dim1/power", "ON")):
                pub(t, p, retain=True)
            time.sleep(0.5)
            _backend_stop()
            pub("stand/dim1/status", "offline", retain=True)          # gone while the backend was down
            _backend_start()
            time.sleep(1)
            _show("after the restart (expect offline)", "dim1")
            continue
        for t, p in steps[n]:
            pub(t, p, retain=t.endswith("/status"))
            time.sleep(0.4)
            _show(f"{t} {p}", "dim1")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    elif cmd == "restart":
        _backend_stop()
        _backend_start()
    elif cmd == "pub":
        pub(args[0], args[1], retain="-r" in args[2:])
    elif cmd == "status":
        print(json.dumps(status(args[0])[1]))
    elif cmd == "cmd":
        v = args[2]
        val = True if v.lower() in ("true", "on") else False if v.lower() in ("false", "off") else int(v) if v.isdigit() else v
        print(_api(f"/api/devices/{args[0]}/control", {"code": args[1], "value": val}))
    elif cmd == "scenario":
        scenario(args[0] if args else "all")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
