# Firmware packages of the provision plug-in

Copy this folder into your ESPHome config directory as `provision/` (the C++ include path in
`provision.yaml` is `provision/provision_scan.h`, relative to that directory), then in a node's YAML:

```yaml
substitutions:
  name: my-node

packages:
  base: !include provision/base.yaml          # Wi-Fi + fallback AP "${name}-setup" + MQTT
  provision: !include provision/provision.yaml  # optional: this node can recover its neighbours
```

`secrets.yaml` beside the node needs `wifi_ssid`, `wifi_password` (the compiled Wi-Fi; a relay hands
over the Wi-Fi it runs on now), `ap_password`, `mqtt_host`, `mqtt_user`, `mqtt_password`. Set
`ap_password` to the house's `PANELS_AP_PASSWORD` (`.env`; Add -> ESPHome shows it with the MQTT secret):
the setup AP is then secured with the one key the plug-in fills in by itself. The MQTT account needs to
publish `provision/seen/<name>` and `provision/<name>/result` and read `provision/#` (a device account
does). `base.yaml` is optional: with your own `wifi:` and `mqtt:` blocks keep a fallback `ap:` (with
`password: !secret ap_password`) and `captive_portal:`, or the node cannot be recovered.

A recovery does not change the firmware. What the lost node's portal saves goes to flash (NVS), keyed by
the config hash: it survives reboots and identical rebuilds and is dropped only by a build with a changed
config or ESPHome version. The relay joins the setup AP with `save: false` and goes home to the network it
ran on before (manual IP, hidden, BSSID kept) without saving anything: its flash stays as it was, and a
reboot when it does not get back brings its own networks back. The durable fix
is an OTA build with the current `HOUSE_WIFI_*` (the plug-in's README, "When the house Wi-Fi changes").

`provision.yaml` reads `ap_password` from `secrets.yaml` too: an empty key in the command means "this
node's own key" for a `*-setup` AP, so the shared key never goes on MQTT. A `*-setup` AP is never joined
open. When this package changes, OTA the relays before updating the core: a relay on a firmware that
does not know the command fails slowly.

The relay needs ESP32 (it uses `esp_wifi` directly) and the ESPHome `http_request` component. It joins
the lost node's AP by name and posts nothing unless the AP's MAC is the one the backend saw. The command
is `ap|host|METHOD|path|ssid_field|psk_field|bssid|id|ap_password`; firmware and backend change together.
