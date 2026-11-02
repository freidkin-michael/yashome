# Rules engine

Three kinds of automation, all on the server, all edited in the dashboard:

- **Binding** -- an event of a source (a remote button, a sensor) triggers an action on a
  target channel. Actions: `on`, `off`, `toggle`, `bright_up`, `bright_down` (+/-10 %, floor 1 %).
  Options: `conditions` (AND), `time_window` (may
  cross midnight), `for_sec` + `retrigger` (`restart | extend | ignore`), and for numeric sensors
  `threshold` + `hyst` -- hysteresis, no bounce, no shot on the first reading after start.
- **Auto-off (inching)** -- "after any turn-on, switch off after N seconds". Applies to a manual
  press, a schedule and a binding alike, so there is one place that says how long a channel
  stays on - except the ON of a binding with a `for`: its `for` decides when it ends (its echo does
  not arm the auto-off; an OFF, or 5 s, ends that exception, and a later ON is armed as usual).
- **Schedule** -- time + weekdays per channel, evaluated once a minute in `TZ`.

`GET /api/rules` returns them as one list. Rule ids:

```
b|<src>|<code>|<gesture>               binding, e.g. b|remote-4|action|1_single
a|<dev>/<code>|sched|<HH:MM>|<action>  schedule entry
a|<dev>/<code>|inch                    auto-off of a channel
```

Conditions evaluate exactly the chosen operator (`<`, `>`, `<=`, `>=`, `==`, `!=`, `on`,
`off`); incomparable types or an empty value make the condition fail, and an empty value is
refused when the binding is saved. A condition on a device that is not live (offline, or waiting
for its report: docs/device-state.md) fails too: the rule is skipped and the press log says
"skipped: <device> offline" ("waiting" while it waits for its report, "gone" for a device that no
longer exists).

An action aimed at an offline device is skipped, not queued and not retried, with a log line and
"skipped: <device> offline" (or waiting, or gone) in the press log; one whose command did not go out
(a zigbee publish that failed) or whose action raised (a TV that is off, a refused value) says
"failed: <device> <reason>" ("failed: <reason>" when no device is known); a rule whose action is `off`
on an IR button is such a failure (a button is only pressed, 400), it presses nothing. Rules -> Run then answers 409
or 502 with the reason (409 also for a rule whose target is gone, whose channel is missing or whose
step is on a non-dimmer: "<device> has no channel <code>", "is not a dimmer"; 404 only for a rule that does
not exist), and an NFC tap "Not done". A timer that comes due while its
device is offline waits for it, retried every 15 s: an auto-off up to 30 min past its time, a `for`
revert and a schedule entry up to 10 min; later it is dropped with a log line (never hours later);
a schedule entry is caught up only on the same day. A missed schedule entry (also one a newer entry of
the same channel replaced while it waited) shows in Settings -> Overview for a day, with its channel,
also after a restart. A shot that waits costs no write to disk: `pending_timers.json` keeps its first
time. At a start, a shot of a device not known yet (waiting for its first report or availability)
waits until it is known, however late the shot is (the start grace ends that within 35 s): an online
device then gets it, an overdue auto-off too. A device that comes back and reports a channel ON that
has an auto-off window, with no auto-off or `for` waiting for it, gets the window again from its
return.

Deferred deadlines (auto-off, `for`) are stored in `pending_timers.json` with an absolute `run_at`:
a restart does not lose them, overdue ones fire within 2 s of their device being known (above). Start-up
waits for the broker's CONNACK before replaying them (a QoS 0 publish before the connection is
silently dropped), and a failed shot is re-armed (15 s, then up to every 10 min) until it lands - an
OFF is not dropped for a broker outage; for an offline device it waits as above, and a shot whose
device is gone gives up after 20 tries. Deleting or disabling a binding cancels its pending `for`
revert.

A binding never targets a read-only channel (a sensor, a trigger): it is refused when saved. A sensor
binding fires on a live reading only: the retained values a broker sends on every (re)connect are
no new event.

## A condition on a state the house computes

A rule's condition may name any device channel, also one a plug-in computes (a virtual device of the plug-in): the
light's rule "radar presence=false -> light off" may carry the condition "humidity below 70", so the light stays on while
the bath is steamy and the radar cannot see behind the curtain.
