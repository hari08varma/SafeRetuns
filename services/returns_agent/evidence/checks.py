"""Deterministic evidence checks (no model): metadata and capture time, perceptual-hash
duplicates, and editing or AI-generation indicators. Every finding is a signal for risk
scoring and human review, never a reason to reject on its own."""

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import ExifTags, Image

HASH_BITS = 64
DUPLICATE_MAX_DISTANCE = 6  # Hamming distance between 64-bit hashes counted as the same photo
EDITOR_MARKERS = (
    "photoshop",
    "lightroom",
    "gimp",
    "snapseed",
    "picsart",
    "facetune",
    "canva",
    "midjourney",
    "dall-e",
    "dall·e",
    "stable diffusion",
    "firefly",
    "imagen",
    "generative",
)
_TAG = {name: tag for tag, name in ExifTags.TAGS.items()}
_EXIF_IFD = 0x8769
_CAPTURE_SLACK = timedelta(hours=12)  # clocks and time zones on phones are not exact


def dhash(data: bytes) -> str:
    """64-bit difference hash: stable under resizing, recompression and small colour edits."""
    with Image.open(io.BytesIO(data)) as img:
        small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def read_metadata(data: bytes) -> dict[str, Any]:
    """The few metadata fields the checks use (camera make/model, capture time, software)."""
    with Image.open(io.BytesIO(data)) as img:
        exif = img.getexif()
        text_chunks = {str(k).lower(): str(v)[:200] for k, v in (img.info or {}).items()}
    sub = exif.get_ifd(_EXIF_IFD) if exif else {}
    captured = sub.get(_TAG["DateTimeOriginal"]) or exif.get(_TAG["DateTime"])
    meta: dict[str, Any] = {
        "make": _text(exif.get(_TAG["Make"])),
        "model": _text(exif.get(_TAG["Model"])),
        "software": _text(exif.get(_TAG["Software"])),
        "captured_at": _parse_exif_time(captured),
    }
    # PNG text chunks written by image generators (for example "parameters", "prompt").
    generator = next(
        (k for k in text_chunks if k in ("parameters", "prompt", "workflow", "dream")), None
    )
    if generator:
        meta["generator_chunk"] = generator
    return {k: v for k, v in meta.items() if v}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = value.decode(errors="ignore") if isinstance(value, bytes) else str(value)
    return text.strip("\x00 ").strip()[:100] or None


def _parse_exif_time(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class PriorPhoto:
    """A photo already on file: from this customer's other cases, or another customer's."""

    phash: str
    same_customer: bool


@dataclass
class CheckResult:
    phash: str
    metadata: dict[str, Any]
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"phash": self.phash, "metadata": self.metadata, "flags": self.flags}


def run_checks(
    data: bytes,
    delivered_at: datetime | None,
    prior: list[PriorPhoto],
    batch_hashes: list[str] | None = None,
) -> CheckResult:
    phash = dhash(data)
    meta = read_metadata(data)
    flags: list[str] = []
    if not meta.get("captured_at") and not meta.get("make"):
        flags.append("no_camera_metadata")  # common for screenshots and messaging apps
    captured = meta.get("captured_at")
    if captured and delivered_at and datetime.fromisoformat(captured) < delivered_at - (
        _CAPTURE_SLACK
    ):
        flags.append("captured_before_delivery")
    software = (meta.get("software") or "").lower()
    if meta.get("generator_chunk") or any(m in software for m in EDITOR_MARKERS):
        flags.append("editing_software")
    near = [p for p in prior if hamming(p.phash, phash) <= DUPLICATE_MAX_DISTANCE]
    if any(not p.same_customer for p in near):
        flags.append("duplicate_other_customer")
    if any(p.same_customer for p in near):
        flags.append("duplicate_own_photo")
    if any(hamming(h, phash) <= DUPLICATE_MAX_DISTANCE for h in batch_hashes or []):
        flags.append("duplicate_in_upload")
    return CheckResult(phash=phash, metadata=meta, flags=flags)
