# The core: backups, sign-in codes, health, the update helper.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _load_pluginlib():
    """tools/pluginlib.py: discovery and `requires` shared with mkpasswd.py, check_plugins.py and envlib.sh."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("yashome_pluginlib", Path(__file__).parent / "tools" / "pluginlib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pluginlib = _load_pluginlib()


def _load_hostsock():
    """tools/hostsock.py: the house's helpers on its host, each behind a socket file in /run/yashome (no port, no token)."""
    import importlib.util
    if "yashome_hostsock" not in sys.modules:
        spec = importlib.util.spec_from_file_location("yashome_hostsock", Path(__file__).parent / "tools" / "hostsock.py")
        mod = sys.modules["yashome_hostsock"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return sys.modules["yashome_hostsock"]


hostsock = _load_hostsock()


def _load_wol():
    """tools/wol.py: Wake-on-LAN (the host's relay first, then the broadcast), shared with the webos CLI."""
    import importlib.util
    if "yashome_wol" in sys.modules:                # loaded once: the backend and the plug-ins share it
        return sys.modules["yashome_wol"]
    spec = importlib.util.spec_from_file_location("yashome_wol", Path(__file__).parent / "tools" / "wol.py")
    mod = sys.modules["yashome_wol"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wake_on_lan = _load_wol().wake


# ---- backups (tools/backup.py): one every night, Settings -> Backups, make backup / make restore ----
def _load_backup():
    import importlib.util
    spec = importlib.util.spec_from_file_location("yashome_backup", Path(__file__).parent / "tools" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = _load_backup()
BACKUP_DIR = ROOT / "backups"
BACKUP_AT = os.environ.get("BACKUP_AT") or "03:30"          # empty in .env = the default
_backup_lock = threading.Lock()
_backup_last = {"error": None, "warnings": [], "ts": 0}


def _backup_keep() -> int:
    return backup.keep(os.environ)


def backup_now(light: bool = False) -> dict:
    """A backup now; light (before a merge): the house's settings and data without its build cache and recordings."""
    with _backup_lock:
        try:
            path, warn = backup.make(ROOT, HOUSE, BACKUP_DIR, dict(os.environ), keep=_backup_keep(),
                                     extra=PLUGINS_EXTRA, light=light)
            if not light:                           # a merge's light backup never stands in for the nightly one
                _backup_last.update(error=None, warnings=warn, ts=time.time())
            for w in warn:
                log.warning(f"[backup] {w}")
            log.info(f"[backup] {path.name}")
            return {"ok": True, "name": path.name, "warnings": warn}
        except Exception as e:                      # noqa: BLE001 - a failed backup is reported, never fatal
            err = f"{type(e).__name__}: {e}"
            if not light:                           # the merge that asked reports its own failure
                _backup_last.update(error=err, ts=time.time())
            log.error(f"[backup] failed: {e}")
            return {"ok": False, "error": err}


def backup_next(now: datetime, at: str):
    """When the nightly backup runs next: None for "off" or a bad time."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (at or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    nxt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


def _backup_loop():
    if backup_next(datetime.now(), BACKUP_AT) is None:
        log.warning(f"[backup] no nightly backup: BACKUP_AT={BACKUP_AT!r} (off, or HH:MM)")
        return
    while True:
        now = datetime.now()
        time.sleep(max(60, (backup_next(now, BACKUP_AT) - now).total_seconds()))
        backup_now()


def _house_token_only(request: Request):
    if not is_house_token(request.headers):
        raise HTTPException(403, "only the dashboard does this")


# ---- sign in a new device by a one-time code (Settings -> This screen -> Sign-in -> Another browser): 6 digits, 5 min ----
_signin = {"code": "", "until": 0.0, "tries": 0}
_signin_lock = threading.Lock()


@app.post("/api/signin/start")
def signin_start(request: Request):
    import secrets as _secrets
    _house_token_only(request)
    with _signin_lock:
        _signin.update(code=f"{_secrets.randbelow(10 ** 6):06d}", until=time.time() + 300, tries=0)
        return {"code": _signin["code"], "valid_s": 300}


class SigninBody(Body):
    code: str


@app.post("/api/signin")
def signin(body: SigninBody, request: Request):
    """The code from another signed-in screen, once, within 5 min, 5 tries: answers the house token."""
    with _signin_lock:
        ok = bool(_signin["code"]) and time.time() < _signin["until"] and _signin["tries"] < 5 \
            and hmac.compare_digest(body.code.strip().encode(), _signin["code"].encode())
        def clean(v, n):
            return "".join(c if " " <= c <= "~" else "?" for c in (v or "?")[:n])
        fwd = request.headers.get("x-forwarded-for")
        who = (f"{request.client.host if request.client else '?'}"
               + (f" (forwarded for {clean(fwd, 60)}, unverified)" if fwd else "")
               + f" [{clean(request.headers.get('user-agent'), 80)}]")
        if not ok:
            _signin["tries"] += 1
            if _signin["code"] and _signin["tries"] <= 5:      # a burnt or missing code: no line per request
                log.warning(f"[signin] a wrong code from {who}")
            raise HTTPException(403, "no such code: ask for a new one on a signed-in screen (Settings)")
        _signin.update(code="", until=0.0)
    log.warning(f"[signin] a device signed in by code: {who}")
    return {"token": DASHBOARD_TOKEN}


def health_row(rid: str, state: str, title: str, detail: str = "", fix: str = "", args=(), link: str = "", cmd: str = "") -> dict:
    """One line of the health page: state ok | warn | bad; title, detail and fix are English words the dashboard
    translates ({0}... take args); link: the Settings page that fixes it ("devices/forgotten"); cmd: host commands, shown
    under More, never translated ({0}... take args too)."""
    return {"id": rid, "state": state if state in ("ok", "warn", "bad") else "bad", "title": title,
            "detail": detail, "fix": fix, "args": [str(a) for a in args], "link": link, "cmd": cmd}


def _core_health() -> list:
    rows = []
    cli = _mqtt_client
    rows.append(health_row("broker", "ok", "Broker") if cli is not None and cli.is_connected() else
                health_row("broker", "bad", "Broker", "not connected", "Devices cannot be reached: restart the house", link="restart",
                           cmd="make update; docker compose logs mosquitto"))
    if "settings" in _load_failed:
        rows.append(health_row("settings", "bad", "Settings", "the settings file cannot be read",
                               "Restore the last good backup", link="backups", cmd="make restore FILE=backups/<name>"))
    for pid, err in sorted(_PLUGIN_ERRORS.items()):
        rows.append(health_row(f"plugin:{pid}", "bad", "Plug-in {0}", "did not load: {1}",
                               "The rooms of the house need it: put it back" if pid in pluginlib.REQUIRED else
                               "The Plug-ins page shows why; fix it or switch it off", (pid, err), link="plugins/list",
                               cmd="make update" if pid in pluginlib.REQUIRED else ""))
    tz = os.environ.get("TZ") or "UTC"
    last = _clock_last["ts"]
    if last is not None and time.time() - last < CLOCK_STALE_S:
        rows.append(health_row("clock", "ok", "Clock", "{0}, time zone {1}", "", (time.strftime("%H:%M"), tz)))
    elif last is not None or _clock_last["error"]:
        rows.append(health_row("clock", "bad", "Clock", "not sent for {0} min: {1}",
                               "Restart the house; the house log says why", (round((time.time() - last) / 60) if last else "-", _clock_last["error"] or "-"),
                               cmd="docker compose logs home; docker compose restart home"))
    try:
        free = shutil.disk_usage(HOUSE).free
    except OSError:
        free = None
    if free is not None:
        gb = round(free / 1e9, 1)
        rows.append(health_row("disk", "ok" if free > 1e9 else "warn" if free > 2e8 else "bad", "Disk",
                               "{0} GB free", "" if free > 1e9 else "Free some space: delete old backups or files you do not need", (gb,),
                               link="" if free > 1e9 else "backups", cmd="" if free > 1e9 else "docker image prune"))
    items = [b for b in backup.listing(BACKUP_DIR) if "-light" not in b["name"]]
    age = time.time() - items[0]["ts"] if items else None
    if _backup_last["error"]:
        rows.append(health_row("backup", "bad", "Backup", "the last one failed: {0}",
                               "Back up now shows the error", (_backup_last["error"],), link="backups"))
    elif age is None:
        rows.append(health_row("backup", "warn", "Backup", "none yet", "Back up now", link="backups"))
    else:
        how = ("{0} min ago", max(1, round(age / 60))) if age < 3600 else ("{0} h ago", round(age / 3600)) if age < 86400 \
            else ("{0} days old", int(age // 86400))
        rows.append(health_row("backup", "ok" if age < 2 * 86400 else "warn", "Backup", how[0],
                               "" if age < 2 * 86400 else "Back up now; the nightly backup did not run, its time is on the Backups page", (how[1],),
                               link="" if age < 2 * 86400 else "backups"))
    day = time.time() - 86400
    _missed_sched[:] = [m for m in _missed_sched if m[2] > day]
    if _missed_sched:
        names = ", ".join(f"{BY_ID_NAME.get(d, d)}.{c} {time.strftime('%H:%M', time.localtime(t))}"
                          for d, c, t in _missed_sched[-5:])
        rows.append(health_row("schedules", "warn", "Schedules", "{0} missed in a day, the device away at their time: {1}",
                               "Check that device; the next schedule runs as usual",
                               (len(_missed_sched), names)))
    with _cache_lock:
        gone = sorted(d for d, st in _status_cache.items()
                      if d in DEVICES and _is_offline(d) and time.time() - st["offline_since"] > 600)
    if gone:
        names = ", ".join(BY_ID_NAME.get(d, d) for d in gone[:8]) + (f" +{len(gone) - 8}" if len(gone) > 8 else "")
        rows.append(health_row("devices", "warn", "Devices", "{0} offline for more than 10 min: {1}",
                               "Check their power and network; each shows again once it reports", (len(gone), names), link="devices"))
    for name, (when, why) in sorted(_TASK_DEATHS.items()):
        if time.time() - when < 3600:              # a task that died lately: it is started again by itself
            rows.append(health_row(f"task:{name}", "warn", "Background task {0}", "died {1} min ago: {2}",
                                   "It starts again by itself; nothing to do", (name, int((time.time() - when) // 60), why[:120])))
    return rows


HEALTH_TIMEOUT_S = 5
HEALTH_ROWS_MAX = 50
_health_runs: dict = {}                  # check -> its run: one at a time, a hung one is reported, not restarted
_health_lock = threading.Lock()


def _check_name(fn) -> str:
    mod = getattr(fn, "__module__", "") or ""
    parts = mod.split(".")
    return parts[1] if parts[0] == _PLUGIN_PKG and len(parts) > 1 else "core" if fn is _core_health else mod or "?"


def _run_check(fn) -> dict:
    """The check's run: a new daemon thread unless one is still going (a hung check holds one thread,
    not one per report, and never the process's exit)."""
    with _health_lock:
        run = _health_runs.get(fn)
        if run is not None and run["done"].is_set() and not run["seen"]:
            run["seen"] = True                    # a slow check's answer, finished after the last report
            return run
        if run is None or not run["thread"].is_alive():
            run = {"done": threading.Event(), "rows": None, "error": None, "started": time.monotonic(), "seen": True}

            def work(r=run):
                try:
                    r["rows"] = fn()
                except Exception as e:            # noqa: BLE001  reported as a row
                    r["error"] = f"{type(e).__name__}: {e}"[:200]
                finally:
                    r["done"].set()
            run["thread"] = threading.Thread(target=work, daemon=True, name=f"health-{_check_name(fn)}")
            _health_runs[fn] = run
            run["thread"].start()
        return run


def _rows_of(name: str, got) -> list:
    """A check's answer as proper rows: anything else is a row saying so, never a broken page."""
    if not isinstance(got, list):
        return [health_row(f"check:{name}", "bad", "Check {0}", "gave no list of rows", "", (name,))]
    out = []
    for r in got:
        if len(out) >= HEALTH_ROWS_MAX:
            out.append(health_row(f"check:{name}", "warn", "Check {0}", "{1} more rows not shown", "",
                                  (name, len(got) - HEALTH_ROWS_MAX)))
            break
        if isinstance(r, dict) and r.get("title") and r.get("state") in ("ok", "warn", "bad") \
                and isinstance(r.get("args", []), (list, tuple)):
            out.append(health_row(str(r.get("id") or name), r["state"], str(r["title"]), str(r.get("detail") or ""),
                                  str(r.get("fix") or ""), [str(a) for a in r.get("args") or []], str(r.get("link") or ""), str(r.get("cmd") or "")))
        else:
            out.append(health_row(f"check:{name}", "bad", "Check {0}", "gave a row the page cannot show", "", (name,)))
    return out


def health_report() -> dict:
    """Every check, the core's and the plug-ins', within one time budget, worst first."""
    checks = [_core_health] + list(HEALTH_CHECKS)
    runs = [(fn, _run_check(fn)) for fn in checks]
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    rows = []
    for fn, run in runs:
        name = _check_name(fn)
        late = time.monotonic() - run["started"] > HEALTH_TIMEOUT_S      # hung before this report: no waiting
        if not run["done"].wait(0 if late else max(0.0, deadline - time.monotonic())):
            run["seen"] = False
            secs = int(time.monotonic() - run["started"])
            rows.append(health_row(f"check:{name}", "bad", "Check {0}", "did not answer for {1} s",
                                   "The backend log shows what it waits for", (name, secs), cmd="docker compose logs home"))
        elif run["error"]:
            rows.append(health_row(f"check:{name}", "bad", "Check {0}", "failed: {1}",
                                   "The backend log shows the details", (name, run["error"]), cmd="docker compose logs home"))
        else:
            rows.extend(_rows_of(name, run["rows"]))
    order = {"bad": 0, "warn": 1, "ok": 2}
    rows.sort(key=lambda r: order.get(r["state"], 0))
    return {"rows": rows, "worst": rows[0]["state"] if rows else "ok", "ts": time.time()}


@app.get("/api/health/report")
async def health_report_api(request: Request):
    _house_token_only(request)
    return await run_in_threadpool(health_report)


HELPER_URL = (os.environ.get("HELPER_URL") or "http://helper:8796").rstrip("/")


def _helper(method: str, path: str, body=None, timeout: float = 15):
    """One call to the helper (helper/helper.py): plug-ins, their keys, apply, update, roll back."""
    key = os.environ.get("HELPER_KEY", "")
    if not key:
        raise HTTPException(503, "no helper: run make update once on the host")
    req = urllib.request.Request(HELPER_URL + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Helper-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error") or f"HTTP {e.code}"
        except ValueError:
            why = f"HTTP {e.code}"
        raise HTTPException(e.code if e.code in (400, 404, 409) else 502, f"helper: {why}")
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"the helper does not answer ({type(e).__name__}): make update on the host")


class HelperPluginsBody(Body):
    on: list


class HelperEnvBody(Body):
    key: str
    value: str = ""


class HelperJobBody(Body):
    kind: str
    file: str = ""


@app.get("/api/helper/state")
async def helper_state(request: Request):
    _house_token_only(request)
    return await run_in_threadpool(_helper, "GET", "/v1/state", None, 40)


@app.post("/api/helper/plugins")
async def helper_plugins(body: HelperPluginsBody, request: Request):
    _house_token_only(request)
    return await run_in_threadpool(_helper, "POST", "/v1/plugins", {"on": body.on})


@app.post("/api/helper/env")
async def helper_env(body: HelperEnvBody, request: Request):
    _house_token_only(request)
    return await run_in_threadpool(_helper, "POST", "/v1/env", {"key": body.key, "value": body.value})


@app.post("/api/helper/jobs")
async def helper_jobs(body: HelperJobBody, request: Request):
    _house_token_only(request)
    if body.kind not in ("apply", "update", "rollback", "restore", "restart"):
        raise HTTPException(400, "kind: apply, update, rollback, restore or restart")
    if body.kind == "restore" and not _backup_name_ok(body.file):
        raise HTTPException(404, "no such backup")
    return await run_in_threadpool(_helper, "POST", "/v1/jobs", {"kind": body.kind, "file": body.file})


@app.get("/api/helper/jobs/current")
async def helper_job(request: Request):
    _house_token_only(request)
    return await run_in_threadpool(_helper, "GET", "/v1/jobs/current")


@app.get("/api/backups")
def backups_list(request: Request):
    _house_token_only(request)
    items = backup.listing(BACKUP_DIR)
    return {"items": items, "age_s": int(time.time() - items[0]["ts"]) if items else None,
            "error": _backup_last["error"], "warnings": _backup_last["warnings"], "at": BACKUP_AT,
            "nightly": backup_next(datetime.now(), BACKUP_AT) is not None, "keep": _backup_keep()}


@app.post("/api/backups")
async def backups_make(request: Request):
    _house_token_only(request)
    return await run_in_threadpool(backup_now)


def _backup_name_ok(name: str) -> bool:
    return bool(re.fullmatch(r"yashome-backup-\d{8}-\d{6}(-light)?(-\d{1,3})?\.tar\.gz", name)) and (BACKUP_DIR / name).is_file()


BACKUP_UPLOAD_MAX = 512 * 1024 * 1024


@app.post("/api/backups/upload")
async def backups_upload(request: Request):
    """A backup file from another computer (the raw body): kept as a new archive, then restorable from the list."""
    _house_token_only(request)
    if int(request.headers.get("content-length") or 0) > BACKUP_UPLOAD_MAX:
        raise HTTPException(413, "larger than 512 MB")
    data = await request.body()
    return await run_in_threadpool(_backup_store, data)


def _backup_store(data: bytes) -> dict:
    import io
    import tarfile
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            top = {m.name.split("/")[0] for m in t.getmembers()}
    except (tarfile.TarError, OSError, EOFError):
        raise HTTPException(400, "not a readable backup")
    if "yashome-backup.json" not in top:
        raise HTTPException(400, "not a Yashome backup (no yashome-backup.json)")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    path = BACKUP_DIR / f"{backup.PREFIX}{stamp}.tar.gz"
    while path.exists():
        n += 1
        path = BACKUP_DIR / f"{backup.PREFIX}{stamp}-{n}.tar.gz"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return {"ok": True, "name": path.name}


@app.get("/api/backups/{name}")
def backups_get(name: str, request: Request):
    _house_token_only(request)
    if not _backup_name_ok(name):
        raise HTTPException(404, "no such backup")
    return FileResponse(BACKUP_DIR / name, media_type="application/gzip", filename=name,
                        headers={"Cache-Control": "no-store"})
_plugin_roots, _plugin_requires = pluginlib.roots, pluginlib.read_requires
_plugin_reqs, _plugin_closure = pluginlib.requirements, pluginlib.closure
