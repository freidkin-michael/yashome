#!/usr/bin/env python3
"""Yashome brand kit generator. Source of truth for the wordmark "Yash!", the app icon and the palette.

The wordmark text is Nunito ExtraBold (OFL) converted to outlines, so the SVGs render the same
everywhere and the repository needs no font files. Run from the repository root:

    python3 -m pip install fonttools skia-python      # once
    python3 branding/brand.py

Writes SVG sources into branding/, the PWA icons (icon-192.png, icon-512.png, icon-maskable-512.png,
favicon-32.png) into the repository root and the preview sheet into docs/img/brand.png."""
import os
import pathlib
import re
import urllib.request

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "yashome-brand"
NUNITO_URL = "https://github.com/google/fonts/raw/main/ofl/nunito/Nunito%5Bwght%5D.ttf"

# Contrast (WCAG): deep teal #177A75 carries white text at 5.2:1 and reads on white at 5.2:1;
# the bright teal #3CC2B8 is for text, borders and marks on the dark background only (8.7:1 on
# #0E1116, 2.2:1 under white text - never a fill behind white). The dark values are the dashboard's.
THEMES = {
    "light": dict(bg="#FFFFFF", ink="#177A75", sun="#F5B642", text="#1E2B30", muted="#5F6F74",
                  surface="#F3F7F7", tile="#177A75", tile_ink="#FFFFFF", fill="#177A75"),
    "dark":  dict(bg="#0E1116", ink="#3CC2B8", sun="#FFC34D", text="#E6EAF0", muted="#8A93A3",
                  surface="#1A1F29", tile="#1A1F29", tile_ink="#3CC2B8", fill="#177A75"),
}


