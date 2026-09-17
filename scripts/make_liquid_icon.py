"""Generate the GigaAMLiquid app icon: the sidebar brand mark on a macOS-style tile.

The mark is the three sound bars from `brandMark()` in main.swift (widths 6,
heights 30/46/24, pitch 12 in a 36-pt box), scaled onto a 1024×1024 rounded
square with a soft blue-violet gradient and a glass highlight. Output:

    assets/icon-liquid.png   1024×1024 preview
    assets/icon-liquid.icns  built with iconutil from a full iconset

Usage::

    python scripts/make_liquid_icon.py [--out assets]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
# macOS app icons leave a transparent margin around the tile.
TILE_INSET = 100
TILE_RADIUS = 186  # ≈ 22.5% of the tile, the system rounded-square proportion
BAR_WIDTHS = [30, 46, 24]  # sidebar heights; the mark is 36 pt wide (3 bars, pitch 12)
GRADIENT_TOP = (74, 96, 214)  # blue
GRADIENT_BOTTOM = (118, 60, 190)  # violet
ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def _rounded_mask(size: int, inset: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, size - inset, size - inset), radius=radius, fill=255)
    return mask


def _gradient(size: int) -> Image.Image:
    top, bottom = GRADIENT_TOP, GRADIENT_BOTTOM
    rows = [
        tuple(int(top[c] + (bottom[c] - top[c]) * y / (size - 1)) for c in range(3)) + (255,)
        for y in range(size)
    ]
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y, color in enumerate(rows):
        draw.line((0, y, size, y), fill=color)
    return image


def render(size: int = SIZE) -> Image.Image:
    tile = _gradient(size)
    # Glass highlight: a soft white ellipse across the upper half.
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).ellipse((-size * 0.1, -size * 0.55, size * 1.1, size * 0.55), fill=(255, 255, 255, 46))
    highlight = highlight.filter(ImageFilter.GaussianBlur(size * 0.04))
    tile.alpha_composite(highlight)

    # Brand mark: three bars, centred, occupying ~44% of the tile width.
    mark_scale = (size - 2 * TILE_INSET) * 0.44 / 36
    bar_width = 6 * mark_scale
    pitch = 12 * mark_scale
    mark_width = 2 * pitch + bar_width
    origin_x = (size - mark_width) / 2
    center_y = size / 2
    bars = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bars)
    for index, height_pt in enumerate(BAR_WIDTHS):
        height = height_pt * mark_scale
        x0 = origin_x + index * pitch
        draw.rounded_rectangle(
            (x0, center_y - height / 2, x0 + bar_width, center_y + height / 2),
            radius=bar_width / 2,
            fill=(255, 255, 255, 255),
        )
    shadow = bars.filter(ImageFilter.GaussianBlur(size * 0.012))
    shadow_dark = Image.new("RGBA", (size, size), (20, 16, 60, 0))
    shadow_dark.putalpha(shadow.getchannel("A").point(lambda a: int(a * 0.45)))
    tile.alpha_composite(shadow_dark, (0, int(size * 0.008)))
    tile.alpha_composite(bars)

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(tile, (0, 0), _rounded_mask(size, TILE_INSET, TILE_RADIUS))
    return icon


def build_icns(icon: Image.Image, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    preview = out_dir / "icon-liquid.png"
    icon.save(preview)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon-liquid.iconset"
        iconset.mkdir()
        for base in ICONSET_SIZES:
            if base <= 512:
                icon.resize((base, base), Image.Resampling.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
            double = base * 2
            if base >= 16 and double <= SIZE:
                icon.resize((double, double), Image.Resampling.LANCZOS).save(iconset / f"icon_{base}x{base}@2x.png")
        target = out_dir / "icon-liquid.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)
    return preview, target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--out", type=Path, default=Path("assets"))
    args = parser.parse_args(argv)
    if shutil.which("iconutil") is None:
        parser.error("iconutil not found; this script runs on macOS")
    preview, icns = build_icns(render(), args.out)
    print(f"wrote {preview} and {icns} ({icns.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
