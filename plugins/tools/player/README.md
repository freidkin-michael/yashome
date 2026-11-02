# player - a film on a TV, played by the computer at that TV

A **player** is a computer of the house (plug-in `computers`) at a TV, with the role **Player (mpv)** installed from its card
(Settings -> Devices -> Computers and servers -> the computer -> Player (mpv) -> Install). Every computer at a TV can have
one; there is nothing to pick on the Servers tab. Not in a WSL: it plays on the screen of a Linux computer.

**What Install does there** (`helper/roles/player-install.sh`, as root through the helper): mpv when missing (apt), this
plug-in's `host/ctl.py` and `host/yashome-player.service` from the house (`/plugins/player/host.tar.gz`, no key in it) in
`/opt/yashome-player`, the user unit enabled for the user the house logs in as. mpv waits there with no window
(`--idle`, its IPC socket in `$XDG_RUNTIME_DIR/yashome-player/`, the unit's own runtime directory) and draws on that user's
graphical session: that user has to be logged in on the screen. Remove (`player-remove.sh`) takes the unit and the folder
away; mpv stays installed.

**No port, no key**: the house sends every command through the helper over ssh (`POST /v1/hosts/player`): the helper
checks it (only the command's own fields, an http(s) address) and runs `python3 /opt/yashome-player/ctl.py` there with it on
stdin; ctl.py talks to mpv's socket and prints the player's state. One ssh connection per computer stays open five minutes
(ssh's ControlPersist), so a press does not wait for a login.

**On the dashboard**: the window of the computer's TV (the TV and input set on the computer's card) has the player: the film
and where it is, back / on 30 s, pause, stop, the volume, mute, the sound track and subtitles, and a field for an address.
Play turns the TV on (an LG by Wake-on-LAN), switches it to the computer's input, then mpv opens the address full screen.
The TV's card shows what plays. Settings -> TVs -> Players lists every player (one at no TV too).

## API

- `GET /api/player` - `{players: [{id, name, tv, tv_name, input, state, error}]}`; `state` is ctl.py's: `{idle, volume, mute,
  title, url, pause, pos, duration, buffering, tracks: [{id, type: audio|sub, lang, title, selected}]}` (asked at most every 2 s).
- `POST /api/player/{id}/play {url, title?, start?}` - its TV on and on its input, then the address; `tv_note` says why the TV
  could not be switched (the film plays anyway).
- `POST /api/player/{id}/cmd {cmd, ...}` - `pause` / `mute` `{on?}` (without it: the other way), `seek {by | to}` seconds,
  `volume {level 0-130 | by}`, `audio` / `sub` `{id}` (0 = none), `stop`, `status`.

## Films: sources

The **Films** tab: a name searches every source at once; a page address pasted from a site (there, or in the field of a
player) goes to the source of that site, which takes it apart. A film's window: translation, season, episode, quality, the
player ("Watch on", the last one picked on this screen first), Watch / Continue from where it stopped. What a player watches
is kept in `plugin-state/player/watching.json`: the Continue row, and the next episode starts by itself when one ended past
90 % (Stop by hand ends it: no next one). A player's card has Next episode.

A **source** is a Python module. The house's own: `plugin-data/player/sources/<name>.py` (the house's repository; read again
when the file changes, a broken one is listed with its error in Settings -> TVs -> Players), asked first for a pasted address.
Then the built-in ones: **Files** (the folders of Settings -> TVs -> Players: web listings of a NAS, their sub-folders too)
and **Link** (a film's own address, or a plain page that shows one: its og:video, `<video>`, `<source>`, an .m3u8 / .mp4 in it;
a site that refuses programs or hides its stream needs a source of its own). A source has `NAME` and some of:

```python
NAME = "My site"

def search(query):            # -> [{"id", "title", "year"?, "poster"?, "kind"?: "film" | "series", "note"?}]
def match(url):               # a page address pasted from the site -> the id of its film, None: not this source's
def info(id, translation=None):
    # -> {"title", "year"?, "poster"?, "description"?, "kind"?, "translations"?: [{"id", "name"}],
    #     "seasons"?: [{"n", "episodes": [{"n", "title"?}]}], "qualities"?: ["1080p", ...]}
    # translation given: the seasons and qualities of that translation
def stream(id, translation=None, season=None, episode=None, quality=None):
    # -> {"url", "headers"?: {"Referer": ..., "User-Agent": ..., "Cookie": ...}, "subs"?: [url], "title"?}
```

It runs inside the backend (trusted code, like a plug-in), on the house's computer: the stream address is asked there and
sent to the player with its headers (at most 10, one line each) and subtitle files (at most 10 http(s) addresses). An
exception is shown in words; `_sources.SourceError("...")` says it as it is. Posters are fetched by the house (only the
addresses the sources gave), as the page shows no image from elsewhere.

- `GET /api/player/sources` - the sources and the folders of Files; `PUT /api/player/folders {folders}` (owner)
- `GET /api/player/search?q=` - `{results: [{source, source_name, id, title, ...}], errors: {source: why}}`
- `POST /api/player/resolve {url}` - `{source, source_name, id, info}` of a pasted address
- `GET /api/player/info?source=&id=&translation=` - its info; `GET /api/player/poster?u=` - a poster a source named
- `POST /api/player/{id}/watch {source, id, translation?, season?, episode?, quality?, start?, title?, poster?}` - its TV on and
  on its input, the source's stream there; `POST /api/player/{id}/next` - the next episode
- `GET /api/player/recent`, `DELETE /api/player/recent?source=&id=` - Continue
