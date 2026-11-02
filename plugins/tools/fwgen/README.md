# fwgen - the yashGen firmware builder

Settings -> "yashGen" opens `https://yashgen.<domain>/` in a new tab. The builder is a separate tool,
not a part of the house; the front door (nginx) proxies that host to it behind the same sign-in as the dashboard and adds the
builder's token itself, so no token is in a URL, a page or git. Its own host (not a path of the dashboard's) keeps the link
out of the installed app's scope: it opens in a browser tab.

- Where it runs: this page lists the computers with yashGen (Settings -> Devices -> Computers -> a card -> yashGen firmware
  builder -> Install); Use moves it there (plug-in computers, its host piece: README there). The front door keeps its address.
- Turning it off = `fwgen` out of `PLUGINS` (and the `yashgen` host of the front door).
- Without that front-door host (a direct `:8765` address) the link falls back to `/fwgen/`, which then does not exist.
