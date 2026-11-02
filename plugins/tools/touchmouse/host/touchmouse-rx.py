#!/usr/bin/env python3
"""Touchmouse receiver for a Linux host: UDP from a phone or a panel -> a uinput mouse.
Datagrams: "m dx dy" (move), "b l|r|m 1|0" (button down/up), "w n" (wheel notches),
"k back|forward|playpause|voldown|mute|volup|home|kbd|up|down|left|right|enter" (a key; the volume ones are
the media keys: the desktop changes the system's volume), "t <text>" (UTF-8 text, at most 200 bytes: handed to the typing
helper on 127.0.0.1, which types it in the desktop session). Needs /dev/uinput."""
import fcntl
import os
import socket
import struct
import time

PORT = int(os.environ.get("TOUCHMOUSE_PORT", "7709"))
BIND = os.environ.get("TOUCHMOUSE_BIND", "0.0.0.0")
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BUTTONS = {"l": BTN_LEFT, "r": BTN_RIGHT, "m": BTN_MIDDLE}
KEYS = {"back": 158, "home": 172, "kbd": 148,      # kbd = KEY_PROG1 (XF86Launch1): free for a binding, GNOME ignores KEY_KEYBOARD
        "up": 103, "left": 105, "right": 106, "down": 108, "enter": 28, "forward": 159,
        "playpause": 164, "mute": 113, "voldown": 114, "volup": 115}      # media keys: the desktop plays, pauses, sets the volume
TYPE_PORT = int(os.environ.get("TOUCHMOUSE_TYPE_PORT", "7710"))     # the typing helper (a user service), loopback only
MAX_TEXT = 200                     # bytes of text in one datagram
MAX_TEXT_PER_S = 10                # more text datagrams in a second are dropped
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT, UI_DEV_CREATE = 0x40045564, 0x40045565, 0x40045566, 0x5501
EVFMT = "llHHi"
MAX_STEP = 500                     # one datagram moves the pointer at most this far
MAX_WHEEL = 20                     # and turns the wheel at most this many notches
MAX_PER_S = 500                    # more datagrams in a second are dropped
RELEASE_S = 2.0                    # a button held this long without a datagram is let go (a lost "b l 0")


def uinput():
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    for k in [*BUTTONS.values(), *KEYS.values()]:
        fcntl.ioctl(fd, UI_SET_KEYBIT, k)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
    for r in (REL_X, REL_Y, REL_WHEEL):
        fcntl.ioctl(fd, UI_SET_RELBIT, r)
    os.write(fd, struct.pack("80sHHHHi", b"yashome touchmouse", 6, 0x1209, 3, 1, 0) + b"\0" * (64 * 4 * 4))
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def emit(fd, *events):
    t = time.time()
    sec, usec = int(t), int((t % 1) * 1e6)
    os.write(fd, b"".join(struct.pack(EVFMT, sec, usec, ty, c, v) for ty, c, v in events + ((EV_SYN, 0, 0),)))


held = set()


def handle(fd, words):
    if words[0] == "m" and len(words) == 3:
        dx, dy = (max(-MAX_STEP, min(MAX_STEP, int(w))) for w in words[1:])
        emit(fd, (EV_REL, REL_X, dx), (EV_REL, REL_Y, dy))
    elif words[0] == "w" and len(words) == 2:
        n = max(-MAX_WHEEL, min(MAX_WHEEL, int(words[1])))
        if n:
            emit(fd, (EV_REL, REL_WHEEL, n))
    elif words[0] == "b" and len(words) == 3 and words[1] in BUTTONS:
        btn, down = BUTTONS[words[1]], words[2] == "1"
        (held.add if down else held.discard)(btn)
        emit(fd, (EV_KEY, btn, 1 if down else 0))
    elif words[0] == "k" and len(words) == 2 and words[1] in KEYS:
        emit(fd, (EV_KEY, KEYS[words[1]], 1))
        emit(fd, (EV_KEY, KEYS[words[1]], 0))


def forward_text(out, raw: bytes) -> bool:
    """Valid UTF-8 within the cap goes to the typing helper (a datagram on loopback); anything else is dropped."""
    if not raw or len(raw) > MAX_TEXT:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    try:
        out.sendto(raw, ("127.0.0.1", TYPE_PORT))
    except OSError:
        return False
    return True


def release_all(fd):
    for btn in list(held):
        emit(fd, (EV_KEY, btn, 0))
    held.clear()


def main():
    fd = uinput()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((BIND, PORT))
    s.settimeout(RELEASE_S)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second, count, texts = int(time.time()), 0, 0
    while True:
        try:
            data = s.recv(MAX_TEXT + 16)
        except socket.timeout:
            release_all(fd)
            continue
        now = int(time.time())
        second, count, texts = (second, count + 1, texts) if now == second else (now, 1, 0)
        if count > MAX_PER_S:
            continue
        if data[:2] == b"t ":
            texts += 1
            if texts <= MAX_TEXT_PER_S:
                forward_text(out, data[2:])
            continue
        words = data.decode("ascii", "replace").split()
        try:
            if words:
                handle(fd, words)
        except ValueError:
            pass


if __name__ == "__main__":
    main()
