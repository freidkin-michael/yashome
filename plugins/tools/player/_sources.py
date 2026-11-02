"""The player's sources: where a film is found, what it has (translations, seasons, qualities) and the stream to play.

A source is a module with NAME and some of these functions (each one it lacks is a thing it does not do):
    search(query) -> [{id, title, year?, poster?, kind?: "film" | "series", note?}]
    match(url) -> id | None                    a page address pasted from a site: the id of its film, None = not this source's
    info(id, translation=None) -> {title, year?, poster?, description?, kind?, translations?: [{id, name}],
                                   seasons?: [{n, episodes: [{n, title?}]}], qualities?: [name]}
    stream(id, translation=None, season=None, episode=None, quality=None) -> {url, headers?: {name: value}, subs?: [url], title?}
The house's own sources are plugin-data/player/sources/<name>.py (the house's repository; read again when a file changes),
asked first; then the built-in ones: Files (the folders of the settings, a NAS's web listing) and Link (a film's own
address, or a page that shows one). A source runs inside the backend: trusted code, like a plug-in."""
import html
import importlib.util
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import app as core

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT = 15
PAGE_MAX = 3 * 1024 * 1024
MEDIA_EXT = (".mp4", ".mkv", ".webm", ".m3u8", ".mpd", ".avi", ".mov", ".ts", ".m4v", ".mpg", ".wmv", ".flv")
MEDIA_TYPES = ("video/", "audio/", "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml")
USER_DIR = core.plugin_data("player") / "sources"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CONF: dict = {}                      # the settings section "player" (the plug-in hands it over): {"folders": [url]}


class SourceError(Exception):
    """What a source says went wrong, in words."""


def fetch(url: str, headers=None, limit=PAGE_MAX, method="GET"):
    """(final url, content type, body bytes) of a plain request; never a challenge solved, a refusal is a refusal."""
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.geturl(), (r.headers.get("Content-Type") or "").lower(), (r.read(limit) if method == "GET" else b"")
    except urllib.error.HTTPError as e:
        raise SourceError(f"the site answered {e.code}" + (" (it lets no program in)" if e.code in (403, 429, 503) else ""))
    except (OSError, ValueError) as e:
        raise SourceError(f"the site does not answer ({type(e).__name__})")


def _text(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype)
    try:
        return body.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def is_media(url: str) -> bool:
    return urllib.parse.urlsplit(url).path.lower().endswith(MEDIA_EXT)


def nice_name(url: str) -> str:
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return re.sub(r"[._]+", " ", re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)).strip() or url


# ---- Link: a film's own address, or a page that shows one (its og:video, <video>, <source>, an .m3u8/.mp4 in it) ----
class Link:
    NAME = "Link"
    _pages: dict = {}                # page url -> (ts, info): a page read once for info and its stream

    @staticmethod
    def match(url):
        return url if re.match(r"^https?://", url or "") else None

    @classmethod
    def _page(cls, url):
        hit = cls._pages.get(url)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        if is_media(url):
            out = {"title": nice_name(url), "kind": "film", "found": [{"name": nice_name(url), "url": url}]}
        else:
            final, ctype, body = fetch(url)
            if ctype.startswith(MEDIA_TYPES):
                out = {"title": nice_name(final), "kind": "film", "found": [{"name": nice_name(final), "url": final}]}
            else:
                out = cls.parse(final, _text(body, ctype))
        if not out["found"]:
            raise SourceError("no video on this page (a site that hides it needs a source of its own)")
        if len(cls._pages) > 50:
            cls._pages.clear()
        cls._pages[url] = (time.time(), out)
        return out

    @staticmethod
    def parse(base: str, page: str) -> dict:
        def meta(prop):
            m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)', page, re.I) \
                or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']', page, re.I)
            return html.unescape(m.group(1)).strip() if m else ""
        title = meta("og:title") or html.unescape((re.search(r"<title[^>]*>([^<]*)", page, re.I) or [None, ""])[1]).strip()
        urls = [meta(p) for p in ("og:video:secure_url", "og:video:url", "og:video")]
        urls += re.findall(r'<(?:video|source)[^>]+src=["\']([^"\']+)', page, re.I)
        urls += re.findall(r'https?:(?:\\?/){2}[^\s"\'<>]+?\.(?:m3u8|mp4|mpd|webm)(?:\?[^\s"\'<>]*)?', page)
        found, seen = [], set()
        for u in urls:
            u = html.unescape(u.replace("\\/", "/")).strip()
            if not u or u.startswith(("blob:", "data:")):
                continue
            u = urllib.parse.urljoin(base, u)
            if not re.match(r"^https?://", u) or u in seen:
                continue
            seen.add(u)
            q = re.search(r"(2160|1440|1080|720|480|360|240)p?", u)
            found.append({"name": (q.group(1) + "p \u00b7 " if q else "") + nice_name(u), "url": u})
            if len(found) >= 20:
                break
        return {"title": title[:200] or nice_name(base), "poster": meta("og:image"), "description": meta("og:description")[:600],
                "kind": "film", "found": found, "page": base}

    @classmethod
    def info(cls, id, translation=None):
        p = cls._page(id)
        return {k: v for k, v in p.items() if k not in ("found", "page")} | {"qualities": [f["name"] for f in p["found"]]}

    @classmethod
    def stream(cls, id, translation=None, season=None, episode=None, quality=None):
        p = cls._page(id)
        f = next((f for f in p["found"] if f["name"] == quality), p["found"][0])
        headers = {"User-Agent": UA} | ({"Referer": p["page"]} if p.get("page") else {})
        return {"url": f["url"], "headers": headers, "title": p["title"]}


