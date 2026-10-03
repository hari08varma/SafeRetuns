"""Synthetic evidence photos for evals and tests: distinct structured images (so perceptual
hashes differ like real photos do), with optional camera metadata and tamper markers."""

import io
import random
from datetime import datetime

from PIL import Image, ImageDraw


def make_photo(
    seed: str,
    captured_at: datetime | None = None,
    software: str | None = None,
    comment: str | None = None,
    size: tuple[int, int] = (640, 480),
) -> bytes:
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(12):
        x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
        x1, y1 = x0 + rng.randrange(40, 320), y0 + rng.randrange(40, 240)
        colour = tuple(rng.randrange(256) for _ in range(3))
        if rng.random() < 0.5:
            draw.rectangle((x0, y0, x1, y1), fill=colour)
        else:
            draw.ellipse((x0, y0, x1, y1), fill=colour)
    exif = Image.Exif()
    exif[271], exif[272] = "Xiaomi", "Redmi Note 13"  # Make, Model
    if captured_at:
        exif[306] = captured_at.strftime("%Y:%m:%d %H:%M:%S")  # DateTime
    if software:
        exif[305] = software
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, exif=exif, comment=comment or "")
    return out.getvalue()
