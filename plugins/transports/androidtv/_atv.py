"""Android TV / Google TV over the LAN: the protocol of the Google TV phone remote (the androidtvremote2 package, put
into EXTRA_PIP): a client certificate the TV trusts after one code on its screen, then keys, apps, power and volume.
The package is imported here only, so the plug-in loads (and says what is missing) without it."""
try:
    from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
    MISSING = ""
except ImportError as _e:              # the house runs without it: its health row says what to add
    AndroidTVRemote = None
    MISSING = str(_e)

    class CannotConnect(Exception):
        pass

    class ConnectionClosed(Exception):
        pass

    class InvalidAuth(Exception):
        pass

PACKAGES = "androidtvremote2==0.3.2 aiofiles==25.1.0 protobuf==7.36.2 cryptography==50.0.1"   # for EXTRA_PIP
CLIENT = "Yashome"                     # the name the TV shows while it pairs

# the tv plug-in's remote keys -> the TV's key codes (androidtvremote2 takes the name without KEYCODE_)
REMOTE = {"up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "ok": "DPAD_CENTER",
          "back": "BACK", "home": "HOME", "menu": "MENU", "info": "INFO", "vol_up": "VOLUME_UP", "vol_down": "VOLUME_DOWN",
          "ch_up": "CHANNEL_UP", "ch_down": "CHANNEL_DOWN"}
MEDIA = {"play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "stop": "MEDIA_STOP", "rewind": "MEDIA_REWIND",
         "fast_forward": "MEDIA_FAST_FORWARD"}
INPUTS = {"HDMI 1": "TV_INPUT_HDMI_1", "HDMI 2": "TV_INPUT_HDMI_2", "HDMI 3": "TV_INPUT_HDMI_3", "HDMI 4": "TV_INPUT_HDMI_4"}
ON, OFF, MUTE, UP, DOWN = "WAKEUP", "SLEEP", "VOLUME_MUTE", "VOLUME_UP", "VOLUME_DOWN"   # WAKEUP/SLEEP: discrete, not a toggle


def remote(host: str, certfile: str, keyfile: str):
    """A client for one TV (not connected yet); RuntimeError when the package is not installed."""
    if AndroidTVRemote is None:
        raise RuntimeError(f"the androidtvremote2 package is missing ({MISSING}): add it to EXTRA_PIP")
    return AndroidTVRemote(CLIENT, certfile, keyfile, host)
