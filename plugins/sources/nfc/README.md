# nfc - NFC cards as a trigger

An NTAG 424 DNA sticker with SDM opens `/nfc?picc=...&cmac=...` on the phone that taps it: a fresh
cryptogram on every tap. A tap that verifies becomes the event "card = <name>" of the device `nfc`.
Your rules decide what it does (unlock a door, run a scene). A forged, foreign or replayed link is
refused before any rule runs: the tag's counter makes a used link worthless.

| file | what |
|---|---|
| `__init__.py` | the `/nfc` page (an open route), the check, the device `nfc`, the counters, `/api/nfc` (the list, add, wait for a tap, rename, remove) |
| `ui.js` | Settings -> Devices -> NFC tags: the tags with the rules each one starts, add by a tap, rename, remove |
| `_sdm.py` | the NTAG 424 DNA SDM decryption and CMAC |
| `i18n.json` | the page's words |
| `setup/env.example` | `NFC_SDM_META_KEY`, `NFC_SDM_FILE_KEY` |

The counters live in `plugin-state/nfc/nfc_counters.json`. Never restore an old copy: it would make
used links valid again. Needs `EXTRA_PIP=cryptography==50.0.1`.

The Yash! app takes the tap itself (NDEF link) and POSTs the link's query back with `fmt=json`: the answer is
`{ok, head, note}` and the app shows it as a notification. A plain GET never acts.

What the page says: "Accepted" once the rule ran. When the rule drives a "confirm" channel (a lock),
the page waits up to 20 s for the device's own report and says what it reported ("Door: Locked").
It says "No answer" when the report does not come, and "offline" when the device is offline ("is
waiting for its report" when a condition's device waits after a start or an outage, "is gone" when it
no longer exists); then the command is not sent at all.

The tags are `plugin-data/nfc/cards.json` (`{"cards": {uid: name}}`), kept in Settings -> Devices -> NFC tags: a list of
the tags, each with its name, its last tap and the rules its tap starts ("+ Rule" opens the rule window on that tag, the
event "NFC tag tapped"). The pen renames a tag: its rules follow the new name (a tap is the gesture "<name>"), a switched
off one stays off. Remove takes the tag off the list with its rules.

"+ Add a tag" asks for a name and waits 2 minutes for a tap: the next tap of a sticker of ours (it verifies with the keys)
that is not on the list, on any phone with the Yash! app (or a browser), puts it there with that name, and the phone says
"Added". Its counter is stored, so the link that tap made never counts. A listed tag tapped meanwhile does its job as
always. A sticker of ours tapped while nothing waits is refused and shown there for 10 minutes with Add. The API also
takes a typed UID (`POST /api/nfc/cards`; a new sticker: its first tap counts).
