"""The voice container (the plug-in's README, "One voice container"): its two parts as child processes, the TTS
(tts/server.py, port 8092) and the recognizer service (vad/voice_vad.py, ports 8091 and 8093). A part that stops is
started again after a pause, the other one runs on; SIGTERM stops both."""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = {"tts": ("tts", "server.py"), "vad": ("vad", "voice_vad.py")}
PAUSE_S = 3.0           # before a part that stopped is started again: a part that fails at once does not spin
GRACE_S = 2.5           # SIGTERM to the parts, then SIGKILL (compose gives the container 5 s)


def _start(name: str, parts: dict) -> subprocess.Popen:
    folder, script = parts[name]
    return subprocess.Popen([sys.executable, "-u", script], cwd=os.path.join(HERE, folder))


def run(parts: dict = PARTS, pause: float = PAUSE_S, should_stop=None) -> dict:
    """Keep every part running until SIGTERM / SIGINT (or should_stop() is true); -> {part: times it was started again}."""
    stop = {"now": False}
    restarts = {n: 0 for n in parts}
    procs = {n: _start(n, parts) for n in parts}

    def on_signal(sig, frame):
        stop["now"] = True
    before = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    while not stop["now"] and not (should_stop and should_stop()):
        for n, p in list(procs.items()):
            rc = p.poll()
            if rc is not None:
                print(f"[voice] {n} stopped (exit {rc}): started again in {pause:.0f} s", flush=True)
                time.sleep(pause)
                procs[n] = _start(n, parts)
                restarts[n] += 1
        time.sleep(0.2)
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    end = time.time() + GRACE_S
    for p in procs.values():
        try:
            p.wait(max(0.1, end - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    for s, h in before.items():
        signal.signal(s, h)
    return restarts


if __name__ == "__main__":
    run()
