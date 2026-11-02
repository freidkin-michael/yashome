# Security model

- **One bearer token.** `DASHBOARD_TOKEN` gates `/api` and `/ws`, sent as a header
  (`Authorization: Bearer`); only the WebSocket takes it as `?token=`. A link of the form
  `http://<host>:8765/?token=<token>&tab=<tab>` signs a device in once: the page stores the token,
  removes it from the address bar at once, and the backend masks `token=` in its logs. Treat such
  a link like the token itself. Without a configured
  token every request is refused. Cross-site POSTs and WebSocket handshakes (an `Origin` that
  is not the dashboard's own host) are refused too, and the page carries a CSP that
  keeps scripts, requests and images on the dashboard's own origin (inline script is allowed,
  the page is built that way). The dashboard asks for it once and keeps it in the
  browser's local storage; Settings -> This device -> Log out forgets it.
- **Authenticated broker with an ACL.** `allow_anonymous false`; accounts and roles through
  mosquitto's dynamic-security plug-in: `home` for the backend (all topics), one account per
  plug-in service that asks for it in its `setup/broker.json` with rights on its own topics only
  (the Zigbee plug-in: `z2m`, `zigbee2mqtt/#`), one account per ESP panel (`panel-<name>`, on its own
  `panel/<name>/#`; a plug-in may make such accounts at runtime), and optional device accounts (`MQTT_DEVICE_USERS`)
  that may do everything except publish to `home/state/#`, to what a plug-in marks
  `device_readonly` (`zigbee2mqtt/#`) and to `MQTT_BACKEND_ONLY` - so a node cannot send what only
  the backend should (permit_join, a relay command). A new install has `MQTT_BACKEND_ONLY=home/cmd/#`:
  only the backend commands the house; an existing house keeps its value (empty) until the owner sets it. Never give a node the backend account `home`: it
  may write everything, the state mirror included - nodes use a device account. `make
  secrets` writes `config/mosquitto/dynamic-security.json` from `.env`; rotate by editing `.env`
  and running `make update` (it restarts the broker).
- **The helper holds the docker socket.** That is root on the host. It is a container of its own (not the
  backend), on a network it shares only with the backend, every call needs `HELPER_KEY`, and it does a
  fixed list of things: write `PLUGINS` and the keys a plug-in's `setup/env.example` declares (never the
  core's own keys, nor a path or a device of the host), run `make update`, `git pull --ff-only` after a
  backup, roll the code back. The dashboard reaches it with the house token only (not a phone's, nor a
  panel's). It is always on, so treat the house token as root on the host: whoever holds it - or runs
  script in the dashboard, or is a plug-in (they run inside the backend) - can switch on a plug-in and
  run `make update`, or point a plug-in's account at a host
  of their own. Update pulls what the remote has, unsigned, with a deploy key of its own, never your
  `~/.ssh`. The host itself (and a plug-in on the host network) can reach the helper's container
  address: there `HELPER_KEY` is the only guard. Its jobs run in a one-shot container, so a job
  survives the helper being recreated.
- **No outbound connections from the core.** It talks only to your broker and your devices. Of the
  plug-ins only `weather` goes out (Open-Meteo for the forecast and the city search, the Israel
  Meteorological Service's public last-hour file, and ipwho.is once when no place is set yet).

## Do not expose the ports directly

`8765` (dashboard) and `1883` (broker) are published (zigbee2mqtt's page and the voice service's spoken files go through
`8765`) on all interfaces of the host so a fresh install just works
on the LAN; `BIND_ADDRESS` in `.env` (the LAN address) keeps them off the others. Do not forward them on the
router. For access from outside:

1. Put a reverse proxy with TLS in front of `8765` (nginx, Caddy, Traefik). The backend refuses
   changes whose `Origin` is not its own host, so the proxy must pass the original host on (or
   list the public address in `DASHBOARD_ORIGINS` in `.env`). Caddy does this by itself:

   ```
   home.example.org {
       reverse_proxy 127.0.0.1:8765
   }
   ```

   nginx needs the host and the WebSocket upgrade spelled out:

   ```
   location / {
       proxy_pass http://127.0.0.1:8765;
       proxy_set_header Host $http_host;      # with the port: the Origin check compares both
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # panels refuse to pair through a proxy
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
   }
   ```

2. Prefer a VPN (WireGuard) to a public hostname. If it must be public, add a second factor at
   the proxy (Authelia, Authentik); the proxy can inject the bearer token for authenticated
   sessions, so the in-page sign-in card never appears from outside.
3. Bind the dashboard, the broker and the zigbee2mqtt frontend to the LAN address once your
   nodes are known: `BIND_ADDRESS=192.0.2.10` in `.env`, then `make update` (a reverse proxy and
   local scripts then reach them at that address too).

## A family phone with full access

The family plug-in gives a phone either per-device rights (hidden / view / control) or full access.
Full access is "use the house, do not set it up": a phone reads everything the dashboard reads, but it
changes only the routes the core and the plug-ins mark `use_route` (`core.is_use_route`). Every other
change needs the house token, so a lost phone cannot rebuild the house.

A phone with full access **can**:
- see every device and control it (`/api/devices/{id}/control`, the live `/ws`);
- run, enable, delete and set rules, schedules and auto-off (`/api/rules/*`, `/api/bindings/*`, `/api/automation/*`);
- change its favourites;
- press an IR remote's button;
- play films on a player (search, play, pause, next, the recent list);
- ask the house in words (the text box, push-to-talk, the chat and its speech) and record voice samples; it cannot make the house's speakers talk (`/api/voice/say`).

It **cannot** (these need the house token, the dashboard's):
- pair, revoke or change phones, their rights, the family settings or the app build (`/api/family/*` admin);
- set a rule that rings a phone;
- add, remove, rename, merge or relink devices (Zigbee, ESPHome, generic MQTT, TVs, SwitchBot, NFC tags);
- edit rooms, the map, panel layouts, IR remotes and appliances, voice satellites and settings, wiring;
- open the panels' pairing window, bind, unbind or delete a panel, build or flash an ESP panel;
- computers and servers, the build server, provisioning an ESP over its setup AP;
- backups, the update helper, the sign-in codes for new devices, the health report, the ESP MQTT block.

A plug-in that adds a route a phone should use marks it: `@core.use_route` above `@core.app.post(...)`.

A phone with per-device rights reaches only the devices it may see, and controls only those marked control.

## What the token does not protect

Anyone on your Wi-Fi who can reach the broker with a valid account can switch devices; the
token protects the HTTP side. Keep the broker accounts as secret as the token.
