# watchdog - the house keeps itself consistent

One loop every 45 s runs checks so nobody has to babysit the house. A **data** fix (a retained ghost, a dangling reference) is made and
logged at once; an **actuation** fix only for a state a rule set, rate limited, and never for a lock (by its category or a "lock" channel), an air conditioner, the boiler or anything
marked sensitive; everything else is reported. Every fix is one log line with its reason (`[watchdog] <check> <subject>: <what>`).

| check | what it does |
|---|---|
| `rules` | a channel a rule switched (`core.bind_origin`) while the rule's source (a live binary sensor) has said the opposite for 2 minutes, and a plain on/off rule for that opposite exists for the same channel: that rule runs again (its conditions apply), once per 2 minutes, twice at most, then an alert. A channel nobody's rule switched (a hand, the app, voice) is never in the list; an IR remote (no feedback) is skipped; a target on the never list is only reported |
| `fresh` | an online sensor that has reported nothing for longer than its kind does (mains 20 min, battery 6 h; buttons, voice and indicators never) gets `stale: true` in its status: the dashboard dims its card (marked as stale), a room's temperature and the voice answers never use it; the next report clears it |
| `commands` | the newest command to a channel (`core.sent_commands`; the watchdog's own retry is no new command) while the device has reported since and, past the channel's confirm window, still says the value from before: sent again once (never for a never-actuate or IR device, nor a channel its plug-in marks `"toggle": true` (a key that only flips): those are open issues with "Send again"), then an alert; a channel the device lists in `no_enforce` (a TV that wakes by itself) is not checked at all |
| `ghosts` | the retained `home/state/<id>/*` of ids that are no device are cleared (every 10 minutes) |
| `refs` | a gone device's data (name, channel names, dim windows, favourites, rooms, tab order, tile sizes, a room's thermometer) is listed in one row "Forgotten devices" that opens Settings -> Devices -> All devices -> Forgotten (Forget removes it by hand). It is forgotten by itself only after `forget_days` (30; 0 = never; Settings -> Automation -> Watchdog) of absence, and only while its system runs: its plug-in is loaded and another device of its transport is here. A device the watchdog never saw is only listed; one held back by its system makes the row a warning. Rules, automations and panels that name it are reported after about 3 minutes, never touched |
| `presence` | the phones (Yash! app) and the router (presence host piece) disagree for 30 minutes: reported |

An issue closes by itself when its cause is gone (it stays in `resolved` with its duration); one that comes back within the hour is the same
entry with a count (`opened`).

The dashboard has a "Health" button in the header beside History (owner only): its dot, and its border while something is open, take the colour of the worst open issue (green = nothing open, yellow = a warning, orange = a retry waiting for its answer, red = an alert), the number of open issues follows the word, and a press opens the report (open issues, fixes, solved by themselves, observations, the digest) in a dialog. A fix is listed only once the device confirmed it (the issue the house acted on is gone; a retry that fails is an alert, not a fix); a command speaks the device's own verb (unlock, turn off), never a field or True/False; the history shows the last 24 h, older rows behind "More N", and keeps 7 days. `GET /api/watchdog` (the house token): `{status, count, issues, fixes, resolved, digest}`; `status` is `green` (nothing open), `yellow` (a warning),
`orange` (a retry waiting for its answer; the history never colours it), `red` (an alert). A daily digest is made after 08:00. The row "Home watchdog" is on
Settings -> Overview.

The confirm window of a channel is learned from its own answers (`core.ON_REPORT`: the time from a command to the device's first report of the value, the last 20): max(15 s, 1.5 x their 90th percentile), at most 180 s, once 5 are known; before that the start value of the device's kind (`confirm_start`: a lock 60 s, a TV 90 s, else 15 s). The device window shows it ("answers in about N s, waits M s") and takes a wait by hand (`confirm`), which wins. An answer after the window, before any retry, is listed under "Answered slowly", not as a problem. `GET|PUT /api/watchdog/answers/{device}`, `POST /api/watchdog/resend {key}` (the house token).

`GET|PUT /api/watchdog/settings` reads and sets `forget_days` (Settings -> Automation -> Watchdog -> Forgetting).
`POST /api/watchdog/note {title, text}`: a helper of the house posts what it found; the last 20 are kept (in `state.json`) and shown
in the report. All with the house token.

Files: `plugin-state/watchdog/state.json` (the rule origins, so "a rule set it" survives a restart, the fixes, the slow answers, the digest, since when each gone device is gone and its plug-in and transport); `plugin-data/watchdog/latency.json` (the answer times);
`plugin-data/watchdog/settings.json`, all optional:

    {"never_actuate": {"ids": ["water heater"], "categories": ["lock", "climate"]}, "checks": {"rules": true, "fresh": true, "commands": true, "ghosts": true, "refs": true, "presence": true}, "fresh": {"mains_s": 1200, "battery_s": 21600}, "forget_days": 30, "confirm_start": {"lock": 60, "tv": 90}, "confirm": {"front_lock/locked": 90}}

A device with `"sensitive": true` in its registry entry is never actuated either.

| file | what |
|---|---|
| `__init__.py` | the loop, the checks, the routes above, the health rows |
| `ui.js`, `i18n.json` | the Health button and its report, the Forgetting page in Settings |

Setup: turn `watchdog` on in Settings -> Maintenance -> Plug-ins and update. Everything else is optional (the file above).
