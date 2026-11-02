"""The dashboard's script and style: web/js/*.js (in the order of their names) and web/app.css.

The script runs in one function scope, as it always did: only what its Object.assign(window, ...) names
reaches the plug-ins and the onclick= attributes. The parts are pieces of that one function, so they are
served joined: GET /app.js = app_js(). page() gives index.html with both put back inline, the one file
the page used to be (the tests and tools/check_js.py read that). Standard library only: the core loads it
by its path, the tests import it."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEAD = "// One scope: only the names below reach window (the plug-ins' ui.js and the onclick= attributes).\n(() => {\n"
TAIL = "})();\n"


def js_parts(root=ROOT) -> list:
    return sorted((pathlib.Path(root) / "web" / "js").glob("*.js"))


def app_js(root=ROOT) -> str:
    return HEAD + "".join(p.read_text(encoding="utf-8") for p in js_parts(root)) + TAIL


def app_css(root=ROOT) -> str:
    return (pathlib.Path(root) / "web" / "app.css").read_text(encoding="utf-8")


def page(root=ROOT) -> str:
    """index.html with /app.css and /app.js put back inline."""
    html = (pathlib.Path(root) / "index.html").read_text(encoding="utf-8")
    link, script = '<link rel="stylesheet" href="/app.css">\n', '<script src="/app.js"></script>\n'
    if html.count(link) != 1 or html.count(script) != 1:
        raise ValueError("index.html must load /app.css and /app.js once each")
    return html.replace(link, "<style>\n" + app_css(root) + "</style>\n").replace(script, "<script>\n" + app_js(root) + "</script>\n")
