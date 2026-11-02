"""player: a film or any http(s) stream on a TV of the house, played by the computer at that TV.

A player is a computer of the house (plug-in computers) with the role player installed from its card: mpv waiting in its
user's graphical session (host/yashome-player.service), driven by host/ctl.py. The house never opens a port there: every
command goes through the helper over ssh (POST /v1/hosts/player), which runs ctl.py with the command on its stdin.

The computer's card says which TV its screen is and on which input: Play turns that TV on and switches it to the input,
then mpv opens the address full screen. The dashboard shows each player in the window of its TV (ui.js).

What to play comes from the sources (_sources.py): search, or a page address pasted from a site, then its translations,
seasons, episodes and qualities; Watch asks the source for the stream (with the headers the site wants) and sends it to the
player picked. What a player watches is kept (plugin-state/player/watching.json): Continue from where it stopped, and the
next episode starts by itself when one ends.
"""
import asyncio
import io
import json
import os
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

import app as core

from . import _sources as src

PLUGIN = {"name": "Player", "version": "1.1.0", "icon": "play"}

ROLE = "player"
STATE_TTL = 2.0                    # a player's state is asked again after this
TV_WAKE_S = 40                     # a TV switched on answers within this (LG: Wake-on-LAN, then its socket)
_HERE = os.path.dirname(os.path.abspath(__file__))
_cache: dict = {}                  # player id -> (ts, state | None, error | None)
_lock = threading.Lock()


def _computers():
    mod = sys.modules.get("yashome_plugins.computers")
    if mod is None:
        raise HTTPException(409, "the plug-in computers is off: the players are its computers")
    return mod


def players() -> list:
    """The computers with a player: {id, name, host, user, tv, input}."""
    return [p for p in _computers().with_role(ROLE) if not p.get("wsl")]


def _player(pid: str) -> dict:
    p = next((p for p in players() if p["id"] == pid), None)
    if p is None:
        raise HTTPException(404, "no player on that computer")
    return p


def _send(p: dict, cmd: dict) -> dict:
    st = _send_raw(p, cmd)
    _seen(p["id"], st)
    return st


def _send_raw(p: dict, cmd: dict) -> dict:
    """One command to its ctl.py; its state back (kept for STATE_TTL), an error of the player as 409."""
    try:
        out = core.helper("POST", "/v1/hosts/player", {"host": p["host"], "user": p["user"], **cmd}, 20)
    except HTTPException as e:
        out = {"ok": False, "error": str(e.detail).removeprefix("helper: ")}
    if not out.get("ok"):
        with _lock:
            _cache[p["id"]] = (time.time(), None, out.get("error") or "no answer")
        raise HTTPException(409, out.get("error") or "no answer")
    with _lock:
        _cache[p["id"]] = (time.time(), out.get("state") or {}, None)
    return out.get("state") or {}


def _state(p: dict) -> tuple:
    with _lock:
        hit = _cache.get(p["id"])
    if hit and time.time() - hit[0] < STATE_TTL:
        return hit[1], hit[2]
    try:
        return _send(p, {"cmd": "status"}), None
    except HTTPException as e:
        return None, e.detail


def _view(p: dict, st=None, err=None) -> dict:
    tv = core.DEVICES.get(p.get("tv") or "") or {}
    return {"id": p["id"], "name": p["name"], "tv": p.get("tv") if tv else None, "tv_name": tv.get("name"),
            "input": p.get("input"), "state": st, "error": err, "watching": _watch["now"].get(p["id"])}


@core.app.get("/api/player")
def api_list():
    """Every player with what it plays now (each asked at most every STATE_TTL)."""
    ps = players()
    with ThreadPoolExecutor(max_workers=4) as ex:
        states = list(ex.map(_state, ps))
    return {"players": [_view(p, *s) for p, s in zip(ps, states)]}


class PlayBody(core.Body):
    url: str = Field(max_length=2000)
    title: str = Field("", max_length=200)
    start: float = Field(0, ge=0)


async def _tv_ready(p: dict) -> str:
    """Its TV on and on its input; "" or why not (the film plays anyway: the TV may be on that input already)."""
    tv = p.get("tv")
    if not tv or tv not in core.DEVICES:
        return ""
    try:
        st = core.get_status(tv)
        if not st.get("online") or (st.get("values") or {}).get("power") is not True:
            await core.control(tv, core.ControlBody(code="power", value=True, source="player"))
            for _ in range(TV_WAKE_S):
                st = core.get_status(tv)
                if st.get("online") and (st.get("values") or {}).get("power") is True:
                    break
                await asyncio.sleep(1)
            else:
                return "the TV did not come on"
        if p.get("input") and (st.get("values") or {}).get("input") != p["input"]:
            await core.control(tv, core.ControlBody(code="input", value=p["input"], source="player"))
    except HTTPException as e:
        return str(e.detail)
    return ""


