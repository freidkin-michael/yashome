"""presence host piece (host/phone-presence.sh) with ip, arping, ping and mosquitto_pub stubbed on PATH:
what it publishes on a first start, with the phone there or not, and what it refuses."""
import os
import pathlib
import subprocess
import sys
import time

import pytest

me = sys.modules["yashome_plugins.presence"]
SCRIPT = pathlib.Path(me.__file__).parent / "host/phone-presence.sh"
MAC = "00:11:22:33:44:55"
STUBS = {
    "ip": """#!/bin/sh
case "$*" in
  "route show default") [ -n "$NO_ROUTE" ] || echo "default via 192.0.2.1 dev eth9 proto dhcp";;
  "-4 neigh show dev eth9") [ -n "$PRESENT" ] && echo "192.0.2.7 lladdr $MAC REACHABLE";;
  "-4 addr show eth9") echo "    inet 192.0.2.5/24 brd 192.0.2.255 scope global eth9";;
esac
exit 0
""",
    "arping": '#!/bin/sh\n[ -n "$PRESENT" ] && echo "Unicast reply from 192.0.2.7 [$MAC] 1.0ms"\nexit 0\n',
    "ping": '#!/bin/sh\n[ -n "$PRESENT" ]\n',
    "mosquitto_pub": '#!/bin/sh\necho "$*" >> "$PUB_LOG"\n',
}


def run(tmp_path, present, macs=MAC, no_route=False, until=None, password="pw"):
    """Start the script, wait (10 s at most) for `until` in what it published, stop it."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    log = tmp_path / "pub.log"
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "PRESENCE_MACS": macs, "PRESENCE_INTERVAL": "0.2",
           "PRESENCE_SWEEP_GAP": "999999999", "PRESENCE_STATE_DIR": str(tmp_path / "state"),
           "MQTT_PRESENCE_PASSWORD": password, "PUB_LOG": str(log), "MAC": MAC,
           "PRESENT": "1" if present else "", "NO_ROUTE": "1" if no_route else ""}
    proc = subprocess.Popen(["bash", str(SCRIPT)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and proc.poll() is None:
        if until and log.exists() and until in log.read_text():
            break
        time.sleep(0.05)
    rc = proc.poll()
    if rc is None:
        proc.kill()
        proc.wait()
    return rc, (log.read_text() if log.exists() else "")


def test_a_phone_there_is_home_without_an_event(tmp_path):
    rc, out = run(tmp_path, present=True, until="presence/unseen_days")
    assert rc is None and "-r -t presence/state -m ON" in out and "presence/event" not in out
    assert oct((tmp_path / "state/mosquitto_pub").stat().st_mode & 0o777) == "0o600"


def test_a_first_start_with_nobody_home_is_away_at_once_and_no_departure(tmp_path):
    rc, out = run(tmp_path, present=False, until="presence/unseen_days")
    assert "-r -t presence/state -m OFF" in out and "-m ON" not in out and "presence/event" not in out
    assert "presence/unseen_days -m 0" in out


def test_a_restart_keeps_the_grace_of_a_phone_seen_just_before(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state/last_seen").write_text(str(int(time.time()) - 60))
    rc, out = run(tmp_path, present=False, until="presence/unseen_days")
    assert "-r -t presence/state -m ON" in out and "-m OFF" not in out and "presence/event" not in out


def test_a_second_start_with_nobody_ever_seen_stays_away(tmp_path):
    run(tmp_path, present=False, until="presence/unseen_days")
    (tmp_path / "pub.log").unlink()
    rc, out = run(tmp_path, present=False, until="presence/unseen_days")
    assert "-r -t presence/state -m OFF" in out and "-m ON" not in out and "presence/event" not in out


@pytest.mark.parametrize("macs", ["44:55", "00-11-22-33-44-55", "00:11:22:33:44:55,zz"])
def test_a_bad_mac_is_refused(tmp_path, macs):
    rc, out = run(tmp_path, present=True, macs=macs)
    assert rc == 2 and out == ""


def test_no_macs_or_no_password_is_a_setup_error(tmp_path):
    assert run(tmp_path, present=True, macs="")[0] == 2
    assert run(tmp_path, present=True, password="")[0] == 2 and not (tmp_path / "pub.log").exists()


def test_no_interface_exits_so_systemd_retries(tmp_path):
    rc, out = run(tmp_path, present=True, no_route=True)
    assert rc == 1 and out == ""


def test_the_sources_hold_no_secret_on_a_command_line():
    text = SCRIPT.read_text()
    assert "-P \"$" not in text.replace("printf -- '-h %s\\n-p %s\\n-u %s\\n-P %s\\n'", "")
    assert os.access(SCRIPT, os.X_OK)
