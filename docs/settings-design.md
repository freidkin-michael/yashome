# Yashome Settings: a menu by what the owner manages

Principles:
1. The menu is by subject, not by plug-in. Several plug-ins may feed one page (Tizen + webOS + IR -> "TVs").
2. Every page MANAGES: the list of what exists with its state -> "Add" -> open / change / remove -> "how it works".
3. "About the plug-in" (version, folder, health rows) only in Maintenance -> Plug-ins, never as a tab on subject pages.
4. A plug-in with nothing of its own to manage is not a menu item; it adds a section to the page it serves (fwdesc -> the device window; stats, tv-cards -> nothing).
5. Everything settable lives here: .env and file-only settings get a UI (secrets masked; bootstrap ones read-only).

Menu: the left column is the groups only (Overview, House, Devices, Automation, Voice, This screen, Maintenance, Other);
a group's subjects are the tabs on top of its page, as on Family, and a subject's own tabs a row of chips below.
The page closes with a Close button, never an x. The house language and this screen's are one page (This screen ->
Language); Maintenance -> Restart recreates every service of the stack (the helper's restart job), for what a
network drop leaves stuck.

Menu:
- Overview - health, what is not set up, updates, links.
- HOUSE
  - General - house language, house place (moved from Weather), time zone, clock language.
  - Home map - the map EDITOR is the page (walls, rooms, zones, openings, underlay, icons); a room's rename, thermometer/hygrometer and delete are in its zone sheet. The main screen's map is view + control only.
  - Family - people, phones, rights, QR pairing, the app; "Who is home": phones' MACs, router, grace (today presence.env on the host).
- DOOR CARDS (own top item) - cards by number: add (tap the reader or type the UID), name, remove; a card may belong to a family member or be a fixed tag in the house. What a card DOES is set in Automation (rules with a "card tapped" trigger), linked from here.
- DEVICES
  - All devices - list, search, offline, remove; "Add" = the current Add window (Zigbee / ESP via AP / codes / SwitchBot / touch-mouse).
    Tab "Forgotten": gone devices whose name, room, favourite or place is still stored, each with "Forget" (all of it in one step; a rule naming it stays).
  - Zigbee - Gateway: stick (serial path) or network (host:port), adapter (ember/zstack...), channel, PAN/ext PAN, network key (masked), status/firmware (today .env + z2m configuration.yaml); Devices; Pairing (open for N min).
  - Wi-Fi / ESP - nodes, firmware generator, recovery: who is offline -> [Restore]; helper nodes.
  - IR - blasters, remotes, buttons, learning, IR appliances (today the Appliances tab).
  - TVs - ONE page for every TV: control (Tizen / webOS / IR), address, MAC, pairing, power-on (WoL / IR), inputs, apps, remote and commands; [Find on the network] [Add a TV]. Samsung's current page is the model; LG gets the same UI (today JSON only).
  - Climate - ACs, modes, room thermometer source.
  - Locks - SwitchBot etc.
  - Cameras - add/edit: address, user, password (today a settings file only).
  - Wall panels - the whole Panels tab (builder, bind, room, charger, build/OTA, launcher) + PANELS_* incl. the AP password (masked).
- AUTOMATION
  - Automations - the whole Rules tab, incl. the NFC tag actions.
  - History - view + what is recorded, retention, size, erase.
  - Watchdog - answer times, the "silent" threshold (10 min), which checks are on; "Forgetting": after how many days a gone device is forgotten by itself (30, 0 = never).
- VOICE (own section) - wake word (the page must show the real current one), satellites, volume, background, recordings, model, answers.
- DATA - Weather (sources, station; reads the house place).
- THIS SCREEN - screen language, sign in/out, connect another screen (code), install as an app.
- MAINTENANCE - Backups (incl. the nightly time), Update, Plug-ins (on/off + "About the plug-in" for each), Wizard, Build server.

Order of work (each: a throwaway stack, shots to the owner, then live):
1. Menu by subject: drop "About the plug-in" tabs from subject pages, drop non-items, move the existing main-screen tabs/dialogs in as pages (IR, Panels, Rules, History, Family, Map editor, Add).
2. Zigbee gateway page; TVs with LG.
3. Door cards, "Who is home" phones, Cameras.
4. Watchdog, History retention, Wall panels .env -> UI.
5. Main screen cleanup; the wizard; no guessed settings.

## The house's variables (.env)
- Every .env key shows on its subject page (Z2M_* on Zigbee, PANELS_* on Wall panels, FAMILY_* on Family, NFC_* on Door cards, BACKUP_AT on Backups, TZ on General); a key with no subject is on Maintenance -> "House variables" (all keys, grouped, searchable).
- Owner only (the house token); refused in restricted mode.
- A password or token shows as stars with "Show" (its value comes by a separate request).
- A save writes .env atomically and keeps a timestamped copy (.env.bak-...), checks the format of a known key, and says per key whether it applies at once or after a restart, with [Restart the house].
- A key that can lock the owner out (DASHBOARD_TOKEN, MQTT_*) asks to confirm and says what happens.
- A secret is never written to a log and never in an answer unless "Show" asked for it.

## Plug-in API
A plug-in's settings entry names the subject it feeds: settings: [{subject: 'ir', title, render | tabs: [{id, title, render}], needs: ['GET /api/...']}]; it becomes a tab of that page, several plug-ins feed one page. An entry with no known subject gets a page of its own under "Other". In restricted mode a page or tab whose needs the mode refuses is greyed "Not available here". PLUGIN may set "icon", "menu_group" and "menu": False (no page).

A typed field on a page makes leaving it ask about unsaved changes; a field that saves on change itself carries data-autosave and does not.
