#!/usr/bin/env python3
"""Backups of a house: one tar.gz (mode 600) with everything that cannot be made again.

  house/          HOUSE_DIR as a whole (without .git and backups/), or the house files of this checkout
  env/.env        the secrets: tokens, broker passwords, keys
  zigbee2mqtt/    Z2M_DATA_DIR: the coordinator's network key and the paired devices (not its log)
  plugins-extra/  your own plug-ins
  yashome-backup.json   what this is: format, time, parts
Runtime state (pending timers, the status cache) stays out: restored days later it would replay old
commands. The backend makes one every night (BACKUP_AT, or "off") and Settings -> Backups shows it;
`make backup` makes one now; `make restore FILE=...` (tools/restore.sh) puts one back.

  python3 tools/backup.py make      python3 tools/backup.py list
"""
import json
import os
import re
import stat
import sys
import tarfile
import time
from pathlib import Path

PREFIX = "yashome-backup-"
FORMAT = 1
HOUSE_FILES = ("settings.json", "mqtt_devices.json", "plugin-data", "plugin-state")
RUNTIME = ("pending_timers.json", "status_cache.json")
PARTS = ("house", "env", "zigbee2mqtt", "plugins-extra")
LIGHT_SKIP = ("esphome", "voice")    # a light backup (before a merge): the house without its build cache and recordings
PACE = 32 << 20                       # a sync and a page-cache drop every this many bytes written
_LOG = re.compile(r"(^|/)zigbee2mqtt/log(/|$)|\.pre-restore-|(^|/)pending_timers\.corrupt-")


def keep(env: dict) -> int:
    """BACKUP_KEEP as a number of archives (14 when empty or not a number)."""
    try:
        return max(1, int(env.get("BACKUP_KEEP") or 14))
    except ValueError:
        return 14


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def items(root: Path, house: Path, env: dict, extra: Path | None = None) -> tuple[list, list]:
    """((path, name in the archive), ...) of everything a backup holds, and the warnings."""
    out, warn = [], []
    backups = root / "backups"
    if not house.is_dir():
        raise FileNotFoundError(f"the house directory {house} is not there (HOUSE_DIR in .env)")
    if _same(house, root):
        out += [(root / n, f"house/{n}") for n in HOUSE_FILES if (root / n).exists()]
        out += [(p, f"house/{p.name}") for p in sorted(root.glob("settings.json.pre-split-*"))]
    else:
        out += [(p, f"house/{p.name}") for p in sorted(house.iterdir())
                if p.name not in (".git",) + RUNTIME and not _same(p, backups)]
    if (root / ".env").is_file():
        out.append((root / ".env", "env/.env"))
    z2m = (env.get("Z2M_DATA_DIR") or "").strip().strip("\"'")
    if z2m:
        zp = (root / z2m) if not os.path.isabs(z2m) else Path(z2m)
        held = any(p.is_dir() and (_same(zp, p) or str(zp.resolve()).startswith(str(p.resolve()) + os.sep)) for p, _ in out)
        if zp.is_dir() and not held:
            out.append((zp.resolve(), "zigbee2mqtt"))      # a link to the real folder: its content, not the link
        elif not zp.is_dir():
            warn.append(f"zigbee2mqtt data ({z2m}) is not reachable here: the network key is NOT in this backup")
    extra = extra if extra is not None else root / "plugins-extra"
    if extra.is_dir() and any(extra.iterdir()):
        out.append((extra.resolve(), "plugins-extra"))
    return out, warn


def _filter(t):
    name = t.name
    parts = name.split("/")
    # .esphome: ESPHome's build cache, rebuilt by the next compile (its storage files are root's 0600)
    if "__pycache__" in parts or ".esphome" in parts or name.endswith((".tmp", ".pyc", ".part", ".apk")) or _LOG.search(name):
        return None
    return t


def _add(tar, path: Path, arc: str, warn: list) -> bool:
    """Add a file or a tree member by member: what this user cannot read (zigbee2mqtt's container may write as
    another one) is left out with a warning instead of failing the whole backup; a directory that cannot be
    listed is left out whole (no empty stand-in). Symlinks stay links. False when nothing of `path` went in."""
    left = []

    def readable(p: str) -> bool:
        try:
            st = os.lstat(p)
        except OSError:
            return False
        if stat.S_ISDIR(st.st_mode):
            return os.access(p, os.R_OK | os.X_OK)
        if not stat.S_ISREG(st.st_mode):
            return True                          # links, FIFOs, devices: tar stores or skips them, never opened
        try:
            with open(p, "rb"):
                return True
        except OSError:
            return False

    def one(p: str, a: str) -> bool:
        if _filter(tarfile.TarInfo(a)) is None:
            return True
        if not readable(p):
            left.append(p)
            return False
        try:
            tar.add(p, arcname=a, recursive=False)
            return True
        except (PermissionError, FileNotFoundError):       # gone or locked meanwhile; a full disk still fails
            tar.inodes = {k: v for k, v in tar.inodes.items() if v != a}
            left.append(p)
            return False
    went = one(str(path), arc)
    if went and path.is_dir() and not path.is_symlink():
        for d, dirs, files in os.walk(path, onerror=lambda e: left.append(e.filename)):
            rel = os.path.relpath(d, path)
            base = arc if rel == "." else f"{arc}/{rel}"
            dirs[:] = [n for n in sorted(dirs) if _filter(tarfile.TarInfo(f"{base}/{n}")) is not None
                       and one(os.path.join(d, n), f"{base}/{n}")]
            for n in sorted(files):
                one(os.path.join(d, n), f"{base}/{n}")
    if left:
        warn.append(f"{len(left)} item(s) not readable, left out: {', '.join(sorted(left)[:3])}")
    return went


