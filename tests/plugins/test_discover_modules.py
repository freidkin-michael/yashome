"""The shipped discovery modules (discover.py of computers and panels) under the core's runner: the same wire
formats and files as before, and each stops on the runner's stop event."""
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RUNNER = _load("yashome_discover_runner_t", ROOT / "discover" / "runner.py")


def _free_udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_both_shipped_modules_are_found_when_their_plugins_are_on():
    names = [n for n, _ in RUNNER.modules("computers,panels,weather")]
    assert names == ["computers", "panels"]
    assert RUNNER.modules("weather") == []


def test_the_computers_module_asks_once_and_writes_who_answered(tmp_path, monkeypatch):
    d = _load("computers_discover_t", ROOT / "plugins/tools/computers/discover.py")
    hello, asker = _free_udp(), _free_udp()
    monkeypatch.setattr(d, "PORT", hello)
    monkeypatch.setattr(d, "FROM_PORT", asker)
    monkeypatch.setattr(d, "BCAST", "127.0.0.1")
    monkeypatch.setattr(d, "LISTEN_S", 0.5)
    got = []

    def responder():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", hello))
            s.settimeout(5)
            data, addr = s.recvfrom(256)
            got.append(data)
            s.sendto(json.dumps({"yashome": 1, "name": "den", "servers": ["touchmouse"],
                                 "ports": {"touchmouse": 7709}}).encode(), addr)
    t = threading.Thread(target=responder)
    t.start()
    stop = threading.Event()
    ctx = RUNNER.Context("computers", tmp_path, stop)
    run = threading.Thread(target=d.run, args=(ctx,))
    run.start()
    out = tmp_path / "discovered.json"
    end = time.time() + 5
    while time.time() < end and not out.exists():
        time.sleep(0.05)
    stop.set()
    run.join(3)
    t.join(3)
    assert not run.is_alive()
    assert got == [b"? yashome 1"]
    hosts = json.loads(out.read_text())["hosts"]
    assert [(h["host"], h["name"], h["servers"], h["ports"]) for h in hosts] == [
        ("127.0.0.1", "den", ["touchmouse"], {"touchmouse": 7709})]


def test_the_panels_module_takes_only_answers_with_the_sessions_word(tmp_path):
    d = _load("panels_discover_t", ROOT / "plugins/devices/panels/discover.py")
    ok = json.dumps({"proto": "pwpanel/1", "role": "panel", "id": "tab1", "name": "Hall", "model": "X", "magic": "w"})
    assert d.answer(ok.encode(), "w") == {"id": "tab1", "name": "Hall", "model": "X"}
    assert d.answer(ok.encode(), "other") is None
    assert d.answer(ok.encode(), "") is None                                       # pairing closed
    assert d.answer(ok.replace('"panel"', '"server"').encode(), "w") is None       # our own invitation
    assert d.answer(b"junk", "w") is None
    (tmp_path / "pairing.json").write_text(json.dumps({"until": 5.0, "magic": "w"}))
    assert d.pairing(tmp_path) == (5.0, "w")
    assert d.pairing(tmp_path / "none") == (0.0, "")


def test_the_panels_module_writes_the_neighbour_table(tmp_path):
    d = _load("panels_discover_t2", ROOT / "plugins/devices/panels/discover.py")
    src = tmp_path / "arp"
    src.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "192.0.2.5        0x1         0x2         00:11:22:33:44:55     *        eth0\n"
                   "192.0.2.6        0x1         0x0         aa:bb:cc:dd:ee:ff     *        eth0\n")              # incomplete: left out
    d.arp(tmp_path, str(src))
    assert json.loads((tmp_path / "arp.json").read_text())["arp"] == {"192.0.2.5": "001122334455"}
    d.flush({"t": {"id": "t", "name": "n", "model": "m", "ip": "192.0.2.5", "seen": time.time()}}, tmp_path)
    assert [x["id"] for x in json.loads((tmp_path / "discovered.json").read_text())["devices"]] == ["t"]
