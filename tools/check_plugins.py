"""The shipped plug-ins: ASCII everywhere except i18n.json; Russian anywhere only in i18n files and lines
marked i18n; i18n.json = {"xx": {"English": "text"}};
ui.js and static/*.js parse (node --check, when node is there); and nothing of one house in them -
no private LAN address, MAC, Zigbee address or home directory outside the documented placeholders, and
none of the words of your own house: names of hosts, users, rooms, devices, one per line in the
git-ignored .house-words (or HOUSE_WORDS=a,b,c)."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASCII_ONLY = ("plugins/", "tests/plugins/")                 # the core itself keeps Russian in i18n.js
BINARY = {".png", ".jpg", ".jpeg", ".ico", ".gif", ".webp", ".woff", ".woff2", ".db", ".onnx", ".apk", ".svg"}
PLACEHOLDER_IPS = {"192.168.4.1", "192.168.4.0"}          # the setup AP every ESPHome node opens
PLACEHOLDER_MACS = {"00:11:22:33:44:55", "aa:bb:cc:dd:ee:ff"}
PRIVATE_IP = re.compile(rb"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")
MAC = re.compile(rb"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
IEEE = re.compile(rb"\b0x[0-9a-fA-F]{16}\b")                  # a Zigbee device address: test ones start 0x00000000
_hw = ROOT / ".house-words"
_words = [w.strip() for w in ((_hw.read_text(encoding="utf-8-sig").splitlines() if _hw.is_file() else [])
                              + __import__("os").environ.get("HOUSE_WORDS", "").split(",")) if w.strip()]
WORDS = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in _words) + r")(?![\w-])", re.I) if _words else None
HOME_DIR = re.compile(rb"(?<![\w/])/home/([a-z_][a-z0-9_-]*)")
rc = 0
# plug-in packages: plugins/<name>/ or plugins/<group>/<name>/ (a group folder has no __init__.py)
PKGS = sorted(p.parent for p in (ROOT / "plugins").glob("*/__init__.py")) + sorted(
    p.parent for p in (ROOT / "plugins").glob("*/*/__init__.py") if not (p.parent.parent / "__init__.py").exists())


def bad(msg):
    global rc
    print("FAIL", msg)
    rc = 1


def tracked():
    """The files git would publish (every file under plugins/ and tests/ without git)."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
        return [ROOT / n.decode() for n in out.split(b"\0") if n]
    except (OSError, subprocess.CalledProcessError):
        if (ROOT / ".git").exists():
            raise SystemExit("check_plugins: a git checkout, but git ls-files failed - refusing a partial scan")
        return [f for base in ("plugins", "tests") for f in (ROOT / base).rglob("*")
                if f.is_file() and "__pycache__" not in f.parts]


CYRILLIC = re.compile(r"[\u0400-\u04ff]|\\u04[0-9a-fA-F]{2}")   # i18n: what the rule looks for
MARK = re.compile(r"(?:^|\s)(#|//)\s*i18n\b")                        # a comment "# i18n: ..." marks localization code
for f in sorted(tracked()):
    if not f.is_file() or f.suffix.lower() in BINARY:
        continue
    raw, rel = f.read_bytes(), f.relative_to(ROOT).as_posix()
    if f.name not in ("i18n.json", "i18n.js"):          # Russian only in translations and localization code
        lines = raw.decode("utf-8", "replace").split("\n")
        for n, line in enumerate(lines):
            if CYRILLIC.search(line) and not MARK.search(line) and (n == 0 or not MARK.search(lines[n - 1])):
                bad(f"{rel}:{n + 1}: Russian outside i18n (mark localization code with an i18n comment)")
    if rel.startswith(ASCII_ONLY) and f.name != "i18n.json" and re.search(rb"[^\x00-\x7f]", raw):
        bad(f"{rel}: non-ASCII outside i18n.json")
    for ip in {m.decode() for m in PRIVATE_IP.findall(raw)} - PLACEHOLDER_IPS:
        bad(f"{rel}: a LAN address {ip} (use 192.0.2.x, the documentation range, or .env)")
    for mac in {m.decode().lower().replace("-", ":") for m in MAC.findall(raw)} - PLACEHOLDER_MACS:
        bad(f"{rel}: a MAC address {mac} (use 00:11:22:33:44:55 or .env)")
    for ieee in {m.decode().lower() for m in IEEE.findall(raw)}:
        if not ieee.startswith("0x00000000"):
            bad(f"{rel}: a Zigbee address {ieee} (test ones start with 0x00000000)")
    for user in {m.decode() for m in HOME_DIR.findall(raw)} - {"someone"}:
        bad(f"{rel}: a home directory /home/{user} (use /home/someone or a path from .env)")
    for w in {m.lower() for m in WORDS.findall(raw.decode("utf-8", "replace"))} if WORDS else ():
        bad(f"{rel}: '{w}' is on your house's word list (.house-words, HOUSE_WORDS)")
for f in [q / "i18n.json" for q in PKGS if (q / "i18n.json").is_file()]:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        ok = isinstance(d, dict) and all(re.fullmatch(r"[a-z]{2}", k) and isinstance(v, dict)
                                         and all(isinstance(a, str) and isinstance(b, str) for a, b in v.items())
                                         for k, v in d.items())
        if not ok:
            bad(f"{f.relative_to(ROOT)}: must be {{\"xx\": {{\"English\": \"text\"}}}}")
    except ValueError as e:
        bad(f"{f.relative_to(ROOT)}: {e}")
names = {q.name for q in PKGS}
GROUPS = {"transports", "devices", "sources", "tools"}
for q in PKGS:
    if q.parent != ROOT / "plugins" and q.parent.name not in GROUPS:
        bad(f"{q.relative_to(ROOT)}: group folder {q.parent.name!r} is not one of {sorted(GROUPS)}")
sys.path.insert(0, str(ROOT / "tools"))
import pluginlib  # noqa: E402
for n, why_not in sorted(pluginlib.plan([q for q in PKGS if not q.name.startswith("_")], None)["errors"].items()):
    bad(f"plug-in {n}: {why_not}")
dup = sorted(n for n in names if [q.name for q in PKGS].count(n) > 1)
if dup:
    bad(f"plug-in names must be unique across the group folders: {dup}")
node = shutil.which("node")
for f in [q / "ui.js" for q in PKGS if (q / "ui.js").is_file()] + [j for q in PKGS for j in sorted(q.glob("static/*.js"))]:
    if node:
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        if r.returncode:
            bad(f"{f.relative_to(ROOT)}: {r.stderr.strip()[:300]}")
if not node:
    print("note: node not found, JS syntax not checked")
print("plug-ins:", "ok" if rc == 0 else "FAILED")
sys.exit(rc)
