"""Build server: where firmware (ESPHome) and the apps are compiled - on a computer of the house, this one included.

Installed and picked in Settings -> Devices -> Computers and servers (plug-in computers); it listens on that computer's
loopback only and the house reaches it through the helper's ssh tunnel (helper:8795), so it has no key. Settings section
`build`: {"remote": <that computer's host>} while one is in use. Other plug-ins (`requires: build`) call
submit() / job() / artifact(); the dashboard proxies a job's firmware for the first flash over USB from the browser.
"""
import base64
import http.client
import io
import json
import os
import re
import secrets
import tarfile
import threading
import time
import urllib.error
import urllib.request

from fastapi import HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import app as core

PLUGIN = {"name": "Build server", "version": "3.0.0", "icon": "hammer"}

TUNNEL_URL = "http://helper:8795"           # the helper's ssh tunnel to the computer in use (helper README)
_cfg: dict = dict(core.cfg_section("build", {}))
core.SETTINGS_SECTIONS["build"] = lambda: _cfg
for _old in ("url", "paired_at"):            # a server registered by a one-time code and its key (before 3.0): gone
    _cfg.pop(_old, None)
(core.plugin_state("build") / "key").unlink(missing_ok=True)


def server() -> str:
    """The address of the build server in use (the helper's tunnel to its computer), "" = none."""
    return TUNNEL_URL if _cfg.get("remote") else ""


def use_remote(host: str = "") -> None:
    """The build server the house uses: that computer's (its tunnel is the helper's: plug-in computers), "" = none."""
    if host:
        _cfg["remote"] = host
    else:
        _cfg.pop("remote", None)
    core.save_settings()
    core.log.info(f"[build] build server: {host or 'this host'}")


def in_use() -> str:
    """The computer whose build server is in use ("" = none)."""
    return str(_cfg.get("remote") or "")


