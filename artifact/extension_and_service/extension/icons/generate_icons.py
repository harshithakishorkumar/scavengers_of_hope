"""Generate placeholder icons for the Scavengers of Hope extension.

Simple design: dark circle, white sans-serif "S" centered. Final art TBD.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

BG_COLOR  = "#1a1a1a"
FG_COLOR  = "#ffffff"
RING_COLOR = "#d55e00"     # subtle Okabe-Ito orange accent ring

SIZES = [16, 32, 48, 128]


def font_for(size):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, int(size * 0.62))
        except OSError:
            continue
    return ImageFont.load_default()


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Soft border ring (only visible at large sizes)
    if size >= 32:
        ring_w = max(1, size // 32)
        d.ellipse((0, 0, size - 1, size - 1), fill=RING_COLOR)
        inset = ring_w
        d.ellipse((inset, inset, size - 1 - inset, size - 1 - inset),
                  fill=BG_COLOR)
    else:
        d.ellipse((0, 0, size - 1, size - 1), fill=BG_COLOR)

    # "S" centered
    font = font_for(size)
    text = "S"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) / 2 - bbox[0]
    ty = (size - th) / 2 - bbox[1]
    d.text((tx, ty), text, fill=FG_COLOR, font=font)
    return img


def main():
    for s in SIZES:
        img = draw_icon(s)
        out = HERE / f"icon-{s}.png"
        img.save(out, "PNG")
        print(f"wrote {out.name}  ({s}x{s})")


if __name__ == "__main__":
    main()
