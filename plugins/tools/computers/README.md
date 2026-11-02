# computers - the house's computers on its network and the servers on them

Settings -> Devices -> Computers and servers -> Computers: each computer of the house with whether the house reaches it, its name, the TV and
input its screen is, and Remove. "Search the network" looks through the house's /24 (from `BIND_ADDRESS`) for machines
with SSH; "Add by address" takes one by hand.

A computer of the house is a machine the house's key (`YASHOME_SSH_DIR/id`, the one the helper uses for git) logs in to
as `COMPUTERS_SSH_USER`, and where `sudo` asks that user no password. This plug-in never holds the key: the helper checks
(`POST /v1/hosts/check`) and searches (`POST /v1/hosts/scan`), running the fixed `helper/host_probe.sh` there and
nothing else. The helper keeps each computer's host key the first time (`.helper/hosts_known_hosts`); a key that changes
later is refused until "Take the new key" (after a reinstall). Remove forgets it too.

The TV: the probe reads each connected screen's EDID - its maker and the HDMI port it is plugged in (the CEC physical
address, 1.0.0.0 = HDMI 1). The card offers the house's one TV of that maker with its input of that number ("HDMI 3");
an input renamed on the TV ("PC") is picked by hand.

## Who runs what: one broadcast

Every computer that got a server from its card also gets the hello responder (`helper/roles/hello-install.sh`, run by
the helper after each install or remove; not in WSL: its NAT hides it): the system unit `yashome-hello`, UDP 7708,
private askers only. The companion `computers-discover` (`setup/compose.yml`, host network) broadcasts `? yashome 1`
there every 30 s; each computer answers `{"yashome": 1, "name", "servers": [...], "ports": {"touchmouse": 7709}}`,
and who answered is `plugin-state/computers/discovered.json` (`discovered()`, dropped 5 min after the last answer).
The plug-ins of those servers take it from there: touchmouse lists every receiver found so, without a hand entry.
Keep 7708/udp closed to everything but the house's LAN: where ufw is active the installer opens it to the computer's own
LAN only. The companion asks from UDP 7707 and the answers come back there: on the house's host with ufw,
`sudo ufw allow from <LAN>/24 to any port 7707 proto udp` (a reply to a broadcast matches no connection the firewall knows).
The ports are fixed in practice; tests move them with `YASHOME_HELLO_PORT` (7708, both ends) and `YASHOME_DISCOVER_PORT` (7707, the companion).

## Servers (roles)

A computer can take a role of the house, installed from its card and picked on the role's own page. The helper runs a
fixed script there as root (`sudo sh -s`, arguments checked): `helper` ROLE_SCRIPTS. The probe reports a role as installed
when `/opt/yashome-<role>/compose.yml` is there (readable by that user: it holds no key).

One page, Settings -> Devices -> Computers and servers, two tabs. **Computers**: a short card per computer (name, address,
its TV, the servers on it - green: in use there); a click opens it: rename, its TV and input, Remove, and each server
Install or Remove (not the one in use; the build server's Android app builder is a line under it). **Servers**: each server
and a drop-down of the computers that have it - the one it runs on (by name; for the big model the house's own computer is
"<name>: only the small model"); a line when its ssh tunnel is down. One computer per server.

**No keys between the house and its servers**: the build server and TTS on a computer listen on its loopback only, and the
house reaches them through the helper's ssh tunnel (`POST /v1/tunnels`: `helper:8795`, `helper:8092` -> that computer's
`127.0.0.1`), with the same key that installed them; the big model and yashGen go through the host piece's tunnels. Use
points the tunnel there; the card shows whether it is up. The helper keeps its tunnels over a restart, and the check every
5 minutes puts them right when they differ from the servers in use.

- **Build server**: Install runs the build plug-in's `server/install-build-server.sh` with the house's address (to fetch the
  server from): docker when missing, the server in `/opt/yashome-build` on `127.0.0.1:8795` (no key). Use: the
  house builds there (settings `build.remote`). Remove (`helper/roles/build-remove.sh`) takes the container, its volumes and
  the folder away; not the one in use. The house's own computer takes it the same way (over ssh to itself, its own
  `/opt/yashome-build`): no `.env` key.
- **Android app builder**: beside a build server (its jobs pass through it): Install / Remove run that installer again with
  `apk` / `noapk` (the Android SDK image, about 7 GB the first time). The signing keys travel with each app job, through the
  tunnel.

