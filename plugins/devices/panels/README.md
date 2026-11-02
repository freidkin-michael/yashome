# panels - wall panels: a tablet in a browser, or an ESP screen

A panel is a layout of widgets (switches, dimmers, sensors, clock, weather, AC, cameras) for one wall
screen. The builder draws it; a tablet shows it in a browser; an ESP screen gets it over MQTT.

| part | where | what |
|---|---|---|
| this plug-in | the core | layouts, the Panels tab (by room) and the same page in Settings -> Devices -> Panels, the builder `/panel-builder`, the kiosk page `/panel-view.html?panel=<name>`, pairing of Android tablets, Add -> "ESP screen: its first firmware over USB" |
| `discover/` | the `panels-discover` container, in the host's network | invites tablets by a UDP broadcast (port 51823) while pairing is open |

Files: the layouts are configuration, `plugin-data/panels/layouts.json` (kept in the house's git);
`plugin-state/panels/` holds the bound tablets (`paired.json`), the pairing window and who answered.

Setup: turn `panels` on in Settings -> Maintenance -> Plug-ins and update. `make install` /
`make update` makes the keys of `setup/env.example`. ESP screens and the launcher app also need the
`build` plug-in and a build server (Settings -> Devices -> Computers and servers).

## The Panels tab (and Settings -> Devices -> Panels)

Each panel is a card with its live picture (`/panel-view`); a click opens the builder (`/panel-builder`) inside the tab,
"All panels" returns. The Panel builder button and Edit open the builder in the same tab (no new window: an installed dashboard would open it as another app). Under "Manage panels": Edit, Flash, the room,
Pair again, Delete and the screen's MAC. Every card and every picture says its kind, with an icon: "Android tablet" (the
launcher) or "ESP screen" (its own firmware); "Manage panels" lists the Android tablets first, then the ESP screens,
each by room.

## A tablet

The panel launcher, Home Panel, is a small Android app in `launcher/` (a full-screen kiosk that
pairs, keeps the screen on and reports its battery; see its README). The self-update, the package
move and the reports below serve a launcher of your own that supports them; the shipped one does not. It needs Android 7 or newer.
Panels -> **Build the app** builds it on the build server's app builder (Settings -> Devices -> Computers
and servers: a computer with the Android app builder) with the house's key and offers it at `/plugins/panels/panel.apk` (open: a tablet has
no key before it pairs). A tablet can also show a panel in its browser: sign the browser in on the
dashboard (Settings -> This screen -> Sign-in -> Another browser) and open `/panel-view.html?panel=<name>`.

