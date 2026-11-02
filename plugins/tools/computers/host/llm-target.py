"""llm-target.py <llm.json | yashgen.json>: the computer of the house's big model (or of yashGen) as shell assignments
(HOST, RUSER, WSL, SERVE) for llm-tunnel.sh / yashgen-tunnel.sh; each value checked by its pattern (none can reach the
shell otherwise). An empty value: no such field."""
import json
import re
import shlex
import sys

t = json.load(open(sys.argv[1]))
OK = {"host": r"^$|^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$", "ruser": r"^$|^[a-z_][a-z0-9_-]{0,31}$",
      "wsl": r"^$|^[A-Za-z0-9._-]{1,40}/[a-z_][a-z0-9_-]{0,31}$", "serve": r"^$|^/[A-Za-z0-9._/-]{1,200}$"}
for k, rx in OK.items():
    v = str(t.get("user" if k == "ruser" else k) or "")
    if not re.match(rx, v):
        sys.exit(f"{sys.argv[1]}: {k} is not valid")
    print(f"{k.upper()}={shlex.quote(v)}")
