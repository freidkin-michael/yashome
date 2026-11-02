"""The engine holds nothing of a real house: no home-LAN address, no real MAC, no device id or host name of the house beside
it (YASHOME_HOUSE_DIR, else ~/yashome-house; skipped when there is none). Ids and host names only (they have "_", "-" or a
digit): plain words like a room's name are everyday language and may appear."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOUSE = Path(os.environ.get("YASHOME_HOUSE_DIR") or Path.home() / "yashome-house")
MAC = re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b")
BARE = re.compile(r"mac[\"']?\s*[:=]\s*[\"']?([0-9a-f]{12})\b")
PLACEHOLDER = re.compile(r"^(aa:bb:cc|00:00:00|11:22:33|de:ad:be|12:34:56)")
# ids every house has because the engine or a remote names them so: an IR key, the shower plug-in's own device
GENERIC = re.compile(r"^(power_on|power_off|vol_up|vol_down|ch_up|ch_down|hdmi ?\d)$")


def _house_words():
    ids, macs = set(), set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("id", "name", "friendly_name", "host", "hostname", "node", "device") and isinstance(v, str):
                    if len(v) >= 5 and re.search(r"[_\-\d]", v) and not MAC.fullmatch(v.lower()) and not GENERIC.match(v.lower()):
                        ids.add(v.lower())
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for f in [*HOUSE.glob("*.json"), *HOUSE.glob("plugin-data/*/*.json")]:
        try:
            walk(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    tracked = subprocess.run(["git", "-C", str(HOUSE), "ls-files"], capture_output=True, text=True).stdout.split()
    live = [str(f.relative_to(HOUSE)) for f in [HOUSE / "status_cache.json", HOUSE / "pending_timers.json", *HOUSE.glob("plugin-state/**/*")]
            if f.is_file() and f.stat().st_size < 5_000_000]
    for rel in tracked + live:                           # every MAC of the house, by its pattern: tracked files and the runtime ones
        try:
            t = (HOUSE / rel).read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        found = MAC.findall(t.replace("-", ":")) + [":".join(h[i:i + 2] for i in range(0, 12, 2)) for h in BARE.findall(t)]
        macs.update(m for m in found if not PLACEHOLDER.match(m))
    return ids, macs


@pytest.mark.skipif(not HOUSE.is_dir(), reason="no house beside the engine")
def test_no_house_data_in_the_tracked_files():
    ids, macs = _house_words()
    print(f"the house beside the engine: {len(ids)} ids, {len(macs)} MACs")
    assert ids and macs, "the house beside the engine has no device ids or MACs: the check would prove nothing"
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
    hits = []
    for rel in files:
        p = ROOT / rel
        try:
            t = p.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"\b192\.168\.50\.\d{1,3}\b", t):
            hits.append(f"{rel}: a home-LAN address")
        if any(m in t or m.replace(":", "") in t for m in macs):
            hits.append(f"{rel}: a MAC of the house")
        hits += [f"{rel}: the house's id #{i}" for i, w in enumerate(sorted(ids)) if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", t)]
    assert hits == []                                       # what and where, never the value itself
