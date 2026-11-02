#!/usr/bin/env python3
"""Once per lock: fetch its BLE encryption key from the SwitchBot account and print the env-file
lines. Usage: switchbot-get-key.py <BLE MAC> <account e-mail> (asks for the password)."""
import getpass
import sys

from switchbot.devices.lock import SwitchbotLock


def main():
    if len(sys.argv) != 3:
        print("usage: switchbot-get-key.py <BLE MAC> <SwitchBot account e-mail>", file=sys.stderr)
        print("needs the SwitchBot account's own password (set one in the app when you sign in with Google)"
              " and 2FA off meanwhile", file=sys.stderr)
        sys.exit(2)
    mac, user = sys.argv[1].upper(), sys.argv[2]
    res = SwitchbotLock.retrieve_encryption_key(mac, user, getpass.getpass("SwitchBot account password: "))
    print()
    print("# into /etc/yashome/switchbot-<id>.env:")
    print(f"SWITCHBOT_LOCK_MAC={mac}")
    print(f"SWITCHBOT_LOCK_KEY_ID={res['key_id']}")
    print(f"SWITCHBOT_LOCK_ENC_KEY={res['encryption_key']}")


if __name__ == "__main__":
    main()