def font_extrabold() -> TTFont:
    """Nunito at weight 800, instanced from the variable font (downloaded once into the cache)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    var = CACHE / "Nunito[wght].ttf"
    if not var.exists():
        urllib.request.urlretrieve(NUNITO_URL, var)
    return instancer.instantiateVariableFont(TTFont(var), {"wght": 800})


def text_paths(font: TTFont, text: str, size: float, right_x: float, baseline_y: float, fill: str, align: str = "right") -> str:
    """Glyph outlines of `text` as <path> elements (font units -> px); right-aligned at right_x, or left-aligned there."""
    gs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    k = size / upm
    names = [cmap[ord(c)] for c in text]
    width = sum(gs[n].width for n in names) * k
    x, out = (right_x - width if align == "right" else right_x), []
    for n in names:
        pen = SVGPathPen(gs)
        gs[n].draw(pen)
        out.append(f'<path transform="translate({x:.2f} {baseline_y}) scale({k:.5f} {-k:.5f})" fill="{fill}" d="{pen.getCommands()}"/>')
        x += gs[n].width * k
    return "".join(out)


def sun(cx, cy, r, color, ray_w):
    rays = []
    for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
        rays.append(f"M{cx+dx*r*1.6:.1f} {cy+dy*r*1.6:.1f} L{cx+dx*r*2.1:.1f} {cy+dy*r*2.1:.1f}")
    k = 0.7071
    for dx, dy in [(k, k), (-k, k), (k, -k), (-k, -k)]:
        rays.append(f"M{cx+dx*r*1.55:.1f} {cy+dy*r*1.55:.1f} L{cx+dx*r*1.95:.1f} {cy+dy*r*1.95:.1f}")
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>'
            f'<path d="{" ".join(rays)}" stroke="{color}" stroke-width="{ray_w}" stroke-linecap="round" fill="none"/>')


def mark_h(ink, sun_c):
    """The h drawn as the house: stem + arch, roof with a chimney, the ! as the far wall, the sun as its dot."""
    return (f'<g fill="none" stroke="{ink}" stroke-width="36" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M588 150 L588 300"/>'
            f'<path d="M588 235 Q588 190 638 190 Q683 190 683 235 L683 300"/>'
            f'<path d="M553 175 L668 85 L783 175"/>'
            f'<path d="M753 160 L753 235"/></g>'
            + sun(753, 288, 17, sun_c, 7))


def wordmark(font, t, bg=True, ink=None, sun_c=None):
    ink, sun_c = ink or t["ink"], sun_c or t["sun"]
    rect = f'<rect width="900" height="400" fill="{t["bg"]}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="400" viewBox="0 0 900 400">{rect}'
            f'{text_paths(font, "Yas", 230, 545, 300, ink)}{mark_h(ink, sun_c)}</svg>')


def icon(t, tile=True, rounded=True):
    ink = t["tile_ink"] if tile else t["ink"]
    rect = f'<rect width="512" height="512" rx="{112 if rounded else 0}" fill="{t["tile"]}"/>' if tile else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">{rect}'
            f'<g fill="none" stroke="{ink}" stroke-width="52" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M92 262 L256 116 L420 262"/><path d="M256 222 L256 316"/></g>'
            + sun(256, 392, 30, t["sun"], 13) + "</svg>")


def sheet(font, t, name):
    sw = [("Primary", t["ink"]), ("Fill", t["fill"]), ("Sun", t["sun"]), ("Text", t["text"]),
          ("Muted", t["muted"]), ("Surface", t["surface"]), ("Background", t["bg"])]
    chips = "".join(
        f'<rect x="{60 + i * 130}" y="560" width="110" height="80" rx="14" fill="{c}" stroke="{t["muted"]}" stroke-opacity="0.35"/>'
        f'{text_paths(font, label, 20, 60 + i * 130, 668, t["text"], "left")}'
        f'{text_paths(font, c, 15, 60 + i * 130, 692, t["muted"], "left")}'
        for i, (label, c) in enumerate(sw))
    wm = wordmark(font, t, bg=False).split(">", 1)[1].rsplit("</svg>", 1)[0]
    ic = icon(t).split(">", 1)[1].rsplit("</svg>", 1)[0]
    title = f"Yashome brand - {name}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="760" viewBox="0 0 1000 760">'
            f'<rect width="1000" height="760" fill="{t["bg"]}"/>'
            f'{text_paths(font, title, 30, 60, 70, t["text"], "left")}'
            f'{text_paths(font, "Yet Another Smart Home  |  Nunito ExtraBold (OFL)", 18, 60, 102, t["muted"], "left")}'
            f'<g transform="translate(-10 110) scale(0.8)">{wm}</g>'
            f'<g transform="translate(730 150) scale(0.42)">{ic}</g>'
            f'<g transform="translate(870 390) scale(0.14)">{ic}</g>'
            f'<g transform="translate(730 390) scale(0.0625)">{ic}</g>'
            f'{text_paths(font, "Palette", 22, 60, 530, t["text"], "left")}{chips}</svg>')


def render_png(svg: str, path: pathlib.Path, width: int, height: int):
    import skia
    dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream(svg.encode("utf-8")))
    surf = skia.Surface(width, height)
    # setContainerSize does not scale the viewBox: draw at viewBox size, scaled onto the surface
    m = re.search(r'viewBox="([-\d.]+)[ ,]+([-\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)"', svg)
    vw, vh = (float(m.group(3)), float(m.group(4))) if m else (width, height)
    canvas = surf.getCanvas()
    canvas.scale(width / vw, height / vh)
    dom.setContainerSize(skia.Size(vw, vh))
    dom.render(canvas)
    surf.makeImageSnapshot().save(str(path))


def main():
    font = font_extrabold()
    for name, t in THEMES.items():
        (HERE / f"yash-wordmark-{name}.svg").write_text(wordmark(font, t), encoding="utf-8")
        (HERE / f"yash-wordmark-{name}-transparent.svg").write_text(wordmark(font, t, bg=False), encoding="utf-8")
        (HERE / f"yash-icon-{name}.svg").write_text(icon(t), encoding="utf-8")
        (HERE / f"yash-icon-{name}-transparent.svg").write_text(icon(t, tile=False), encoding="utf-8")
        (HERE / f"yash-sheet-{name}.svg").write_text(sheet(font, t, name), encoding="utf-8")
    # the dashboard header: white mark with the sun, on whatever the presence pill paints behind it
    (HERE / "yash-wordmark-header.svg").write_text(wordmark(font, THEMES["dark"], bg=False, ink="#FFFFFF"), encoding="utf-8")
    # PWA icons: the light tile (teal, white mark) reads on every launcher; maskable = square, full bleed
    light = THEMES["light"]
    render_png(icon(light), ROOT / "icon-512.png", 512, 512)
    render_png(icon(light), ROOT / "icon-192.png", 192, 192)
    render_png(icon(light, rounded=False), ROOT / "icon-maskable-512.png", 512, 512)
    render_png(icon(light), ROOT / "favicon-32.png", 32, 32)
    (ROOT / "docs" / "img").mkdir(parents=True, exist_ok=True)
    import skia
    top, bottom = skia.Surface(1000, 760), skia.Surface(1000, 760)
    for surf, name in ((top, "light"), (bottom, "dark")):
        dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream(sheet(font, THEMES[name], name).encode()))
        dom.setContainerSize(skia.Size(1000, 760)); dom.render(surf.getCanvas())
    both = skia.Surface(1000, 1520); c = both.getCanvas()
    c.drawImage(top.makeImageSnapshot(), 0, 0); c.drawImage(bottom.makeImageSnapshot(), 0, 760)
    both.makeImageSnapshot().save(str(ROOT / "docs" / "img" / "brand.png"))
    print("brand kit written:", ", ".join(sorted(p.name for p in HERE.glob("*.svg"))))


if __name__ == "__main__":
    main()
