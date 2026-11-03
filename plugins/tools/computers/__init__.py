"""computers: the house's computers on its network, in Settings -> Devices -> Computers. A computer is a machine the house's
key (YASHOME_SSH_DIR/id, the helper's) logs in to as COMPUTERS_SSH_USER and where sudo asks no password: the helper checks
that (POST /v1/hosts/check, a fixed probe) and finds them (POST /v1/hosts/scan: the house's /24, port 22). This plug-in never
holds the key: it asks the helper.

The list is plugin-data/computers/computers.json {"computers": {id: {name, host, user, mac, hostname, os, tv, input}}}.
tv / input: the TV and its input the computer's screen is; the probe reads the HDMI port from the screen's EDID, and the
page offers the TV of that maker with that input. Every listed computer is checked every CHECK_S; a computer found by a scan
at another address (same host name) moves there. Owner only (the house token)."""
import asyncio
import ipaddress
import json
import os
import re
import sys
import threading
import time

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

import app as core

PLUGIN = {"name": "Computers", "version": "1.0.0", "icon": "screen"}

DATA = core.plugin_data("computers") / "computers.json"
CHECK_S = 300                      # every listed computer checked this often
SCAN_TIMEOUT = 90
USER = (os.environ.get("COMPUTERS_SSH_USER") or "").strip()
SELF = (os.environ.get("BIND_ADDRESS") or "").strip()     # the house's own address: that computer is the house
TV_MAKERS = {"SAM": "tizen", "GSM": "webos"}             # EDID maker -> the transport of its TVs (Samsung, LG)
PORT = (os.environ.get("DASHBOARD_PORT") or "8765").strip()
def _house_url() -> str:
    """The address a computer's role calls the house at; refused without BIND_ADDRESS, as voice does."""
    if not SELF:
        raise HTTPException(503, "BIND_ADDRESS is not set: the computer would not know the house's address")
    return f"http://{SELF}:{PORT}"


# The roles a computer can take: installed from its card by the helper (helper ROLE_SCRIPTS), picked on the role's own page.
ROLES = {"build": {"title": "Build server", "plugin": "build"},
         "apk": {"title": "Android app builder", "plugin": "build"},       # beside a build server: its jobs pass through it
         "tts": {"title": "Speech (TTS)", "plugin": "voice"},
         "llm": {"title": "Big model (LLM)", "plugin": "voice"},
         "yashgen": {"title": "yashGen firmware builder", "plugin": "fwgen"},
         "player": {"title": "Player (mpv)", "plugin": "player"},      # one on every computer at a TV: no Use
         "touchmouse": {"title": "Touchmouse receiver", "plugin": "touchmouse"}}
MANY = ("player", "touchmouse")    # on every computer that wants one: nothing to pick (Servers lists where they are)
TTS_PORT = 8092
LLM_TARGET = core.plugin_data("computers") / "llm.json"   # the computer of the big model: the host's tunnel follows this file (host/)
YASHGEN_TARGET = core.plugin_data("computers") / "yashgen.json"   # the computer yashGen runs on: likewise (host/yashgen-tunnel.sh)

DISCOVERED = core.plugin_state("computers") / "discovered.json"   # discover.py: who answered the house's broadcast
DISCOVERED_STALE_S = 600           # discover.py rewrites it every 30 s: older means it stopped


