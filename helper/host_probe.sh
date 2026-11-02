# What the helper runs on one of the house's computers (ssh ... sh -s < this): fixed, nothing of a request in it.
# key=value lines: host, os, mac (of the default route's interface), one screen=<connector>|<vendor>|<name>|<port> per
# connected screen, one role=<name> per role installed here (/opt/yashome-<name>/compose.yml), sudo last. <port> is the
# TV input the screen is: its EDID carries the CEC physical address of the HDMI port it is plugged in (1.0.0.0 = HDMI 1);
# empty for a laptop's own screen or a monitor.
echo "host=$(hostname)"
echo "os=$( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -sr)"
i=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
echo "mac=$(cat /sys/class/net/"$i"/address 2>/dev/null)"
command -v python3 >/dev/null && python3 -I - <<'PY'
import glob
for p in sorted(glob.glob("/sys/class/drm/card*-*/edid")):
    try:
        if open(p[:-4] + "status").read().strip() != "connected":
            continue
        e = open(p, "rb").read()
    except OSError:
        continue
    if len(e) < 128:
        continue
    m = e[8] << 8 | e[9]
    vend = "".join(chr(((m >> s) & 31) + 64) for s in (10, 5, 0))
    name = ""
    for i in range(54, 126, 18):
        if e[i:i + 3] == b"\0\0\0" and e[i + 3] == 0xFC:
            name = e[i + 5:i + 18].split(b"\n")[0].decode("ascii", "replace").strip()
    port = ""
    for b in range(1, len(e) // 128):
        x = e[b * 128:(b + 1) * 128]
        if x[0] != 2:
            continue
        k = 4
        while k < min(x[2], 127):
            tag, ln = x[k] >> 5, x[k] & 31
            if tag == 3 and x[k + 1:k + 4] == b"\x03\x0c\x00" and x[k + 4] >> 4:
                port = str(x[k + 4] >> 4)
            k += ln + 1
    print("screen=%s|%s|%s|%s" % (p.split("/")[-2].split("-", 1)[-1], vend, name.replace("|", " "), port))
PY
for d in /opt/yashome-*/; do [ -f "$d/compose.yml" ] && echo "role=$(basename "$d" | sed 's/^yashome-//')"; done 2>/dev/null
[ -f /opt/yashome-player/ctl.py ] && echo role=player                                # mpv, a user unit: no compose
[ -f /usr/local/lib/yashome/touchmouse-rx.py ] && echo role=touchmouse              # the touchmouse receiver: a system unit
sudo -n grep -qs '^  apk-builder:' /opt/yashome-build/compose.yml && echo role=apk     # the installer keeps it root's only
if command -v ollama >/dev/null 2>&1 || [ -x "$HOME/ollama/bin/ollama" ]; then            # LLM: Ollama, system-wide or in the home (WSL)
    echo role=llm
    [ -x "$HOME/ollama/serve.sh" ] && echo "llm_serve=$HOME/ollama/serve.sh"
    m=$(curl -sf -m 3 http://127.0.0.1:11434/api/tags 2>/dev/null | python3 -c 'import json, sys; print(",".join(x["name"] for x in json.load(sys.stdin).get("models", [])))' 2>/dev/null)
    [ -n "$m" ] && echo "llm_models=$m"
fi
sudo -n true 2>/dev/null && echo sudo=yes || echo sudo=no
