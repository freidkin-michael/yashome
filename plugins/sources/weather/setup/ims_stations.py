"""Rebuilds ims_stations.json (next to the weather plug-in) from the IMS public station list, no token needed:
python3 setup/ims_stations.py [source.json]. The active stations only: number, name, latitude, longitude, and whether they
measure the temperature, radiation (Grad: sun or cloud) and rain."""
import json
import os
import sys
import time
import urllib.request

URL = "https://ims.gov.il/en/envista_station_info"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ims_stations.json")


def build(src: dict, fetched: str) -> dict:
    """A station listed without coordinates ("Bet Dagan Rad", a sensor mast of "Bet Dagan") takes those of the station whose
    name it extends (at_of says which); one with neither is left out."""
    every = [s for s in (src.get("data") or {}).get("stations", {}).values() if s.get("active") == "1"]
    placed = {str(s.get("name") or "").strip().lower(): s for s in every if s.get("lat") and s.get("lon")}
    out = []
    for s in every:
        name, at = str(s.get("name") or "").strip(), s
        if not (s.get("lat") and s.get("lon")):
            base = max((n for n in placed if name.lower().startswith(n) and name.lower() != n), key=len, default=None)
            if base is None:
                continue
            at = placed[base]
        mon = s.get("monitors") or {}
        rec = {"num": int(s["stn_num"]), "name": name, "lat": round(float(at["lat"]), 4), "lon": round(float(at["lon"]), 4),
               "temp": bool(mon.get("TD")), "rad": bool(mon.get("Grad")), "rain": bool(mon.get("Rain"))}
        if at is not s:
            rec["at_of"] = int(at["stn_num"])
        out.append(rec)
    out.sort(key=lambda x: x["num"])
    return {"source": URL, "fetched": fetched, "note": "the active IMS stations; rebuilt by setup/ims_stations.py", "stations": out}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        src = json.load(open(sys.argv[1], encoding="utf-8"))
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "yashome-weather/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:          # noqa: S310 - the fixed IMS address
            src = json.loads(r.read().decode("utf-8"))
    data = build(src, time.strftime("%Y-%m-%d"))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{len(data['stations'])} stations -> {os.path.normpath(OUT)}")
