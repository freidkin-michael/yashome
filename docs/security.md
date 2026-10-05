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
  browser's local storage; "Log out" forgets it.
- **Authenticated broker with an ACL.** `allow_anonymous false`; accounts and roles through
  mosquitto's dynamic-security plug-in: `home` for the backend (all topics), one account per
  plug-in service that asks for it in its `setup/broker.json` with rights on its own topics only
  (the Zigbee plug-in: `z2m`, `zigbee2mqtt/#`), and optional device accounts (`MQTT_DEVICE_USERS`)
  that may do everything except publish to `home/state/#`, to what a plug-in marks
  `device_readonly` (`zigbee2mqtt/#`) and to `MQTT_BACKEND_ONLY` - so a node cannot send what only
  the backend should (permit_join, a relay command). Your own nodes may also use `home`. `make
  secrets` writes `config/mosquitto/dynamic-security.json` from `.env`; rotate by editing `.env`
  and running `make update` (it restarts the broker).
- **No outbound connections from the core.** It talks only to your broker and your devices. Of the
  plug-ins only `weather` goes out (Open-Meteo, and ipwho.is once to find the area when
  `HOME_LAT`/`HOME_LON` are empty).

## Do not expose the ports directly

`8765` (dashboard), `1883` (broker) and a plug-in's own ports (the Zigbee plug-in: `8080`, the
zigbee2mqtt frontend) are published on all interfaces of the host so a fresh install just works
on the LAN. Do not forward them on the
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
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
   }
   ```

2. Prefer a VPN (WireGuard) to a public hostname. If it must be public, add a second factor at
   the proxy (Authelia, Authentik); the proxy can inject the bearer token for authenticated
   sessions, so the in-page token prompt never appears from outside.
3. Bind the broker and a plug-in's frontend to the LAN address only once your nodes are
   known: change `ports:` in `docker-compose.yml` (or the plug-in's `setup/compose.yml`) to
   `"192.0.2.10:1883:1883"`.

## What the token does not protect

Anyone on your Wi-Fi who can reach the broker with a valid account can switch devices; the
token protects the HTTP side. Keep the broker accounts as secret as the token.
