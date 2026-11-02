# Home Panel - the Android launcher of the wall panels

Home Panel turns an Android tablet into a wall panel of the house. It replaces the home screen
(`CATEGORY_HOME`), keeps the screen on, pairs with the house and shows the panel's layout from
`/panel-view.html`. It also reports its battery (the charge window), serves the tablet's cameras
and keeps the kiosk usable without anybody touching Android.

## Files

| file | what |
|---|---|
| `build.sh` | builds the APK in a docker Android toolchain, by hand |
| `app/build.gradle.kts` | package name, version, signing |
| `Launcher.java` | the home screen: pairing code, then the panel page in a WebView |
| `Pairing.java` | answers the house's UDP invitation (port 51823), registers, waits for the bind |
| `Heartbeat.java` | `POST /api/panels/heartbeat` once a minute; learns name, charge window, orientation |
| `Caps.java` | the passport (screen, cameras, sensors, battery) sent with register and heartbeat |
| `Bridge.java` | the `Panel` JavaScript object the panel page calls (settings, status, camera port, touch mouse) |
| `Status.java` | battery, Wi-Fi and link state for the page's status bar |
| `TouchMouse.java` | sends the page touchpad's moves to a touch mouse receiver over UDP |
| `CamServer.java`, `Conn.java`, `CamOp.java`, `Idle.java` | `/snapshot` and `/stream` of the tablet's cameras on port 8081 - only with the camera key the house gives in the heartbeat answer (its camera proxy sends it) |
| `Charge.java` | the charge window with root (sysfs node); without root the house switches the charger's plug |
| `Ambient.java` | brightness from the proximity and light sensors |
| `Rotation.java` | locks the system rotation to the layout's orientation |
| `TapCorner.java` | top-left corner: 3 taps open settings, a 10 s hold starts pairing again |
| `Link.java`, `Adb.java`, `Net.java` | why there is no link (and fixes with root), wireless debugging, HTTP |
| `SettingsActivity.java`, `Prefs.java` | the kiosk settings page and what the panel remembers |
| `res/values*/strings.xml` | the texts, English and Russian |

## Building

From the dashboard: Panels -> **Build the app**. The build plug-in's app builder builds this folder;
the Panels tab links the APK (`/plugins/panels/panel.apk`).

By hand, on any host with docker: `./build.sh` (or `./build.sh <dir>`). The APK lands in
`<house>/plugin-state/panels/panel.apk`, or `apk/` here without a house.

Signing: the house's key, `plugin-state/panels/app.keystore` with `app.keystore.pass` (and
`app.keystore.alias` when not `home`) - made by the first Build the app, used by `build.sh` too, in
the backup. A key you already have (tablets run a launcher signed with it): put it there before the
first build, with `PANELS_APP_ID` set to its package. Without any, `build.sh` makes `debug.keystore`
once; keep it, a new key means reinstalling the app on every tablet.

## Pairing

1. Install the APK on the tablet, start it and make it the home app: it shows a six-digit code.
2. Dashboard -> Panels -> **Connect a tablet**: the house broadcasts an invitation in the network.
3. The tablet answers and registers; check the code, name the panel, **Bind**.
4. The tablet gets its own key and opens the panel, for example
   `http://192.0.2.10:8765/panel-view.html?panel=hall`.

To pair again, hold the top-left corner for 10 s; three taps there open the settings
(server address, key, panel name, reset pairing, Android settings).
A panel that is paired already needs no invitation then: it registers with its house over HTTP at
once (the code on its screen is checked on the dashboard as for a new one), and its key stays until
a new one is handed over. Only a new key ends pairing: the old record answers "bound" without one,
and the saved key never goes to another server. After 5 minutes without a bind it goes back to its
panel; the pairing screen shows only while the thread that asks for it is the live pairing.

## Left out

Voice (the wake word and the microphone stream) is not in this app: it comes with the voice
plug-in. The page's voice calls on the `Panel` bridge are no-ops until then.

## Updating tablets

The shipped launcher does not update itself. A house whose launcher does (a device owner set once over adb,
`dpm set-device-owner <package>/<its AdminReceiver>`, installs without a tap; otherwise the system asks on the
panel) reads the version at `GET /plugins/panels/app` and the file at `/plugins/panels/panel.apk`. Its project
lives in `plugin-data/panels/launcher/`, which Build the app prefers to this directory.

## A house with an older launcher

A house whose tablets already run an older launcher under another package name sets
`PANELS_APP_ID` in `.env` to that package. The build then uses it as the application id, so the
new APK installs as an update over the old one (it must be signed with the same key).
