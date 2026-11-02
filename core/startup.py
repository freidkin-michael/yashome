# The core: pollers, the clock, startup and shutdown.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

async def _start_pollers():
    global _MAIN_LOOP, _STARTED
    _STARTED = time.time()
    _check_state_requests()
    _MAIN_LOOP = asyncio.get_running_loop()   # worker threads bridge async work here
    _load_status_cache()
    _load_pending()           # before the broker: an early retained ON must not overwrite the file
    _load_missed()
    _migrate_settings_at_start()   # before the broker too: no save may rewrite settings.json before its copy is kept
    _mqtt_start()
    threading.Thread(target=_mqtt_refresh_loop, daemon=True,
                     name="mqtt-refresh").start()
    threading.Thread(target=_status_cache_saver_loop, daemon=True,
                     name="status-cache-saver").start()
    threading.Thread(target=_backup_loop, daemon=True, name="backup").start()
    threading.Thread(target=_liveness_loop, daemon=True, name="liveness").start()
    global _AFTER_BROKER
    _AFTER_BROKER = asyncio.create_task(_after_broker())   # the API is up now; the rest waits for the broker


def _migrate_settings_at_start() -> bool:
    """First boot: materialise settings.json. A loaded plug-in's section still in it moves to the
    plug-in's file; when a copy there disagreed with it, the old settings.json is put aside first."""
    moving = _IN_SETTINGS_FILE & set(_SECTION_FILE)
    if "settings" in _load_failed or (SETTINGS_PATH.exists() and not moving):
        return False
    clash = sorted(set(_SPLIT_CONFLICTS) & moving)
    if moving:                               # settings.json as it was (one copy back for an older core), and both copies of a clash
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for f in {SETTINGS_PATH, *(_SPLIT_CONFLICTS[k] for k in clash)}:
            if f.is_file():
                f.with_name(f"{f.name}.pre-split-{stamp}").write_bytes(f.read_bytes())
        if clash:
            log.warning(f"[settings] {clash} differed between files; the plug-in files won, both copies kept as *.pre-split-{stamp}")
    _save_settings()
    return True


def _server_words(lang: str) -> dict:
    """The backend's own texts in `lang` (i18n.json beside app.py; the dashboard's are in i18n.js)."""
    try:
        return json.loads((Path(__file__).parent / "i18n.json").read_text(encoding="utf-8")).get(lang) or {}
    except (OSError, ValueError):
        return {}


def clock_text(t: time.struct_time, lang: str = None) -> tuple:
    """("HH:MM", "Mon 27 July") in the house language: what the clocks and panels print as they come."""
    words = _server_words(lang or house_lang())
    day, month = _DAYS[(t.tm_wday + 1) % 7], _MONTHS[t.tm_mon - 1]
    return time.strftime("%H:%M", t), f"{words.get(day, day)} {t.tm_mday} {words.get(month, month)}"


_clock_last = {"ts": None, "error": None}
CLOCK_STALE_S = 180


def _clock_loop():
    """home/clock/time and /date, retained, each minute in this host's time zone (TZ). Nothing it
    raises ends it: the health page shows a clock that stopped."""
    while True:
        try:
            cli = _mqtt_client
            if cli is not None and cli.is_connected():
                hm, date = clock_text(time.localtime())
                cli.publish("home/clock/time", hm, retain=True)
                cli.publish("home/clock/date", date, retain=True)
                _clock_last.update(ts=time.time(), error=None)
        except Exception as e:                             # noqa: BLE001
            _clock_last["error"] = f"{type(e).__name__}: {e}"
            log.warning(f"[clock] not sent: {_clock_last['error']}")
        time.sleep(60.5 - time.time() % 60)


