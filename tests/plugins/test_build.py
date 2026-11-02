"""build: the build server the house uses (this host's, or a computer's through the helper's ssh tunnel: no key; only the
house token), and the build server itself with a stand-in esphome: a job compiles, flashes, serves its firmware and drops
its secrets."""
import base64
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.build"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
SERVER = pathlib.Path(me.__file__).parent / "server/server.py"
FAKE_ESPHOME = """#!/bin/sh
case "$1" in
  version) echo "Version: 2026.7.4";;
  compile) [ -f secrets.yaml ] && cp secrets.yaml seen-secrets
           grep -q fail "$2" && exit 1
           grep -q slow "$2" && sleep 1.2
           grep -q hang "$2" && sleep 30
           echo "[1/2] Compiling"; echo "[2/2] Linking"
           d=.esphome/build/node/build; mkdir -p $d; printf FACTORY > $d/firmware.factory.bin
           printf OTA > $d/firmware.ota.bin;;
  upload) grep -q offline "$2" && exit 1
          echo "Uploading: [=====] 100%"; echo "$4" > uploaded-to;;
esac
"""


@pytest.fixture()
def build_server(tmp_path, monkeypatch):
    esp = tmp_path / "esphome"
    esp.write_text(FAKE_ESPHOME)
    esp.chmod(0o755)
    env = {**os.environ, "BUILD_WORK": str(tmp_path / "work"), "ESPHOME_BIN": str(esp)}
    sys.path.insert(0, str(SERVER.parent))
    for k in ("BUILD_WORK", "ESPHOME_BIN"):
        monkeypatch.setenv(k, env[k])
    import importlib
    import server as srv
    importlib.reload(srv)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.H)
    threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True).start()   # a quick shutdown
    yield f"http://127.0.0.1:{httpd.server_port}", srv, tmp_path / "work"
    httpd.shutdown()
    httpd.server_close()                            # else a late request to this port is accepted and hangs
    sys.path.remove(str(SERVER.parent))
    sys.modules.pop("server", None)


