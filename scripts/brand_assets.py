"""Generate app brand assets from the source logo (brand/source/instilens-black-on-white.png).

Outputs (frontend/public):
  brand/wordmark-{dark,light}.png   INSTILENS wordmark only, transparent (dark = black glyphs for light theme)
  brand/lockup-{dark,light}.png     wordmark + tagline + "ISTANBUL - ESTD 2027"
  brand/tagline-{dark,light}.png    "SEE WHERE SMART MONEY MOVES."
  icon-{1024,512,192}.png           stencil "I" glyph on the blue gradient (PWA / Tauri source)
  icon-maskable-512.png             same with safe-zone padding
  apple-touch-icon.png (180), favicon-32.png, favicon-16.png

Run:  python3 scripts/brand_assets.py   (needs Pillow)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "brand/source/instilens-black-on-white.png"
PUB = ROOT / "frontend/public"
BRAND = PUB / "brand"

# Measured on the 2000x2000 source: vertical bands and the first "I" glyph.
WORDMARK = (261, 972, 1739, 1125)
TAGLINE = (261, 1151, 1739, 1186)
LOCKUP = (261, 972, 1739, 1267)
GLYPH_I = (261, 972, 401, 1125)

# Gradient of the dark variant (sampled from instilens-white-on-blue.png).
GRAD_A = (0, 0, 0)
GRAD_B = (53, 51, 205)


def alpha_mask() -> Image.Image:
    """Black-on-white → 8-bit coverage mask (255 = ink)."""
    return ImageOps.invert(Image.open(SRC).convert("L"))


def colorize(mask: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    out = Image.new("RGBA", mask.size, rgb + (0,))
    out.putalpha(mask)
    return out


def crop_scaled(mask: Image.Image, box: tuple[int, int, int, int], width: int) -> Image.Image:
    part = mask.crop(box)
    h = round(part.height * width / part.width)
    return part.resize((width, h), Image.LANCZOS)


def gradient(size: int) -> Image.Image:
    """Diagonal black → blue gradient, top-left to bottom-right."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    n = 2 * (size - 1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / n
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(GRAD_A, GRAD_B))
    return img


def icon(mask: Image.Image, size: int, glyph_ratio: float) -> Image.Image:
    bg = gradient(size).convert("RGBA")
    glyph = mask.crop(GLYPH_I)
    target_h = round(size * glyph_ratio)
    scale = target_h / glyph.height
    glyph = glyph.resize((round(glyph.width * scale), target_h), Image.LANCZOS)
    ink = colorize(glyph, (255, 255, 255))
    bg.alpha_composite(ink, ((size - ink.width) // 2, (size - ink.height) // 2))
    return bg


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    mask = ImageOps.invert(Image.open(SRC).convert("L"))

    for name, box, width in (("wordmark", WORDMARK, 1400), ("lockup", LOCKUP, 1400), ("tagline", TAGLINE, 1000)):
        part = crop_scaled(mask, box, width)
        colorize(part, (0, 0, 0)).save(BRAND / f"{name}-dark.png", optimize=True)
        colorize(part, (255, 255, 255)).save(BRAND / f"{name}-light.png", optimize=True)

    master = icon(mask, 1024, 0.56)
    master.save(PUB / "icon-1024.png", optimize=True)
    for s in (512, 192):
        master.resize((s, s), Image.LANCZOS).save(PUB / f"icon-{s}.png", optimize=True)
    icon(mask, 512, 0.42).save(PUB / "icon-maskable-512.png", optimize=True)
    master.resize((180, 180), Image.LANCZOS).save(PUB / "apple-touch-icon.png", optimize=True)
    for s in (32, 16):
        icon(mask, 256, 0.64).resize((s, s), Image.LANCZOS).save(PUB / f"favicon-{s}.png", optimize=True)
    print("ok →", BRAND, PUB)


if __name__ == "__main__":
    main()
