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


def hgradient(w: int, h: int, a: tuple[int, int, int], b: tuple[int, int, int]) -> Image.Image:
    """Horizontal a → b gradient (the wordmark treatment: black on the left, blue on the right)."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        t = x / max(1, w - 1)
        c = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
        for y in range(h):
            px[x, y] = c
    return img


def gradient_text(mask_part: Image.Image, a: tuple[int, int, int], b: tuple[int, int, int]) -> Image.Image:
    """Glyph mask filled with a horizontal gradient, transparent elsewhere."""
    fill = hgradient(mask_part.width, mask_part.height, a, b).convert("RGBA")
    fill.putalpha(mask_part)
    return fill


def glyph_icon(mask: Image.Image, size: int, glyph_ratio: float, bg: tuple[int, int, int, int]) -> Image.Image:
    """Stencil “I” with the black→blue gradient on a flat background (transparent for the favicon, white for app icons)."""
    out = Image.new("RGBA", (size, size), bg)
    glyph = mask.crop(GLYPH_I)
    target_h = round(size * glyph_ratio)
    glyph = glyph.resize((round(glyph.width * target_h / glyph.height), target_h), Image.LANCZOS)
    ink = gradient_text(glyph, GRAD_A, GRAD_B)
    out.alpha_composite(ink, ((size - ink.width) // 2, (size - ink.height) // 2))
    return out


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

    # Gradient wordmark (the "I…S" black→blue treatment) for light surfaces, and a white→blue one for dark.
    wm = crop_scaled(mask, WORDMARK, 1400)
    gradient_text(wm, GRAD_A, GRAD_B).save(BRAND / "wordmark-gradient-dark.png", optimize=True)
    gradient_text(wm, (255, 255, 255), (120, 118, 255)).save(BRAND / "wordmark-gradient-light.png", optimize=True)

    # App icon: gradient “I” on white (PWA/Tauri/iOS need an opaque square); favicon: same glyph, transparent.
    master = glyph_icon(mask, 1024, 0.62, (255, 255, 255, 255))
    master.save(PUB / "icon-1024.png", optimize=True)
    for s in (512, 192):
        master.resize((s, s), Image.LANCZOS).save(PUB / f"icon-{s}.png", optimize=True)
    glyph_icon(mask, 512, 0.46, (255, 255, 255, 255)).save(PUB / "icon-maskable-512.png", optimize=True)
    master.resize((180, 180), Image.LANCZOS).save(PUB / "apple-touch-icon.png", optimize=True)
    fav = glyph_icon(mask, 256, 0.92, (0, 0, 0, 0))
    for s in (64, 32, 16):
        fav.resize((s, s), Image.LANCZOS).save(PUB / f"favicon-{s}.png", optimize=True)
    # Header mark: transparent gradient glyph (no square), a bit larger than the favicon.
    fav.save(PUB / "brand/mark-gradient.png", optimize=True)
    # Dark header: white→blue glyph so the black end doesn't vanish on the dark background.
    g = mask.crop(GLYPH_I); g = g.resize((round(g.width * 236 / g.height), 236), Image.LANCZOS)
    m = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); ink = gradient_text(g, (255, 255, 255), (120, 118, 255)); m.alpha_composite(ink, ((256 - ink.width) // 2, 10)); m.save(PUB / "brand/mark-gradient-light.png", optimize=True)
    print("ok →", BRAND, PUB)


if __name__ == "__main__":
    main()
