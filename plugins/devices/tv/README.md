# tv - TV cards, the Appliances tab and Settings -> TVs

Dashboard side only. The TV itself is reached by another plug-in: `webos` for LG webOS TVs,
`tizen` for Samsung Tizen TVs, `ir` for an IR one. Nothing to set up here.

| file | what |
|---|---|
| `__init__.py` | the plug-in's entry (no API of its own) |
| `ui.js`, `i18n.json` | the card face of category `tv` (power, volume + mute, inputs), the Appliances tab (`tv` and `media` devices), a 12 s hold of "on" while a TV wakes over Wake-on-LAN (webos, or a device with `wakes`, as tizen), the page Settings -> Devices -> TVs |

## Settings -> Devices -> TVs

One card per TV. A transport plug-in (webos, tizen) fills in its own card: address, pairing,
inputs, apps. A TV with an IR remote shows the remote and a Keys button that opens Settings ->
Devices -> IR. A TV that only IR reaches points to the IR page. Each transport may add its own
buttons below the cards (for example "add a TV"). The `player` plug-in adds a Players tab here.

The `ir` plug-in adds its appliances and the "+ IR appliance" button to the Appliances tab.