@core.use_route
@core.app.post("/api/player/{pid}/play")
async def api_play(pid: str, body: PlayBody):
    """Its TV on and on its input, then the address full screen there."""
    p = await asyncio.to_thread(_player, pid)
    tv = await _tv_ready(p)
    if tv:
        core.log.warning(f"[player] {p['name']}: {tv}")
    cmd = {"cmd": "play", "url": body.url.strip(), "title": body.title.strip(), **({"start": body.start} if body.start else {})}
    st = await asyncio.to_thread(_send, p, cmd)
    core.log.info(f"[player] {p['name']}: plays {body.title.strip() or body.url.strip()[:80]}")
    return {**_view(p, st), "tv_note": tv}


class CmdBody(core.Body):
    cmd: str
    on: bool | None = None
    by: float | None = None
    to: float | None = None
    level: float | None = None
    id: int | None = None


CMDS = ("status", "pause", "mute", "seek", "volume", "audio", "sub", "stop")


@core.use_route
@core.app.post("/api/player/{pid}/cmd")
def api_cmd(pid: str, body: CmdBody):
    """pause / mute {on?}, seek {by | to}, volume {level | by}, audio / sub {id} (0 = none), stop, status.
    Stop by hand ends what it watches: no next episode after it."""
    if body.cmd not in CMDS:
        raise HTTPException(400, "cmd: " + ", ".join(CMDS))
    p = _player(pid)
    if body.cmd == "stop":
        with _lock:
            if _watch["now"].pop(pid, None) is not None:
                _save_watch(True)
    st = _send(p, {"cmd": body.cmd, **{k: v for k, v in body.model_dump().items() if k != "cmd" and v is not None}})
    return _view(p, st)


def host_files():
    """What a computer gets when it becomes a player (helper/roles/player-install.sh): ctl.py and the user unit."""
    def plain(t):
        t.uid = t.gid = 0
        t.uname = t.gname = ""
        t.mode = 0o755 if t.name.endswith(".py") else 0o644
        return t
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("ctl.py", "yashome-player.service"):
            tar.add(os.path.join(_HERE, "host", name), arcname=name, filter=plain)
    return Response(buf.getvalue(), media_type="application/gzip")


core.app.add_api_route("/plugins/player/host.tar.gz", host_files, methods=["GET"])
core.open_route(host_files)


# ---- what to play: the sources (search, a pasted address, info) and the player's film (Continue, the next episode) ----
_conf: dict = core.cfg_section("player", {}) or {}
if not isinstance(_conf, dict):
    _conf = {}
core.SETTINGS_SECTIONS["player"] = lambda: _conf
src.CONF = _conf
WATCH = core.plugin_state("player") / "watching.json"
RECENT_MAX = 30
NEXT_AT = 0.9                       # a film counts as seen from here (the credits)
_posters: set = set()               # poster addresses the sources gave: the only ones the house fetches
_watch = {"now": {}, "recent": []}  # now: player id -> what it plays; recent: [{source, id, title, poster, season, ...}]
_saved = [0.0]


def _load_watch() -> None:
    try:
        w = json.loads(WATCH.read_text(encoding="utf-8"))
        if isinstance(w, dict):
            _watch["now"] = w.get("now") if isinstance(w.get("now"), dict) else {}
            _watch["recent"] = [r for r in w.get("recent") or [] if isinstance(r, dict)][:RECENT_MAX]
    except (OSError, ValueError):
        pass


_load_watch()


def _save_watch(force=False) -> None:
    if force or time.time() - _saved[0] > 30:
        _saved[0] = time.time()
        core.atomic_json_dump(WATCH, _watch, ensure_ascii=False)


def _key(w: dict) -> tuple:
    return (w.get("source"), w.get("id"), w.get("season"), w.get("episode"))


def _seen(pid: str, st: dict) -> None:
    """A state of a player: where its film is, for Continue; a film that ended near its end: the next episode (the loop)."""
    with _lock:
        w = _watch["now"].get(pid)
        if not w:
            return
        if not st.get("idle") and st.get("pos") is not None:
            w.update(pos=st["pos"], duration=st.get("duration") or w.get("duration"), playing=True)
            rec = next((r for r in _watch["recent"] if _key(r) == _key(w)), None)
            if rec is not None:
                rec.update(pos=st["pos"], duration=w.get("duration"), ts=time.time())
            _save_watch()
        elif st.get("idle") and w.get("playing"):
            w["playing"] = False
            d, pos = w.get("duration") or 0, w.get("pos") or 0
            w["ended"] = bool(d) and pos >= d * NEXT_AT
            _save_watch(True)


