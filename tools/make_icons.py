"""Regenerates the app icons in static/icons/. Run:  python3 tools/make_icons.py   (needs Pillow)"""
import os
from PIL import Image, ImageDraw

GREEN, DARK, WHITE, LIGHT = (45, 106, 79), (27, 67, 50), (255, 255, 255), (216, 243, 220)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "icons")


def draw_icon(size):
    s = size * 4  # draw large, then shrink for smooth edges
    img = Image.new("RGB", (s, s), GREEN)
    d = ImageDraw.Draw(img)
    u = s / 100.0  # 100-unit grid; artwork stays inside the central ~60% (safe for maskable icons)
    # awning with stripes
    left, right, top, bottom = 24, 76, 30, 44
    stripes = 6
    w = (right - left) / stripes
    for i in range(stripes):
        d.rectangle([(left + i * w) * u, top * u, (left + (i + 1) * w) * u, bottom * u],
                    fill=WHITE if i % 2 == 0 else LIGHT)
        d.pieslice([(left + i * w) * u, (bottom - w / 2) * u, (left + (i + 1) * w) * u, (bottom + w / 2) * u],
                   0, 180, fill=WHITE if i % 2 == 0 else LIGHT)
    d.rectangle([(left - 2) * u, (top - 4) * u, (right + 2) * u, top * u], fill=WHITE)
    # shop body, door, window
    d.rectangle([28 * u, 49 * u, 72 * u, 72 * u], fill=WHITE)
    d.rectangle([44 * u, 56 * u, 56 * u, 72 * u], fill=GREEN)
    d.rectangle([32 * u, 56 * u, 41 * u, 66 * u], fill=LIGHT)
    d.rectangle([59 * u, 56 * u, 68 * u, 66 * u], fill=LIGHT)
    d.rectangle([22 * u, 72 * u, 78 * u, 75 * u], fill=DARK)
    return img.resize((size, size), Image.LANCZOS)


os.makedirs(OUT, exist_ok=True)
for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
    draw_icon(size).save(os.path.join(OUT, name))
    print("wrote", name)