`GET /plugins/panels/app` (open) names the published launcher: `{code, name, package, size, sha256, path}`, read
from the APK's own manifest. A launcher that updates itself asks it, downloads `/plugins/panels/panel.apk` when
`package` is its own and `code` is newer, checks size and sha256 and hands it to the Android installer (the signature is checked
there). The heartbeat says when to: a tablet whose `caps.app_code` (or, when it sends no code, its `caps.app` name)
differs from the published launcher, on the same package (`caps.pkg`, when it sends one), gets `"update": true` in the heartbeat's answer, and a launcher that knows
it checks at once (a failed install is retried at most every 5 minutes) instead of at its half-hour check.
The launcher row (Panels -> Manage panels) shows `panel.apk | <versionName> (<versionCode>) | <built>` (the version comes with
`GET /api/panels/launcher` as `apk_name`/`apk_code`) and a **QR** of the APK's address on this dashboard (written under it: through a tunnel it is no use), for a tablet's
camera (`static/qrcode.js`: qrcode-generator 1.4.4, Kazuhiko Arase, MIT - `static/qrcode-LICENSE.txt`). The tablet
list shows each tablet's launcher version from its last heartbeat, `3.28 -> 3.32` when it is behind the published
one (the same test as the heartbeat's, against the version a finished build shows), and its last update error.
A house with a launcher of its own puts that Gradle project in `plugin-data/panels/launcher/`
(it has `app/build.gradle.kts`): **Build the app** then builds it instead of the shipped `launcher/`.

**Another package name.** `PANELS_APP_ID` names the launcher package the house builds (`-PyashAppId`). A tablet
whose heartbeat carries a `caps.pkg` listed in `PANELS_MIGRATE_FROM` gets `"transfer_owner": <PANELS_APP_ID>` at
most every 10 minutes, and each ask is logged. The move is one way: a tablet on `PANELS_APP_ID` is never asked,
and one on a package that is not listed is not asked either (Health names it). A launcher that supports it does nothing while that package is not installed.
Once it is installed, the launcher hands over the device owner, HOME and all its settings (pairing included), then
goes quiet. Health warns when a tablet stays quiet 2 minutes after an ask and the new package never beat. The
package gets onto a tablet by `adb install`, one tablet at a time: a launcher that checks `package` never fetches
another package's APK. A launcher that does not check it installs any published APK with a higher `code`, and as
device owner it installs a different package silently. Every such tablet then moves at its next ask.
The package it moved from stays installed until it is removed (`adb uninstall`).

1. Install the launcher on the tablet (`http://<house>/plugins/panels/panel.apk`) and start it: it
   waits for an invitation.
2. Dashboard -> Panels -> **Connect an Android tablet** (with the `connect` plug-in: the Add / bind window, which the header's Add button
   opens too). For 5 minutes the house invites tablets of this network.
3. The tablet appears with a code on its screen: type the code, give the panel a name, **Bind**.
   The panel is made for that screen (its size, its camera and microphone).
   With the cameras plug-in on, the tablet's cameras become cameras of the house (Cameras tab).
4. Panels -> **Edit** opens the builder; the tablet shows the result at once.

On the tablet (docs/device-state.md) a widget whose device is offline (an AC and a remote too) is
greyed and takes no tap; one whose device waits for its report after a start or an outage is dimmed
less; there is no caption, only the shade. The tablet sees a change within its poll (3 s). After a tap
on a dimmer, its switch or a lock the widget pulses until the device reports (the panel looks again
every half second meanwhile) and says "No answer from <widget>" when it did not within 5 s; when the
server does not answer, the wait ends without that message (the status dot shows the link).

The tablet gets a key of its own (`ypk_...`), never the house token. It goes out once, within 10
minutes of the bind, and only to the address the tablet registered from; only its sha256 is kept.
The key opens the panel's own layout and only the devices and cameras its widgets use (their states
and snapshots; control only of a device a widget commands - its command topic, or the state topic of
a toggle, button or dimmer the kiosk taps; only a toggle, dimmer, button, AC, remote or climate
widget gives control (a new widget type gives none until it is listed); `/api/devices`,
`/api/cameras` and `/ws` show only those), weather, health, the touchmouse receivers and the
heartbeat; nothing else (no settings, rules, pairing or other panels). A device added to the panel
is reachable once the layout is saved. Unbind, a new bind or deleting the panel kills the key, also
on an open `/ws`.

One tablet, one record. The launcher's id is `and-` + the tablet's full ANDROID_ID, which survives a
reinstall signed with the same key. A tablet paired again (**Pair again**, or it simply asks again)
keeps its record and its panel (a tablet that still has a working key asks again only after a long
press on its brand corner, and then keeps its code): the dashboard shows it waiting with its code,
its key works until a human checks that code, then a new key replaces it. A short id (`and-` +
8 characters) takes the full id in place at its first heartbeat with its own key (cutover-compat). **Delete** on a panel removes its layout, its tablets with their keys and
its retained messages; **Unbind** (in Connect an Android tablet; with the `connect` plug-in in **Android tablets**) frees only the tablet.
`POST /api/panels/bind` with `keep_key: true` binds a tablet that is bound already (the code still checked) to a panel without a new
key: its next heartbeat names the panel; should its pairing screen poll within the key window, it gets a new key then. A tablet bound to
another panel leaves the one it served. A pairing-again from another address within 10 minutes is
refused, and one nobody confirms lapses after 10 minutes. A full id also drops a bound record of the
same tablet's short id (cutover-compat).
Binding, unbinding and deleting need the house token: a phone of the family plug-in cannot do them.

