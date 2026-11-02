#!/usr/bin/env python3
"""Convert the simple Yashome brand SVGs (path/circle/rect/g, translate+scale) to an Android VectorDrawable.

  svg2vd.py in.svg out.xml [--size WxHdp] [--skip-rect] [--inset F] [--color-map '#AAA=@color/x,...']
"""
import argparse
import re
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"


def circle_path(cx, cy, r):
    return f"M{cx - r},{cy} a{r},{r} 0 1,0 {2 * r},0 a{r},{r} 0 1,0 {-2 * r},0 Z"


def rect_path(w, h, rx):
    if not rx:
        return f"M0,0 H{w} V{h} H0 Z"
    return (f"M{rx},0 H{w - rx} A{rx},{rx} 0 0 1 {w},{rx} V{h - rx} A{rx},{rx} 0 0 1 {w - rx},{h} "
            f"H{rx} A{rx},{rx} 0 0 1 0,{h - rx} V{rx} A{rx},{rx} 0 0 1 {rx},0 Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--size"); ap.add_argument("--skip-rect", action="store_true")
    ap.add_argument("--inset", type=float, default=0.0, help="shrink artwork to (1-2*inset) around the centre")
    ap.add_argument("--color-map", default="")
    a = ap.parse_args()
    cmap = dict(kv.split("=", 1) for kv in a.color_map.split(",") if kv)
    col = lambda c: cmap.get(c.upper(), cmap.get(c, c))

    root = ET.parse(a.src).getroot()
    vw, vh = [float(x) for x in root.get("viewBox").split()[2:]]
    w, h = (a.size.split("x") if a.size else (str(vw / 4), str(vh / 4)))
    out = ['<vector xmlns:android="http://schemas.android.com/apk/res/android"',
           f'    android:width="{w}dp" android:height="{h}dp"',
           f'    android:viewportWidth="{vw:g}" android:viewportHeight="{vh:g}">']
    s = 1 - 2 * a.inset
    out.append(f'  <group android:translateX="{vw * a.inset:g}" android:translateY="{vh * a.inset:g}" '
               f'android:scaleX="{s:g}" android:scaleY="{s:g}">')

    def emit(el, inh):
        tag = el.tag.replace(NS, "")
        st = {**inh, **{k: v for k, v in el.attrib.items() if k in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin")}}
        if tag == "g":
            for c in el:
                emit(c, st)
            return
        if tag == "rect":
            if a.skip_rect:
                return
            d = rect_path(float(el.get("width")), float(el.get("height")), float(el.get("rx", 0)))
        elif tag == "circle":
            d = circle_path(float(el.get("cx")), float(el.get("cy")), float(el.get("r")))
        elif tag == "path":
            d = el.get("d")
        else:
            return
        attrs = [f'android:pathData="{d}"']
        if st.get("fill", "#000000") != "none":
            attrs.append(f'android:fillColor="{col(st.get("fill", "#000000"))}"')
        if st.get("stroke") and st["stroke"] != "none":
            attrs += [f'android:strokeColor="{col(st["stroke"])}"', f'android:strokeWidth="{st.get("stroke-width", "1")}"']
            if "stroke-linecap" in st:
                attrs.append(f'android:strokeLineCap="{st["stroke-linecap"]}"')
            if "stroke-linejoin" in st:
                attrs.append(f'android:strokeLineJoin="{st["stroke-linejoin"]}"')
        path = "    <path " + " ".join(attrs) + "/>"
        m = re.match(r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)\s*scale\(([-\d.]+)[ ,]+([-\d.]+)\)", el.get("transform", ""))
        if m:
            tx, ty, sx, sy = m.groups()
            path = (f'    <group android:translateX="{tx}" android:translateY="{ty}" android:scaleX="{sx}" android:scaleY="{sy}">\n'
                    f'  {path}\n    </group>')
        elif el.get("transform"):
            raise SystemExit(f"unsupported transform {el.get('transform')}")
        out.append(path)

    for c in root:
        emit(c, {})
    out += ["  </group>", "</vector>", ""]
    open(a.dst, "w").write("\n".join(out))


if __name__ == "__main__":
    main()
