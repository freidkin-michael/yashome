"""Backups (tools/backup.py, tools/restore.sh, /api/backups): what goes in, who may take it, how it comes back."""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "app" not in sys.modules:
    _FIX = tempfile.mkdtemp(prefix="yashome-test-")
    atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
    shutil.copytree(ROOT / "tests" / "fixtures", _FIX, dirs_exist_ok=True)
    os.environ["HOME_STACK_ROOT"] = _FIX
    os.environ.pop("HOUSE_ROOT", None)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import app  # noqa: E402
import backup  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def names(path):
    with tarfile.open(path) as t:
        return set(t.getnames())


class Make(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        a = self.a = self.d / "app"
        a.mkdir()
        (a / ".env").write_text("DASHBOARD_TOKEN=t\nZ2M_DATA_DIR=./plugin-data/zigbee/zigbee2mqtt\n")
        (a / "app.py").write_text("code")
        (a / "plugins-extra/mine/__pycache__").mkdir(parents=True)
        (a / "plugins-extra/mine/__init__.py").write_text("")
        (a / "plugins-extra/mine/__pycache__/x.pyc").write_text("")

    def test_a_house_beside_the_code_seen_by_two_paths_holds_only_its_files(self):
        a = self.a
        (a / "settings.json").write_text("{}")
        (a / "settings.json.pre-split-1").write_text("{}")
        (a / "pending_timers.json").write_text("{}")
        (a / "plugin-state/nfc").mkdir(parents=True)
        (a / "plugin-state/nfc/nfc_counters.json").write_text("{}")
        z = a / "plugin-data/zigbee/zigbee2mqtt"
        (z / "log/2026").mkdir(parents=True)
        (z / "coordinator_backup.json").write_text("key")
        (z / "log/2026/log.log").write_text("x" * 1000)
        (a / "backups").mkdir()
        (a / "backups/yashome-backup-old.tar.gz").write_text("old")
        other = self.d / "house-link"
        other.symlink_to(a)                             # /app and /house: one directory, two paths
        out, warn = backup.make(a, other, a / "backups", backup._env(a))
        got = names(out)
        assert {"house/settings.json", "house/settings.json.pre-split-1", "house/plugin-state/nfc/nfc_counters.json",
                "env/.env", "house/plugin-data/zigbee/zigbee2mqtt/coordinator_backup.json",
                "plugins-extra/mine/__init__.py", "yashome-backup.json"} <= got
        assert not any(n.startswith(("house/app.py", "house/backups", "house/.env")) or "/log/" in n
                       or "__pycache__" in n or "pending_timers" in n for n in got), got
        assert oct(out.stat().st_mode & 0o777) == "0o600" and warn == []

    def test_a_house_of_its_own_and_zigbee_beside_the_code(self):
        a, h = self.a, self.d / "house"
        (h / ".git").mkdir(parents=True)
        (h / "mqtt_devices.json").write_text("[]")
        (h / "status_cache.json").write_text("{}")
        (a / ".env").write_text("Z2M_DATA_DIR=./config/zigbee2mqtt\n")
        (a / "config/zigbee2mqtt").mkdir(parents=True)
        (a / "config/zigbee2mqtt/database.db").write_text("db")
        got = names(backup.make(a, h, a / "backups", backup._env(a))[0])
        assert {"house/mqtt_devices.json", "zigbee2mqtt/database.db", "env/.env"} <= got
        assert not any(n.startswith(("house/.git", "house/status_cache")) for n in got)

    def test_zigbee_out_of_reach_is_a_warning_and_extra_plugins_follow_their_setting(self):
        (self.a / ".env").write_text("Z2M_DATA_DIR=/nowhere/zigbee2mqtt\n")
        mine = self.d / "elsewhere"
        (mine / "p").mkdir(parents=True)
        (mine / "p/__init__.py").write_text("")
        out, warn = backup.make(self.a, self.a, self.a / "backups", backup._env(self.a), extra=mine)
        assert "plugins-extra/p/__init__.py" in names(out) and "network key is NOT" in warn[0]

    def test_only_the_newest_are_kept_and_names_never_clash(self):
        made = [backup.make(self.a, self.a, self.a / "backups", {}, keep=2)[0] for _ in range(3)]
        assert len(set(made)) == 3 and len(backup.listing(self.a / "backups")) == 2
        assert not list((self.a / "backups").glob("*.part"))


    def test_a_light_backup_leaves_the_cache_and_recordings_out_and_never_pushes_a_full_one_out(self):
        a, h = self.a, self.d / "house"
        for f in ("settings.json", "plugin-data/map/map.json", "esphome/build/big.bin", "voice/rec/1.wav"):
            (h / f).parent.mkdir(parents=True, exist_ok=True)
            (h / f).write_text("x")
        (a / ".env").write_text("Z2M_DATA_DIR=./config/zigbee2mqtt\n")
        (a / "config/zigbee2mqtt").mkdir(parents=True)
        (a / "config/zigbee2mqtt/database.db").write_text("db")
        full = backup.make(a, h, a / "backups", backup._env(a), keep=1)[0]
        light, _ = backup.make(a, h, a / "backups", backup._env(a), keep=1, light=True)
        got = names(light)
        assert {"house/settings.json", "house/plugin-data/map/map.json", "env/.env"} <= got and light.name.endswith("-light.tar.gz")
        assert not any(n.startswith(("house/esphome", "house/voice", "zigbee2mqtt", "plugins-extra")) for n in got)
        assert full.exists()                                                      # still kept: light ones rotate apart
        assert "house/esphome/build/big.bin" in names(full)

    def test_the_esphome_build_cache_is_never_backed_up(self):
        h = self.d / "house"
        for f in ("esphome/radar-1.yaml", "esphome/radar-1/.esphome/storage/radar-1.yaml.validated.yaml"):
            (h / f).parent.mkdir(parents=True, exist_ok=True)
            (h / f).write_text("x")
        out, warn = backup.make(self.a, h, self.a / "backups", {}, keep=1)
        got = names(out)
        assert "house/esphome/radar-1.yaml" in got and not any("/.esphome" in n for n in got) and not warn

    def test_a_backup_is_written_paced(self):
        synced = []
        with unittest.mock.patch.object(backup, "PACE", 1), unittest.mock.patch.object(backup.os, "fdatasync", lambda fd: synced.append(fd)):
            backup.make(self.a, self.a, self.a / "backups", {})
        assert synced                                                             # the dirty memory is flushed as it goes


class MakeEdges(Make):
    def test_one_directory_by_two_unrelated_paths_is_one_house(self):
        other = self.d / "other"
        other.mkdir()
        (self.a / "settings.json").write_text("{}")
        real = os.path.samefile
        same = {(str(other), str(self.a)), (str(self.a), str(other))}
        with unittest.mock.patch("os.path.samefile", lambda x, y: (str(x), str(y)) in same or real(x, y)):
            got = [arc for _, arc in backup.items(self.a, other, {})[0]]
        assert "house/settings.json" in got and not any(a.startswith("house/app.py") for a in got)

    def test_a_house_of_its_own_never_holds_the_backups_or_runtime(self):
        h = self.d / "house"
        h.mkdir()
        (self.a / "backups").mkdir()
        (h / "backups").symlink_to(self.a / "backups")
        for n in ("pending_timers.json", "status_cache.json", "pending_timers.corrupt-1.json", "x.pre-restore-1"):
            (h / n).write_text("{}")
        (h / "mqtt_devices.json").write_text("[]")
        got = names(backup.make(self.a, h, self.a / "backups", {})[0])
        assert "house/mqtt_devices.json" in got
        assert not any(n.startswith(("house/backups", "house/pending", "house/status", "house/x.pre")) for n in got), got

    def test_a_failed_write_leaves_no_part_and_never_truncates_one(self):
        with unittest.mock.patch("tarfile.TarFile.add", side_effect=OSError("disk full")):
            self.assertRaises(OSError, backup.make, self.a, self.a, self.a / "backups", {})
        assert not list((self.a / "backups").glob("*.part"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        part = self.a / "backups" / f"{backup.PREFIX}{stamp}.tar.gz.part"
        part.write_text("someone else's")
        with unittest.mock.patch("time.strftime", lambda *_: stamp):
            self.assertRaises(FileExistsError, backup.make, self.a, self.a, self.a / "backups", {})
        assert part.read_text() == "someone else's"

    def test_a_missing_house_dir_is_said_plainly(self):
        with self.assertRaises(FileNotFoundError) as e:
            backup.items(self.a, self.d / "nowhere", {})
        assert "HOUSE_DIR" in str(e.exception)

    def test_the_command_line_follows_its_settings(self):
        co = self.d / "co"
        shutil.copytree(ROOT / "tools", co / "tools")
        mine = self.d / "mine"
        (mine / "p").mkdir(parents=True)
        (mine / "p/__init__.py").write_text("")
        (co / ".env").write_text(f"BACKUP_KEEP=1\nPLUGINS_EXTRA_DIR={mine}\n")
        for _ in range(2):
            r = subprocess.run([sys.executable, str(co / "tools/backup.py"), "make"], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
        left = backup.listing(co / "backups")
        assert len(left) == 1 and "plugins-extra/p/__init__.py" in names(co / "backups" / left[0]["name"])
        (co / ".env").write_text("BACKUP_KEEP=14 days\n")
        assert subprocess.run([sys.executable, str(co / "tools/backup.py"), "make"], capture_output=True).returncode == 0


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads and writes every file: no permission to test")
class Unreadable(Make):
    def test_unreadable_files_and_dirs_are_left_out_with_a_warning_links_stay(self):
        a = self.a
        (a / "settings.json").write_text("{}")
        z = a / "plugin-data/zigbee/zigbee2mqtt"
        (z / "locked").mkdir(parents=True)
        (z / "locked/x").write_text("x")
        (z / "coordinator_backup.json").write_text("key")
        (z / "dangling").symlink_to(a / "nowhere")
        (a / "mqtt_devices.json").write_text("[]")
        os.chmod(z / "locked", 0)
        os.chmod(a / "mqtt_devices.json", 0)
        try:
            out, warn = backup.make(a, a, a / "backups", backup._env(a))
        finally:
            os.chmod(z / "locked", 0o700)
            os.chmod(a / "mqtt_devices.json", 0o600)
        got = names(out)
        assert "house/plugin-data/zigbee/zigbee2mqtt/coordinator_backup.json" in got
        assert "house/plugin-data/zigbee/zigbee2mqtt/dangling" in got
        assert "house/mqtt_devices.json" not in got and any("not readable" in w for w in warn), warn

    def test_a_hard_link_to_an_unreadable_file_never_points_at_a_missing_member(self):
        a = self.a
        (a / "plugin-state").mkdir()
        (a / "plugin-state/a.db").write_text("secret")
        os.link(a / "plugin-state/a.db", a / "plugin-state/b.db")
        os.chmod(a / "plugin-state/a.db", 0)
        try:
            out, warn = backup.make(a, a, a / "backups", {})
        finally:
            os.chmod(a / "plugin-state/a.db", 0o600)
        dest = self.d / "x"
        dest.mkdir()
        r = subprocess.run(["tar", "xzf", str(out), "-C", str(dest)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert warn

    def test_a_fifo_never_hangs_the_backup(self):
        (self.a / "plugin-state").mkdir()
        os.mkfifo(self.a / "plugin-state/pipe")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import backup; "
                            "from pathlib import Path; a = Path(sys.argv[2]); backup.make(a, a, a / 'backups', {})",
                            str(ROOT / "tools"), str(self.a)], timeout=20, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_an_unlistable_zigbee_folder_is_no_part_at_all(self):
        a = self.a
        (a / ".env").write_text("Z2M_DATA_DIR=./config/zigbee2mqtt\n")
        (a / "config/zigbee2mqtt").mkdir(parents=True)
        (a / "config/zigbee2mqtt/database.db").write_text("db")
        os.chmod(a / "config/zigbee2mqtt", 0)
        try:
            out, warn = backup.make(a, a, a / "backups", backup._env(a))
        finally:
            os.chmod(a / "config/zigbee2mqtt", 0o700)
        with tarfile.open(out) as t:
            manifest = json.loads(t.extractfile("yashome-backup.json").read())
            got = t.getnames()
        assert "zigbee2mqtt" not in manifest["parts"] and not any(n.startswith("zigbee2mqtt") for n in got)
        assert any("not readable" in w for w in warn)

    def test_a_linked_zigbee_folder_is_backed_up_by_its_content(self):
        a = self.a
        real = self.d / "z2m-real"
        real.mkdir()
        (real / "coordinator_backup.json").write_text("key")
        (a / "z2m").symlink_to(real)
        (a / ".env").write_text("Z2M_DATA_DIR=./z2m\n")
        assert "zigbee2mqtt/coordinator_backup.json" in names(backup.make(a, a, a / "backups", backup._env(a))[0])

    def test_an_unreadable_env_is_one_line_from_the_command_line(self):
        co = self.d / "co2"
        shutil.copytree(ROOT / "tools", co / "tools")
        (co / ".env").write_text("X=1\n")
        os.chmod(co / ".env", 0)
        try:
            r = subprocess.run([sys.executable, str(co / "tools/backup.py"), "make"], capture_output=True, text=True)
        finally:
            os.chmod(co / ".env", 0o600)
        assert r.returncode != 0 and "backup: .env" in r.stderr and "Traceback" not in r.stderr

    def test_the_command_line_names_a_missing_house_without_a_traceback(self):
        co = self.d / "co"
        shutil.copytree(ROOT / "tools", co / "tools")
        (co / ".env").write_text("HOUSE_DIR=./gone\n")
        r = subprocess.run([sys.executable, str(co / "tools/backup.py"), "make"], capture_output=True, text=True)
        assert r.returncode != 0 and "HOUSE_DIR" in r.stderr and "Traceback" not in r.stderr


class Api(unittest.TestCase):
    TOKEN = "backup-test-token-0123456789"

    def setUp(self):
        self.old, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, self.TOKEN
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.old_dir, app.BACKUP_DIR = app.BACKUP_DIR, self.d / "backups"
        self.c = TestClient(app.app)
        self.house = {"Authorization": "Bearer " + self.TOKEN}

    def tearDown(self):
        app.DASHBOARD_TOKEN, app.BACKUP_DIR = self.old, self.old_dir

    def test_only_the_house_token_lists_makes_and_downloads(self):
        os.environ["BACKUP_KEEP"] = "3"
        try:
            r = self.c.post("/api/backups", headers=self.house).json()
        finally:
            os.environ.pop("BACKUP_KEEP")
        assert r["ok"], r
        listed = self.c.get("/api/backups", headers=self.house).json()
        assert listed["items"][0]["name"] == r["name"]
        d = self.c.get("/api/backups/" + r["name"], headers=self.house)
        assert d.status_code == 200 and d.headers["cache-control"] == "no-store"
        app.TOKEN_CHECKS.append(lambda tok, m, p: tok == "phone-token-0123456789")
        try:
            phone = {"Authorization": "Bearer phone-token-0123456789"}
            for call in (lambda: self.c.get("/api/backups", headers=phone), lambda: self.c.post("/api/backups", headers=phone),
                         lambda: self.c.get("/api/backups/" + r["name"], headers=phone)):
                assert call().status_code == 403
        finally:
            app.TOKEN_CHECKS.pop()
        assert self.c.get("/api/backups").status_code == 401
        for bad in ("x.tar.gz", "yashome-backup-20260101-000000.tar.gz.part", "..%2Fsettings.json"):
            assert self.c.get("/api/backups/" + bad, headers=self.house).status_code == 404

    def test_the_backend_keeps_its_setting(self):
        os.environ["BACKUP_KEEP"] = "2"
        try:
            for _ in range(3):
                assert app.backup_now()["ok"]
        finally:
            os.environ.pop("BACKUP_KEEP")
        assert len(backup.listing(app.BACKUP_DIR)) == 2

    def test_a_loose_name_never_reaches_beside_the_folder(self):
        app.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        (app.BACKUP_DIR.parent / "secret.tar.gz").write_text("x")
        for name in ("..%2Fsecret.tar.gz", "yashome-backup-x/../../secret.tar.gz"):
            assert self.c.get("/api/backups/" + name, headers=self.house).status_code == 404

    def test_an_uploaded_backup_is_kept_and_a_stranger_file_refused(self):
        assert app.backup_now()["ok"]
        name = backup.listing(app.BACKUP_DIR)[0]["name"]
        data = (app.BACKUP_DIR / name).read_bytes()
        r = self.c.post("/api/backups/upload", content=data, headers=self.house)
        assert r.status_code == 200 and (app.BACKUP_DIR / r.json()["name"]).is_file() and r.json()["name"] != name
        for junk in (b"not a tarball", b""):
            assert self.c.post("/api/backups/upload", content=junk, headers=self.house).status_code == 400
        assert self.c.post("/api/backups/upload", content=data).status_code == 401

    def test_restore_job_names_only_a_known_archive(self):
        for bad in ("../x.tar.gz", "yashome-backup-20260101-000000.tar.gz", ""):
            r = self.c.post("/api/helper/jobs", json={"kind": "restore", "file": bad}, headers=self.house)
            assert r.status_code == 404, bad

    def test_a_merge_s_light_backup_is_restorable_and_never_hides_a_failed_nightly_one(self):
        r = app.backup_now(light=True)
        assert r["ok"] and r["name"].endswith("-light.tar.gz") and app._backup_name_ok(r["name"])
        import helper.helper as hh
        assert hh.BACKUP_NAME.fullmatch(r["name"]) and hh.BACKUP_NAME.fullmatch(r["name"].replace(".tar", "-2.tar"))
        old = dict(app._backup_last)
        try:
            app._backup_last.update(error="OSError: disk full")
            assert app.backup_now(light=True)["ok"] and app._backup_last["error"] == "OSError: disk full"
        finally:
            app._backup_last.clear(); app._backup_last.update(old)

    def test_off_means_the_loop_waits_for_a_time_and_makes_none(self):
        old, app.BACKUP_AT = app.BACKUP_AT, "off"
        waits = []
        try:
            with unittest.mock.patch.object(app, "backup_now", side_effect=AssertionError("no backup when off")):
                assert app._backup_tick(lambda t: (waits.append(t), True)[1]) is False
        finally:
            app.BACKUP_AT = old
        assert waits == [None]                          # no timeout: only a new time wakes it

    def test_the_time_set_on_the_page_is_saved_and_the_loop_takes_it_without_a_restart(self):
        old = dict(app._house)
        try:
            for bad in ("25:00", "3:3", "x", "never"):
                assert self.c.put("/api/house", json={"backup_at": bad}, headers=self.house).status_code == 400
            app._backup_wake.clear()
            assert self.c.put("/api/house", json={"backup_at": "4:05"}, headers=self.house).status_code == 200
            assert app._backup_wake.is_set() and app._house["backup_at"] == "04:05" and app.backup_at() == "04:05"
            assert json.loads(app.SETTINGS_PATH.read_text())["house"]["backup_at"] == "04:05"
            b = self.c.get("/api/backups", headers=self.house).json()
            assert (b["at"], b["nightly"]) == ("04:05", True)
            waits = []
            assert app._backup_tick(lambda t: (waits.append(t), False)[1]) is False   # woke early: not due, no backup
            due = app.backup_next(datetime.now(), "04:05") - datetime.now()
            assert abs(waits[0] - due.total_seconds()) < 5
            self.c.put("/api/house", json={"backup_at": "off"}, headers=self.house)
            assert self.c.get("/api/backups", headers=self.house).json()["nightly"] is False
            self.c.put("/api/house", json={"backup_at": ""}, headers=self.house)          # empty: .env's time again
            assert "backup_at" not in app._house and app.backup_at() == app.BACKUP_AT
            assert app._clean_house({"lang": "en", "backup_at": "99:99"}) == {"lang": "en"}
        finally:
            app._house.clear(); app._house.update(old)

    def test_the_loop_makes_the_backup_once_its_time_came(self):
        made = []
        seq = iter([datetime(2026, 10, 6, 3, 29), datetime(2026, 10, 6, 3, 30, 1)])

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return next(seq)
        with unittest.mock.patch.object(app, "datetime", Clock), unittest.mock.patch.object(app, "backup_now", lambda: made.append(1)), \
                unittest.mock.patch.object(app, "backup_at", lambda: "03:30"):
            waits = []
            assert app._backup_tick(lambda t: (waits.append(t), False)[1]) is True
        assert waits == [60.0] and made == [1]

    def test_the_nightly_time(self):
        now = datetime(2026, 10, 6, 20, 0)
        assert app.backup_next(now, "03:30") == datetime(2026, 10, 7, 3, 30)
        assert app.backup_next(now, "21:15") == datetime(2026, 10, 6, 21, 15)
        for off in ("off", "", "25:00", "3:3", "x"):
            assert app.backup_next(now, off) is None


class Restore(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        src = self.src = self.d / "src"
        shutil.copytree(ROOT / "tools", src / "tools")
        (src / ".env").write_text("HOUSE_DIR=./h\nZ2M_DATA_DIR=./config/zigbee2mqtt\nDASHBOARD_TOKEN=old\n")
        (src / "h").mkdir()
        (src / "h/settings.json").write_text('{"v": "old"}')
        (src / "config/zigbee2mqtt").mkdir(parents=True)
        (src / "config/zigbee2mqtt/database.db").write_text("old-net")
        (src / "plugins-extra/p").mkdir(parents=True)
        (src / "plugins-extra/p/__init__.py").write_text("old")
        self.bin = self.d / "bin"
        self.bin.mkdir()

    def run_restore(self, archive, docker_rc=0):
        (self.bin / "docker").write_text(f"#!/bin/sh\necho docker \"$@\" >> {self.d}/calls\nexit {docker_rc}\n")
        (self.bin / "make").write_text(f"#!/bin/sh\necho make \"$@\" >> {self.d}/calls\n")
        for t in ("docker", "make"):
            (self.bin / t).chmod(0o755)
        return subprocess.run(["sh", "tools/restore.sh", str(archive)], cwd=self.src, capture_output=True, text=True,
                              env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}, stdin=subprocess.DEVNULL)

    def test_restore_puts_everything_back_and_keeps_the_current_aside(self):
        s = self.src
        out, _ = backup.make(s, s / "h", s / "backups", backup._env(s), extra=s / "plugins-extra")
        (s / "h/settings.json").write_text('{"v": "new"}')
        (s / "config/zigbee2mqtt/database.db").write_text("new-net")
        (s / ".env").write_text("HOUSE_DIR=./h\nZ2M_DATA_DIR=./config/zigbee2mqtt\nDASHBOARD_TOKEN=new\n")
        (s / "plugins-extra/p/__init__.py").write_text("new")
        r = self.run_restore(out)
        assert r.returncode == 0, r.stderr
        assert (s / "h/settings.json").read_text() == '{"v": "old"}'
        assert (s / "config/zigbee2mqtt/database.db").read_text() == "old-net"
        assert (s / "plugins-extra/p/__init__.py").read_text() == "old"
        assert "DASHBOARD_TOKEN=old" in (s / ".env").read_text() and oct((s / ".env").stat().st_mode & 0o777) == "0o600"
        assert ["DASHBOARD_TOKEN=new" in p.read_text() for p in s.glob(".env.pre-restore-*")] == [True]
        calls = (self.d / "calls").read_text()
        assert "docker compose stop" in calls and "make update" in calls

    def test_a_flat_house_restores_only_house_files_and_keeps_this_house_dir(self):
        s = self.src
        (s / ".env").write_text("Z2M_DATA_DIR=./config/zigbee2mqtt\nHOUSE_DIR=/old/box/house\n")
        (s / "settings.json").write_text('{"v": "old"}')
        (s / "app.py").write_text("old code")
        out, _ = backup.make(s, s, s / "backups", backup._env(s))
        (s / ".env").write_text("Z2M_DATA_DIR=./config/zigbee2mqtt\n")
        (s / "app.py").write_text("new code")
        r = self.run_restore(out)
        assert r.returncode == 0, r.stderr
        assert (s / "app.py").read_text() == "new code" and (s / "settings.json").read_text() == '{"v": "old"}'
        assert "HOUSE_DIR=/old" not in (s / ".env").read_text()

    def tarball(self, members):
        p = self.d / f"t{len(list(self.d.glob('t*.tar.gz')))}.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            for arc, src in members:
                t.add(src, arcname=arc)
        return p

    def test_a_manifest_alone_or_known_parts_alone_are_not_enough(self):
        m = self.d / "m.json"
        m.write_text("{}")
        assert self.run_restore(self.tarball([("yashome-backup.json", m), ("tools", self.src / "tools")])).returncode != 0
        r = self.run_restore(self.tarball([("house", self.src / "h")]))
        assert r.returncode != 0 and "format 1" in r.stderr

    def test_a_new_computer_takes_the_backups_zigbee_place_and_pre_split_files(self):
        s = self.src
        (s / "h/settings.json.pre-split-1").write_text("{}")
        out, _ = backup.make(s, s / "h", s / "backups", backup._env(s))
        (s / ".env").write_text("HOUSE_DIR=./h\n")
        shutil.rmtree(s / "config")
        r = self.run_restore(out)
        assert r.returncode == 0, r.stderr
        assert (s / "config/zigbee2mqtt/database.db").read_text() == "old-net"
        assert (s / "h/settings.json.pre-split-1").exists()

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads and writes every file: no permission to test")
    def test_a_half_restore_says_how_to_undo_and_leaves_no_secrets_behind(self):
        s = self.src
        out, _ = backup.make(s, s / "h", s / "backups", backup._env(s))
        (s / ".env").write_text("HOUSE_DIR=./other\nZ2M_DATA_DIR=./config/zigbee2mqtt\n")
        (s / "other").mkdir()
        (s / "other/settings.json").write_text("x")
        os.chmod(s / "other", 0o500)
        tmpdir = self.d / "tmp"
        tmpdir.mkdir()
        try:
            (self.bin / "docker").write_text(f"#!/bin/sh\necho docker \"$@\" >> {self.d}/calls\n")
            (self.bin / "make").write_text("#!/bin/sh\n")
            for t in ("docker", "make"):
                (self.bin / t).chmod(0o755)
            r = subprocess.run(["sh", "tools/restore.sh", str(out)], cwd=s, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "TMPDIR": str(tmpdir)})
        finally:
            os.chmod(s / "other", 0o700)
        assert r.returncode != 0 and "NOT finished" in r.stderr and "make update starts it again" in r.stderr
        assert list(tmpdir.iterdir()) == []
        assert "HOUSE_DIR=./other" in (s / ".env").read_text()

    def test_a_flat_backup_onto_a_house_of_its_own_puts_zigbee_where_env_says(self):
        s = self.src
        flat = self.d / "flat"
        flat.mkdir()
        (flat / ".env").write_text("Z2M_DATA_DIR=./plugin-data/zigbee/zigbee2mqtt\r\n")
        (flat / "settings.json").write_text('{"v": "flat"}')
        (flat / "plugin-data/zigbee/zigbee2mqtt").mkdir(parents=True)
        (flat / "plugin-data/zigbee/zigbee2mqtt/database.db").write_text("flat-net")
        os.chmod(flat / "settings.json", 0o644)
        out, _ = backup.make(flat, flat, flat / "backups", backup._env(flat))
        r = self.run_restore(out)
        assert r.returncode == 0, r.stderr
        assert (s / "plugin-data/zigbee/zigbee2mqtt/database.db").read_text() == "flat-net"
        assert not (s / "h/plugin-data/zigbee/zigbee2mqtt").exists()
        assert oct((s / "h/settings.json").stat().st_mode & 0o777) == "0o644"

    def test_an_env_holding_only_house_dir_and_no_env_at_all(self):
        s = self.src
        only = self.d / "only"
        (only / "h").mkdir(parents=True)
        (only / ".env").write_text("HOUSE_DIR=./h\n")
        (only / "h/settings.json").write_text("{}")
        out, _ = backup.make(only, only / "h", only / "backups", backup._env(only))
        r = self.run_restore(out)
        assert r.returncode == 0, r.stderr
        assert (s / ".env").read_text() == "HOUSE_DIR=./h\n"

    def test_a_kept_copy_is_never_overwritten(self):
        s = self.src
        out, _ = backup.make(s, s / "h", s / "backups", backup._env(s))
        now = int(time.time())
        for t in range(now - 1, now + 30):
            (s / f"h/settings.json.pre-restore-{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}").write_text("mine")
        r = self.run_restore(out)
        assert r.returncode != 0 and "exists already" in r.stderr
        assert all(p.read_text() == "mine" for p in (s / "h").glob("settings.json.pre-restore-*"))

    def test_a_stranger_archive_or_a_house_that_will_not_stop_is_refused(self):
        bad = self.d / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as t:
            t.add(self.src / "tools", arcname="tools")
        assert self.run_restore(bad).returncode != 0
        out, _ = backup.make(self.src, self.src / "h", self.src / "backups", backup._env(self.src))
        r = self.run_restore(out, docker_rc=1)
        assert r.returncode != 0 and (self.src / "h/settings.json").read_text() == '{"v": "old"}'
        assert "make update" not in (self.d / "calls").read_text()


if __name__ == "__main__":
    unittest.main()


class EmptyIsTheDefault(unittest.TestCase):
    def test_empty_keys_of_the_example_read_as_their_defaults(self):
        core = Path(__file__).resolve().parent.parent
        env = {**os.environ, "HOME_STACK_ROOT": str(core / "tests" / "fixtures"),
               "BACKUP_AT": "", "TZ": "", "MQTT_HOST": "", "MQTT_PORT": ""}
        code = "import app; print(app.BACKUP_AT, app._LOCAL_TZ, app.MQTT_HOST, app.MQTT_PORT)"
        r = subprocess.run([sys.executable, "-c", code], cwd=core, env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.split()[-4:], ["03:30", "UTC", "mosquitto", "1883"], r.stderr[-400:])
