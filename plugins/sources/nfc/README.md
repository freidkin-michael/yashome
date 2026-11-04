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
| `_ntag424.py` | writing a card: EV2First authentication, secure messaging, the commands, the NDEF template (no I/O) |
| `i18n.json` | the page's words |
| `setup/env.example` | `NFC_SDM_META_KEY`, `NFC_SDM_FILE_KEY`, `NFC_MASTER_KEY` |
| `setup/settings.example.json` | `card_url`: the link written into new cards, stored by the house (no setting in the dashboard) |

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

## Writing a card

"Write a card" in the Yash! app's menu (an administrator: the house token, or `core.is_admin_request` where the core has
it; the dashboard only says where to find it) makes the house write a blank NTAG 424 DNA, or one it wrote before,
through the Yash! app. The keys never leave the house: the phone only carries APDUs, which are MACed or encrypted with
session keys. Needs `NFC_MASTER_KEY` (key 0, 32 hex) beside the two SDM keys; without it `start` answers 503. The card's
link is the stored `card_url` (the house's settings), else the first https address of `DASHBOARD_ORIGINS` +
`/nfc?picc={PICC}&cmac={MAC}`, else the https address the write was started on (the proxy's Host), which is then stored
in `card_url` so every later card gets the same link. With none of them `start` answers 409: open the dashboard on its
https address.

What the card gets (`_ntag424.write_card`, the flow of the Android writer): select the NDEF application; authenticate key 0
with the house key, else the factory key (a card of another system is refused untouched); a card that takes the house key
has its UID read at once, and a card on the list stops the write before anything is written (below); the NDEF file = the URL record
(`{PICC}` and `{MAC}` as `*` runs) then an Android Application Record (`FAMILY_APP_ID`, default `com.yash.home`); file 02
settings: SDM on, PICC data (UID + counter) encrypted with key 1, the CMAC with key 2, access R/W/RW = key 2, change = key 0;
then keys 1, 2, 0 become the house's (key version 1); then the house authenticates with its new key 0 and reads the UID.
No name is given up front. A card not on the list goes there under an automatic name: "Card <n>" in the house language
(Settings -> General; the word from `i18n.json`), n the lowest number no listed card has in any
case; the result says `new: true` and the phone offers to name it. A card on the list keeps its name and its rules.
The log names the card by its name on the list.

The session (one at a time; it ends 60 s after its last step; nothing secret in any answer or log line):

- `POST /api/nfc/write/start` (no body needed) -> `{sid, step, done, result, left}`; the phone that called it drives the
  session, no other phone is told.
- `POST /api/nfc/write/{sid}/step {}` -> `{apdu_hex, step, step_text}`: the first APDU (or the unanswered one again, after a
  lost reply).
- `POST /api/nfc/write/{sid}/step {resp_hex}` (the card's whole answer, SW1 SW2 included) -> the next `{apdu_hex}`, or
  `{done: true, result: {ok, step, step_text, uid?, error?, message?, note?, listed?, new?}}`: `listed` = the card's name on
  the list, `new` = it was put there now under the automatic name.
- Language: `step`, `error` (`cancelled` on a cancel) are the log's English words; `step_text`, `message`, `note` and every
  refusal's `detail` are in the asker's language - `?lang=xx` on any `/api/nfc` route (the app sends its own), else the
  house language (the dashboard). The words are in this plug-in's i18n.json.
- A card already on the list: `result = {ok: false, step: "uid", known: <its name>, uid}`, nothing written. The phone
  asks "This is the card <name>. Rewrite it? It keeps its name and rules.":
  `POST /api/nfc/write/{sid}/rewrite` starts the step loop again for that card (it is held again; the session keeps its
  address), `DELETE /api/nfc/write/{sid}` ends the session and leaves the card as it is. A blank card is never asked about.
- `GET /api/nfc/write/{sid}` (the session's state), `DELETE /api/nfc/write/{sid}` (cancel).
- Naming a new card: `PATCH /api/nfc/cards/{uid} {name}`, the rename of the tags list (the dashboard's pen), open to an
  administrator; its rules follow the new name.

The phone (Yash! `NfcWriteActivity`): "Write a card" in the menu opens the "Hold the card to the phone" screen at once and
calls `start`. The screen connects IsoDep (5 s timeout), asks for the first APDU, then loops transceive -> step until
`done`, showing the step. A card moved away says so and Try again cancels the session and starts a new one; a 60 s pause
ends it. A listed card shows the question with Rewrite and Cancel; Rewrite says "Hold the card to the phone again". A new
card shows "Written: Card 1" and "Name this card" with that name filled in: Save renames it, Close keeps the automatic
name. Reader mode stays on while the screen is open, so a card left at the phone is ignored, never handled as a tap.