def call(url, path, body=None):
    req = urllib.request.Request(url + path, method="POST" if body is not None else "GET",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def local(monkeypatch, url):
    """The plug-in uses a test server as the computer in use (the helper's tunnel)."""
    monkeypatch.setattr(me, "TUNNEL_URL", url)
    monkeypatch.setattr(me, "_cfg", {"remote": "192.0.2.61"})


def wait(url, job_id):
    for _ in range(300):
        st = json.loads(call(url, f"/v1/jobs/{job_id}")[1])
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not end")


def test_the_server_builds_flashes_serves_and_forgets_the_secrets(build_server):
    url, srv, work = build_server
    assert json.loads(call(url, "/v1/info")[1])["esphome"].endswith("2026.7.4")
    code, body = call(url, "/v1/jobs", {"name": "node", "files": {"main.yaml": "esphome: {}",
                                                                   "secrets.yaml": "wifi: x"},
                                        "ota": {"address": "192.0.2.30", "attempts": 1}})
    assert code == 202
    st = wait(url, json.loads(body)["id"])
    assert st["state"] == "done" and st["artifacts"] == ["firmware.factory.bin", "firmware.ota.bin"]
    assert (work / "node/seen-secrets").read_text() == "wifi: x" and not (work / "node/secrets.yaml").exists()
    assert (work / "node/uploaded-to").read_text().strip() == "192.0.2.30"
    assert call(url, f"/v1/jobs/{st['id']}/firmware.factory.bin")[1] == b"FACTORY"
    assert call(url, f"/v1/jobs/{st['id']}/main.yaml")[0] == 404


def test_the_server_refuses_bad_jobs_and_reports_a_failed_compile(build_server):
    url, srv, work = build_server
    for bad in ({"name": "../x", "files": {"main.yaml": ""}}, {"name": "ok", "files": {"../main.yaml": ""}},
                {"name": "ok", "files": {"other.yaml": ""}}, {"name": "ok", "files": {"main.yaml": ""}, "ota": {}}):
        assert call(url, "/v1/jobs", bad)[0] == 400, bad
    code, body = call(url, "/v1/jobs", {"name": "broken", "files": {"main.yaml": "fail"}})
    st = wait(url, json.loads(body)["id"])
    assert st["state"] == "error" and st["error"] == "compile"



def test_a_job_through_the_plugin_and_its_firmware_through_the_dashboard(build_server, monkeypatch):
    url, srv, work = build_server
    local(monkeypatch, url)
    job_id = me.submit("node", {"main.yaml": "esphome: {}"})
    st = wait(url, job_id)
    assert st["state"] == "done"
    r = TestClient(core.app).get(f"/api/build/jobs/{job_id}/firmware.factory.bin", headers=HOUSE)
    assert r.status_code == 200 and r.content == b"FACTORY"
    assert TestClient(core.app).get("/api/build/jobs/zz/firmware.factory.bin", headers=HOUSE).status_code == 400



def test_attempts_are_bounded_and_a_job_keeps_its_own_firmware(build_server):
    url, srv, work = build_server
    assert call(url, "/v1/jobs", {"name": "n", "files": {"main.yaml": ""}, "ota": {"address": "a", "attempts": 99}})[0] == 400
    a = wait(url, json.loads(call(url, "/v1/jobs", {"name": "node", "files": {"main.yaml": "first"}})[1])["id"])
    (work / "node/.esphome/build/node/build/firmware.factory.bin").write_text("LATER")
    assert call(url, f"/v1/jobs/{a['id']}/firmware.factory.bin")[1] == b"FACTORY"


def test_a_failed_ota_still_gives_the_firmware_for_usb(build_server):
    url, srv, work = build_server
    code, body = call(url, "/v1/jobs", {"name": "node", "files": {"main.yaml": "offline"},
                                        "ota": {"address": "192.0.2.31", "attempts": 1}})
    st = wait(url, json.loads(body)["id"])
    assert st["state"] == "error" and st["error"] == "upload"
    assert call(url, f"/v1/jobs/{st['id']}/firmware.factory.bin")[1] == b"FACTORY"


def test_one_build_at_a_time_and_a_hung_one_is_stopped(build_server, monkeypatch):
    url, srv, work = build_server
    a = json.loads(call(url, "/v1/jobs", {"name": "one", "files": {"main.yaml": "slow"}})[1])["id"]
    for _ in range(100):
        if json.loads(call(url, f"/v1/jobs/{a}")[1])["state"] == "compiling":
            break
        time.sleep(0.02)
    b = json.loads(call(url, "/v1/jobs", {"name": "two", "files": {"main.yaml": "ok"}})[1])["id"]
    time.sleep(0.2)                                 # b had its chance to start: it must not have
    assert json.loads(call(url, f"/v1/jobs/{a}")[1])["state"] == "compiling"
    assert json.loads(call(url, f"/v1/jobs/{b}")[1])["state"] == "queued"
    assert wait(url, a)["state"] == "done" and wait(url, b)["state"] == "done"
    monkeypatch.setattr(srv, "COMPILE_S", 0.3)
    st = wait(url, json.loads(call(url, "/v1/jobs", {"name": "three", "files": {"main.yaml": "hang"}})[1])["id"])
    assert st["state"] == "error" and any("stopped after 0.3 s" in line for line in st["log"])





def test_not_a_build_server_is_a_503_not_a_500(monkeypatch):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]

    def banner():
        conn, _ = s.accept()
        conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
        conn.close()
    threading.Thread(target=banner, daemon=True).start()
    local(monkeypatch, f"http://127.0.0.1:{port}")
    r = TestClient(core.app).post("/api/build/server/test", headers=HOUSE)
    s.close()
    assert r.status_code == 503 and "not a build server" in r.json()["detail"]


def test_the_server_guards_its_jobs(build_server):
    url, srv, work = build_server
    req = urllib.request.Request(url + "/v1/jobs", method="POST", data=b"x" * (srv.MAX_BODY + 1),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    except OSError:
        code = 413
    assert code == 413
    a = call(url, "/v1/jobs", {"name": "same", "files": {"main.yaml": "slow"}})
    assert a[0] == 202 and call(url, "/v1/jobs", {"name": "same", "files": {"main.yaml": ""}})[0] == 409
    assert call(url, "/v1/jobs", {"name": "node\n", "files": {"main.yaml": ""}})[0] == 400
    wait(url, json.loads(a[1])["id"])


def _raw_server(answer: bytes):
    """A socket that answers every request with `answer`. close() stops it for good: a thread left in
    accept() on a closed socket wakes up on the next socket given the same fd and answers for it."""
    import socket

    class Raw:
        def __init__(self):
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0))
            self.sock.listen(4)
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()

        def serve(self):
            while True:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return
                with conn:
                    conn.settimeout(5)
                    try:
                        conn.recv(65536)
                        conn.sendall(answer)
                    except OSError:
                        pass

        def close(self):
            try:
                self.sock.shutdown(socket.SHUT_RDWR)   # wakes the accept()
            except OSError:
                pass
            self.sock.close()
            self.thread.join(5)
    r = Raw()
    return r, f"http://127.0.0.1:{r.sock.getsockname()[1]}"


