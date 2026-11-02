# ir - learned IR/RF remotes through ESPHome blasters

A blaster is an ESPHome node with an IR (or RF) transmitter, and maybe a receiver. It is only a pipe:
it sends the raw frames it gets on `<node>/tx/{ir,rf}_raw` and reports the frames it hears on
`<node>/rx/{ir,rf}_raw`. The codes are stored here, never on the node, so a reflashed or replaced
board loses nothing.

| file | what |
|---|---|
| `__init__.py` | remotes, appliances, learning, sending, IR toggles (`/api/ir/*`) |
| `ui.js`, `i18n.json` | the IR remotes tab and Settings -> Devices -> IR (the same page), learning, the appliance tiles, the buttons on a host device's card, "+ IR appliance" on the Appliances tab |
| `setup/settings.example.json`, `setup/ir_remotes.example.json` | the shape of the settings sections and of the stored codes |

- **Remote**: a set of learned buttons, in `plugin-data/ir/ir_remotes.json`.
  A button may carry an `icon` (a key of the dashboard's icon set, picked when it is named): it decides the button's
  face and its place in the remote layout.
- **Blaster**: an entry in the settings section `ir_blasters` (`rx: true` = it can learn). The IR remotes tab lists them under
  "Blasters" with their state and what sends through them. "+ Blaster" (`POST /api/ir/blasters` with `node`, the ESPHome name,
  and `name`) adds one, which works at once (its topics are subscribed then); "Learn through it" (`PATCH` with `rx: true`) makes
  it the one blaster that learns. `PATCH /api/ir/blasters/<node>` (`name`) renames one,
  `DELETE /api/ir/blasters/<node>` forgets it and is refused (409, the users named) while a tile, a toggle or a device
  whose topics are the node's (an air conditioner its firmware drives) sends through it.
- **Appliance** (`ir_appliances`): a remote + the blaster in that room. It becomes a device with one
  channel per button, so rules, schedules, favourites, panels and voice can press a learned button.
  A press is `true` (or no value); `off`, `false` or `0` sends nothing and answers 400.
  With `device` set, its buttons appear on that device's card (an IR remote for a TV, for example).
- **IR toggle** (`ir_toggles`): a box whose power key flips it. A TCP probe tells its real state, so
  "power on" sends the frame only when the box is off. `keep_on` presses power after a minute of
  silence (at most once per 5 minutes). A `touchmouse` field (cutover-compat) (`{"port": 7709, "os": "android"}`) makes
  the box a touchpad target at its probe address.

Setup: flash a blaster (an ESPHome node with the topics above) and turn `ir` on. Then Settings ->
Devices -> IR (or the IR remotes tab, shown once there is a remote or a blaster): "+ Blaster", then
"+ Remote" (learn its buttons with "+ Learn a button", or "+ 433 MHz" for an RF one), then
"+ IR appliance" (there, or on the Appliances tab).

Learning: open a remote, press Learn, point the original remote at the blaster. One learning
session at a time; closing the window cancels it.

The Appliances tab and its "+ IR appliance" button belong to the `tv` plug-in. With the `panels`
plug-in on, the dashboard also loads its widget drawing (`window.PW`), so an appliance with enough
known buttons shows a real remote layout with icons (digits, colour and channel keys as well: the
wall panels leave those out, the dashboard does not); without it, the buttons are a plain list.
A group can be PART OF THIS REMOTE instead (a keypad, the inputs: `part: true` on its buttons, the "part of the remote" switch
by the group's name in the remote window, `PATCH .../buttons/<id> {"part": true}`): it never counts as another device, so a TV
with a "Digits" group keeps its one remote. A button moved into a group or learned into it takes that group's kind; a group whose
buttons disagree shows a dash on its switch, and a tap sets all. No flag: the group is a device of its own.
A remote whose keys are in two or more groups of devices drives two things in one body (a ceiling light with a fan): its
tile shows each group as a titled zone ("Light", "Fan") with the keys by name in their stored order, and no
remote layout.

Renaming the device an appliance shows on (its `device`) moves the appliance with it.

**Power from** (the appliance window: a switch channel or none; `power: {device, code}`): a fixture whose mains go through a
wall switch (a ceiling lamp with a fan). While that channel says off, its keys are greyed on the tile with "No power: switch on
...", and a press from anywhere (a rule, voice, a panel, `/control`) is refused with 409 naming the switch instead of a frame
into a dead fixture; an unknown state (the switch offline) does not refuse. IR has no feedback, so what the tile and the map
show is what was SENT: the last key of each group of two keys or more (`sent_<group>` in the appliance's state; one key alone
is a toggle and says nothing); it is cleared when the switch goes off. The map shows such a fixture as one lamp: lit from the
switch, the badge the keys last sent, a tap opens its window; a dashed line from the switch is drawn by itself.

One name each: an appliance's or a toggle's name is in its record only (`core.NAME_OWNERS`), so the tile's pencil and the
card's rename change the same name, shown on the tile, the card, in voice and on the map; at start, a rename found in the
core's `settings.json` "names" moves into the record. A blaster fronted by a device (its card) is named by that device and
keeps no name in `ir_blasters`; a blaster without one is named there.

A remote can hide groups of keys on its face (power, navigation, volume, channels, media, other: `PATCH /api/ir/remotes/<id>` with
`hide`; the buttons stay learned and still work in rules). The dashboard card hides them, a panel tile copies the list when it is
bound. Buttons that have no place on the face are icons without text; the name is the tooltip.