Tablets find the house by a broadcast, so they must be in the same network (Wi-Fi and LAN of one subnet).
The address they get is this host's LAN address and `DASHBOARD_PORT`; the broadcast is 255.255.255.255.
Tablets must reach the dashboard directly (its LAN address), not through a reverse proxy (pairing
through one is refused - the proxy must pass X-Forwarded-For, as the nginx example in docs/security.md
does, or every tablet would have the proxy's address): pairing counts
waiting tablets per address (5) and a tablet's camera is fetched from the address its heartbeat came from
(a loopback, link-local or docker-gateway address is never used; only the launcher's camera port 8081).

## Launcher watchdog

A bound tablet whose launcher stopped (no heartbeat) gets it started again. Each tablet's row under Panels -> Android tablets
(Connect an Android tablet when the `connect` plug-in is off) has
**Raise the launcher if it fell** (on by default), **after N min** of silence (2) and **at most once in M min** (10), kept in
`paired.json` as `watch: {on, after_min, every_min}` (`PATCH /api/panels/bind/<id>/watch`, the house key; 1-60 and 1-240 min).
Every 30 s the plug-in looks at the bound tablets; of those silent for N minutes (counted from the plug-in's start after a
restart, when the beats in memory are gone) and not asked in the last M, the one asked longest ago gets one call of
`start_launcher` (one tablet per pass, one pass at a time). Each call is logged (`[panels] watchdog <id>: ...`); the row shows
**last raise: HH:MM** and a failure's reason, or **adb unavailable** when the tablet could not be reached (nothing was tried),
until it is heard again.

`start_launcher` asks the host, never adb itself: the adb key stays on the host, out of the web-facing container. The helper is
`tools/panel-start/panel_start.py`, started per connection, as the house's user (its adb key), on `/run/yashome/panel-start.sock`
(the house's group only; not installed: no try, "adb unavailable"). `sudo sh tools/host-sockets.sh` (once, and again after an update that changes the host pieces) installs the house's helpers
on its host: this one too when the plug-in is on and adb is
there. No port, no key, no address.

It takes one line, `start-launcher <tablet id>` (256 bytes, 5 s; 8 connections at most). It refuses
any other verb or argument count, an id that is not a bound tablet of `plugin-state/panels/paired.json`, and an
address outside the house LAN (a private IPv4 of the host's own interfaces; docker bridges, VPN and loopback do not count).
Then it runs only `adb devices`, `adb connect <ip>:5555` and `<ip>:<caps.adb.port>` when the tablet is not connected, and
`adb -s <serial> shell am start -a android.intent.action.MAIN -c android.intent.category.HOME` (the launcher the device owner pinned as HOME, whatever its package), 30 s in all; the plug-in waits 35 s. Its answer: `ok`,
a failure's `why`, `noadb` (no connection) or `refused`; no answer at all counts as `noadb`.

## Touchmouse in the status bar

An Android panel can show a mouse button in its status bar: pick a receiver (a TV box or a computer
from `GET /api/touchmouse`) in the builder's **Touchmouse** select. The layout keeps it as `"bar":
[{"type": "touchmouse", "receiver": "<receiver id>"}]` (at most 4 items; a `{"type":
"airmouse", "device": ...}` item is converted to it on save; cutover-compat). A tap on the button opens a
full-screen touchpad: a finger moves the pointer, a tap clicks, a two-finger tap right-clicks, two
fingers dragged up and down scroll, a three-finger tap clicks the wheel, plus Back, Home and
Keyboard (the key `kbd`, for a binding on the receiver). The launcher itself sends the UDP
(`Panel.touchmouseStart/tmMove/tmClick/tmMiddle/tmScroll/tmKey`; a launcher without `tmScroll`
keeps the pad working without the wheel); the
button shows only when the receiver exists and the launcher has the bridge. The touchpad closes
after 5 idle minutes or when the screen goes off. A panel key may read `/api/touchmouse`, not change
it. ESP screens have no bar.

## Touchmouse widget

An Android panel can also hold a **Touchmouse** widget (`{"type": "touchmouse", "receiver": "<receiver id>"}`, with x, y, w, h like
any widget): a touchpad, Back / Forward / Keyboard, the four arrows as a cross with Enter in the middle and a text field with Send,
all inside the layout and sent to the receiver by the launcher. A launcher without `Panel.tmText` shows only the pad, Back and
Keyboard; one without the bridge shows a note to update the app. A panel key may read the receivers of its widgets.

## Charge window

A tablet on the wall charges all the time, which wears its battery out. With a smart plug on its charger
the house keeps the battery between two thresholds (`PUT /api/panels/<name>/charger` `{"device":
"<plug>", "min": 40, "max": 85}`); 40 and 85 are the defaults.

## ESP screens

One screen, one panel: a MAC that belongs to a panel already is not bound or confirmed under another
name (409, "this MAC is panel X's: one screen, one panel"); bind it under its own name, which keeps
its layout.

An ESP screen (a board with a profile in `profiles/`: `waveshare7`, `onx35`) runs ESPHome firmware made
from its layout by `firmware/gen.py`; the build plug-in compiles and flashes it: turn `build` on in
`PLUGINS` and give it a build server (Settings -> Devices -> Computers and servers). The screens join the
house Wi-Fi (`HOUSE_WIFI_SSID` / `HOUSE_WIFI_PASSWORD` in `.env`, 2.4 GHz), reach the broker at `BIND_ADDRESS`
and their firmware has no language: their labels are the layout's, typed by hand; the rest - the date
(`home/clock/date`) and the firmware's own words (the network state, the new screen, the AC card, the weather's
words) - the house sends in its language, retained on `home/clock/words`; a screen keeps the last ones in its flash
and shows English until the first come. A new language of the house needs no new firmware. `make install` / `make update` makes the screens'
broker account (`MQTT_PANELS_USER` / `MQTT_PANELS_PASSWORD`), their OTA password and the password of
their own fallback Wi-Fi (`PANELS_AP_PASSWORD`: a screen that loses yours opens `<name>-setup` after a
minute, to be given new Wi-Fi details from a phone).

1. Panels -> **Connect an ESP screen**: pick the board, **Build the first firmware**, **Download the
   firmware** and flash it over USB with ESPHome Web (web.esphome.io, Chrome or Edge) or
   `esptool.py write_flash 0 panel-<board>.factory.bin`. **Flash over USB (beta)** does the same from the
   dashboard (Chrome or Edge, dashboard over HTTPS; pick the screen's port, never a Zigbee stick); on
   Linux it connects to an ESP32-C3 only now and then, so the download is the dependable way. If a
   screen stays dark after any attempt, unplug it and plug it in again (it may wait in its loader).
2. The screen joins the Wi-Fi and shows a six-digit code; it appears in the same window. Type the code,
   name the panel (small latin letters, digits or -: it becomes the node name), **Bind**: the panel is
   made for that board and its own firmware goes to it over the air (through safe mode, like every flash);
   the screen stays listed as new until that firmware is in.
3. Edit the layout in the builder, then **Build and flash** there (it saves first) or **Flash** on its card. The screen restarts in safe mode first
   (no display, so the flash write is not starved by the screen), then takes the new firmware. A screen that
   does not restart within 30 s, or does not come back in safe mode within 90 s, ends the job: the build server drops it.

A screen built by this plug-in has a broker account of its own (see "Broker accounts of the screens"): it reads
the house state (`home/state/#`, `home/weather`, the clock on `home/clock/`) and writes only under its
own `panel/<name>/`. A screen whose firmware carries no own account uses the shared `panels` account,
which may write anywhere under `panel/`. Their commands go to
`panel/<name>/cmd/<device>/<code>`, and the plug-in passes on only the exact commands a widget of an ESP
panel sends (a device a panel only shows cannot be switched through it).

On the screen (docs/device-state.md) a toggle, dimmer, sensor or temperature whose device is offline
is greyed and takes no tap, one waiting for its report dimmed: the plug-in publishes
`panel/<name>/away` (retained) within a second of a change. A tap on a dimmer leaves the bar where
it is and dims its fill until the device reports, 5 s at most.

What the shared account means: whoever takes a screen on it (or its broker password - such firmware
and the starter image carry it) sees the live state of every device
of the house and can send the commands that any ESP panel's widgets send. A screen with its own account
reaches only its own panel's commands. Restart and safe mode
take only a message with the panel's own key (a new one with every flash, known to the backend and that
firmware only; a new screen takes only its own code), so a broker client cannot loop the screens. The key
travels on the screen's own topic during a flash and is retired by the next good one; after a flash that
failed once the screen had been sent to safe mode, flash it again (or clear that topic) to retire the key
it heard; wiping a screen is a local 10 s hold only. Device accounts cannot read the codes of new screens
either. Flashing, binding and confirming a screen need the dashboard's own token (not a phone's, nor a
panel's key). It cannot reach devices no ESP panel commands, the core's `home/cmd`, settings or rules.

A flash goes only to the address the panel's MAC has on the network (the host's neighbour table, read
by the `panels-discover` container), checked again right before the upload; never to an address a broker
client claims. A panel gets its MAC by the bind with the code, or - one made in the builder or bound
before - from a human: its card shows the MAC and address its screen reports, **Yes, this is it**. A new
screen is flashed only when its announced address belongs to its MAC; two new screens with one code are
refused; the first announcement of a MAC stays until it is bound or 10 minutes pass, and the screens'
account cannot read the announcements (their codes) at all. A flash the screen did not take (it did not
restart into safe mode) is called off on the build server, so the next one can start at once.

The clock and the date on the screens are texts the core sends every minute (retained on
`home/clock/`, in `CLOCK_LANG`). A new board is a new file in
`profiles/` (the board, display, touch and backlight blocks); the generator draws the widgets. The
screens do not onboard other nodes (`provision`), and their fallback Wi-Fi has a password
(`PANELS_AP_PASSWORD`).

## Broker accounts of the screens

Each ESP panel gets a broker account of its own when its firmware is built: `panel-<name>`, which
may use only `panel/<name>/#` and read the house state, weather and clock. It is kept in
`plugin-state/panels/mqtt_accounts.json` (0600; the nightly backup holds it, like every file of
`plugin-state/`). The backend adds it to the running broker (`$CONTROL/dynamic-security`) and waits
up to 5 s for the broker's own answers (matched by correlation data, so another admin's traffic does
not count); the firmware carries it only then. A build of a panel whose account the broker has not
confirmed yet (a new one, a lost file entry, a failed earlier build) replaces it as a whole: deleted
and made again with the stored password and the current rights (a connected screen reconnects); a
confirmed account is not sent again. A broker started from a file without the backend's right on
`$CONTROL` cannot be told: the build then answers 503 "restart it once (docker compose restart
mosquitto), then build again", and the account is sent again at the next build. `make update`
restarts the broker only when its file changed, and the failed build has rewritten that file
already, so restart the broker itself. The broker's file is
rewritten too (`tools/mkpasswd.py --accounts-only`), for its next start; the broker's own "Error
saving Dynamic security plugin config ... Read-only file system" in its log is expected (its config
directory is read-only on purpose, the file is written by the backend).

