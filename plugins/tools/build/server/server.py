#!/usr/bin/env python3
"""Yashome build server: compiles ESPHome configurations the core sends and flashes them over the air.

HTTP on $BUILD_PORT (default 8795), no key: its port is on its computer's loopback only (the house reaches it through ssh,
plug-in computers) and on the compose network of its app builder:
  GET  /v1/info                     ESPHome version, the jobs running
  POST /v1/jobs                     {"name", "files": {path: text | {"b64": data}}, "main"?,
                                     "ota"?: {"address", "attempts"?, "hold"?}}
  POST /v1/jobs/<id>/go             a job with ota.hold waits after compiling (state "ready") for this
  POST /v1/jobs/<id>/abort          ...or ends it without an upload (the node could not be prepared)
  GET  /v1/jobs/<id>                state (queued, compiling, uploading, done, error), progress, log tail
  GET  /v1/jobs/<id>/<artifact>     firmware.factory.bin (USB from the browser) or firmware.ota.bin
One job per name at a time; a name keeps its build cache in $BUILD_WORK/<name>; secrets.yaml is
removed when the job ends.

Android apps: {"kind": "apk", "name", "files": {"project.tar.gz", "release.keystore"?,
"google-services.json"?}, "app_id"?, "alias"?, "password", "make_key"?} goes to the app builder
($APK_URL, the same image with BUILD_MODE=apk and the Android SDK); its job ids start with "a" and are
passed on too (firmware job ids start with "f"). The builder signs with the house's key, or makes one:
the artifact release.keystore, kept until the house has stored it and sends
  DELETE /v1/jobs/<id>/<artifact>
It returns app.apk; the key and google-services.json leave the project when the job ends.
"""
import base64
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import threading
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORK = Path(os.environ.get("BUILD_WORK", "/work"))
ES = os.environ.get("ESPHOME_BIN", "esphome")
MODE = os.environ.get("BUILD_MODE", "esphome")              # "apk": the app builder
APK_URL = os.environ.get("APK_URL", "").rstrip("/")
APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
APK_FILES = ("project.tar.gz", "release.keystore", "google-services.json")
APK_S = 30 * 60
UNTAR_MAX_BYTES, UNTAR_MAX_MEMBERS = 512 * 1024 * 1024, 50_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}(/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}){0,3}$")
ARTIFACTS = ("firmware.factory.bin", "firmware.ota.bin")
MAX_BODY = 16 * 1024 * 1024
COMPILE_S, UPLOAD_S = 45 * 60, 6 * 60
_NINJA = re.compile(r"\[(\d+)/(\d+)\]")
_PCT = re.compile(r"(\d{1,3})\s*%")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_lock = threading.Lock()
_jobs: "OrderedDict[str, dict]" = OrderedDict()
_busy: set = set()
_one = threading.Semaphore(1)          # one build at a time: two IDF links overrun the memory limit


