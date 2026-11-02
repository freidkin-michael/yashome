"""NFC cards as a trigger: an NTAG 424 DNA sticker opens /nfc with a fresh SDM cryptogram; a tap
that verifies is an event of the device `nfc` (code "card", gesture = the card's name) and the
rules decide what it does (unlock a door, run a scene). The keys come from .env (NFC_SDM_*); the cards are
plugin-data/nfc/cards.json, set in Settings -> Devices -> NFC tags."""
import asyncio
import html
import json
import os
import pathlib
import threading
import time
import urllib.parse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import app as core

from . import _sdm

PLUGIN = {"name": "NFC", "version": "2.1.0", "icon": "nfc"}

def _max_counters(new: bytes, old: bytes) -> bytes:
    """Two copies of the counters: the higher one per card (anything unreadable stays as it was)."""
    try:
        a, b = json.loads(new), json.loads(old)
        return json.dumps({u: max(int(a.get(u, -1)), int(b.get(u, -1))) for u in set(a) | set(b)}).encode()
    except (ValueError, TypeError, AttributeError):
        return new


COUNTERS_PATH = core.plugin_state_file("nfc", "nfc_counters.json", merge=_max_counters)
DEVICE_ID = "nfc"
CONFIRM_S = 20                    # a rule on a lock: how long the tap page waits for the lock's own report
CONFIRM_POLL_S = 0.5              # how often it looks


CARDS_FILE = core.plugin_data("nfc") / "cards.json"     # {"cards": {uid hex: name}}: the house's cards
NEW_S = 600                       # a tap of an unknown sticker that verified (ours, not on the list) is offered for adding this long
ENROLL_S = 120                    # "Add a tag": how long the house waits for the new tag's tap
UID_LEN = 14                      # an NTAG 424 DNA UID: 7 bytes


def _key(name: str) -> bytes:
    """An AES-128 key from the environment; a typo leaves it unset ("Not set up"), not the plug-in unloaded."""
    try:
        k = bytes.fromhex(os.environ.get(name) or "")
    except ValueError:
        k = b""
    if os.environ.get(name) and len(k) != 16:
        core.log.error(f"[nfc] {name} is not 32 hex digits: ignored")
        return b""
    return k


_META_KEY = _key("NFC_SDM_META_KEY")
_FILE_KEY = _key("NFC_SDM_FILE_KEY")