def test_a_cut_error_answer_or_a_stranger_json_is_a_503_502_not_a_500(monkeypatch):
    sock, url = _raw_server(b"HTTP/1.1 500 X\r\nContent-Length: 100\r\n\r\nabc")
    local(monkeypatch, url)
    r = TestClient(core.app).post("/api/build/server/test", headers=HOUSE)
    sock.close()
    assert r.status_code == 502 and "HTTP 500" in r.json()["detail"]
    sock, url = _raw_server(b"HTTP/1.1 202 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]")
    monkeypatch.setattr(me, "TUNNEL_URL", url)
    try:
        with pytest.raises(core.HTTPException) as e:
            me.submit("node", {"main.yaml": ""})
        assert e.value.status_code == 503
    finally:
        sock.close()




def test_the_server_refuses_a_name_with_a_newline(build_server):
    url, srv, work = build_server
    assert call(url, "/v1/jobs", {"name": "n", "files": {"main.yaml": "", "a.yaml\n": ""}})[0] == 400


def test_no_sleep_after_the_last_ota_try(build_server, monkeypatch):
    url, srv, work = build_server
    naps, real = [], time.sleep
    # srv.time is the time module: the test's own polling must still sleep, only the OTA pauses are skipped
    monkeypatch.setattr(srv.time, "sleep", lambda s: naps.append(s) if s >= 1 else real(s))
    st = wait(url, json.loads(call(url, "/v1/jobs", {"name": "naps", "files": {"main.yaml": "offline"},
                                                      "ota": {"address": "192.0.2.31", "attempts": 3}})[1])["id"])
    assert st["state"] == "error" and naps.count(8) == 2



