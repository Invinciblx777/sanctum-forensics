"""Draw the Sanctum shield icon at build time, in every format a package needs.

The mark is the same shield-and-cross the sidebar draws inline (App.tsx), in
the interface's primary text colour on its base surface. Generated rather than
committed as binaries so there is one source for it and nothing opaque in the
repository.

    python packaging/make_icons.py build/icons
    -> sanctum.png (512), sanctum.ico (Windows), sanctum.icns (macOS)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

BASE = (14, 18, 22, 255)  # --surface-base
MARK = (230, 237, 243, 255)  # --text-primary


def draw(size: int) -> Image.Image:
    scale = size / 32
    image = Image.new("RGBA", (size, size), BASE)
    pen = ImageDraw.Draw(image)
    width = max(1, round(2 * scale))
    shield = [
        (16, 4),
        (26, 8),
        (26, 17),
        (24, 22),
        (20, 26),
        (16, 28),
        (12, 26),
        (8, 22),
        (6, 17),
        (6, 8),
        (16, 4),
    ]
    pen.line(
        [(x * scale, y * scale) for x, y in shield],
        fill=MARK,
        width=width,
        joint="curve",
    )
    pen.line(
        [(11 * scale, 16 * scale), (21 * scale, 16 * scale)], fill=MARK, width=width
    )
    pen.line(
        [(16 * scale, 11 * scale), (16 * scale, 21 * scale)], fill=MARK, width=width
    )
    return image


def main(out: str) -> int:
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    big = draw(512)
    big.save(target / "sanctum.png")
    big.save(
        target / "sanctum.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    big.save(target / "sanctum.icns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "build/icons"))
