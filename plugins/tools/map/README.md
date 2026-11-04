# map - the floor plan of the house

OWNER ONLY (the house token / the dashboard login): every route answers 401 or 403 to anyone else (a full-access family phone included) and the
dashboard shows the tab "Home map" only when the first call succeeds. The same page is Settings -> House -> Home map.

Setup: turn `map` on in Settings -> Maintenance -> Plug-ins and update; optionally put the house's drawing in
`plugin-data/map/scheme.svg` (below) or upload a plan of the flat on the map. Files: `__init__.py` (the routes below), `ui.js`
and `i18n.json` (the page).

## The scheme

The one background of the map is the house's own SCHEME: `plugin-data/map/scheme.svg` (house data, not part of the product), a static drawing whose
viewBox is the frame of every coordinate (0..1). The plug-in uses it only when it holds no script, no event attribute, no `foreignObject`/`image`/
`use`/`style`/`a`/animation, no external reference and no entity (it is checked every time it is read and served with a sandboxing
`Content-Security-Policy`; `<defs>`, filters and gradients are fine). Its walls are in `<g data-walls>` (or `shape-rendering="crispEdges"`): strip
points and dropped things snap to them, to every zone edge and to the edges of shapes marked <data-snap> (garden, fences); the nearest edge within about 10 screen pixels wins and is highlighted while a point is dragged. Without the file (or with one that fails the check) the map is a blank grid in a 1000x700 frame and works the same: zones are drawn by
hand, things placed, strips drawn; snapping then uses the zone edges.

