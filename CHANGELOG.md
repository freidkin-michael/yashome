# Changelog -- Yashome

## Unreleased

- voice: one container `voice`, built from the plug-in, runs the TTS and the recognizer service (stream -> VAD -> Vosk ->
  intent -> house, the model fallback) that was a house stack (`voice-vad`); the model hosts, the wake check on the
  satellites and the TV of the TV commands are Settings -> Voice fields (`llm_url`, `big_url`, `big_health`,
  `wake_on_device`, `tv_device`) instead of env lines; the house's phrase map, glossary, phrase log and samples stay in
  `plugin-data/voice/`. Moving from the house stack: the plug-in's README, "One voice container".

- matter (new plug-in): Matter devices through python-matter-server, run by the new computers role "Matter controller"
  (pinned image, host network, the computer's Bluetooth, its WebSocket on the loopback, reached through the helper's tunnel
  `matter`; one per house). Add -> Matter pairs by one code (a new device, or one shared from a phone app); the house's
  Thread network is given to it (never logged or shown); each node is a device with on/off, brightness, temperature,
  humidity, motion, contact and battery channels; Settings -> Devices -> Matter lists the nodes and unpairs one.
- yashctl: `make install` and `make update` write `~/.local/bin/yashctl` (a wrapper that runs this checkout's
  tools/yashctl), so `yashctl apply`, `yashctl update` work as plain commands; a foreign one is left alone.
- computers: yashGen's compose.yml has its own healthcheck on the builder's port 8090 (the inherited ESPHome one probed
  6052 and showed the container unhealthy); Install again on a computer that has the role.
- nfc: writing a card speaks the asker's language: refusals, steps and results (`step_text`, `message`, `note`) in the
  app's language (`?lang=`, Yash! 0.38.4 sends it) or the house's (the dashboard); the log stays English.
- thread (new plug-in): the house's Thread network. Border routers are computers with the new role "Thread border router"
  (plug-in computers: the pinned OTBR on a radio of `/dev/serial/by-id`, TREL and backbone on the LAN, REST on its loopback
  only, reached through the helper's tunnels `thread1`-`thread8`); the house keeps the dataset (never logged or shown), forms
  or takes the network on the main router and gives it to the others, sets leader weight 255 / 64, restarts a stuck router,
  lists the nodes with parent and link, pairs by PSKd (Add -> Thread); Settings -> Devices -> Thread; a "T" on the map for a
  Thread router (`window.MapMarks`). Helper: `POST /v1/hosts/thread` (fixed ctl.sh), the probe lists serial devices.
  Nodes: "Add to the house" for a node with no house device, its card for one with; the card has a Thread section.
  One-button pairing: Add -> Thread opens the commissioner with the public code of the Yash firmware and the house pairs
  the node over Thread (`ctl.sh pair`, plug-in pair `pair_with`): its own account, OTA password and card, no code typed.
  The role's ctl.sh lets the nodes' MQTT through docker's FORWARD DROP; the router keeps the host's /dev/log and logs to
  the host journal (`journalctl -t otbr-agent`, notice level).
- esphome: a discovered control with `ent_cat` config/diagnostic is aux: it does not make the node a light or a switch
  (the add dialog's category, the card's kind); a config number is a folded setting, not a dimmer.
- core: one `discover` container (host network, no privileges, read-only code) runs the enabled plug-ins' discovery
  modules (`discover.py`, `run(ctx)`, docs/plugins.md), each in its own thread, restarted alone when it fails; it replaces
  `computers-discover` and `panels-discover` (same ports and files). `make update` removes the two old containers.
- computers: `LLM_LISTEN` - the big model's tunnel and health also listen there (a Docker bridge's gateway), no relay needed.
- bathshower removed: a plain humidity sensor and a rule's condition do what it did (the light stays on while the bath is steamy).
- nfc: Settings -> Devices -> NFC tags: every tag with the rules its tap starts, "+ Rule" on a tag, Remove (with its rules); a
  tag is added by tapping it (a sticker of ours not on the list is offered, its counter kept) or by its UID; the list is
  plugin-data/nfc/cards.json (seeded once from NFC_CARDS). The rule window has the event "NFC tag tapped".
