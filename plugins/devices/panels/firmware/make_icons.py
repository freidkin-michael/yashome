#!/usr/bin/env python3
"""Icons for the ESP panels, drawn with PIL at 4x and downsampled to PNG.

ac_*  AC tile buttons, white shapes on alpha, 64x64: cool = snowflake,
      heat = flame, dry = drop, fan = 3 blades, auto = A in a circle,
      low/med/max = 1/2/3 bars, on/off = power symbols.
ir_*  IR remote keys, white on alpha, 64x64, from 24-unit paths; the panel
      recolours them (image_recolor), dot = the four colour keys.
wx_*  weather, 10 kinds, 96x96 in colour.
Usage: make_icons.py [outdir]   (default: icons/ next to this script)
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

S = 4
W = (255, 255, 255, 255)


AC_N = 64 * S


def ac_p(v):
    return v * AC_N / 100


def ac_line(d, x1, y1, x2, y2, w=9):
    d.line([ac_p(x1), ac_p(y1), ac_p(x2), ac_p(y2)], fill=W, width=int(ac_p(w)))
    for x, y in ((x1, y1), (x2, y2)):
        d.ellipse([ac_p(x) - ac_p(w / 2), ac_p(y) - ac_p(w / 2), ac_p(x) + ac_p(w / 2), ac_p(y) + ac_p(w / 2)], fill=W)


def snowflake(d):
    for i in range(6):
        a = math.radians(i * 60)
        x, y = 50 + math.cos(a) * 36, 50 + math.sin(a) * 36
        ac_line(d, 50, 50, x, y, 7)
        for s in (-1, 1):
            b = a + s * math.radians(35)
            bx, by = 50 + math.cos(a) * 22, 50 + math.sin(a) * 22
            ac_line(d, bx, by, bx + math.cos(b) * 12, by + math.sin(b) * 12, 6)


def flame(d):
    pts = [(50, 8), (62, 26), (68, 44), (78, 36), (82, 58), (74, 82), (56, 92), (36, 88), (22, 72), (22, 50), (34, 30), (42, 40)]
    d.polygon([(ac_p(x), ac_p(y)) for x, y in pts], fill=W)
    inner = [(50, 52), (58, 64), (60, 76), (52, 86), (42, 82), (38, 70), (44, 60)]
    d.polygon([(ac_p(x), ac_p(y)) for x, y in inner], fill=(0, 0, 0, 0))


def drop(d):
    d.polygon([(ac_p(50), ac_p(8)), (ac_p(78), ac_p(52)), (ac_p(22), ac_p(52))], fill=W)
    d.ellipse([ac_p(22), ac_p(36), ac_p(78), ac_p(92)], fill=W)


def fan(d):
    for i in range(3):
        a = i * 120
        blade = Image.new("RGBA", (AC_N, AC_N), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blade)
        bd.ellipse([ac_p(50) - ac_p(11), ac_p(50) - ac_p(42), ac_p(50) + ac_p(11), ac_p(50) - ac_p(4)], fill=W)
        blade = blade.rotate(-a, resample=Image.BICUBIC, center=(ac_p(50), ac_p(50)))
        d._image.alpha_composite(blade)
    d.ellipse([ac_p(41), ac_p(41), ac_p(59), ac_p(59)], fill=W)
    d.ellipse([ac_p(46), ac_p(46), ac_p(54), ac_p(54)], fill=(0, 0, 0, 0))


def auto(d):
    d.ellipse([ac_p(8), ac_p(8), ac_p(92), ac_p(92)], outline=W, width=int(ac_p(7)))
    f = None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(path):
            f = ImageFont.truetype(path, int(ac_p(56)))
            break
    if f is None:
        f = ImageFont.load_default()
    d.text((ac_p(50), ac_p(52)), "A", fill=W, font=f, anchor="mm")


def wind(d, n):
    xs = {1: (50,), 2: (37, 63), 3: (25, 50, 75)}[n]
    for x in xs:
        d.rounded_rectangle([ac_p(x - 7.5), ac_p(20), ac_p(x + 7.5), ac_p(80)], radius=ac_p(6), fill=W)


def power_on(d):
    d.arc([ac_p(18), ac_p(20), ac_p(82), ac_p(84)], 300, 240, fill=W, width=int(ac_p(9)))
    d.rounded_rectangle([ac_p(45.5), ac_p(8), ac_p(54.5), ac_p(50)], radius=ac_p(4.5), fill=W)


def power_off(d):
    d.ellipse([ac_p(16), ac_p(16), ac_p(84), ac_p(84)], outline=W, width=int(ac_p(9)))


AC_KINDS = {"cool": snowflake, "heat": flame, "dry": drop, "fan": fan, "auto": auto, "on": power_on, "off": power_off,
         "low": lambda d: wind(d, 1), "med": lambda d: wind(d, 2), "max": lambda d: wind(d, 3)}


IR_N = 64 * S
U = IR_N / 24.0


def pt(x, y):
    return (x * U, y * U)


def ir_line(d, pts, w=2.2, close=False):
    ps = [pt(x, y) for x, y in pts]
    if close:
        ps.append(ps[0])
    d.line(ps, fill=W, width=int(w * U), joint="curve")
    r = w * U / 2
    for x, y in (ps if not close else ps[:-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=W)


def arc(d, cx, cy, r, a0, a1, w=2.2):
    d.arc([pt(cx - r, cy - r), pt(cx + r, cy + r)], a0, a1, fill=W, width=int(w * U))


def poly(d, pts):
    d.polygon([pt(x, y) for x, y in pts], fill=W)


def circle(d, cx, cy, r, fill=False, w=2.2):
    box = [pt(cx - r, cy - r), pt(cx + r, cy + r)]
    if fill:
        d.ellipse(box, fill=W)
    else:
        d.ellipse(box, outline=W, width=int(w * U))


def ir_font(px):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, int(px * U))
    return ImageFont.load_default()


def speaker(d):
    poly(d, [(4, 10), (4, 14), (7, 14), (11, 17), (11, 7), (7, 10)])


def vol_up(d):
    speaker(d); arc(d, 15, 12, 3.5, -46, 46); arc(d, 17.5, 12, 7, -46, 46)


def vol_down(d):
    speaker(d); arc(d, 15, 12, 3.5, -46, 46)


def mute(d):
    speaker(d); ir_line(d, [(15, 9), (21, 15)]); ir_line(d, [(21, 9), (15, 15)])


def chevron(d, up):
    ir_line(d, [(6, 15), (12, 9), (18, 15)] if up else [(6, 9), (12, 15), (18, 9)])


def arrow(d, dx, dy):
    ir_line(d, [(12 - 7 * dx, 12 - 7 * dy), (12 + 7 * dx, 12 + 7 * dy)])
    tip = (12 + 7 * dx, 12 + 7 * dy)
    ir_line(d, [(tip[0] - 7 * dx - 7 * dy, tip[1] - 7 * dy + 7 * dx), tip, (tip[0] - 7 * dx + 7 * dy, tip[1] - 7 * dy - 7 * dx)])


def ok(d):
    circle(d, 12, 12, 8.5); circle(d, 12, 12, 3, fill=True)


def back(d):
    ir_line(d, [(9, 14), (4, 9), (9, 4)]); ir_line(d, [(4, 9), (13, 9)]); arc(d, 13, 15, 6, -90, 90); ir_line(d, [(13, 21), (8, 21)])


def home(d):
    ir_line(d, [(3, 11), (12, 3), (21, 11)]); ir_line(d, [(5, 10), (5, 20), (10, 20), (10, 14), (14, 14), (14, 20), (19, 20), (19, 10)])


def menu(d):
    for y in (7, 12, 17):
        ir_line(d, [(4, y), (20, y)])


def info(d):
    circle(d, 12, 12, 9); ir_line(d, [(12, 11), (12, 17)]); circle(d, 12, 7.6, 1.3, fill=True)


def exit_(d):
    ir_line(d, [(14, 4), (20, 4), (20, 20), (14, 20)]); ir_line(d, [(10, 8), (6, 12), (10, 16)]); ir_line(d, [(6, 12), (16, 12)])


def source(d):
    ir_line(d, [(8, 3), (8, 8)]); ir_line(d, [(16, 3), (16, 8)]); ir_line(d, [(5, 8), (19, 8), (19, 11)]); ir_line(d, [(5, 8), (5, 11)])
    arc(d, 12, 11, 7, 0, 180); ir_line(d, [(12, 18), (12, 21)])


def hdmi(d):
    ir_line(d, [(2, 9), (22, 9), (22, 14), (20, 16), (4, 16), (2, 14)], close=True); ir_line(d, [(6, 12), (18, 12)])


def hdmi_n(n):
    def f(d):
        ir_line(d, [(2, 5), (22, 5), (22, 9.5), (20, 11.5), (4, 11.5), (2, 9.5)], close=True, w=2.0)
        d.text(pt(12, 18.5), str(n), fill=W, font=ir_font(9), anchor="mm")
    return f


def play(d):
    poly(d, [(7, 4), (20, 12), (7, 20)])


def pause(d):
    poly(d, [(6, 4), (10, 4), (10, 20), (6, 20)]); poly(d, [(14, 4), (18, 4), (18, 20), (14, 20)])


def stop(d):
    poly(d, [(5, 5), (19, 5), (19, 19), (5, 19)])


def prev_(d):
    ir_line(d, [(6, 5), (6, 19)]); poly(d, [(20, 5), (8, 12), (20, 19)])


def next_(d):
    ir_line(d, [(18, 5), (18, 19)]); poly(d, [(4, 5), (16, 12), (4, 19)])


def rew(d):
    poly(d, [(12, 5), (2, 12), (12, 19)]); poly(d, [(22, 5), (12, 12), (22, 19)])


def ff(d):
    poly(d, [(2, 5), (12, 12), (2, 19)]); poly(d, [(12, 5), (22, 12), (12, 19)])


def delete(d):
    ir_line(d, [(8, 5), (21, 5), (21, 19), (8, 19), (3, 12)], close=True); ir_line(d, [(11, 9), (17, 15)]); ir_line(d, [(17, 9), (11, 15)])


def tools(d):
    ir_line(d, [(5, 19), (13, 11)], w=3.2); circle(d, 16.5, 7.5, 4.3, w=2.6)
    d.polygon([pt(16.5, 7.5), pt(24, 1), pt(24, 8)], fill=(0, 0, 0, 0))


def guide(d):
    ir_line(d, [(4, 5), (20, 5), (20, 19), (4, 19)], close=True); ir_line(d, [(4, 10), (20, 10)]); ir_line(d, [(9, 10), (9, 19)])


def dot(d):
    circle(d, 12, 12, 7, fill=True)


IR_KINDS = {"vol_up": vol_up, "vol_down": vol_down, "mute": mute,
         "ch_up": lambda d: chevron(d, True), "ch_down": lambda d: chevron(d, False),
         "up": lambda d: arrow(d, 0, -1), "down": lambda d: arrow(d, 0, 1), "left": lambda d: arrow(d, -1, 0), "right": lambda d: arrow(d, 1, 0),
         "ok": ok, "back": back, "home": home, "menu": menu, "info": info, "exit": exit_, "source": source, "hdmi": hdmi,
         "hdmi1": hdmi_n(1), "hdmi2": hdmi_n(2), "hdmi3": hdmi_n(3), "hdmi4": hdmi_n(4),
         "play": play, "pause": pause, "stop": stop, "prev": prev_, "next": next_, "rew": rew, "ff": ff,
         "del": delete, "tools": tools, "guide": guide, "dot": dot}


WX_N = 96 * S
SUN, MOON, CLOUD_L, CLOUD_M, CLOUD_D, DROP, FLAKE, BOLT = (
    (255, 211, 92, 255), (242, 233, 200, 255), (220, 227, 236, 255), (185, 195, 208, 255),
    (111, 122, 138, 255), (111, 181, 255, 255), (232, 241, 255, 255), (255, 224, 102, 255))


def wx_p(v):
    return v * WX_N / 100


def sun(d, cx, cy, r, rays=True):
    if rays:
        for i in range(8):
            a = i * math.pi / 4
            x1, y1 = cx + math.cos(a) * (r + 5), cy + math.sin(a) * (r + 5)
            x2, y2 = cx + math.cos(a) * (r + 12), cy + math.sin(a) * (r + 12)
            d.line([wx_p(x1), wx_p(y1), wx_p(x2), wx_p(y2)], fill=SUN, width=int(wx_p(4)))
            d.ellipse([wx_p(x1) - wx_p(2), wx_p(y1) - wx_p(2), wx_p(x1) + wx_p(2), wx_p(y1) + wx_p(2)], fill=SUN)
            d.ellipse([wx_p(x2) - wx_p(2), wx_p(y2) - wx_p(2), wx_p(x2) + wx_p(2), wx_p(y2) + wx_p(2)], fill=SUN)
    d.ellipse([wx_p(cx - r), wx_p(cy - r), wx_p(cx + r), wx_p(cy + r)], fill=SUN)


def moon(d, cx, cy, r):
    pts = []
    for i in range(0, 181, 4):
        a = math.radians(90 + i)
        pts.append((wx_p(cx + math.cos(a) * r), wx_p(cy - math.sin(a) * r)))
    ri, ox = r * 1.25, r * 0.6
    for i in range(180, -1, -4):
        a = math.radians(90 + i)
        pts.append((wx_p(cx + ox + math.cos(a) * ri), wx_p(cy - math.sin(a) * ri)))
    d.polygon(pts, fill=MOON)
    for sx, sy, sr in ((cx - r - 6, cy - r, 2.2), (cx + r + 4, cy - r - 6, 1.8), (cx + r + 9, cy + 4, 1.4)):
        d.ellipse([wx_p(sx - sr), wx_p(sy - sr), wx_p(sx + sr), wx_p(sy + sr)], fill=MOON)


def cloud(d, col, dx=0, dy=0, k=1.0):
    def e(x, y, r):
        d.ellipse([wx_p(dx + (x - r) * k), wx_p(dy + (y - r) * k), wx_p(dx + (x + r) * k), wx_p(dy + (y + r) * k)], fill=col)
    e(30, 60, 12); e(46, 50, 17); e(66, 56, 13); e(74, 62, 10)
    d.rounded_rectangle([wx_p(dx + 18 * k), wx_p(dy + 56 * k), wx_p(dx + 84 * k), wx_p(dy + 72 * k)], radius=wx_p(8 * k), fill=col)


def drops(d, n, col, w, length):
    for i in range(n):
        x = 32 + i * (40 / max(1, n - 1))
        d.line([wx_p(x), wx_p(76), wx_p(x - 3), wx_p(76 + length)], fill=col, width=int(wx_p(w)))
        for (px, py) in ((x, 76), (x - 3, 76 + length)):
            d.ellipse([wx_p(px) - wx_p(w / 2), wx_p(py) - wx_p(w / 2), wx_p(px) + wx_p(w / 2), wx_p(py) + wx_p(w / 2)], fill=col)


def flakes(d, n):
    for i in range(n):
        x, y = 33 + i * (36 / max(1, n - 1)), 80 + (i % 2) * 7
        for a in (0, 60, 120):
            r = math.radians(a)
            d.line([wx_p(x - math.cos(r) * 4), wx_p(y - math.sin(r) * 4), wx_p(x + math.cos(r) * 4), wx_p(y + math.sin(r) * 4)],
                   fill=FLAKE, width=int(wx_p(1.8)))


def bolt(d):
    d.polygon([(wx_p(52), wx_p(58)), (wx_p(40), wx_p(80)), (wx_p(50), wx_p(80)), (wx_p(47), wx_p(96)), (wx_p(61), wx_p(74)), (wx_p(52), wx_p(74)), (wx_p(58), wx_p(58))], fill=BOLT)


def fog(d):
    for i in range(3):
        y = 80 + i * 7
        d.line([wx_p(24), wx_p(y), wx_p(76), wx_p(y)], fill=CLOUD_L, width=int(wx_p(3.5)))


def wx_draw(kind):
    im = Image.new("RGBA", (WX_N, WX_N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    sky = {
        "sun": lambda: sun(d, 50, 50, 20),
        "moon": lambda: moon(d, 52, 50, 20),
        "psun": lambda: (sun(d, 34, 34, 14), cloud(d, CLOUD_L)),
        "pmoon": lambda: (moon(d, 36, 34, 13), cloud(d, (201, 210, 222, 255))),
        "cloud": lambda: (cloud(d, (154, 165, 181, 255), 12, -12, 0.8), cloud(d, CLOUD_L)),
        "fog": lambda: (cloud(d, CLOUD_M), fog(d)),
        "drizzle": lambda: (cloud(d, CLOUD_M), drops(d, 4, DROP, 3, 5)),
        "rain": lambda: (cloud(d, (154, 165, 181, 255)), drops(d, 4, DROP, 4, 10)),
        "snow": lambda: (cloud(d, (201, 210, 222, 255)), flakes(d, 4)),
        "storm": lambda: (cloud(d, CLOUD_D), drops(d, 3, DROP, 3.5, 8), bolt(d)),
    }
    sky[kind]()
    return im.resize((96, 96), Image.LANCZOS)


WX_KINDS = ("sun", "moon", "psun", "pmoon", "cloud", "fog", "drizzle", "rain", "snow", "storm")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    os.makedirs(out, exist_ok=True)
    for prefix, kinds, n in (("ac", AC_KINDS, AC_N), ("ir", IR_KINDS, IR_N)):
        for k, fn in kinds.items():
            im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            fn(ImageDraw.Draw(im))
            im.resize((64, 64), Image.LANCZOS).save(os.path.join(out, f"{prefix}_{k}.png"), optimize=True)
    for k in WX_KINDS:
        wx_draw(k).save(os.path.join(out, f"wx_{k}.png"), optimize=True)
    print("icons:", len(AC_KINDS) + len(IR_KINDS) + len(WX_KINDS), "->", out)


if __name__ == "__main__":
    main()
