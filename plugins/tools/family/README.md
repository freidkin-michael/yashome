# family - the Yash! app for the people of the house

The phones of the house get the dashboard in an app: their devices, ring a phone, the house state
while away. Each phone is paired by one QR from the dashboard and gets a token of its own; the
house token (`DASHBOARD_TOKEN`) never goes to a phone.

| part | where | what |
|---|---|---|
| this plug-in | the core | phone tokens, rights, the pairing page `/plugins/family/static/pair.html` (it only opens the app), the app download `/plugins/family/yash.apk` (the link, the build and the pairing of new phones are all on the Family page) |
| `bridge/` | a container on this host - optional (below) | Firebase: the sign-in token the app gets once with its claim, pushes (ring, sync), `/state` for the phones |
| `static/` | served by the core | `pair.html` (the QR's landing page; it clears the code from the address bar and the history), `qrcode.js` (qrcode-generator 1.4.4, Kazuhiko Arase, MIT - `qrcode-LICENSE.txt`; its few block characters written as `\u` escapes) |
| `app/` | Build the app in the dashboard, or `app/build.sh` | the Android app; the APK lands in `plugin-state/family/yash.apk` (outside git and backups: it is rebuilt); Firebase in it only with your `google-services.json` |

## With Firebase or without

- **With Firebase** (`FAMILY_FIREBASE_DB` set): steps
  1-5 below. A ring is a push and reaches the phone anywhere. Widgets and app updates go by a
  data-less "tickle" (`{cmd: pull}`): the phone then asks the house over HTTPS
  (`GET /api/family/me/pull`) and tells it its network (`X-Net-*` headers, a report) and the
  answer it applied last (`ack`); a network change is `POST /api/family/me/report`. The phone has no
  service and no open connection; `/api/family/live` is open only while the app screen is.
  The phones see the house state while away (`/state`).
- **Without** (both empty): no Firebase account, no bridge container. Step 4 (the app) and step 5 (pair): the
  address for the phones is this house on its network (`BIND_ADDRESS` and the dashboard's port) unless Settings ->
  House -> Family -> App names another. The
  QR holds that address and a one-time code the app swaps for its token; the app keeps a connection to the house open
  (`/api/family/live`, a quiet notification shows it), and rings, widget state and updates go down it - only while the
  phone is on the house's network or the VPN. No away state.

A ring goes every way there is (the live connection and the push), and the list shows "reachable
now" when the phone holds the connection; only "last ring ack" says the phone really rang. Rules
can ring a phone too: Rules -> then -> "Ring a phone" (5-300 seconds, or Stop ringing); a rule rings
a phone at most once a minute. Only the dashboard (the house token) stores or runs such a rule - a
phone with full access cannot ring the others. "Stop" in the list ends a ring.

The app talks to the address for the phones (Settings -> House -> Family -> App; empty = this house on its network) over https, or
plain http on your own network or VPN - never plain http over the internet. **With Firebase, use an address
the phones reach from outside**: with a LAN or VPN-only address the tickle still wakes the phone
away from home, but its pull fails (widgets and updates do not arrive); the house logs a warning at
start. An address set there reaches the phones already paired too: each pull carries it (`srv`), and the app
moves to it once its own token works there (at most one try an hour) - no new QR. On each phone, once: allow notifications and the location permissions, and try Test ring in
its settings (app/README.md); the app never asks to skip battery optimisation.

## Presence: who is home

The Family button in the header (before Log out; also Family phone in the Add menu) opens the Family page, the same as
Settings -> House -> Family: a strip with the add form and the app, a square per phone that opens its window lists each paired phone with a dot - at home, away or
not known yet - and "At home: ... Away: ..." above the list, next to Rights, Ring and Delete. Each phone
also has a device `person_<name>` (hidden from the tab grids) with `home` (true / false) and the event
`arrival` / `departure` for rules. A phone is home while the house believes it is on the house network. The app tells the house - when its network changes and when it
starts, not by a heartbeat - the gateway of its Wi-Fi network (a VPN is skipped), its OWN address on that network and, with the location
permission, the network's name (`POST /api/family/me/report`). The report is a state: home stays until a later report says otherwise (a
network that is gone is a report) or for 24 hours (`NET_HOME_MAX_S`) without any contact. Home drops only on a positive away report (a report or a pull from another
network or from mobile data), never on silence alone: a still phone in deep Doze may run no work for hours. A pull or report from the home
network renews the 24 hours; the last reports survive a house
restart (`plugin-state/family/net_home.json`; the entry of a revoked phone is forgotten, so a phone paired again does not inherit it). The open app screen's live link (`/api/family/live`) from a house-network address
(the house's network: `BIND_ADDRESS`'s /24, else that of the phones' address when it is a private IPv4 one, else none) counts too. A reported address in the house's network, or a gateway the house learned,
makes the phone home even with an always-on VPN; once the house learned a Wi-Fi name the name must match too, so a router elsewhere with
the same default address does not count. Nothing is set by hand: a report from the house's network teaches the house its router's
address and Wi-Fi name (`plugin-state/family/home_wifi.json`; the name is learned once, from the first report that has one; a report whose name does not match teaches nothing). Android lets an app read the name only with the location permission (the app asks once, then once more for
"Allow all the time", and explains; the location itself is never read or sent). A phone that could not read the name reports
`ssid_known=false` (the app keeps the last known name for the same gateway when a read fails): the name is then not asked and the address alone decides. The surest signal is the phone's MAC seen on the house Wi-Fi by the presence
plug-in: the phone's window on the Family page -> "Also at home when" -> that presence device (`PUT /api/family/<phone>/presence`). After the last such moment the phone stays home for 600 seconds
(default 600). After a house restart nothing is published, and no event fires, until a report is read back or the grace has passed.

## Updating the app from the house

Build the app (Build the app, or `app/build.sh`): the APK in `plugin-state/family/yash.apk` is the one
the phones fetch; Settings -> House -> Family -> App shows `yash.apk | <versionName> (<versionCode>) | <built>`. The app tells the house its version with each report and each pull; the list shows it and, when it is
older than the built APK, an "Update app" button (`POST /api/family/update`, the dashboard only; `name`
empty = every phone with an older app). The house then sends `update` down the phone's live link when its app screen is
open; otherwise the command waits for the phone's next pull and the phone is woken by a high-priority, data-less Firebase message. The
app downloads the APK from its own house address (not one named in the command), checks its sha256 and
size, and hands it to Android's installer, which accepts only an APK signed with the same key. From
Android 12 an app that installed the previous version updates without a tap; otherwise a notification
asks for one. Once per phone: Settings -> Apps -> Yash! -> Install unknown apps -> allow (the app
notifies when that is missing). The house also sends the command by itself, at most once an hour per phone, when a
phone reports an older version; the app's settings have "Check for an update" (`GET /api/family/me/app`).

## Widgets on the phone

The Yash! app has one home-screen widget, Channel (1x1): a switch, a lock, a dimmer (top third
brighter, bottom third dimmer, middle on/off) or an air conditioner (its mode), with a short name
and an icon of your choice (app/README.md). The app tells the house which channels its widgets
show (`PUT /api/family/me/widgets`, within the phone's rights); the house looks at them every 2 s.
In a house without Firebase the change goes down the live link (`{cmd: state}`). In a Firebase house, only if you allow it
(Family phone -> "Widget states through Firebase", off by default) the house sends a tickle and the phone pulls the change over
HTTPS: nothing of the house goes through Google. The tickle gaps, per phone: a normal change at most once per 120 s; a
lock's change (high priority) after 10 s; a tickle the phone has not answered with a pull is repeated after 600 s, and a lock's gap
doubles with each unanswered tickle (10, 20, 40 ... up to 600 s). Without the setting the widgets update when tapped, every 15 minutes
and when the app is opened; while the phone charges the app also pulls every 2 minutes (battery is no concern then).

## The keys and tokens at a glance

| what | where you get it | where it goes | secret? |
|---|---|---|---|
| `google-services.json` | Firebase console -> Project settings -> Your apps -> the Android app -> download | `plugins/tools/family/app/app/google-services.json` (git ignores it) | no (it is inside the APK), but it is yours |
| service account key (JSON) | Firebase console -> Project settings -> Service accounts -> Generate new private key | a file outside every repository, e.g. `/srv/secrets/firebase-key.json`; its full path in `FAMILY_FIREBASE_KEY` | **yes** - full admin of the Firebase project |
| database URL | Firebase console -> Realtime Database -> the URL at the top (`https://<project>-default-rtdb.<region>.firebasedatabase.app`) | `FAMILY_FIREBASE_DB` | no |
| package name | you choose it once (default `com.yash.home`) | the Android app in Firebase, and `FAMILY_APP_ID` | no |
| address for the phones | this house on its network by default; a domain over https, a VPN address: Settings -> House -> Family -> App | settings `family.server_url` | no |
| `FAMILY_BRIDGE_KEY` | `make install` / `make update` generates it | `.env` (and the bridge host's `.env`, the same value) | **yes** |
| `MQTT_FAMILY_PASSWORD` | `make install` / `make update` generates it | `.env` (and the bridge host's `.env`) | **yes** |
| a phone's token (`yfd_...`) | made when the app swaps the QR's one-time code (`yfc_...`) | nowhere by hand: the app keeps it | **yes** - only its sha256 is kept here |
| release signing key | made by the first Build the app, or your existing key imported (step 4) | `plugin-state/family/app.keystore` + `app.keystore.pass` | **yes** |

## Step by step

### 1. A Firebase project of your own

1. <https://console.firebase.google.com> -> Add project (Analytics is not needed).
2. Build -> Realtime Database -> Create database (pick a region; start in locked mode - the bridge
   writes the real rules in step 3).
3. Build -> Authentication -> Get started (no sign-in provider needs enabling: the app signs in with
   custom tokens the bridge makes).
4. Project settings -> General -> Your apps -> Add app -> Android. Package name: `com.yash.home`, or
   your own (then set `FAMILY_APP_ID` to the same). Download `google-services.json` and put it at
   `plugins/tools/family/app/app/google-services.json`.
5. Project settings -> Service accounts -> Firebase Admin SDK -> Generate new private key. Store the
   JSON file outside every repository (not in the house repository either), readable by the user
   the containers run as (`PUID`), for example:

       sudo install -d -m 700 -o $(id -u) /srv/secrets
       mv ~/Downloads/<project>-firebase-adminsdk-*.json /srv/secrets/firebase-key.json
       chmod 600 /srv/secrets/firebase-key.json

### 2. `.env` of the house

    PLUGINS=...,family
    FAMILY_FIREBASE_DB=https://<project>-default-rtdb.<region>.firebasedatabase.app
    FAMILY_FIREBASE_KEY=/srv/secrets/firebase-key.json
    # FAMILY_APP_ID=org.example.yash      only if you chose another package name

A full path for `FAMILY_FIREBASE_KEY` (no `~`). Then `make update`: it generates `FAMILY_BRIDGE_KEY`
and `MQTT_FAMILY_PASSWORD`, creates the bridge's broker account (it writes `family/#`, reads
`home/state/#`), switches the compose profile `family` on and starts the bridge.

### 3. The database rules (once)

    docker compose run --rm family-bridge python yash_bridge.py rules

(after `make update`, which builds the bridge image).

### 4. Build the app

Settings -> House -> Family -> App -> Build the app: the build server's app builder (Settings -> Devices -> Computers and
servers: a computer with the Android app builder) builds it from `app/` here, signs it with the house's key and puts it where the pairing
page offers it. The key is made at the first build and kept in `plugin-state/family/app.keystore`
(with `app.keystore.pass`) - in the backup; lose it and every phone has to uninstall the app once.
With Firebase, put your `google-services.json` into `plugin-data/family/` first.

**A key you already have** (phones run an app signed with it): BEFORE the first Build, put it there
yourself - the keystore as `plugin-state/family/app.keystore`, its password as `app.keystore.pass`
and its alias as `app.keystore.alias` (when not `home`), each `chmod 600`. The package must be the
same too (`FAMILY_APP_ID`). Otherwise the first Build makes a new key and the phones cannot update.

By hand, on any host with docker - the same key (the house's, when there is one):

    plugins/tools/family/app/build.sh

It builds in a container and copies the APK to `plugin-state/family/yash.apk`, where the Family page
offers it. Without `app/google-services.json` it builds an app without Firebase (for a house without
it): the phone then keeps a link to the house open - a quiet "Connected to the house" notification -
and starts it again after a reboot. The signing key: the house's (`plugin-state/family/app.keystore`,
under a name of its own for the build, never touching an `app/release.keystore`); without one,
`app/release.keystore` with `ANDROID_KEYSTORE_PASSWORD` in the environment, else `app/debug.keystore`
(made once by build.sh - keep it, a new key means uninstalling the app on every phone). With both a
house key and a different `app/release.keystore`, build.sh stops: import yours as the house's key
first (above), so the dashboard and build.sh sign with the same one.

### 5. Pair a phone

Dashboard -> Add -> Family phone -> a name in latin letters (`kid`, `phone1`), tick Full access only
for a phone that may do everything the dashboard does -> Create QR. Scan the
QR with the phone's camera: the page installs the app (allow installs from the browser when Android
asks) and opens it; the phone connects by itself. The QR holds a one-time code, not the phone's
token: the app swaps it for a token of its own at once, and the code dies (a second scan, or a photo
of the QR, gets nothing). Unused, it dies after one hour (the list shows waiting / connected / qr
expired). Until the phone has used it, whoever scans it first gets the phone's place: show it only
to the phone, close it after the scan.
"New QR" in a phone's row (a new phone, a reinstalled app, an expired QR) makes a new token for that
name at once - the old one stops working - and keeps its name and rights. So does Add with a listed
name: the Full access box counts only for a name not in the list; Rights changes a listed one.

One phone, one entry: the app sends the phone's ANDROID_ID (`and-...`, it survives a reinstall signed
with the same key) when it claims a QR. A phone the house knows by that id gets its own entry back (its
name, widgets and the rules that ring it), when the QR was made for a new name; its old token dies,
and the QR's placeholder entry goes. Never matched by name. An app that sends no id still
pairs by its QR alone; the list marks a phone paired without the id ("New QR with an updated app
stops duplicates"). The absorbed entry keeps its name, widgets and rules; its rights and screen come
from the new QR (the admin's latest choice: a restricted QR never hands out the old full rights). A
New QR of a listed phone is never folded into another entry: claimed by a phone known under another
name, it is refused (409, "delete one of the two, then pair").

Delete (the dashboard, the house token only) removes a phone for good: its entry (token, QR code,
widgets, rights), its open sockets, its Firebase sign-in and push token, and the rules that ring it. A
row only Firebase still lists ("only in Firebase: a leftover") is deleted the same way.

A renamed device keeps its place on every phone: the rights, the app screen and the widgets follow the
new id.

Full access is "use the house, do not set it up": such a phone reads everything and changes devices,
rules, favourites, the player and voice, nothing else; the list is in docs/security.md ("A family phone with full access").

Rights: Family phone -> Rights. Full access, or per device hidden / view / control; hidden devices
never reach the phone from the house server. A new phone without Full access sees nothing there until
you give it devices. The away state (the topics of state.json, through Firebase) is the same for
every paired phone: rights do not cover it.

### Typing it by hand (the app's "Advanced")

The app's setup screen has Advanced: server address + "Dashboard token". Use it only for your own
phone, with the house token (`DASHBOARD_TOKEN` from `.env`): that phone then has everything the
dashboard has and cannot be revoked on its own (changing `DASHBOARD_TOKEN` logs out every client).
For the family, always pair by QR.

## The bridge's own environment

`setup/compose.yml` sets them from the house's keys, never by hand: `YASH_FB_DB` (=
`FAMILY_FIREBASE_DB`), `YASH_FB_KEY` (the key file mounted from `FAMILY_FIREBASE_KEY`), `YASH_STATE_CFG`
(the whitelist, `/house/state.json`), `BRIDGE_KEY` (= `FAMILY_BRIDGE_KEY`), `MQTT_HOST` / `MQTT_PORT` /
`MQTT_USER` / `MQTT_PASS` (the broker `mosquitto:1883`, `MQTT_FAMILY_USER`, `MQTT_FAMILY_PASSWORD`). Three keep the code's default: `BRIDGE_PORT` (8790), `YASH_I18N` (`/app/i18n.json`, the labels) and
`YASH_QR_DIR` (`.`, where pairing QR images go).

## What the phones see while away

`plugin-data/family/state.json` (made from `setup/state.example.json` at the first `make update`)
lists the topics that go to Firebase `/state` - Google sees them, so list only what you want there.
The bridge account reads `family/#` and `home/state/#` only. Labels are English; the `"i18n"` part
adds the house's own words per language.
