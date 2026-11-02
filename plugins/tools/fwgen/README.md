# fwgen - the yashGen firmware builder

yashGen describes a device and makes its ESPHome firmware, to flash over USB. It is a separate tool,
not a part of the house. This plug-in only adds Settings -> Maintenance -> Firmware builder: a short
note and **Open the builder**.

The link opens in a new browser tab. When the dashboard runs at `home.<domain>`, it points to
`https://yashgen.<domain>/`. The front door (nginx) proxies that host to the builder, behind the
same sign-in as the dashboard, and adds the builder's token itself, so no token is in a URL, a page
or git. Its own host keeps the link out of the installed app's scope. On any other address (for
example a direct `:8765` one) the link is `/fwgen/`, which the dashboard does not serve.

| file | what |
|---|---|
| `__init__.py` | the plug-in's entry (no API of its own) |
| `ui.js`, `i18n.json` | the Settings page |

Setup:

1. Install the builder on a computer of the house: Settings -> Devices -> Computers and servers -> its
   card -> yashGen -> Install (plug-in `computers`, see its README).
2. Give the front door a `yashgen.<domain>` host that proxies to it.
3. Turn `fwgen` on in Settings -> Maintenance -> Plug-ins and update.

Turning it off: `fwgen` out of `PLUGINS` (and the `yashgen` host out of the front door).