def esphome_version() -> str:
    if MODE == "apk":
        return "apk builder"
    try:
        return subprocess.run([ES, "version"], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"unavailable: {e}"


VERSION = esphome_version()


def _set(job, **kw):
    with _lock:
        job.update(kw)


def run_stream(job, cmd, cwd, phase, limit):
    """Run under a PTY (esphome prints its OTA progress only to a terminal); progress and log tail into job.
    A run over `limit` seconds is killed with its whole process group."""
    m, s = pty.openpty()
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=s, stderr=s, stdin=subprocess.DEVNULL, close_fds=True,
                             start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb"})
    except OSError:
        os.close(m)
        raise
    finally:
        os.close(s)
    try:
        return _pump(job, p, m, phase, time.time() + limit, limit)
    finally:
        os.close(m)


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _pump(job, p, m, phase, deadline, limit):
    pend = ""
    while True:
        if time.time() > deadline:
            _kill(p)
            job["log"].append(f"{phase}: stopped after {limit} s")
            break
        ready, _, _ = select.select([m], [], [], min(5, max(0.1, deadline - time.time())))
        if not ready:
            if p.poll() is not None:
                break
            continue
        try:
            chunk = os.read(m, 4096)
        except OSError:
            break
        if not chunk:
            break
        pend += _ANSI.sub("", chunk.decode("utf-8", "replace"))
        *lines, pend = re.split(r"[\r\n]", pend)
        for line in filter(None, (x.strip() for x in lines)):
            job["log"].append(line)
            hit = _NINJA.search(line) if phase == "compiling" else _PCT.search(line)
            if hit:
                pct = int(int(hit.group(1)) * 100 / int(hit.group(2))) if phase == "compiling" else int(hit.group(1))
                _set(job, progress=min(100, pct))
    try:
        return p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        _kill(p)
        return p.wait()


def artifact(job, name):
    f = WORK / ".jobs" / job["id"] / name
    return f if f.is_file() else None


def built(job, name):
    base = WORK / job["name"] / ".esphome" / "build"           # ESPHome 2026: <node>/build/; older: .pioenvs
    hits = sorted([*base.glob(f"*/build/{name}"), *base.glob(f"*/.pioenvs/*/{name}")],
                  key=lambda f: f.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def build(job, files, main, ota):
    d = WORK / job["name"]
    _one.acquire()
    held = True
    try:
        d.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
            if rel.endswith("secrets.yaml"):
                f.chmod(0o600)
        _set(job, state="compiling", progress=0)
        if run_stream(job, [ES, "compile", main], d, "compiling", COMPILE_S):
            return _set(job, state="error", error="compile")
        out = WORK / ".jobs" / job["id"]
        out.mkdir(parents=True, exist_ok=True)
        for a in ARTIFACTS:
            src = built(job, a)
            if src:
                shutil.copy2(src, out / a)
        _set(job, artifacts=[a for a in ARTIFACTS if (out / a).is_file()])
        _one.release()                       # an upload compiles nothing: the next build may start
        held = False
        if ota and ota.get("hold"):
            _set(job, state="ready")
            if not _go[job["id"]].wait(HOLD_S):
                return _set(job, state="error", error="not released for upload")
            if job.get("abort"):
                return _set(job, state="error", error="upload aborted")
        if ota:
            _set(job, state="uploading", progress=0)
            tries = int(ota.get("attempts") or 3)
            for attempt in range(1, tries + 1):
                if run_stream(job, [ES, "upload", main, "--device", str(ota["address"])], d, "uploading", UPLOAD_S) == 0:
                    break
                job["log"].append(f"upload attempt {attempt} failed")
                if attempt < tries:
                    time.sleep(8)
            else:
                return _set(job, state="error", error="upload")
        _set(job, state="done", progress=100)
    except Exception as e:  # noqa: BLE001 - a job never takes the server down
        _set(job, state="error", error=f"{type(e).__name__}: {e}")
    finally:
        if held:
            _one.release()
        for rel in files:
            if rel.endswith("secrets.yaml"):
                (d / rel).unlink(missing_ok=True)
        with _lock:
            _busy.discard(job["name"])
            _go.pop(job["id"], None)
            job["ended"] = time.time()


HOLD_S = 600
_go: dict = {}                                    # job id -> Event: a held job may upload


def _untar(data: bytes, dest: Path):
    """The project, safely: regular files and directories only, nothing outside dest."""
    import io
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        total, members = 0, []
        for m in t:
            members.append(m)
            total += m.size
            if len(members) > UNTAR_MAX_MEMBERS or total > UNTAR_MAX_BYTES:
                raise ValueError("project.tar.gz: more than 50000 files or 512 MB")
            if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in Path(m.name).parts:
                raise ValueError(f"project.tar.gz: {m.name} is not a plain file inside the project")
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, filter="data")
        else:
            t.extractall(dest)


def _clean_gradle_home():
    """What one project's build could leave in the shared gradle home to run in the next one: gone."""
    home = Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    shutil.rmtree(home / "init.d", ignore_errors=True)
    for name in ("init.gradle", "init.gradle.kts", "gradle.properties"):
        (home / name).unlink(missing_ok=True)


def build_apk(job, files, opts):
    """An Android app: the project unpacked fresh, the house's key (or a new one), gradle assembleRelease."""
    src = WORK / job["name"] / "src"
    out = WORK / ".jobs" / job["id"]
    _one.acquire()
    os.environ.update(ANDROID_KEYSTORE_PASSWORD=opts["password"], ANDROID_KEY_ALIAS=opts["alias"])
    try:
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        _set(job, state="compiling", progress=0)
        _untar(files["project.tar.gz"], src)
        key = src / "release.keystore"
        out.mkdir(parents=True, exist_ok=True)
        if "release.keystore" in files:
            key.write_bytes(files["release.keystore"])
        elif opts.get("make_key"):
            made = subprocess.run(["keytool", "-genkeypair", "-noprompt", "-keystore", str(key), "-alias", opts["alias"],
                                   "-storepass:env", "ANDROID_KEYSTORE_PASSWORD", "-keypass:env",
                                   "ANDROID_KEYSTORE_PASSWORD", "-keyalg", "RSA",
                                   "-keysize", "2048", "-validity", "10000", "-dname", "CN=Yashome"],
                                  capture_output=True, text=True, timeout=120)
            if made.returncode:
                job["log"].append(made.stderr[-300:])
                return _set(job, state="error", error="keytool")
            shutil.copy2(key, out / "release.keystore")
        if "google-services.json" in files:
            (src / "app" / "google-services.json").write_bytes(files["google-services.json"])
        cmd = ["gradle", "--no-daemon", "--console=plain", "assembleRelease"]
        if opts.get("app_id"):
            cmd.append(f"-PyashAppId={opts['app_id']}")
        _clean_gradle_home()
        rc = run_stream(job, cmd, src, "compiling", APK_S)
        apk = src / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
        if rc or not apk.is_file():
            return _set(job, state="error", error="gradle")
        shutil.copy2(apk, out / "app.apk")
        _set(job, artifacts=[a for a in ("app.apk", "release.keystore") if (out / a).is_file()], state="done", progress=100)
    except Exception as e:  # noqa: BLE001 - a job never takes the server down
        _set(job, state="error", error=f"{type(e).__name__}: {e}")
    finally:
        os.environ.pop("ANDROID_KEYSTORE_PASSWORD", None)
        os.environ.pop("ANDROID_KEY_ALIAS", None)
        _one.release()
        for f in (src / "release.keystore", src / "app" / "google-services.json"):
            f.unlink(missing_ok=True)
        if job.get("state") != "done":                  # a new key of a failed job never stays behind
            (out / "release.keystore").unlink(missing_ok=True)
        with _lock:
            _busy.discard(job["name"])
            _go.pop(job["id"], None)
            job["ended"] = time.time()


def forward(method: str, path: str, body=None, timeout=60):
    """An app job on the build server: passed to the app builder, its answer passed back."""
    if not APK_URL:
        return 503, {"error": "no app builder here: Settings -> Computers and servers -> its computer -> Android app builder"}, None
    req = urllib.request.Request(APK_URL + path, method=method, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None, (r.read(), r.headers.get("Content-Type", "application/json"))
    except urllib.error.HTTPError as e:
        return e.code, None, (e.read(), "application/json")
    except OSError as e:                                # no such host, refused: the builder is not running
        return 503, {"error": f"no app builder here ({type(e).__name__}): Settings -> Computers and servers -> its computer -> Android app builder"}, None


def _decode(files):
    """{path: text | {"b64": data}} -> {path: str | bytes}; None when a path or a value is bad."""
    if not isinstance(files, dict):
        return None
    out = {}
    for k, v in files.items():
        if not (isinstance(k, str) and PATH_RE.fullmatch(k) and ".." not in k):
            return None
        if isinstance(v, dict) and isinstance(v.get("b64"), str):
            try:
                v = base64.b64decode(v["b64"], validate=True)
            except ValueError:
                return None
        elif not isinstance(v, str):
            return None
        out[k] = v
    return out


def release(job_id: str, abort: bool = False):
    with _lock:
        job, ev = _jobs.get(job_id), _go.get(job_id)
    if job is None:
        return 404, {"error": "no such job"}
    if ev is None or job["state"] != "ready":
        return 409, {"error": f"the job is {job['state']}, not waiting"}
    job["abort"] = abort
    ev.set()
    return 200, {"ok": True}


def new_apk_job(body: dict):
    name, files = body.get("name"), _decode(body.get("files"))
    opts = {"app_id": body.get("app_id"), "alias": body.get("alias") or "home", "password": body.get("password"),
            "make_key": body.get("make_key") is True}
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return 400, {"error": "name: a-z 0-9 -, 1-40 chars"}
    if files is None or "project.tar.gz" not in files or set(files) - set(APK_FILES) \
            or not all(isinstance(v, bytes) for v in files.values()):
        return 400, {"error": "files: project.tar.gz, release.keystore?, google-services.json? (b64)"}
    if opts["app_id"] is not None and not (isinstance(opts["app_id"], str) and APP_ID_RE.fullmatch(opts["app_id"])):
        return 400, {"error": "app_id: a package name like org.example.app"}
    if not (isinstance(opts["password"], str) and 6 <= len(opts["password"]) <= 128) or not NAME_RE.fullmatch(opts["alias"]):
        return 400, {"error": "password: 6-128 chars; alias: a-z 0-9 -"}
    if "release.keystore" not in files and not opts["make_key"]:
        return 400, {"error": "release.keystore, or make_key: true"}
    with _lock:
        if name in _busy:
            return 409, {"error": f"{name} is building already"}
        _busy.add(name)
        job = {"id": "a" + uuid.uuid4().hex[:11], "name": name, "state": "queued", "progress": None, "error": None,
               "artifacts": [], "started": time.time(), "ended": None, "log": deque(maxlen=60)}
        _jobs[job["id"]] = job
        for old in [j for j in list(_jobs.values()) if j["ended"]][:max(0, len(_jobs) - 50)]:
            _jobs.pop(old["id"], None)
            shutil.rmtree(WORK / ".jobs" / old["id"], ignore_errors=True)
    threading.Thread(target=build_apk, args=(job, files, opts), daemon=True).start()
    return 202, {"id": job["id"]}


def new_job(body: dict):
    name, files = body.get("name"), body.get("files")
    main = body.get("main") or "main.yaml"
    ota = body.get("ota")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return 400, {"error": "name: a-z 0-9 -, 1-40 chars"}
    files = _decode(files)
    if files is None or main not in files:
        return 400, {"error": "files: {relative path: text | {\"b64\": data}}, the main file among them"}
    if ota is not None and not (isinstance(ota, dict) and isinstance(ota.get("address"), str) and ota["address"]
                                and ota.get("attempts", 3) in (1, 2, 3, 4, 5) and ota.get("hold", False) in (True, False)):
        return 400, {"error": "ota: {address, attempts 1..5, hold true|false}"}
    with _lock:
        if name in _busy:
            return 409, {"error": f"{name} is building already"}
        _busy.add(name)
        job = {"id": "f" + uuid.uuid4().hex[:11], "name": name, "state": "queued", "progress": None, "error": None,
               "artifacts": [], "started": time.time(), "ended": None, "log": deque(maxlen=60)}
        _jobs[job["id"]] = job
        _go[job["id"]] = threading.Event()
        for old in [j for j in list(_jobs.values()) if j["ended"]][:max(0, len(_jobs) - 50)]:
            _jobs.pop(old["id"], None)
            shutil.rmtree(WORK / ".jobs" / old["id"], ignore_errors=True)
    threading.Thread(target=build, args=(job, files, main, ota), daemon=True).start()
    return 202, {"id": job["id"]}


def view(job):
    return {k: (list(v) if k == "log" else v) for k, v in job.items()}


class H(BaseHTTPRequestHandler):
    server_version = "yashome-build"
    timeout = 30

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if MODE != "apk" and len(parts) >= 3 and parts[:2] == ["v1", "jobs"] and parts[2].startswith("a"):
            return self._pass(*forward("GET", self.path.split("?")[0]))
        if parts == ["v1", "info"]:
            with _lock:
                info = {"esphome": VERSION, "busy": sorted(_busy)}
            if MODE != "apk":
                code, _, got = forward("GET", "/v1/info", timeout=3)
                info["apk"] = code == 200
            return self._send(200, info)
        if len(parts) in (3, 4) and parts[:2] == ["v1", "jobs"]:
            with _lock:
                job = _jobs.get(parts[2])
                snap = view(job) if job else None
            if snap is None:
                return self._send(404, {"error": "no such job"})
            if len(parts) == 3:
                return self._send(200, snap)
            f = artifact(job, parts[3]) if parts[3] in snap["artifacts"] else None   # also after a failed OTA
            if f is None:
                return self._send(404, {"error": "no such artifact"})
            return self._send(200, raw=f.read_bytes(), ctype="application/octet-stream")
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        """DELETE /v1/jobs/<id>/<artifact>: the house has stored it (a new signing key) - it leaves here."""
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["v1", "jobs"]:
            return self._send(404, {"error": "not found"})
        if MODE != "apk" and parts[2].startswith("a"):
            return self._pass(*forward("DELETE", self.path.split("?")[0]))
        with _lock:
            job = _jobs.get(parts[2])
            if job is None or parts[3] not in job["artifacts"]:
                return self._send(404, {"error": "no such artifact"})
            job["artifacts"] = [a for a in job["artifacts"] if a != parts[3]]
        (WORK / ".jobs" / job["id"] / parts[3]).unlink(missing_ok=True)
        self._send(200, {"ok": True})

    def do_POST(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if MODE != "apk" and len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[2].startswith("a"):
            return self._send(404, {"error": "an app job has nothing to release"})
        if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] in ("go", "abort"):
            return self._send(*release(parts[2], abort=parts[3] == "abort"))
        if parts != ["v1", "jobs"]:
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0:
            return self._send(400, {"error": "a JSON body"})
        if n > MAX_BODY:
            return self._send(413, {"error": f"at most {MAX_BODY} bytes"})
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except ValueError:
            return self._send(400, {"error": "JSON"})
        body = body if isinstance(body, dict) else {}
        if body.get("kind") == "apk":
            if MODE == "apk":
                return self._send(*new_apk_job(body))
            return self._pass(*forward("POST", "/v1/jobs", raw))
        if MODE == "apk":
            return self._send(400, {"error": "this is the app builder: kind apk only"})
        self._send(*new_job(body))

    def _pass(self, code, obj, got):
        if got is None:
            return self._send(code, obj)
        return self._send(code, raw=got[0], ctype=got[1])

    def log_message(self, fmt, *args):
        print(f"[build] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    for left in WORK.glob("**/secrets.yaml"):            # a job killed with the container left its secrets
        left.unlink(missing_ok=True)
    shutil.rmtree(WORK / ".jobs", ignore_errors=True)
    print(f"[build] {VERSION if MODE == 'apk' else 'ESPHome ' + VERSION}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("BUILD_PORT", "8795"))), H).serve_forever()
