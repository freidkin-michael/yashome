"""Tests for helper/helper.py and tools/helper-job.sh; no docker needed."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline

REPO = Path(__file__).resolve().parents[2]
KEY = "test-key-123"

_spec = importlib.util.spec_from_file_location("yashome_helper", REPO / "helper" / "helper.py")
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)

ENV_EXAMPLE = """\
# Broker password
# used by b only
B_PASSWORD=changeme

# Poll interval in seconds
B_INTERVAL=30
# a plug-in may not reach the core's keys through this
DASHBOARD_TOKEN=
BACKUP_AT=
"""

ENV_TEXT = "# my house\nTZ=Europe/Paris\nB_INTERVAL=45 # faster\nPLUGINS=a\n"

FAKE_MAKE = """#!/bin/sh
echo "make $*" >> "$FAKE_MAKE_LOG"
exit "${FAKE_MAKE_RC:-0}"
"""

FAKE_BACKUP = """import os, pathlib, sys
pathlib.Path(".helper/backup-called").write_text(" ".join(sys.argv[1:]))
print("backups/yashome-1.tar.gz")
sys.exit(int(os.environ.get("FAKE_BACKUP_RC", "0")))
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)
    seed = tmp_path / "seed"
    (seed / "plugins" / "a").mkdir(parents=True)
    (seed / "plugins" / "a" / "__init__.py").write_text("")
    b = seed / "plugins" / "devices" / "b"
    (b / "setup").mkdir(parents=True)
    (b / "__init__.py").write_text("")
    (b / "setup" / "env.example").write_text(ENV_EXAMPLE)
    (seed / "secrets.env.example").write_text("DASHBOARD_TOKEN=\n# BACKUP_AT=03:30\n")
    (seed / "tools").mkdir()
    shutil.copy(REPO / "tools" / "helper-job.sh", seed / "tools" / "helper-job.sh")
    (seed / "tools" / "backup.py").write_text(FAKE_BACKUP)
    (seed / "CHANGELOG.md").write_text("# Changelog\n\n## 1.0\n- first\n")
    git(seed, "init", "-q", "-b", "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "initial")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    git(seed, "remote", "add", "origin", str(bare))
    co = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(bare), str(co))
    (seed / "CHANGELOG.md").write_text("# Changelog\n\n## 1.1\n- new thing\n\n## 1.0\n- first\n")
    git(seed, "commit", "-q", "-am", "release 1.1")
    git(seed, "push", "-q", "origin", "main")
    env = co / ".env"
    env.write_text(ENV_TEXT)
    env.chmod(0o600)
    return co


