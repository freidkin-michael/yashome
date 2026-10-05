# Rules engine

Three kinds of automation, all on the server, all edited in the dashboard:

- **Binding** -- an event of a source (a remote button, a sensor) triggers an action on a
  target channel. Actions: `on`, `off`, `toggle`, `bright_up`, `bright_down` (+/-10 %, floor 1 %).
  Options: `conditions` (AND), `time_window` (may
  cross midnight), `for_sec` + `retrigger` (`restart | extend | ignore`), and for numeric sensors
  `threshold` + `hyst` -- hysteresis, no bounce, no shot on the first reading after start.
- **Auto-off (inching)** -- "after any turn-on, switch off after N seconds". Applies to a manual
  press, a schedule and a binding alike, so there is one place that says how long a channel
  stays on.
- **Schedule** -- time + weekdays per channel, evaluated once a minute in `TZ`.

`GET /api/rules` returns them as one list. Rule ids:

```
b|<src>|<code>|<gesture>               binding, e.g. b|remote-4|action|1_single
a|<dev>/<code>|sched|<HH:MM>|<action>  schedule entry
a|<dev>/<code>|inch                    auto-off of a channel
```

Conditions evaluate exactly the chosen operator (`<`, `>`, `<=`, `>=`, `==`, `!=`, `on`,
`off`); incomparable types or an empty value make the condition fail, and an empty value is
refused when the binding is saved.

Deferred deadlines (auto-off, `for`) are stored in `pending_timers.json` with an absolute
`run_at`: a restart does not lose them, overdue ones fire immediately. Start-up waits for the
broker's CONNACK before replaying them (a QoS 0 publish before the connection is silently
dropped), and a failed shot is re-armed a limited number of times.