async def _run_startup_tasks():
    """Plug-in STARTUP_TASKS, started and left running: a coroutine function as a task (a poller),
    a plain callable in a daemon thread. Nothing waits for them - a poll loop is fine - and
    nothing they raise, SystemExit included, stops the core: a dying one is logged, shown on the health
    page and started again (5 s, doubling up to 5 min); one that returns stays ended."""
    for task in STARTUP_TASKS:
        name = getattr(task, "__qualname__", repr(task))
        if asyncio.iscoroutinefunction(task):
            async def guarded(fn=task, name=name):
                pause = TASK_RESTART_S
                while True:                                # a task that dies starts again, later each time
                    began = time.monotonic()
                    try:
                        await fn()
                        return
                    except asyncio.CancelledError:
                        raise
                    except BaseException as e:             # noqa: BLE001 - SystemExit from a plug-in
                        _TASK_DEATHS[name] = (time.time(), f"{type(e).__name__}: {e}")
                        log.error(f"[plugins] task {name} died: {type(e).__name__}: {e}; again in {pause:.0f} s")
                    if time.monotonic() - began > TASK_HEALTHY_S:
                        pause = TASK_RESTART_S                 # it ran well for long: a fresh start, not the old backoff
                    await asyncio.sleep(pause)
                    pause = min(pause * 2, TASK_RESTART_MAX_S)
            _PLUGIN_TASKS.append(asyncio.create_task(guarded(), name=name))   # held: tasks are kept weakly
            continue

        def run(fn=task, name=name):
            pause = TASK_RESTART_S
            while True:
                began = time.monotonic()
                try:
                    fn()
                    return
                except BaseException as e:                 # noqa: BLE001
                    _TASK_DEATHS[name] = (time.time(), f"{type(e).__name__}: {e}")
                    log.error(f"[plugins] start task {name} failed: {type(e).__name__}: {e}; again in {pause:.0f} s")
                if time.monotonic() - began > TASK_HEALTHY_S:
                    pause = TASK_RESTART_S
                time.sleep(pause)
                pause = min(pause * 2, TASK_RESTART_MAX_S)
        threading.Thread(target=run, daemon=True, name=f"plugin-{name}"[:40]).start()


async def _after_broker():
    # Elapsed deadlines fire through MQTT the moment they are replayed, and a
    # QoS-0 publish before CONNACK is silently dropped, so wait for the link.
    if _paho is not None and not await asyncio.to_thread(_mqtt_ready.wait, 15):
        log.info("[mqtt] no CONNACK after 15s, replaying timers anyway")
    def step(fn, what):
        try:
            fn()
        except Exception:
            log.exception(f"[start] {what} failed; the rest of the start goes on")
    # the scheduler first: whatever breaks below, schedules keep running
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
    threading.Thread(target=_clock_loop, daemon=True, name="clock").start()
    await _run_startup_tasks()                          # plug-ins first: timers may fire on their devices
    step(_replay_pending_on_boot, "timer replay")      # deferred deadlines (inching + rule `for`)
    step(_rearm_inching_on_boot, "inching re-arm")     # channels on at boot without a timer

    # once more when the retained frames have arrived: the cache read at start may be older
    _t = threading.Timer(20, lambda: step(_rearm_inching_on_boot, "inching re-arm"))
    _t.daemon = True
    _t.start()
    threading.Thread(target=_prune_orphan_state_mirror, daemon=True,
                     name="mirror-prune").start()
    def audit_when_known():
        if not _wait_registry(60):                        # every plug-in's device list, as for the mirror prune
            log.info(f"[audit] skipped: no device list from {sorted(REGISTRY_PENDING)} within 60 s")
            return
        step(_audit_orphan_refs, "reference audit")       # loud about silently-dead bindings
    threading.Thread(target=audit_when_known, daemon=True, name="audit").start()


def _shutdown():
    """At a stop: the status cache as it is now (the saver waits for a debounce) and a clean
    disconnect from the broker."""
    try:
        _atomic_json_dump(STATUS_CACHE_PATH, _cache_snapshot(), ensure_ascii=False)
    except Exception as e:                                     # noqa: BLE001
        log.warning(f"[status-cache] save at stop failed: {e}")
    cli = _mqtt_client
    if cli is not None:
        try:
            cli.disconnect()
            cli.loop_stop()
        except Exception as e:                                 # noqa: BLE001
            log.warning(f"[mqtt] disconnect at stop: {e}")


@contextlib.asynccontextmanager
async def _lifespan(_app):
    await _start_pollers()
    yield
    await asyncio.to_thread(_shutdown)


app.router.lifespan_context = _lifespan
