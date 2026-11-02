# tv - TV cards and the Appliances tab

Dashboard side only; the TV itself is another plug-in (`webos` for LG webOS TVs, `tizen` for Samsung Tizen TVs, `ir` for an IR one).

| file | what |
|---|---|
| `__init__.py` | the plug-in's entry (no API of its own) |
| `ui.js`, `i18n.json` | the card face of category `tv` (power, volume + mute, inputs), the Appliances tab (`tv` and `media` devices), a 12 s hold of "on" while a TV wakes over Wake-on-LAN (webos, or a device with `wakes`, as tizen) |

The `ir` plug-in adds its appliances and the "+ IR appliance" button to the Appliances tab.