@pytest.fixture
def server(checkout, monkeypatch):
    monkeypatch.setattr(helper, "docker_gid", lambda: 999)
    monkeypatch.setattr(helper, "container_exists", lambda name: True)
    cfg = helper.Config({"HELPER_KEY": KEY, "HELPER_PORT": "0", "HELPER_BIND": "127.0.0.1",
                         "YASHOME_DIR": str(checkout), "YASHOME_HOUSE_ABS": str(checkout.parent / "house")})
    srv = helper.make_server(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, key=KEY):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if key is not None:
        req.add_header("X-Helper-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_key_required(server):
    assert call(server, "GET", "/v1/state", key=None)[0] == 401
    assert call(server, "GET", "/v1/state", key="wrong")[0] == 401
    assert call(server, "GET", "/v1/nope")[0] == 404
    server.cfg.key = ""
    status, body = call(server, "GET", "/v1/state")
    assert status == 503 and "error" in body


def test_state(server, checkout):
    status, st = call(server, "GET", "/v1/state")
    assert status == 200
    assert st["plugins"] == [
        {"name": "a", "group": "", "on": True, "required": False, "requires_on": False},
        {"name": "b", "group": "devices", "on": False, "required": False, "requires_on": False},
    ]
    keys = {k["key"]: k for k in st["keys"]["b"]}
    assert keys["B_PASSWORD"] == {"key": "B_PASSWORD", "comment": "Broker password used by b only",
                                  "secret": True, "host_only": False, "network_ok": False, "set": False, "value": None}
    assert keys["B_INTERVAL"]["value"] == "45" and keys["B_INTERVAL"]["set"] is True
    assert "a" not in st["keys"]
    g = st["git"]
    assert g["fetch_error"] is None and g["upstream"] == "origin/main"
    assert g["behind"] == 1 and g["ahead"] == 0 and g["dirty"] is False
    assert len(g["incoming"]) == 1 and g["incoming"][0].endswith("release 1.1")
    assert "## 1.1" in g["changelog"] and "- new thing" in g["changelog"]
    assert "- first" not in g["changelog"]
    assert st["previous"] is None and st["job"] is None


def test_state_secret_hidden_when_set(server, checkout):
    with open(checkout / ".env", "a") as f:
        f.write("B_PASSWORD='s3cret value'\n")
    keys = {k["key"]: k for k in call(server, "GET", "/v1/state")[1]["keys"]["b"]}
    assert keys["B_PASSWORD"]["set"] is True and keys["B_PASSWORD"]["value"] is None


def test_state_uses_pluginlib_for_requires(server, checkout):
    (checkout / "tools" / "pluginlib.py").write_text(
        "import sys\n"
        "if sys.argv[1] == 'all':\n    print('./plugins/a'); print('./plugins/devices/b')\n"
        "else:\n    print('./plugins/a'); print('./plugins/devices/b')\n")
    plugins = {p["name"]: p for p in call(server, "GET", "/v1/state")[1]["plugins"]}
    assert plugins["a"]["on"] and not plugins["a"]["requires_on"]
    assert plugins["b"]["on"] and plugins["b"]["requires_on"] and plugins["b"]["group"] == "devices"


def test_plugins(server, checkout):
    env = checkout / ".env"
    status, body = call(server, "POST", "/v1/plugins", {"on": ["a", "nope"]})
    assert status == 400 and "nope" in body["error"]
    assert env.read_text() == ENV_TEXT
    assert call(server, "POST", "/v1/plugins", {"on": "a"})[0] == 400
    status, body = call(server, "POST", "/v1/plugins", {"on": ["b", "a"]})
    assert status == 200 and body == {"ok": True, "plugins": ["a", "b"]}
    assert env.read_text() == ENV_TEXT.replace("PLUGINS=a", "PLUGINS=a,b")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert [p for p in os.listdir(checkout) if p.startswith(".env.")] == [".env.lock"]


def test_plugins_appends_when_missing(server, checkout):
    env = checkout / ".env"
    env.write_text("TZ=UTC")
    assert call(server, "POST", "/v1/plugins", {"on": ["b"]})[0] == 200
    assert env.read_text() == "TZ=UTC\nPLUGINS=b\n"


def test_env(server, checkout):
    env = checkout / ".env"
    assert call(server, "POST", "/v1/env", {"key": "TZ", "value": "UTC"})[0] == 400
    assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": "a\nb"})[0] == 400
    assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": "x" * 513})[0] == 400
    assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": 5})[0] == 400
    assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": "p@ss word#1"})[0] == 200
    assert env.read_text() == ENV_TEXT + "B_PASSWORD='p@ss word#1'\n"
    assert helper.parse_env(env.read_text())["B_PASSWORD"] == "p@ss word#1"
    assert call(server, "POST", "/v1/env", {"key": "B_INTERVAL", "value": "10"})[0] == 200
    assert "B_INTERVAL=10\n" in env.read_text()
    assert call(server, "POST", "/v1/env", {"key": "B_INTERVAL", "value": ""})[0] == 200
    assert "B_INTERVAL" not in env.read_text()
    assert env.read_text().startswith("# my house\nTZ=Europe/Paris\nPLUGINS=a\n")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_body_limits(server):
    status, _ = call(server, "POST", "/v1/env", {"key": "B_INTERVAL", "value": "x" * 20000})
    assert status == 400


def test_jobs(server, checkout, monkeypatch):
    calls = []
    monkeypatch.setattr(helper, "run_job_container", lambda argv: (calls.append(argv), (0, "id"))[1])
    status, body = call(server, "POST", "/v1/jobs", {"kind": "rollback"})
    assert status == 409 and calls == []
    assert call(server, "POST", "/v1/jobs", {"kind": "bogus"})[0] == 400
    status, body = call(server, "POST", "/v1/jobs", {"kind": "apply"})
    assert status == 202 and body["kind"] == "apply" and body["started"]
    d, house = str(checkout), str(checkout.parent / "house")
    assert calls == [["docker", "run", "-d", "--rm", "--name", "yashome-job", "--add-host", "host.docker.internal:host-gateway",
                      "--user", f"{os.getuid()}:{os.getgid()}", "--group-add", "999",
                      "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", f"{d}:{d}",
                      "-v", f"{house}:{house}", "-w", d, "-e", "HOME=/tmp", "-e", "YASHOME_JOB=1",
                      "yashome-helper:local",
                      "sh", "tools/helper-job.sh", "apply"]]
    assert call(server, "POST", "/v1/jobs", {"kind": "update"})[0] == 409
    job = call(server, "GET", "/v1/jobs/current")[1]["job"]
    assert job["kind"] == "apply" and job["state"] == "running"


def test_job_start_failure(server, monkeypatch):
    monkeypatch.setattr(helper, "run_job_container", lambda argv: (125, "no such image"))
    status, body = call(server, "POST", "/v1/jobs", {"kind": "apply"})
    assert status == 503 and "no such image" in body["error"]
    job = call(server, "GET", "/v1/jobs/current")[1]["job"]
    assert job["state"] == "error" and job["rc"] == 125 and any("no such image" in line for line in job["log"])


def test_jobs_current(server, checkout, monkeypatch):
    assert call(server, "GET", "/v1/jobs/current") == (200, {"job": None})
    hd = checkout / ".helper"
    hd.mkdir()
    (hd / "job.json").write_text(json.dumps({"kind": "update", "state": "done", "started": "s",
                                             "ended": "e", "rc": 0}))
    (hd / "job.log").write_text("".join(f"line {i}\n" for i in range(100)))
    job = call(server, "GET", "/v1/jobs/current")[1]["job"]
    assert job["state"] == "done" and job["rc"] == 0
    lines = job["log"]
    assert len(lines) == 80 and lines[0] == "line 20" and lines[-1] == "line 99"
    (hd / "job.json").write_text(json.dumps({"kind": "update", "state": "running", "started": "s"}))
    monkeypatch.setattr(helper, "container_exists", lambda name: False)
    job = call(server, "GET", "/v1/jobs/current")[1]["job"]
    assert job["state"] == "error" and job["lost"] is True
    assert call(server, "GET", "/v1/state")[1]["job"]["lost"] is True


def test_rollback_allowed_with_previous(server, checkout, monkeypatch):
    monkeypatch.setattr(helper, "run_job_container", lambda argv: (0, "id"))
    (checkout / ".helper").mkdir()
    (checkout / ".helper" / "previous").write_text(git(checkout, "rev-parse", "HEAD") + "\n")
    assert call(server, "GET", "/v1/state")[1]["previous"] == git(checkout, "rev-parse", "HEAD")
    assert call(server, "POST", "/v1/jobs", {"kind": "rollback"})[0] == 202


@pytest.fixture
def job_env(checkout, tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "make").write_text(FAKE_MAKE)
    (fakebin / "make").chmod(0o755)
    (fakebin / "python3").symlink_to(sys.executable)
    env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", FAKE_MAKE_LOG=str(tmp_path / "make.log"))
    return env


def run_job(checkout, env, kind, **extra):
    r = subprocess.run(["sh", "tools/helper-job.sh", kind], cwd=checkout, env=dict(env, **extra),
                       capture_output=True, text=True, check=False)
    return r.returncode, json.loads((checkout / ".helper" / "job.json").read_text())


def test_job_script_apply(checkout, job_env, tmp_path):
    rc, job = run_job(checkout, job_env, "apply")
    assert rc == 0 and job["state"] == "done" and job["rc"] == 0 and job["kind"] == "apply"
    assert job["started"] and job["ended"]
    assert (tmp_path / "make.log").read_text() == "make update\n"
    assert "== apply done rc=0" in (checkout / ".helper" / "job.log").read_text()


def test_job_script_apply_failure(checkout, job_env):
    rc, job = run_job(checkout, job_env, "apply", FAKE_MAKE_RC="2")
    assert rc == 2 and job["state"] == "error" and job["rc"] == 2 and job["ended"]


def test_job_script_update_then_rollback(checkout, job_env, tmp_path):
    old = git(checkout, "rev-parse", "HEAD")
    rc, job = run_job(checkout, job_env, "update")
    assert rc == 0 and job["state"] == "done"
    assert (checkout / ".helper" / "backup-called").read_text() == "make"
    assert (checkout / ".helper" / "previous").read_text().strip() == old
    assert git(checkout, "rev-parse", "HEAD") != old
    assert "- new thing" in (checkout / "CHANGELOG.md").read_text()

    (checkout / "CHANGELOG.md").write_text("local edit\n")
    rc, job = run_job(checkout, job_env, "rollback")
    assert rc == 3 and job["state"] == "error"
    assert (checkout / ".helper" / "previous").exists()
    git(checkout, "checkout", "--", "CHANGELOG.md")

    rc, job = run_job(checkout, job_env, "rollback")
    assert rc == 0 and job["state"] == "done" and job["kind"] == "rollback"
    assert git(checkout, "rev-parse", "HEAD") == old
    assert not (checkout / ".helper" / "previous").exists()
    assert (tmp_path / "make.log").read_text() == "make update\nmake update\n"


def test_job_script_update_failure_and_missing_previous(checkout, job_env):
    rc, job = run_job(checkout, job_env, "update", FAKE_MAKE_RC="1")
    assert rc == 1 and job["state"] == "error" and job["kind"] == "update"
    (checkout / ".helper" / "previous").unlink()
    rc, job = run_job(checkout, job_env, "rollback")
    assert rc != 0 and job["state"] == "error"
    rc, job = run_job(checkout, job_env, "bogus")
    assert rc == 2 and job["kind"] == "invalid" and job["state"] == "error"


def test_a_plug_in_never_reaches_the_cores_keys(server):
    keys = {k["key"] for k in call(server, "GET", "/v1/state")[1]["keys"]["b"]}
    assert keys == {"B_PASSWORD", "B_INTERVAL"}
    for k in ("DASHBOARD_TOKEN", "BACKUP_AT", "HELPER_KEY", "PLUGINS", "COMPOSE_ENV_FILES", "DOCKER_HOST",
              "YASHOME_SSH_DIR"):
        status, body = call(server, "POST", "/v1/env", {"key": k, "value": "x"})
        assert status == 400 and "core" in body["error"], k


def envlib_get(cwd, key):
    return subprocess.run(["sh", "-c", f". {REPO}/tools/envlib.sh; get {key}"], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.rstrip("\n")


def test_env_quotes_read_alike_by_compose_and_envlib(server, checkout):
    for bad in ("a\\b", "it's \"x\"", "a$b", "a`b", "/etc/x", "../x", "~/x", "a/../b"):
        assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": bad})[0] == 400, bad
    for value in ("it's", 'say "hi"', "a b # c"):
        assert call(server, "POST", "/v1/env", {"key": "B_PASSWORD", "value": value})[0] == 200
        assert helper.parse_env((checkout / ".env").read_text())["B_PASSWORD"] == value
        assert envlib_get(checkout, "B_PASSWORD") == value


@pytest.mark.parametrize("sep", ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"])
def test_env_line_separators(server, checkout, sep):
    status, _ = call(server, "POST", "/v1/env", {"key": "B_INTERVAL", "value": f"1{sep}B_PASSWORD=evil"})
    assert status == 400
    (checkout / ".env").write_text(f"B_INTERVAL=1{sep}B_PASSWORD=evil\n", encoding="utf-8")
    assert "B_PASSWORD" not in helper.parse_env(helper.read_text(str(checkout / ".env")))


def test_envlib_get_takes_the_last(tmp_path):
    (tmp_path / ".env").write_text("K=first\nK=last # note\n")
    assert envlib_get(tmp_path, "K") == "last"
    assert helper.parse_env((tmp_path / ".env").read_text())["K"] == "last"


def test_host_only_keys_are_read_only(server, checkout):
    with open(checkout / "plugins" / "devices" / "b" / "setup" / "env.example", "a") as f:
        f.write("\nB_DATA_DIR=\nB_SERIAL=\n# [host-only] a file on this host\nB_CERT=\nMQTT_B_USER=b\n")
    keys = {k["key"]: k for k in call(server, "GET", "/v1/state")[1]["keys"]["b"]}
    assert [k for k in keys if keys[k]["host_only"]] == ["B_DATA_DIR", "B_SERIAL", "B_CERT", "MQTT_B_USER"]
    for k in ("B_DATA_DIR", "B_SERIAL", "B_CERT", "MQTT_B_USER"):
        status, body = call(server, "POST", "/v1/env", {"key": k, "value": "x"})
        assert status == 400 and "host" in body["error"], k


def test_no_env_writes_while_a_job_runs(server, checkout):
    (checkout / ".helper").mkdir()
    (checkout / ".helper" / "job.json").write_text(json.dumps({"kind": "apply", "state": "running"}))
    before = (checkout / ".env").read_text()
    assert call(server, "POST", "/v1/env", {"key": "B_INTERVAL", "value": "9"})[0] == 409
    assert call(server, "POST", "/v1/plugins", {"on": ["b"]})[0] == 409
    assert (checkout / ".env").read_text() == before


def test_plugins_keeps_names_it_cannot_see(server, checkout):
    env = checkout / ".env"
    env.write_text("PLUGINS=mine,a\n")
    status, body = call(server, "POST", "/v1/plugins", {"on": ["b"]})
    assert status == 200 and body["plugins"] == ["mine", "b"]
    assert env.read_text() == "PLUGINS=mine,b\n"
    env.write_text("PLUGINS=*\n")
    assert call(server, "POST", "/v1/plugins", {"on": ["b"]})[0] == 409
    assert env.read_text() == "PLUGINS=*\n"


def test_fetch_is_cached(server, checkout, monkeypatch):
    fetches = []
    real = helper.run
    monkeypatch.setattr(helper, "run", lambda cmd, cfg, timeout=10: (fetches.append(cmd) if "fetch" in cmd else None,
                                                                       real(cmd, cfg, timeout))[1])
    call(server, "GET", "/v1/state")
    call(server, "GET", "/v1/state")
    assert len(fetches) == 1
    monkeypatch.setattr(helper, "FETCH_EVERY", 0)
    call(server, "GET", "/v1/state")
    assert len(fetches) == 2


def test_job_mounts(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "docker_gid", lambda: 999)
    d = str(tmp_path / "co")
    cfg = helper.Config({"YASHOME_DIR": d, "YASHOME_HOUSE_ABS": d + "/.helper/none-house",
                         "YASHOME_EXTRA_ABS": "/srv/extra", "YASHOME_Z2M_ABS": "/srv/z2m",
                         "YASHOME_SSH_DIR": "/home/someone/.ssh"})
    argv = helper.job_argv(cfg, "update")
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == ["/var/run/docker.sock:/var/run/docker.sock", f"{d}:{d}", "/srv/extra:/srv/extra",
                      "/srv/z2m:/srv/z2m", "/home/someone/.ssh:/run/yashome-ssh:ro"]
    assert "GIT_SSH_COMMAND=ssh -i /k/id" not in argv
    cfg.git_ssh = "ssh -i /k/id"
    assert "GIT_SSH_COMMAND=ssh -i /k/id" in helper.job_argv(cfg, "update")


def test_restore_job_takes_one_archive_of_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "docker_gid", lambda: 999)
    d = tmp_path / "co"
    (d / "backups").mkdir(parents=True)
    cfg = helper.Config({"YASHOME_DIR": str(d)})
    name = "yashome-backup-20261018-120000.tar.gz"
    assert helper.job_argv(cfg, "restore", name)[-3:] == ["tools/helper-job.sh", "restore", name]
    for bad in (None, "", "../x.tar.gz", name):                  # none, loose, escaping, not there yet
        with pytest.raises(helper.HttpError) as e:
            helper.start_job(cfg, "restore", bad)
        assert e.value.status == 400


def test_job_log_kept_as_prev(server, checkout, monkeypatch):
    monkeypatch.setattr(helper, "run_job_container", lambda argv: (0, "id"))
    (checkout / ".helper").mkdir()
    (checkout / ".helper" / "job.log").write_text("last job\n")
    assert call(server, "POST", "/v1/jobs", {"kind": "apply"})[0] == 202
    assert (checkout / ".helper" / "job.log.prev").read_text() == "last job\n"
    assert (checkout / ".helper" / "job.log").read_text() == ""


def test_worktree_update_refused(checkout, job_env, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    git(checkout, "worktree", "add", "-q", str(wt))
    cfg = helper.Config({"YASHOME_DIR": str(wt)})
    st = helper.git_state(cfg)
    assert st["worktree"] is True and "worktree" in st["fetch_error"]
    monkeypatch.setattr(helper, "docker_gid", lambda: 999)
    with pytest.raises(helper.HttpError) as e:
        helper.start_job(cfg, "update")
    assert e.value.status == 409
    rc, job = run_job(wt, job_env, "update")
    assert rc == 4 and job["state"] == "error"
    assert not (wt / ".helper" / "backup-called").exists()


def test_job_script_update_refuses_a_dirty_tree(checkout, job_env):
    (checkout / "CHANGELOG.md").write_text("local edit\n")
    rc, job = run_job(checkout, job_env, "update")
    assert rc == 3 and not (checkout / ".helper" / "backup-called").exists()


def test_job_script_update_stops_when_the_backup_fails(checkout, job_env, tmp_path):
    old = git(checkout, "rev-parse", "HEAD")
    rc, job = run_job(checkout, job_env, "update", FAKE_BACKUP_RC="1")
    assert rc == 1 and git(checkout, "rev-parse", "HEAD") == old
    assert not (tmp_path / "make.log").exists()


def test_job_script_failed_pull_keeps_the_rollback_target(checkout, job_env, tmp_path):
    hd = checkout / ".helper"
    hd.mkdir()
    (hd / "previous").write_text("a" * 40 + "\n")
    # Diverged from upstream: --ff-only must refuse, not merge.
    (checkout / "local.txt").write_text("x\n")
    git(checkout, "add", "local.txt")
    git(checkout, "commit", "-q", "-m", "local")
    head = git(checkout, "rev-parse", "HEAD")
    rc, job = run_job(checkout, job_env, "update")
    assert rc != 0 and git(checkout, "rev-parse", "HEAD") == head
    assert (hd / "previous").read_text() == "a" * 40 + "\n"
    assert not (tmp_path / "make.log").exists()


def test_job_script_up_to_date_keeps_the_rollback_target(checkout, job_env, tmp_path):
    git(checkout, "pull", "-q", "--ff-only")
    hd = checkout / ".helper"
    hd.mkdir()
    (hd / "previous").write_text("b" * 40 + "\n")
    rc, job = run_job(checkout, job_env, "update")
    assert rc == 0 and (hd / "previous").read_text() == "b" * 40 + "\n"
    assert not (hd / "backup-called").exists() and not (tmp_path / "make.log").exists()


def test_job_script_rollback_names_the_backup(checkout, job_env):
    assert run_job(checkout, job_env, "update")[0] == 0
    assert (checkout / ".helper" / "previous-backup").read_text() == "backups/yashome-1.tar.gz\n"
    assert run_job(checkout, job_env, "rollback")[0] == 0
    log = (checkout / ".helper" / "job.log").read_text()
    assert "backup made before the update: backups/yashome-1.tar.gz" in log
    assert not (checkout / ".helper" / "previous-backup").exists()


def test_ports_and_choices(server, checkout):
    with open(checkout / "plugins" / "devices" / "b" / "setup" / "env.example", "a") as f:
        f.write("\nB_PORT=8080\n# the stick [one of: ember zstack]\nB_ADAPTER=\n")
    for bad in ("0.0.0.0:2222", "127.0.0.1:22", "0", "65536", "80a"):
        assert call(server, "POST", "/v1/env", {"key": "B_PORT", "value": bad})[0] == 400, bad
    assert call(server, "POST", "/v1/env", {"key": "B_PORT", "value": "8081"})[0] == 200
    assert call(server, "POST", "/v1/env", {"key": "B_ADAPTER", "value": "ember: x"})[0] == 400
    assert call(server, "POST", "/v1/env", {"key": "B_ADAPTER", "value": "zstack"})[0] == 200
    assert call(server, "POST", "/v1/env", {"key": "B_ADAPTER", "value": ""})[0] == 200


def test_state_says_plugins_all(server, checkout):
    assert call(server, "GET", "/v1/state")[1]["plugins_all"] is False
    (checkout / ".env").write_text("PLUGINS=*\n")
    assert call(server, "GET", "/v1/state")[1]["plugins_all"] is True


def test_job_script_rollback_without_previous(checkout, job_env):
    rc, job = run_job(checkout, job_env, "rollback")
    assert rc == 2 and "no previous commit recorded" in (checkout / ".helper" / "job.log").read_text()


def test_a_network_ok_key_takes_tcp_or_none_but_never_a_device(server, checkout):
    with open(checkout / "plugins" / "devices" / "b" / "setup" / "env.example", "a") as f:
        f.write("\n# the coordinator [network-ok]\nB_SERIAL=\n")
    key = {k["key"]: k for k in call(server, "GET", "/v1/state")[1]["keys"]["b"]}["B_SERIAL"]
    assert key["host_only"] and key["network_ok"]
    for value in ("tcp://192.0.2.7:6638", "tcp://slzb.local:6638", "none"):
        assert call(server, "POST", "/v1/env", {"key": "B_SERIAL", "value": value})[0] == 200, value
    assert "B_SERIAL=none" in (checkout / ".env").read_text()
    for value in ("/dev/sda", "tcp://192.0.2.7:0", "tcp://192.0.2.7", "tcp://-x:1", "udp://192.0.2.7:1", "x", ""):
        status, body = call(server, "POST", "/v1/env", {"key": "B_SERIAL", "value": value})
        assert status == 400 and "host" in body["error"], value


def test_a_failed_check_has_a_reason_in_words_and_the_page_never_says_up_to_date_then(monkeypatch):
    raw = ("Warning: Identity file /run/yashome-ssh/id not accessible: No such file or directory.\n"
           "ssh: connect to host localhost port 2222: Connection refused\nfatal: Could not read from remote repository.")
    assert helper.fetch_reason(raw) == "the git server does not answer" and helper.fetch_reason(None) is None
    assert helper.fetch_reason("Permission denied (publickey).") == "the git server refused the deploy key"
    page = _dashboard_page()
    block = page[page.index("${g.fetch_error ? `<div class=\"bindcaption\" data-up=\"failed\""):]
    block = block[:block.index("data-up=\"checked\"")]
    assert "Up to date." not in block                                        # the failed branch has no "up to date"
    assert page.count("i18n('Up to date.')") == 1                            # ... and the only one is the checked branch


def test_a_localhost_remote_is_rewritten_for_the_helper_and_its_jobs(monkeypatch):
    monkeypatch.setenv("YASHOME_GIT_INSTEADOF", "ssh://git@host.docker.internal:2222/=ssh://git@localhost:2222/")
    assert helper.git_rewrite_env() == {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "url.ssh://git@host.docker.internal:2222/.insteadOf",
                                        "GIT_CONFIG_VALUE_0": "ssh://git@localhost:2222/"}
    monkeypatch.setenv("YASHOME_GIT_INSTEADOF", "")
    assert helper.git_rewrite_env() == {}


# ---- the house's computers: /v1/hosts/check, scan, forget ----
FAKE_SSH = r"""#!/bin/sh
echo "$@" >> "$FAKE_SSH_LOG"
cat >> "$FAKE_SSH_LOG.in"
case "$FAKE_SSH_MODE" in
  ok) printf 'host=bedroom\nos=Ubuntu 26.04 LTS\nmac=00:11:22:33:44:55\nscreen=HDMI-A-2|SAM|SAMSUNG|1\nscreen=eDP-1|BOE||\nsudo=yes\n' ;;
  nosudo) printf 'host=kid\nos=Ubuntu\nmac=\nsudo=no\n' ;;
  refused) echo "fraddy@x: Permission denied (publickey)." >&2; exit 255 ;;
  changed) echo "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!" >&2; exit 255 ;;
  hold) exec sleep 30 ;;
  player) case " $* " in *" -M "*) exit 0 ;; esac; printf '{"ok": true, "state": {"idle": true, "volume": 80}}\n' ;;
esac
"""


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text(FAKE_SSH)
    (bin_ / "ssh").chmod(0o755)
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "id").write_text("k")
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_LOG", str(tmp_path / "ssh.log"))
    monkeypatch.setattr(helper, "SSH_KEYS", str(keys))
    cfg = helper.Config({"YASHOME_DIR": str(tmp_path), "HELPER_KEY": KEY})
    return cfg, tmp_path / "ssh.log"


def test_a_host_check_reads_the_probe_and_its_screens(fake_ssh, monkeypatch):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    st, out = helper.post_hosts_check(cfg, {"hosts": [{"host": "192.0.2.33", "user": "fraddy"}]})
    r = out["hosts"][0]
    assert st == 200 and r["ok"] and r["sudo"] and (r["hostname"], r["mac"]) == ("bedroom", "00:11:22:33:44:55")
    assert r["screens"] == [{"connector": "HDMI-A-2", "vendor": "SAM", "name": "SAMSUNG", "port": 1},
                            {"connector": "eDP-1", "vendor": "BOE", "name": "", "port": None}]
    args = log.read_text()
    assert "BatchMode=yes" in args and "StrictHostKeyChecking=accept-new" in args and "-l fraddy 192.0.2.33 sh -s" in args
    assert "hosts_known_hosts" in args


def test_a_host_check_says_why_it_is_not_a_computer(fake_ssh, monkeypatch):
    cfg, _ = fake_ssh
    for mode, why in (("nosudo", "sudo asks for a password"), ("refused", "the key is refused"), ("changed", "its host key changed")):
        monkeypatch.setenv("FAKE_SSH_MODE", mode)
        r = helper.check_host(cfg, "192.0.2.40", "fraddy")
        assert not r["ok"] and r["error"] == why, mode


def test_hosts_take_no_ssh_option_and_scan_only_a_small_private_net(fake_ssh):
    cfg, _ = fake_ssh
    for bad in ("-oProxyCommand=x", "a b", "", "x;y"):
        with pytest.raises(helper.HttpError):
            helper.post_hosts_check(cfg, {"hosts": [{"host": bad, "user": "fraddy"}]})
    with pytest.raises(helper.HttpError):
        helper.post_hosts_check(cfg, {"hosts": [{"host": "192.0.2.1", "user": "-x"}]})
    with pytest.raises(helper.HttpError):
        helper.post_hosts_check(cfg, {"hosts": [{"host": "192.0.2.1", "user": "u"}] * (helper.HOSTS_MAX + 1)})
    for net in ("8.8.8.0/24", "10.0.0.0/16", "nonsense"):
        with pytest.raises(helper.HttpError):
            helper.post_hosts_scan(cfg, {"net": net, "user": "fraddy"})


def test_a_scan_checks_only_the_addresses_with_ssh(fake_ssh, monkeypatch):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    monkeypatch.setattr(helper, "_ssh_open", lambda ip: ip in ("192.0.2.33", "192.0.2.61"))
    st, out = helper.post_hosts_scan(cfg, {"net": "192.0.2.0/24", "user": "fraddy"})
    assert st == 200 and out["net"] == "192.0.2.0/24" and sorted(h["host"] for h in out["hosts"]) == ["192.0.2.33", "192.0.2.61"]
    assert log.read_text().count("sh -s") == 2


def test_the_probe_is_a_fixed_script_in_the_image():
    probe = Path(helper.HOST_PROBE)
    assert probe.name == "host_probe.sh" and "sudo -n true" in probe.read_text()
    assert subprocess.run(["sh", "-n", str(probe)]).returncode == 0
    assert "host_probe.sh" in (REPO / "helper" / "Dockerfile").read_text()


def test_a_role_runs_its_fixed_script_with_checked_arguments_in_the_background(fake_ssh, monkeypatch, tmp_path):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    script = tmp_path / "plugins" / "tools" / "build" / "server"
    script.mkdir(parents=True)
    (script / "install-build-server.sh").write_text("echo installing\n")
    body = {"host": "192.0.2.33", "user": "fraddy", "role": "build", "action": "install",
            "args": {"house": "http://192.0.2.10:8765"}}
    with pytest.raises(helper.HttpError) as e:
        helper.post_hosts_run(cfg, body)
    assert e.value.status == 409                                              # never checked: its host key is not known
    (tmp_path / ".helper").mkdir(exist_ok=True)
    (tmp_path / ".helper" / "hosts_known_hosts").write_text("")
    for bad in ({"house": "http://192.0.2.10:8765; rm -rf /"}, {"house": "http://a b"}, {}):
        with pytest.raises(helper.HttpError):
            helper.post_hosts_run(cfg, {**body, "args": bad})
    with pytest.raises(helper.HttpError):
        helper.post_hosts_run(cfg, {**body, "role": "anything"})
    st, out = helper.post_hosts_run(cfg, body)
    assert st == 202 and out["run"]["state"] == "running"
    for _ in range(100):
        if helper.get_hosts_runs(cfg, None)[1]["runs"]["192.0.2.33"]["state"] != "running":
            break
        threading.Event().wait(0.05)
    run = helper.get_hosts_runs(cfg, None)[1]["runs"]["192.0.2.33"]
    assert run["state"] == "done" and run["rc"] == 0 and run["log"]
    args = log.read_text()
    assert "StrictHostKeyChecking=yes" in args and args.rstrip().endswith("timeout -k 10 1800 sudo -n sh -s") and "192.0.2.10:8765" not in args
    sent = (tmp_path / "ssh.log.in").read_text()
    assert sent.startswith("set -- 'http://192.0.2.10:8765'\necho installing")     # in the script: not on a command line


def test_the_probe_reports_the_roles_installed(fake_ssh, monkeypatch):
    cfg, _ = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    monkeypatch.setattr(helper, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "host=x\nrole=build\nrole=bad name\nsudo=yes\n", ""))
    r = helper.check_host(cfg, "192.0.2.33", "fraddy")
    assert r["ok"] and r["roles"] == ["build"]


def test_the_app_builder_role_fixes_its_own_argument(fake_ssh, monkeypatch, tmp_path):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    d = tmp_path / "plugins" / "tools" / "build" / "server"
    d.mkdir(parents=True, exist_ok=True)
    (d / "install-build-server.sh").write_text("echo x\n")
    (tmp_path / ".helper").mkdir(exist_ok=True)
    (tmp_path / ".helper" / "hosts_known_hosts").write_text("")
    for action, want in (("install", " apk"), ("remove", " noapk")):
        body = {"host": f"192.0.2.{50 + len(action)}", "user": "fraddy", "role": "apk", "action": action,
                "args": {"house": "http://192.0.2.10:8765", "apk": "apk" if action == "remove" else "noapk"}}
        helper.post_hosts_run(cfg, body)
        for _ in range(100):
            if helper.get_hosts_runs(cfg, None)[1]["runs"][body["host"]]["state"] != "running":
                break
            threading.Event().wait(0.05)
        assert (tmp_path / "ssh.log.in").read_text().splitlines()[-2].endswith("'http://192.0.2.10:8765'" + want.replace(" ", " '") + "'")


def test_a_windows_computer_runs_the_probe_and_its_roles_in_its_wsl(fake_ssh, monkeypatch, tmp_path):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    st, out = helper.post_hosts_check(cfg, {"hosts": [{"host": "192.0.2.40", "user": "yash-train", "wsl": "Ubuntu-26.04/train"}]})
    assert out["hosts"][0]["ok"] and log.read_text().splitlines()[-1].endswith("-l yash-train 192.0.2.40 wsl -d Ubuntu-26.04 -u train -- sh -s")
    for bad in ("Ubuntu 26.04/train", "Ubuntu/-x", "Ubuntu", "a/b;c"):
        with pytest.raises(helper.HttpError):
            helper.post_hosts_check(cfg, {"hosts": [{"host": "192.0.2.40", "user": "yash-train", "wsl": bad}]})
    assert helper.remote("sudo -n sh -s", "D/u") == "wsl -d D -u u -- sudo -n sh -s" and helper.remote("sh -s") == "sh -s"


def test_the_probe_reports_ollama_its_server_and_models(fake_ssh, monkeypatch):
    cfg, _ = fake_ssh
    out = "host=x\nrole=llm\nllm_serve=/home/someone/ollama/serve.sh\nllm_models=qwen3:14b,bad model,qwen3:4b-instruct-2507-q4_K_M\nsudo=yes\n"
    monkeypatch.setattr(helper, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    r = helper.check_host(cfg, "192.0.2.33", "fraddy")
    assert r["roles"] == ["llm"] and r["llm"] == {"serve": "/home/someone/ollama/serve.sh", "models": ["qwen3:14b", "qwen3:4b-instruct-2507-q4_K_M"]}
    for bad in ("x y", "-x", "a;b"):
        assert not helper.ROLE_ARG_RE["model"].match(bad)


def test_every_role_script_is_in_the_checkout_and_parses():
    root = REPO
    for (role, action), (rel, _names) in helper.ROLE_SCRIPTS.items():
        assert (root / rel).is_file(), (role, action, rel)
        assert subprocess.run(["sh", "-n", str(root / rel)]).returncode == 0, rel


def test_a_tunnel_brings_a_computers_server_here_and_follows_the_one_in_use(fake_ssh, monkeypatch, tmp_path):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "hold")
    monkeypatch.setattr(helper, "TUNNEL_RETRY_S", 0.1)
    helper._tunnels.clear()
    with pytest.raises(helper.HttpError) as e:
        helper.post_tunnels(cfg, {"role": "build", "host": "192.0.2.61", "user": "fraddy"})
    assert e.value.status == 409                                              # never checked: its host key is not known
    (tmp_path / ".helper").mkdir(exist_ok=True)
    (tmp_path / ".helper" / "hosts_known_hosts").write_text("")
    for bad in ({"role": "nope", "host": "192.0.2.61", "user": "fraddy"}, {"role": "build", "host": "-oProxyCommand=x", "user": "fraddy"},
                {"role": "build", "host": "192.0.2.61", "user": "Bad User"}):
        with pytest.raises(helper.HttpError):
            helper.post_tunnels(cfg, bad)

    def until(cond):
        for _ in range(100):
            if cond():
                return True
            threading.Event().wait(0.05)
        return False
    try:
        helper.post_tunnels(cfg, {"role": "build", "host": "192.0.2.61", "user": "fraddy"})
        assert until(lambda: helper.get_tunnels(cfg, None)[1]["tunnels"]["build"]["state"] == "up")
        assert until(lambda: log.exists() and "-L" in log.read_text())
        args = log.read_text().splitlines()[-1]
        assert "-N" in args and "-L 0.0.0.0:8795:127.0.0.1:8795" in args and args.endswith("-l fraddy 192.0.2.61")
        assert "StrictHostKeyChecking=yes" in args and "ExitOnForwardFailure=yes" in args
        assert json.loads((tmp_path / ".helper" / "tunnels.json").read_text()) == {"build": {"host": "192.0.2.61", "user": "fraddy", "wsl": ""}}
        first = helper._tunnels["build"]["proc"]
        helper.post_tunnels(cfg, {"role": "build", "host": "192.0.2.62", "user": "fraddy"})   # one computer at a time: the old one ends
        assert until(lambda: first.poll() is not None)
        assert until(lambda: (helper.get_tunnels(cfg, None)[1]["tunnels"]["build"] or {}).get("host") == "192.0.2.62")
        helper._tunnels.clear()
        helper.restore_tunnels(cfg)                                           # a restarted helper takes its tunnels up again
        assert until(lambda: helper.get_tunnels(cfg, None)[1]["tunnels"]["build"]["state"] == "up")
        helper.post_tunnels(cfg, {"role": "build"})
        assert helper.get_tunnels(cfg, None)[1]["tunnels"] == {} and json.loads((tmp_path / ".helper" / "tunnels.json").read_text()) == {}
        monkeypatch.setenv("FAKE_SSH_MODE", "refused")                        # ssh ends: down with why, tried again
        helper.post_tunnels(cfg, {"role": "tts", "host": "192.0.2.61", "user": "fraddy"})
        assert until(lambda: (helper._tunnels["tts"].get("error") or "").startswith("fraddy@x: Permission denied"))
        assert helper._tunnels["tts"]["state"] in ("down", "up")
    finally:
        for role in list(helper._tunnels):
            helper.set_tunnel(cfg, role, save=False)


def test_a_wsl_computer_s_tunnel_holds_its_distro_running(tmp_path):
    import types
    cfg = types.SimpleNamespace(dir=str(tmp_path))
    cmd = helper._tunnel_cmd(cfg, {"host": "192.0.2.7", "user": "u", "wsl": "Ubuntu-24.04/yash"}, 8092)
    assert "-N" not in cmd and cmd[-6:] == ["wsl", "-d", "Ubuntu-24.04", "--", "sleep", "2147483647"]
    assert "-N" in helper._tunnel_cmd(cfg, {"host": "192.0.2.7", "user": "u", "wsl": ""}, 8092)


def test_a_player_command_reaches_ctl_on_its_stdin_with_only_its_own_fields(fake_ssh, monkeypatch, tmp_path):
    cfg, log = fake_ssh
    monkeypatch.setenv("FAKE_SSH_MODE", "player")
    body = {"host": "192.0.2.61", "user": "fraddy", "cmd": "play", "url": "https://example.com/a.m3u8", "title": "A film",
            "start": 12, "on": True, "evil": "x", "headers": {"Referer": "https://site.example.com/"}, "subs": ["https://example.com/a.vtt"]}
    with pytest.raises(helper.HttpError) as e:
        helper.post_hosts_player(cfg, body)
    assert e.value.status == 409                                              # never checked: its host key is not known
    (tmp_path / ".helper").mkdir(exist_ok=True)
    (tmp_path / ".helper" / "hosts_known_hosts").write_text("")
    for bad in ({"cmd": "run"}, {"cmd": "play", "url": "file:///etc/passwd"}, {"cmd": "play", "url": None}, {"cmd": "seek", "by": "1; rm"},
                {"cmd": "pause", "on": "yes"}, {"cmd": "volume", "level": True}, {"cmd": "status", "wsl": "Ubuntu/u"},
                {"cmd": "status", "host": "-oProxyCommand=x"}, {"headers": {"X": "a\r\nB: c"}}, {"headers": {"Bad name": "x"}},
                {"subs": ["file:///etc/passwd"]}, {"headers": {f"H{i}": "x" for i in range(11)}}):
        with pytest.raises(helper.HttpError):
            helper.post_hosts_player(cfg, {**body, **bad})
    st, out = helper.post_hosts_player(cfg, body)
    assert st == 200 and out == {"ok": True, "state": {"idle": True, "volume": 80}}
    sent = (tmp_path / "ssh.log.in").read_text().strip().splitlines()[-1]
    assert json.loads(sent) == {"cmd": "play", "url": "https://example.com/a.m3u8", "title": "A film", "start": 12,
                                "headers": {"Referer": "https://site.example.com/"}, "subs": ["https://example.com/a.vtt"]}
    args = log.read_text().splitlines()
    assert any(" -M -N -f " in a for a in args)                               # one shared connection, in the background
    assert args[-1].endswith("-l fraddy 192.0.2.61 python3 /opt/yashome-player/ctl.py") and "StrictHostKeyChecking=yes" in args[-1]


def test_containers_says_which_one_has_no_network(monkeypatch, tmp_path):
    seen = []
    inspect = [{"Name": "/yashome-zigbee2mqtt-1", "Config": {"Labels": {"com.docker.compose.service": "zigbee2mqtt"}},
                "HostConfig": {"NetworkMode": "yashome_default"}, "NetworkSettings": {"Networks": {}}},
               {"Name": "/yashome-home-1", "Config": {"Labels": {"com.docker.compose.service": "home"}},
                "HostConfig": {"NetworkMode": "yashome_default"}, "NetworkSettings": {"Networks": {"yashome_default": {}}}},
               {"Name": "/yashome-off-1", "Config": {}, "HostConfig": {"NetworkMode": "none"}, "NetworkSettings": {"Networks": {}}}]

    def fake_run(cmd, cfg, timeout=10, input=None):
        seen.append(cmd)
        out = "a\nb\nc\n" if cmd[1] == "ps" else json.dumps(inspect)
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(helper, "run", fake_run)
    cfg = helper.Config({"YASHOME_DIR": str(tmp_path)})
    status, body = helper.get_containers(cfg, None)
    assert status == 200 and seen[0][:3] == ["docker", "ps", "-q"] and "label=com.docker.compose.project=yashome" in seen[0]
    assert seen[1] == ["docker", "inspect", "a", "b", "c"]
    assert [(c["name"], c["service"], c["netless"]) for c in body["containers"]] == [
        ("yashome-home-1", "home", False), ("yashome-off-1", "", False), ("yashome-zigbee2mqtt-1", "zigbee2mqtt", True)]
    monkeypatch.setattr(helper, "run", lambda *a, **k: None)
    with pytest.raises(helper.HttpError):
        helper.get_containers(cfg, None)
    assert ("GET", "/v1/containers") in helper.ROUTES
