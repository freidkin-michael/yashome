# stats - counts for a status page

`GET /api/stats` (token-protected, like every `/api` route) for a status page widget such as Homepage:

    {"online": 14, "offline": 2, "total": 16, "bindings": 9, "events_last_minute": 3}

- `online` / `offline` / `total`: the devices and how many are online now (live: a device still waiting
  for its report after a start or a return counts as offline here);
- `bindings`: the rules on a source's event (a button, a sensor), not auto-offs or schedules;
- `events_last_minute`: the device events of the last minute that the house keeps (Zigbee actions,
  NFC cards, presence arrivals and departures; at most the last 20 per device).

No configuration.

| file | what |
|---|---|
| `__init__.py` | the route |
