# history - one timeline of the house

Everything that happened, newest first, in a local SQLite file (`plugin-state/history/history.db`, WAL): state changes of devices **with who caused
them** (the app, voice, a rule by name, the watchdog, a timer, or the device itself: a hand at the wall, a sensor), commands sent, rule firings,
devices going offline and back, the watchdog's fixes, issues and "solved by itself" (with their duration and how often it came back), and the WARNING and
ERROR lines of the log. Nothing is sent anywhere. The **History** button in the header (owner only) and Settings -> Automation -> History open it: filters by kind (states, commands, rules,
errors), room, device, time range, a search, a tap on an entry opens the device, **Erase** erases what the filters show or everything.

Thinned so it stays readable: a temperature when it moved 0.5, a humidity 3, other numbers 1 (an illuminance 30 %), or one sample an hour; signal strengths,
uptimes, per-gate radar energies and the like are not kept; the same event twice within 2 s is one. The log lines are redacted (no token, password or key
of any shape; a line that holds a value of the environment's secrets is dropped), one line a minute per text.

`GET /api/history?room=&device=&kind=&source=&since=&until=&q=&before=&limit=` (newest first, `more` says there is a next page; `since`/`until` seconds
or an ISO date), `DELETE /api/history` with the same filters or `all=1` (no filter and no `all` is refused), `GET /api/history/info`. House token only.
Settings, optional: `plugin-data/history/settings.json` `{"retention_days": 30, "max_rows": 500000}` (old entries are removed every 6 hours).
Other plug-ins add to it with `core.emit_event({"kind": ..., "device": ...})`; it listens through `core.ON_EVENT`.

| file | what |
|---|---|
| `__init__.py` | the store, the thinning, the routes above |
| `ui.js`, `i18n.json` | the header button, the timeline window and its Settings page |

Setup: turn `history` on in Settings -> Maintenance -> Plug-ins and update. Nothing else to set.