def discovered() -> list:
    """The computers that answered the house's one broadcast ("? yashome 1", UDP 7708), each with the servers installed
    on it: [{host, name, servers, ports, seen}]. The plug-ins of those servers read it (touchmouse: its receivers)."""
    try:
        d = json.loads(DISCOVERED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(d, dict) or time.time() - float(d.get("updated") or 0) > DISCOVERED_STALE_S:
        return []
    return [h for h in d.get("hosts") or [] if isinstance(h, dict) and h.get("host") and isinstance(h.get("servers"), list)]


def by_host() -> dict:
    """host -> {id, name, tv (its TV's name)} of the listed computers."""
    tvs = {d["id"]: d.get("name") or d["id"] for d in list(core.DEVICES.values()) if d.get("category") == "tv"}
    return {c["host"]: {"id": cid, "name": c.get("name") or cid, "tv": tvs.get(c.get("tv"))} for cid, c in _load().items()}


_lock = threading.Lock()
_status: dict = {}                 # id -> the last check {ok, ssh, sudo, error, screens, ts}
_found: list = []                  # the last scan's computers not on the list (ok or not, with why)
_scan = {"ts": None, "net": None, "running": False, "error": None}


def _load() -> dict:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
        cs = raw.get("computers") if isinstance(raw, dict) else None
        return {str(k): v for k, v in cs.items() if isinstance(v, dict) and v.get("host")} if isinstance(cs, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        core.log.error(f"[computers] plugin-data/computers/{DATA.name} unreadable ({e}): the list is empty until it is fixed")
        return {}


_computers = _load()
for _c in _computers.values():         # the key of a TTS service of before (it was reached by its address): not used any more
    _c.pop("keys", None)


def _save() -> None:
    core.atomic_json_dump(DATA, {"computers": _computers}, indent=1, ensure_ascii=False)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "pc"
    cid, n = base, 2
    while cid in _computers:
        cid, n = f"{base}-{n}", n + 1
    return cid


def _tvs() -> list:
    return [{"id": d["id"], "name": d.get("name") or d["id"], "transport": d.get("transport"),
             "inputs": next((c.get("range") or [] for c in d.get("controls") or [] if c.get("code") == "input"), [])}
            for d in list(core.DEVICES.values()) if d.get("category") == "tv"]


def _input_for(tv: dict, port: int):
    """The TV's input that is HDMI <port>: its label says so ("HDMI 3"); a renamed one ("PC") is not guessed."""
    pat = re.compile(rf"^hdmi\s*{port}$", re.I)
    return next((i for i in tv["inputs"] if pat.match(str(i).strip())), None)


def _guess(screens: list) -> dict:
    """The TV and input a probe's screens point to: the one TV of that maker; nothing when it is not clear."""
    for sc in screens or []:
        kind = TV_MAKERS.get(sc.get("vendor"))
        if not kind or not sc.get("port"):
            continue
        tvs = [t for t in _tvs() if t["transport"] == kind]
        if len(tvs) == 1:
            return {"tv": tvs[0]["id"], "input": _input_for(tvs[0], sc["port"]), "port": sc["port"]}
        return {"tv": None, "input": None, "port": sc["port"]}
    return {}


def _view() -> dict:
    tvs = {t["id"]: t for t in _tvs()}
    with _lock:
        out = []
        for cid, c in sorted(_computers.items(), key=lambda kv: (kv[1].get("host") != SELF, kv[1].get("name", "").lower())):
            st = _status.get(cid) or {}
            out.append({"id": cid, **c, "self": c.get("host") == SELF, "status": st, "guess": _guess(st.get("screens")),
                        "tv_name": (tvs.get(c.get("tv")) or {}).get("name"), "roles": _roles(c, st), "run": _runs.get(c["host"])})
        return {"user": USER, "computers": out, "found": list(_found), "scan": dict(_scan), "tvs": list(tvs.values()), "tunnels": dict(_tunnels),
                "net": _net(), "roles": {k: {"title": v["title"], "on": _mod(v["plugin"]) is not None} for k, v in ROLES.items()}}


def _mod(plugin: str):
    return sys.modules.get(f"yashome_plugins.{plugin}")


def _build_active():
    """The computer whose build server the house uses (None: none)."""
    b = _mod("build")
    return (b.in_use() or None) if b else None


def _roles(c: dict, st: dict) -> dict:
    """role -> {installed, active} on this computer (the house's own: its TTS, Ollama and yashGen are its own services)."""
    out = {}
    host = _build_active()
    v = _mod("voice")
    tts = v.tts_in_use() if v else ""
    if c.get("host") == SELF:
        out["tts"] = {"installed": "voice" in (os.environ.get("COMPOSE_PROFILES") or "").split(","), "active": not tts, "local": True}
    else:
        out["tts"] = {"installed": "tts" in (st.get("roles") or []), "active": tts == c.get("host")}
    llm = _target(LLM_TARGET).get("host", "")
    if c.get("host") == SELF:                    # its own Ollama is the small model, always there; Use = no big one
        out["llm"] = {"installed": "llm" in (st.get("roles") or []), "active": not llm, "local": True,
                      "models": (st.get("llm") or {}).get("models") or []}
    else:
        out["llm"] = {"installed": "llm" in (st.get("roles") or []), "active": llm == c.get("host"),
                      "models": (st.get("llm") or {}).get("models") or []}
    gen = _target(YASHGEN_TARGET).get("host", "")
    if c.get("host") == SELF:                    # the builder's own instance on this host (YASHGEN_SRC: the host piece runs it)
        out["yashgen"] = {"installed": bool((os.environ.get("YASHGEN_SRC") or "").strip()), "active": not gen, "local": True}
    else:
        out["yashgen"] = {"installed": "yashgen" in (st.get("roles") or []), "active": gen == c.get("host")}
    roles = st.get("roles") or []                # the house's own computer too: installed over ssh like any other
    out["build"] = {"installed": "build" in roles, "active": host == c.get("host")}
    out["apk"] = {"installed": "apk" in roles, "active": host == c.get("host"), "needs": "build"}
    for k in MANY:
        out[k] = {"installed": k in roles, "active": False, "many": True}
    return out


def with_role(role: str) -> list:
    """The listed computers whose last check found this role installed: {id, name, host, user, wsl, tv, input}."""
    with _lock:
        return [{"id": cid, "name": c.get("name") or cid, "host": c["host"], "user": c.get("user") or USER, "wsl": c.get("wsl") or "",
                 "tv": c.get("tv"), "input": c.get("input")}
                for cid, c in _computers.items() if role in ((_status.get(cid) or {}).get("roles") or [])]


def _target(path) -> dict:
    """llm.json / yashgen.json: the computer the host piece serves that role from ({} = this one)."""
    try:
        t = json.loads(path.read_text(encoding="utf-8") or "{}")
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def _use_llm(c: dict | None, st: dict | None = None) -> None:
    """The big model's computer, for the host's tunnel and health (host/install.sh); None = none (the file emptied: it waits)."""
    if c is None:
        LLM_TARGET.write_text("", encoding="utf-8")
    else:
        core.atomic_json_dump(LLM_TARGET, {"host": c["host"], "user": c.get("user") or USER, "wsl": c.get("wsl") or "",
                                           "serve": ((st or {}).get("llm") or {}).get("serve") or ""})
    core.log.info(f"[computers] big model: {c['name'] if c else 'none (this computer only)'}")


def _use_yashgen(c: dict | None) -> None:
    """yashGen's computer, for the host's yashgen-tunnel.sh; None = this one (the file emptied: its own builder runs)."""
    if c is None:
        YASHGEN_TARGET.write_text("", encoding="utf-8")
    else:
        core.atomic_json_dump(YASHGEN_TARGET, {"host": c["host"], "user": c.get("user") or USER})
    core.log.info(f"[computers] yashGen: {c['name'] if c else 'this computer'}")


_runs: dict = {}                   # host -> the helper's last run there (a role installed or removed)


def _refresh_runs() -> None:
    """The helper's runs; a run that ended since the last look has its computer checked again (its roles changed)."""
    try:
        runs = core.helper("GET", "/v1/hosts/runs", None, 5).get("runs") or {}
    except HTTPException:
        return
    ended = [h for h, r in runs.items() if r.get("state") != "running" and (_runs.get(h) or {}).get("state") == "running"]
    _runs.clear()
    _runs.update(runs)
    if ended:
        ids = [cid for cid, c in _computers.items() if c["host"] in ended]
        threading.Thread(target=_check, args=(ids,), daemon=True).start()


def _net():
    try:
        ip = ipaddress.ip_address(SELF)
    except ValueError:
        return None
    return str(ipaddress.ip_network(f"{ip}/24", strict=False)) if ip.version == 4 and ip.is_private else None


def _need_user() -> str:
    if not USER:
        raise HTTPException(503, "COMPUTERS_SSH_USER is not set: the user the house logs in to its computers as")
    return USER


_tunnels: dict = {}                # role -> the helper's tunnel {host, state, since, error}: the build and TTS servers of a computer


def _tunnel(role: str, c: dict | None) -> None:
    """The helper's ssh tunnel to the server of this role on that computer (None: no tunnel, the house's own)."""
    body = {"role": role, "host": c["host"], "user": c.get("user") or USER, "wsl": c.get("wsl") or ""} if c else {"role": role}
    res = core.helper("POST", "/v1/tunnels", body, 15)
    _tunnels.clear()
    _tunnels.update(res.get("tunnels") or {})


def _sync_tunnels() -> None:
    """The helper's tunnels follow the servers in use (after a helper restart it has its own copy; this is the check)."""
    b, v = _mod("build"), _mod("voice")
    want = {"build": b.in_use() if b else "", "tts": v.tts_in_use() if v else ""}
    res = core.helper("GET", "/v1/tunnels", None, 5).get("tunnels") or {}
    for role, host in want.items():
        if (res.get(role) or {}).get("host", "") != host:
            _tunnel(role, next((c for c in _computers.values() if c["host"] == host), None) if host else None)
            res = dict(_tunnels)
    _tunnels.clear()
    _tunnels.update(res)


def _follow_move(old: str, c: dict, st: dict | None) -> None:
    """A computer in use got a new address: its servers in use point there too (they named the old one)."""
    b, v = _mod("build"), _mod("voice")
    if b and b.in_use() == old:
        _tunnel("build", c)
        b.use_remote(c["host"])
    if v and v.tts_in_use() == old:
        _tunnel("tts", c)
        v.use_tts(c["host"], f"http://helper:{TTS_PORT}")
    if _target(LLM_TARGET).get("host") == old:
        _use_llm(c, st)
    if _target(YASHGEN_TARGET).get("host") == old:
        _use_yashgen(c)


def _check(ids: list) -> None:
    """The helper checks these computers; their status is kept (an unreachable one keeps its last screens)."""
    with _lock:
        todo = [(cid, _computers[cid]) for cid in ids if cid in _computers]
    if not todo:
        return
    res = core.helper("POST", "/v1/hosts/check", {"hosts": [{"host": c["host"], "user": c.get("user") or USER, "wsl": c.get("wsl") or ""}
                                                         for _, c in todo]}, 30)
    now = time.time()
    with _lock:
        for (cid, c), r in zip(todo, res.get("hosts") or []):
            old = _status.get(cid) or {}
            st = {k: r.get(k) for k in ("ok", "ssh", "sudo", "error")}
            st.update({"ts": now, "screens": r.get("screens") if r.get("ssh") else old.get("screens"),
                       "roles": r.get("roles") if r.get("ssh") else old.get("roles"), "llm": r.get("llm") if r.get("ssh") else old.get("llm"),
                       "seen": now if r.get("ssh") else old.get("seen")})
            _status[cid] = st
            if r.get("ssh"):
                for k in ("hostname", "os", "mac"):
                    if r.get(k):
                        c[k] = r[k]
        _save()


async def _loop():
    await asyncio.sleep(30)
    while True:
        try:
            if USER and _computers:
                await asyncio.to_thread(_check, list(_computers))
                await asyncio.to_thread(_sync_tunnels)
        except Exception as e:  # noqa: BLE001 - the helper may be down: try again next time
            core.log.warning(f"[computers] check failed: {getattr(e, 'detail', e)}")
        await asyncio.sleep(CHECK_S)


core.STARTUP_TASKS.append(_loop)


class AddBody(core.Body):
    host: str
    name: str = ""
    user: str = ""
    wsl: str = ""                  # a Windows computer: <distribution>/<user> of its WSL (the checks and roles run there)


class EditBody(core.Body):
    name: str | None = None
    tv: str | None = None
    input: str | None = None


def _name(name: str) -> str:
    name = " ".join(str(name).split())
    if not name or len(name) > 40:
        raise HTTPException(400, "a name of 1-40 characters")
    return name


@core.app.get("/api/computers")
def api_list(request: Request):
    core.house_token_only(request)
    _refresh_runs()
    return _view()


class RoleBody(core.Body):
    action: str                    # install (also: use it) | remove | use


@core.app.post("/api/computers/{cid}/roles/{role}")
def api_role(cid: str, role: str, body: RoleBody, request: Request):
    """A server on a computer: install puts it there (the helper runs its script over ssh), remove takes it off (not the one
    in use), use makes it the house's - one computer per server. The build server and TTS listen on that computer's loopback
    only: use points the helper's ssh tunnel there, so they need no key. The house's own computer: use = its own again."""
    core.house_token_only(request)
    if role not in ROLES or body.action not in ("install", "remove", "use"):
        raise HTTPException(400, "role: " + ", ".join(ROLES) + "; action: install, remove or use")
    b = _mod(ROLES[role]["plugin"])
    if b is None:
        raise HTTPException(409, f"the plug-in {ROLES[role]['plugin']} is off")
    with _lock:
        c = _computers.get(cid)
        st = _status.get(cid) or {}
    if c is None:
        raise HTTPException(404, "no such computer")
    r = _roles(c, st)[role]
    if role in MANY:                     # any Linux computer, the house's own too; each one serves itself
        if body.action == "use":
            raise HTTPException(400, "every computer with it serves itself: there is nothing to pick")
        if c.get("wsl"):
            raise HTTPException(409, "it runs on a Linux computer, not in a WSL")
        res = core.helper("POST", "/v1/hosts/run", {"host": c["host"], "user": c.get("user") or USER, "role": role, "action": body.action,
                                                      "args": {"house": _house_url()} if body.action == "install" else {}}, 15)
        _runs[c["host"]] = res.get("run") or {}
        core.log.info(f"[computers] {role} {body.action} on {c['name']} ({c['host']})")
        return _view()
    if c.get("host") == SELF and role not in ("build", "apk"):     # the house's own computer: a build server like any other's
        if body.action != "use" or not r["installed"]:
            raise HTTPException(409, "on this computer these are the house's own services (their keys: Settings -> their page -> Keys)")
        if role == "tts":
            _tunnel("tts", None)
            b.use_tts("")
        elif role == "llm":
            _use_llm(None)
        elif role == "yashgen":
            _use_yashgen(None)
        else:
            _tunnel("build", None)
            b.use_remote("")
        return _view()
    if role == "llm":
        if body.action == "use":
            if not r["installed"]:
                raise HTTPException(409, "there is no Ollama there: install it first")
            _use_llm(c, st)
            return _view()
        if body.action == "remove" and r["active"]:
            raise HTTPException(409, "the house's big model runs there now: use another computer (or none) first")
        args = {}
        if body.action == "install":
            conf = (getattr(b, "_cfg", {}) or {}).get("config") or {}
            args = {"model": conf.get("big_model") or "qwen3:14b"}
    elif role == "yashgen":
        if c.get("wsl"):
            raise HTTPException(409, "yashGen runs on a Linux computer (not in a WSL)")
        if body.action == "use":
            if not r["installed"]:
                raise HTTPException(409, "it is not installed there")
            _use_yashgen(c)
            return _view()
        if body.action == "remove" and r["active"]:
            raise HTTPException(409, "yashGen runs there now: use another computer (or this one) first")
        args = {}
    elif role == "tts":
        if body.action == "use":
            if not r["installed"]:
                raise HTTPException(409, "it is not installed there")
            _tunnel("tts", c)
            b.use_tts(c["host"], f"http://helper:{TTS_PORT}")
            return _view()
        if body.action == "remove" and r["active"]:
            raise HTTPException(409, "the house speaks with it now: use another TTS service first")
        args = {}
        if body.action == "install":
            args = {"house": _house_url()}
    elif role == "apk":                  # the app builder lives beside the build server: the build server's installer, run again
        if body.action == "use":
            raise HTTPException(400, "the app builder is used with its build server: use that one")
        if not _roles(c, st)["build"]["installed"]:     # its installer, run again: the build server is there already
            raise HTTPException(409, "the app builder goes beside a build server: install that first")
        args = {"house": _house_url()}
    elif body.action == "remove":
        if r["active"]:
            raise HTTPException(409, "the house builds there now: pick another build server first")
        args = {}
    elif body.action == "use":
        if not r["installed"]:
            raise HTTPException(409, "it is not installed there")
        _tunnel("build", c)
        b.use_remote(c["host"])
        return _view()
    else:
        args = {"house": _house_url()}
    res = core.helper("POST", "/v1/hosts/run", {"host": c["host"], "user": c.get("user") or USER, "wsl": c.get("wsl") or "", "role": role,
                                                  "action": "remove" if body.action == "remove" else "install", "args": args}, 15)
    _runs[c["host"]] = res.get("run") or {}
    core.log.info(f"[computers] {role} {body.action} on {c['name']} ({c['host']})")
    return _view()


@core.app.post("/api/computers")
def api_add(body: AddBody, request: Request):
    """On the list only when it is a computer of the house: the key logs in and sudo asks no password."""
    core.house_token_only(request)
    user = (body.user or "").strip() or _need_user()
    host, wsl = body.host.strip(), body.wsl.strip()
    with _lock:
        if any(c["host"] == host for c in _computers.values()):
            raise HTTPException(409, "this address is on the list already")
    r = (core.helper("POST", "/v1/hosts/check", {"hosts": [{"host": host, "user": user, "wsl": wsl}]}, 30).get("hosts") or [{}])[0]
    if not r.get("ok"):
        raise HTTPException(409, f"not a computer of the house: {r.get('error') or 'no answer'}")
    with _lock:
        if r.get("hostname") and any(c.get("hostname") == r["hostname"] for c in _computers.values()):
            raise HTTPException(409, f"{r['hostname']} is on the list already (at another address)")
        cid = _slug(r.get("hostname") or host)
        c = {"name": _name(body.name or r.get("hostname") or host), "host": host, "user": user,
             **({"wsl": wsl} if wsl else {}), **{k: r.get(k) for k in ("hostname", "os", "mac") if r.get(k)}}
        g = _guess(r.get("screens"))
        if g.get("tv"):
            c["tv"], c["input"] = g["tv"], g.get("input")
        _computers[cid] = c
        _status[cid] = {"ok": True, "ssh": True, "sudo": True, "error": None, "ts": time.time(), "seen": time.time(),
                        "screens": r.get("screens"), "roles": r.get("roles"), "llm": r.get("llm")}
        _found[:] = [f for f in _found if f.get("hostname") != r.get("hostname")]
        _save()
    core.log.info(f"[computers] {c['name']} ({host}) added")
    return _view()


@core.app.patch("/api/computers/{cid}")
def api_edit(cid: str, body: EditBody, request: Request):
    """A new name; the TV its screen is and that TV's input ("" = none)."""
    core.house_token_only(request)
    tvs = {t["id"]: t for t in _tvs()}
    with _lock:
        c = _computers.get(cid)
        if c is None:
            raise HTTPException(404, "no such computer")
        if body.name is not None:
            c["name"] = _name(body.name)
        if body.tv is not None:
            if body.tv and body.tv not in tvs:
                raise HTTPException(400, "no such TV")
            c["tv"] = body.tv or None
            if not body.tv:
                c["input"] = None
        if body.input is not None:
            tv = tvs.get(c.get("tv"))
            if body.input and not (tv and body.input in tv["inputs"]):
                raise HTTPException(400, "that TV has no such input")
            c["input"] = body.input or None
        _save()
    return _view()


@core.app.delete("/api/computers/{cid}")
def api_remove(cid: str, request: Request):
    """Off the list (nothing changes on the computer); the house forgets its host key too."""
    core.house_token_only(request)
    with _lock:
        c = _computers.get(cid)
        if c is None:
            raise HTTPException(404, "no such computer")
        if c.get("host") == SELF:
            raise HTTPException(409, "this is the computer the house runs on")
        _computers.pop(cid)
        _status.pop(cid, None)
        _save()
    try:
        core.helper("POST", "/v1/hosts/forget", {"host": c["host"]})
    except HTTPException:
        pass
    core.log.info(f"[computers] {c['name']} ({c['host']}) removed")
    return _view()


@core.app.post("/api/computers/{cid}/forget-key")
def api_forget_key(cid: str, request: Request):
    """Its host key changed (a reinstall): the house takes the new one at the next check."""
    core.house_token_only(request)
    with _lock:
        c = _computers.get(cid)
    if c is None:
        raise HTTPException(404, "no such computer")
    core.helper("POST", "/v1/hosts/forget", {"host": c["host"]})
    _check([cid])
    return _view()


@core.app.post("/api/computers/check")
async def api_check(request: Request):
    core.house_token_only(request)
    _need_user()
    await run_in_threadpool(_check, list(_computers))
    return _view()


@core.app.post("/api/computers/scan")
async def api_scan(request: Request):
    """The house's /24 searched again: the listed computers checked, a listed one at a new address moved there, the rest
    offered (and the machines with ssh that are not computers of the house, with why)."""
    core.house_token_only(request)
    user, net = _need_user(), _net()
    if not net:
        raise HTTPException(503, "BIND_ADDRESS is not a private address: the network to search is not known")
    with _lock:
        if _scan["running"]:
            raise HTTPException(409, "a search is running")
        _scan.update({"running": True, "error": None})
    try:
        res = await run_in_threadpool(core.helper, "POST", "/v1/hosts/scan", {"net": net, "user": user}, SCAN_TIMEOUT)
    except HTTPException as e:
        with _lock:
            _scan.update({"running": False, "error": e.detail})
        raise
    now = time.time()
    with _lock:
        by_name = {c.get("hostname"): cid for cid, c in _computers.items() if c.get("hostname")}
        hosts = {c["host"] for c in _computers.values()}
        found, seen, moved = [], set(), []
        for r in sorted(res.get("hosts") or [], key=lambda r: not r.get("ok")):
            cid = by_name.get(r.get("hostname"))
            if cid:
                c = _computers[cid]
                if r["host"] != c["host"] and r.get("ok") and _status.get(cid, {}).get("ok") is not True:
                    core.log.info(f"[computers] {c['name']} moved {c['host']} -> {r['host']}")
                    moved.append((c["host"], c, _status.get(cid)))
                    c["host"] = r["host"]
                if r["host"] == c["host"]:
                    _status[cid] = {**{k: r.get(k) for k in ("ok", "ssh", "sudo", "error")}, "ts": now, "seen": now,
                                    "screens": r.get("screens"), "roles": r.get("roles"), "llm": r.get("llm")}
                continue
            if r["host"] in hosts or (r.get("hostname") and r["hostname"] in seen):
                continue
            seen.add(r.get("hostname"))
            found.append({k: r.get(k) for k in ("host", "ok", "error", "hostname", "os", "mac")} | {"guess": _guess(r.get("screens"))})
        _found[:] = found
        _scan.update({"running": False, "ts": now, "net": res.get("net") or net})
        _save()
    for old, c, st in moved:             # outside the lock: these ask the helper
        try:
            await run_in_threadpool(_follow_move, old, c, st)
        except HTTPException as e:
            core.log.warning(f"[computers] {c['name']}: its servers still point at {old}: {e.detail}")
    return _view()
