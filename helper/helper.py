"""Yashome helper: privileged dashboard API for plug-ins, .env keys and apply/update/rollback jobs."""
import contextlib
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 32 * 1024
MAX_VALUE = 512
JOB_NAME = "yashome-job"
JOB_KINDS = ("apply", "update", "rollback", "restore", "restart")
BACKUP_NAME = re.compile(r"yashome-backup-\d{8}-\d{6}(-light)?(-\d{1,3})?\.tar\.gz")
DOCKER_SOCK = "/var/run/docker.sock"
LOG_TAIL = 80
INCOMING_MAX = 50
CHANGELOG_MAX = 200
GIT = ["git", "-c", "safe.directory=*"]
FETCH_EVERY = 600
SSH_KEYS = "/run/yashome-ssh"  # the deploy key's directory in the helper and its jobs (YASHOME_SSH_DIR)
SECRET_RE = re.compile(r"PASSWORD|KEY|TOKEN|SECRET|PSK", re.IGNORECASE)
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LINE_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
PLAIN_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@%+,=-]*$")
# The house's computers (plug-in computers): one is a machine this key logs in to and where sudo needs no password.
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")   # an address or a name, never an ssh option
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
WSL_RE = re.compile(r"^([A-Za-z0-9._-]{1,40})/([a-z_][a-z0-9_-]{0,31})$")     # a Windows computer: its WSL distribution/user
HOSTS_MAX = 32
HOST_TIMEOUT = 12
SCAN_PORT_S = 0.6
HOST_PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "host_probe.sh")   # what runs there (sh -s)
SCREEN_KEEP = 8
# A role on a computer (plug-in computers): (role, action) -> the script it runs there as root (sudo sh -s < it), from this
# checkout, with the arguments each one takes (checked here: nothing else reaches the remote shell).
_BUILD_INSTALL = "plugins/tools/build/server/install-build-server.sh"
ROLE_SCRIPTS = {("build", "install"): (_BUILD_INSTALL, ("house",)),
                ("build", "remove"): ("helper/roles/build-remove.sh", ()),
                ("apk", "install"): (_BUILD_INSTALL, ("house", "apk")),      # the app builder beside the build server
                ("apk", "remove"): (_BUILD_INSTALL, ("house", "apk")),
                ("tts", "install"): ("helper/roles/tts-install.sh", ("house",)),
                ("tts", "remove"): ("helper/roles/tts-remove.sh", ()),
                ("llm", "install"): ("helper/roles/llm-install.sh", ("model",)),
                ("llm", "remove"): ("helper/roles/llm-remove.sh", ()),
                ("yashgen", "install"): ("helper/roles/yashgen-install.sh", ()),
                ("yashgen", "remove"): ("helper/roles/yashgen-remove.sh", ()),
                ("player", "install"): ("helper/roles/player-install.sh", ("house",)),
                ("player", "remove"): ("helper/roles/player-remove.sh", ()),
                ("touchmouse", "install"): ("helper/roles/touchmouse-install.sh", ("house",)),
                ("touchmouse", "remove"): ("helper/roles/touchmouse-remove.sh", ())}
ROLE_ARG_RE = {"house": re.compile(r"^https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?$"),
               "apk": re.compile(r"^(apk|noapk)$"),
               "model": re.compile(r"^[a-z0-9][a-z0-9._/-]{0,80}(?::[A-Za-z0-9._-]{1,40})?$")}
ROLE_ARG_FIXED = {("apk", "install"): {"apk": "apk"}, ("apk", "remove"): {"apk": "noapk"}}   # not the caller's to choose
HELLO_SCRIPT = "helper/roles/hello-install.sh"   # after every role run: the hello responder (UDP 7708)
ROLE_TIMEOUT = 1800                  # docker installed and an image built: minutes
ROLE_LOG = 200
_runs = {}                           # host -> {role, action, state, started, ended, rc, log}
_runs_lock = threading.Lock()

ENV_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
# Set while `docker run` is in flight: job.json says running before the container exists.
_starting = threading.Event()


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.key = env.get("HELPER_KEY", "")
        self.port = int(env.get("HELPER_PORT") or 8796)
        self.bind = env.get("HELPER_BIND") or "0.0.0.0"
        self.dir = os.path.abspath(env.get("YASHOME_DIR") or os.getcwd())
        house = env.get("YASHOME_HOUSE_ABS")
        self.house = os.path.abspath(house) if house else None
        self.image = env.get("HELPER_IMAGE") or "yashome-helper:local"
        self.extra_default = env.get("PLUGINS_EXTRA_DIR") or ""
        # Host paths the jobs see at the same place (tools/secrets.sh); a placeholder inside the checkout when unused.
        self.mounts = [os.path.abspath(p) for p in (house, env.get("YASHOME_EXTRA_ABS"), env.get("YASHOME_Z2M_ABS")) if p]
        ssh = env.get("YASHOME_SSH_DIR")
        self.ssh_dir = os.path.abspath(ssh) if ssh else None
        self.git_ssh = env.get("GIT_SSH_COMMAND") or ""


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def git_rewrite_env():
    """YASHOME_GIT_INSTEADOF="<url the helper reaches>=<url of the remote>": a remote on the host's localhost (Gitea) is
    host.docker.internal from inside the helper and its jobs; git's own url.insteadOf, as env so the jobs take it too."""
    new, sep, old = (os.environ.get("YASHOME_GIT_INSTEADOF") or "").partition("=")
    if not (sep and new.strip() and old.strip()):
        return {}
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{new.strip()}.insteadOf", "GIT_CONFIG_VALUE_0": old.strip()}


FETCH_REASONS = (                # a failed fetch in words: the first match of its git/ssh text
    ("Connection refused", "the git server does not answer"),
    ("Could not resolve hostname", "the git server's name is not known here"),
    ("Network is unreachable", "no network to the git server"),
    ("timed out", "the git server did not answer in time"),
    ("not accessible", "no deploy key here"),
    ("Permission denied", "the git server refused the deploy key"),
    ("Host key verification failed", "the git server's host key is not the known one"),
    ("not found", "the repository is not on the git server"),
)