class _Paced:
    """The archive's file, written with a sync and a page-cache drop every PACE bytes: a big backup never fills the dirty
    memory of the host (a 1.6 GB buffered write stalled the house once)."""
    def __init__(self, fh):
        self.fh, self.n = fh, 0

    def write(self, b):
        r = self.fh.write(b)
        self.n += len(b)
        if self.n >= PACE:
            self.fh.flush()
            os.fdatasync(self.fh.fileno())
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(self.fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            self.n = 0
        return r

    def flush(self):
        self.fh.flush()

    def tell(self):
        return self.fh.tell()


def make(root: Path, house: Path, out_dir: Path, env: dict, keep: int = 14, extra: Path | None = None, light: bool = False):
    """Write one backup; returns (path, warnings). Only the newest `keep` stay. light: the house without LIGHT_SKIP, and
    neither zigbee2mqtt nor your plug-ins (a restore puts back only what a backup holds)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    parts, warn = items(root, house, env, extra)
    if light:
        parts = [(p, a) for p, a in parts if a.split("/")[0] in ("house", "env") and not (a.startswith("house/") and a.split("/")[1] in LIGHT_SKIP)]
        warn = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kind = "-light" if light else ""
    name = out_dir / f"{PREFIX}{stamp}{kind}.tar.gz"
    n = 1
    while name.exists():
        n += 1
        name = out_dir / f"{PREFIX}{stamp}{kind}-{n}.tar.gz"
    tmp = name.with_name(name.name + ".part")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=_Paced(fh), mode="w:gz") as tar:
            went = {arc.split("/")[0] for path, arc in parts if _add(tar, path, arc, warn)}
            manifest = json.dumps({"format": FORMAT, "created": int(time.time()), "parts": sorted(went),
                                   **({"light": list(LIGHT_SKIP)} if light else {})}).encode()
            info = tarfile.TarInfo("yashome-backup.json")
            info.size, info.mtime, info.mode = len(manifest), int(time.time()), 0o600
            tar.addfile(info, fileobj=__import__("io").BytesIO(manifest))
        os.replace(tmp, name)
    finally:
        tmp.unlink(missing_ok=True)
    same = [x for x in listing(out_dir) if ("-light" in x["name"]) == light]    # a light one never pushes a full one out
    for old in same[max(1, keep if not light else 5):]:
        (out_dir / old["name"]).unlink(missing_ok=True)
    return name, warn


def listing(out_dir: Path) -> list:
    """Newest first: name, size, time."""
    files = [p for p in out_dir.glob(f"{PREFIX}*.tar.gz") if p.is_file()] if out_dir.is_dir() else []
    return [{"name": p.name, "size": p.stat().st_size, "ts": int(p.stat().st_mtime)}
            for p in sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)]


def _env(root: Path) -> dict:
    out = {}
    for line in (root / ".env").read_text(encoding="utf-8").splitlines() if (root / ".env").is_file() else []:
        k, sep, v = line.partition("=")
        if sep and k.strip() and not k.startswith("#"):
            out[k.strip()] = v.split(" #")[0].strip().strip("\"'")
    return out


def _path(root: Path, value: str, default: str) -> Path:
    p = Path(value or default)
    return p if p.is_absolute() else root / p


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    try:
        env = _env(root)
    except OSError as e:
        sys.exit(f"backup: .env: {e}")
    out = root / "backups"
    if len(sys.argv) > 1 and sys.argv[1] == "make":
        try:
            path, warn = make(root, _path(root, env.get("HOUSE_DIR"), "."), out, env,
                              keep=keep(env), extra=_path(root, env.get("PLUGINS_EXTRA_DIR"), "plugins-extra"))
        except FileNotFoundError as e:
            sys.exit(f"backup: {e}")
        for w in warn:
            print("warning:", w, file=sys.stderr)
        print(path)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        for b in listing(out):
            print(f"{b['name']}  {b['size'] // 1024} KB  {time.strftime('%Y-%m-%d %H:%M', time.localtime(b['ts']))}")
    else:
        sys.exit(__doc__)
