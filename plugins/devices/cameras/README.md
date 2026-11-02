# cameras - IP cameras on the dashboard

IP cameras proxied through the backend, so the dashboard stays on one origin and a camera's password
never reaches a browser. A Cameras tab (shown once there is at least one camera) shows one camera at a
time: its snapshot fetched again every 0.4 s (2 s after an error, a fetch given up after 8 s).

| file | what |
|---|---|
| `__init__.py` | the routes below, `register_source(fn)` for cameras another plug-in adds (the panels plug-in: the bound tablets' cameras, with a key header of their own) |
| `ui.js`, `i18n.json` | the Cameras tab |
| `setup/settings.example.json` | the settings section `cameras` |

## Routes (with a token, like every `/api` route)

- `GET /api/cameras` - `[{id, name}]`, the configured cameras and those of the sources.
- `GET /api/camera/<id>/snapshot` - one JPEG (or another image type, never SVG): the camera's `snapshot`
  URL, else the first frame of its `stream`. One snapshot per camera at a time (a second one meanwhile:
  503), the whole read within about 8 s (checked after each chunk) and 4 MiB. A camera with neither
  field answers 502 "camera url is not http(s)".
- `GET /api/camera/<id>/stream?fps=3` - MJPEG. The camera's own stream when it answers below 400 with
  `multipart/x-mixed-replace` and a boundary (re-framed: only its JPEG frames go out); otherwise one
  stitched from snapshots at `fps` (0.2-10, default 3; outside that range it is clamped, not a number:
  422) - without a `snapshot` to stitch from, 502 "camera has neither stream nor snapshot". At most 6
  streams at once (then 503); opening the camera's stream may take 30 s. No UI of this repository uses it today; it is there for your own pages.

Errors: 404 unknown camera; 502 when the URL is not http(s), the camera redirects (a redirect is never
followed), is unreachable, answers 400 or more, answers something that is not an image, too much or too
slowly, or has neither `snapshot` nor `stream`.

## Settings

The section lives in `plugin-data/cameras/settings.json`: `{"cameras": [{id, name, snapshot?, stream?,
user?, pass?}]}`; `user` / `pass` go to the camera as HTTP Basic auth. Every string value is expanded
with the environment when the backend starts (`${VAR}` or `$VAR`; an unset one stays as it is written),
so a password stays in `.env` and the file keeps the placeholder.