def _posters_of(items) -> None:
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("poster"), str) and it["poster"].startswith(("http://", "https://")):
            _posters.add(it["poster"])
    while len(_posters) > 3000:
        _posters.pop()


def _err(e) -> HTTPException:
    return HTTPException(502, str(e))


@core.app.get("/api/player/sources")
def api_sources():
    """Every source: its name, whether it searches; the house's own broken ones with why."""
    return {"sources": src.listing(), "folders": list(_conf.get("folders") or [])}


@core.app.get("/api/player/search")
def api_search(q: str = ""):
    """Every source that searches, at once: {results: [{source, id, title, year, poster, kind, note}], errors: {source: why},
    searched: [source]} (Files only with folders; none: nowhere to search, the page says so)."""
    q = q.strip()[:200]
    if not q:
        return {"results": [], "errors": {}, "searched": []}
    names = [x["id"] for x in src.listing() if x.get("search") and not (x["id"] == "files" and not src.Files.folders())]

    def one(name):
        try:
            res = src.call(name, "search", q) or []
            nm = str(getattr(src.get(name), "NAME", name))
            return name, [dict(r, source=name, source_name=nm) for r in res if isinstance(r, dict) and r.get("id") is not None][:60], None
        except src.SourceError as e:
            return name, [], str(e)
    with ThreadPoolExecutor(max_workers=6) as ex:
        done = list(ex.map(one, names))
    results = [r for _, rs, _ in done for r in rs]
    _posters_of(results)
    return {"results": results, "errors": {n: e for n, _, e in done if e}, "searched": names}


class ResolveBody(core.Body):
    url: str = Field(max_length=4000)


@core.use_route
@core.app.post("/api/player/resolve")
def api_resolve(body: ResolveBody):
    """A page address pasted from a site: the first source it belongs to (the house's own first, Link last) and its info."""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "an address that starts with http:// or https://")
    for name, s in src.sources().items():
        if not callable(getattr(s, "match", None)):
            continue
        try:
            iid = src.call(name, "match", url)
        except src.SourceError:
            continue
        if iid is None:
            continue
        try:
            info = src.call(name, "info", iid) or {}
        except src.SourceError as e:
            raise _err(e)
        _posters_of([info])
        return {"source": name, "source_name": str(getattr(s, "NAME", name)), "id": iid, "info": info}
    raise HTTPException(404, "no source knows this address")


@core.app.get("/api/player/info")
def api_info(source: str, id: str, translation: str = ""):
    """Its translations, seasons and episodes, qualities (the seasons of one translation, when it is given)."""
    try:
        info = src.call(source, "info", id, translation or None) or {}
    except src.SourceError as e:
        raise _err(e)
    _posters_of([info])
    return {"source": source, "source_name": str(getattr(src.get(source), "NAME", source)), "id": id, "info": info}


@core.app.get("/api/player/poster")
def api_poster(u: str):
    """A poster a source named (no other address): the page may show no image from elsewhere."""
    if u not in _posters:
        raise HTTPException(404, "not a poster of a search")
    try:
        _, ctype, body = src.fetch(u, limit=2 * 1024 * 1024)
    except src.SourceError as e:
        raise _err(e)
    if not ctype.startswith("image/"):
        raise HTTPException(502, "not an image")
    return Response(body, media_type=ctype.split(";")[0], headers={"Cache-Control": "private, max-age=86400"})


class WatchBody(core.Body):
    source: str = Field(max_length=40)
    id: str = Field(max_length=2000)
    translation: str | None = Field(None, max_length=200)
    season: int | None = None
    episode: int | None = None
    quality: str | None = Field(None, max_length=200)
    start: float | None = Field(None, ge=0)
    title: str = Field("", max_length=200)
    poster: str = Field("", max_length=2000)


def _stream(w: dict) -> dict:
    try:
        s = src.call(w["source"], "stream", w["id"], translation=w.get("translation"), season=w.get("season"),
                     episode=w.get("episode"), quality=w.get("quality")) or {}
    except src.SourceError as e:
        raise _err(e)
    if not isinstance(s.get("url"), str) or not s["url"].startswith(("http://", "https://")):
        raise HTTPException(502, f"{w['source']}: no stream address")
    return s


def _label(w: dict, s: dict) -> str:
    t = w.get("title") or s.get("title") or ""
    if w.get("season") is not None and w.get("episode") is not None:
        t = f"{t} \u00b7 S{w['season']}E{w['episode']}"
    return t[:200]


