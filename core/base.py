# The core: logging, paths, the settings file and its sections.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")


class _RedactToken(logging.Filter):
    """uvicorn's access log prints the full URL: /ws?token=... and /?token=... would put the
    house key into `docker compose logs` (and into any issue a log gets pasted into)."""
    _re = re.compile(r"(token=)[^&\s\"']+")

    def filter(self, record):
        if record.args:
            record.args = tuple(self._re.sub(r"\1***", a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._re.sub(r"\1***", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactToken())
log = logging.getLogger("home")

# HOME_STACK_ROOT lets tests point at a fixture tree instead of the live one.
ROOT = Path(os.environ.get("HOME_STACK_ROOT") or Path(__file__).parent)
CHECKOUT = Path(__file__).resolve().parent       # the core's own files (tools/), whatever ROOT is
# The house's own files (devices, settings, plugin-data/, runtime state): /house in the container
# when HOUSE_DIR in .env names a directory of their own; unset, beside the code as always.
HOUSE = Path(os.environ.get("HOUSE_ROOT") or ROOT)
MQTT_DEVICES_PATH = HOUSE / "mqtt_devices.json"  # generic MQTT devices (ESPHome, Tasmota, own firmware)
STATUS_CACHE_PATH = HOUSE / "status_cache.json"
SETTINGS_PATH = HOUSE / "settings.json"


# -- Unified config: one settings.json holds names/bindings/favorites/automations.
# In-memory vars are authoritative; _save_settings() atomically dumps them all
# under one re-entrant lock (no read-modify-write of the file -> no lost updates).
# Per-section locks alias this same lock.
_settings_lock = threading.RLock()
_cfg: dict = {}
# Files that failed to parse at boot. While a name is in here the matching
# save is refused: writing the in-memory defaults back would turn one corrupt
# read into the loss of every binding and favourite.
_load_failed: set = set()
_SETTINGS_BAD = ""        # which file stopped the saves and why: the text of every refusal
if SETTINGS_PATH.exists():
    try:
        _cfg = json.load(open(SETTINGS_PATH))
        if not isinstance(_cfg, dict):
            raise ValueError(f"top level is {type(_cfg).__name__}, not an object")
    except Exception as _e:
        _cfg = {}
        _load_failed.add("settings")
        _SETTINGS_BAD = f"settings.json is unreadable ({str(_e)[:80]})"
        log.error(f"[settings] {SETTINGS_PATH} unreadable ({_e}); saves DISABLED until fixed")
def _same_dir(a: Path, b: Path) -> bool:
    """One directory under two names (the container mounts the checkout at /app and, without HOUSE_DIR, at /house)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


if (not SETTINGS_PATH.exists() and not _same_dir(HOUSE, ROOT)
        and ((ROOT / "settings.json").exists() or (ROOT / "mqtt_devices.json").exists())):
    _load_failed.add("settings")         # HOUSE_DIR names an empty directory while the checkout holds the house
    _SETTINGS_BAD = "HOUSE_DIR is empty but the checkout still holds the house (make move-house moves it)"
    log.error(f"[settings] {_SETTINGS_BAD}; saves DISABLED until fixed")
_CORE_SECTIONS = {"_version", "names", "channel_names", "channel_dimrange", "bindings", "favorites", "rooms",
                  "tab_order", "tile_sizes", "room_sensors", "serves", "automations", "rules_disabled", "house", "aliases"}
_IN_SETTINGS_FILE = set(_cfg)

# A plug-in's sections live in its own plugin-data/<name>/settings.json: merged into _cfg here,
# before any plug-in reads them at import; every save writes each back to its owner's file.
PLUGIN_DATA = HOUSE / "plugin-data"
PLUGIN_SETTINGS = "settings.json"
_SECTION_FILE: dict = {}
_SECTION_MOVED: dict = {}        # former owner -> sections another plug-in took over (dropped from its file at the next save)
_SPLIT_CONFLICTS: dict = {}     # section -> the plug-in file it differed from settings.json in (both kept aside)


for _f in sorted(PLUGIN_DATA.glob("*/" + PLUGIN_SETTINGS)) if PLUGIN_DATA.is_dir() else ():
    if not re.match(r"^[a-z][a-z0-9_]{0,31}$", _f.parent.name):
        continue
    try:
        with open(_f, encoding="utf-8") as _fh:
            _sec = json.load(_fh)
        if not isinstance(_sec, dict):
            raise ValueError(f"top level is {type(_sec).__name__}, not an object")
    except Exception as _e:
        _load_failed.add("settings")
        _SETTINGS_BAD = f"plugin-data/{_f.parent.name}/settings.json is unreadable ({str(_e)[:80]})"
        log.error(f"[settings] {_f} unreadable ({_e}); saves DISABLED until fixed")
        continue
    for _k, _v in _sec.items():
        if _k in _CORE_SECTIONS:
            log.warning(f"[settings] {_f}: section {_k!r} belongs to the core, ignored")
        elif _k in _SECTION_FILE:
            _load_failed.add("settings")
            _SETTINGS_BAD = (f"section {_k!r} is in plugin-data/{_SECTION_FILE[_k]}/settings.json and in "
                             f"plugin-data/{_f.parent.name}/settings.json: keep it in one")
            log.error(f"[settings] {_SETTINGS_BAD}; saves DISABLED until fixed")
        else:
            _SECTION_FILE[_k] = _f.parent.name
            if _k in _IN_SETTINGS_FILE and _cfg[_k] != _v:      # e.g. an older core saved its own copy
                _SPLIT_CONFLICTS[_k] = _f
                log.warning(f"[settings] section {_k!r}: {_f} wins over a different copy in settings.json "
                            "(both are kept aside at the start)")
            _cfg[_k] = _v


class Body(BaseModel):
    """What a request body is built on: no string in it is longer than 4096 characters unless its field says more
    (Field(max_length=...)), so a 3 MB name never reaches settings.json. Plug-ins use core.Body too."""
    model_config = ConfigDict(str_max_length=4096)