def fetch_reason(error):
    """A failed check's reason for the page; the raw text goes under More."""
    if not error:
        return None
    return next((why for pat, why in FETCH_REASONS if pat.lower() in error.lower()), "git could not reach the repository")


def run(cmd, cfg, timeout=10, input=None):
    env = dict(os.environ)
    env.setdefault("HOME", "/tmp")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(git_rewrite_env())
    try:
        return subprocess.run(cmd, cwd=cfg.dir, capture_output=True, text=True, check=False,
                              errors="replace", timeout=timeout, env=env, input=input)
    except (OSError, subprocess.TimeoutExpired):
        return None


def env_path(cfg):
    return os.path.join(cfg.dir, ".env")


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def parse_value(raw):
    raw = raw.strip()
    if raw[:1] == "'":
        end = raw.find("'", 1)
        if end > 0:
            return raw[1:end]
    elif raw[:1] == '"':
        out, i = [], 1
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                out.append(raw[i + 1])
                i += 2
                continue
            if c == '"':
                return "".join(out)
            out.append(c)
            i += 1
    return re.sub(r"\s+#.*$", "", raw)


def lines_of(text, keepends=False):
    """.env lines split on \n only (one trailing \r dropped), as compose and the shell read them: str.splitlines()
    also breaks on \x85, \u2028 and friends, which would let a value smuggle in a line of its own."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
        ends = ["\n"] * len(parts)
    else:
        ends = ["\n"] * (len(parts) - 1) + [""]
    if keepends:
        return [p + e for p, e in zip(parts, ends)]
    return [p[:-1] if p.endswith("\r") else p for p in parts]


def parse_env(text):
    values = {}
    for line in lines_of(text):
        m = LINE_KEY_RE.match(line)
        if m:
            values[m.group(1)] = parse_value(line[m.end():])
    return values


def format_value(value):
    # Plain values stay unquoted so tools that read .env literally (make) agree. No escapes: post_env
    # refuses a backslash and a value holding both quotes, so compose and envlib's get read it alike.
    if PLAIN_VALUE_RE.match(value):
        return value
    quote = '"' if "'" in value else "'"
    return quote + value + quote


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_env_key(path, key, value):
    out, done = [], False
    for line in lines_of(read_text(path), keepends=True):
        m = LINE_KEY_RE.match(line)
        if m and m.group(1) == key:
            if value is not None and not done:
                eol = line[len(line.rstrip("\r\n")):]
                out.append(line[:m.end()] + format_value(value) + eol)
            done = True
            continue
        out.append(line)
    if value is not None and not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={format_value(value)}\n")
    atomic_write(path, "".join(out))


@contextlib.contextmanager
def env_locked(cfg):
    """.env for writing: one writer here, and the same flock as tools/envlib.sh; never while a job runs."""
    with ENV_LOCK, open(os.path.join(cfg.dir, ".env.lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with JOB_LOCK:  # no job starts between this check and the write
            job = current_job(cfg)
            if job and job.get("state") == "running":
                raise HttpError(409, "a job is running: change it when the job is over")
            yield


def plugin_roots(cfg, envvals):
    extra = envvals.get("PLUGINS_EXTRA_DIR") or cfg.extra_default or "./plugins-extra"
    return ["./plugins", extra]


def abs_in(cfg, path):
    return os.path.normpath(os.path.join(cfg.dir, path))


def pluginlib(cfg, *args):
    if not os.path.isfile(os.path.join(cfg.dir, "tools", "pluginlib.py")):
        return None
    r = run([sys.executable, "tools/pluginlib.py", *args], cfg)
    if r is None or r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _is_plugin(path):
    return os.path.isfile(os.path.join(path, "__init__.py"))


def _subdirs(path):
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except OSError:
        return []
    return [e.path for e in entries if e.is_dir() and not e.name.startswith((".", "_"))]


def fallback_discover(root):
    found = []
    for d in _subdirs(root):
        if _is_plugin(d):
            found.append(d)
        else:
            found.extend(s for s in _subdirs(d) if _is_plugin(s))
    return found


def discover_plugins(cfg, envvals):
    roots = plugin_roots(cfg, envvals)
    paths = pluginlib(cfg, "all", *roots)
    if paths is None:
        paths = [p for r in roots for p in fallback_discover(abs_in(cfg, r))]
    root_abs = {abs_in(cfg, r) for r in roots}
    plugins = {}
    for p in paths:
        p = abs_in(cfg, p)
        parent = os.path.dirname(p)
        name = os.path.basename(p)
        group = "" if parent in root_abs else os.path.basename(parent)
        plugins.setdefault(name, {"name": name, "group": group, "path": p})
    return plugins


def enabled_plugins(cfg, envvals, plugins):
    raw = envvals.get("PLUGINS", "")
    wanted = {n.strip() for n in raw.split(",") if n.strip()}
    explicit = set(plugins) if "*" in wanted else wanted & set(plugins)
    dirs = pluginlib(cfg, "dirs", raw, *plugin_roots(cfg, envvals))
    if dirs is None:
        enabled = set(explicit)
    else:
        enabled = {os.path.basename(os.path.normpath(d)) for d in dirs}
    return enabled, explicit


def read_env_example(path):
    keys, comment, seen = [], [], set()
    for line in lines_of(read_text(path)):
        s = line.strip()
        if not s:
            comment = []
            continue
        if s.startswith("#"):
            text = s.lstrip("#").strip()
            if text:
                comment.append(text)
            continue
        m = LINE_KEY_RE.match(line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            keys.append((m.group(1), " ".join(comment)))
        comment = []
    return keys


CORE_PREFIXES = ("COMPOSE_", "DOCKER_", "YASHOME_")
CORE_ALWAYS = {"DASHBOARD_TOKEN", "HELPER_KEY", "PLUGINS", "COMPOSE_FILE", "COMPOSE_PROFILES", "YASHOME_DIR",
               "YASHOME_HOUSE_ABS", "DOCKER_GID", "HOUSE_DIR", "PUID", "PGID", "MQTT_USER", "MQTT_PASSWORD"}


def core_keys(cfg):
    """The core's own keys (secrets.env.example, set or commented out, and a fixed list): never a plug-in's,
    so a plug-in's env.example cannot open the house token or the helper's own key to the dashboard."""
    keys = set(CORE_ALWAYS)
    for line in read_text(os.path.join(cfg.dir, "secrets.env.example")).splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


HOST_ONLY_SUFFIXES = ("_DIR", "_SERIAL", "_PUBLISH", "_PATH", "_FILE", "_USER")
PORT_RE = re.compile(r"^[0-9]{1,5}$")
ONE_OF_RE = re.compile(r"\[one of: ([^\]]+)\]")


def is_host_only(key, comment):
    """A host path, a device, a bind address or a broker account name: shown in the UI, changed on the host."""
    return key.endswith(HOST_ONLY_SUFFIXES) or "[host-only]" in comment


NETWORK_RE = re.compile(r"^tcp://[A-Za-z0-9][A-Za-z0-9.-]*:([0-9]{1,5})$")


def network_ok(comment):
    """[network-ok]: a host-only key (a device) the dashboard may still set to tcp://host:port or none."""
    return "[network-ok]" in comment


