# Yashome Settings: a menu by what the owner manages

Principles:
1. The menu is by subject, not by plug-in. Several plug-ins may feed one page (Samsung + LG + the player -> "TVs").
2. Every page manages something: the list of what exists with its state, "Add", then open / change / remove.
3. "About the plug-in" (version, folder, errors) lives only in Maintenance -> Plug-ins and update, never on a subject page.
4. A plug-in with nothing of its own to manage has no page; it adds a section where it serves (fwdesc -> the device window).

Settings is a full-screen section. The gear in the header opens it. The left column is the menu of groups, with a search on
top. A group's subjects are the tabs on top of its page; a subject's own tabs are a row below. Every page has its own URL,
`#settings/<subject>/<tab>`, so the browser's Back works. A page closes with a Close button. On a phone the menu and the
page take turns.

The menu (`ST_GROUPS` and `ST_SUBJECTS` in `web/js/35-settings.js`; "core" = the core draws it, the rest a plug-in):

- **Overview** (core) - the house checks itself: what is fine and what needs you (`GET /api/health/report`).
  Each row may open the Settings place that fixes it.
- **House**
  - Home map - the map editor (walls, rooms, zones, openings, underlay, icons). A room's rename, thermometer and
    delete are in its zone sheet. The main screen's map is view and control only.
  - Family - the people of the house: phones, rights, QR pairing; tab App: build the Yash! app, the address for phones.
  - House language (core) - the language of the voice, the clocks and the panels.
  - Weather - the place, the station of the weather now, the forecast.
- **Devices**
  - All devices (core) - every device: kind, how it is connected, room, whether it answers; "Add device".
    Tab Forgotten: gone devices whose name, room, favourite or place is still stored, each with "Forget".
  - Zigbee - the gateway (connection, adapter, network key), its devices, pairing.
  - IR - blasters, remotes, learning, IR appliances.
  - TVs - every TV: address, pairing, inputs, apps; the players.
  - Panels - wall panels: layouts and the builder, binding, firmware.
  - NFC tags - tags and cards: what each one starts; add one, remove one.
  - Computers and servers - tab Computers: the computers of the house, which TV each screen is. Tab Servers: which
    computer runs each server role (build server, speech, big model, yashGen). See plugins/tools/computers/README.md.
- **Automation**
  - Rules (core) - what happens when: buttons, sensors, schedules.
  - History - everything that happened in the house, and who did it.
  - Watchdog - tab Forgetting: after how many days a gone device is forgotten by itself.
- **Voice** - wake word, smart answers, questions before acting.
- **This screen**
  - Language (core) - the language of this screen.
  - Sign-in (core) - the sign-in of this screen and the app shortcut.
  - Sign in from another screen (core) - a one-time code that lets another phone, tablet or computer in.
- **Maintenance**
  - Backups (core) - the nightly backups, one now, downloads, restore.
  - Plug-ins and update (core) - tab Plug-ins: what is on, each plug-in's keys, Apply. Tab Update: a new version, Roll back.
  - Restart (core) - every service of the house started anew (the helper's restart job), for what a network drop
    leaves stuck.
  - Firmware builder - opens the yashGen firmware builder.
- **Other** - a page for each plug-in settings entry that names no known subject.

A page appears only when it has a tab: a subject whose plug-in is off is not listed. Old links keep working:
`#settings/health` opens Overview, `general` opens House language, `build` opens Computers and servers (`ST_ALIAS`).

## Restricted mode
In restricted mode (a public demo, docs/security.md) a page or tab whose `needs` the mode refuses is shown greyed,
"Not available here", and draws nothing.

## Plug-in API
A plug-in's settings entry names the subject it feeds:
`settings: [{subject: 'ir', title, expl?, render | tabs: [{id, title, render}], needs: ['GET /api/...']}]`.
It becomes a tab of that page; several plug-ins feed one page. An entry with no known subject gets a page of its own
under "Other". `PLUGIN` may set `"icon"` (the icon of that page) and `"menu": False` (no page at all).

A typed field on a page makes leaving it ask about unsaved changes. A field that saves on change itself carries
`data-autosave` and does not ask.
