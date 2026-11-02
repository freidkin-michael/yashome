# presence - is anyone home

The device `presence` tells whether any of the house's phones is on the Wi-Fi: `home` (true / false),
`stale` with `unseen_days` (no phone seen for 4 days or more - a changed phone or MAC rather than a long
absence), and the event `event` = `arrival` / `departure` for rules ("arrival -> hall light on"). The
brand badge in the header turns green (home), red (away), amber (stale) or plain with "no link" when
the host piece has been silent for 15 minutes.

## The host piece

`host/phone-presence.sh` runs on any Linux host of the house LAN (the core's host or another one):
it finds the phones by their Wi-Fi MACs (L2 arping, which gets through Android's doze; ICMP only with
the MAC confirmed; a probe every 30 s, a sweep of the interface's /24 at most every 5 min - a wider
LAN is swept only in part), keeps "home" for 10 min after the last answer of any phone (`PRESENCE_INTERVAL`,
`PRESENCE_SWEEP_GAP`, `PRESENCE_GRACE` in its env file, in seconds) and publishes on the plug-in's own
bus `presence/#` with its own broker account. "stale" means no phone at all was seen for 4 days; one
phone of several with a changed MAC does not show there.

1. On the phones: Wi-Fi -> the house network -> Privacy -> use the device MAC (not a randomised one);
   note that MAC.
2. `make update` on the core host generates `MQTT_PRESENCE_PASSWORD` (account `presence`, rights on
   `presence/#` only).
3. On the host that runs the piece (packages `iputils-arping`, `mosquitto-clients`):

       sudo install -D -m 755 plugins/sources/presence/host/phone-presence.sh /usr/local/lib/yashome/phone-presence.sh
       sudo install -D -m 600 plugins/sources/presence/host/presence.env.example /etc/yashome/presence.env
       sudo editor /etc/yashome/presence.env      # PRESENCE_MACS, MQTT_HOST/PORT, MQTT_PRESENCE_PASSWORD
       sudo install -m 644 plugins/sources/presence/host/phone-presence.service /etc/systemd/system/
       sudo systemctl daemon-reload && sudo systemctl enable --now phone-presence

   It runs as a dynamic user with CAP_NET_RAW only. `PRESENCE_IFACE` empty = the interface of the
   default route. MQTT is plain: across hosts keep it on the LAN or a VPN.

Check: `journalctl -u phone-presence -f`, and the device `presence` in the dashboard.