def _load_cards() -> dict:
    """cards.json ({} before the first tag)."""
    try:
        raw = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        cards = raw.get("cards") if isinstance(raw, dict) else None
        if not isinstance(cards, dict):
            raise ValueError("not {cards: {uid: name}}")
        return {str(u).lower(): str(n) for u, n in cards.items() if str(n).strip()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        core.log.error(f"[nfc] plugin-data/nfc/{CARDS_FILE.name} unreadable ({e}): no cards until it is fixed")
        return {}


def _write_cards(cards: dict) -> None:
    core.atomic_json_dump(CARDS_FILE, {"cards": dict(sorted(cards.items(), key=lambda kv: kv[1].lower()))}, indent=1, ensure_ascii=False)


_CARDS = _load_cards()
_new: dict = {}                     # uid -> {"ctr", "ts"}: verified taps of stickers not on the list (to add them)
_last: dict = {}                    # uid -> the time of its last accepted tap
_enroll: dict = {}                  # {"name", "until"}: the next tap of a tag of ours not on the list is added with this name
_added: dict = {}                   # {"uid", "name", "ts"}: the tag the last such tap added (the page says so)
_ctr_lock = threading.Lock()
_seen: dict = {}                    # the highest counter per card, also kept in memory: a missing file never re-opens old links
_fails: list = []                   # times of failed verifications: only their logging is limited
_I18N = json.loads((pathlib.Path(__file__).parent / "i18n.json").read_text(encoding="utf-8"))

core.OPEN_PATHS.add("/nfc")                               # a phone opens the sticker's link: no token

# The link is a one-time credential. A plain GET (a browser, a link-preview bot) only returns a
# page that POSTs it back; the tap counts on that POST.
_POST_BACK = ("<script>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
              "body:location.search.slice(1)}).then(r=>r.text()).then(h=>{document.open();document.write(h);document.close()})"
              "</script>")


def _lang(request: Request) -> str:
    al = (request.headers.get("accept-language") or "").lower()
    return next((lg for lg in _I18N if al.startswith(lg)), "en")


def _page(lang: str, head: str, note: str, ok: bool = True) -> Response:
    colour = "#22c55e" if ok else "#ef4444"
    e = html.escape
    return Response(
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(head)}</title><style>body{{background:#0f172a;color:#e2e8f0;"
        "font-family:-apple-system,system-ui,sans-serif;display:flex;"
        "justify-content:center;align-items:center;min-height:100vh;margin:0}"
        ".b{text-align:center;padding:2rem}h1{font-size:1.6rem;margin:0 0 .5rem}"
        "p{color:#94a3b8;margin:0}</style></head><body><div class=\"b\">"
        f'<h1 style="color:{colour}">{e(head)}</h1><p>{e(note)}</p></div></body></html>',
        media_type="text/html; charset=utf-8")


def _check_counter(uid_hex: str, ctr: int) -> str:
    """'' when the counter is new (and is now stored), else why not. Any doubt refuses: a counters
    file that cannot be read or holds garbage never lets an old link through."""
    with _ctr_lock:
        try:
            disk = json.loads(COUNTERS_PATH.read_text(encoding="utf-8"))
            if not isinstance(disk, dict) or not all(isinstance(v, int) for v in disk.values()):
                raise ValueError("not {uid: int}")
        except FileNotFoundError:
            _seen[uid_hex] = max(ctr, _seen.get(uid_hex, -1))    # verified: above every link it made
            fix = json.dumps({u: _seen[u] for u in _CARDS if u in _seen})
            todo = [u for u in _CARDS if u not in _seen]
            core.log.error(f"[nfc] plugin-state/nfc/{COUNTERS_PATH.name} is missing: refusing every tap. Same stickers: put the backup "
                           "back, or tap EACH card yourself now and write the last line printed after that "
                           "(an old link also counts here, only your own fresh taps make it safe); so far: "
                           f"{fix}" + (f", not tapped yet: {', '.join(todo)}" if todo else "")
                           + "; {} is only for NEW stickers - for old ones it re-opens every used link")
            return "broken"
        except (OSError, ValueError) as e:
            core.log.error(f"[nfc] plugin-state/nfc/{COUNTERS_PATH.name} unreadable ({e}): refusing every tap until fixed")
            return "broken"
        last = max(int(disk.get(uid_hex, -1)), _seen.get(uid_hex, -1))
        if ctr <= last:
            return "replay"
        disk[uid_hex] = _seen[uid_hex] = ctr
        try:
            core.atomic_json_dump(COUNTERS_PATH, disk)
        except OSError as e:
            core.log.error(f"[nfc] could not store the counter: {e}")
            return "broken"
    return ""


@core.app.get("/nfc")
async def nfc_get(request: Request):
    lang = _lang(request)
    t = lambda s: _I18N.get(lang, {}).get(s) or s
    page = _page(lang, t("One moment"), t("checking the card"))
    return Response(page.body.replace(b"</body>", _POST_BACK.encode() + b"</body>"), media_type=page.media_type)


@core.open_route              # the phone posting its sticker's link back has no token; the link proves itself
@core.app.post("/nfc")
async def nfc_post(request: Request):
    form = {k: v[0] for k, v in urllib.parse.parse_qs((await request.body()).decode("ascii", "replace")).items()}
    as_json = form.get("fmt") == "json" or "application/json" in (request.headers.get("accept") or "")
    return await _tap(request, form.get("picc", ""), form.get("cmac", ""), as_json)


def _failed(why: str):
    """A tap that did not verify never blocks one that does (a 64-bit CMAC is not guessed by
    rate); only the log is limited, to 20 lines a minute."""
    now = time.time()
    _fails[:] = [x for x in _fails if now - x < 60]
    _fails.append(now)
    if len(_fails) <= 20:
        core.log.warning(f"[nfc] {why}")


async def _tap(request: Request, picc: str, cmac: str, as_json: bool = False):
    lang = _lang(request)

    def t(s: str, **kw) -> str:
        return (_I18N.get(lang, {}).get(s) or s).format(**kw)

    def reply(head: str, note: str, ok: bool = True, status: int = 200, **kw):
        if as_json:                                  # the Yash! app shows the answer as a notification
            return JSONResponse({"ok": ok, "head": t(head, **kw), "note": t(note, **kw)}, status_code=status)
        r = _page(lang, t(head, **kw), t(note, **kw), ok)
        r.status_code = status
        return r

    if not (_META_KEY and _FILE_KEY):
        core.log.error("[nfc] not set up: NFC_SDM_META_KEY and NFC_SDM_FILE_KEY are needed")
        return reply("Not set up", "card keys are not set", ok=False, status=503)
    try:
        uid, ctr = _sdm.verify(_META_KEY, _FILE_KEY, bytes.fromhex(picc), bytes.fromhex(cmac))
    except ValueError as e:
        _failed(f"refused: {e}")
        return reply("Refused", "the tag did not verify", ok=False, status=403)
    uid_hex = uid.hex()
    card = _CARDS.get(uid_hex)
    if card is None and _enroll and time.time() < _enroll["until"]:     # Settings waits for a new tag: this is it
        name = _enroll["name"]
        try:
            await run_in_threadpool(_add, uid_hex, name, ctr)
        except HTTPException as e:
            return reply("Not added", "{why}", ok=False, status=e.status_code, why=e.detail)
        _enroll.clear()
        _added.update({"uid": uid_hex, "name": name, "ts": time.time()})
        return reply("Added", "tag {name} is on the list now", name=name)
    if card is None:                              # ours (it verified), not on the list: Settings offers to add it
        _failed(f"unknown card {uid_hex}")
        now = time.time()
        for u in [u for u, v in _new.items() if now - v["ts"] > NEW_S]:
            _new.pop(u, None)
        _new[uid_hex] = {"ctr": max(ctr, (_new.get(uid_hex) or {}).get("ctr", -1)), "ts": now}
        return reply("Refused", "not our card", ok=False, status=403)
    why = await run_in_threadpool(_check_counter, uid_hex, ctr)
    if why == "replay":
        core.log.warning(f"[nfc] replay: card {uid_hex} counter {ctr}")
        return reply("Refused", "this link was used already", ok=False, status=403)
    if why:
        return reply("Refused", "the cards are not available, see the server log", ok=False, status=503)
    now = time.time()
    with core.events_lock:
        core.events[DEVICE_ID].append({"ts": now, "code": "card", "value": card, "t": int(now)})
    core.store_status(DEVICE_ID, {"online": True, "values": {}, "raw": {},
                                   "last_event_ts": now, "last_event": {"code": "card", "value": card}})
    _last[uid_hex] = now
    core.log.info(f"[nfc] card {card} ({uid_hex}) counter {ctr}")
    res = await run_in_threadpool(core.safe_execute_binding, DEVICE_ID, "card", card)   # the rules decide
    dev = (core.DEVICES.get((res or {}).get("target")) or {})
    ctl = (dev.get("_ctl") or {}).get((res or {}).get("code")) or {}
    name = dev.get("name") or (res or {}).get("target") or "card " + card     # an error without a target
    if res and res.get("error") == "offline" and not res.get("raised"):
        text = {"waiting": "{name} is waiting for its report", "gone": "{name} is gone"}.get(res.get("why"),
                                                                                             "{name} is offline")
        return reply("Not done", text, ok=False, status=503, name=name)
    if res and res.get("error"):                       # e.g. a zigbee publish that failed
        return reply("Not done", "{name}: the command did not go out", ok=False, status=502, name=name)
    if not (res and ctl.get("confirm") and isinstance(res.get("value"), bool)):
        return reply("Accepted", "card {card}", card=card)   # a transport's own payload, a level: no on/off to wait for
    deadline = time.time() + CONFIRM_S          # a lock: the page says what the lock itself reports
    while time.time() < deadline:
        if (core.get_status(res["target"]).get("values") or {}).get(res["code"]) == res["value"]:
            text = ctl.get("text_on" if res["value"] else "text_off") or ("On" if res["value"] else "Off")
            return reply("Done", "{name}: {text}", name=name, text=t(text))
        await asyncio.sleep(CONFIRM_POLL_S)
    return reply("No answer", "{name} did not confirm in {secs} s", ok=False, status=504, name=name, secs=CONFIRM_S)

def _device() -> dict:
    return {"id": DEVICE_ID, "name": "NFC", "transport": "nfc", "category": "button", "virtual": "tag",
            "controls": [{"kind": "trigger_text", "code": "card", "label": "Card", "ro": True, "values": sorted(set(_CARDS.values()))}]}


core.register_device(_device())


# ---- Settings -> Devices -> NFC tags: the list, a tag added (tap it, or its UID typed), a tag removed ----
def _seed_counter(uid: str, ctr: int) -> bool:
    """A tag added from its tap: that tap's counter is stored, so its link (seen by the phone) never counts later."""
    with _ctr_lock:
        try:
            disk = json.loads(COUNTERS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if _CARDS:                             # the other cards' counters are lost: _check_counter refuses, none made up here
                return False
            disk = {}
        except (OSError, ValueError):
            return False
        if not isinstance(disk, dict):
            return False
        disk[uid] = _seen[uid] = max(int(disk.get(uid, -1)), ctr, _seen.get(uid, -1))
        core.atomic_json_dump(COUNTERS_PATH, disk)
    return True


def _view() -> dict:
    now = time.time()
    if _enroll and now >= _enroll["until"]:
        _enroll.clear()
    return {"ready": bool(_META_KEY and _FILE_KEY), "device": DEVICE_ID,
            "cards": [{"uid": u, "name": n, "last": _last.get(u)} for u, n in sorted(_CARDS.items(), key=lambda kv: kv[1].lower())],
            "new": [{"uid": u, "ts": v["ts"]} for u, v in sorted(_new.items(), key=lambda kv: -kv[1]["ts"]) if now - v["ts"] <= NEW_S],
            "enroll": {"name": _enroll["name"], "left": int(_enroll["until"] - now)} if _enroll else None,
            "added": dict(_added) if _added else None}


def _check_name(name: str, uid: str = "") -> str:
    """A tag's name as stored: spaces folded, 1-40 characters, none of | / + # (it is a part of a rule id), unique in any case."""
    name = " ".join(name.split())
    if not name or len(name) > 40 or any(c in name for c in "|/+#"):
        raise HTTPException(400, "a name of 1-40 characters, without | / + #")
    if name.lower() in {n.lower() for u, n in _CARDS.items() if u != uid}:
        raise HTTPException(409, "another tag has this name")
    return name


def _add(uid: str, name: str, ctr=None) -> None:
    """On the list; ctr is the counter of the tap that brought it (stored: that tap's link never counts later)."""
    global _CARDS
    if uid in _CARDS:
        raise HTTPException(409, f"this tag is on the list already: {_CARDS[uid]}")
    name = _check_name(name)
    if ctr is not None and not _seed_counter(uid, ctr):
        raise HTTPException(503, "the counters of the tags are not available, see the server log")
    _new.pop(uid, None)
    cards = {**_CARDS, uid: name}
    _write_cards(cards)
    _CARDS = cards
    core.update_device(_device())
    core.log.info(f"[nfc] tag {name} ({uid}) added" + (" from its tap" if ctr is not None else " by its UID"))


class CardBody(core.Body):
    uid: str
    name: str


class NameBody(core.Body):
    name: str


@core.app.get("/api/nfc")
def api_nfc(request: Request):
    core.house_token_only(request)
    return _view()


@core.app.post("/api/nfc/cards")
def api_add_card(body: CardBody, request: Request):
    """A tag on the list: the one just tapped (its counter kept) or a UID typed in (a new sticker: its first tap counts)."""
    core.house_token_only(request)
    uid = body.uid.strip().lower().replace(":", "").replace(" ", "")
    if len(uid) != UID_LEN or any(c not in "0123456789abcdef" for c in uid):
        raise HTTPException(400, f"the UID is {UID_LEN} hex digits")
    _add(uid, body.name, (_new.get(uid) or {}).get("ctr"))
    return _view()


@core.app.post("/api/nfc/enroll")
def api_enroll(body: NameBody, request: Request):
    """"Add a tag": the next tap of a tag of ours that is not on the list, on any phone, puts it there with this name."""
    core.house_token_only(request)
    _enroll.update({"name": _check_name(body.name), "until": time.time() + ENROLL_S})
    _added.clear()
    return _view()


@core.app.delete("/api/nfc/enroll")
def api_enroll_cancel(request: Request):
    core.house_token_only(request)
    _enroll.clear()
    return _view()


@core.app.patch("/api/nfc/cards/{uid}")
def api_rename_card(uid: str, body: NameBody, request: Request):
    """A new name; the rules of the tag follow it (a tap is the gesture "<name>", so they are keyed by it), switched off ones stay off."""
    core.house_token_only(request)
    global _CARDS
    uid = uid.lower()
    if uid not in _CARDS:
        raise HTTPException(404, "no such tag")
    old, name = _CARDS[uid], _check_name(body.name, uid)
    if name == old:
        return _view()
    with core.bindings_lock:
        gestures = (core.bindings.get(DEVICE_ID) or {}).get("card") or {}
        if old in gestures:
            gestures[name] = gestures.pop(old)
            was, now = f"b|{DEVICE_ID}|card|{old}", f"b|{DEVICE_ID}|card|{name}"
            if was in core.rules_disabled:
                core.rules_disabled.discard(was)
                core.rules_disabled.add(now)
            core.save_settings()
        cards = {**_CARDS, uid: name}
        _write_cards(cards)
        _CARDS = cards
    core.update_device(_device())
    core.log.info(f"[nfc] tag {old} ({uid}) renamed to {name}")
    return _view()


@core.app.delete("/api/nfc/cards/{uid}")
def api_remove_card(uid: str, request: Request):
    """Off the list: its taps are refused from now on; a rule naming it stays until it is deleted (the page offers that)."""
    core.house_token_only(request)
    global _CARDS
    uid = uid.lower()
    if uid not in _CARDS:
        raise HTTPException(404, "no such tag")
    cards = {u: n for u, n in _CARDS.items() if u != uid}
    _write_cards(cards)
    name, _CARDS = _CARDS[uid], cards
    core.update_device(_device())
    core.log.info(f"[nfc] tag {name} ({uid}) removed")
    return _view()