Deleting a panel removes its account; a panel built again under the same name gets a new password,
so a screen whose firmware has the earlier password stays offline until it is flashed again. A
screen on the shared `panels` account keeps it until it is flashed again. Nothing retires that
account by itself: once every screen has its own account, change `MQTT_PANELS_PASSWORD` in `.env`
and run `make update`. That breaks every screen still on the shared account (a starter flashed but
not yet bound too), so flash those again first.

## The knob

A knob (`knob128`: a round 240x240 screen on an ESP32-C3, turned and pushed) is a panel of up to four
rows - switch, dimmer, air conditioner - made in the builder like any panel. Turning dims the row or sets
the temperature, a short push selects the next row, a long push switches it; holding it 10 s shows the
countdown to its factory reset. Its firmware is `firmware/gen_knob.py` with `profiles/knob128.yaml`
(`kind: list`; display, SPI, backlight, encoder and button pins); everything else - building, flashing
through safe mode, binding a new knob by its code, the broker rules - is the same as for the screens.

## On the dashboard

The plug-in's `ui.js` also loads `static/widgets.js` (`window.PW`, the panels' widget drawing) into
the dashboard. The `ir` cards use it to draw a remote layout with icons, and the `climate` cards use
it for the mode and fan icons. With this plug-in off, those cards fall back to plain buttons.

