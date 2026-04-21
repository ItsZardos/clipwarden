"""Generate the ClipWarden tray icons.

Produces three multi-resolution .ico files under ``assets/``:

* ``icon.ico``          - normal state, monochrome white
* ``icon-disabled.ico`` - paused / disabled state, muted grey
* ``icon-alert.ico``    - transient post-detection state, saturated red

Each .ico contains 16, 32, 48, and 256 pixel entries, each rasterised at
its native resolution rather than down-sampled from the 256 px image, so
the small sizes stay sharp in the Windows tray.

The recipe is deliberately deterministic: re-running the script on the
same Pillow release produces byte-identical .ico files, which keeps the
commit diff clean. If the design changes, the icons change; nothing in
between.

Art direction (placeholder):

    a rounded shield silhouette with a horizontal rectangular slot cut
    out near the top, reminiscent of a clipboard's metal clip. See
    ``assets/README.md`` for the "replace me" notice to a future
    designer.

Usage::

    python tools/gen_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICON_SIZES: tuple[int, ...] = (16, 32, 48, 256)

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"

# Solid white reads well on the default Windows 11 translucent-dark
# tray; SmartScreen's contrast-inversion in the light-theme flyout
# shows the same silhouette in dark.
NORMAL_COLOR: tuple[int, int, int, int] = (255, 255, 255, 255)

# Paused / disabled state: muted neutral grey at full alpha. A lower
# alpha would blend into the taskbar and read as "broken" rather than
# "intentionally off".
DISABLED_COLOR: tuple[int, int, int, int] = (140, 140, 140, 255)

# Transient post-detection flash. Saturated red reads as "alert" at
# 16 px against both light and dark taskbars; dropping saturation
# muddies the signal on translucent Windows 11 tray backgrounds.
ALERT_COLOR: tuple[int, int, int, int] = (220, 38, 38, 255)

_TRANSPARENT: tuple[int, int, int, int] = (0, 0, 0, 0)


def _draw_shield(
    draw: ImageDraw.ImageDraw,
    size: int,
    color: tuple[int, int, int, int],
) -> None:
    """Fill a heater-shield silhouette into ``draw``.

    Built from a rounded rectangle (top half) fused with a triangle
    (bottom point) so the shape has rounded top corners without needing
    a bezier path. The rectangle and triangle overlap along y=0.50*s so
    no seam is visible even at 16 px.
    """
    s = size
    top_rect = (0.12 * s, 0.12 * s, 0.88 * s, 0.58 * s)
    draw.rounded_rectangle(top_rect, radius=0.12 * s, fill=color)

    triangle = [
        (0.12 * s, 0.50 * s),
        (0.88 * s, 0.50 * s),
        (0.50 * s, 0.92 * s),
    ]
    draw.polygon(triangle, fill=color)


def _cut_slot(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Cut the clipboard-style slot out of the shield.

    ImageDraw on an RGBA canvas writes pixel values directly (no alpha
    blending), so filling with fully-transparent pixels effectively
    erases the shield underneath.
    """
    s = size
    slot = (0.30 * s, 0.28 * s, 0.70 * s, 0.38 * s)
    draw.rounded_rectangle(slot, radius=0.04 * s, fill=_TRANSPARENT)


def _render(size: int, color: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), _TRANSPARENT)
    draw = ImageDraw.Draw(img)
    _draw_shield(draw, size, color)
    _cut_slot(draw, size)
    return img


def _save_ico(path: Path, color: tuple[int, int, int, int]) -> None:
    # Render each icon size at its native resolution. The largest goes
    # first because Pillow's ICO writer rejects any requested size that
    # is larger than the base image; passing the 256 px image as base
    # lets it match every other requested size in ``append_images``.
    images = sorted(
        (_render(s, color) for s in ICON_SIZES),
        key=lambda im: im.width,
        reverse=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    base, *extras = images
    base.save(
        path,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=extras,
    )


def main() -> None:
    _save_ico(ASSETS_DIR / "icon.ico", NORMAL_COLOR)
    _save_ico(ASSETS_DIR / "icon-disabled.ico", DISABLED_COLOR)
    _save_ico(ASSETS_DIR / "icon-alert.ico", ALERT_COLOR)
    print(f"wrote {ASSETS_DIR / 'icon.ico'}")
    print(f"wrote {ASSETS_DIR / 'icon-disabled.ico'}")
    print(f"wrote {ASSETS_DIR / 'icon-alert.ico'}")


if __name__ == "__main__":
    main()
