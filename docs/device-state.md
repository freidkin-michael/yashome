# Device state: live, waiting, offline

Status: implemented in the core (`core/status.py`, `core/mqtt_devices.py`, `core/timers.py`), the dashboard, the
Android panel and the ESP screens; groups (D8) are not. Two features share one model:
dimmers wait for the device instead of showing the sent value, and a device that went away and came
back shows the state it really has.

## The model

Every device has one state in the core, seen the same way by the dashboard, REST, the WebSocket, the
retained `home/state` mirror, the Android panels and the ESP screens:

| state | meaning | what is shown |
|---|---|---|
| live | the device reported since it last came up | the reported values; tooltip "last report HH:MM" |
| waiting | the backend started (`"start"`) or it came back after an outage (`"return"`) and has not reported yet; ends as below | the last values greyed, "waiting for report" |
| offline | its availability says offline, or it went silent (below), or its bridge is down | the last values greyed, "offline since HH:MM" |

Waiting ends:
- a live report makes the device live;
- at the start, for a device with an availability signal the availability decides: its retained
  message in the broker's replay makes it live with the retained values (a retained state frame alone
  does not); for a device without one a retained state frame counts as a report;
- back after an outage only a report ends it (the same online word again does not);
- a heartbeat device waits for its report and is offline after the silence rule;
- a device whose silence is normal (no heartbeat: door, button, battery sensor, a zigbee2mqtt device
  that sends nothing on return, an idle lamp or lock) is live with its last values 35 s (the ask
  window of 5 s plus 30 s) after the start or after the online word; only a device with a confirm
  channel (a lock, a dimmer) back after a real outage its availability announced keeps waiting for
  its report - and can be commanded meanwhile.
The 35 s count only while the broker is connected, from its last (re)connect (also for a device back
before a broker outage).

A channel can also be waiting on its own: a command was sent and the device has not answered yet
(see "Commands that wait for the device").

Status fields (`GET /api/devices/{id}/status`, the WebSocket frame): `online` is true only when
live; `last_seen` (time of the last report), `offline_since`, `waiting` (`"start"` or
`"return"`), `waiting_since` (the online word it waits after) and `cmd_wait` (`{code: value sent}`
while a command to a confirm channel waits for the device; not stored, added when the status is
read). The mirror's `home/state/<device>/online` follows `online`: false while waiting or offline.
The channel values in the mirror stay the last reported ones.

## Commands that wait for the device

- A brightness channel and the on/off switch it dims are confirm channels, like the lock: the core
  never writes a sent value; the cache, the mirror and every screen change only when the device
  reports. The switch a brightness dims: `<x>` for `<x>_bright` (ESPHome), the switch of the same gang
  for `brightness[_<gang>]` (`state[_<gang>]`, zigbee2mqtt).
- A channel the device never reports (a generic control with a `command_topic` and no `state_topic`,
  the colour channel, IR) cannot wait: it stays optimistic.
- REST answers once the command is sent: `{ok: true, code, waiting: true}` for a confirm
  channel. It does not block until the report.
