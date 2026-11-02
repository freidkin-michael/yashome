"""map: the floor plan of the house, with the devices and loads on it and what controls what. OWNER ONLY (the house token): every route
answers 403 to anyone else, and the dashboard shows the tab only when the first call succeeds.

Data: plugin-data/map/map.json (the house repository keeps its history) and the house's vector scheme beside it (scheme.svg). Coordinates are 0..1 of the scheme.
An ITEM is a registry device that has a place ("device") or a load that has no device of its own - a lamp, a socket, a boiler ("load"); a LINK says
which channel of which device controls a load. Links the house already knows (rules, panel buttons, IR blasters) are computed on demand and
never stored (GET /api/map/auto)."""
import hashlib
import json
import math
import pathlib
import re
import xml.etree.ElementTree as ET
import sys
import threading
import time
import uuid

from fastapi import HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import ConfigDict, Field

import app as core

PLUGIN = {"name": "Home map", "version": "1.0.0", "icon": "map"}

SCHEME_FILE = "scheme.svg"                                 # house data: plugin-data/map/scheme.svg, the one background of the map, checked every time it is read
CANVAS = {"x": 0, "y": 0, "w": 1000, "h": 700}                 # the frame of a house without a drawing: zones are drawn by hand on a grid
MAX_SVG_BYTES = 1024 * 1024
SVG_MAX_ELEMENTS, SVG_MAX_DEPTH = 200000, 64               # a plan of a flat has thousands of elements and a few levels
END_TIE = 1e-6                                            # ends this close in x: one above the other (the page uses the same)
LOAD_TYPES = ("lamp", "socket", "ac", "tv", "boiler", "led", "fan", "other")
SVG_NS = "{http://www.w3.org/2000/svg}"
SVG_TAGS = {"svg", "g", "defs", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "title", "desc", "linearGradient", "radialGradient", "stop",
            "filter", "feDropShadow", "feGaussianBlur", "feOffset", "feFlood", "feComposite", "feMerge", "feMergeNode", "feColorMatrix", "feBlend"}
SVG_ATTRS = {"xmlns", "viewBox", "preserveAspectRatio", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "transform", "id", "class",
             "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset",
             "opacity", "display", "shape-rendering", "vector-effect", "filter", "font-family", "font-size", "font-weight", "letter-spacing", "text-anchor", "dominant-baseline",
             "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "dx", "dy", "stdDeviation", "flood-color", "flood-opacity", "in", "in2", "result",
             "mode", "operator", "type", "values", "k1", "k2", "k3", "k4", "data-walls", "data-snap", "xml:space"}
SVG_VALUE = re.compile(r"^(?!.*(?:url\(|\\|&|javascript|data:|expression))", re.I)              # a value that is no url() and no escape ...
SVG_URL = re.compile(r"^url\(#[A-Za-z0-9_-]+\)$")                                                # ... except a reference to an id of the same file
ID_RE = r"^[A-Za-z0-9_-]{1,24}$"
VIRTUAL_TRANSPORTS = ("ir", "nfc", "presence", "family", "voice")    # remotes, tags, people, composites, voice cards: nothing stands there
# "Join neighbour walls" (frame units; a hand-drawn plan, not a setting): an edge within 3 degrees of an axis and STRAIGHT_MIN long is
# meant to lie on it; two zones' edges JOIN_GAP apart (a wall's thickness) are one wall, ends JOIN_END apart continue one wall.
SLANT = 0.0524                                               # tan(3 degrees)
STRAIGHT_MIN = 30
JOIN_GAP = 16
JOIN_END = 12
THINGS_FILE = "things.json"                                # house data (optional): {"things": [{dev, name, kind, members}]} names, kinds and merges
_lock = threading.RLock()


def _file():
    return core.plugin_data("map") / "map.json"


def _empty() -> dict:
    return {"v": 1, "rev": 0, "rooms": [], "unplaced": [], "items": [], "links": [], "walls": [], "openings": []}


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(d, dict):
        return _empty()
    for old in ("plan", "plans", "plan_urls", "frame"):       # keys of an older file (the plan under the zones is "underlay" now)
        d.pop(old, None)
    if isinstance(d.get("openings"), list):                  # an opening of before sat on (zone, edge index): it has no place now
        d["openings"] = [o for o in d["openings"] if isinstance(o, dict) and "a" in o]
    return {**_empty(), **d}


def _save(d: dict) -> None:
    core.atomic_json_dump(_file(), d, indent=1, ensure_ascii=False)


def _owner(request: Request) -> None:
    core.house_token_only(request)


def _svg_ok(raw: bytes, limit: int = MAX_SVG_BYTES) -> bool:
    """A static drawing only: every element and attribute is on a short list (no script, animation, style, link, image, foreign element or prefix), a
    value is no url() but a reference to an id of the file, and there is no entity or document type."""
    if len(raw) > limit:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if re.search(r"<!(?!--)|<\?(?!xml )", text) or len(re.findall(r"<[A-Za-z]", text)) > SVG_MAX_ELEMENTS:
        return False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    if root.tag != SVG_NS + "svg":
        return False
    stack = [(root, 1)]
    while stack:                                             # a drawing nested deeper than any plan needs is refused
        el, depth = stack.pop()
        if depth > SVG_MAX_DEPTH:
            return False
        stack.extend((c, depth + 1) for c in el)
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(SVG_NS) or el.tag[len(SVG_NS):] not in SVG_TAGS:
            return False
        for k, v in el.attrib.items():
            name = {"{http://www.w3.org/XML/1998/namespace}space": "xml:space"}.get(k, k)
            if name not in SVG_ATTRS or not (SVG_URL.match(v.strip()) or SVG_VALUE.match(v)):
                return False
    return True


def _scheme():
    """{w, h} of the house's scheme from its viewBox, or None when there is none (or it is not a safe drawing)."""
    f = core.plugin_data("map") / SCHEME_FILE
    try:
        raw = f.read_bytes()
    except OSError:
        return None
    if not _svg_ok(raw):
        core.log.warning("[map] scheme.svg is not a static drawing: not used")
        return None
    box = _svg_box(raw)
    if box is None:
        core.log.warning("[map] scheme.svg has no usable size (viewBox or width/height): not used")
    return box


def _num(v):
    """A finite number of an SVG attribute ("530", "530px"), else None."""
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(px)?\s*", str(v or ""))
    if not m:
        return None
    f = float(m.group(1))
    return f if math.isfinite(f) else None