def test_binary_files_and_an_upload_held_until_released(build_server):
    url, srv, work = build_server
    png = bytes(range(256))
    body = {"name": "held", "files": {"main.yaml": "x", "icons/a.png": {"b64": base64.b64encode(png).decode()}},
            "ota": {"address": "192.0.2.40", "hold": True}}
    jid = json.loads(call(url, "/v1/jobs", body)[1])["id"]
    for _ in range(300):
        st = json.loads(call(url, f"/v1/jobs/{jid}")[1])
        if st["state"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert st["state"] == "ready" and (work / "held/icons/a.png").read_bytes() == png
    assert call(url, "/v1/jobs/nope/go", {})[0] == 404
    assert call(url, f"/v1/jobs/{jid}/go", {})[0] == 200
    assert wait(url, jid)["state"] == "done"
    assert call(url, f"/v1/jobs/{jid}/go", {})[0] == 409
    bad = {"name": "bad", "files": {"main.yaml": "x", "a.bin": {"b64": "%%%"}}}
    assert call(url, "/v1/jobs", bad)[0] == 400


def test_a_held_upload_can_be_aborted(build_server):
    url, srv, work = build_server
    jid = json.loads(call(url, "/v1/jobs", {"name": "aborted", "files": {"main.yaml": "x"},
                                            "ota": {"address": "192.0.2.42", "hold": True}})[1])["id"]
    for _ in range(300):
        if json.loads(call(url, f"/v1/jobs/{jid}")[1])["state"] == "ready":
            break
        time.sleep(0.05)
    assert call(url, f"/v1/jobs/{jid}/abort", {})[0] == 200
    st = wait(url, jid)
    assert st["state"] == "error" and "aborted" in st["error"]
    assert call(url, "/v1/jobs", {"name": "aborted", "files": {"main.yaml": "x"}})[0] == 202


def test_a_held_upload_never_released_ends_in_an_error(build_server, monkeypatch):
    url, srv, work = build_server
    monkeypatch.setattr(srv, "HOLD_S", 0.2)
    jid = json.loads(call(url, "/v1/jobs", {"name": "never", "files": {"main.yaml": "x"},
                                            "ota": {"address": "192.0.2.41", "hold": True}})[1])["id"]
    st = wait(url, jid)
    assert st["state"] == "error" and "released" in st["error"]


def test_the_server_file_runs_standalone(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {**os.environ, "BUILD_PORT": str(port), "BUILD_WORK": str(tmp_path),
           "ESPHOME_BIN": "/bin/true"}
    (tmp_path / "x").mkdir()
    (tmp_path / "x/secrets.yaml").write_text("left behind")
    p = subprocess.Popen([sys.executable, str(SERVER)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                code, body = call(f"http://127.0.0.1:{port}", "/v1/info")
                break
            except OSError:
                time.sleep(0.1)
        assert code == 200 and not (tmp_path / "x/secrets.yaml").exists()
    finally:
        p.kill()
        p.wait()


FAKE_GRADLE = """#!/bin/sh
[ -f fail ] && exit 1
[ -f release.keystore ] || exit 2
d=app/build/outputs/apk/release; mkdir -p $d
printf "APK %s %s %s" "$ANDROID_KEY_ALIAS" "$ANDROID_KEYSTORE_PASSWORD" "$4" > $d/app-release.apk
[ -f app/google-services.json ] && cp app/google-services.json seen-google
echo "BUILD SUCCESSFUL"
"""
FAKE_KEYTOOL = """#!/bin/sh
for a in "$@"; do [ "$a" = -storepass ] && exit 9; done
while [ $# -gt 0 ]; do [ "$1" = -keystore ] && printf "NEWKEY-%s" "$ANDROID_KEYSTORE_PASSWORD" > "$2"; shift; done
"""


def _load_server(name, env, monkeypatch):
    import importlib.util
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(name, SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True).start()   # a quick shutdown
    return httpd, mod


@pytest.fixture()
def app_builder(tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in (("gradle", FAKE_GRADLE), ("keytool", FAKE_KEYTOOL)):
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setenv("GRADLE_USER_HOME", str(tmp_path / "gradle-home"))
    apk, apk_mod = _load_server("server_apk", {"BUILD_MODE": "apk",
                                               "BUILD_WORK": str(tmp_path / "apkwork")}, monkeypatch)
    esp, _ = _load_server("server_esp", {"BUILD_MODE": "esphome", "BUILD_WORK": str(tmp_path / "work"),
                                          "APK_URL": f"http://127.0.0.1:{apk.server_port}",
                                          "ESPHOME_BIN": "/bin/true"}, monkeypatch)
    yield f"http://127.0.0.1:{esp.server_port}", apk_mod, tmp_path / "apkwork"
    apk.shutdown()
    apk.server_close()
    esp.shutdown()
    esp.server_close()


def _project(tmp_path, fail=False):
    p = tmp_path / "proj"
    (p / "app" / "build").mkdir(parents=True)
    (p / "app" / "build" / "old.apk").write_text("stale")
    (p / ".gradle").mkdir()
    (p / "settings.gradle.kts").write_text('rootProject.name = "x"')
    (p / "release.keystore").write_text("NOT TO SEND")
    (p / "app" / "google-services.json").write_text("{}")
    for name in (".env", "upload.jks", "release.p12", "keystore.properties", "release.keystore.bak"):
        (p / name).write_text("NOT TO SEND")
    (p / ".git").mkdir()
    (p / ".git" / "config").write_text("NOT TO SEND")
    (p / "gradle.properties").write_text("android.useAndroidX=true")
    (p / "link").symlink_to("/etc/passwd")
    if fail:
        (p / "fail").write_text("")
    return p


def test_the_project_tar_has_sources_only(tmp_path):
    import io
    import tarfile
    names = tarfile.open(fileobj=io.BytesIO(me.project_tar(_project(tmp_path)))).getnames()
    assert "settings.gradle.kts" in names and "app" in names and "gradle.properties" in names
    assert not [n for n in names if "build" in n.split("/") or n.endswith((".keystore", "google-services.json"))
                or n.startswith(".") or n.endswith((".jks", ".p12", ".bak", "keystore.properties")) or n == "link"]


def test_an_app_is_built_through_the_build_server_and_a_new_key_leaves_once(app_builder, tmp_path):
    url, apk_mod, work = app_builder
    files = {"project.tar.gz": {"b64": base64.b64encode(me.project_tar(_project(tmp_path))).decode()},
             "google-services.json": {"b64": base64.b64encode(b'{"g": 1}').decode()}}
    body = {"kind": "apk", "name": "yash", "files": files, "password": "secret-pass", "app_id": "org.example.app",
            "make_key": True}
    code, out = call(url, "/v1/jobs", body)
    assert code == 202
    jid = json.loads(out)["id"]
    assert jid.startswith("a")
    st = wait(url, jid)
    assert st["state"] == "done" and sorted(st["artifacts"]) == ["app.apk", "release.keystore"]
    assert call(url, f"/v1/jobs/{jid}/app.apk")[1] == b"APK home secret-pass -PyashAppId=org.example.app"
    assert call(url, f"/v1/jobs/{jid}/release.keystore") == (200, b"NEWKEY-secret-pass")
    assert call(url, f"/v1/jobs/{jid}/release.keystore")[0] == 200          # a lost answer loses nothing
    gone = urllib.request.Request(url + f"/v1/jobs/{jid}/release.keystore", method="DELETE")
    assert urllib.request.urlopen(gone, timeout=10).status == 200            # the house stored it
    assert call(url, f"/v1/jobs/{jid}/release.keystore")[0] == 404
    src = work / "yash" / "src"
    assert not (src / "release.keystore").exists() and not (src / "app" / "google-services.json").exists()
    assert (src / "seen-google").read_text() == '{"g": 1}'
    assert "ANDROID_KEYSTORE_PASSWORD" not in os.environ
    assert json.loads(call(url, "/v1/info")[1])["apk"] is True


def test_bad_app_jobs_are_refused(app_builder, tmp_path):
    import io
    import tarfile
    url = app_builder[0]
    good = {"kind": "apk", "name": "yash", "password": "secret-pass", "make_key": True,
            "files": {"project.tar.gz": {"b64": base64.b64encode(me.project_tar(_project(tmp_path))).decode()}}}
    for bad in ({"app_id": "Not.Valid"}, {"password": "123"}, {"make_key": False}, {"name": "Bad Name"},
                {"files": {}}, {"files": {**good["files"], "other.txt": {"b64": ""}}}, {"alias": "../x"}):
        assert call(url, "/v1/jobs", {**good, **bad})[0] == 400, bad
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    evil = {**good, "files": {"project.tar.gz": {"b64": base64.b64encode(buf.getvalue()).decode()}}}
    st = wait(url, json.loads(call(url, "/v1/jobs", evil)[1])["id"])
    assert st["state"] == "error" and "not a plain file" in st["error"]
    failing = {**good, "files": {"project.tar.gz": {"b64": base64.b64encode(
        me.project_tar(_project(tmp_path / "f", fail=True))).decode()}}}
    fid = json.loads(call(url, "/v1/jobs", failing)[1])["id"]
    st = wait(url, fid)
    assert st["state"] == "error" and st["error"] == "gradle"
    assert not list((app_builder[2] / ".jobs").glob("*/release.keystore"))      # a failed job keeps no new key
    apk_mod = app_builder[1]
    apk_mod.UNTAR_MAX_MEMBERS = 1
    try:
        st = wait(url, json.loads(call(url, "/v1/jobs", good)[1])["id"])
        assert st["state"] == "error" and "50000" in st["error"]
    finally:
        apk_mod.UNTAR_MAX_MEMBERS = 50_000


def test_without_an_app_builder_app_jobs_get_503(tmp_path, monkeypatch):
    esp, _ = _load_server("server_noapk", {"BUILD_MODE": "esphome", "APK_URL": "",
                                           "BUILD_WORK": str(tmp_path / "w"), "ESPHOME_BIN": "/bin/true"}, monkeypatch)
    try:
        url = f"http://127.0.0.1:{esp.server_port}"
        assert call(url, "/v1/jobs", {"kind": "apk", "name": "x"})[0] == 503
        assert json.loads(call(url, "/v1/info")[1])["apk"] is False
    finally:
        esp.shutdown()
        esp.server_close()


def test_submit_apk_from_the_plugin(app_builder, tmp_path, monkeypatch):
    url = app_builder[0]
    init = tmp_path / "gradle-home" / "init.d"
    init.mkdir(parents=True)
    (init / "steal.gradle").write_text("// a previous project's leftover")
    local(monkeypatch, url)
    jid = me.submit_apk("panel", _project(tmp_path), "secret-pass", keystore=b"HOUSEKEY", alias="home")
    st = wait(url, jid)
    assert st["state"] == "done" and st["artifacts"] == ["app.apk"]
    assert me.artifact(jid, "app.apk").startswith(b"APK home secret-pass")
    assert not init.exists()



def _app_fakes(monkeypatch, states=None, fail_polls=0):
    calls = {"submit": 0, "polls": 0}
    states = states or [{"state": "done"}]

    def job(jid):
        calls["polls"] += 1
        if calls["polls"] <= fail_polls:
            raise OSError("builder restarting")
        return states.pop(0) if len(states) > 1 else states[0]

    def submit_apk(*a, **k):
        calls["submit"] += 1
        return f"a{calls['submit']}"
    monkeypatch.setattr(me, "submit_apk", submit_apk)
    monkeypatch.setattr(me, "job", job)
    monkeypatch.setattr(me, "artifact", lambda jid, name: {"release.keystore": b"K", "app.apk": b"PK"}[name])
    monkeypatch.setattr(me, "drop_artifact", lambda jid, name: None)
    monkeypatch.setattr(me.AppBuild, "POLL_S", 0.01)
    return calls


def _until(app, want):
    for _ in range(300):
        if app.state.get("state") == want and not app.state.get("busy"):
            return app.state
        time.sleep(0.02)
    raise AssertionError(app.state)


def _fresh_app(tmp_path):
    app = me.AppBuild("testapp", "test-app", tmp_path, "test.apk")
    for f in (app.key, app.key_pass, app.apk):
        f.unlink(missing_ok=True)
    return app


def test_an_app_build_is_busy_until_collected_and_a_stuck_one_ends(tmp_path, monkeypatch):
    calls = _app_fakes(monkeypatch, [{"state": "compiling"}])
    monkeypatch.setattr(me.AppBuild, "POLL_MAX", 5)
    app = _fresh_app(tmp_path)
    app.start()
    with pytest.raises(core.HTTPException) as e:
        app.start()                                     # busy: a second click never makes a second key
    assert e.value.status_code == 409
    assert _until(app, "error")["error"] == "did not end" and calls["submit"] == 1
    app.start()                                         # not stuck after it
    _until(app, "error")


def test_an_app_build_rides_out_poll_errors_and_stores_key_then_apk(tmp_path, monkeypatch):
    _app_fakes(monkeypatch, fail_polls=3)
    app = _fresh_app(tmp_path)
    assert app.start()["new_key"] is True
    st = _until(app, "done")
    assert st["size"] == 2 and app.key.read_bytes() == b"K" and app.apk.read_bytes() == b"PK"
    assert oct(app.key.stat().st_mode & 0o777) == "0o600" and oct(app.key_pass.stat().st_mode & 0o777) == "0o600"
    _app_fakes(monkeypatch, fail_polls=99)
    app.start()
    assert "builder restarting" in _until(app, "error")["error"]
    assert app.status()["apk"] is True and app.status()["key"] is True


def test_a_stale_app_job_never_writes_a_later_one(tmp_path):
    app = _fresh_app(tmp_path)
    app.state.update(job="a2", state="compiling", busy=True)
    app._mine("a1", state="done", busy=False)
    assert app.state["state"] == "compiling" and app.state["busy"] is True


def test_a_private_file_is_created_0600(tmp_path, monkeypatch):
    seen = []
    real = me.os.open
    monkeypatch.setattr(me.os, "open", lambda p, flags, mode=0o777: seen.append(mode) or real(p, flags, mode))
    me.write_private(tmp_path / "k", b"x")
    assert seen[0] == 0o600 and oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"


def test_app_jobs_have_nothing_to_release_and_sources_deep_down_stay(app_builder, tmp_path):
    url = app_builder[0]
    assert call(url, "/v1/jobs/a123456789ab/go", {})[0] == 404
    p = tmp_path / "deep"
    (p / "app" / "src" / "main" / "java" / "org" / "x" / "build").mkdir(parents=True)
    (p / "app" / "src" / "main" / "java" / "org" / "x" / "build" / "Tool.java").write_text("class Tool {}")
    (p / "app" / "build").mkdir()
    (p / "app" / "build" / "out.txt").write_text("stale")
    import io
    import tarfile
    names = tarfile.open(fileobj=io.BytesIO(me.project_tar(p))).getnames()
    assert "app/src/main/java/org/x/build/Tool.java" in names and "app/build/out.txt" not in names



def test_the_key_is_stored_before_the_builder_drops_it_and_a_failed_drop_still_brings_the_apk(tmp_path, monkeypatch):
    _app_fakes(monkeypatch)
    order = []
    real = me.write_private
    monkeypatch.setattr(me, "write_private", lambda p, d: (order.append(("write", p.name)), real(p, d)))

    def drop(jid, name):
        order.append(("drop", name))
        raise core.HTTPException(503, "builder gone")
    monkeypatch.setattr(me, "drop_artifact", drop)
    app = _fresh_app(tmp_path)
    app.start()
    assert _until(app, "done")["size"] == 2
    assert order.index(("write", "app.keystore")) < order.index(("drop", "release.keystore"))


def test_an_unexpected_error_at_start_never_leaves_it_busy(tmp_path, monkeypatch):
    _app_fakes(monkeypatch)
    monkeypatch.setattr(me, "submit_apk", lambda *a, **k: (_ for _ in ()).throw(PermissionError("project")))
    app = _fresh_app(tmp_path)
    with pytest.raises(core.HTTPException) as e:
        app.start()
    assert e.value.status_code == 500 and app.state["busy"] is False



def test_the_build_server_of_a_computer_is_reached_through_the_tunnel_with_no_key(build_server, monkeypatch):
    """Its port is on its computer's loopback only; the house calls the helper's tunnel, with no key."""
    url, srv, work = build_server
    monkeypatch.setattr(me, "TUNNEL_URL", url)
    monkeypatch.setattr(me, "_cfg", {})
    assert me.server() == "" and me.in_use() == ""
    with pytest.raises(core.HTTPException) as e:
        me.submit("node", {"main.yaml": ""})
    assert e.value.status_code == 503
    me.use_remote("192.0.2.61")
    assert me.server() == url and me.in_use() == "192.0.2.61" and me._cfg == {"remote": "192.0.2.61"}
    st = wait(url, me.submit("node", {"main.yaml": "esphome: {}"}))
    assert st["state"] == "done"
    c = TestClient(core.app)
    assert c.get("/api/build/server", headers=HOUSE).json() == {"remote": "192.0.2.61", "set": True}
    me.use_remote("")
    assert me.server() == ""


def test_no_pairing_left_and_only_the_house_token_sees_the_server(monkeypatch):
    import io
    import tarfile
    c = TestClient(core.app)
    for path, method in (("/plugins/build/install.sh", "get"), ("/plugins/build/pair", "post"), ("/api/build/pair/start", "post")):
        assert getattr(c, method)(path, headers=HOUSE).status_code in (404, 405), path
    assert c.put("/api/build/server", headers=HOUSE, json={"url": "http://192.0.2.40:8795"}).status_code == 405
    members = tarfile.open(fileobj=io.BytesIO(c.get("/plugins/build/server.tar.gz").content)).getmembers()
    assert sorted(m.name for m in members) == ["Dockerfile", "apk.Dockerfile", "server.py"]
    tar = tarfile.open(fileobj=io.BytesIO(c.get("/plugins/build/server.tar.gz").content))
    assert b"COPY server.py /srv/server.py" in tar.extractfile("apk.Dockerfile").read()      # its context is that folder
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "" and m.mode == 0o644 for m in members)
    phone = "yfd_" + "z" * 40
    monkeypatch.setattr(core, "TOKEN_CHECKS", [lambda tok, method, path: tok == phone])
    p = {"Authorization": "Bearer " + phone}
    for call_ in (lambda: c.get("/api/build/server", headers=p), lambda: c.post("/api/build/server/test", headers=p),
                  lambda: c.get("/api/build/jobs/0123456789ab", headers=p),
                  lambda: c.get("/api/build/jobs/0123456789ab/firmware.factory.bin", headers=p)):
        assert call_().status_code == 403
    assert c.get("/api/build/jobs/0123456789ab/main.yaml", headers=HOUSE).status_code == 400


def test_the_installer_puts_the_server_on_the_loopback_with_no_key():
    text = (pathlib.Path(me.__file__).parent / "server/install-build-server.sh").read_text()
    assert '"127.0.0.1:8795:8795"' in text and "BUILD_KEY" not in text and "/plugins/build/pair" not in text
    assert subprocess.run(["sh", "-n", str(pathlib.Path(me.__file__).parent / "server/install-build-server.sh")]).returncode == 0