def is_network_value(value):
    m = NETWORK_RE.match(value)
    return value == "none" or bool(m and 0 < int(m.group(1)) < 65536)



def plugin_keys(plugins, core=frozenset()):
    return {name: [e for e in read_env_example(os.path.join(info["path"], "setup", "env.example"))
                   if e[0] not in core and not e[0].startswith(CORE_PREFIXES)]
            for name, info in sorted(plugins.items())}


def is_secret(key):
    return bool(SECRET_RE.search(key))


_fetched = {}  # checkout -> (monotonic time of the last fetch, its error)


def is_worktree(cfg):
    return os.path.isfile(os.path.join(cfg.dir, ".git"))


def fetch(cfg):
    """git fetch at most every FETCH_EVERY seconds: the settings page polls the state."""
    at, error = _fetched.get(cfg.dir, (None, None))
    if at is None or time.monotonic() - at >= FETCH_EVERY:
        r = run(GIT + ["fetch", "--quiet"], cfg, timeout=20)
        ok = r is not None and r.returncode == 0
        error = None if ok else ((r.stderr.strip()[-300:] if r else "") or "fetch failed or timed out")
        _fetched[cfg.dir] = (time.monotonic(), error)
    return error


def git_state(cfg):
    st = {"commit": None, "branch": None, "tag": None, "upstream": None, "behind": 0, "ahead": 0,
          "dirty": False, "incoming": [], "changelog": [], "fetch_error": None, "fetch_reason": None, "worktree": False}
    if is_worktree(cfg):
        # A worktree's repository lies outside the checkout, out of the helper's view.
        st["worktree"] = True
        st["fetch_error"] = "this checkout is a git worktree: update it on the host"
        st["fetch_reason"] = "this checkout is a git worktree: update it on the host"
        return st
    st["fetch_error"] = fetch(cfg)
    st["fetch_reason"] = fetch_reason(st["fetch_error"])

    def out(*args):
        res = run(GIT + list(args), cfg)
        return res.stdout if res is not None and res.returncode == 0 else None

    st["commit"] = (out("rev-parse", "HEAD") or "").strip() or None
    st["branch"] = (out("rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    st["tag"] = (out("describe", "--tags", "--abbrev=7") or "").strip() or None       # engine-2026.10.28-5, or -N-g<sha> past it
    # Untracked files (.env, .helper/) do not block a reset, so they are not "dirty".
    st["dirty"] = bool((out("status", "--porcelain", "--untracked-files=no") or "").strip())
    upstream = (out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "").strip()
    if not upstream:
        return st
    st["upstream"] = upstream
    counts = (out("rev-list", "--left-right", "--count", "HEAD...@{u}") or "").split()
    if len(counts) == 2:
        st["ahead"], st["behind"] = int(counts[0]), int(counts[1])
    log = out("log", "--oneline", "--no-decorate", "-n", str(INCOMING_MAX), "HEAD..@{u}") or ""
    st["incoming"] = [ln for ln in log.splitlines() if ln.strip()]
    # Three dots: only what upstream added since the merge base, not our own commits.
    diff = out("diff", "--no-color", "--no-ext-diff", "HEAD...@{u}", "--", "CHANGELOG.md") or ""
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    st["changelog"] = added[:CHANGELOG_MAX]
    return st


def helper_dir(cfg):
    return os.path.join(cfg.dir, ".helper")


def read_previous(cfg):
    try:
        with open(os.path.join(helper_dir(cfg), "previous"), encoding="utf-8") as f:
            sha = f.read().strip()
    except OSError:
        return None
    return sha if SHA_RE.match(sha) else None


def read_job(cfg):
    try:
        with open(os.path.join(helper_dir(cfg), "job.json"), encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError):
        return None
    return job if isinstance(job, dict) else None


def write_job(cfg, job):
    path = os.path.join(helper_dir(cfg), "job.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f)
        f.write("\n")
    os.replace(tmp, path)


def log_tail(cfg, lines=LOG_TAIL):
    try:
        with open(os.path.join(helper_dir(cfg), "job.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64 * 1024))
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-lines:]


def container_exists(name):
    try:
        r = subprocess.run(["docker", "inspect", "--type", "container", name],
                           capture_output=True, timeout=10, check=False)
    except subprocess.TimeoutExpired:
        return True  # unknown: neither report it lost nor allow a second job
    except OSError:
        return False
    return r.returncode == 0


STACK_PROJECT = "yashome"           # docker-compose.yml: name


def stack_containers(cfg):
    """The stack's running containers and their networks. One left with none (Docker restarted it while the
    link was down) answers nobody: the health page shows it, Restart the house makes it again."""
    r = run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={STACK_PROJECT}"], cfg, timeout=8)
    if r is None or r.returncode != 0:
        raise HttpError(503, "docker does not answer: " + ((r.stderr if r else "") or "no docker").strip()[-200:])
    ids = r.stdout.split()
    if not ids:
        return []
    r = run(["docker", "inspect", *ids], cfg, timeout=8)
    try:
        items = json.loads(r.stdout) if r is not None and r.returncode == 0 else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        raise HttpError(503, "docker inspect gave nothing readable")
    out = []
    for c in items:
        labels = (c.get("Config") or {}).get("Labels") or {}
        nets = sorted((c.get("NetworkSettings") or {}).get("Networks") or {})
        mode = (c.get("HostConfig") or {}).get("NetworkMode") or ""
        out.append({"name": str(c.get("Name") or "").lstrip("/"), "service": labels.get("com.docker.compose.service", ""),
                    "networks": nets, "netless": not nets and mode != "none"})
    return sorted(out, key=lambda x: x["name"])


def get_containers(cfg, _body):
    return 200, {"containers": stack_containers(cfg)}


def current_job(cfg):
    job = read_job(cfg)
    if job is None:
        return None
    if job.get("state") == "running" and not _starting.is_set() and not container_exists(JOB_NAME):
        job["state"] = "error"
        job["lost"] = True
    return job


def docker_gid():
    return os.stat(DOCKER_SOCK).st_gid


def job_argv(cfg, kind, file=None):
    try:
        gid = docker_gid()
    except OSError as e:
        raise HttpError(503, f"docker socket not available: {e}")
    argv = ["docker", "run", "-d", "--rm", "--name", JOB_NAME, "--add-host", "host.docker.internal:host-gateway",
            "--user", f"{os.getuid()}:{os.getgid()}", "--group-add", str(gid),
            "-v", f"{DOCKER_SOCK}:{DOCKER_SOCK}", "-v", f"{cfg.dir}:{cfg.dir}"]
    inside = cfg.dir.rstrip("/") + "/"
    for path in cfg.mounts:
        if not (path + "/").startswith(inside):
            argv += ["-v", f"{path}:{path}"]
    if cfg.ssh_dir:
        argv += ["-v", f"{cfg.ssh_dir}:{SSH_KEYS}:ro"]
    if cfg.git_ssh:
        argv += ["-e", f"GIT_SSH_COMMAND={cfg.git_ssh}"]
    for k, v in git_rewrite_env().items():
        argv += ["-e", f"{k}={v}"]
    argv += ["-w", cfg.dir, "-e", "HOME=/tmp", "-e", "YASHOME_JOB=1", cfg.image, "sh", "tools/helper-job.sh", kind] + ([file] if file else [])
    return argv


def run_job_container(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stderr or r.stdout).strip()


def start_job(cfg, kind, file=None):
    with JOB_LOCK:
        job = current_job(cfg)
        if job and job.get("state") == "running":
            raise HttpError(409, "a job is already running")
        if kind == "restore" and not (file and BACKUP_NAME.fullmatch(file)
                                      and os.path.isfile(os.path.join(cfg.dir, "backups", file))):
            raise HttpError(400, "restore: a yashome-backup-*.tar.gz of backups/")
        if kind == "rollback" and read_previous(cfg) is None:
            raise HttpError(409, "nothing to roll back to")
        if kind not in ("apply", "restart") and is_worktree(cfg):     # no git in them
            raise HttpError(409, "this checkout is a git worktree: update it on the host")
        argv = job_argv(cfg, kind, file)
        os.makedirs(helper_dir(cfg), exist_ok=True)
        started = now_iso()
        # Written here too so a poll right after 202 shows this job, not the last one.
        write_job(cfg, {"kind": kind, "state": "running", "started": started, "ended": None, "rc": None})
        log = os.path.join(helper_dir(cfg), "job.log")
        if os.path.exists(log):
            os.replace(log, log + ".prev")  # the last job's log stays readable on the host
        with open(log, "w", encoding="utf-8"):
            pass
        _starting.set()
        try:
            rc, output = run_job_container(argv)
        finally:
            _starting.clear()
        if rc != 0:
            write_job(cfg, {"kind": kind, "state": "error", "started": started,
                            "ended": now_iso(), "rc": rc})
            with open(os.path.join(helper_dir(cfg), "job.log"), "a", encoding="utf-8") as f:
                f.write(f"docker run failed (rc={rc}): {output}\n")
            raise HttpError(503, f"could not start the job container: {output[-300:]}")
    return {"kind": kind, "started": started}


def hosts_known(cfg):
    return os.path.join(helper_dir(cfg), "hosts_known_hosts")


def host_arg(value):
    host = str(value or "").strip()
    if not HOST_RE.match(host):
        raise HttpError(400, f"not an address or a host name: {host[:60]!r}")
    return host


def user_arg(value):
    user = str(value or "").strip()
    if not USER_RE.match(user):
        raise HttpError(400, "user: a Linux user name")
    return user


def wsl_arg(value):
    v = str(value or "").strip()
    if v and not WSL_RE.match(v):
        raise HttpError(400, "wsl: <distribution>/<user>")
    return v


def remote(shell, wsl=""):
    """The remote command: a Linux login runs it as it is; a Windows one runs it in its WSL distribution as that user."""
    if not wsl:
        return shell
    distro, wuser = WSL_RE.match(wsl).groups()
    return f"wsl -d {distro} -u {wuser} -- {shell}"


def check_host(cfg, host, user, wsl=""):
    """Logs in with the house's key (YASHOME_SSH_DIR/id) and runs host_probe.sh there:
    {ok, ssh, sudo, hostname, os, mac, screens: [{connector, vendor, name, port}], error}.
    A host met for the first time has its key kept (hosts_known_hosts); a key that changed later refuses."""
    out = {"host": host, "user": user, "ok": False, "ssh": False, "sudo": False}
    key = os.path.join(SSH_KEYS, "id")
    if not os.path.isfile(key):
        out["error"] = "the house has no ssh key (YASHOME_SSH_DIR)"
        return out
    os.makedirs(helper_dir(cfg), exist_ok=True)
    with open(HOST_PROBE, encoding="utf-8") as f:
        probe = f.read()
    r = run(["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "UserKnownHostsFile=" + hosts_known(cfg), "-o", "IdentitiesOnly=yes", "-l", user, host, remote("sh -s", wsl)],
            cfg, timeout=HOST_TIMEOUT, input=probe)
    if r is None:
        out["error"] = "no answer"
        return out
    kv, screens = {}, []
    for line in r.stdout.splitlines():
        k, sep, v = line.strip().partition("=")
        if not sep:
            continue
        if k == "screen" and len(screens) < SCREEN_KEEP:
            con, vend, name, port = (v.split("|") + ["", "", "", ""])[:4]
            screens.append({"connector": con[:20], "vendor": vend[:3], "name": name[:20], "port": int(port) if port.isdigit() else None})
        elif k == "role" and v.strip().isidentifier():
            kv.setdefault("roles", []).append(v.strip())
        elif k == "llm_serve" and re.match(r"^/[A-Za-z0-9._/-]{1,200}$", v.strip()):
            kv[k] = v.strip()
        elif k == "llm_models":
            kv[k] = [m for m in v.strip().split(",") if ROLE_ARG_RE["model"].match(m)][:20]
        elif k in ("host", "os", "mac", "sudo"):
            kv[k] = v.strip()
    if r.returncode != 0 or "sudo" not in kv:
        err = (r.stderr or "").lower()
        out["error"] = ("its host key changed" if "identification has changed" in err
                        else "a Windows computer: give its WSL" if "is not recognized" in err or "not recognized as" in (r.stdout or "").lower()
                        else "the key is refused" if "permission denied" in err
                        else "no ssh there" if "connection refused" in err
                        else "not reachable" if "timed out" in err or "no route" in err or "unreachable" in err
                        else "unknown host" if "could not resolve" in err
                        else (r.stderr.strip().splitlines() or ["ssh failed"])[-1][:120])
        return out
    out.update({"ssh": True, "hostname": kv.get("host", "")[:64], "os": kv.get("os", "")[:80], "mac": kv.get("mac", "").lower()[:17],
                "screens": screens, "roles": kv.get("roles", []), "sudo": kv["sudo"] == "yes",
                "llm": {"serve": kv.get("llm_serve", ""), "models": kv.get("llm_models", [])}})
    out["ok"] = out["sudo"]
    if not out["sudo"]:
        out["error"] = "sudo asks for a password"
    return out


def post_hosts_check(cfg, body):
    hosts = body.get("hosts")
    if not isinstance(hosts, list) or not hosts or len(hosts) > HOSTS_MAX:
        raise HttpError(400, f"hosts: a list of 1-{HOSTS_MAX} {{host, user}}")
    todo = [(host_arg(h.get("host")), user_arg(h.get("user")), wsl_arg(h.get("wsl"))) for h in hosts if isinstance(h, dict)]
    if len(todo) != len(hosts):
        raise HttpError(400, "hosts: each one {host, user, wsl?}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        return 200, {"hosts": list(ex.map(lambda hu: check_host(cfg, *hu), todo))}


def _ssh_open(ip):
    try:
        with socket.create_connection((ip, 22), timeout=SCAN_PORT_S):
            return True
    except OSError:
        return False


def post_hosts_scan(cfg, body):
    """Every address of a private network of at most 1024 (a /22) with port 22 open, each checked as above."""
    try:
        net = ipaddress.ip_network(str(body.get("net") or ""), strict=False)
    except ValueError:
        raise HttpError(400, "net: a network like 192.0.2.0/24")
    if net.version != 4 or not net.is_private or net.prefixlen < 22:
        raise HttpError(400, "net: a private IPv4 network of at most 1024 addresses")
    user = user_arg(body.get("user"))
    ips = [str(ip) for ip in net.hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        open22 = [ip for ip, up in zip(ips, ex.map(_ssh_open, ips)) if up]
    with ThreadPoolExecutor(max_workers=8) as ex:
        return 200, {"net": str(net), "hosts": list(ex.map(lambda ip: check_host(cfg, ip, user), open22))}


def _role_run(cfg, host, user, role, action, script, args, wsl=""):
    key = os.path.join(SSH_KEYS, "id")
    run_ = _runs[host]
    cmd = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
           "-o", "UserKnownHostsFile=" + hosts_known(cfg), "-o", "IdentitiesOnly=yes", "-o", "ServerAliveInterval=30",
           "-l", user, host, remote(f"timeout -k 10 {ROLE_TIMEOUT} sudo -n sh -s", wsl)]   # ends there too when it ends here
    rc = None
    try:
        with open(script, encoding="utf-8") as f:       # the arguments at its top, not on a command line (a key would show in ps)
            text = "set -- " + " ".join(f"'{a}'" for a in args) + "\n" + f.read()
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        p.stdin.write(text)
        p.stdin.close()
        timer = threading.Timer(ROLE_TIMEOUT, p.kill)
        timer.start()
        for line in p.stdout:
            with _runs_lock:
                run_["log"] = (run_["log"] + [line.rstrip()[:300]])[-ROLE_LOG:]
        rc = p.wait()
        timer.cancel()
        if rc == 0 and not wsl:        # the hello responder tells the house's one broadcast what runs here (not from WSL: NAT)
            with open(os.path.join(cfg.dir, HELLO_SCRIPT), encoding="utf-8") as f:
                h = subprocess.run(cmd, input=f.read(), capture_output=True, text=True, errors="replace", timeout=120)
            with _runs_lock:
                run_["log"] = (run_["log"] + [x[:300] for x in (h.stdout + h.stderr).splitlines()])[-ROLE_LOG:]
    except (OSError, subprocess.TimeoutExpired) as e:
        with _runs_lock:
            run_["log"].append(f"helper: {e}")
    with _runs_lock:
        run_.update(state="done" if rc == 0 else "failed", rc=rc, ended=now_iso())


def post_hosts_run(cfg, body):
    """A role installed on (or removed from) one computer, in the background: one run per computer at a time."""
    host, user, wsl = host_arg(body.get("host")), user_arg(body.get("user")), wsl_arg(body.get("wsl"))
    role, action = str(body.get("role") or ""), str(body.get("action") or "")
    spec = ROLE_SCRIPTS.get((role, action))
    if spec is None:
        raise HttpError(400, "role/action: " + ", ".join(f"{r} {a}" for r, a in ROLE_SCRIPTS))
    rel, names = spec
    given = {**(body.get("args") or {}), **ROLE_ARG_FIXED.get((role, action), {})}
    args = []
    for n in names:
        v = str(given.get(n) or "")
        if not ROLE_ARG_RE[n].match(v):
            raise HttpError(400, f"{n}: not valid")
        args.append(v)
    script = os.path.join(cfg.dir, rel)
    if not os.path.isfile(script):
        raise HttpError(404, f"{rel} is not in this checkout")
    if not os.path.isfile(hosts_known(cfg)):
        raise HttpError(409, "check the computer first (its host key is not known yet)")
    with _runs_lock:
        if (_runs.get(host) or {}).get("state") == "running":
            raise HttpError(409, "a run is going on there")
        _runs[host] = {"host": host, "role": role, "action": action, "state": "running", "started": now_iso(), "ended": None,
                       "rc": None, "log": []}
    threading.Thread(target=_role_run, args=(cfg, host, user, role, action, script, args, wsl), daemon=True).start()
    return 202, {"run": dict(_runs[host])}


def get_hosts_runs(cfg, _body):
    with _runs_lock:
        return 200, {"runs": {h: dict(r, log=list(r["log"])) for h, r in _runs.items()}}


def post_hosts_forget(cfg, body):
    """Its kept host key goes: the next check takes the new one (after a reinstall)."""
    host = host_arg(body.get("host"))
    if os.path.isfile(hosts_known(cfg)):
        run(["ssh-keygen", "-R", host, "-f", hosts_known(cfg)], cfg)
    return 200, {"host": host}


# ---- the player of a computer (role player): one command to its mpv, through the fixed ctl.py there ----
PLAYER_CTL = "python3 /opt/yashome-player/ctl.py"
PLAYER_TIMEOUT = 15
PLAYER_FIELDS = {"status": (), "play": ("url", "title", "start", "headers", "subs"), "pause": ("on",), "mute": ("on",), "seek": ("by", "to"),
                 "volume": ("level", "by"), "audio": ("id",), "sub": ("id",), "stop": ()}
PLAYER_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f]{1,2000}$")


def player_cmd(body):
    """The command as ctl.py takes it, only its own fields, each of its own type (ctl.py checks the ranges)."""
    cmd = str(body.get("cmd") or "")
    if cmd not in PLAYER_FIELDS:
        raise HttpError(400, "cmd: " + ", ".join(PLAYER_FIELDS))
    out = {"cmd": cmd}
    for k in PLAYER_FIELDS[cmd]:
        v = body.get(k)
        if v is None:
            continue
        if k == "url":
            if not isinstance(v, str) or not PLAYER_URL_RE.match(v):
                raise HttpError(400, "url: an http(s) address")
        elif k == "title":
            v = str(v)[:200]
        elif k == "headers":
            if not isinstance(v, dict) or len(v) > 10 or not all(
                    re.match(r"^[A-Za-z0-9-]{1,40}$", str(n)) and isinstance(x, str) and len(x) <= 4000 and not re.search(r"[\x00-\x1f]", x)
                    for n, x in v.items()):
                raise HttpError(400, "headers: at most 10 {name: value}, one line each")
        elif k == "subs":
            if not isinstance(v, list) or len(v) > 10 or not all(isinstance(u, str) and PLAYER_URL_RE.match(u) for u in v):
                raise HttpError(400, "subs: at most 10 http(s) addresses")
        elif k == "on":
            if not isinstance(v, bool):
                raise HttpError(400, "on: true or false")
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            raise HttpError(400, f"{k}: a number")
        out[k] = v
    if cmd == "play" and "url" not in out:
        raise HttpError(400, "url: an http(s) address")
    return out


def post_hosts_player(cfg, body):
    """{host, user, cmd, ...}: ctl.py there gets the command on its stdin; its answer {ok, state | error} comes back.
    One ssh connection per computer stays open a while (ControlPersist), so a press does not wait for a new login."""
    host, user = host_arg(body.get("host")), user_arg(body.get("user"))
    if body.get("wsl"):
        raise HttpError(400, "a player plays on a Linux computer's screen, not in a WSL")
    req = player_cmd(body)
    if not os.path.isfile(hosts_known(cfg)):
        raise HttpError(409, "check the computer first (its host key is not known yet)")
    ssh = ["ssh", "-i", os.path.join(SSH_KEYS, "id"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
           "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + hosts_known(cfg), "-o", "IdentitiesOnly=yes",
           "-S", "/tmp/yh-player-" + hashlib.sha1(f"{user}@{host}".encode()).hexdigest()[:16]]
    with _player_master_lock:           # one shared connection, made once: two at a time left a stray plain ssh behind
        if not os.path.exists(ssh[-1]):  # in the background, on /dev/null (a pipe would wait for it); a dead PC drops it
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(ssh + ["-M", "-N", "-f", "-o", "ControlPersist=300", "-o", "ServerAliveInterval=10",
                                      "-o", "ServerAliveCountMax=2", "-l", user, host], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=PLAYER_TIMEOUT, env={**os.environ, "HOME": "/tmp"})
    r = run(ssh + ["-o", "ControlMaster=no", "-l", user, host, PLAYER_CTL], cfg, timeout=PLAYER_TIMEOUT, input=json.dumps(req))
    if r is None:
        raise HttpError(504, "the computer did not answer")
    try:
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        err = (r.stderr or "").strip().splitlines()
        raise HttpError(502, (next((x for x in reversed(err) if "post-quantum" not in x and not x.startswith("**")), "")
                              or f"no answer from its player (rc {r.returncode})")[:200])
    if not isinstance(out, dict):
        raise HttpError(502, "not an answer of its player")
    return 200, out


# ---- tunnels: a server of another computer (build, tts) reached through ssh, so it needs no key of its own ----
# helper:<port> -> that computer's 127.0.0.1:<port>; the server there listens on its loopback only. The dashboard's
# backend reaches the helper on the compose network; nothing else does (the helper publishes no port).
TUNNEL_PORTS = {"build": 8795, "tts": 8092}
TUNNEL_RETRY_S = 10
_tunnels = {}                        # role -> {host, user, wsl, gen, state, since, error, proc}
_tunnels_lock = threading.Lock()


_player_master_lock = threading.Lock()


def tunnels_file(cfg):
    return os.path.join(helper_dir(cfg), "tunnels.json")


def _tunnel_view():
    return {r: {k: t.get(k) for k in ("host", "user", "wsl", "state", "since", "error")} for r, t in _tunnels.items() if t.get("host")}


def _tunnel_cmd(cfg, t, port):
    # a Windows computer: the tunnel holds its WSL distro running (it stops when idle and the service in it with it)
    m = WSL_RE.match(t.get("wsl") or "")
    keep = ["wsl", "-d", m.group(1), "--", "sleep", "2147483647"] if m else []
    return ["ssh"] + ([] if keep else ["-N"]) + ["-i", os.path.join(SSH_KEYS, "id"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + hosts_known(cfg), "-o", "IdentitiesOnly=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-L", f"0.0.0.0:{port}:127.0.0.1:{port}", "-l", t["user"], t["host"]] + keep


def _tunnel_loop(cfg, role, gen):
    """Keeps the tunnel of this role up while its target stays the same (gen); ssh ended: again after TUNNEL_RETRY_S."""
    port = TUNNEL_PORTS[role]
    while True:
        with _tunnels_lock:
            t = _tunnels.get(role)
            if not t or t["gen"] != gen or not t.get("host"):
                return
            cmd = _tunnel_cmd(cfg, t, port)
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        except OSError as e:
            p, err = None, str(e)
        if p is not None:
            with _tunnels_lock:
                if _tunnels.get(role) is not t:          # set_tunnel put a new target meanwhile
                    p.kill()
                    return
                t.update(proc=p, state="up", since=now_iso(), error=None)
            err = (p.communicate()[1] or "").strip().splitlines()
            err = next((x for x in reversed(err) if "post-quantum" not in x and not x.startswith("**")), f"ssh ended ({p.returncode})")
        with _tunnels_lock:
            if _tunnels.get(role) is not t:
                return
            t.update(proc=None, state="down", since=now_iso(), error=err[:200])
        threading.Event().wait(TUNNEL_RETRY_S)


def set_tunnel(cfg, role, host="", user="", wsl="", save=True):
    with _tunnels_lock:
        old = _tunnels.get(role) or {"gen": 0}
        if old.get("proc"):
            old["proc"].kill()
        t = {"host": host, "user": user, "wsl": wsl, "gen": old["gen"] + 1, "state": "starting" if host else None,
             "since": now_iso(), "error": None, "proc": None}
        _tunnels[role] = t
        if save:
            os.makedirs(helper_dir(cfg), exist_ok=True)
            atomic_write(tunnels_file(cfg), json.dumps({r: {k: x[k] for k in ("host", "user", "wsl")}
                                                        for r, x in _tunnels.items() if x.get("host")}))
    if host:
        threading.Thread(target=_tunnel_loop, args=(cfg, role, t["gen"]), daemon=True, name=f"tunnel-{role}").start()


def restore_tunnels(cfg):
    try:
        saved = json.loads(read_text(tunnels_file(cfg)) or "{}")
    except ValueError:
        saved = {}
    for role, t in saved.items():
        if role in TUNNEL_PORTS and isinstance(t, dict) and HOST_RE.match(str(t.get("host") or "")) and USER_RE.match(str(t.get("user") or "")):
            set_tunnel(cfg, role, t["host"], t["user"], str(t.get("wsl") or ""), save=False)


def post_tunnels(cfg, body):
    """{role, host, user, wsl}: the role's server is that computer's from now on (host empty: none, the tunnel goes)."""
    role = str(body.get("role") or "")
    if role not in TUNNEL_PORTS:
        raise HttpError(400, "role: " + ", ".join(TUNNEL_PORTS))
    if body.get("host"):
        host, user, wsl = host_arg(body.get("host")), user_arg(body.get("user")), wsl_arg(body.get("wsl"))
        if not os.path.isfile(hosts_known(cfg)):
            raise HttpError(409, "check the computer first (its host key is not known yet)")
        cur = _tunnels.get(role) or {}
        if (cur.get("host"), cur.get("user"), cur.get("wsl")) != (host, user, wsl):
            set_tunnel(cfg, role, host, user, wsl)
    else:
        set_tunnel(cfg, role)
    with _tunnels_lock:
        return 200, {"tunnels": _tunnel_view()}


def get_tunnels(cfg, _body):
    with _tunnels_lock:
        return 200, {"tunnels": _tunnel_view(), "ports": TUNNEL_PORTS}


def get_state(cfg, _body):
    envvals = parse_env(read_text(env_path(cfg)))
    plugins = discover_plugins(cfg, envvals)
    enabled, explicit = enabled_plugins(cfg, envvals, plugins)
    required = set(pluginlib(cfg, "required") or ())       # always on (pluginlib.REQUIRED): no box to untick
    plist = [{"name": n, "group": i["group"], "on": n in enabled or n in required, "required": n in required,
              "requires_on": n in enabled and n not in explicit and n not in required}
             for n, i in sorted(plugins.items())]
    keys = {}
    for name, entries in plugin_keys(plugins, core_keys(cfg)).items():
        if not entries:
            continue
        keys[name] = []
        for key, comment in entries:
            secret = is_secret(key)
            value = envvals.get(key)
            keys[name].append({"key": key, "comment": comment, "secret": secret, "host_only": is_host_only(key, comment),
                               "network_ok": network_ok(comment),
                               "set": bool(value), "value": None if secret else value})
    plugins_all = "*" in [n.strip() for n in envvals.get("PLUGINS", "").split(",")]
    return 200, {"plugins": plist, "plugins_all": plugins_all, "keys": keys, "git": git_state(cfg),
                 "previous": read_previous(cfg), "job": current_job(cfg)}


def post_plugins(cfg, body):
    names = body.get("on")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HttpError(400, '"on" must be a list of plug-in names')
    with env_locked(cfg):
        envvals = parse_env(read_text(env_path(cfg)))
        known = set(discover_plugins(cfg, envvals))
        unknown = sorted(set(names) - known)
        if unknown:
            raise HttpError(400, "unknown plug-in: " + ", ".join(unknown))
        current = [n.strip() for n in envvals.get("PLUGINS", "").split(",") if n.strip()]
        if "*" in current:
            raise HttpError(409, "PLUGINS=* switches every plug-in on: change it on the host")
        # Names this helper cannot see (a plug-in folder not mounted here) stay as they are.
        kept = [n for n in current if n not in known]
        chosen = kept + sorted(set(names))
        write_env_key(env_path(cfg), "PLUGINS", ",".join(chosen))
    return 200, {"ok": True, "plugins": chosen}


def post_env(cfg, body):
    key, value = body.get("key"), body.get("value", "")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise HttpError(400, "bad key")
    if not isinstance(value, str) or len(value) > MAX_VALUE:
        raise HttpError(400, f"value must be a string of at most {MAX_VALUE} chars")
    if any(unicodedata.category(c)[0] == "C" or c in "\x85\u2028\u2029" for c in value):
        raise HttpError(400, "value must not contain line breaks or control characters")
    if "$" in value or "`" in value or "\\" in value or ("'" in value and '"' in value):
        raise HttpError(400, "value must not contain $, `, a backslash or both quote kinds: set it on the host")
    if value.startswith(("/", "./", "../", "~")) or "/../" in value:
        raise HttpError(400, "a path is set on the host only")
    with env_locked(cfg):
        envvals = parse_env(read_text(env_path(cfg)))
        allowed = {k: c for entries in plugin_keys(discover_plugins(cfg, envvals), core_keys(cfg)).values() for k, c in entries}
        if key not in allowed:
            raise HttpError(400, "not a plug-in's key (the core's own keys are changed on the host only)")
        if is_host_only(key, allowed[key]) and not (network_ok(allowed[key]) and is_network_value(value)):
            raise HttpError(400, "this key names a path, a device or an account of the host: set it on the host only"
                            + (" (here: tcp://host:port or none)" if network_ok(allowed[key]) else ""))
        if value and key.endswith("_PORT") and not (PORT_RE.match(value) and 0 < int(value) < 65536):
            raise HttpError(400, "a port is a number from 1 to 65535")
        choices = ONE_OF_RE.search(allowed[key])
        if value and choices and value not in choices.group(1).split():
            raise HttpError(400, "one of: " + choices.group(1))
        write_env_key(env_path(cfg), key, value or None)
    return 200, {"ok": True, "key": key, "set": bool(value)}


def post_jobs(cfg, body):
    kind = body.get("kind")
    if kind not in JOB_KINDS:
        raise HttpError(400, "kind must be one of " + ", ".join(JOB_KINDS))
    return 202, start_job(cfg, kind, body.get("file") if kind == "restore" else None)


def get_job(cfg, _body):
    job = current_job(cfg)
    if job is None:
        return 200, {"job": None}
    job["log"] = log_tail(cfg)
    return 200, {"job": job}


ROUTES = {
    ("GET", "/v1/state"): get_state,
    ("POST", "/v1/plugins"): post_plugins,
    ("POST", "/v1/env"): post_env,
    ("POST", "/v1/jobs"): post_jobs,
    ("GET", "/v1/jobs/current"): get_job,
    ("GET", "/v1/containers"): get_containers,
    ("POST", "/v1/hosts/check"): post_hosts_check,
    ("POST", "/v1/hosts/scan"): post_hosts_scan,
    ("POST", "/v1/hosts/forget"): post_hosts_forget,
    ("POST", "/v1/hosts/run"): post_hosts_run,
    ("GET", "/v1/hosts/runs"): get_hosts_runs,
    ("POST", "/v1/hosts/player"): post_hosts_player,
    ("POST", "/v1/tunnels"): post_tunnels,
    ("GET", "/v1/tunnels"): get_tunnels,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "yashome-helper"

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, cfg):
        if not cfg.key:
            raise HttpError(503, "HELPER_KEY is not set")
        got = self.headers.get("X-Helper-Key", "")
        if not hmac.compare_digest(got.encode(), cfg.key.encode()):
            raise HttpError(401, "bad or missing X-Helper-Key")

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "bad Content-Length")
        if length < 0 or length > MAX_BODY:
            raise HttpError(400, f"body larger than {MAX_BODY} bytes")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise HttpError(400, "body is not JSON")
        if not isinstance(body, dict):
            raise HttpError(400, "body must be a JSON object")
        return body

    def _handle(self, method):
        cfg = self.server.cfg
        try:
            self._auth(cfg)
            route = ROUTES.get((method, self.path.split("?", 1)[0]))
            if route is None:
                raise HttpError(404, "not found")
            status, obj = route(cfg, self._body() if method == "POST" else None)
            self._send(status, obj)
        except HttpError as e:
            self._send(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 - keep serving, log the cause
            print(f"helper: {self.command} {self.path} failed: {e!r}", file=sys.stderr)
            self._send(500, {"error": "internal error"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")


def make_server(cfg):
    srv = ThreadingHTTPServer((cfg.bind, cfg.port), Handler)
    srv.daemon_threads = True
    srv.cfg = cfg
    return srv


def main():
    cfg = Config()
    if not cfg.key:
        print("helper: HELPER_KEY is empty, every request gets 503", file=sys.stderr)
    srv = make_server(cfg)
    restore_tunnels(cfg)
    print(f"helper: listening on {cfg.bind}:{cfg.port}, checkout {cfg.dir}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
