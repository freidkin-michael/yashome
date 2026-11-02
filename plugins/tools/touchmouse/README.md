# touchmouse - a phone or a wall panel as a touchpad

The Yash! app (and the Home Panel status bar) moves the pointer of a TV box or a computer of the
house: one finger moves, a tap clicks, two fingers right-click or - dragged up and down - scroll,
three fingers click the wheel; under the pad one row: rewind, Play/Pause, fast forward (the left and right arrow keys:
a player seeks) and the volume (down, mute, up - the computer's own). The phone sends
small UDP datagrams straight to a receiver on the LAN - fast, no round trip through the dashboard.
This plug-in lists the receivers (`GET /api/touchmouse`); the dashboard's Add -> Touchmouse adds and
deletes plain hosts.

| file | what |
|---|---|
| `__init__.py` | the list: devices with a `touchmouse` field and the hosts of the settings section `touchmouse` |
| `ui.js`, `i18n.json` | Add -> Touchmouse |
| `__init__.py` (routes) | `GET /api/touchmouse`, `PUT` / `DELETE /api/touchmouse/receivers/<id>` (a host added by hand), `GET /plugins/touchmouse/host.tar.gz` (the host files, for the installer) |
| `host/touchmouse-rx.py`, `host/touchmouse-rx.service` | the receiver of a Linux host (a uinput mouse) |
| `host/touchmouse-type.py`, `host/touchmouse-type.service` | the typing helper of the desktop session (below) |
| `setup/settings.example.json` | the shape of the settings section |

## Found by themselves

A computer of the house with the receiver installed from its card answers the house's one broadcast (the computers
plug-in, "Who runs what") and is listed here by itself, with its computer's name and TV ("found in the network",
no Delete: remove the receiver from the computer's card). A receiver listed by hand or by a device keeps that entry.

## A receiver

**A Linux computer** (a media PC, a desktop): the easy way is Settings -> Devices -> Computers and servers -> its
card -> Touchmouse receiver -> Install (plug-in computers): the receiver and the typing helper, found by themselves.
By hand, on that host, from a checkout of this repository -

    sudo install -D -m 755 plugins/tools/touchmouse/host/touchmouse-rx.py /usr/local/lib/yashome/touchmouse-rx.py
    sudo install -m 644 plugins/tools/touchmouse/host/touchmouse-rx.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now touchmouse-rx

then the dashboard -> Add -> Touchmouse -> its address, port 7709, Linux. `/etc/yashome/touchmouse.env`
may set `TOUCHMOUSE_PORT` and `TOUCHMOUSE_BIND` (the address to listen on) and `TOUCHMOUSE_TYPE_PORT` (the loopback
port of the typing helper, 7710).

**An Android TV box**: it needs a receiver app or service of its own on the box (the datagrams are
the same); give its device in `mqtt_devices.json` a field
`"touchmouse": {"port": 7709, "os": "android"}` (`host` defaults to the device's address) - it then
appears in the list by itself.

## The datagrams

    m <dx> <dy>      move (a step is capped at 500)
    b l|r|m 1|0      left / right / middle (wheel) button down / up
    w <n>            the wheel turns n notches (+ up, - down; capped at 20)
    k back|home|kbd  a key (kbd = KEY_PROG1, XF86Launch1: bind it on the receiver, e.g. to an on-screen keyboard)
    k forward        KEY_FORWARD (browser forward)
    k playpause      KEY_PLAYPAUSE (the player in front plays or pauses)
    k voldown|mute|volup   KEY_VOLUMEDOWN / KEY_MUTE / KEY_VOLUMEUP: the desktop changes the system's volume
    k up|down|left|right|enter   the arrow keys and Enter
    t <text>         UTF-8 text (at most 200 bytes; the phone splits longer text) typed into whatever has the focus

There is no key or signature: anyone who can send UDP to the port moves the pointer and types text (the pointer,
three buttons, the wheel, Back, Home, Keyboard, the arrows, Enter and text). The unit takes datagrams from private addresses only (LAN,
VPN; `IPAddressAllow` - narrow it with `systemctl edit touchmouse-rx`), at most 500 a second, and lets
a held button go after 2 s without a datagram. Allow the port
only from the house LAN and your VPN, for example `sudo ufw allow from <your LAN>/24 to any port
7709 proto udp` (and the same for your VPN's range).

## Typing text (Linux, GNOME on Wayland)

The receiver unit is sealed (no capabilities, `AF_INET` only), so it does not type itself: it hands each `t <text>`
datagram to `touchmouse-type.py`, a user service of the desktop session that listens on 127.0.0.1:7710 only and calls
the screen keyboard extension's D-Bus method `org.yashome.Osk.Type` (keysyms, so any language types). Install it for the
logged-in user: copy `host/touchmouse-type.py` to `/usr/local/lib/yashome/`, `host/touchmouse-type.service` to
`~/.config/systemd/user/`, then `systemctl --user daemon-reload && systemctl --user enable --now touchmouse-type`.
