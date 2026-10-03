"""Perceptual hashing (difference hash). Robust to resizing, re-compression and small edits;
rotations and mirroring are covered by hashing every orientation."""

from PIL import Image, ImageOps

HASH_BITS = 64
DUPLICATE_DISTANCE = 10  # Hamming distance at or below which two photos are the same picture


def dhash(img: Image.Image) -> str:
    gray = ImageOps.grayscale(img).resize((9, 8), Image.Resampling.LANCZOS)
    px = gray.tobytes()  # one byte per pixel in mode L
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def orientation_hashes(img: Image.Image) -> list[str]:
    """The hash of the photo in all 8 orientations (4 rotations x mirror)."""
    variants = []
    for mirrored in (img, ImageOps.mirror(img)):
        for angle in (0, 90, 180, 270):
            variants.append(dhash(mirrored.rotate(angle, expand=True)))
    return variants


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def closest(candidates: list[str], known: str) -> int:
    return min(distance(c, known) for c in candidates)