def _svg_box(raw: bytes):
    """{x, y, w, h} of a drawing: its viewBox, else its width and height; None when neither gives a size above 0."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    vb = [_num(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v]
    if len(vb) == 4 and None not in vb and vb[2] > 0 and vb[3] > 0:
        return {"x": vb[0], "y": vb[1], "w": vb[2], "h": vb[3]}
    w, h = _num(root.get("width")), _num(root.get("height"))
    if w and h and w > 0 and h > 0:
        return {"x": 0.0, "y": 0.0, "w": w, "h": h}
    return None


# ---- validation of what the dashboard sends ----
class Point(core.Body):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Room(core.Body):
    id: str = Field(pattern=ID_RE)
    name: str = Field(min_length=1, max_length=40)
    house_room: str = Field(default="", max_length=64)     # the dashboard room this contour is
    poly: list[Point] = Field(min_length=3, max_length=60)
    walls: list[bool] = Field(default=[], max_length=60)   # edge i (point i -> i+1) is a wall; empty: not marked yet


class Item(core.Body):
    """A device or a load. An LED strip is drawn as a line: `path` holds its points (0..1) and `smooth` says whether it is rounded."""
    id: str = Field(pattern=ID_RE)
    kind: str = Field(pattern="^(device|load)$")
    dev: str = Field(default="", max_length=64)            # a device item: its registry id (or panel:<name>)
    name: str = Field(default="", max_length=60)
    type: str = Field(default="", max_length=10)           # a load: one of LOAD_TYPES
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    room: str = Field(default="", max_length=24)
    path: list[Point] = Field(default_factory=list, max_length=400)
    smooth: bool = False                                   # straight segments unless the owner rounds it


class Link(core.Body):
    load: str = Field(pattern=ID_RE)                      # the item (a load) that is controlled
    dev: str = Field(min_length=1, max_length=64)          # by this device
    code: str = Field(default="", max_length=64)           # and this channel of it
    note: str = Field(default="", max_length=60)
    seg: list[int] = Field(default_factory=list, max_length=2)    # a part of a strip: [first point, last point] of its path; empty = the whole load


class ZoneIn(core.Body):
    id: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,24}$")
    name: str = Field(min_length=1, max_length=40)
    poly: list[Point] = Field(min_length=3, max_length=60)


class ZonesBody(core.Body):
    zones: list[ZoneIn] = Field(max_length=60)


def _simple_polygon(pts: list) -> bool:
    """No two edges cross or touch except the neighbouring ones at their shared corner, and the outline has an area."""
    n = len(pts)

    def cross(o, a, b):
        return (a["x"] - o["x"]) * (b["y"] - o["y"]) - (a["y"] - o["y"]) * (b["x"] - o["x"])

    def hit(p, q, r, s):                                       # the segments pq and rs share a point
        d1, d2, d3, d4 = cross(p, q, r), cross(p, q, s), cross(r, s, p), cross(r, s, q)
        if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
            return True
        on = lambda a, b, c: abs(cross(a, b, c)) < 1e-12 and min(a["x"], b["x"]) - 1e-12 <= c["x"] <= max(a["x"], b["x"]) + 1e-12 and min(a["y"], b["y"]) - 1e-12 <= c["y"] <= max(a["y"], b["y"]) + 1e-12   # noqa: E731
        return on(p, q, r) or on(p, q, s) or on(r, s, p) or on(r, s, q)
    for i in range(n):
        for j in range(i + 1, n):
            a, b, c, d = pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]
            if j == i + 1:
                if abs(cross(a, b, d)) < 1e-12 and ((d["x"] - b["x"]) * (a["x"] - b["x"]) + (d["y"] - b["y"]) * (a["y"] - b["y"])) > 0:
                    return False                                 # the next edge folds back over this one
                continue
            if i == 0 and j == n - 1:
                if abs(cross(c, d, b)) < 1e-12 and ((b["x"] - a["x"]) * (c["x"] - a["x"]) + (b["y"] - a["y"]) * (c["y"] - a["y"])) > 0:
                    return False
                continue
            if hit(a, b, c, d):
                return False
    return _area(pts) > 1e-6


class Wall(core.Body):
    """A wall inside a zone (the zone stays one room): a line from a to b, 0..1 of the frame."""
    id: str = Field(pattern=ID_RE)
    a: list[float] = Field(min_length=2, max_length=2)
    b: list[float] = Field(min_length=2, max_length=2)


class Opening(core.Body):
    """A door or a window drawn on a wall: its two ends (0..1 of the frame, on the wall's line), so it keeps its place whatever happens to
    the zones (an import renumbers their edges). A door opens to the left (hinge at a), to the right (hinge at b) or slides."""
    id: str = Field(pattern=ID_RE)
    a: list[float] = Field(min_length=2, max_length=2)
    b: list[float] = Field(min_length=2, max_length=2)
    kind: str = Field(pattern="^(door|window)$")
    open: str = Field(default="left", pattern="^(left|right|slide)$")
    side: bool = False                                     # the leaf lies on the other side of the wall


class MapBody(core.Body):
    model_config = ConfigDict(extra="forbid")
    rev: int
    rooms: list[Room] = Field(max_length=60)
    items: list[Item] = Field(max_length=600)
    links: list[Link] = Field(max_length=1200)
    walls: list[Wall] | None = Field(default=None, max_length=400)          # None: kept as they are (a page that does not know them)
    openings: list[Opening] | None = Field(default=None, max_length=400)


def _check(b: MapBody) -> None:
    ids = [i.id for i in b.items]
    if len(set(ids)) != len(ids) or len({r.id for r in b.rooms}) != len(b.rooms):
        raise HTTPException(400, "item and room ids must be unique")
    devs = [i.dev for i in b.items if i.kind == "device"]
    if any(not d for d in devs) or len(set(devs)) != len(devs):
        raise HTTPException(400, "a device is placed once")
    for i in b.items:
        if i.kind == "load" and (not i.name.strip() or i.type not in LOAD_TYPES):
            raise HTTPException(400, f"a load needs a name and a type of {LOAD_TYPES}")
    loads = {i.id: i for i in b.items if i.kind == "load"}
    for i in b.items:
        if i.path and (i.kind != "load" or i.type != "led" or len(i.path) < 2):
            raise HTTPException(400, "only an LED strip has a path, of at least two points")
    for w in b.walls or []:
        if not all(0 <= v <= 1 for v in w.a + w.b):
            raise HTTPException(400, "a wall lies inside the frame")
    for o in b.openings or []:
        if not all(0 <= v <= 1 for v in o.a + o.b) or o.a == o.b:
            raise HTTPException(400, "a door or window has two different ends inside the frame")
        if o.b[0] < o.a[0] - END_TIE or (abs(o.b[0] - o.a[0]) <= END_TIE and o.b[1] < o.a[1]):
            o.a, o.b = o.b, o.a                               # a lies left of (or above) b: "left" and "right" mean the same on every wall
            o.open = {"left": "right", "right": "left"}.get(o.open, o.open)      # the same door: its hinge and its leaf stay where they were
            o.side = not o.side
    if len({o.id for o in b.openings or []}) != len(b.openings or []):
        raise HTTPException(400, "door and window ids must be unique")
    for ln in b.links:
        if ln.load not in loads:
            raise HTTPException(400, "a link starts at a load of the map")
        if ln.seg:
            n = len(loads[ln.load].path)
            if len(ln.seg) != 2 or not 0 <= ln.seg[0] < ln.seg[1] < n:
                raise HTTPException(400, "a part of a strip is two of its points, in order")


# ---- the map ----
@core.app.get("/api/map")
def map_get(request: Request):
    _owner(request)
    with _lock:
        d = _load()
        d["links"] = _links(d)
    d["load_types"] = list(LOAD_TYPES)
    d["ui_v"] = int((pathlib.Path(__file__).parent / "ui.js").stat().st_mtime)      # the build of the screen the page should be running
    d["scheme"] = d.get("canvas") or _scheme() or dict(CANVAS)       # {w, h}: the frame of every coordinate; without a drawing a blank one
    u = d.get("underlay")
    d["scheme_img"] = bool(u.get("show") and _underlay_file(u)) if u else _scheme() is not None   # a drawing is shown (the plan, or a scheme not moved yet)
    if u and u.get("removed"):                               # the owner removed the plan: the house's scheme does not come back under the zones
        d["underlay"] = None
    return d


@core.app.put("/api/map")
def map_put(body: MapBody, request: Request):
    """The whole map (the dashboard sends what it has; rev is the revision it started from: a stale one is refused with 409)."""
    _owner(request)
    _check(body)
    with _lock:
        cur = _load()
        if body.rev != cur["rev"]:
            raise HTTPException(409, f"the map changed meanwhile (revision {cur['rev']})")
        for r in body.rooms:
            r.name = core.check_name(r.name, "zone", most=40)   # zones of one name are one room: no uniqueness here
            if not _simple_polygon([p.model_dump() for p in r.poly]):
                raise HTTPException(400, f"the outline of '{r.name}' crosses itself or has no area")
            if r.walls and len(r.walls) != len(r.poly):
                raise HTTPException(400, f"'{r.name}': one wall mark per edge")
        allowed = {t["id"] for t in _things()} | {i["dev"] for i in cur["items"] if i["kind"] == "device"}      # what is already placed stays
        bad = [i.dev for i in body.items if i.kind == "device" and i.dev not in allowed]
        if bad:
            raise HTTPException(400, f"not a physical thing that can stand somewhere: {bad[0]}")
        items = [i.model_dump() for i in body.items]
        for i in items:
            if not i["path"]:
                del i["path"]                                   # only a strip has a path
            else:                                    # the handle of a strip (its icon, its room) is the middle of its path
                i["x"], i["y"] = i["path"][len(i["path"]) // 2]["x"], i["path"][len(i["path"]) // 2]["y"]
        was = {i["id"]: i.get("name") for i in cur["items"]}
        loads = [i for i in items if i.get("kind") == "load"]
        for i in loads:                                 # a new or renamed lamp: checked like every name
            if i.get("name") != was.get(i["id"]):
                i["name"] = core.check_name(i.get("name"), "lamp", [x.get("name") for x in loads if x is not i])
        res = _sync_rooms([{k: v for k, v in r.model_dump().items() if k != "walls" or v} for r in body.rooms], items, cur["unplaced"])
        new = {**cur, "rev": cur["rev"] + 1, "rooms": res["zones"], "unplaced": res["unplaced"],
               "items": items, "links": cur["links"] if core.wiring_owns("map") else [ln.model_dump() for ln in body.links]}
        if body.walls is not None:
            new["walls"] = [w.model_dump() for w in body.walls]
        if body.openings is not None:
            new["openings"] = [o.model_dump() for o in body.openings]
        new["openings"], dropped = _on_walls(new)
        _wiring_links([ln.model_dump() for ln in body.links])
        _save(new)
    return {"rev": new["rev"], "kept": res["kept"], "removed": res["removed"], "openings_dropped": dropped,
            "zones": [{"id": z["id"], "house_room": z["house_room"]} for z in res["zones"]]}


@core.app.put("/api/map/zones")
def map_zones(body: ZonesBody, request: Request):
    """The owner's data import: the zones (an outline and a name each) of the house, replacing the zones of the map; the dashboard rooms follow."""
    _owner(request)
    with _lock:
        cur = _load()
        old = {z["id"]: z for z in cur["rooms"]}
        marks = _edge_marks(cur)
        zones = []
        for i, z in enumerate(body.zones):
            zid = z.id or f"z{i}"
            poly = [p.model_dump() for p in z.poly]
            zones.append({"id": zid, "name": z.name, "house_room": (old.get(zid) or {}).get("house_room", ""), "poly": poly,
                          **({"walls": w} if (w := _marks_for(poly, marks, cur, zid)) else {})})
        if len({z["id"] for z in zones}) != len(zones):
            raise HTTPException(400, "zone ids must be unique")
        res = _sync_rooms(zones, cur["items"], cur["unplaced"])
        new = {**cur, "rev": cur["rev"] + 1, "rooms": res["zones"], "unplaced": res["unplaced"]}
        new["openings"], dropped = _on_walls(new)
        _save(new)
    return {"rev": new["rev"], "kept": res["kept"], "removed": res["removed"], "openings_dropped": dropped,
            "zones": [{"id": z["id"], "house_room": z["house_room"]} for z in res["zones"]]}


def _on_walls(d: dict):
    """The openings that still lie on a wall (a zone edge marked wall, or a free wall; both ends within a unit of its line, inside it);
    one whose wall went open, moved or was removed goes. -> (kept, how many went)."""
    fr = d.get("canvas") or _scheme() or CANVAS
    lines = [((p["x"] * fr["w"], p["y"] * fr["h"]), (q["x"] * fr["w"], q["y"] * fr["h"]))
             for z in d["rooms"] if len(z.get("walls") or []) == len(z["poly"])
             for i, (p, q) in enumerate(zip(z["poly"], z["poly"][1:] + z["poly"][:1])) if z["walls"][i]]
    lines += [((w["a"][0] * fr["w"], w["a"][1] * fr["h"]), (w["b"][0] * fr["w"], w["b"][1] * fr["h"])) for w in d.get("walls") or []]

    def on(pt, A, B):
        dx, dy = B[0] - A[0], B[1] - A[1]
        l2 = dx * dx + dy * dy
        if l2 < 1:
            return False
        t = ((pt[0] - A[0]) * dx + (pt[1] - A[1]) * dy) / l2
        return -0.01 <= t <= 1.01 and abs((pt[0] - A[0]) * dy - (pt[1] - A[1]) * dx) / math.sqrt(l2) <= 1
    kept = []
    for o in d.get("openings") or []:
        a, b = (o["a"][0] * fr["w"], o["a"][1] * fr["h"]), (o["b"][0] * fr["w"], o["b"][1] * fr["h"])
        if any(on(a, A, B) and on(b, A, B) for A, B in lines):
            kept.append(o)
    return kept, len(d.get("openings") or []) - len(kept)


def _edge_key(a: dict, b: dict, fr: dict) -> tuple:
    """An edge by its ends in frame units (to half a unit), whichever way it runs."""
    p = (round(a["x"] * fr["w"] * 2), round(a["y"] * fr["h"] * 2))
    q = (round(b["x"] * fr["w"] * 2), round(b["y"] * fr["h"] * 2))
    return (p, q) if p <= q else (q, p)


def _edge_marks(d: dict) -> list:
    """[(edge key, wall?, zone id, length)] of every marked zone edge of the map."""
    fr = d.get("canvas") or _scheme() or CANVAS
    out = []
    for z in d["rooms"]:
        w, pts = z.get("walls") or [], z["poly"]
        if len(w) == len(pts):
            for i in range(len(pts)):
                k = _edge_key(pts[i], pts[(i + 1) % len(pts)], fr)
                out.append((k, bool(w[i]), z["id"], math.hypot(k[1][0] - k[0][0], k[1][1] - k[0][1]) / 2))
    return out


def _pick(cands: list, zid: str):
    """One mark of the known edges that fit: the zone's own first, then the shortest; two that disagree there: open."""
    if not cands:
        return None
    own = [c for c in cands if c[2] == zid]
    pool = own or cands
    best = min(c[3] for c in pool)
    marks = {c[1] for c in pool if c[3] <= best + 0.5}
    return marks.pop() if len(marks) == 1 else False


def _on_old_edge(a, b, marks: list, zid: str = ""):
    """The mark of the known edge this one lies on (both ends within a unit of its line, overlapping it; see _pick), else None."""
    hits = []
    for (p, q), m, z, _ln in marks:
        P, Q = (p[0] / 2, p[1] / 2), (q[0] / 2, q[1] / 2)
        dx, dy = Q[0] - P[0], Q[1] - P[1]
        ln = math.hypot(dx, dy)
        if ln < 1:
            continue
        off = lambda x, y: abs((x - P[0]) * dy - (y - P[1]) * dx) / ln      # noqa: E731
        t = lambda x, y: ((x - P[0]) * dx + (y - P[1]) * dy) / ln / ln     # noqa: E731
        if off(*a) <= 1 and off(*b) <= 1 and min(max(t(*a), t(*b)), 1) - max(min(t(*a), t(*b)), 0) > 0.01:
            hits.append(((p, q), m, z, _ln))
    return _pick(hits, zid)


def _marks_for(poly: list, marks: list, d: dict, zid: str = "") -> list:
    """The wall marks of an imported outline: an edge the map had keeps its mark, a part of one (split, shortened) takes that edge's
    mark, any other is open (never a wall by default: the Opening tool snaps to walls); none known: unmarked ([])."""
    fr = d.get("canvas") or _scheme() or CANVAS
    pt = lambda p: (p["x"] * fr["w"], p["y"] * fr["h"])      # noqa: E731
    got = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        k = _edge_key(a, b, fr)
        exact = [c for c in marks if c[0] == k]
        own = [c for c in exact if c[2] == zid]
        m = _pick(own, zid) if own else _on_old_edge(pt(a), pt(b), [c for c in marks if c[2] == zid], zid)   # the zone's own edges first, both ways
        if m is None:
            m = _pick(exact, zid) if exact else _on_old_edge(pt(a), pt(b), marks, zid)
        got.append(m)
    return [bool(m) for m in got] if any(m is not None for m in got) else []


# ---- the scheme of the house ----
@core.app.get("/api/map/scheme")
def map_scheme(request: Request):
    """The house's scheme (an SVG drawing, checked) with a sandboxing Content-Security-Policy."""
    _owner(request)
    if _scheme() is None:
        raise HTTPException(404, "the house has no scheme: put scheme.svg into plugin-data/map")
    return FileResponse(core.plugin_data("map") / SCHEME_FILE, media_type="image/svg+xml",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


# ---- the plan of the flat under the zones (the underlay): a picture or an SVG the owner uploads, placed on the frame ----
UNDERLAY_MAX = 8 * 1024 * 1024
UNDERLAY_KINDS = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg"}


def _underlay_file(u: dict):
    f = core.plugin_data("map") / str(u.get("file") or "")
    return f if u.get("file") and f.is_file() else None


def _kind_of_bytes(raw: bytes):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return "svg" if _svg_ok(raw, UNDERLAY_MAX) else None


@core.app.get("/api/map/underlay")
def map_underlay(request: Request):
    """The underlay's file (an SVG is checked and sandboxed like the scheme)."""
    _owner(request)
    with _lock:
        u = _load().get("underlay") or {}
    f = _underlay_file(u)
    if f is None:
        raise HTTPException(404, "no plan of the flat")
    return FileResponse(f, media_type=UNDERLAY_KINDS.get(u.get("kind"), "application/octet-stream"),
                        headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


@core.app.put("/api/map/underlay")
async def map_underlay_put(request: Request, w: float = Query(0, ge=0, le=1e6, allow_inf_nan=False), h: float = Query(0, ge=0, le=1e6, allow_inf_nan=False)):
    """Upload the plan: the body is the file (PNG, JPEG or a static SVG); w, h its size in pixels. It is fitted into the frame, shown, 0.6 opaque."""
    _owner(request)
    raw = await request.body()
    if not raw or len(raw) > UNDERLAY_MAX:
        raise HTTPException(400, "the plan: a PNG, JPEG or SVG file up to 8 MB")
    kind = await run_in_threadpool(_kind_of_bytes, raw)          # an 8 MB SVG takes a second to check: not on the event loop
    if kind is None:
        raise HTTPException(400, "the plan: a PNG, JPEG or SVG file up to 8 MB")
    if kind == "svg":
        box = await run_in_threadpool(_svg_box, raw)
        if box is None:
            raise HTTPException(400, "the SVG plan has no size: it needs a viewBox or a width and a height")
        w, h = box["w"], box["h"]
    if not (0 < w <= 1e6 and 0 < h <= 1e6):
        raise HTTPException(400, "the plan's size is not known")
    return await run_in_threadpool(_underlay_store, raw, kind, w, h)


def _underlay_store(raw: bytes, kind: str, w: float, h: float) -> dict:
    """The uploaded plan written and placed (in the threadpool: the lock and an 8 MB write stay off the event loop)."""
    with _lock:
        d = _load()
        fr = d.get("canvas") or _scheme() or dict(CANVAS)
        k = min(fr["w"] / w, fr["h"] / h)
        name = f"underlay.{kind}"
        tmp = core.plugin_data("map") / f".{name}.tmp"
        tmp.write_bytes(raw)
        tmp.replace(core.plugin_data("map") / name)
        d["canvas"] = {"x": 0, "y": 0, "w": fr["w"], "h": fr["h"]}
        d["underlay"] = {"file": name, "kind": kind, "x": (fr["w"] - w * k) / 2, "y": (fr["h"] - h * k) / 2, "w": w * k, "h": h * k,
                         "rot": 0, "opacity": 0.6, "show": True, "vw": w, "vh": h, "v": uuid.uuid4().hex[:8]}
        d["rev"] += 1
        _save(d)
    return {"rev": d["rev"], "underlay": d["underlay"]}


class UnderlayPatch(core.Body):
    model_config = ConfigDict(extra="forbid")
    x: float | None = Field(default=None, ge=-100000, le=100000)
    y: float | None = Field(default=None, ge=-100000, le=100000)
    w: float | None = Field(default=None, gt=0, le=1000000)
    h: float | None = Field(default=None, gt=0, le=1000000)
    rot: float | None = Field(default=None, ge=-360, le=360)
    opacity: float | None = Field(default=None, ge=0, le=1)
    show: bool | None = None


@core.app.patch("/api/map/underlay")
def map_underlay_patch(body: UnderlayPatch, request: Request):
    """Where the plan lies on the frame (x, y, w, h in frame units, rot in degrees around its middle), how opaque, shown or not."""
    _owner(request)
    with _lock:
        d = _load()
        if not d.get("underlay") or d["underlay"].get("removed"):
            raise HTTPException(404, "no plan of the flat")
        d["underlay"].update({k: v for k, v in body.model_dump().items() if v is not None})
        d["rev"] += 1
        _save(d)
    return {"rev": d["rev"], "underlay": d["underlay"]}


@core.app.delete("/api/map/underlay")
def map_underlay_del(request: Request):
    """The plan goes (its file too, unless it is the house's scheme.svg); zones, walls and things stay. A mark stays that it was removed,
    so the house's scheme.svg does not come back under the zones."""
    _owner(request)
    with _lock:
        d = _load()
        u = d.get("underlay") or {}
        f = _underlay_file(u)
        if f is not None and f.name != SCHEME_FILE:
            f.unlink()
        d["underlay"] = {"removed": True}
        d["rev"] += 1
        _save(d)
    return {"rev": d["rev"]}


def _edges(d: dict) -> list:
    """Every zone edge in frame units: (zone index, edge index, (ax, ay), (bx, by))."""
    fr = d.get("canvas") or _scheme() or CANVAS
    out = []
    for zi, z in enumerate(d["rooms"]):
        pts = [(p["x"] * fr["w"], p["y"] * fr["h"]) for p in z["poly"]]
        out += [(zi, i, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    return out


def _axis(a, b):
    """'h' or 'v' for an edge that runs along an axis (within 3 degrees), else None; and its coordinate, its span."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dy) <= SLANT * abs(dx) and abs(dx) > 3:
        return "h", (a[1] + b[1]) / 2, tuple(sorted((a[0], b[0])))
    if abs(dx) <= SLANT * abs(dy) and abs(dy) > 3:
        return "v", (a[0] + b[0]) / 2, tuple(sorted((a[1], b[1])))
    return None, 0, (0, 0)


def _straighten(d: dict) -> int:
    """Edges within 3 degrees of horizontal or vertical and at least 30 units long (a curve's short pieces stay) become exactly so (both
    points take the edge's middle coordinate; a corner takes one from each of its edges). A zone that would cross itself keeps its points.
    -> how many edges were slanted."""
    fr = d.get("canvas") or _scheme() or CANVAS
    want: dict = {}
    n = 0
    for zi, i, a, b in _edges(d):
        ax, c, _sp = _axis(a, b)
        if not ax or math.hypot(b[0] - a[0], b[1] - a[1]) < STRAIGHT_MIN:
            continue
        k = 1 if ax == "h" else 0
        if abs(a[k] - b[k]) > 0.01:
            n += 1
        m = len(d["rooms"][zi]["poly"])
        for pi in (i, (i + 1) % m):
            want.setdefault((zi, pi, ax), []).append(c)
    for zi, z in enumerate(d["rooms"]):
        new = [dict(p) for p in z["poly"]]
        for pi, p in enumerate(new):
            if (zi, pi, "h") in want:
                p["y"] = round(sum(want[(zi, pi, "h")]) / len(want[(zi, pi, "h")]) / fr["h"], 6)
            if (zi, pi, "v") in want:
                p["x"] = round(sum(want[(zi, pi, "v")]) / len(want[(zi, pi, "v")]) / fr["w"], 6)
        if _simple_polygon(new):
            z["poly"] = new
    return n


def _at(e, t: float) -> float:
    """An axis edge's coordinate at t along its axis (a slanted one is measured where the other edge is, not at its middle)."""
    (ax, ay), (bx, by), axis = e[2], e[3], e[4]
    u0, u1, v0, v1 = (ax, bx, ay, by) if axis == "h" else (ay, by, ax, bx)
    return v0 if u1 == u0 else v0 + (v1 - v0) * (t - u0) / (u1 - u0)


def _tidy_shared_edges(d: dict) -> int:
    """Two zones drawn each on its side of a wall leave two parallel edges a few units apart (two wall lines). Edges along one axis, of
    different zones, at most 16 units apart (a wall's thickness, a slant) and overlapping, move onto one line (their length-weighted middle): a shared boundary is one
    edge; so do edges that continue each other end to end (at most 12 units apart both ways): one straight wall has no step at a joint. A zone whose outline would cross itself keeps its points. -> how many edges moved."""
    fr = d.get("canvas") or _scheme() or CANVAS
    _straighten(d)
    E = [(zi, i, a, b, *_axis(a, b)) for zi, i, a, b in _edges(d)]
    E = [e for e in E if e[4]]
    parent = list(range(len(E)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for x in range(len(E)):
        for y in range(x + 1, len(E)):
            e, f = E[x], E[y]
            lo, hi = max(e[6][0], f[6][0]), min(e[6][1], f[6][1])
            if e[0] == f[0] or e[4] != f[4]:
                continue
            if hi - lo > 3 and abs(_at(e, (lo + hi) / 2) - _at(f, (lo + hi) / 2)) <= JOIN_GAP:
                parent[find(x)] = find(y)                     # two sides of one wall
            elif -JOIN_END <= hi - lo <= 3 and abs(e[5] - f[5]) <= JOIN_END:
                parent[find(x)] = find(y)                     # one wall continued by the next room: no step at the joint
    groups: dict = {}
    for k in range(len(E)):
        groups.setdefault(find(k), []).append(E[k])
    want: dict = {}                                     # (zone, point, axis) -> [coordinates]
    moved = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        ln = [e[6][1] - e[6][0] for e in g]
        mid = sum(e[5] * l for e, l in zip(g, ln)) / sum(ln)
        for zi, i, _a, _b, ax, c, _s in g:
            n = len(d["rooms"][zi]["poly"])
            for pi in (i, (i + 1) % n):
                want.setdefault((zi, pi, ax), []).append(mid)
            moved += abs(c - mid) > 0.01
    for zi, z in enumerate(d["rooms"]):
        new = [dict(p) for p in z["poly"]]
        for pi, p in enumerate(new):
            if (zi, pi, "h") in want:
                p["y"] = round(sum(want[(zi, pi, "h")]) / len(want[(zi, pi, "h")]) / fr["h"], 6)
            if (zi, pi, "v") in want:
                p["x"] = round(sum(want[(zi, pi, "v")]) / len(want[(zi, pi, "v")]) / fr["w"], 6)
        if _simple_polygon(new):
            z["poly"] = new
    return moved


@core.app.post("/api/map/zones/tidy")
def map_zones_tidy(request: Request):
    """Neighbour zones drawn a wall apart share one edge ("Join neighbour walls")."""
    _owner(request)
    with _lock:
        d = _load()
        moved = _tidy_shared_edges(d)
        _resnap(d)
        d["openings"], dropped = _on_walls(d)
        d["rev"] += 1
        _save(d)
    return {"rev": d["rev"], "moved": moved, "openings_dropped": dropped}


def _resnap(d: dict) -> None:
    """After "Join neighbour walls": an opening its moved wall left behind goes onto the nearest wall line within JOIN_GAP units
    (both ends projected onto it), so it moves with its edge."""
    fr = d.get("canvas") or _scheme() or CANVAS
    lines = [((p["x"] * fr["w"], p["y"] * fr["h"]), (q["x"] * fr["w"], q["y"] * fr["h"]))
             for z in d["rooms"] if len(z.get("walls") or []) == len(z["poly"])
             for i, (p, q) in enumerate(zip(z["poly"], z["poly"][1:] + z["poly"][:1])) if z["walls"][i]]
    lines += [((w["a"][0] * fr["w"], w["a"][1] * fr["h"]), (w["b"][0] * fr["w"], w["b"][1] * fr["h"])) for w in d.get("walls") or []]
    kept, _n = _on_walls(d)
    for o in d.get("openings") or []:
        if o in kept:
            continue
        a, b = (o["a"][0] * fr["w"], o["a"][1] * fr["h"]), (o["b"][0] * fr["w"], o["b"][1] * fr["h"])
        best = None
        for A, B in lines:
            dx, dy = B[0] - A[0], B[1] - A[1]
            l2 = dx * dx + dy * dy
            if l2 < 1:
                continue
            t = lambda q: ((q[0] - A[0]) * dx + (q[1] - A[1]) * dy) / l2                            # noqa: E731
            ta, tb = sorted((t(a), t(b)))
            lo, hi = max(ta, 0.0), min(tb, 1.0)
            if (hi - lo) * math.sqrt(l2) < 4:                  # the wall must cover the opening (4 units at least), not only its line
                continue
            seg = lambda q, tq: math.hypot(q[0] - A[0] - dx * tq, q[1] - A[1] - dy * tq)            # noqa: E731
            off = max(seg(a, min(max(t(a), 0), 1)), seg(b, min(max(t(b), 0), 1)))                  # to the segment, not its infinite line
            if off <= JOIN_GAP and (best is None or off < best[0]):
                best = (off, A, dx, dy, min(max(t(a), lo), hi), min(max(t(b), lo), hi))
        if best:
            _off, A, dx, dy, ca, cb = best                # each end onto the wall on its own: the door keeps its own a -> b
            na = [round((A[0] + dx * ca) / fr["w"], 6), round((A[1] + dy * ca) / fr["h"], 6)]
            nb = [round((A[0] + dx * cb) / fr["w"], 6), round((A[1] + dy * cb) / fr["h"], 6)]
            if nb[0] < na[0] - END_TIE or (abs(nb[0] - na[0]) <= END_TIE and nb[1] < na[1]):
                na, nb = nb, na                           # really turned round on the plan: the same hinge and leaf, so the words flip
                o["open"] = {"left": "right", "right": "left"}.get(o.get("open"), o.get("open"))
                o["side"] = not o.get("side")
            o["a"], o["b"] = na, nb


def _migrate_scheme():
    """Once: the house's scheme.svg becomes the plan under the zones (hidden), its size the frame."""
    with _lock:
        d = _load()
        sch = _scheme()
        if d.get("underlay") or d.get("canvas") or sch is None:
            return
        d["canvas"] = {"x": 0, "y": 0, "w": sch["w"], "h": sch["h"]}
        d["underlay"] = {"file": SCHEME_FILE, "kind": "svg", "x": 0, "y": 0, "w": sch["w"], "h": sch["h"], "rot": 0, "opacity": 1, "show": False,
                         "vw": sch["w"], "vh": sch["h"]}
        d["rev"] += 1
        _save(d)
    core.log.warning("[map] scheme.svg is now the plan under the zones (hidden)")


def _migrate_geometry():
    """Once (geo 4): near-axis edges straightened, neighbour zones a wall apart get one shared edge. A house with no zones yet only takes
    geo 4: the zones drawn later are never moved by a restart."""
    with _lock:
        d = _load()
        if d.get("geo", 0) >= 4:
            return
        moved = _tidy_shared_edges(d) if d["rooms"] else 0
        d["geo"], d["rev"] = 4, d["rev"] + 1
        _save(d)
    core.log.warning(f"[map] geometry 4: {moved} shared edges joined")


# ---- what can be put on the map ----
def _panels() -> dict:
    mod = sys.modules.get("yashome_plugins.panels")
    return dict(getattr(mod, "_layouts", {}) or {}) if mod is not None else {}


def _things_cfg() -> dict:
    try:
        d = json.loads((core.plugin_data("map") / THINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _kind_of(did: str, d: dict) -> str:
    """What a registry device is on a wall: switch, dimmer, socket, lock, motion, climate (a thermometer), door, button, ac, tv, blaster, other."""
    codes = {c.get("code") for c in d.get("controls") or []}
    model = str(d.get("model") or "").lower()
    cat = d.get("category") or ""
    ir = sys.modules.get("yashome_plugins.ir")
    if cat == "tv":
        return "tv"
    if cat == "climate":
        return "ac"
    if d.get("ir_power"):                         # an IR fixture on a wall switch (a lamp with a fan): a lamp of the house
        return "lamp"
    if did.startswith("blaster:") or did in {b.get("node") for b in (getattr(ir, "BLASTERS", None) or [])}:
        return "blaster"
    if cat == "button":
        return "button"
    if cat == "sensor":
        if codes & {"occupancy", "presence"}:
            return "motion"
        if codes & {"contact", "door"}:
            return "door"
        return "climate" if codes & {"temperature", "humidity"} else "other"
    if cat == "switch":
        if "locked" in codes or "lock" in codes:
            return "lock"
        if any(str(c).startswith("brightness") for c in codes):
            return "dimmer"
        return "socket" if ("plug" in model or codes & {"power", "current", "voltage"}) else "switch"
    return "other"


def _room_label(v) -> str:
    """A room as the owner reads it: a satellite's `room` may hold a dashboard room id."""
    v = str(v or "")
    return next((str(r.get("name") or v) for r in core.get_rooms() or [] if r.get("id") == v), v)


def _dash_room_of(did: str) -> str:
    for r in core.get_rooms() or []:
        if did in (r.get("devices") or []):
            return str(r.get("name") or "")
    return ""


def _area(poly: list) -> float:
    return abs(sum(a["x"] * b["y"] - b["x"] * a["y"] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def _zone_of(x: float, y: float, zones: list):
    """The smallest zone (outline) that contains the point, or None."""
    best = None
    for z in zones:
        pts = [(p["x"], p["y"]) for p in z["poly"]]
        inside, j = False, len(pts) - 1
        for i in range(len(pts)):
            if ((pts[i][1] > y) != (pts[j][1] > y)) and x < (pts[j][0] - pts[i][0]) * (y - pts[i][1]) / (pts[j][1] - pts[i][1] or 1e-12) + pts[i][0]:
                inside = not inside
            j = i
        if inside and (best is None or _area(z["poly"]) < _area(best["poly"])):
            best = z
    return best


def _room_referenced(room: dict) -> bool:
    """Something that names the room (by id or name) keeps it from disappearing: a rule, a binding, a favourite, the order of its tab, a satellite."""
    voice = sys.modules.get("yashome_plugins.voice")
    sats = [{k: v for k, v in x.items() if "room" in k} for x in (getattr(voice, "_sats", lambda: [])() or [])]
    blob = json.dumps([core.cfg_section(k, []) for k in ("automations", "bindings", "favorites", "tab_order")] + [sats], ensure_ascii=False)
    return any(json.dumps(k, ensure_ascii=False) in blob for k in (room.get("id"), "room:" + str(room.get("id")), room.get("name")) if k)


def _new_room_id(taken: set) -> str:
    rid = "z" + uuid.uuid4().hex[:10]
    return rid if rid not in taken else _new_room_id(taken)


def _sync_rooms(zones: list, items: list, unplaced=()) -> dict:
    """The map's zones ARE the dashboard rooms, and so are its rooms with no outline yet (unplaced). A zone takes the room it names (the same name
    keeps the room's id, so rules, voice and favourites keep working) or makes a new one; a device standing in a zone is in that room only; an
    unplaced room whose room got an outline leaves the list; a room with neither stays while a device is left in it and goes when it holds none
    (while something names it, it stays without a tab); with no zone drawn no room goes. Nothing drawn or unplaced: the rooms are left alone.
    -> {zones, unplaced, kept: [rooms kept because something names them], removed: [rooms that went]}"""
    kept, removed = [], []
    unplaced = [dict(u) for u in unplaced or ()]
    if not zones and not unplaced:
        core.set_room_locked((), (), ())
        return {"zones": zones, "unplaced": [], "kept": kept, "removed": removed}
    placed, things = [], set()
    for i in items:
        if i["kind"] == "device":
            if i["dev"] not in core.DEVICES:             # a thing that is no device of the house (an IR blaster): no room member
                things.add(i["dev"])
                continue
            z = _zone_of(i["x"], i["y"], zones)
            if z is not None:
                placed.append((i["dev"], z))

    def apply(rooms):
        by_id = {r["id"]: r for r in rooms}
        by_name = {str(r["name"]).strip().lower(): r for r in rooms}
        wanted = {}                                              # room id -> the name its zone gives it
        for z in zones + unplaced:
            z["name"] = z["name"].strip()
            r = by_id.get(z.get("house_room")) or by_name.get(z["name"].lower())
            if r is None:
                r = {"id": _new_room_id(set(by_id)), "name": z["name"], "devices": []}
                rooms.append(r)
                by_id[r["id"]] = r
                by_name[z["name"].lower()] = r
            z["house_room"] = r["id"]
            wanted.setdefault(r["id"], z["name"])               # two outlines of one room: the first name
        names = {}                                               # the names after the renames: no two alike (swapping two names is fine)
        for r in rooms:
            n = wanted.get(r["id"], r["name"])
            if n.lower() in names and names[n.lower()] != r["id"] and (r["id"] in wanted or names[n.lower()] in wanted):
                raise HTTPException(409, f"a room named '{n}' exists already")
            names[n.lower()] = r["id"]
        for r in rooms:
            r["name"] = wanted.get(r["id"], r["name"])
        for r in rooms:                                         # what an older sync put there
            if things & set(r["devices"]):
                r["devices"] = [d for d in r["devices"] if d not in things]
        for dev, z in placed:                                   # exactly one room: where the icon stands
            for r in rooms:
                if r["id"] == z["house_room"]:
                    if dev not in r["devices"]:
                        r["devices"].append(dev)
                elif dev in r["devices"]:
                    r["devices"].remove(dev)
        zone_rooms = {z["house_room"] for z in zones}
        unplaced[:] = [u for u in unplaced if u["house_room"] not in zone_rooms]     # drawn now: the outline is its place
        loose = {u["house_room"] for u in unplaced}             # no outline yet: always a tab, so it can be found
        hidden = set()
        for r in list(rooms):
            if zones and r["id"] not in zone_rooms | loose and not r["devices"]:   # no zone, no device: the map has no place for it (none drawn: none goes)
                if _room_referenced(r):
                    kept.append({"room": r["name"], "why": "a rule, a favourite or a satellite names it"})
                    hidden.add(r["id"])                            # kept for what names it, but no tab
                else:
                    rooms.remove(r)
                    removed.append(r["name"])
        core.set_room_locked({dev for dev, _ in placed}, zone_rooms, hidden)       # under the lock the room API reads them with
    core.rooms_edit(apply)
    try:                                                         # the rooms live in settings.json: a copy beside the map keeps what the map changed recoverable
        core.atomic_json_dump(core.plugin_data("map") / "rooms.json", core.get_rooms(), indent=1, ensure_ascii=False)
    except OSError as e:
        core.log.warning(f"[map] rooms snapshot not saved: {e}")
    return {"zones": zones, "unplaced": unplaced, "kept": kept, "removed": removed}


def _lock_from_map() -> None:
    """After a start: the placed devices that stand in a zone keep their room from the map, and the zones' rooms stay."""
    d = _load()
    if d["rooms"] or d["unplaced"]:
        zone_rooms = {z.get("house_room") for z in d["rooms"] if z.get("house_room")}
        loose = {u.get("house_room") for u in d["unplaced"]}
        core.set_room_locked((i["dev"] for i in d["items"] if i["kind"] == "device" and _zone_of(i["x"], i["y"], d["rooms"]) is not None), zone_rooms,
                             (r["id"] for r in core.get_rooms() if r["id"] not in zone_rooms | loose and not r.get("devices") and _room_referenced(r)))


# ---- the rooms are the map's: made, renamed and deleted here (the core's room routes hand them over: ROOMS_OWNER, docs/plugins.md) ----
class RoomName(core.Body):
    name: str = Field(min_length=1, max_length=40)


def _named(d: dict, name: str, rid: str = "") -> bool:
    n = name.strip().lower()
    return any(str(r.get("name", "")).strip().lower() == n and r.get("id") != rid for r in core.get_rooms()) or \
        any(z["name"].strip().lower() == n and z.get("house_room") != rid for z in d["rooms"] + d["unplaced"])


def create_room(name: str) -> dict:
    """A room with no outline yet (drawn later on the map, or never): an unplaced zone and its dashboard room. -> the room."""
    name = name.strip()
    with _lock:
        d = _load()
        if not name or _named(d, name):
            raise HTTPException(409, f"a room named '{name}' exists already")
        d["unplaced"].append({"id": "u" + uuid.uuid4().hex[:10], "name": name, "house_room": ""})
        res = _sync_rooms(d["rooms"], d["items"], d["unplaced"])
        d.update(rev=d["rev"] + 1, rooms=res["zones"], unplaced=res["unplaced"])
        _save(d)
        rid = d["unplaced"][-1]["house_room"]
    return next(r for r in core.get_rooms() if r["id"] == rid)


def rename_room(rid: str, name: str) -> dict:
    """The room's new name: its outline (or its unplaced entry) carries it."""
    name = name.strip()
    with _lock:
        d = _load()
        mine = [z for z in d["rooms"] + d["unplaced"] if z.get("house_room") == rid]
        if not mine and not any(r["id"] == rid for r in core.get_rooms()):
            raise HTTPException(404, "unknown room")
        if not name or _named(d, name, rid):
            raise HTTPException(409, f"a room named '{name}' exists already")
        if mine:
            for z in mine:
                z["name"] = name
            res = _sync_rooms(d["rooms"], d["items"], d["unplaced"])
            d.update(rev=d["rev"] + 1, rooms=res["zones"], unplaced=res["unplaced"])
            _save(d)
        else:                                                    # kept only for what names it (no tab): the name is the core's alone
            core.rooms_edit(lambda rooms: [r.update(name=name) for r in rooms if r["id"] == rid])
    return next(r for r in core.get_rooms() if r["id"] == rid)


def delete_room(rid: str) -> None:
    """The room goes, with its outline on the map; its devices are in no room after it."""
    with _lock:
        d = _load()
        if not any(z.get("house_room") == rid for z in d["rooms"] + d["unplaced"]) and not any(r["id"] == rid for r in core.get_rooms()):
            raise HTTPException(404, "unknown room")
        d["rooms"] = [z for z in d["rooms"] if z.get("house_room") != rid]
        d["unplaced"] = [u for u in d["unplaced"] if u.get("house_room") != rid]
        core.rooms_edit(lambda rooms: rooms.__setitem__(slice(None), [r for r in rooms if r["id"] != rid]))
        res = _sync_rooms(d["rooms"], d["items"], d["unplaced"])
        d.update(rev=d["rev"] + 1, rooms=res["zones"], unplaced=res["unplaced"])
        d["openings"], _ = _on_walls(d)
        _save(d)


@core.app.post("/api/map/rooms")
def map_room_new(body: RoomName, request: Request):
    _owner(request)
    return {"ok": True, "room": create_room(body.name)}


@core.app.put("/api/map/rooms/{rid}")
def map_room_rename(rid: str, body: RoomName, request: Request):
    _owner(request)
    return {"ok": True, "room": rename_room(rid, body.name)}


@core.app.delete("/api/map/rooms/{rid}")
def map_room_delete(rid: str, request: Request):
    _owner(request)
    delete_room(rid)
    return {"ok": True}


def _migrate_rooms() -> None:
    """Once: a dashboard room the map has no zone for becomes an unplaced room of the map (map.json and the rooms as they were backed up first,
    with their sha256, into plugin-state/map/rooms-migration-<UTC>/; plugin-data/map/.rooms-migrated marks it done)."""
    mark = core.plugin_data("map") / ".rooms-migrated"
    if mark.exists():
        return
    with _lock:
        d = _load()
        known = {z.get("house_room") for z in d["rooms"] + d["unplaced"]}
        loose = [r for r in core.get_rooms() if r["id"] not in known]
        try:                                                     # a house it cannot write (read-only): next start again
            out = ""
            if loose:
                out = core.plugin_state("map") / f"rooms-migration-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
                out.mkdir(parents=True, exist_ok=True)
                lines = []
                for name, data in (("map.json", _file().read_bytes() if _file().is_file() else None),
                                   ("rooms.json", json.dumps(core.get_rooms(), indent=1, ensure_ascii=False).encode())):
                    if data is not None:
                        (out / name).write_bytes(data)
                        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
                (out / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                d["unplaced"] += [{"id": "u" + uuid.uuid4().hex[:10], "name": r["name"], "house_room": r["id"]} for r in loose]
                d["rev"] += 1
                _save(d)
                core.log.info(f"[map] {len(loose)} rooms of the dashboard taken over as rooms with no outline yet; backup in {out}")
            core.atomic_json_dump(mark, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "rooms": [r["name"] for r in loose], "backup": str(out)})
        except OSError as e:
            core.log.warning(f"[map] rooms not taken over yet, next start again: {e}")


def _rename_refs(d: dict, things: dict, old: str, new: str) -> dict:
    """A device key that becomes another (a rename, a merge): its icons point at the new one (an icon of the new one is
    there already: the old one's goes, one thing has one icon); things.json's entries and members too."""
    n = {"icons": 0, "things": 0}
    has_new = any(i.get("dev") == new for i in d.get("items") or [])
    items = []
    for i in d.get("items") or []:
        if i.get("dev") == old:
            n["icons"] += 1
            if has_new:
                continue
            i = {**i, "dev": new}
        items.append(i)
    d["items"] = items
    for t in things.get("things") or []:
        if isinstance(t, dict) and t.get("dev") == old:
            t["dev"] = new
            n["things"] += 1
        if isinstance(t, dict) and old in (t.get("members") or []):
            t["members"] = [m for m in t["members"] if m not in (old, t.get("dev"))]
            n["things"] += 1
    return n


def _on_rename(old: str, new: str):
    with _lock:
        d, things = _load(), _things_cfg()
        n = _rename_refs(d, things, old, new)
        if n["icons"]:
            d["rev"] = int(d.get("rev") or 0) + 1
            _save(d)
        if n["things"]:
            core.atomic_json_dump(core.plugin_data("map") / THINGS_FILE, things, indent=1, ensure_ascii=False)


def _merge_plan(old: str, new: str) -> dict:
    with _lock:
        n = _rename_refs(_load(), _things_cfg(), old, new)
    return {f"map: {k}": v for k, v in n.items() if v}


core.ON_RENAME.append(_on_rename)
core.MERGE_PLAN.append(_merge_plan)
core.ROOMS_OWNER.update(create=create_room, rename=rename_room, delete=delete_room)     # the core's room routes hand over to these
_migrate_rooms()
_lock_from_map()


def _things() -> list:
    """The physical things the owner may put on the map, one entry per thing under its human name: a TV is one entry with its remote paths as
    members, a satellite one entry with its microphone and speaker. Remotes, appliance cards, tags, people, voice cards of phones and panels are
    not things: they have no place. Kinds and merges of the house come from plugin-data/map/things.json; the name is the device's own (one
    name everywhere), a blaster without a device has the IR plug-in's, and only a panel takes a "label" from there."""
    cfg = {str(t.get("dev")): t for t in _things_cfg().get("things") or [] if isinstance(t, dict) and t.get("dev")}
    members = {m: t["dev"] for t in cfg.values() for m in t.get("members") or []}
    out, seen = [], set()

    def add(did, name, kind, room, online, mem=(), detail=""):
        if did in seen:
            return
        seen.add(did)
        label = str((cfg.get(did) or {}).get("label") or "") if did.startswith("panel:") else ""
        out.append({"id": did, "name": name, "category": kind, "kind": "panel" if did.startswith("panel:") else "device", "room": room,
                    "online": online, "members": list(mem), "detail": detail, **({"label": label} if label else {})})
    for did, d in list(core.DEVICES.items()):
        if did in members:
            continue
        c = cfg.get(did) or {}
        if d.get("voice_role") or d.get("category") == "voice" or (d.get("transport") in VIRTUAL_TRANSPORTS and not c and not d.get("ir_power")
                                                                     and not d.get("ir_blaster")):          # a blaster's own device stands there
            continue
        reg = d.get("name") or did
        st = core.status_copy(did, {})
        add(did, str(reg), str(c.get("kind") or _kind_of(did, d)), _room_label(d.get("room")) or _dash_room_of(did), st.get("online") if "online" in st else None,
            c.get("members") or ())
    voice = sys.modules.get("yashome_plugins.voice")
    for s in list(getattr(voice, "_sats", lambda: [])() or []):
        if s.get("phone") or s.get("panel") or s.get("id", "") + "_mic" not in core.DEVICES:
            continue
        mic = s["id"] + "_mic"
        c = cfg.get(mic) or {}
        add(mic, str(core.DEVICES[mic].get("name") or s.get("name") or s["id"]), str(c.get("kind") or "speaker"), _room_label(s.get("room")) or _dash_room_of(mic),
            core.status_copy(mic, {}).get("online"), [s["id"] + "_spk"])
    ir = sys.modules.get("yashome_plugins.ir")
    for b in list(getattr(ir, "BLASTERS", None) or []):
        node = str(b.get("node") or "")
        if node and node not in core.DEVICES and "blaster:" + node not in core.DEVICES:      # an older IR plug-in: no device of its own
            add("blaster:" + node, str(ir._blaster_name(node)), "blaster", "", bool(ir._node_online(node)))
    for name in _panels():
        add(f"panel:{name}", name, "panel", "", None)
    order = ("lamp", "switch", "dimmer", "socket", "lock", "ac", "tv", "blaster", "motion", "climate", "door", "button", "speaker", "panel", "other")
    return sorted(out, key=lambda x: (order.index(x["category"]) if x["category"] in order else len(order), str(x["name"]).lower()))


@core.app.get("/api/map/parts")
def map_parts(request: Request):
    """{primary: [member ids]} of the merges in things.json: a member (the remote of a TV or an air conditioner) is a part of its primary, so
    the dashboard shows it inside the primary's window and not as a card of its own."""
    _owner(request)
    return {str(t["dev"]): [m for m in t["members"] if m in core.DEVICES] for t in _things_cfg().get("things") or []
            if isinstance(t, dict) and t.get("dev") and isinstance(t.get("members"), list) and t["members"]}


@core.app.get("/api/map/candidates")
def map_candidates(request: Request):
    """The physical things that may be placed: [{id, name, category (the kind), room, online, members, detail}]. A thing that is not
    physical (a remote card, a tag, a person) cannot be placed: the PUT of the map refuses it."""
    _owner(request)
    return _things()


# ---- the links the house already knows (read only, computed on demand) ----
@core.app.get("/api/map/auto")
def map_auto(request: Request):
    """[{from, to, via, note}]: ids are registry ids (or panel:<name>); the dashboard draws the ones whose two ends are on the map."""
    _owner(request)
    out, seen = [], set()

    def add(a, b, via, note=""):
        if a and b and a != b and (a, b, via) not in seen:
            seen.add((a, b, via))
            out.append({"from": a, "to": b, "via": via, "note": note})
    with core.bindings_lock:
        for src, codes in core.bindings.items():
            for code, gestures in codes.items():
                for g, b in gestures.items():
                    tgt = b.get("target")
                    if tgt in core.DEVICES:
                        add(src, tgt, "rule", f"{code} {g}")
    for name, lay in _panels().items():
        for w in lay.get("widgets") or []:
            for k in ("cmd_topic", "dim_topic", "power_cmd", "temp_cmd", "mode_cmd", "fan_cmd"):
                m = re.fullmatch(r"(?:home/cmd|panel/[^/]+/cmd)/([^/]+)/([^/]+)", str(w.get(k) or ""))
                if m and m.group(1) in core.DEVICES:
                    add(f"panel:{name}", m.group(1), "panel", m.group(2))
    ir = sys.modules.get("yashome_plugins.ir")
    for a in list(getattr(ir, "APPLIANCES", None) or []) if ir is not None else []:
        if not isinstance(a, dict) or not a.get("blaster"):
            continue
        tgt = a.get("device") if a.get("device") in core.DEVICES else a.get("id") if a.get("id") in core.DEVICES else None   # an IR-only appliance is its own device
        src = str(a["blaster"]) if str(a["blaster"]) in core.DEVICES else f"blaster:{a['blaster']}"                     # a bare IR node: its blaster card
        if tgt:
            add(src, tgt, "ir", str(a.get("name") or ""))
    return out


# ---- the facts for the voice: where things are and what controls them ----
def _room_of(item: dict, rooms: list) -> str:
    x, y = item["x"], item["y"]
    for r in rooms:
        pts = [(p["x"], p["y"]) for p in r.get("poly") or []]
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            if ((pts[i][1] > y) != (pts[j][1] > y)) and x < (pts[j][0] - pts[i][0]) * (y - pts[i][1]) / (pts[j][1] - pts[i][1] or 1e-12) + pts[i][0]:
                inside = not inside
            j = i
        if inside:
            return r["name"]
    return ""


@core.app.get("/api/map/facts")
def map_facts(request: Request):
    """Plain lines for the voice model: "<load name> (<room>, <type>): controlled by <device> / <channel>". Only what the owner put on the map."""
    _owner(request)
    items = facts_items()
    return {"lines": [i["text"] for i in items], "items": items}


def facts() -> list:
    return [i["text"] for i in facts_items()]


def facts_items() -> list:
    """[{text, load, controlled}]: a line per load (controlled = a device is linked to it) and per placed device that lies in a room."""
    d = _load()
    rooms = d["rooms"]
    devs = core.DEVICES
    links = _links(d)
    out = []
    for it in d["items"]:
        room = _room_of(it, rooms)
        if it["kind"] == "load":
            by = []
            for ln in links:
                if ln["load"] == it["id"] and ln["dev"] in devs:
                    ctl = next((c for c in devs[ln["dev"]].get("controls") or [] if c.get("code") == ln["code"]), {})
                    part = f" (part {ln['seg'][0] + 1}-{ln['seg'][1] + 1} of the strip)" if ln.get("seg") else ""
                    by.append(f"{devs[ln['dev']].get('name') or ln['dev']} / {ctl.get('label') or ln['code']}".strip(" /") + part)
            what = "LED strip" if it.get("path") else ""
            inner = ", ".join(x for x in (room, "" if what else it["type"]) if x)
            out.append({"text": f"{what + ' ' if what else ''}{it['name']}" + (f" ({inner})" if inner else "") + (f": controlled by {'; '.join(by)}" if by else ""),
                        "load": True, "controlled": bool(by)})
        elif it["dev"] in devs and room:
            out.append({"text": f"{devs[it['dev']].get('name') or it['dev']} is in {room}", "load": False, "controlled": False})
    return out


def forget_thing(dev: str) -> int:
    """A thing that is gone (a removed IR blaster): its icons and their links leave the map. -> how many icons went."""
    with _lock:
        d = _load()
        gone = {i["id"] for i in d["items"] if i.get("kind") == "device" and i.get("dev") == dev}
        if not gone:
            return 0
        d["items"] = [i for i in d["items"] if i["id"] not in gone]
        links = [ln for ln in _links(d) if ln.get("load") not in gone and ln.get("dev") != dev]
        if not core.wiring_owns("map"):
            d["links"] = links
        d["rev"] += 1
        _wiring_links(links)
        _save(d)
    return len(gone)


# ---- the wiring: a load's links are wiring entries (core wiring.json); map.json's "links" is read only until it is taken over ----
def _link_entry(ln: dict) -> dict:
    return {"switch": {"dev": ln["dev"], "code": ln.get("code") or ""}, "target": {"load": ln["load"], "seg": list(ln.get("seg") or [])},
            "note": ln.get("note") or ""}


def _link_key(dev, code, load, seg) -> tuple:
    return dev, code or "", load, tuple(seg or ())


def _wiring_links(links: list) -> None:
    """The map's links become the wiring's load entries (an entry kept keeps its id); with a broken wiring list only map.json has them."""
    def change(ws):
        old = {_link_key(e["switch"]["dev"], e["switch"]["code"], e["target"].get("load"), e["target"].get("seg")): e["id"]
               for e in ws if e["target"].get("load")}
        new = []
        for ln in links:
            k = _link_key(ln["dev"], ln.get("code"), ln["load"], ln.get("seg"))
            new.append({**_link_entry(ln), **({"id": old[k]} if k in old else {})})
        return [e for e in ws if not e["target"].get("load")] + new
    try:
        core.wiring_edit(change, check=False)
    except core.WiringBroken as e:
        core.log.warning(f"[map] the links are kept in map.json only: {e}")


def _links(d: dict) -> list:
    """The links of the map: the wiring's load entries; map.json's own while the wiring does not speak for the map."""
    if not core.wiring_owns("map"):
        return d["links"]
    return [{"load": e["target"]["load"], "dev": e["switch"]["dev"], "code": e["switch"]["code"], "note": e.get("note") or "",
             "seg": list(e["target"].get("seg") or [])} for e in core.wiring() if e["target"].get("load")]


def _wiring_old() -> list:
    """WIRING_MIGRATORS: the links map.json kept before the wiring list."""
    return [_link_entry(ln) for ln in _load()["links"] if isinstance(ln, dict) and ln.get("load") and ln.get("dev")]


core.WIRING_MIGRATORS.append({"name": "map", "files": [_file()], "entries": _wiring_old})


def _move_stale_links() -> int:
    """map.json's links, once the wiring speaks for the map: a pair the wiring lacks moves into it (logged), then the file keeps
    none, so the wires have one source. -> how many moved."""
    if not core.wiring_owns("map"):
        return 0
    d = _load()
    old = [ln for ln in d.get("links") or [] if isinstance(ln, dict) and ln.get("load") and ln.get("dev")]
    if not d.get("links"):
        return 0
    have = {_link_key(e["switch"]["dev"], e["switch"].get("code"), e["target"].get("load"), e["target"].get("seg"))
            for e in core.wiring() if e["target"].get("load")}
    miss = [ln for ln in old if _link_key(ln["dev"], ln.get("code"), ln["load"], ln.get("seg")) not in have]
    if miss:
        core.wiring_edit(lambda ws: ws + [_link_entry(ln) for ln in miss], check=False)
    d["links"] = []
    _save(d)
    core.log.warning(f"[map] map.json links moved into the wiring: {len(miss)} new, {len(old) - len(miss)} already there; the file keeps none")
    return len(miss)


def _migrate_thing_names():
    """A "name" in things.json (the map's own name of a thing, before one name lived on the device) moves to a device that has no name of
    its own, or to a blaster with neither, and only then leaves the file; one that found no taker stays. A panel keeps its "label"."""
    f = core.plugin_data("map") / THINGS_FILE
    cfg = _things_cfg()
    things = [t for t in cfg.get("things") or [] if isinstance(t, dict)]
    named = [t for t in things if t.get("name") and not str(t.get("dev", "")).startswith("panel:")]
    if not named:
        return
    ir = sys.modules.get("yashome_plugins.ir")
    gone = 0
    for t in named:
        dev, name = str(t.get("dev") or ""), str(t["name"]).strip()
        d = core.DEVICES.get(dev)
        if dev.startswith("blaster:") and ir is not None:
            b = ir._blaster(dev[len("blaster:"):])
            did = ir._blaster_device(b["node"]) if b is not None else None
            d, dev = (core.DEVICES.get(did), did) if did else (None, dev)
            if b is not None and not did:
                if not b.get("name"):
                    b["name"] = name
                    ir._save()
                t.pop("name")
                gone += 1
                continue
        if d is not None:
            if not d.get("name") or d.get("name") == dev:
                core.set_device_name(dev, name)
            t.pop("name")                                 # the device is there: its own name or this one, nothing is lost
            gone += 1
    if gone:
        core.atomic_json_dump(f, cfg, indent=1, ensure_ascii=False)
    core.log.warning(f"[map] things.json: {gone} of {len(named)} names settled on their devices, the rest wait for theirs")


async def _thing_names_task():
    """At start, once every plug-in's devices are in (Zigbee and webOS come later than the plug-ins load): the names move,
    and map.json's leftover links move into the wiring."""
    if await run_in_threadpool(core.wait_registry, 300):
        await run_in_threadpool(_migrate_thing_names)
        await run_in_threadpool(_move_stale_links)
    else:
        core.log.warning("[map] the device lists are not all in after 5 min: things.json names are left as they are")


_migrate_scheme()
_migrate_geometry()
core.STARTUP_TASKS.append(_thing_names_task)
