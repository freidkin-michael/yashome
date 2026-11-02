# Devices: one physical thing, one device

A device of the house is a physical thing: a TV, an air conditioner, a wall tablet, a switch. Its channels (power,
volume, listen, say...) and the ways the house reaches it are parts of that one device, never devices of their own.

## Parts

A plug-in that owns a device registers it (`core.register_device`). Another plug-in may add a part to it:

    core.extend_device(dev_id, part, channels, transport, info=None, cfg=None, map=None)

- `channels`: the part's channels. A code the device does not have yet becomes the part's: a command on it goes to
  the part's transport (`core.TRANSPORTS` / `ASYNC_CONTROL` / `CONTROL_KINDS` of its kind) with the device's config and
  the part's `cfg` over it, and it does not need the device online (an IR remote reaches a TV that is asleep).
- A code the device has already stays the device's own; the part is its fallback through `map`
  (`{code: {value: the part's code}}`, `"*"` for any value): when the device's own way fails or it is offline, the
  first part that maps the code does it.
- `info`: what the part shows (its remote, its blaster); the dashboard gets it as the device's `parts`, and each
  channel of a part carries `part`.
- The part may come before the device registers or after; `core.drop_part(dev_id, part)` takes it away.

## Merging two devices into one

`GET /api/devices/merge-plan?old=&new=` is the dry run: what a merge of `old` into `new` would move, store by store
(names, rooms, favourites, rules, tab order, tile sizes, room thermometers...) and plug-in by plug-in (`core.MERGE_PLAN`:
`fn(old, new) -> {what: count}`), and the codes only `old` has. `POST /api/devices/merge {old, new}` makes a backup
first (its name is in the answer: restoring it is the way back), moves every reference (`core.migrate_device_key` and
the plug-ins' `ON_RENAME`), tells the plug-ins (`core.DEVICE_MERGED`: `fn(old, new)`, the owner of `old` stops
registering it) and keeps `old` as an alias of `new` (`core.aliases(new)`, `core.merged(old)`): the history of the
device reads its former ids too. An MQTT device of the house is never the one merged away. Both routes take the house
token; restricted mode refuses them.

## The parts the plug-ins add

- IR: an IR appliance with a host device (`device` in its record) that was merged into it is that device's part
  `ir`: its remote's keys are the device's channels (`ir_*`), sent through its blaster; its discrete on / off keys
  are power's fallback. The device's window shows them as its remote; the TVs page names the remote and the blaster.
- The map, Samsung (its IR on key) and the panels (their widgets) follow a merge like a rename, and tell the dry run
  what they would move.