def _call(method: str, path: str, body=None, raw=False, timeout=20):
    url = server()
    if not url:
        raise HTTPException(503, "no build server: Settings -> Devices -> Computers and servers")
    req = urllib.request.Request(url + path, method=method, headers={"Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return data if raw else json.loads(data or b"null")
    except urllib.error.HTTPError as e:
        try:
            why = "wrong key" if e.code in (401, 403) else e.read().decode(errors="replace")[:300]
        except (OSError, http.client.HTTPException):
            why = f"HTTP {e.code}"
        raise HTTPException(e.code if e.code in (400, 404, 409) else 502, f"build server: {why}")
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise HTTPException(503, f"build server unreachable, or not a build server: {type(e).__name__}: {e}")


def submit(name: str, files: dict, main: str = "main.yaml", ota: dict | None = None) -> str:
    """Start a build; returns the job id. files = {relative path: text or bytes}, secrets.yaml among them if
    needed. ota = {"address", "attempts"?, "hold"?}: with hold the job waits after compiling (state "ready")
    until release(job id) - time to put the node into a state that takes an upload."""
    wire = {k: {"b64": base64.b64encode(v).decode()} if isinstance(v, bytes) else v for k, v in files.items()}
    r = _call("POST", "/v1/jobs", {"name": name, "files": wire, "main": main, "ota": ota}, timeout=60)
    if not isinstance(r, dict) or not isinstance(r.get("id"), str):
        raise HTTPException(503, "not a build server: its answer has no job id")
    return r["id"]


APK_SKIP_DIRS = {"build", "apk", ".cxx", ".externalNativeBuild", "captures"}   # at the project or a module root
APK_SKIP_FILES = {"google-services.json", "local.properties", "keystore.properties", "secrets.properties",
                  "key.properties", "signing.properties", "app.keystore.alias"}
APK_SKIP_SUFFIXES = (".keystore", ".jks", ".p12", ".pfx", ".pem", ".key", ".bak", ".apk", ".aab", ".pass")


def project_tar(root) -> bytes:
    """An Android project as the app builder takes it: sources only - no build output, no keys."""
    from pathlib import Path
    root = Path(root)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root)
            dirs = rel.parts if f.is_dir() else rel.parts[:-1]
            if f.is_symlink() or any(p.startswith(".") for p in dirs) \
                    or any(p in APK_SKIP_DIRS for p in dirs[:2]):       # app/src/.../build/ (a package) stays
                continue
            if f.is_file() and (rel.name in APK_SKIP_FILES or rel.name.startswith(".")
                                or rel.name.lower().endswith(APK_SKIP_SUFFIXES)):
                continue
            if f.is_file() or f.is_dir():
                t.add(f, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


def submit_apk(name: str, project, password: str, keystore: bytes | None = None,
               google_services: bytes | None = None, app_id: str | None = None, alias: str = "home") -> str:
    """Build an Android app on the app builder; returns the job id. Signed with keystore, or - None - with
    a key the builder makes: fetch artifact(id, "release.keystore"), store it, then drop_artifact() it.
    The key travels in the job, through the house's ssh tunnel to that computer."""
    files = {"project.tar.gz": project_tar(project)}
    if keystore is not None:
        files["release.keystore"] = keystore
    if google_services is not None:
        files["google-services.json"] = google_services
    wire = {k: {"b64": base64.b64encode(v).decode()} for k, v in files.items()}
    r = _call("POST", "/v1/jobs", {"kind": "apk", "name": name, "files": wire, "password": password, "alias": alias,
                                   "app_id": app_id, "make_key": keystore is None}, timeout=120)
    if not isinstance(r, dict) or not isinstance(r.get("id"), str):
        raise HTTPException(503, "not a build server: its answer has no job id")
    return r["id"]


def write_private(path, data: bytes):
    """A secret file (a signing key, its password): created 0600, never readable by others in between."""
    from pathlib import Path
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())                       # on disk before the builder drops its copy
    tmp.replace(path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class AppBuild:
    """A plug-in's Android app built on the app builder (Settings -> Build the app of that plug-in).

    The house's key: plugin-state/<plugin>/app.keystore with app.keystore.pass (and app.keystore.alias for an
    imported key that is not "home") - made by the builder at the first build, in the backup. The APK goes
    to plugin-state/<plugin>/<apk> (outside the house's git; backups leave *.apk out). A build stays busy
    until its key and APK are stored, so a second click never makes a second key."""
    POLL_S, POLL_MAX, POLL_ERRORS = 5, 720, 5

    def __init__(self, plugin: str, name: str, project, apk: str, app_id: str | None = None, firebase: bool = False):
        from pathlib import Path
        state = core.plugin_state(plugin)
        self.plugin, self.name, self.project, self.app_id = plugin, name, Path(project), app_id
        self.key, self.key_pass, self.key_alias = state / "app.keystore", state / "app.keystore.pass", state / "app.keystore.alias"
        self.apk, self.old_apk = state / apk, core.plugin_data(plugin) / apk
        self.google = core.plugin_data(plugin) / "google-services.json" if firebase else None
        self.state: dict = {}
        self.lock = threading.Lock()

    def file(self):
        """The built APK: plugin-state, else an older copy in plugin-data; None when there is none."""
        return next((f for f in (self.apk, self.old_apk) if f.is_file()), None)

    def status(self) -> dict:
        f = self.file()
        return {**self.state, "apk": f is not None, "apk_time": int(f.stat().st_mtime) if f else None,
                "key": self.key.is_file()}

    def start(self) -> dict:
        with self.lock:
            if self.state.get("busy"):
                raise HTTPException(409, "the app is being built already")
            if not self.key_pass.is_file():
                write_private(self.key_pass, secrets.token_urlsafe(24).encode())
            password = self.key_pass.read_text().strip()
            alias = self.key_alias.read_text().strip() if self.key_alias.is_file() else "home"
            key = self.key.read_bytes() if self.key.is_file() else None
            google = self.google.read_bytes() if self.google is not None and self.google.is_file() else None
            self.state.clear()
            self.state.update(state="starting", busy=True, cloud=google is not None)
            try:
                jid = submit_apk(self.name, self.project, password, keystore=key, google_services=google,
                                 app_id=self.app_id, alias=alias)
            except HTTPException as e:
                self.state.update(state="error", error=e.detail, busy=False)
                raise
            except Exception as e:                      # an unreadable file here: never stuck busy
                self.state.update(state="error", error=f"{type(e).__name__}: {e}", busy=False)
                raise HTTPException(500, f"the app build could not start: {type(e).__name__}: {e}")
            self.state["job"] = jid
        threading.Thread(target=self._collect, args=(jid, key is None), daemon=True, name=f"app-{self.plugin}").start()
        return {"ok": True, "job": jid, "new_key": key is None}

    def _mine(self, jid: str, **kw):
        """Update the state - only while it is still this job's."""
        with self.lock:
            if self.state.get("job") == jid:
                self.state.update(kw)

    def _collect(self, jid: str, made_key: bool):
        """Follow the job to its end (the dashboard may be closed): a new key first, then the APK."""
        try:
            st, errors = {}, 0
            for _ in range(self.POLL_MAX):
                try:
                    st, errors = job(jid), 0
                except Exception as e:                      # noqa: BLE001 - a restart, a blip: try again
                    errors += 1
                    if errors >= self.POLL_ERRORS:
                        raise
                    core.log.info(f"[build] app {self.name} {jid}: no answer ({getattr(e, 'detail', e)}), trying again")
                    time.sleep(self.POLL_S)
                    continue
                if st.get("state") in ("done", "error"):
                    break
                self._mine(jid, state=st.get("state"), progress=st.get("progress"), log=list(st.get("log") or [])[-8:])
                time.sleep(self.POLL_S)
            if st.get("state") != "done":
                return self._mine(jid, state="error", error=st.get("error") or "did not end", log=list(st.get("log") or [])[-8:])
            self._mine(jid, state="collecting", progress=100)
            if made_key:
                write_private(self.key, artifact(jid, "release.keystore"))
                try:
                    drop_artifact(jid, "release.keystore")         # stored here: it leaves the builder
                except Exception as e:                          # noqa: BLE001 - the APK still comes
                    core.log.warning(f"[build] app {self.name}: the new key stays on the builder: {getattr(e, 'detail', e)}")
            data = artifact(jid, "app.apk")
            tmp = self.apk.with_suffix(".apk.tmp")
            tmp.write_bytes(data)
            tmp.replace(self.apk)
            self._mine(jid, state="done", built=int(time.time()), size=len(data))
            core.log.warning(f"[build] app {self.name} built: {len(data)} bytes")
        except Exception as e:                              # noqa: BLE001 - shown in the dashboard, never raised
            self._mine(jid, state="error", error=f"{getattr(e, 'detail', e)}")
        finally:
            self._mine(jid, busy=False)


def job(job_id: str) -> dict:
    return _call("GET", f"/v1/jobs/{job_id}")


def release(job_id: str, abort: bool = False):
    """Let a job held before its upload go on, or end it without the upload."""
    return _call("POST", f"/v1/jobs/{job_id}/{'abort' if abort else 'go'}", {})


def drop_artifact(job_id: str, name: str):
    """The house has the artifact (a new signing key): the build server removes it."""
    return _call("DELETE", f"/v1/jobs/{job_id}/{name}")


def artifact(job_id: str, name: str) -> bytes:
    return _call("GET", f"/v1/jobs/{job_id}/{name}", raw=True, timeout=120)


def _house_only(request: Request):
    """Builds carry the house's Wi-Fi and broker secrets: only the house token points them anywhere."""
    if not core.is_house_token(request.headers):
        raise HTTPException(403, "only the dashboard does this")


@core.app.get("/api/build/server")
def build_server_get(request: Request):
    _house_only(request)
    return {"remote": in_use(), "set": bool(server())}


@core.app.post("/api/build/server/test")
async def build_server_test(request: Request):
    _house_only(request)
    return await run_in_threadpool(_call, "GET", "/v1/info", None, False, 10)


@core.app.get("/api/build/jobs/{job_id}")
async def build_job(job_id: str, request: Request):
    _house_only(request)
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(400, "job id")
    return await run_in_threadpool(job, job_id)


@core.app.get("/api/build/jobs/{job_id}/{name}")
async def build_artifact(job_id: str, name: str, request: Request):
    _house_only(request)
    if not re.fullmatch(r"[0-9a-f]{12}", job_id) or name not in ("firmware.factory.bin", "firmware.ota.bin"):
        raise HTTPException(400, "job id / artifact")
    data = await run_in_threadpool(artifact, job_id, name)
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- the build server itself, for the installer on a computer of the house (helper role build) ----
HERE = os.path.dirname(os.path.abspath(__file__))


def build_server_files():
    """The build server itself (Dockerfile, server.py) as a tar.gz: the same code as in this repository."""
    def plain(t):
        t.uid = t.gid = 0
        t.uname = t.gname = ""
        t.mode = 0o644
        return t
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("Dockerfile", "server.py"):
            tar.add(os.path.join(HERE, "server", name), arcname=name, filter=plain)
        with open(os.path.join(HERE, "apk", "Dockerfile"), encoding="utf-8") as f:      # the app builder, from the same folder
            df = f.read().replace("COPY server/server.py", "COPY server.py").encode()
        ti = plain(tarfile.TarInfo("apk.Dockerfile"))
        ti.size = len(df)
        tar.addfile(ti, io.BytesIO(df))
    return Response(buf.getvalue(), media_type="application/gzip")


core.app.add_api_route("/plugins/build/server.tar.gz", build_server_files, methods=["GET"])
core.open_route(build_server_files)


def _health():
    if not server():
        return [core.health_row("build", "warn", "Build server", "not set",
                                "Only needed for ESP firmware", link="computers")]
    try:
        info = _call("GET", "/v1/info", timeout=4)
    except HTTPException as e:
        return [core.health_row("build", "bad", "Build server", "{0}", "Settings -> Computers and servers", (e.detail,), link="computers")]
    return [core.health_row("build", "ok", "Build server", "ESPHome {0}", "", ((info or {}).get("esphome", "?"),))]


core.HEALTH_CHECKS.append(_health)