- **Speech (TTS)**: Install (`helper/roles/tts-install.sh`) puts the voice plug-in's TTS service (`/plugins/voice/tts.tar.gz`)
  in `/opt/yashome-tts` on `127.0.0.1:8092` (`VOICE_TTS_OPEN=1`). Use: the house speaks with it (settings `voice.tts`) and
  hands its spoken files to the satellites itself (`/plugins/voice/tts/...`, the voice plug-in's README); the house's own:
  back to its `tts` container. Remove: not the one in use.

- **Big model (LLM)**: Install (`helper/roles/llm-install.sh`) puts Ollama and the voice settings' big model there: on Linux
  the official installer (a service on its loopback), in a WSL Ollama in the user's `~/ollama` with `serve.sh`. Use makes it
  the house's big model: `plugin-data/computers/llm.json` {host, user, wsl, serve}; the host piece follows it. The house's
  own computer: its Ollama is the small model, always there; Use on it = no big model. Remove: not the one in use.

  The host piece (`host/install.sh`, run once by the house's owner on the house's computer, again after these files
  change): user units `yashome-llm-tunnel` (`llm-tunnel.sh`: ssh with the house's key and the helper's known hosts,
  `-L 127.0.0.1:11435:127.0.0.1:11434`, running `serve` there for a WSL one), `yashome-llm-health` (`llm-health.py`,
  `127.0.0.1:11436/health`: available / busy / off; an NVIDIA card's owner gets it back at once) and
  `yashome-llm-follow.path` (llm.json changed: the tunnel restarts). Their environment (`~/.config/yashome/llm.env`):
  `HOUSE_DIR`, `YASHOME_SSH_KEY` (the house's key), `YASHOME_HOSTS_KNOWN` (the helper's known hosts); `LLM_JOBS_DIR`: a
  file `job.<name>` there = a training job of ours owns the card (busy). Ollama is never opened to the network. The voice
  service reaches :11435 and :11436 through its relays.

- **yashGen firmware builder** (Linux only, not a WSL): Install (`helper/roles/yashgen-install.sh`) prepares
  `/opt/yashome-yashgen` (docker, rsync, a compose.yml on the official ESPHome image of the builder's version - start.sh
  writes it as `ESPHOME_VERSION` in its `.env` - and start.sh / stop.sh); nothing runs yet. Use (the Servers tab, or the card) writes `plugin-data/computers/yashgen.json` {host, user};
  the house's own computer: empty = its own builder. Remove: not the one in use.

  The host piece, when `YASHGEN_SRC` is in `.env` (the builder's folder on the house's computer): the user units
  `yashome-yashgen-tunnel` and `yashome-yashgen-follow.path`. `yashgen-tunnel.sh`: no computer in use, this host's builder
  runs (`YASHGEN_LOCAL_UNIT`); a computer: that unit stops, the builder's code (without tests, outputs and data), its data
  (projects, own secrets, pairing codes; the newer copy wins) and the house's node YAMLs (no secrets) go there with rsync,
  `start.sh` gets the builder's token and the house's ESP secrets (`ESPHOME_SECRETS`, as `YASHGEN_HOUSE_SECRETS` in the host piece's `yashgen.env`) on its stdin - they stay in
  `/run` there, never on its disk - and an ssh tunnel brings its `127.0.0.1:8090` to `YASHGEN_LISTEN`, where the front door
  already proxies yashGen: the front door does not change. `yashgen-stop.sh` (on stop and on every move) brings the data
  back and stops it there. A deletion does not travel: a project deleted there comes back from here.

- **Player (mpv)**: on every computer at a TV that wants one (no Use, not on the Servers tab; not in a WSL): Install
  (`helper/roles/player-install.sh`) puts mpv and the player plug-in's user unit there, Remove takes them off (mpv stays).
  The house drives it through the helper over ssh: the player plug-in's README.

- **Touchmouse receiver**: like the player, on every Linux computer that wants one (no Use; not in a WSL): Install
  (`helper/roles/touchmouse-install.sh`) puts the touchmouse plug-in's receiver there, Remove takes it off. The hello
  responder then names it with its port, so the touchmouse plug-in lists it: that plug-in's README.

The arguments reach a script inside it (`set -- ...` at its top), never on a command line: a key would show in `ps`.

**A Windows computer** (OpenSSH): add it with its WSL, `<distribution>/<user>`; the probe and the roles run there
(`wsl -d <distribution> -u <user> -- sh -s`), so that user needs sudo without a password in that distribution.

| file | what |
|---|---|
| `__init__.py` | the list (`plugin-data/computers/computers.json`), `/api/computers` (list, add, edit, remove, check, scan, roles, forget-key), a check of every computer every 5 minutes |
| `ui.js` | the Settings page |
| `setup/env.example` | `COMPUTERS_SSH_USER`; `YASHGEN_*` for the host piece (`YASHGEN_RESPEAKER`: a folder of node YAMLs outside the house that also goes to the builder) |
| `host/` | the host piece: `install.sh`, the big model's tunnel and health, yashGen's tunnel and stop, `llm-target.py` (reads `llm.json` / `yashgen.json` for the tunnels) |
| `discover/discoverd.py`, `setup/compose.yml` | the `computers-discover` companion (the broadcast above) |
| `setup/secrets.sh` | makes the companion's `plugin-state/computers/` folder as the house's user |
| `i18n.json` | the page's words |

A computer found at another address with the same host name moves there when its old address does not answer.
Owner only (the house token).
