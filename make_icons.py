#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标：icon-192.png / icon-512.png / apple-touch-icon.png

一次性脚本，改了图标设计后重跑即可。依赖 Pillow。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BG = (44, 44, 42, 255)        # 墨色 #2C2C2A
FG = (250, 249, 247, 255)     # 米白
ACCENT = (226, 75, 74, 255)   # 红 #E24B4A

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def pick_font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def rounded_canvas(size: int, radius_ratio: float = 0.22) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    return img


def draw_icon(size: int) -> Image.Image:
    img = rounded_canvas(size)
    d = ImageDraw.Draw(img)

    font = pick_font(int(size * 0.46))
    text = "AI"
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    d.text(((size - w) / 2 - left, size * 0.44 - h / 2 - top), text,
           font=font, fill=FG)

    dot_r = max(3, int(size * 0.035))
    cx, cy = int(size * 0.70), int(size * 0.26)
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=ACCENT)
    return img


def main() -> None:
    specs = [
        ("icon-192.png", 192),
        ("icon-512.png", 512),
        ("apple-touch-icon.png", 180),
    ]
    for name, size in specs:
        img = draw_icon(size)
        if name == "apple-touch-icon.png":
            bg = Image.new("RGB", img.size, BG[:3])
            bg.paste(img, (0, 0), img)
            img = bg
        img.save(ROOT / name, optimize=True)
        print("生成", name, size)


if __name__ == "__main__":
    main()