- plug-in UI: `Home.toast` (called by plug-ins, missing until now), `Home.rules`, `Home.ruleCard`, `Home.newRule`, `Home.watchRules`.
- settings: the left column lists groups only (Overview, House, Devices, Automation, Voice, This screen, Maintenance); a group's
  pages are tabs on top, a page's own tabs chips below; one Language page for the house and this screen; Close is a button
  with its word (the settings and the help sheet); old links (#settings/general) open where the page went.
- helper: a `restart` job, every container of the stack recreated (`docker compose up -d --force-recreate`): Settings ->
  Maintenance -> Restart, for a container a link drop left with no network.
- tizen (new plug-in): Samsung Tizen TVs (2016 and later), set up in Settings -> Samsung TV (address, MAC, inputs, apps, an IR power-on fallback): pairing by Allow on the TV (the token in plugin-state, 600, never logged), power on by Wake-on-LAN repeated at +20/+45/+90 s (a WoL soon after standby may wake only the network), the IR button when the TV does not answer in 20 s, off by KEY_POWER; the screen state from UPnP, volume and mute absolute over UPnP, inputs and media as keys, apps over the REST API, SSDP discovery.
- core: `core.wake_on_lan(mac)` (tools/wol.py, shared with the webos CLI); a device with `wakes: true` is never refused as offline and the TV card holds its "on" while it wakes.
- wiring: a plug-in that was taken over (the map's links, an IR appliance's power) reads `wiring.json` only and its old fields are no longer written; a device key change (zigbee rename, relink) moves its wiring entries; `core.wiring_owns(name)`.
- IR remotes: a group can be part of the remote (a keypad, the inputs): "part of this remote" on the group's heading; such a group makes no zone of its own, so a TV with "Digits" or "HDMI" keeps one remote face.
- bathshower: the bathroom radar is chosen in Settings -> Bath shower among the devices that report humidity and presence (none: the plug-in rests, Health says so); a house that ran it with the radar fixed in the code takes that radar once, after a backup of its settings.
- dashboard: a tile keeps its own height for a narrow screen (640 px or less) apart from the wide one (`hn` in /api/tile_sizes; a save sends only what it changes); a double-click resets the height of the current width only.
- map: the grid lines are one device pixel at any zoom.
- help: "Order" on the tab toolbar is in the help sheet only.
- docs: README Requirements - limit the host's unwritten data (vm.dirty_bytes) when it also writes big files: a large copy to a slow disk stalled the dashboard.

- helper: a host-only key marked `[network-ok]` in its env.example (the Zigbee coordinator) can be set from Settings to `tcp://host:port` or `none`; paths and devices stay on the host.
- zigbee: a network coordinator (SMLIGHT SLZB, ZigStar): `Z2M_SERIAL=tcp://host:port`, found over mDNS at install with its adapter; `Z2M_DEVICE` (set by make update) passes the stick or /dev/null; the `serial:` block of configuration.yaml follows `Z2M_SERIAL` on every update and zigbee2mqtt restarts when it changed; make check tests the coordinator's port.
- map: the house's own SVG scheme is the only background (checked, no raster plans, no upload); zones drawn on it are the dashboard rooms (renamed from their label, a placed device is in the room where its icon stands, such a device's room selector is read-only, rooms with no device are not tabs); only physical things can be placed, one entry per thing under its dashboard name, grouped by room and kind; the real state of every thing on the plan, live; icons scale with the zoom and cluster; a portrait phone turns the scheme; core: `rooms_edit`, `ROOM_LOCKED`, `/api/rooms/locked`, plug-in scripts load with `?v=<mtime>`.
- plug-in `map` (owner only): the floor plan with the devices and loads on it, links (set by hand and read from the house's rules, panels and IR blasters), rooms by outline, facts for the voice.
- voice: the TTS is Silero only (Piper removed): Russian v5_ru xenia, English v3_en en_99, models fetched once into the volume, warmed at start, numbers spoken as words, a faint lead-in noise for a satellite that needs it. The Silero models are CC BY-NC-SA 4.0: Yashome is for non-commercial use only (NOTICE.md).
- panels: the launcher reports whether it is in front; a health warning when another app is; the launcher 3.19 comes back to the front after an update and after 5 minutes of another app.
- voice (step 2): satellites (a mic and/or a player), their cards, say/play/stop/volume (API, home/cmd), the stop and the listening sound on a wake, the reply to the satellite that heard.
- voice: one audio format to the satellites, WAV PCM 16 kHz 16-bit stereo, converted on the server (the TTS repeats its mono voice on both channels).
- plug-in `voice` (step 1): the TTS service (Piper, voices kept loaded, WAV 16 kHz under random names, confirmation sounds) and its status route.
- remote faces: the power, volume and channel keys share one row of up to three keys; Info counts as an extra key when the remote has Exit.
- remote tiles on panels: power_on / power_off ids are the On and Off keys of the face (they came out as text chips).
- panels: the launcher's heartbeat carries the wireless-adb state and port and the Do-Not-Disturb filter; the panel card shows them.
- IR remotes: a remote hides groups of keys (power, navigation, volume, channels, media, other) on its card and panel tiles; the keys without a place on the face are icons without text.
- panels: a Touchmouse widget (touchpad, Back / Forward / Keyboard, arrow cross with Enter, a text field) for Android panels; the launcher bridge gets tmText and the new keys.
- touchmouse: arrows and Enter keys, `t <text>` typing (a loopback helper in the desktop session); the app's mouse screen keeps Back, the arrows and Enter and has a text field.
- plug-in API: `retained_topics` (a scan of the retained topics under filters); removing a described node clears its HA discovery configs too.
- plug-in API: `DISCOVERY_IGNORE`; the Add ESPHome discovery skips nodes with a description or a pairing, and unpair clears their HA configs.
- pair: a revoked or moved device's retained topics under its own prefix are cleared.
- pair: after a house restart the wait for a sent pairing resumes.
- pair: a paired panel is waited for on panel/<node>/status and gets no card.
- pair: a refused pairing says the relay's steps (where it stopped).
- provision host relay: a portal that answers with any HTTP status (a 404 at /) counts as up; the pairing firmware has no page at /.
- pair: an account kind panel (panel/<name>/# read+write, the house state read-only) for an existing panel layout.
- provision host gate: the profile of a setup AP also gets ipv4.never-default (no default route through the setup AP).
- pair: a paired device can be marked "moved to another house": unpaired and revoked, its card stays offline and says so.
- pair: Add -> "Pair a device": the setup network and the code typed, pasted from the label QR (YASHPAIR:1:<name>-setup:<code>) or scanned with the camera.
- pair: POST /api/pair/start pairs a device behind its setup AP through the host relay; the card is made from its description.
- provision host relay: it can carry a pair request (a form of a few fields to POST /yash/pair of a device's setup AP, the AP key being the pairing code) and hands the device's JSON answer back.
- pair (new plug-in, first step): a device's own broker account with a narrow ACL made and revoked at run time (dynamic security), the OTA password kept by the house, revoke on removal.
- compose: every service carries `com.centurylinklabs.watchtower.enable: "false"`, so a host's Watchtower neither tries to pull the locally built images nor replaces the pinned ones.
- the temporary link to the firmware builder is labelled yashGen and opens https://yashgen.<domain>/.
- dashboard: a channel marked `advanced` is not a room's temperature; a channel's `note` shows under its name in Details (the bath radar's heated SHT41).
- bathshower (new plug-in): the virtual device `bath_shower` (a shower starts on a fast humidity rise of the bath radar and ends when the water stops); a rule uses it as a condition so the bath light stays on in a shower the radar cannot see.
- fwdesc: "firmware changed" compares with the previous description only; the channels a hand-written card lacks are a calm offer.
- fwdesc: a description with device.mac_suffix may sit under <name> or <name>-<6 hex> (one image, many boards).
- docs: pairing.md - the house side of pairing a fwgen device (per-device broker account and ACL through dynamic-security, the OTA password, the relay request, removal and moving); design only.
- dashboard: a summary's chevron is drawn with borders (no glyph); an ESP node's boot button is under Details; fwdesc's window section is last and collapsed, with plain words.
- fwdesc: the secret-value check reads the nodes' secrets.yaml (ESPHOME_SECRETS in .env, mounted read-only), skips non-secret names and says so on the health page when it cannot read it.
- fwdesc: the device window of a generic-MQTT device has a section "Firmware and hardware": the build line of the three cases, the picture or pin list, entities, history.
- fwdesc (new plug-in): reads what a fwgen firmware publishes of itself (`<node>/fwgen/description`, `/hash`), keeps it per device, registers build records,
  `GET /api/devices/{id}/description`, `POST|GET /api/fwgen/builds`; removal of a device clears its retained topics.
- plug-in API: `NODE_SUBTOPICS` (a plug-in reads `<node>/<name>/...` of any node) and `DEVICE_REMOVED`.
- docs: device-description.md - the house side of the hardware description a fwgen firmware publishes (topic, storage, pairing, device window, changes, ACL); design only.
- fwgen (TEMPORARY): Settings -> "yashGen" opens https://yashgen.<domain>/ (the front door proxies it to the fwgen web builder behind the dashboard's sign-in).
- dashboard: a card in a packed grid is no longer stretched past its content (the AC light tile in a room); the type pills stay one row from 900 px to 1250 px and scroll.
- dashboard: a new room opens its device picker, a new IR remote opens its first learning step; every type pill has a count; on a phone a name field
  is not pre-selected; the icon editor has "Text" as the first chip and a thin dark scrollbar.
- dashboard: the camera page paints the camera's last frame at once (kept on the device) and refreshes it in place; the other cameras are probed
  only when the chosen one gives a black frame.
- IR remotes: the button editor offers 70 icons in sets (power, sound, navigation, playback, inputs, colours, light, climate, misc), each titled.
- IR remotes: the button editor shows the words the dashboard shows (what is not touched stays as stored), lists the remote's groups as chips,
  titles every icon and drops the duplicate power key; a question window over another window has an opaque backdrop.
- IR remotes: one window edits a button (name, group, icon); a group is renamed from the pencil on its heading.
- dashboard: the camera box keeps its size and shows no broken image while the cameras are probed; every IR remote folds on a desktop too;
  a media box's power key carries its state (dot and word) - one control; the plug-in buttons and Order share one row.
- IR remotes: a button gets an icon from the dashboard's set when it is named (a window of icons, or text only); the icon decides its place
  in the remote layout. Stored as `icon` of the button; /api/ir remotes and the tile controls carry it.
- dashboard: a label "Group - button" is translated part by part, so one IR group is one group whatever the language of its buttons.
- dashboard: a media box with an IR remote is one card (the remote folds under Show all buttons); the AC name shows in full; the last press
  names its day; IR groups and buttons in the UI language; no digit under an unnamed gang; a lock has a padlock; the camera page opens on a
  camera with a picture and says once when every camera is black.
- dashboard: a question window opened over a device window (a button name in the IR editor, a rename) keeps the keyboard focus
  while the page updates behind it.
- dashboard: an IR tile with buttons no longer prints its remote and blaster line (they are in its window); a unit C reads as the degree sign C.
- dashboard: the camera page opens on the camera that last gave a real picture.
- dashboard: a card is as tall as its content (no fixed minimum); the gangs of a 3 or 4 gang switch sit in one row.
- dashboard: a sensor card keeps one reading (temperature with humidity) - presence, distance, battery and the rest are in its window.
- dashboard: the IR remote and blaster of a TV or an AC are in its window under "IR control", not on its card; the TV's power
  button also sends the IR power-on when the TV is off.
- dashboard: the card of a switch or dimmer shows its state and the switch only; power, current, voltage and energy are in its window.
- core: after a start or an MQTT reconnect the on/off rules of binary sensors are checked once against the current state, so a
  presence edge lost during a restart no longer leaves the light on.
- dashboard: the build server is called one thing in Russian everywhere.
- dashboard: a button of a button/card device shows the last press ("last card: open, 12:04"); a rule's trigger chip spells the
  device and channel like its title; the AC card shows its power as a state label with one action button.
- dashboard: a card without data says "no data" (with the time of the last value) in full, on its own line under the name.
- dashboard: an ESP node's Wi-Fi signal, uptime and boot button are under Details, not on the card; uptime reads as days and hours.
- Add ESPHome: a node whose topics already belong to a card (its topic prefix may differ from its name) is "already in the house".
- dashboard: the loaded plug-ins list shows the plug-in names in the UI language.
- dashboard: the + Room chip is a 44 px action with a dashed outline; both navigation rows fade at their edges the same, stronger.
- dashboard: the power dot of a media player says the state in words (Switched on / Switched off), not the short On / Off.
- dashboard: the backup health row counts in minutes or hours the first day; a rule names its device and channel with a
  dot between them instead of two colons; Settings -> This device says when it already is the installed app.
- dashboard: Settings -> Plug-ins and update names each plug-in and each .env key in plain words with a line of what it does;
  the Build server page says what it is for and what each field means.
- dashboard: Settings -> Backups restores an archive with a confirmation window, from the list or from an uploaded file,
  and shows the result; the shell command hint is gone. The helper has a restore job (current state backed up first).
- dashboard: on a phone the AC card shows power and temperature with fan and mode under "More"; the TV card folds its
  inputs the same way.
- dashboard: the camera page names the camera and the time of the frame above the picture and says inside the box
  when the frame is black or missing; on a phone each IR remote shows its first row with "Show all buttons".
- dashboard: Settings is a menu of pages (This device, Language, Connect a device, Backups, each plug-in's, Plug-ins and
  update, Health), every item with a line of what it does; each health row says what it checks and, when not green,
  what to do.
- dashboard: places and kinds are two different rows - Favourites and the rooms as tabs (each room shows its
  temperature), the kinds as small pills under "By kind:"; the home strip only says who is home; Install lives in
  Settings only; the family page lost the Firebase widget checkbox (the server setting stands); the IR tile's Set up
  is 44 px; a rule's kind tag comes first with a space, no double colon; IR counts read "(built into the firmware),
  N groups"; no health poll before sign-in; a climate favourite shows its set temperature inside the circle.
- dashboard: a desktop shows every section (the row wraps), a phone scrolls it; the rule run button is an SVG; a
  sensor rule names its channel in its title and reads "= yes / = no"; the NFC rule no longer says "card" twice.
- dashboard: cameras are named by phone and side and say when there is no picture; a sensor rule names its channel
  and reads "triggered / cleared", the door card values read as words; the IR pages lost "tile" and "appliance"
  for "IR device" and the new-remote question says what comes next; the panel app row sits under Manage panels;
  the builder's toolbar wraps and its Undo / Redo / Reload are words.
- dashboard: a channel named "-" or a "nothing" word is shown by its number; a device of a kind the dashboard does not know
  is sorted by its channels (a sensor with temperature and humidity lands under Sensors); the presence card is the
  home strip, not a grid card; a device that is online but has sent nothing looks like it waits; favourite cards say
  their state under the circle; the AC on / off buttons carry their word; the media box's power dot is named.
- dashboard: the navigation is one row that scrolls sideways (faded edges, the active chip kept in view); on a phone
  the search is a round icon that opens into a field; sections without a device are not offered; the sections read
  shorter (Rules, Appliances in Russian); Favourites keep the home strip and the buttons on one row; the Family button is an icon
  with the count; every tappable thing is at least 44 px; Settings says where an installed app lands.
- dashboard: dates and times are written the UI language's way (24-hour); the dimming limits really hide on a TV;
  a TV that is off shows a dash for its volume instead of -1.
- dashboard: a channel label is translated by its longest known prefix ("Moving threshold g3 (...)"), more channel
  words in the dictionary; the presence device is "Who is home".
- dashboard: the first screen opens with a strip - who is home and each room with its temperature (a room chip opens
  the room); the presence card is named in the UI language; "launcher" reads "Home Panel app";
  the app build logs show only while building or after a failure; the dictionary lost its glyph-era keys.
- dashboard: OK of the device window is hidden until a change (a CSS rule overrode the attribute); a level control is
  named by the device (Volume) with the dimming limits only on lights; text readings are translated (a lock's
  states, a door open/closed); the IR remotes page opens with one sentence.
- dashboard: questions (a room's name, a device's or channel's name, IR remotes, buttons and tiles, the Zigbee rename)
  open as a small window of the page instead of the browser's prompt(); every card and favourite carries an icon of
  the device's kind; the IR pages use SVG pen / trash / play; the Family button is an icon with the count on a phone,
  so the phone header stays one row.
- dashboard: the rule editor says "New automation" / "Edit the automation", takes times as HH:MM (24-hour, whatever the
  browser's locale), opens a new rule with "choose" in every list instead of the first device, and names a lone
  switch channel "Power" instead of its code.
- dashboard: a search box at the end of the navigation row finds devices by name, room or kind across every section;
  Escape or picking a section clears it.
- dashboard: the device window shows OK only after a field was changed (x drops the edits); its first rows are the
  room (a select, for every transport) and the favourite; Settings begin with the settings and keep the health list
  folded at the end (unfolded when the gear shows a warning).
- dashboard: units in the UI language (the Russian dictionary names W, V, A, kWh, cm); no glyphs that need a symbol font (plain + and SVG pencil / speaker);
  climate, TV and IR cards show the room like every card and say Fan / Mode / Temperature; the IR tile's gear is a
  "Set up" button; the Zigbee rename is one line; the ESPHome dialog opens with one plain sentence; an unknown
  category reads "device"; the presence watchdog readings left the card; rule buttons and day chips are 40-44 px.
- dashboard: one navigation row - Favourites, the rooms (with "+ Room"), then the sections - instead of room chips in the
  header and a drawer behind a burger; it scrolls sideways on a phone. The phone header is one row (icon buttons, the
  connection state as a dot). Buttons are 40 px tall, header and window buttons 44. Log out and Install moved to
  Settings -> This device. The Family button has no emoji and names its count.
- dashboard: cards speak one language - readings carry a unit (the device's, else by channel: W, V, A, kWh, %, cm, lx),
  at most one decimal, more channel words translated, the lock's Locked/Unlocked too; the line under a card is its room
  (else its kind) instead of transport ids; the reading-only cards lost "no switches"; the device window shows kind and
  room in its head and keeps connection, model, address, id and the raw state under Details.
- dashboard: one "Add device" in the header; the other buttons say what they add or remove (Pick favourites, Remove cards,
  New rule, Devices of the room, Rename room, Delete room); every card has a star that keeps its main channel in Favourites.
- dashboard: a sign-in card instead of "Error: 401" (the 6-digit code or the token, the error said on the card; the header
  keeps only the brand until signed in); a fresh visit opens Favourites, the last section is kept within one browser
  session only; the closed sections drawer is out of the keyboard's tab order.
- dashboard: Add device -> ESPHome no longer prints the broker password; the secrets.yaml line is masked, with Copy and a
  15-second Show; the whole how-to block is collapsed under "How to set up a node that is not here yet".
- family app 0.19.0 takes a door card tap itself: the card's NDEF link (`/nfc?...`) is POSTed back to the paired house with
  `fmt=json` and the answer (`{ok, head, note}`, new in the nfc plug-in) is a notification. The older widget app did this with a GET,
  which a plain GET never does now.
- panels: Panel builder and Edit open the builder inside the Panels tab, not in a new window (a dashboard installed as an app opened
  it as a second app); the separate Open link of the panel page is gone, the cards show it.
- panels: Connect an ESP screen offers every board with firmware of ours, the round knob too (it was listed for canvas boards only).
- panels: with empty decimals the builder and the panel page print a value as the device sent it ("46.0"), like the ESP firmware does,
  instead of the number from JSON (46): the preview matches the screen.
- panels: the builder has a Build and flash button for a board with our firmware (saves, compiles, flashes over the air; the
  phase shows in its status line).
- panels: the panel page (`/panel-view`) may load the launcher's own camera stream (`http://127.0.0.1:8081`): the core's
  `img-src` blocked it and the camera tile stayed black on the tablet itself.
- panels: the preview cards of the Panels tab take a camera snapshot every 30 s (`?camslow=1`) instead of every second: a
  tablet has one camera, and the dashboard's polling kept it from showing its own picture on the tablet.
- panels: the Panels tab shows each panel as it looks (a live preview, a card per panel); a click opens the builder in the tab
  ("All panels" goes back). Flash, room, pairing again and delete are under "Manage panels".
- family app 0.18.2: a card of a unit that is on (a switch, a TV, an AC) glows warm and an AC that is off has its off button red, as on the dashboard.
- family: app 0.18.0 keeps the house link (`/api/family/live`) open for every paired phone, also one paired with Firebase:
  its Wi-Fi gateway and version reach the house, so who is home no longer needs the phone's MAC. A phone that was
  paired with Firebase starts the link by itself after the update.
- family: `FAMILY_PRESENCE_LINKS=<phone>=<presence device>` gives a phone without a live link (paired through the cloud) the
  home state of a presence device.
- family: a Family button in the header, before Log out, opens the Family page (like a room): one strip on top with
  the add-a-phone form and the app (link, Build the app), then a small square per phone (green: at home, dim: away, dashed: not known yet); a click on it opens the window with
  everything (Rights, New QR, Update app, Ring, Stop, Delete). The count at home is on the button. Core: a plug-in tab may be
  `hidden` (not in the bar) and `Home.openTab('p:<id>')` opens it.
- panels: `GET /plugins/panels/app` (open) names the published launcher (`code`, `name`, `size`, `sha256`,
  read from the APK's manifest) for launchers that update themselves; Build the app uses the house's own
  launcher project in `plugin-data/panels/launcher/` when there is one; `PANELS_RESTART_WAIT_S` and
  `PANELS_SAFE_MODE_WAIT_S` for firmware that restarts slowly into safe mode.
- family: the app download, its build and the pairing, rights and removal of phones are all on the Family
  card; the pairing page no longer offers or links the APK (it only opens the app).
- family: the house updates the app on a phone: the app reports its version over the live link, the list
  shows it with an Update app button (`POST /api/family/update`, or `FAMILY_AUTO_UPDATE=1`); the house
  sends `update` down the link, the app fetches the built APK from its own house address, checks its sha256
  and size and hands it to the system installer (the same signing key only; no tap on Android 12+ when
  the app installed the previous version, else a notification). App 0.17.0, permission REQUEST_INSTALL_PACKAGES.
- family: each paired phone gets the device `person_<name>` (`home`, event `arrival` / `departure`): home
  while its live link is open from a house-network address (`FAMILY_HOME_NETS`, default 192.168.0.0/16)
  and for `FAMILY_PRESENCE_GRACE` s (600) after; no event on the first reading after a house restart; a
  revoked phone's device goes. The app reports its Wi-Fi gateway every
  minute (`{"gw": ...}`, no location permission); one of `FAMILY_HOME_GATEWAYS` counts as home through a VPN.
- touchmouse: the datagrams gain `w n` (the wheel, +up, at most 20 notches), `b m 1|0` (the middle
  button) and `k kbd` (KEY_PROG1, for a binding on the receiver, e.g. an on-screen keyboard); the
  Linux receiver turns them into uinput events. The panel touchpad and the Yash! app scroll with two
  fingers dragged up and down, click the wheel with a three-finger tap, and have a Keyboard button
  beside Back and Home. A receiver without the new commands ignores them. The app's player list falls
  back to the old server's `/api/airmouse` on a 404 (cutover-compat).
- rules: Run of a rule that sent nothing because its channel is missing or not a dimmer answers 409
  with the reason (404 is only for a rule that does not exist); a rule action `off` on an IR button
  presses nothing and says "failed" in the press log; an action that raises with the text "offline" is
  still a failure, not a skip.
- rules: an action that raises (a webOS TV that is off, a refused value, any transport's HTTP error)
  is "failed: <reason>" in the press log and an NFC tap answers 502 "Not done", not "Accepted"; a
  failed press with no target names the action, never "None".
- dashboard keyboard and switches: Tab reaches every card (Enter / Space opens it) and each switch
  dot, dimmer pill and toggle (Enter / Space switches it; a held key acts once); the arrows step a
  dimmer by a tenth of its window (one point in a window under 10 points, its ends reached, from
  outside only into it) and switch it on, sent once 250 ms after the last key. A waiting step goes out
  first when the switch, the pill, the dot (a tap or a drag, its own level last) or the device window
  of that dimmer is used; a step that finds the device offline sends nothing. Every path (keys, pill,
  dot, toggle, favourite, the window's toggles) records what it asked, so the next toggle builds on it
  while the command is in flight; a failed command is forgotten and gives back the record it replaced.
  A dimmer at 0 steps up a tenth (the floor rule compares with the real floor); a pill side tap at the
  window's end sends nothing. A redraw keeps the focus and marks the cards of an offline device off at
  once; closing the device window finds its card; an offline card takes the focus of its control and
  its controls leave the tab order; a plug-in face's inputs are left alone.
- rules: Run on a rule whose target device was deleted answers 409 "<target> is gone: nothing was
  sent".
- rules: a manual Run whose action did not go out answers 409 ("<device> is offline: nothing was
  sent"; Run ignores the conditions, so only the target's own state counts) or 502 (e.g. a zigbee
  publish that failed), not "ok"; an NFC tap then says "Not done: the command did
  not go out". webOS takes a whole volume also written 50.0.
- plug-ins (review sweep): IR presses only on true (`home/cmd/<ir>/<button> off` pressed it); webOS
  checks volume 0-100, mute on/off, an input or app id and the media keys before the TV (400); a
  zigbee binding whose publish failed is "failed" in the press log and arms no `for` or auto-off;
  the build server's pairing code has 64 bits; family writes its file with a unique temp and fsync
  and, like webOS pairing, off the event loop.
- core (review sweep): a rule window with from == to is the whole day (it never opened); a renamed
  device shows its new name in the press log at once; the 30 s cycle publishes only what changed (the
  whole mirror on a (re)connect and /api/resync); a channel taken out of a device leaves its cached
  value and its retained topic; an unreadable mqtt_devices.json no longer prunes its devices' mirror
  and cached state; a stop writes the status cache and leaves the broker cleanly. `POST
  /api/rooms/reorder` is gone (nothing called it). Docs: brightness numbers on the bus vs REST, the
  control kinds, the 32-character plug-in name.
- core: SECURITY: a WebSocket frame built for one caller was served to another that sent its token in
  `X-Token` within 0.25 s (a family phone could get the full house). The frame is now shared only
  between connections whose headers (but the handshake nonce) and query are all equal.
- core: at a start a waiting shot looks again every 2 s, not 15. dashboard: Enter and Space on a
  switch of an offline device window do nothing (and it leaves the tab order); the meta line is no
  longer dimmed (contrast); an echo inside the first 1.5 s ends the pulse at once.
- panels: the ESP screen's away list is `/<device>#o/` (a live "lamp" was greyed with an offline "desk
  lamp"); widgets with only a command topic grey too; the dimmer's wait ends at 5 s (checked every
  250 ms); the plug-in reads a panel's devices again on a layout save, a device added, removed or
  renamed, or after 10 s.
- core: the WebSocket sends at most one frame every 0.25 s (the latest state) and an unchanged one
  only every 2 s; screens of one key share a build. At a start an overdue auto-off waits until its
  device is known and then goes out (it was dropped for an online device). A zigbee or JSON frame
  that carries a channel unchanged at the value just sent answers that command.
- dashboard: a device that answers with another value ends the wait without "No answer"; the wait
  ends after its time even with the WebSocket down; a plug-in face pulses; going away shows after
  3 s; the offline device window is off for the keyboard too; toasts span the screen.
- panels: on a tablet an AC switched off takes a tap again (the device's offline state has a class of
  its own); an offline AC or remote is greyed too; one quick poll timer however many taps; a redraw
  only when a status changed, so the pulse runs smoothly; a server that does not answer ends a
  command's wait without "No answer".
- panels: an ESP screen greys a toggle, dimmer or sensor whose device is offline (it takes no tap)
  and dims one waiting for its report, from one retained topic the plug-in keeps, `panel/<name>/away`;
  a dimmer tap no longer moves the bar: its fill dims until the device reports (5 s at most). The
  firmware subscribes a topic once however many widgets read it. Reflash the screens to get it.
- panels: on a tablet a widget of an offline device is greyed and takes no tap, one waiting for its
  report dimmed; a dimmer, its switch or a lock pulses until the device answers and the panel says
  "No answer from <widget>" when it did not. tools/stand.py: `STAND_PLUGINS` loads plug-ins.
- dashboard: a device back shows live at once (going away still waits 3 s); the wait of a dimmer or a
  lock ends without WebSocket frames too and honours a plug-in's `holds`; the device window's commands
  are off while it is offline; toasts stack (one per text, at most three), are announced and never
  block a tap; dimmed cards keep their name and badge readable. core: a WebSocket frame goes out as
  soon as a status changes; a timer's own shot no longer voids a schedule catch-up of the channel.
- core: a timer waiting for its device writes nothing to disk while it waits (it rewrote
  pending_timers.json twice a retry); at a start a shot waits until its device is known; a schedule
  is caught up only the same day; a missed one (also one replaced by a newer entry while it waited)
  is kept a day across restarts and names its channel.
- zigbee: a device's retained "online" no longer makes it live while its bridge is down (either order
  of the replay); an empty availability message is ignored. core: at most 10 real asks a tick (a
  device that cannot be asked no longer takes a slot); a bad `state_request` is named with its cause
  when the device comes in.
- dashboard: a card shows live, "waiting for report" or "offline since HH:MM" (greyed, controls off,
  the change shown once it held 3 s), keeps the last values with "last report HH:MM" in its tooltip;
  a dimmer, its switch or a lock pulses until the device answers (the status' new `cmd_wait`) and a
  toast says "No answer from <device>" when it did not; control failures are toasts, not dialogs.
- core: a command to an offline device is refused for every kind of channel (a colour, an IR press
  too), and a rule on an offline zigbee2mqtt device sends nothing; the press log and the NFC page say
  offline, waiting or gone; a flood of home/cmd to an offline device is one log line per 10 s. A
  binding's ON with a `for` does not also arm the auto-off; an OFF, or 5 s, ends that, and a manual
  ON gets its auto-off. A panel's charger plug that is offline is not commanded (logged once).
- core: a device back after an outage (or waiting since the start) that has not reported in 5 s is
  asked once: zigbee2mqtt `/get`; a generic device through the new optional `state_request`
  `{"topic", "payload"}` of its entry; a plug-in through `core.STATE_ASK[transport]`.
- core: a command to an offline device is refused (409 "<name> is offline"; on home/cmd a log line),
  except a webOS TV; a rule skips an offline target and a condition on a device that is not live,
  and its press log says "skipped: <device> offline"; a timer due while the device is offline waits
  for it (an auto-off up to 30 min, a `for` revert and a schedule up to 10 min, then dropped; a
  missed schedule shows in Settings -> Health), and a device back ON gets its auto-off window again;
  Settings -> Health lists devices offline for more than 10 min.
- core: a device with a heartbeat that falls silent for `DEVICE_SILENT_S` (600 s) is offline. The
  heartbeat is learned conservatively (10+ regular reports over 10 min, re-learned on every report,
  forgotten after 24 h unconfirmed); `offline_after_s` in a device entry opts it in with its own
  limit. A device with an availability topic, or one that reports only on change, never goes by
  silence. New key `DEVICE_SILENT_S` in `.env` (`make update` adds it empty).
- core: a device is live, waiting or offline (docs/device-state.md). After a start every cached
  device waits for its own word (availability, a report) and is never shown live from yesterday's
  cache; back after an outage it waits for its report (a device whose silence is normal is live
  with its last values 35 s later, counted while the broker is connected); a command no longer marks
  a device online.
  The status has `last_seen`, `offline_since`, `waiting` ("start" | "return"), `waiting_since`; `home/state/<id>/online` is false while it
  waits. A device with neither availability nor a report is live with its last values 35 s after
  the start.
- tests: the panel firmware generators' output is pinned file by file (`tests/plugins/
  test_panels_golden.py`; `--dump DIR` shows it, `--update` takes a change made on purpose).
- core: a dimmer waits for its device - a brightness channel and the switch it dims are confirm
  channels (the lock's rule): the cache and the home/state mirror change only on the device's report,
  `/control` answers `waiting: true`. A toggle or a step right after a command builds on the newest
  unanswered command (an echo of that channel answers it and older ones; any other report of it,
  the old value too, settles after 1 s and voids only the commands sent before it; 5 s at most); a lamp with one switch and one brightness pairs them; a dimmer with an
  availability topic that is offline is refused (409). The dashboard's slider still shows the value
  sent for 4 s until its own step (docs/device-state.md).
- design: docs/device-state.md - dimmers wait for the device, a device that went away shows its last
  values greyed and its real state when it is back. The core does it (the entries above); still to
  come: the dashboard (the slider's wait and revert with a toast, greyed cards, "waiting" apart from
  "offline") and the panels and ESP screens.
- core: a binding without a target in settings.json is dropped with a warning; the backend did not start
  (a name used before it was defined). The startup audit now also lists tab order, tile sizes and
  disabled rules that name a missing device.
- core: a device rename (zigbee2mqtt) or relink keeps its channels' brightness windows (dimrange); they
  were dropped and the dimmer went back to its full range. A channel the target has keeps its own.
- plug-in `webos`: power-on works from a container - the host's Wake-on-LAN relay (`host/wol-relay.py`,
  `WOL_RELAY=<host>:9099`) gets the request first, then the direct packet. The relay limits each sender
  (10 requests in 10 s) and remembers at most 4096 senders (a spoofed flood still blocks power-on: let
  only the container reach the port); it stops on a bad setting (exit 78, not retried: a port below
  1024 too) and retries an address not up yet or a port in use. A TV's mac must be one of aa:bb:.., aa-bb-.., aabbcc.. (else 400).
- plug-in `panels`: every ESP panel gets a broker account of its own at its next build (panel/<name>/#);
  screens flashed before keep the shared account until reflashed. `make update` restarts the broker with
  its new file (the backend's `$CONTROL` right); only when a panel build ran before that, restart it by
  hand (`docker compose restart mosquitto`): until then a build answers 503.
- broker: a new install has `MQTT_BACKEND_ONLY=home/cmd/#` - only the backend commands the house; a device
  account (an ESP node) can no longer publish there. An existing house keeps its rights: `make update`
  adds the key empty, with a note, if it is missing. Set it to `home/cmd/#` once no node of yours
  publishes to home/cmd (older firmware of a knob or panel may).
- PLUGINS_EXTRA_DIR must be written out: an existing `~/x` makes make install / make update stop
  with a message (compose and the backend expanded it, the install steps did not).
- tests: `pytest tests` no longer mixes the plug-ins' harness into the core's (3 order-dependent
  failures); `pytest tests/plugins` runs the plug-ins'.
- plug-in `family`: one phone, one entry - the app sends its ANDROID_ID with the claim, and a phone the
  house knows gets its entry back (name, rights, widgets, rules; the old token dies), never matched by
  name; older apps pair as before, flagged "update the app"; Delete (house token) also drops the rules
  that ring the phone, its live sockets and acks, and deletes Firebase-only leftovers. Update the Yash! app.
- plug-in `panels`: one tablet, one record - the launcher sends its full ANDROID_ID, a tablet paired
  again keeps its record (a new key, the old one revoked once a human checked the new code), an older
  short id is renamed in place; one ESP screen (MAC), one panel; Pair again (Android) and Delete on every panel;
  bind, unbind and delete need the house token (a family phone with full rights could do them).
- core: `MQTT_SHARED_READ` - a plug-in may open filters inside its namespace for generic devices to read;
  plug-in `panels` opens `panel/+/sensor/#` and `panel/+/status`, so a screen's own sensors (a
  `mqtt_devices.json` device on them) work again.
- core: rules respect a "confirm" channel too: no early value, and a rule on an offline lock is not
  sent (logged); `_safe_execute_binding` returns what the rule did.
- plug-in `nfc`: a card whose rule drives a lock waits for the lock's own report and says Locked /
  Unlocked, "No answer" after 20 s, or "offline"; other rules still say "Accepted". README.
- core: `BIND_ADDRESS` in .env - the host address the dashboard, the broker and zigbee2mqtt listen on
  (empty = every interface, as before); `<HOUSE_DIR>/compose.override.yml` joins the stack when it
  exists (a reverse proxy's network, an alias).
- plug-in `ir`: an IR toggle entry may carry `touchmouse` (port, os): the box becomes a touchpad target
  at its probe address.
- plug-in `panels`: the dashboard loads the panels' widget drawing (window.PW) again, so IR appliance
  cards show the remote layout with icons and AC cards the mode / fan icons, as in the old stack;
  without `panels` they stay plain buttons. READMEs for `ir`, `climate` and `tv`.
- plug-in `switchbot` (was switchbot-lock of the old stack): a SwitchBot Lock / Pro / Ultra over Bluetooth
  through host/switchbot-lock.py on a host near the lock (switchbot-lock@<id>, a dynamic user, its own
  broker account on switchbot/# only); Add -> SwitchBot lock adds it as a generic MQTT device (replacing a
  hand-made SwitchBot entry of the same id, topics move to switchbot/<id>/); a retained command is
  ignored; battery and link on the health page. Moving: stop the old bridge first (one BLE session).
- core: a channel with `confirm: true` (the SwitchBot lock) shows a command's value only once the
  device reports it, and refuses a command while the device is offline; a command no longer marks a
  device with an `availability_topic` online.
- plug-in `provision`: an ESPHome node that lost its Wi-Fi gets the house Wi-Fi again through its
  `<name>-setup` AP - a neighbour node with the firmware package (it hands over its own Wi-Fi; the
  house Wi-Fi password and the shared setup-AP key never go on MQTT) or the host piece (a spare Wi-Fi
  card, a root-owned nmcli gate, token; the AP key on stdin, never in argv); one shared setup-AP key
  PANELS_AP_PASSWORD, filled in by itself; an open "-setup" AP is refused when PANELS_AP_PASSWORD is set; the AP's MAC is shown (red
  when not Espressif) and pinned, one name heard open and secured is refused; a node still online is
  refused (the owner may force it past a stale retained status), success only once it turns online after the start; Add -> ESP via its setup AP; firmware
  packages base.yaml / provision.yaml for your own nodes. Upgrading: OTA the relay nodes first, then
  the core (an old relay fails slowly with the new command)
- core: Add -> ESPHome's secrets.yaml lines include ap_password (PANELS_AP_PASSWORD) when it is set
- panels: the Wi-Fi SSID and password come as a pair, PANELS_WIFI_* or else HOUSE_WIFI_*
- core: HOUSE_WIFI_SSID / HOUSE_WIFI_PASSWORD, the house Wi-Fi for your nodes (panels use it unless
  PANELS_WIFI_* override it); a dimmer without a switch goes off with 0 at the floor
- Yash! app 0.16.0: one widget, Channel - a switch, a lock, a dimmer (top + / middle on-off / bottom -) or an
  air conditioner (its mode), a short name and an icon of your choice; its state comes as soon as the
  house changes (PUT /api/family/me/widgets; the live link, or - only with "Widget states through Firebase"
  on, off by default - a push through Google at most every 10 s)
- core: REST control takes "up"/"down" for a dimmer, the same step as home/cmd
- Home Panel: a tablet's cameras answer only the house's camera proxy (a key per tablet from the heartbeat);
  the panel page cannot leave the house's address; no microphone widget until the voice plug-in
- plug-in `panels`: the Home Panel launcher (launcher/, Android) - Panels -> Build the app builds it with the
  house's key, /plugins/panels/panel.apk offers it; PANELS_APP_ID for a house with an older launcher;
  the voice wake word comes later with the voice plug-in
- plug-in `family`: Family phone -> Build the app - the Yash! app built by the app builder, signed with the
  house's key (plugin-state/family/app.keystore, made at the first build), served by the pairing page
- plug-in `build`: the app builder (BUILD_APK=1) - Android apps built on the build host from their sources
  here and signed with the house's keys (plugin-state, in the backup; the first build makes the key);
  submit_apk() for plug-ins; firmware job ids start with "f", app job ids with "a"
- plug-in `touchmouse` (was the air mouse of the old stack): a phone or a wall panel as the touchpad of a
  TV box or a computer - GET /api/touchmouse lists the receivers (devices with a "touchmouse" field,
  hosts of its settings section, Add -> Touchmouse), host/touchmouse-rx.py + a hardened systemd unit is
  the receiver of a Linux host; the Yash! app's touchpad reads /api/touchmouse - update the app
  (0.15.0): an older one asks /api/airmouse and finds no receiver; the receiver's unit takes only
  private addresses, at most 500 datagrams a second, and lets a held button go after 2 s
- plug-in `family`: works without Firebase (FAMILY_FIREBASE_DB and FAMILY_BRIDGE_URL empty) - the QR
  holds the address and the phone token (drawn in the browser, static/qrcode.js), the app keeps
  /api/family/live open on the house network and a ring goes down it; with Firebase a ring goes both
  ways; the list shows "reachable now"; Stop; the rule action "Ring a phone" (ring or stop, 5-300 s,
  once a minute per phone, the house token only); GET /api/family/names
- plug-in `family`: the pairing QR holds a one-time code (POST /api/family/claim swaps it for the
  phone's token; a second use or a photo of the QR gets nothing; with Firebase the custom token comes
  with the claim, never in the QR); pair.html clears it from the history
- core: a plug-in rule action may be house_token_only (stored, run, enabled, deleted by the dashboard
  only); run() sees "_forced" when the rule list's Run fired it
- Yash! app 0.15.0: builds and runs without google-services.json; pairs by a QR without the cloud
  token; a foreground service (remoteMessaging) keeps /api/family/live open, starts after a reboot;
  plain http on the house network allowed
- the clock in the core: home/clock/time ("HH:MM") and home/clock/date (CLOCK_LANG: en, ru), retained,
  each minute in TZ - the topics the ESP clocks already read; devices may not write them; panels read
  them instead of their own panel/_clock/ (rebuild an ESP panel's firmware to switch)
- the helper container (helper/): Settings -> Plug-ins switches plug-ins and fills their keys, Apply runs
  make update; Settings -> Update shows the incoming CHANGELOG lines, backs up, git pull --ff-only +
  make update, and rolls the code back; jobs run in a one-shot container; the dashboard reaches the helper
  with the house token only (/api/helper/*), on a network of their own; keys naming a host path or device
  or account (*_USER) are read-only there, a *_PORT takes a number only, a "[one of: ...]" key in
  env.example one of those words (Z2M_ADAPTER); git over ssh with a deploy key of its own
  (~/.config/yashome/ssh, README); make update writes HELPER_KEY, YASHOME_DIR, YASHOME_HOUSE_ABS,
  YASHOME_EXTRA_ABS, YASHOME_Z2M_ABS, YASHOME_SSH_DIR and DOCKER_GID
- tools/envlib.sh: get reads the last of a repeated key (as compose does); env_set takes .env.lock
- Add ESPHome: a node not there yet gets its mqtt: block - a device account of MQTT_DEVICE_USERS, the broker
  as the dashboard is reached (MQTT_NODE_HOST to override), discovery on - and its secrets.yaml line
- Settings -> Health: every part of the house in plain words, worst first, with a fix per row - the
  broker, settings, plug-ins that did not load, disk, backup age; zigbee2mqtt, the build server,
  presence, weather, the family bridge, panel discovery and tablets' heartbeats from their plug-ins; a
  ring on the gear when something needs a look (core.HEALTH_CHECKS, GET /api/health/report)
- plug-in `panels`: the knob (knob128, round 240x240, ESP32-C3) - up to four rows (switch, dimmer, air
  conditioner) from the builder, firmware by firmware/gen_knob.py with profiles/knob128.yaml (kind: list);
  built, flashed and bound like the screens
- plug-in `panels`: ESP screens - firmware from the layout (firmware/gen.py, a board = profiles/<hw>.yaml),
  built and flashed by the build plug-in through safe mode; a new screen gets a starter firmware over USB
  and binds by the code on its screen (panel/_new/<mac>); one broker account for all screens (reads
  home/state, home/weather, panel/_clock; writes only panel/ - README says what that allows), their
  commands pass the plug-in, which lets through only the exact commands some ESP panel's widgets send;
  a flash goes only to the address of the panel's MAC (host neighbour table via panels-discover); the
  fallback Wi-Fi has its own password; the clock texts on panel/_clock/ (PANELS_LANG)
- build server: binary files in a job ({"b64": ...}); ota.hold - a compiled job waits ("ready") for
  POST /v1/jobs/<id>/go (or /abort), so the node can be prepared for the upload
- broker.json: `hidden` topics - inside its own bus an account may publish there but never receives them
- core: `core.mqtt_command(dev, code, payload)` - the home/cmd payload language for a plug-in's relay
- plug-in `panels` (devices): wall panels - layouts by the builder (/panel-builder, English with
  translations), the kiosk page /panel-view.html, the Panels tab by room; an Android tablet is paired by
  a code on its screen and gets a key of its own (`ypk_...`) that opens only what a panel needs, never
  the house token; the `panels-discover` container invites tablets by broadcast while pairing is open;
  charge window by a smart plug; ESP layouts published retained on `panel/<name>/layout`
- sign in a new device with a 6-digit code from a signed-in screen (Settings -> Connect a
  device), instead of typing the token; README: HTTP inside the house network and an HTTPS recipe
- backups: every night into backups/ (house files, .env, zigbee2mqtt data without its log, own
  plug-ins; no runtime state) - BACKUP_AT (HH:MM in TZ, or off), BACKUP_KEEP; Settings -> Backups (age,
  warnings such as an unreachable zigbee2mqtt folder, now, download); make backup; make restore
  FILE=... keeps the current files aside, refuses archives without the format-1 manifest, takes
  zigbee2mqtt's and own plug-ins' places from the backup's .env; unreadable files are left out with a warning
- docs/security.md: a node never gets the backend account `home`
- plug-in `build` (tools): the build server - an ESPHome container on this host or another one of the
  network with a small HTTP API (jobs, progress, OTA, the firmware for a USB flash from the browser);
  Settings -> Build server (address, key, test); one build at a time; secrets.yaml only for the time
  of a job, but the firmware and its build files carry them: keep the server inside the house network
  - on this computer by default; another computer: one command from Settings (installs docker, fetches the
    server from the house, registers by a one-time code - no password ever given to the house)
- core: `HomePlugins` hook `settings` (a section of Settings per plug-in); `core.is_house_token(headers)`
- plug-in `presence` (sources): the device `presence` (home, stale, arrival / departure events) from
  the phones on the Wi-Fi; the host piece (host/phone-presence.sh + systemd unit) runs on any LAN host
  and publishes on `presence/#` with its own broker account; the header badge
- rules: a trigger device without button gestures (presence events, nfc cards) offers all its values
  in the rule editor (only *single gestures were offered, so these rules could be made through the API only)
- plug-in `family` (tools): the Yash! Android app for the people of the house - one pairing QR, a
  token and rights (hidden / view / control per device; full access when ticked for a new name, or later in Rights)
  per phone, ring, revoke, a QR unused for an hour dies; the family-bridge
  container (Firebase: QR tokens, pushes, the house state while away) may run on another host
  (`FAMILY_BRIDGE_URL`); the app source and its build script come with it
- core: `WS_FILTERS` - a plug-in trims the /ws snapshot per caller
- broker accounts of plug-ins: `read` topics (read-only, e.g. `home/state/#`); `topics` (full
  rights) may no longer name the core's bus (`home/`, `homeassistant/`, a wildcard at the top)
- check_plugins: Android `res/values-xx/strings.xml` and `*.i18n.json` count as translation files
- dashboard: every device grid packs its cards by their own height (only the compact tabs did; a
  card stretched to the tallest one of its row); one 10 px gap everywhere
- dashboard: sensor units go through i18n (`lx` has a Russian translation)
- i18n.js parses on Chrome 77: external pages (Android wall panels with old WebViews) may load the
  public `/i18n.js`; no `??`, a test keeps it so. The dashboard itself needs Chrome 80+
- webos: media `volume_up` / `volume_down` (relative steps reach a sound bar over HDMI ARC); REST
  and rules only - a select would not repeat the same step

## 2.0.0 - first public release

The core and the plug-ins in one repository.

**Core**
- one FastAPI backend (`app.py`) and one dashboard page (`index.html`, a PWA), mosquitto in the middle
- generic MQTT devices from `mqtt_devices.json` or ESPHome's MQTT discovery ("Add" -> ESPHome)
- the universal bus: `home/state/<device>/<code>` (retained mirror of every device, `_pct` for
  brightness in the channel's window) and `home/cmd/<device>/<code>`
- rules: triggers (buttons, gestures, sensor thresholds with hysteresis, times), conditions,
  actions, `for` windows; per channel a schedule and an auto-off after any turn-on; timers survive
  a restart with the right remaining time
- rooms, favourites, tab order, tile sizes, channel renames and brightness windows, all saved in
  `settings.json`; an id that changed in its own system moves every reference
  (`POST /api/devices/relink`, audited at start)
- `HOUSE_DIR` in `.env`: the house's files (devices, settings, `plugin-data/`, `plugin-state/`,
  runtime state) in a directory of their own, e.g. a private repository (`examples/house.gitignore`
  keeps state out of git); empty = beside the code. `make move-house` moves an existing house there;
  `make install` / `make update` refuse an empty `HOUSE_DIR` while the checkout still holds a house.
  zigbee2mqtt's network stays under `Z2M_DATA_DIR` in the checkout
- a bearer token on `/api` and `/ws` (fail closed), same-origin checks, a strict CSP; broker
  accounts through mosquitto's dynamic-security plug-in: the backend, one account per plug-in
  service, optional device accounts that cannot write the mirror, a plug-in's read-only bus or
  `MQTT_BACKEND_ONLY`
- English UI with a Russian dictionary, the browser's language by default
- `make check`, `make install`, `make test`, `make update`; `make lint` and `make dev-test` in CI
- `tools/yashctl`: the house from a shell (devices, channels, watch, rules, rooms, plug-ins), `--json` for scripts

**Plug-ins** (docs/plugins.md; which ones load: `PLUGINS` in `.env`)
- the contract: devices of their own, transports and their rule shortcuts, MQTT topics and a
  namespace of their own, rule actions, settings sections, routes, token checks, start tasks, a
  device registry the core waits for, key migration; on the dashboard tabs, the "Add" menu, card
  decorations and faces, header boxes, sections of the device modal
- grouped by role: `plugins/transports/` (how devices are reached), `devices/` (a kind of device
  on the dashboard, over any transport), `sources/` (data and events), `tools/`; the group is only
  a folder, the plug-in's name is its id everywhere
- `requires` in a plug-in (one name per line): what it cannot work without is switched on, set up
  and loaded with it, the same way in the backend, `make install` / `make update` and the broker
  accounts; a missing, failing or circular requirement, or a malformed file, keeps it off with the
  reason in Settings; tabs with the same id from several plug-ins are one tab (the Appliances tab of
  tv, ir and climate)
- a plug-in's settings sections in its own `plugin-data/<name>/settings.json` (one of a loaded
  plug-in left in `settings.json` moves there at the start; if both copies differ, the plug-in's
  file wins and both are kept as `*.pre-split-<time>`; a section in two plug-ins' files stops saves;
  the core reserves that file name in every `plugin-data/<name>/`);
  what a plug-in writes itself (paired keys, counters) in `plugin-state/<name>/` (`core.plugin_state`),
  moved there from `plugin-data/` once
- `setup/` of a plug-in: `detect.sh`, `env.example`, `compose.yml` (in `COMPOSE_FILE`),
  `broker.json`, `secrets.py` and the install / secrets / update / preflight / smoke / summary steps;
  `settings.example.json` and `<file>.example.json` show its configuration, and a test loads them
- shipped: transports zigbee (zigbee2mqtt with its container, pairing, key rename, remove), webos,
  ir; devices climate, tv, cameras; sources weather, nfc; tools stats; and `_example`; their tests
  in `tests/plugins/`. Connectors only: no devices, names or secrets of any house
- `tools/check_plugins.py` keeps any one house's data (LAN addresses, MACs, Zigbee addresses,
  home directories, your house's words) out of the repository, and Russian out of everything but
  the translations
- docs/voice.md: how the house hears today and the target (TTS, replies on the satellite that heard, players, mic and player cards).
- docs/voice.md: the measured speed of Piper voices on the house CPU.
- docs/voice.md: the principle (thin satellites, wake word on the device, everything else on the server), the satellite interface and the decisions (Piper, irina/lessac, confirmations from the server).
- voice (3): the Voice / Sound tab and the voice settings in the device window.
- voice: the Piper voices live in a docker volume (a bind folder made by docker was root-owned and broke the plug-in settings).