## A screen's own sensors

An ESP screen publishes its own sensors (temperature, humidity, light) on `panel/<name>/sensor/...` and
its status on `panel/<name>/status`. These two filters are open for reading, so a generic device in
`mqtt_devices.json` can show them (state topics only; the rest of `panel/` stays the plug-in's).

## Voice of a tablet

A tablet can be a voice satellite (the `voice` plug-in). `GET /api/panels/voice/next` is its long poll
for the commands the voice plug-in queued for it. `POST /api/panels/voice/event` is what it tells back:
a wake word, the player state, listening on or off, the volume.

## A file that does not parse

`layouts.json`, `paired.json` or `service_keys.json` that does not parse (a cut write, a hand edit) is
never written over with what the plug-in could read (nothing): every change that would save it answers
503, and the health page names the file. Fix or remove it; `paired.json` and the keys heal at once,
`layouts.json` after a restart (the layouts are read once). Tablets keep their keys meanwhile; a
heartbeat that would save the layout gets 503 (in the log) until then.

A tablet's heartbeat (once a minute) is kept in memory; `paired.json` is written when its address or
passport changes, or every 30 minutes, not on every beat. An ESP panel's layout is checked when it is
saved: the topics of a new or changed widget must belong to a device of the house (they are mapped to the
house bus at build; a widget whose device went away later stays, sends nothing and is logged), and every
number in a widget must be finite (position and size -10000..100000).

## Layouts and tablets from another install

<!-- cutover-compat -->

Copy that install's `panel_layouts.json` to `plugin-data/panels/layouts.json` and its `paired.json` to
`plugin-state/panels/paired.json`, then restart. A tablet bound there may hold the house token, not a key
of its own: it keeps working only while `DASHBOARD_TOKEN` stays the same. Bind each one again (Panels ->
Connect an Android tablet) to give it its own key.

## Third-party code

`static/esptool.mjs` is esptool-js 0.7.0 (Espressif, Apache License 2.0, `static/esptool-LICENSE.txt`),
the npm package's `bundle.js` unchanged: it writes the first firmware over USB from the browser. The
bundle carries pako (MIT and Zlib) and atob-lite (MIT) inside, with their notices.

A launcher of your own may also report, in its heartbeat (the shipped one does not), the wireless-adb state (`adb`: enabled, wifi, port, rooted) and the Do-Not-Disturb filter (`dnd`: alarms / no-access / ...); the panel's card shows them (`adb :5555`, `adb off`, do not disturb). A rooted tablet keeps `persist.adb.tcp.port` at 5555; on any tablet the launcher keeps "alarms only" once it holds the DND right (`cmd notification allow_dnd <launcher package>`, done by the launcher itself on a rooted tablet, else once over adb).

Such a launcher may also say whether the panel is in front (`front`). When it says no, the health page warns
"another app is in front of the panel".
