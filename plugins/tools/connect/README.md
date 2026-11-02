# connect - one Add / bind window for every way a device joins

The header's Add button opens one window (owner) that starts every way at once and lists everything new in one live list, each row
tagged with its kind and bound in place:

| kind | found by | bound with |
|---|---|---|
| Zigbee | permit-join of the `zigbee` plug-in (opened with the window) | a name, `POST /api/zigbee/pair/add` |
| ESPHome | the core's MQTT discovery (online nodes with channels, not added yet) | a name and a tab, `POST /api/esphome/add` |
| yashGen | a secured `<name>-setup` network in the scan of the `provision` plug-in | the code on its label, `POST /api/pair/start` (`pair`) |
| Lost its Wi-Fi | an open `<name>-setup` network in the same scan | Send Wi-Fi, `POST /api/esphome/ap/provision` (the closest relay) |
| Tablet | the `panels` invite (opened with the window), the launcher answers | the code on its screen and a panel name, `POST /api/panels/bind` |
| ESP screen | a new screen's announce (`panels`) | the code on its screen and a panel name, `POST /api/panels/bind_esp` |

A code typed in the top box is matched against every waiting tablet and ESP screen (`POST /api/connect/code`, house token: the
kind, id and model of each match, never a code); the row is filled and shown, Bind stays a human's press. A tablet that is bound
already and shows its code again (its pairing screen) is matched too: "This is X, bound already to panel P. Bind it again?" binds it
with `keep_key` (below), so a known tablet needs no invitation over the network. A yashGen code is its
setup network's key, which no list knows: with exactly one yashGen device in sight the code goes into its row.

`POST /api/connect/start` opens Zigbee pairing and the tablet invite (a source that fails is named in `errors`, the others stay
open), `POST /api/connect/stop` closes both; the window calls stop when it closes. Each window (its own id, required) holds the ways it
keeps open on a lease of 120 s, renewed by its start every 60 s: a dead tab lets go, a window back from a long sleep or whose renewal
failed keeps its hold, and a way opened on the Zigbee or Panels page is never closed by a window. The setup network scan repeats every 40 s (and on
"Look for networks again"), the lists every 2 s, ESPHome discovery every 6 s. A plug-in that is off is skipped.

What no scan finds stays under "Other ways" in the same window: the `addMenu` rows without `scan: true` (an ESP screen's first
firmware over USB, pairing a yashGen device by its QR, the family phone, SwitchBot, Touchmouse). The Panels tab's "Connect an Android tablet"
opens this window too. Without this plug-in the Add button opens the Add menu (the same rows, one by one).

| file | what |
|---|---|
| `__init__.py` | `POST /api/connect/start`, `/stop`, `/code` (the house token) |
| `ui.js`, `i18n.json` | the Add / bind window |

Setup: turn `connect` on in Settings -> Maintenance -> Plug-ins and update; it uses the plug-ins of the kinds above that are on.
There is no Settings page.
