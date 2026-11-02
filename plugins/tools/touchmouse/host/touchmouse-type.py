#!/usr/bin/env python3
"""Typing helper, a user service of the desktop session: text from the touchmouse receiver (127.0.0.1 only) goes to
the screen keyboard extension's D-Bus method org.yashome.Osk.Type, which types it by keysym (any language)."""
import os
import queue
import socket
import subprocess
import threading

PORT = int(os.environ.get("TOUCHMOUSE_TYPE_PORT", "7710"))
MAX_BYTES = 240                    # one datagram of text (the receiver passes at most 200)
TIMEOUT_S = 5.0


def clean(raw: bytes) -> str:
    """The text of a datagram: strict UTF-8, no control characters except the newline."""
    text = raw.decode("utf-8")
    return "".join(c for c in text if c == "\n" or (ord(c) >= 32 and ord(c) != 127))


def quote(text: str) -> str:
    """The text as a GVariant string literal, which is what gdbus takes for an `s` argument."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def type_text(text: str) -> None:
    subprocess.run(["gdbus", "call", "--session", "--dest", "org.yashome.Osk", "--object-path", "/org/yashome/Osk",
                    "--method", "org.yashome.Osk.Type", quote(text)],
                   timeout=TIMEOUT_S, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def worker(box: queue.Queue) -> None:
    while True:
        text = box.get()
        try:
            type_text(text)
        except (OSError, subprocess.SubprocessError):
            pass


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", PORT))
    box: queue.Queue = queue.Queue(maxsize=50)
    threading.Thread(target=worker, args=(box,), daemon=True).start()
    while True:
        data = s.recv(MAX_BYTES + 16)
        if len(data) > MAX_BYTES:
            continue
        try:
            text = clean(data)
        except UnicodeDecodeError:
            continue
        if text:
            try:
                box.put_nowait(text)
            except queue.Full:
                pass


if __name__ == "__main__":
    main()
