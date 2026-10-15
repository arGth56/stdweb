#!/usr/bin/env python3
"""Regenerate inspect.png / inspect.gif with an explicit "click here" arrow on the
"Run initial inspection" button. Sourced from the existing inspect.png screenshot.

The plain screenshot boxes the button but buries it in the top-left corner with lots
of empty space; new users could not tell where to click. This crops+zooms to the
button and draws an unmistakable arrow + label + caption.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("/home/pyl/repo/stdweb/stdweb/static/onboarding")
SRC = OUT / "inspect.png"
ORIG = OUT / "inspect_orig.png"  # untouched backup of the raw screenshot
SIZE = (720, 450)
BG = (14, 17, 22)

# "Run initial inspection" button bbox in the raw 1280x760 screenshot.
BUTTON = (82, 287, 264, 324)
# 1.6-aspect crops (match GIF canvas), no stretch.
WIDE = (0, 110, 720, 560)
TIGHT = (30, 150, 462, 420)
RED = (224, 49, 49)


def font(size: int, bold: bool = True):
    names = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    )
    for base in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/liberation/"):
        for nm in names:
            p = Path(base + nm)
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def render(im: Image.Image, box) -> Image.Image:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    frame = im.crop((x0, y0, x1, y1)).resize(SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", SIZE, BG)
    canvas.paste(frame, (0, 0))
    return canvas


def button_rect(box):
    """Map BUTTON into SIZE canvas coords for the given crop box."""
    x0, y0, x1, y1 = box
    sx = SIZE[0] / (x1 - x0)
    sy = SIZE[1] / (y1 - y0)
    bx0 = (BUTTON[0] - x0) * sx
    by0 = (BUTTON[1] - y0) * sy
    bx1 = (BUTTON[2] - x0) * sx
    by1 = (BUTTON[3] - y0) * sy
    return [bx0, by0, bx1, by1]


def draw_arrow(draw: ImageDraw.ImageDraw, start, end, width=9, color=RED):
    import math

    draw.line([start, end], fill=color, width=width)
    ang = math.atan2(end[1] - start[1], end[0] - start[0])
    L = 26
    for da in (math.radians(28), -math.radians(28)):
        hx = end[0] - L * math.cos(ang - da)
        hy = end[1] - L * math.sin(ang - da)
        draw.line([end, (hx, hy)], fill=color, width=width)


def pill(draw: ImageDraw.ImageDraw, xy, text, fnt, pad=(14, 8)):
    l, t = xy
    bb = draw.textbbox((0, 0), text, font=fnt)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    box = (l, t, l + w + 2 * pad[0], t + h + 2 * pad[1])
    draw.rounded_rectangle(box, radius=10, fill=RED)
    draw.text((l + pad[0], t + pad[1] - bb[1]), text, font=fnt, fill=(255, 255, 255))
    return box


def annotate(canvas: Image.Image, box) -> Image.Image:
    im = canvas.copy()
    d = ImageDraw.Draw(im)
    bx0, by0, bx1, by1 = button_rect(box)
    # thick ring around the button
    d.rounded_rectangle((bx0 - 7, by0 - 7, bx1 + 7, by1 + 7), radius=10, outline=RED, width=6)
    # arrow from upper-right down to the button's right edge
    tip = (bx1 + 10, (by0 + by1) / 2)
    tail = (min(SIZE[0] - 30, bx1 + 150), max(30, by0 - 90))
    draw_arrow(d, tail, tip)
    # "Click here" pill at the arrow tail
    pill(d, (tail[0] - 20, tail[1] - 46), "Click here", font(22))
    # bottom caption
    bar_h = 44
    d.rectangle((0, SIZE[1] - bar_h, SIZE[0], SIZE[1]), fill=(8, 10, 14))
    d.text((16, SIZE[1] - 34), "Press \u201cRun initial inspection\u201d",
           font=font(19), fill=(255, 255, 255))
    return im


def save_gif(frames, durs, dest: Path):
    pal = frames[-1].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(dest, save_all=True, append_images=q[1:], duration=durs,
              loop=0, optimize=True, disposal=2)


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def main():
    if not ORIG.exists():
        Image.open(SRC).convert("RGB").save(ORIG)
    raw = Image.open(ORIG).convert("RGB")

    still = annotate(render(raw, TIGHT), TIGHT)
    still.save(SRC)
    print("inspect.png (annotated) written", SRC.stat().st_size // 1024, "kB")

    frames, durs = [], []
    n_zoom = 16
    for i in range(n_zoom):
        t = ease(i / (n_zoom - 1))
        frames.append(render(raw, lerp(WIDE, TIGHT, t)))
        durs.append(70)
    held = annotate(render(raw, TIGHT), TIGHT)
    for _ in range(10):
        frames.append(held)
        durs.append(160)
    save_gif(frames, durs, OUT / "inspect.gif")
    print("inspect.gif written", (OUT / "inspect.gif").stat().st_size // 1024, "kB")


if __name__ == "__main__":
    main()
