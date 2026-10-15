#!/usr/bin/env python3
"""Ken Burns zoom GIFs using crop metadata or red control rings."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

OUT = Path("/home/pyl/repo/stdweb/stdweb/static/onboarding")
CROPS = OUT / "crops.json"
SIZE = (720, 450)
BG = (14, 17, 22)

STEPS = [
    ("login", "1 · Log in"),
    ("upload", "2 · Upload a FITS"),
    ("target", "3 · Name the target"),
    ("inspect", "4 · Gain, then inspect"),
    ("photometry", "5 · Filter + photometry"),
    ("results", "6 · Your magnitude"),
    ("subtraction", "7 · Subtraction"),
]

# fallback focus regions (1280xWxH screenshots) when ring detect fails
FALLBACK = {
    "login": (340, 40, 620, 320),
    "upload": (40, 0, 900, 220),
    "target": (40, 180, 620, 200),
    "inspect": (40, 180, 700, 220),
    "photometry": (40, 120, 750, 280),
    "results": (40, 80, 900, 420),
    "subtraction": (40, 80, 700, 220),
}


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def red_focus_box(im: Image.Image) -> tuple[int, int, int, int]:
    """Outline rings only — ignore large filled callouts."""
    a = np.asarray(im.convert("RGB"))
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = (r > 160) & (g < 90) & (b < 90)
    labeled, n = ndimage.label(mask)
    boxes = []
    for i in range(1, n + 1):
        ys, xs = np.where(labeled == i)
        area = len(xs)
        if area < 200 or area > 120000:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        w, h = x1 - x0, y1 - y0
        if w < 40 or h < 24:
            continue
        if w > im.width * 0.85 and h > im.height * 0.45:
            continue
        boxes.append((x0, y0, x1, y1, area))
    if not boxes:
        w, h = im.size
        return (int(w * 0.1), int(h * 0.08), int(w * 0.85), int(h * 0.45))
    # prefer medium-sized boxes (controls), merge if close
    boxes.sort(key=lambda b: -b[4])
    # take up to 2 boxes that aren't the huge top banner
    picked = boxes[:2]
    x0 = min(b[0] for b in picked)
    y0 = min(b[1] for b in picked)
    x1 = max(b[2] for b in picked)
    y1 = max(b[3] for b in picked)
    pad = 48
    w, h = im.size
    return (
        max(0, x0 - pad),
        max(0, y0 - pad),
        min(w, x1 + pad),
        min(h, y1 + pad),
    )


def focus_box(name: str, im: Image.Image) -> tuple[int, int, int, int]:
    box = red_focus_box(im)
    if box[2] - box[0] > 80 and box[3] - box[1] > 60:
        return box
    if name in FALLBACK:
        x0, y0, w, h = FALLBACK[name]
        sx = im.width / 1280
        sy = im.height / max(h, 1)
        return (
            int(x0 * sx),
            int(y0 * sy),
            int((x0 + w) * sx),
            int((y0 + h) * sy),
        )
    return box


def aspect_box(box, img_size, aspect: float):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w / h < aspect:
        nw = h * aspect
        cx = (x0 + x1) / 2
        x0, x1 = cx - nw / 2, cx + nw / 2
    else:
        nh = w / aspect
        cy = (y0 + y1) / 2
        y0, y1 = cy - nh / 2, cy + nh / 2
    W, H = img_size
    if x1 - x0 > W:
        cx = (x0 + x1) / 2
        x0, x1 = 0, W
    if y1 - y0 > H:
        cy = (y0 + y1) / 2
        y0, y1 = 0, H
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(W, x1)
    y1 = min(H, y1)
    return (x0, y0, x1, y1)


def lerp_box(a, b, t: float):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def crop_frame(im: Image.Image, box) -> Image.Image:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    if x1 <= x0 + 2 or y1 <= y0 + 2:
        frame = im.resize(SIZE, Image.Resampling.LANCZOS)
    else:
        frame = im.crop((x0, y0, x1, y1)).resize(SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", SIZE, BG)
    canvas.paste(frame, (0, 0))
    return canvas


def font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        p = Path(path)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def caption(im: Image.Image, text: str) -> Image.Image:
    im = im.copy()
    draw = ImageDraw.Draw(im)
    bar_h = 40
    draw.rectangle((0, SIZE[1] - bar_h, SIZE[0], SIZE[1]), fill=(8, 10, 14))
    draw.text((14, SIZE[1] - 32), text, font=font(17), fill=(255, 255, 255))
    return im


def ken_burns(im: Image.Image, end_box, n_zoom=16, n_hold=7):
    aspect = SIZE[0] / SIZE[1]
    W, H = im.size
    start = aspect_box((0, 0, W, H), (W, H), aspect)
    end = aspect_box(end_box, (W, H), aspect)
    frames, durs = [], []
    for i in range(n_zoom):
        t = ease(i / max(1, n_zoom - 1))
        frames.append(crop_frame(im, lerp_box(start, end, t)))
        durs.append(65)
    hold = crop_frame(im, end)
    for _ in range(n_hold):
        frames.append(hold)
        durs.append(150)
    return frames, durs


def quantize(frames):
    pal = frames[0].quantize(colors=48, method=Image.Quantize.MEDIANCUT)
    return [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]


def save_gif(frames, durs, dest: Path):
    q = quantize(frames)
    q[0].save(
        dest,
        save_all=True,
        append_images=q[1:],
        duration=durs,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main():
    teaser_frames, teaser_durs = [], []
    for name, label in STEPS:
        png = OUT / f"{name}.png"
        im = Image.open(png).convert("RGB")
        end = focus_box(name, im)
        frames, durs = ken_burns(im, end)
        save_gif(frames, durs, OUT / f"{name}.gif")
        print(f"{name}.gif end={end} {(OUT / f'{name}.gif').stat().st_size // 1024} kB")
        if name == "subtraction":
            continue
        short, sd = ken_burns(im, end, n_zoom=8, n_hold=4)
        for fr, d in zip(short, sd):
            teaser_frames.append(caption(fr, label))
            teaser_durs.append(max(55, int(d * 0.9)))
    save_gif(teaser_frames, teaser_durs, OUT / "teaser.gif")
    print(f"teaser.gif {OUT.joinpath('teaser.gif').stat().st_size // 1024} kB")


if __name__ == "__main__":
    main()
