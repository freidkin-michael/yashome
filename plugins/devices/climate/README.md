# climate - air conditioners on the dashboard

The card face and the remote window of an air conditioner (power, temperature, mode, fan). The
"in the room" reading on the card can come from another device's temperature sensor, or from the
room's thermometer (`room_temp_from` = a device id or `room`: the one the room's tab shows and Ask
answers, `core.room_reading`): it is copied every 120 s, and at once when the choice changes.

| file | what |
|---|---|
| `__init__.py` | `POST /api/devices/<id>/room-temp-from`, the settings section `climate` |
| `ui.js`, `i18n.json` | the card face and the remote window |
| `setup/settings.example.json` | the shape of the settings section |

Where to set it: open the AC's window (tap its card) and pick the source of "in the room". There
is no Settings page of its own.

The AC itself is any device of category `climate` (for example an ESPHome node that decodes the AC's
own remote and publishes its state). An `mqtt_devices.json` entry that carries `room_temp_from` is
taken over once, while the settings section has no choice saved yet.

With the `panels` plug-in on, the mode and fan buttons get the panels' icons (`window.PW.acIcon`);
without it they show text.

Renaming a device (the AC or its source) keeps the room reading: the choice follows the new id.
