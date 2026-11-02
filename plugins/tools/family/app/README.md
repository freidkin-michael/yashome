# Yash! - the Android app of the family plug-in

The app of the people of the house: their devices, a ring that sounds even on silent, the house
state while away, widgets, a quick-settings tile for the lock, a touchpad for a TV player. Built by
the dashboard (Family phone -> Build the app, on the build server's app builder) or by
`build.sh` (in a container, any host with docker) - both with the house's key; the Family card
offers the APK.

| file | what |
|---|---|
| `build.sh` | builds `app-release.apk` with the house's key and copies it to `plugin-state/family/yash.apk` (or the directory given) |
| `app/build.gradle.kts` | the app; Firebase only when `app/google-services.json` is there |
| `app/src/main/java/com/yash/home/` | `SetupActivity` (pairing, test ring), `DashboardActivity`, `RingService` (the alarm), `LiveService` (the house link without Firebase), `PushService` (Firebase pushes), `Cloud` (Firebase side), `HomeApi` (the house's REST and sockets), widgets, lock tile, `MouseActivity` (the touchpad: move, tap, two-finger tap or drag, three-finger tap, Back, Home, Keyboard; its receivers come from the touchmouse plug-in) |

## Two ways a ring reaches the phone

- **Firebase** (your `google-services.json` in the build, the house with `FAMILY_FIREBASE_*`): a
  push wakes the app anywhere.
- **The house link** (no Firebase): `LiveService` keeps `/api/family/live` open while the phone
  reaches the house - its Wi-Fi or a VPN. A quiet "Connected to the house" notification shows it; it
  comes back after a reboot. Away without a VPN, no ring arrives. Every minute it also sends the
  gateway of its Wi-Fi network (a VPN is skipped; no location permission): the house counts the phone as
  home when that is its router (the house learned it from a phone at home) and the Wi-Fi name matches one it learned (the
  app asks for the location permission once, to read the name).
  The same message carries the app's version; on the house's `update` command `AppUpdate` downloads the
  built APK, checks its sha256 and hands it to the system installer (`UpdateReceiver` gets the answer).

## On the phone, once

1. Allow notifications (the ring's Stop button lives in its notification).
2. Settings in the app -> Battery: let the app run unrestricted, so Android does not stop the house
   link or delay a push.
3. Test ring / Stop in the app's settings: the alarm sounds on silent and on "Do not disturb" -
   except "Total silence", which mutes alarms too (the settings screen warns then).

## Addresses

https anywhere; plain http only to a private address (192.168.x.x, 10.x, 172.16-31.x, 100.64-127.x
of a VPN) or a local name (`.local`, `.lan`, `.home.arpa`, `.internal`). A pairing link pointing
elsewhere is refused, and the confirmation shows the house's address - and warns when it would
replace the house the phone is connected to.

Pairing sends the phone's ANDROID_ID with the QR's one-time code, so pairing the same phone again
(a reinstall, a new QR) gives back its own entry in the house instead of a second one.

The server settings are encrypted with a key kept in the phone; they are not backed up and not
moved to a new phone: pair the new phone by a new QR.

## Widgets

- **Channel** (`DeviceWidget`, 1x1): one channel per widget, a short name and an icon picked when
  it is placed (lamp, plug, fan, AC, lock, door, gate, boiler, TV, water, garage, power).
  - switch: a tap flips it (shown at once, then confirmed by the house);
  - lock: open/closed picture; a tap locks at once, unlocking asks first (`LockActionActivity`);
  - dimmer: top third brighter, bottom third dimmer (the house steps a tenth, `"up"`/`"down"`),
    middle on/off of its paired switch; shows the percent. The pair is the house's rule: the switch on
    the same gang (`state_l2` for `brightness_l2`, `state` for `brightness`), else none;
  - air conditioner (a `mode` setting plus a power switch): the icon is the current mode, a tap
    switches power.
  Offline or unknown = a dimmed icon.
- **Lock** (`LockWidget`): the older house-lock widget, kept for widgets already placed.

Fresh state: the app tells the house which channels its widgets show,
`PUT /api/family/me/widgets {"items":[{"dev":"...","code":"..."}]}` (when a widget is placed or
removed, when the house link connects, and at app start at most once an hour). The house then sends
changes as `{"cmd":"state","values":{"<dev>":{"<code>":value,"online":true}}}` over the house link;
with Firebase, and only when the house allows it (Family phone -> "Widget states through Firebase",
off by default: the values then pass Google's servers), as an FCM data push `cmd=state` with `s` =
the full `values` object as a JSON string (split over several pushes when long). A 30-minute update
reads the state anyway, as a fallback.

The mouse screen: the pad, then a text field (Send types the text on the receiver) and a row of Back, Forward and Keyboard, then the four arrows as a cross with Enter in the middle.