The PLAN OF THE FLAT ("Plan of the flat" in the map bar) is a picture under the zones and nothing else: nothing is read off it, the owner
draws the walls. A photo or drawing (PNG, JPG or a static SVG checked like the scheme, up to 8 MB, 200000 elements and 64 levels, checked
off the event loop; an SVG's size is its viewBox, else its width and height) is uploaded there (`PUT /api/map/underlay?w=&h=`, the body is the file; w and h finite, at most 1e6), fitted into the
frame, then shown or hidden, made see-through, sized and turned about its middle by sliders and moved by a drag ("Move it", then Done;
`PATCH /api/map/underlay` with x, y, w, h in frame units, rot, opacity, show), or removed (`DELETE`; zones, walls and things stay, and a mark
stays that it was removed, so the house's scheme.svg does not come back under the zones). Its file is served by `GET /api/map/underlay`.
An SVG plan that is shown and not turned is also a snap target (its wall group and data-snap shapes). "Join neighbour walls" in the same
sheet (`POST /api/map/zones/tidy`) gives rooms drawn each on its side of a wall one shared edge: edges along one axis of different zones,
at most 16 units apart and overlapping, move onto their length-weighted middle, and so do edges that continue each other end to end (at most
12 units apart both ways: one straight wall has no step at a joint); before that, edges within 3 degrees of an axis and at least 30 units
long are made exactly horizontal or vertical (a curve's short pieces stay); a zone that would cross itself keeps its points (the numbers are
named at the top of `__init__.py`). The same tidy runs once at a start while `geo` in map.json is below 4, then `geo` becomes 4; a
house with no zones yet takes geo 4 at once, so zones drawn later are never moved by a restart. Names of icons keep off the wall lines, doors and windows.

WALLS INSIDE A ROOM: "Walls" in the map bar is a tool: tap, tap draws a wall (snapping to 0/90 degrees, zone edges and other walls; the room
stays one zone); a tap on a free wall removes it (with the doors and windows on it); Undo / Redo, Done. They are `walls` ([{id, a, b}], 0..1)
of map.json.

DOORS and WINDOWS: "Opening" in the map bar is a tool: a stroke drawn with the mouse or a finger near a wall (a zone edge marked as a wall,
or a free wall; both ends within 30 screen pixels of it) lands on that wall, and its sheet asks Window or Door; a door opens Sliding, To the
left or To the right, and "The other way" puts its leaf on the other side of the wall; a tap on a door or a window (in this tool or in Walls)
opens the same sheet (change, flip, remove). A stroke away from walls says so in the bar; two fingers still pinch. A window is drawn as a
gap with a thin double line, a swing door as its leaf and a quarter circle, a sliding door as a leaf beside the gap with an arrow. They are
`openings` ([{id, a, b: its ends on the wall's line, 0..1 of the frame; kind door|window; open left|right|slide; side}]) of map.json: "left"
hangs the door at a, the end that lies left of (or above) b. An opening keeps its place whatever happens to the zones (an import renumbers
their edges); one stored by zone and edge index instead is dropped when the map is read. One that no longer lies on a wall (its edge
was made open, moved or deleted, its free wall removed) goes at the next save or import (`openings_dropped` in the answer); ids are
unique and a, b are stored left to right (top to bottom) whatever the page sent (a swapped pair keeps its hinge and leaf: "left" and
"right" and the side flip with it). "Join neighbour walls" moves the openings with their edge. The page drops an opening whose wall went
(a room deleted, merged or redrawn) and says how many went. A page that does not send `walls` or `openings`
leaves them as they are. Once, at start, a house's scheme.svg becomes this plan (hidden), its size the frame (`canvas` in map.json).

## Things

The owner taps an empty spot ("What is here?") and picks what stands there. Only PHYSICAL things can be placed: one entry per thing, under its
human name with its kind, room and online state; the list is grouped by kind (switch, dimmer, socket, lock, AC, TV, IR blaster, motion, thermometer,
door, button, speaker, panel, other), searchable by name and room, and the counter counts only these. Remotes, appliance cards, NFC tags, people and
voice cards of phones and panels are no things; the PUT of the map refuses to place them (what is placed already stays). Kinds and merges
of a house are in `plugin-data/map/things.json`: `{"things": [{"dev", "kind", "members": [ids]}]}` - a TV with the remote that drives it is
one thing, an IR blaster that has no registry device is `blaster:<node>`, a satellite is one thing with its microphone and speaker. A thing
is shown under the one name of its device (renamed on its card; a blaster without a device under its name in the IR remotes tab); only a
panel takes a short `label` from there. An IR fixture with a power feed (IR plug-in, "Power from": a lamp with a fan on a
wall switch) is a lamp of the map without any entry there: lit while its switch is on, its badge the keys it last sent, a tap
opens its window, and the switch's dashed line to it comes from that setting.
A plug-in may mark a device's icon (a small blue letter at its top left): each function in `window.MapMarks` gets the device id and
returns `{glyph, title}` or nothing; the thread plug-in marks a device that is a Thread router "T".
A new LOAD (lamp, socket, AC, TV, boiler, LED strip, fan, other) is a thing the house does not know; "Link" says which channel of which device
controls it and a line is drawn. A long press (400 ms) on an icon lifts it, it follows the pointer and a release drops it (switches, sockets, blasters,
buttons and locks snap to the nearest wall line); Esc or a release at the start cancels; a tap opens the sheet; a drag on empty space pans.

## LED strips

A strip is a LINE: after "New load" -> LED strip the owner taps its points one after another and presses Done. Straight segments are the default,
"Smooth" rounds them (Catmull-Rom) per strip; each tapped point snaps to the walls of the vector plan (a few pixels) and to 0 or 90 degrees from the
previous point, a dashed segment shows where the next point would lie. "Edit the path" re-taps it, "Remove the last point" shortens it, a long press
on a point drags that point. A strip is linked to a channel as a whole or per part (two of its points), so one controller with two channels, or one
strip with parts on different channels, is drawn right; the line is coloured by the live state and brightness of its channel. Points are `path` (0..1),
the part of a link is `seg: [first, last]`.

## Rooms

The zones of the house (an outline and a name each) come from the house's data (`PUT /api/map/zones` imports them: `{"zones": [{id?, name, poly}]}`, the
points 0..1 of the scheme) and are edited on the screen: a zone's sheet, its context menu or the menu of an empty spot ("New room here": a small rectangle that
is then edited) lead to the OUTLINE EDITOR - the zone's own dashed outline gets a handle at every corner (drag it; it snaps to the walls and to 0/90 degrees
from its neighbour) and a "+" in the middle of every edge (click or drag it to add a corner); a corner is deleted with a double click, the Delete key or its
context menu (3 corners stay); a tap on an edge itself (not its corner, not its "+") switches it between a WALL (a thick line) and OPEN (a thin
dashed border: an open-plan kitchen and salon); a door is two points added on a wall and its middle part made open; a new point keeps the mark
of the edge it splits, two edges joined by deleting a point are a wall if either was; "Done" also gives the same edge of a neighbouring zone the
same mark. The marks are `walls` of the zone (one per edge, edge i = point i -> i+1; none until the first is set); Ctrl+Z / Ctrl+Y and the buttons undo and redo every step; "Done" saves once (the outline must not cross itself: the house checks it
too and answers 400 otherwise), "Cancel" puts everything back; the neighbouring zones stay faintly in view. THEY ARE the dashboard rooms (tabs, voice "in room 1", room temperature). A zone with the name of an existing room takes that room (its id stays: rules, voice
and favourites keep working), a new name makes a room. On the screen a tap on a zone's label (or "Zone" in the sheet of an empty spot) opens its sheet: Rename
(the dashboard room is renamed, its id stays; a name another zone or room has is refused) and "Merge with..." (one name = one room, two outlines, one label), and
the devices of the room. A device standing in a zone is in that room only and the room selector of its window is read-only ("from the map"; the room API
answers 409 for it); moving its icon moves the device. A device that is not placed keeps its room. A room with no zone stays while a device is left in it and goes
when its last device has been placed elsewhere - never while a rule, a favourite, the order of its tab or a satellite names it (the answer of the PUT says so in
`kept`, and names what went in `removed`). A house that imports no zone keeps its rooms exactly as they are. A copy of the dashboard rooms is written to
`plugin-data/map/rooms.json` at every change, so what the map did is recoverable.

ROOMS WITH NO OUTLINE YET: a room is made by name too (`POST /api/map/rooms`), before or without an outline. It is kept in `unplaced` of
map.json, it is a dashboard room like any other (the "+ Room" chip, a device window's "New room...", `POST /api/rooms`: the core hands
them all to the map, ROOMS_OWNER) and its tab always shows, even empty (an empty room WITH an outline hides as above). The outline
drawn for it later takes it by its name (its id stays) and it leaves `unplaced`; a PUT of the map keeps `unplaced` as it is. A rename
(`PUT /api/map/rooms/{id}`) renames the outline or the unplaced entry with the room; a delete (`DELETE /api/map/rooms/{id}`) takes the room and
its outline away, its devices are in no room after it. "Delete the room" on the map is that delete, at once (not an edit to undo: the question says so), refused while an
edit is not saved yet (never saved for it), after the question its tab asks too: what still names the room (`GET /api/rooms/{id}/refs`: a rule, a panel, a satellite) is listed in it. With no zone drawn no room goes. Once, at the first start of the plug-in, every dashboard room
the map has no zone for becomes an unplaced room: map.json and the rooms as they were are copied first, with their sha256 in `manifest.txt`, to
`plugin-state/map/rooms-migration-<UTC>/`, and `plugin-data/map/.rooms-migrated` marks it done (a house it cannot write tries at the next start).

The right button opens a menu of its own on a desktop (the browser's menu is suppressed inside the map): on a thing - What it turns on / Link, Move, Blink,
Remove; in a room - Rename, Change the outline, Merge with..., Delete the room; on empty space - Place a device here, New lamp here, New room here; on a corner
in the editor - Delete the point. A touch screen has the sheets (a tap) for the same actions. A press on a thing never pans the map: a mouse drags an icon
after 4 px, a finger after a 350 ms press (it may wander 10 px meanwhile); only a press on empty space pans. The zoom (a pinch or the wheel) goes
from half the fitted plan to 8 times it (at most 6 to 1); a pan or a zoom keeps half the pane (or half the plan) covered by the plan, and when one finger
of a pinch lifts the other pans on from where the plan is.

## The screen

Icons are one inline SVG set (no emoji font needed). An icon is 2.4% of the scheme's width, kept between 14 and 44 screen pixels, so it shrinks and grows
with the zoom; values and names show when it is big enough; icons of one zone that would overlap become one round badge with their number (a tap zooms
into them; icons that no zoom parts - one spot, or the zoom at its limit - fan out around the spot instead, each one tappable, and
fold back on a tap beside them or a zoom change; no line is drawn to an icon inside a badge). A room's name and its temperature
move together, after every zoom or pan, to a spot of the room that no icon, badge or fanned icon on the screen covers: near the
middle first, then one font step smaller, then anywhere in the room. The STATE layer (on by default, a checkbox) paints what each thing does now from the dashboard's
live feed and repaints only what changed: a glow around a lamp that is on (brighter when brighter), a dot per key of a switch, a value per sensor, a red
"open" for a door or window, a lock glyph, the AC's mode colour and set temperature, a pulse for motion, a grey "!" with the time of the last contact for a
thing that is offline, a dimmed icon for one not heard of for 12 hours. The list of what is left to place is by room, then kind, with the dashboard's names:
a side panel on a wide screen, a half sheet on a phone. A tap on a thing opens its sheet (what it is, its state, an on/off button per channel, its links,
move, and - apart - remove). On a portrait phone the scheme is turned 90 degrees so its long side runs down the screen (icons and labels stay upright)
and the toolbar is one row with the rest behind "...". A page that was open before the screen changed says so and offers a reload.

## Routes

| route | what |
|---|---|
| `GET /api/map`, `PUT /api/map` | the whole map `{rev, rooms, unplaced, items, links}` in `plugin-data/map/map.json` (and `scheme`: `{w, h}` or null, `load_types`); a PUT with an old `rev` answers 409; its answer is `{rev, kept, zones: [{id, house_room}]}` |
| `GET /api/map/scheme` | the scheme (SVG) |
| `GET`, `PUT`, `PATCH`, `DELETE /api/map/underlay` | the plan of the flat: its file, an upload (`?w=&h=`, the body is the file), its place and look, its removal |
| `POST /api/map/zones/tidy` | "Join neighbour walls" |
| `PUT /api/map/zones` | the owner's import of the house's zones (replaces them; the rooms follow): an edge the map had (the same ends, to half a unit, either way) keeps its wall mark, a part of one (split, shortened: on its line, overlapping it) takes that edge's mark (the zone's own edge first, then the shortest; two that disagree: open), any other edge is open (never a wall by default); doors and windows keep their place, one left with no wall under it goes (`openings_dropped`); same answer as the PUT of the map |
| `GET /api/map/parts` | `{primary: [member ids]}` of the merges in `things.json`: the dashboard hides a member from its grids and offers it as a button in the primary's window |
| `GET /api/map/candidates` | the physical things that may be placed: `[{id, name, category (the kind), room, online, members, detail}]` |
| `GET /api/map/auto` | `[{from, to, via, note}]` the links the house knows (rules, panel buttons, IR blasters), drawn grey and dashed, computed on demand, never stored |
| `POST /api/map/rooms`, `PUT`/`DELETE /api/map/rooms/{id}` | make `{name}` a room with no outline yet, rename one `{name}`, delete one with its outline; `{ok, room}` (`{ok}` for DELETE); a name another room has: 409, an unknown id: 404 |
| `GET /api/map/facts` | `{lines, items: [{text, load, controlled}]}` for the voice model: "<load> (<room>, <type>): controlled by <device> / <channel>", "LED strip <name> ..." |

The dashboard saves with a revision check and, when another tab has saved meanwhile, lays its own changes over the new revision and tries once more.
`plugin-data/map/` is house data: keep it in the house's own git (or its backups) for its history.