- Dashboard slider (and a dimmer's switch): stays where it was released, drawn as "waiting"; the wait
  ends by the rule below (an echo of the value sent, another value that settled for 1 s) and the
  slider shows the reported value, also when it differs from the one sent. No answer within 5 s
  (`CONFIRM_WAIT_S`; a plug-in's `holds` stretch the dashboard's wait - a TV takes up to 60 s -
  while the core's own command history counts 5 s): the slider goes back to the last reported value
  and a toast says "No answer from <device>". A refused command (409 offline, any error) reverts at
  once with its toast.
- What ends a wait: (a) a report within tolerance of the value sent (brightness within one unit or 2
  %, a switch equal); (b) a report of that channel that answers none, the old value too (a
  zigbee2mqtt frame or a device-level JSON frame counts only the channels it changed, and a channel
  it carries unchanged at the newest command's value as its echo), followed by 1 s without another
  (the last of a burst wins: a ramp or a Zigbee transition publishes levels on the way; a device
  that clamps, e.g. answers 80 to 100, shows its answer); (c) the 5 s timeout. A report older than
  the command never ends it.
- A toggle or a step right after a command builds on the newest command still unanswered (two
  quick presses add up). The core keeps the commands of the last 5 s per channel (at most 8): a
  report of that channel equal to one of them (a brightness within one unit or 2 % of its range) is
  the echo of the newest such command and answers it and every older one - a newer one still waits;
  a report of that channel that answers none (a clamp, a refusal that repeats the old value - not
  seen in a zigbee2mqtt or device-level JSON frame, which lists the changed channels only -, a change
  from elsewhere, a ramp's step) starts a 1 s settle that every further such report restarts; when
  it runs out the commands sent before that report are void and the report is the base (a command
  sent after it still counts). While it settles, a step built on a command older than that report
  never goes against it (down from the lower, up from the higher); a command sent after the report is
  the base as it is. A report of another channel answers nothing; a command nobody answers
  counts 5 s.
- Two screens on one dimmer: the one that sent waits; the other changes when the device reports.
  A report during a wait ends it, whoever caused it.
- A dimmer whose on/off is a separate channel: each channel waits for its own report.
- zigbee2mqtt: after a `/set` it publishes the device's new state once the device acknowledged the
  command; that frame counts as the report.
- Android panel (`view.html`): it draws nothing itself after a tap; it shows the control as
  waiting until the cache changes or 5 s pass.
- ESP screen: a tap on a dimmer sends the command without changing its level; the bar dims to show it
  waits and shows the level again when the mirror topic of the device reports it (5 s: back to the last
  one).
- The knob waits for the device's state.

## When a device goes away and comes back

**In the core:** the model above - a report makes a device live (`last_seen`), its own
offline word or a failed poll makes it offline (`offline_since`), back after an outage it waits for
its report (`waiting: "return"`), the status cache loads every device as waiting (`"start"`; one
stored offline stays offline) and the waits end as in "The model"; a command never changes any of
it; the mirror's `online` follows. A periodic device silent past its limit is offline, and
after a start it waits for its report (not live from the cache). A command to an offline device is
refused (409; a webOS TV never), rules and timers skip it (D7), Settings -> Overview lists the
devices offline for more than 10 min. A device waiting 5 s without a report is asked once per
wait (zigbee2mqtt `/get`; a generic device with `state_request`).

**The dashboard:** a card is live, "waiting for report" (dimmed) or offline ("offline since
HH:MM", greyed, its controls take no tap; the card still opens), what it shows changes once the new
state held 3 s; the last values stay on it, the tooltip says "last report HH:MM"; the device window
says online, waiting for report or offline since. A command to a confirm channel changes nothing on
the card: the control pulses while the core lists it in `cmd_wait` and then shows what the device
reported; when the wait ended after 5 s with nothing reported on that channel, a toast says "No
answer from <device>" (another value reported is an answer: shown, no toast); the wait also ends
without a frame (the WebSocket down), and a plug-in's `holds` stretch it only while the channel
reports nothing. A plug-in face pulses as a whole. Going away shows after 3 s (a timer, not the next
frame), coming back live at once. In the device window the commands are off while it is offline, for
the keyboard too (Enter and Space on a switch do nothing, a slider is `disabled`; the dim window and
the names stay editable). Failures are toasts, not dialogs: one per text in 5 s, at most three (an
evicted one may come again), across the screen's width, announced to a screen reader (the live
region exists from the start), never in the way of a tap. The WebSocket sends a frame as soon as a
status changes, at most one every 0.25 s (the latest state), and an unchanged one every 2 s.

**The keyboard on the dashboard:** Tab reaches every card and each switch on it (a dot, a dimmer's
pill, a toggle, a slider); Enter or Space on a card opens its window, on a switch switches it (a
held key acts once). On a dimmer's dot or pill the arrows step a tenth of the channel's window, as
the pill's side zones do (a window under 10 points moves one point; outside the window a key moves
only into it), and switch it on. The level shows at once and goes out 250 ms after the last key, or
at once when the switch, the pill, the dot (a tap or a drag) or the window of that dimmer is used
meanwhile, the touch's own level going last; then it waits for the report as the slider does.

Every switch on the dashboard (a key, a tap, a click, a favourite, the device window's toggles)
builds on what was last asked of it while that command is in flight: its first 1.5 s, or up to 5 s
while the core still waits for the device. A command that failed was not asked (the one before it
counts again); a report from elsewhere inside that time does not change what the next toggle builds
on.

A card says its state to a screen reader ("<name>, offline" / "waiting for report", in the
dashboard's language), and the level is read out with a dimmer's switch. An offline card still
opens; its controls leave the tab order, say they are disabled and do nothing, and a control that
had the focus hands it to its card. A plug-in's face keeps its own controls. Escape closes the
device window and gives the focus back to the card. A redraw of the page (another tab and back, the
window regaining focus) keeps the focus where it was and draws the cards of an offline device off at
once. A language switch reloads the page. Not by keyboard yet: the favourite tiles, the Add-device
rows and the colour row.

**The Android panel** (`plugins/devices/panels/static/view.html`, it polls each device's status every 3 s, so a change shows
within that): a widget of an offline device (an AC and a remote included) is greyed and takes no tap,
waiting dimmed less, with no caption; after a tap on a confirm channel it pulses while the status
lists the command in `cmd_wait` (the panel polls every 0.5 s meanwhile, one timer however many taps)
and says "No answer from <widget>" when the wait ended without the value sent. A status the server
does not give ends the wait without that message. The panel redraws only when a status changed.

**The ESP screen** (`plugins/devices/panels/firmware/gen.py`): a tap on a dimmer sends the command and leaves the bar as
it is; the bar's fill dims until the device's mirror topic reports (5 s at most, then the fill lights
up again over the unchanged level); a second tap meanwhile builds on the first one's value. Any
report of the dimmer ends the wait: a third tap after the first one's echo builds on that echo, and a
brightness report that comes before the on/off one lights the bar for that moment. A widget
whose device is offline is greyed and takes no tap, one waiting for its report dimmed: the panels
plug-in publishes `panel/<name>/away` (retained), the panel's devices that are not live as
`/<device>#o/<device>#w/` (offline, waiting; `/` when all are live; an id is a topic level, so it
holds no `/` or `#`); it publishes again within a second of a change. Toggles, dimmers, sensors and
temperatures are greyed (the device of their first `home/state/<device>/<code>` topic, else their
command topic); an AC, a remote and a button are not. The knob does not grey.

**How the core learns gone and back:**

| source | gone | back |
|---|---|---|
| `availability_topic` (generic MQTT, ESPHome, SwitchBot bridge) | `payload_offline` | `payload_online`, or any live report |
| zigbee2mqtt | `<name>/availability` offline, or `bridge/state` offline (all its devices) | online, or any live report |
| no availability signal, reports periodically | 10 min of silence (below) | the next report |
| no availability signal, reports on change only (door, button, battery sensor) | never by silence | - |
| IR appliance | its blaster | its blaster |
| webOS TV | an unreachable TV is power off, not offline (it sleeps) | - |

**Silence (D1):** learned conservatively - a false offline is worse than a false live. A device
counts as periodic once its last hour holds at least 10 reports (values within 1 s count once) over
10 min or more at regular gaps: the longest at most twice the shortest, the median under 2 min
(a 30 s heartbeat; a door in steady use is not regular). Every report re-learns it and an irregular hour
clears it; the silence check refuses to grey a device whose history up to its last report is not
regular. The flag (`periodic`, `periodic_since`) is kept in the status cache and dropped at a start
when it was not confirmed for 24 h. For a device without availability that you know heartbeats, set
`offline_after_s`: it makes the device periodic with that limit, learned or not. A periodic device
that stays silent for 10 min goes offline; the house-wide default is 10 min (`DEVICE_SILENT_S` in the code).
A device with an availability signal uses that signal at once and never waits for the silence rule.
Event-only devices are never marked offline by silence.

**Offline (D2, D3):**

- The card keeps the last values greyed, a badge "offline since HH:MM", the tooltip "last seen
  HH:MM"; its controls are disabled. The badge waits 3 s before it shows and before it goes (a
  flapping device does not flicker); the state itself changes at once and every change is logged.
- A command to an offline device is refused with 409 "<name> is offline": the dashboard, REST, the
  panels and rules alike; on `home/cmd` (the knob, a panel) it is dropped with a log line (one per
  topic per 10 s, with the count of the rest). Nothing is queued (a coordinator holds a command for
  a gone Zigbee device and would run it minutes later). Not refused: a webOS TV (unreachable means
  asleep: power-on by Wake-on-LAN must go out; another command to a TV that is off gets 409 "the TV
  is off" from its plug-in) and a channel with no availability signal and no state (fire-and-forget:
  it is never offline). An IR appliance whose blaster is offline is refused (the blaster is what
  sends).

**Back (D4, D5):**

- Availability online, or the first report after an outage: the cached values are dropped as live
  values; the next report sets them. The return is processed even when the first report equals the
  old value (the de-duplication of the mirror does not swallow the `online` change).
- A device that came back (or waits since the start) and did not report within 5 s is asked once:
  zigbee2mqtt `<name>/get`; a generic device only when its entry has a `state_request` (optional,
  `{"topic": "...", "payload": "..."}`); at most 10 asks a tick (5 s), the rest on the next, so in a
  house of more than about 70 such devices the last asks come after the 35 s grace. Asked or not,
  the wait then ends as in "The model": a device whose silence is normal is live with its last values
  35 s in, a heartbeat device waits for its report (and is offline by the silence rule).
- A report before the availability `online` brings the device back by itself; the later `online` is
  a no-op. Retained availability from before the backend started is applied during the broker's
  replay.

**Restart (D6):** at start the status cache loads every device as waiting (greyed, last values; one
stored offline stays offline), never as live; waiting then ends as in "The model" (an event-only
device is live with its last value about 35 s after the broker connected, so rules on it keep
working; a lock or a dimmer too). The mirror publishes
`online=false` for them, not the cached `online=true`. A broker restart: the devices whose last
availability was offline stay offline; the others are re-read from the retained replay. A
zigbee2mqtt bridge that is down wins over a device's own retained "online", in either order of the
replay; an empty (cleared) availability message says nothing.

**Rules and timers (D7):**

- A condition on an offline or waiting device is false: the rule is skipped, one log and event line
  "skipped: <device> offline".
- An action aimed at an offline device is skipped with a log and event line: not queued, not retried.
- A timer due while its device is offline waits for it (every 15 s, in memory: no disk write): an
  auto-off up to 30 min, a `for N min` revert and a schedule entry up to 10 min past their time;
  later it is dropped with a log line (never hours later). A schedule entry is caught up only on the
  same day; a missed one (also one a newer entry of the channel replaced) shows in Settings ->
  Overview for a day, also after a restart. At a start a shot waits until its device is known (it
  looks every 2 s), however late: an online device then gets it. Back and reporting a channel ON
  with an auto-off window and nothing waiting for it, the window starts again.
- Settings -> Overview lists the devices offline for more than 10 min.

**Groups (D8), not implemented yet:** no transport delivers groups (zigbee2mqtt groups are not
loaded). When one does: a group card is offline only when all members are; otherwise a badge
"n of m offline"; a command skips the offline members and the toast names them.

**Panels:** the Android panel greys a widget whose device is not live and disables it; the ESP screen
does the same from one retained topic, `panel/<name>/away`, which the panels plug-in keeps. Not one
`home/state/<device>/online` per device: ESPHome's MQTT client (2026.7) queues at most 31 inbound
events and handles one per loop, and at each connect every subscription brings its SUBACK and its
retained value at once; the full test layout has 13 `on_message` topics, this one included (the
components' own topics come on top). For the same reason the firmware
subscribes a topic once, however many widgets read it.

## Decisions

10 min of silence and the last value shown greyed; D1-D8 above; and:

1. `CONFIRM_WAIT_S` = 5 s for every device (a plug-in's `holds` may extend it); no per-device knob.
2. Groups stay reserved (above): no code and no tests until a transport delivers them.
3. The learned "periodic" flag is kept in the status cache, written only when it changes (the cache
   is saved at most every 10 s already: no extra writes in a steady house).
4. The ESP screen reads one retained `panel/<name>/away`, not one `online` topic per device (the
   inbound queue, "Panels" above).

## Tests that prove it

- `tests/test_review_cb.py`: the core model (live, waiting, offline, the asks, the silence rule, confirm channels and
  their echoes, rules and timers on an offline device).
- `tests/plugins/test_zigbee.py`: availability, a bridge that is down, devices waiting since the start.
- `tests/plugins/test_panels.py`, `tests/plugins/test_panels_gen.py`: `panel/<name>/away` and the greyed ESP widgets.
- `tools/stand.py` replays the outage scenarios on a throwaway broker (docker, 127.0.0.1:18830) and this checkout's
  backend (127.0.0.1:18766): `up`, `scenario all`, `pub`, `status`, `cmd`, `restart`, `down`.

## Not implemented yet

- Groups (D8).
- Keyboard use of the favourite tiles, the Add-device rows and the colour row.
