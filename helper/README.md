# helper - the container that changes this host for the dashboard

Settings -> Maintenance -> Plug-ins and update (its Plug-ins and Update tabs), Settings -> Maintenance ->
Restart and Backups, and `yashctl plugin|key|apply|update|rollback|job`,
need things the backend may not do itself: write `.env`, run `make update`, `git pull`. The helper
does them. It holds the docker socket - that is root on the host - so it does a fixed list of
things and nothing else.

| file | what |
|---|---|
| `helper.py` | a small HTTP API (port 8796, on the `helper` network it shares only with the backend), every call with `HELPER_KEY` |
| `host_probe.sh` | what `/v1/hosts/check` runs on a computer (`sh -s`): fixed |
| `Dockerfile` | python alpine (pinned by digest) + docker CLI, compose, git, make, ssh, openssl; a passwd entry for `PUID` |
| `roles/` | the scripts a role install or removal runs on a computer (`sudo -n sh -s`) |
| `../tools/helper-job.sh` | a job: `apply` (make update), `update` (backup, `git merge --ff-only` of upstream, make update), `rollback` (back to the commit before the last update), `restore` (a backup), `restart` (every container of the stack recreated) |

## The API (the backend calls it at `/api/helper/*`, with the house token only)

- `GET /v1/state` - the plug-ins (on, needed by another), their keys from `setup/env.example`
  (secrets never shown, host-only ones read-only but for `network_ok` ones), git (behind, incoming CHANGELOG lines), the job
- `POST /v1/plugins {"on": [...]}` - writes `PLUGINS`; names it cannot see are kept, `PLUGINS=*` is left to the host
- `POST /v1/env {"key", "value"}` - one plug-in key; never the core's keys (`secrets.env.example`,
  `COMPOSE_*`, `DOCKER_*`, `YASHOME_*`), never a path, a device or an account name (a key marked
  `[network-ok]` takes `tcp://host:port` or `none`); a port only as a number
- `POST /v1/jobs {"kind": "apply"|"update"|"rollback"|"restore"|"restart", "file": "<backups/ archive, restore only>"}`, `GET /v1/jobs/current` - one job at a time
- `POST /v1/hosts/check {"hosts": [{"host", "user", "wsl"?}]}` - the house's computers (plug-in computers): logs in to each with
  the deploy key (`~/.config/yashome/ssh/id`, `YASHOME_SSH_DIR`) and runs the fixed `host_probe.sh` (host name, OS, MAC, the screens' EDID,
  whether `sudo -n true` passes); `wsl` is `<distribution>/<user>` for a Windows computer whose WSL runs the
  commands; nothing of the request reaches the remote shell, a host is an address or a name, never an
  option. Host keys: `.helper/hosts_known_hosts`, kept the first time, refused when they change
- `POST /v1/hosts/scan {"net", "user"}` - a private IPv4 net of at most a /22: every address with port 22 open, checked as above
- `POST /v1/hosts/forget {"host"}` - its kept host key goes (after a reinstall)
- `POST /v1/hosts/run {"host", "user", "wsl"?, "role", "action", "args"}`, `GET /v1/hosts/runs` - a role installed on or removed
  from a computer, in the background (one run per computer, its log kept): the script of `ROLE_SCRIPTS` from this
  checkout goes to `sudo -n sh -s -- <args>` there; each argument has its own pattern, a host whose key is not known yet
  (never checked) is refused, and so is a changed key (`StrictHostKeyChecking=yes`). Roles: build, apk, tts, llm,
  yashgen, player, touchmouse, thread, matter (scripts in `helper/roles/` and the build plug-in; thread takes a name of
  `/dev/serial/by-id`, never a path, and a baud rate of a fixed list). After every run the hello
  responder (UDP 7708, `helper/roles/hello-install.sh`) is installed there too
- `POST /v1/hosts/player {"host", "user", "cmd", ...}` - one command to the player of a computer (plug-in player): only the
  command's own fields, each of its type (an http(s) address for play), go on the stdin of the fixed
  `python3 /opt/yashome-player/ctl.py` there; its answer `{ok, state | error}` comes back. One ssh connection per computer
  (`-M -N -f`, ControlPersist 5 min, on /dev/null) carries the commands; not in a WSL
- `POST /v1/hosts/thread {"host", "user", "cmd", ...}` - a Thread border router (plug-in thread): only the fixed
  `sudo -n /opt/yashome-thread/ctl.sh` there with `status`, `restart`, `leaderweight` (`weight` 0-255) or `joiner`
  (`pskd`, `eui` 16 hex or `*`: on its stdin, never on a command line) or `pair` (`addr` an IPv6 address, `form` the pair
  fields: urlencoded on its stdin); `{rc, out}` with the status keys only (`pair_status`, `pair_reply` {ok, node, hash, error})
- `POST /v1/tunnels {"role", "host", "user", "wsl"?}`, `GET /v1/tunnels` - the server of a role on a computer, brought here
  by ssh: `-L 0.0.0.0:<port>:127.0.0.1:<port>` (build 8795, tts 8092, matter 5580 (the Matter controller's WebSocket); `thread1`..`thread8`: 8181-8188 -> that border
  router's REST, 8081) with the house's key and the kept host keys, so that
  server listens on its own loopback and needs no key; the dashboard's backend reaches it as `helper:<port>` on the compose
  network (the helper publishes no port). One computer per role; no host: the tunnel goes. Kept in `.helper/tunnels.json`
  and taken up again at start; an ended ssh is started again after 10 s (state up / down, its last error)

`.env` is written under `.env.lock` (the same lock `tools/envlib.sh` takes), never while a job runs.

## How a job runs

A job is a one-shot container `yashome-job` of the same image, so it survives the helper being
recreated by `make update`. It sees the checkout, the house, `PLUGINS_EXTRA_DIR` and zigbee2mqtt's
data at their real paths (`make update` writes `YASHOME_DIR`, `YASHOME_HOUSE_ABS`, `YASHOME_EXTRA_ABS`,
`YASHOME_Z2M_ABS`); its log is `.helper/job.log` (the one before: `job.log.prev`), its state
`.helper/job.json`, the rollback target `.helper/previous` and the backup made before it
`.helper/previous-backup`.

## Limits

- Update needs a plain `git clone` with no local changes (a `git worktree` keeps its repository
  outside the checkout: update it by hand).
- An `ssh` remote needs a deploy key of its own in `~/.config/yashome/ssh` (`id` + `known_hosts`,
  see the main README) and a host the containers can reach (not `localhost`); `https` just works.
  A key directory made after the install is picked up by the next `make update`.
- Update trusts the remote: what it has is pulled, unsigned.