async def _watch_on(p: dict, w: dict, start=None) -> dict:
    s = await asyncio.to_thread(_stream, w)
    note = await _tv_ready(p)
    cmd = {"cmd": "play", "url": s["url"], "title": _label(w, s)}
    if s.get("headers"):
        cmd["headers"] = {str(k): str(v) for k, v in s["headers"].items()}
    subs = [x if isinstance(x, str) else (x or {}).get("url") for x in s.get("subs") or []]
    if subs:
        cmd["subs"] = [u for u in subs if isinstance(u, str)][:10]
    if start:
        cmd["start"] = start
    with _lock:
        _watch["now"][p["id"]] = dict(w, playing=False, ended=False, pos=start or 0)
        rec = [r for r in _watch["recent"] if _key(r) != _key(w)]
        _watch["recent"] = [dict(w, pos=start or 0, ts=time.time())] + rec[:RECENT_MAX - 1]
        _save_watch(True)
    st = await asyncio.to_thread(_send_raw, p, cmd)
    core.log.info(f"[player] {p['name']}: {cmd['title']} ({w['source']})")
    return {**_view(p, st), "tv_note": note, "watching": _watch["now"].get(p["id"])}


@core.use_route
@core.app.post("/api/player/{pid}/watch")
async def api_watch(pid: str, body: WatchBody):
    """The source's stream of that film (translation, season, episode, quality) on this player, its TV on and on its input."""
    p = await asyncio.to_thread(_player, pid)
    w = body.model_dump(exclude={"start"})
    return await _watch_on(p, w, body.start)


def _next_of(w: dict):
    """(season, episode) after this one, from the source's info; None: the last one, or not a series."""
    if w.get("season") is None or w.get("episode") is None:
        return None
    try:
        seasons = (src.call(w["source"], "info", w["id"], w.get("translation")) or {}).get("seasons") or []
    except src.SourceError:
        return None
    order = [(int(s.get("n")), int(e.get("n"))) for s in seasons for e in s.get("episodes") or []
             if str(s.get("n")).lstrip("-").isdigit() and str(e.get("n")).lstrip("-").isdigit()]
    order.sort()
    later = [x for x in order if x > (w["season"], w["episode"])]
    return later[0] if later else None


@core.use_route
@core.app.post("/api/player/{pid}/next")
async def api_next(pid: str):
    """The next episode of what this player watches."""
    p = await asyncio.to_thread(_player, pid)
    w = _watch["now"].get(pid)
    if not w:
        raise HTTPException(409, "this player watches nothing of a source now")
    nxt = await asyncio.to_thread(_next_of, w)
    if not nxt:
        raise HTTPException(409, "that was the last episode")
    return await _watch_on(p, dict(w, season=nxt[0], episode=nxt[1]))


@core.app.get("/api/player/recent")
def api_recent():
    """What was watched lately, the newest first, with where it stopped (Continue); and what each player watches now."""
    return {"recent": _watch["recent"], "now": _watch["now"]}


@core.use_route
@core.app.delete("/api/player/recent")
def api_recent_forget(source: str, id: str):
    with _lock:
        _watch["recent"] = [r for r in _watch["recent"] if (r.get("source"), r.get("id")) != (source, id)]
        _save_watch(True)
    return {"recent": _watch["recent"]}


class FoldersBody(core.Body):
    folders: list[str] = Field(default_factory=list, max_length=20)


@core.app.put("/api/player/folders")
def api_folders(body: FoldersBody, request: Request):
    """The folders the Files source searches: web listings of a NAS (http(s), each one's sub-folders too)."""
    core.house_token_only(request)
    out = []
    for f in body.folders:
        f = f.strip()
        if not f:
            continue
        if not f.startswith(("http://", "https://")) or len(f) > 500 or any(c.isspace() for c in f):
            raise HTTPException(400, f"not a web address of a folder: {f[:60]}")
        out.append(f if f.endswith("/") else f + "/")
    _conf["folders"] = out
    core.save_settings()
    return {"folders": out}


async def _loop():
    """A film that ended near its end on a player: the next episode there, by itself. Players with a film are asked every 10 s."""
    while True:
        await asyncio.sleep(10)
        try:
            with _lock:
                busy = [pid for pid, w in _watch["now"].items() if w.get("playing") or w.get("ended")]
            for pid in busy:
                p = next((x for x in await asyncio.to_thread(players) if x["id"] == pid), None)
                if p is None:
                    continue
                await asyncio.to_thread(_state, p)
                w = _watch["now"].get(pid) or {}
                if w.get("ended"):
                    w["ended"] = False
                    nxt = await asyncio.to_thread(_next_of, w)
                    if nxt:
                        core.log.info(f"[player] {p['name']}: the next episode by itself")
                        await _watch_on(p, dict(w, season=nxt[0], episode=nxt[1]))
                    else:
                        with _lock:
                            _watch["now"].pop(pid, None)
                            _save_watch(True)
        except Exception as e:  # noqa: BLE001 - a computer or a source away: next time
            core.log.warning(f"[player] {getattr(e, 'detail', e)}")


core.STARTUP_TASKS.append(_loop)