# ---- Files: the folders of the settings (a NAS's web listing: nginx autoindex, Apache, python http.server) ----
class Files:
    NAME = "Files"
    DEPTH = 3
    FILES_MAX = 5000
    TTL = 600
    _index: dict = {"ts": 0.0, "folders": (), "files": []}
    _lock = threading.Lock()

    @staticmethod
    def folders() -> list:
        return [f for f in CONF.get("folders") or [] if isinstance(f, str) and f.startswith(("http://", "https://"))]

    @classmethod
    def _files(cls) -> list:
        folders = tuple(cls.folders())
        with cls._lock:
            ix = cls._index
            if ix["folders"] == folders and time.time() - ix["ts"] < cls.TTL:
                return ix["files"]
        files, todo, seen = [], [(f if f.endswith("/") else f + "/", 0) for f in folders], set()
        while todo and len(files) < cls.FILES_MAX:
            url, depth = todo.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                _, ctype, body = fetch(url, limit=PAGE_MAX)
            except SourceError:
                continue
            for href in re.findall(r'href=["\']([^"\'?#]+)', _text(body, ctype), re.I):
                full = urllib.parse.urljoin(url, html.unescape(href))
                if not full.startswith(url) or full == url:
                    continue                                   # up, elsewhere, itself
                if full.endswith("/"):
                    if depth + 1 < cls.DEPTH:
                        todo.append((full, depth + 1))
                elif is_media(full):
                    files.append(full)
        with cls._lock:
            cls._index = {"ts": time.time(), "folders": folders, "files": files}
        return files

    @classmethod
    def search(cls, query):
        words = [w for w in query.lower().split() if w]
        out = []
        for u in cls._files():
            name = nice_name(u)
            if words and all(w in name.lower() or w in urllib.parse.unquote(u).lower() for w in words):
                out.append({"id": u, "title": name, "kind": "film",
                            "note": urllib.parse.unquote(urllib.parse.urlsplit(u).path.rsplit("/", 2)[-2]) if "/" in u else ""})
            if len(out) >= 50:
                break
        return out

    @classmethod
    def match(cls, url):
        return url if is_media(url) and any(url.startswith(f) for f in cls.folders()) else None

    @staticmethod
    def info(id, translation=None):
        return {"title": nice_name(id), "kind": "film"}

    @staticmethod
    def stream(id, translation=None, season=None, episode=None, quality=None):
        return {"url": id, "title": nice_name(id)}


BUILTIN = {"files": Files, "link": Link}
_user: dict = {}                     # name -> (mtime, module | None, error | None)
_user_lock = threading.Lock()


def _load_user() -> None:
    """plugin-data/player/sources/*.py, each loaded again when its file changed; a broken one keeps its error."""
    present = {p.stem: p for p in sorted(USER_DIR.glob("*.py")) if NAME_RE.match(p.stem)} if USER_DIR.is_dir() else {}
    with _user_lock:
        for name in list(_user):
            if name not in present:
                _user.pop(name)
        for name, p in present.items():
            mt = p.stat().st_mtime
            if name in _user and _user[name][0] == mt:
                continue
            modname = f"yashome_player_source_{name}"
            try:
                spec = importlib.util.spec_from_file_location(modname, p)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[modname] = mod
                spec.loader.exec_module(mod)
                _user[name] = (mt, mod, None)
                core.log.info(f"[player] source {name} loaded")
            except Exception as e:  # noqa: BLE001 - a house's own module: its error is shown, the rest go on
                sys.modules.pop(modname, None)
                _user[name] = (mt, None, f"{type(e).__name__}: {e}")
                core.log.error(f"[player] source {name}: {type(e).__name__}: {e}")


def sources() -> dict:
    """name -> module or class, the house's own first (asked first for a pasted address), the built-in ones after."""
    _load_user()
    with _user_lock:
        own = {n: m for n, (_, m, err) in _user.items() if m is not None and n not in BUILTIN}
    return {**own, **BUILTIN}


def listing() -> list:
    _load_user()
    with _user_lock:
        broken = [{"id": n, "name": n, "error": err, "own": True} for n, (_, m, err) in _user.items() if m is None]
    return [{"id": n, "name": str(getattr(s, "NAME", n)), "own": n not in BUILTIN, "search": callable(getattr(s, "search", None)),
             "match": callable(getattr(s, "match", None))} for n, s in sources().items()] + broken


def get(name: str):
    s = sources().get(name)
    if s is None:
        raise SourceError(f"no source {name!r}")
    return s


def call(name: str, fn: str, *args, **kw):
    """One function of a source; whatever it raises comes back as SourceError in words."""
    f = getattr(get(name), fn, None)
    if not callable(f):
        raise SourceError(f"the source {name} cannot {fn}")
    try:
        return f(*args, **kw)
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001 - a source's own bug: said, not a 500
        raise SourceError(f"{name}: {type(e).__name__}: {str(e)[:200]}")
