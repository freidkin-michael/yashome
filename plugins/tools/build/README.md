# build - the build server

Firmware for the house's own devices (panels, knobs, sensor nodes) is ESPHome: it has to be compiled
(several GB of toolchain, a few minutes per build) and flashed. The build server does that, on a
computer of the house (this one included). Plug-ins that make firmware (`requires: build`) send it jobs; the first
flash of a new device goes over USB from the browser with the firmware the server built, every later
one over the air (OTA) from the server.

## On a computer of the house

Settings -> Devices -> Computers and servers -> a computer -> Build server: Install, then Use (plug-in
computers). Install has the helper log in with the house's ssh key and run `server/install-build-server.sh`
there as root: docker when missing, the build server from the house (`/plugins/build/server.tar.gz`, the
same code as `server/`) in `/opt/yashome-build`, its port on that computer's loopback only
(`127.0.0.1:8795`, no key); both containers carry `com.centurylinklabs.watchtower.enable: "false"` (built there, an
updater must not pull them). Use points the helper's ssh tunnel there (`helper:8795`) and
the house builds there (settings `build.remote` = that computer); Use on the house's own computer = this
host's server again. No key and no code exist for it: whoever can reach the port has logged in to that
computer already. Plan for at least 4 GB of memory and 10 GB of disk there (each build container is
limited to 4 GB).

A job carries the house's Wi-Fi and broker secrets for the firmware: secrets.yaml lives on the server
only for the time of a job, but ESPHome compiles the secrets into the firmware and its build files, which
stay in the server's work volume (the build cache) - treat that computer like the house itself. An
ESPHome config can carry C++: a job runs any code in the build container. One firmware build at a time
(an app build may run beside it: each container has up to 4 GB); a compile is stopped after 45 min, an
upload after 6.

## Android apps (the app builder)

Settings -> Devices -> Computers and servers -> a computer with the build server -> Android app builder -> Install: the
installer again with `apk`, the same server code on the Android SDK image beside it (`apk/Dockerfile`, about 7 GB of disk;
about 5 minutes for the first app, about a minute later with the gradle cache in its volume). The build server passes app
jobs to it; the house still knows one address. It builds the Yash! app (plug-in family) and the Home Panel launcher (plug-in
panels) from their sources in this checkout and signs them with the house's keys, kept in `plugin-state/<plug-in>/` (in
the backup): the first build has the builder make the key, which stays there until the house has stored it. Lose that key
and every phone or panel has to uninstall the app once. The key and its password travel with each job, through the house's
ssh tunnel. The builder clears the shared gradle home's init scripts before every app; it trusts the dependencies gradle
downloads (no verification). Remove takes the container, its volumes and image away. The keys are plain files in
`plugin-state/<plug-in>/` (`app.keystore`, `app.keystore.pass`): copy them away by hand too, if you like a copy outside the
house's backups.

## The server's own environment

`APK_URL` (where the app builder is, set by the installer's compose file), `BUILD_MODE` and `BUILD_WORK` (`esphome` and
`/work` by default; the app builder's image sets `apk`), `BUILD_PORT` (8795), `ESPHOME_BIN` (the esphome command) and
`GRADLE_USER_HOME` (the app builder's Gradle cache).

## API (for plug-ins)

`core`-side helpers in this plug-in: `submit(name, files, main="main.yaml", ota=None) -> job id` (files:
text or bytes; `ota = {"address", "attempts"?, "hold"?}`), `job(id) -> {state, progress, log, artifacts}`,
`release(id, abort=False)` (a job with `hold` waits in state `ready` after compiling, up to 10 minutes, until
this - time to put the node into a state that takes an upload; `abort=True` ends it without the upload), `artifact(id, "firmware.factory.bin") -> bytes`.
Apps: `submit_apk(name, project_dir, password, keystore=None, google_services=None, app_id=None,
alias="home") -> job id` (no keystore: the builder
makes one - fetch `artifact(id, "release.keystore")`, store it, then `drop_artifact(id, "release.keystore")`),
then `artifact(id, "app.apk")`. The project's gradle reads
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` and `release.keystore` in its root, and `-PyashAppId`.
The server itself: `GET /v1/info`, `POST /v1/jobs`, `POST /v1/jobs/<id>/go` / `abort`, `GET /v1/jobs/<id>`,
`GET /v1/jobs/<id>/<artifact>` (no key), see `server/server.py`.
`server()` -> its address ("" = none), `use_remote(host)` / `in_use()`: the computer in use (plug-in computers).

Routes of the house (the dashboard's token only): `GET /api/build/server` (the computer in use, whether one is set),
`POST /api/build/server/test` (the server's `/v1/info`), `GET /api/build/jobs/<id>` (a job's state and log),
`GET /api/build/jobs/<id>/firmware.factory.bin|firmware.ota.bin` (a download). `GET /plugins/build/server.tar.gz` is the
server's code for the installer.

| file | what |
|---|---|
| `__init__.py` | the helpers and routes above |
| `server/server.py`, `server/Dockerfile` | the build server |
| `server/install-build-server.sh` | its installer, run on the computer as root (`house` and `apk` / `noapk` arguments) |
| `apk/Dockerfile` | the app builder's image (Android SDK) |
| `setup/settings.example.json` | the settings section `build` (`remote`: the computer in use) |
| `i18n.json` | the plug-in's words |

There is no Settings page of its own: the server is set up on Settings -> Devices -> Computers and servers.
