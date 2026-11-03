#!/usr/bin/env python3
"""The core's `discover` container: one process in the host's network (a broadcast from the backend's bridge
network reaches nobody) that runs the discovery module of every enabled plug-in. A plug-in ships `discover.py`
next to its `__init__.py` with `run(ctx)` (docs/plugins.md, "Discovery module"). The enabled set is the
backend's: PLUGINS and `requires`, resolved by tools/pluginlib.py. Each module runs in a thread of its own;
one that raises or returns is logged and started again after a pause, the others go on. None enabled: it idles."""
import importlib.util
import os
import re
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pluginlib  # noqa: E402

STATE = Path(os.environ.get("STATE_ROOT") or "/state")      # the house's plugin-state/
BASES = [ROOT / "plugins", ROOT / "plugins-extra"]
PAUSE_MIN, PAUSE_MAX = 5.0, 300.0     # between two starts of a failing module, doubling
CALM_S = 60.0                         # a run that lasted this long starts the next pause from PAUSE_MIN


class Context:
    """What a module's run(ctx) gets: its name, its own state folder, the stop event, log() and wait()."""

    def __init__(self, name: str, state: Path, stop: threading.Event):
        self.name, self.state, self.stop = name, state, stop

    def log(self, msg: str):
        print(f"discover[{self.name}]: {msg}", flush=True)

    def wait(self, seconds: float) -> bool:
        """Sleeps; True when the container is stopping (the module returns then)."""
        return self.stop.wait(seconds)


def modules(plugins: str, bases=None) -> list:
    """[(name, its discover.py)] of the enabled plug-ins that have one, in the backend's load order."""
    raw = re.sub(r"\s#.*$", "", plugins or "")
    listed = {x.strip().strip("\"'") for x in raw.split(",")} - {""}
    found, _ = pluginlib.discover([Path(b) for b in (bases or BASES)])
    by_name = {q.name: q for q in found}
    on = pluginlib.plan(found, None if "*" in listed else listed)["on"]
    return [(n, by_name[n] / "discover.py") for n in on if (by_name[n] / "discover.py").is_file()]


def load(name: str, path: Path):
    """The module, read again at every start: a fixed file is taken without a new container."""
    spec = importlib.util.spec_from_file_location(f"yashome_discover_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "run", None)):
        raise TypeError(f"{path.name} has no run(ctx)")
    return mod


def supervise(name: str, path: Path, stop: threading.Event, state_root: Path = None):
    """Runs one module until the stop event; a failure or a return is logged and it starts again."""
    ctx = Context(name, (state_root or STATE) / name, stop)
    pause = PAUSE_MIN
    while not stop.is_set():
        began = time.monotonic()
        try:
            ctx.state.mkdir(parents=True, exist_ok=True)
            load(name, path).run(ctx)
            if stop.is_set():
                return
            ctx.log("run() returned: started again")
        except (Exception, SystemExit):
            ctx.log("failed, started again:\n" + traceback.format_exc().rstrip())
        if time.monotonic() - began >= CALM_S:
            pause = PAUSE_MIN
        if stop.wait(pause):
            return
        pause = min(pause * 2, PAUSE_MAX)


def start(mods: list, stop: threading.Event, state_root: Path = None) -> list:
    threads = [threading.Thread(target=supervise, args=(n, p, stop, state_root), name=f"discover-{n}", daemon=True)
               for n, p in mods]
    for t in threads:
        t.start()
    return threads


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    mods = modules(os.environ.get("PLUGINS", ""))
    print(f"discover: {', '.join(n for n, _ in mods) or 'no plug-in with a discovery module is on: idle'}", flush=True)
    threads = start(mods, stop)
    while not stop.wait(60):
        pass
    for t in threads:
        t.join(2)


if __name__ == "__main__":
    main()
