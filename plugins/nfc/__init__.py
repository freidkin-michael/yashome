"""NFC cards as a trigger: an NTAG 424 DNA sticker opens /nfc with a fresh SDM cryptogram; a tap
that verifies is an event of the device `nfc` (code "card", gesture = the card's name) and the
rules decide what it does (unlock a door, run a scene). Keys and cards come from .env (NFC_*)."""
import html
import json
import os
import pathlib
import threading
import time
import urllib.parse

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import app as core

from . import _sdm

PLUGIN = {"name": "NFC", "version": "2.0.0"}

COUNTERS_PATH = core.plugin_data("nfc") / "nfc_counters.json"
DEVICE_ID = os.environ.get("NFC_DEVICE") or "nfc"


def _cards() -> dict:
    """NFC_CARDS=<uid hex>=<name>,... ; the older NFC_UID_UNLOCK / NFC_UID_LOCK are cards "unlock" / "lock"."""
    out = {}
    for item in (os.environ.get("NFC_CARDS") or "").split(","):
        uid, _, name = item.partition("=")
        if uid.strip() and name.strip():
            out[uid.strip().lower()] = name.strip()
    for key, name in (("NFC_UID_UNLOCK", "unlock"), ("NFC_UID_LOCK", "lock")):
        if os.environ.get(key):
            out.setdefault(os.environ[key].strip().lower(), name)
    return out


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
_CARDS = _cards()
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
            core.log.error(f"[nfc] {COUNTERS_PATH.name} is missing: refusing every tap. Same stickers: put the backup "
                           "back, or tap EACH card yourself now and write the last line printed after that "
                           "(an old link also counts here, only your own fresh taps make it safe); so far: "
                           f"{fix}" + (f", not tapped yet: {', '.join(todo)}" if todo else "")
                           + "; {} is only for NEW stickers - for old ones it re-opens every used link")
            return "broken"
        except (OSError, ValueError) as e:
            core.log.error(f"[nfc] {COUNTERS_PATH.name} unreadable ({e}): refusing every tap until fixed")
            return "broken"
        last = max(int(disk.get(uid_hex, -1)), _seen.get(uid_hex, -1))
        if ctr <= last:
            return "replay"
        disk[uid_hex] = _seen[uid_hex] = ctr
        try:
            core._atomic_json_dump(COUNTERS_PATH, disk)
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
    return await _tap(request, form.get("picc", ""), form.get("cmac", ""))


def _failed(why: str):
    """A tap that did not verify never blocks one that does (a 64-bit CMAC is not guessed by
    rate); only the log is limited, to 20 lines a minute."""
    now = time.time()
    _fails[:] = [x for x in _fails if now - x < 60]
    _fails.append(now)
    if len(_fails) <= 20:
        core.log.warning(f"[nfc] {why}")


async def _tap(request: Request, picc: str, cmac: str):
    lang = _lang(request)

    def t(s: str, **kw) -> str:
        return (_I18N.get(lang, {}).get(s) or s).format(**kw)

    def reply(head: str, note: str, ok: bool = True, status: int = 200, **kw):
        r = _page(lang, t(head, **kw), t(note, **kw), ok)
        r.status_code = status
        return r

    if not (_META_KEY and _FILE_KEY and _CARDS):
        core.log.error("[nfc] not set up: NFC_SDM_META_KEY, NFC_SDM_FILE_KEY and NFC_CARDS are needed")
        return reply("Not set up", "card keys are not set", ok=False, status=503)
    try:
        uid, ctr = _sdm.verify(_META_KEY, _FILE_KEY, bytes.fromhex(picc), bytes.fromhex(cmac))
    except ValueError as e:
        _failed(f"refused: {e}")
        return reply("Refused", "the tag did not verify", ok=False, status=403)
    uid_hex = uid.hex()
    card = _CARDS.get(uid_hex)
    if card is None:
        _failed(f"unknown card {uid_hex}")
        return reply("Refused", "not our card", ok=False, status=403)
    why = await run_in_threadpool(_check_counter, uid_hex, ctr)
    if why == "replay":
        core.log.warning(f"[nfc] replay: card {uid_hex} counter {ctr}")
        return reply("Refused", "this link was used already", ok=False, status=403)
    if why:
        return reply("Refused", "the cards are not available, see the server log", ok=False, status=503)
    now = time.time()
    with core._events_lock:
        core._events[DEVICE_ID].append({"ts": now, "code": "card", "value": card, "t": int(now)})
    core._store_status(DEVICE_ID, {"online": True, "values": {}, "raw": {},
                                   "last_event_ts": now, "last_event": {"code": "card", "value": card}})
    core.log.info(f"[nfc] card {card} ({uid_hex}) counter {ctr}")
    core._bg.submit(core._safe_execute_binding, DEVICE_ID, "card", card)   # the rules decide what a card does
    return reply("Accepted", "card {card}", card=card)

core.register_device({"id": DEVICE_ID, "name": "NFC", "transport": "nfc", "category": "button",
                      "controls": [{"kind": "trigger_text", "code": "card", "label": "Card", "ro": True,
                                    "values": sorted(set(_CARDS.values()))}]})
