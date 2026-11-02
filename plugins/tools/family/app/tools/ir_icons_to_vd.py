#!/usr/bin/env python3
"""IR_ICON (the remote key icons of the dashboard, dumped to JSON) -> res/drawable/ir_<key>.xml.

  node -e '...eval IR_ICON...' > ir_icons.json ; ir_icons_to_vd.py ir_icons.json app/src/main/res/drawable [prefix]
  (AC_ICON the same way with prefix ac_)
"""
import json
import re
import sys
import xml.etree.ElementTree as ET

CUR = "@color/brand_text"


def col(c, inherit):
    c = c if c is not None else inherit
    if c in (None, "none"):
        return None
    return CUR if c == "currentColor" else c


def circle(cx, cy, r):
    return f"M{cx - r},{cy} a{r},{r} 0 1,0 {2 * r},0 a{r},{r} 0 1,0 {-2 * r},0 Z"


def rect(x, y, w, h, rx):
    if not rx:
        return f"M{x},{y} h{w} v{h} h{-w} Z"
    return (f"M{x + rx},{y} H{x + w - rx} A{rx},{rx} 0 0 1 {x + w},{y + rx} V{y + h - rx} A{rx},{rx} 0 0 1 {x + w - rx},{y + h} "
            f"H{x + rx} A{rx},{rx} 0 0 1 {x},{y + h - rx} V{y + rx} A{rx},{rx} 0 0 1 {x + rx},{y} Z")


def convert(svg):
    root = ET.fromstring(svg.replace('xmlns="http://www.w3.org/2000/svg"', ""))
    base = {k: root.get(k) for k in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin")}
    out = ['<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp"',
           '    android:viewportWidth="24" android:viewportHeight="24">']
    for el in root:
        tag = el.tag
        f = lambda k, d=0.0: float(el.get(k, d))
        if tag == "path":
            d = el.get("d")
        elif tag == "circle":
            d = circle(f("cx"), f("cy"), f("r"))
        elif tag == "rect":
            d = rect(f("x"), f("y"), f("width"), f("height"), f("rx"))
        elif tag == "ellipse":
            cx, cy, rx, ry = f("cx"), f("cy"), f("rx"), f("ry")
            d = f"M{cx - rx},{cy} a{rx},{ry} 0 1,0 {2 * rx},0 a{rx},{ry} 0 1,0 {-2 * rx},0 Z"
        else:
            continue                                   # <text> (hdmi1..4 digits): the plug outline stays
        fill = col(el.get("fill"), base["fill"] if base["fill"] is not None else "black")
        stroke = col(el.get("stroke"), base["stroke"])
        a = [f'android:pathData="{d}"']
        if fill:
            a.append(f'android:fillColor="{fill if fill != "black" else CUR}"')
        if stroke:
            a += [f'android:strokeColor="{stroke}"', f'android:strokeWidth="{el.get("stroke-width", base["stroke-width"] or "2")}"',
                  f'android:strokeLineCap="{el.get("stroke-linecap", base["stroke-linecap"] or "butt")}"',
                  f'android:strokeLineJoin="{el.get("stroke-linejoin", base["stroke-linejoin"] or "miter")}"']
        line = "    <path " + " ".join(a) + "/>"
        rot = re.match(r"rotate\(([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\)", el.get("transform", ""))
        if rot:
            line = (f'    <group android:rotation="{rot.group(1)}" android:pivotX="{rot.group(2)}" android:pivotY="{rot.group(3)}">'
                    "\n  " + line + "\n    </group>")
        out.append(line)
    out.append("</vector>\n")
    return "\n".join(out)


def main():
    icons = json.load(open(sys.argv[1]))
    prefix = sys.argv[3] if len(sys.argv) > 3 else "ir_"
    for k, svg in icons.items():
        open(f"{sys.argv[2]}/{prefix}{k}.xml", "w").write(convert(svg))
    print(len(icons), "icons")


if __name__ == "__main__":
    main()
