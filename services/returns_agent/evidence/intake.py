"""Upload validation and sanitising. Every image is sniffed by its bytes (not the declared
type), size- and pixel-limited, fully decoded, and RE-ENCODED: the stored copy carries no
metadata and no appended payloads (polyglot files are neutralised). Metadata needed for the
authenticity checks is read from the original first."""

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000  # decompression-bomb guard
MAX_FILES = 5
THUMB_SIZE = (256, 256)
_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}
# Markers some generators and editors leave in metadata (signals only, never proof).
_AI_MARKERS = re.compile(
    rb"trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia|Midjourney|DALL[-\xb7 ]?E|"
    rb"Stable Diffusion|Adobe Firefly|Imagen|GPT-4o",
    re.IGNORECASE,
)
_C2PA_MARKERS = re.compile(rb"c2pa|jumb", re.IGNORECASE)
_EDITORS = ("photoshop", "lightroom", "gimp", "snapseed", "picsart", "facetune", "canva")


class EvidenceRejected(ValueError):
    """The upload is not an acceptable image; the message is safe to show the customer."""


@dataclass(frozen=True)
class Metadata:
    has_exif: bool = False
    captured_at: datetime | None = None
    software: str | None = None
    camera: str | None = None
    edited_with: str | None = None
    ai_marker: bool = False
    content_credentials: bool = False  # C2PA manifest present (not verified)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitisedImage:
    mime: str  # of the stored copy
    data: bytes  # re-encoded, metadata-free
    thumbnail: bytes
    width: int
    height: int
    sha256: str  # of the original upload (exact-duplicate check)
    metadata: Metadata
    image: Image.Image  # decoded pixels, for hashing


def sniff(data: bytes) -> str | None:
    for signature, mime in _SIGNATURES.items():
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _metadata(img: Image.Image, data: bytes) -> Metadata:
    exif = img.getexif()
    tags: dict[str, Any] = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
    sub = exif.get_ifd(ExifTags.IFD.Exif) if exif else {}
    tags |= {ExifTags.TAGS.get(k, str(k)): v for k, v in sub.items()}
    captured = None
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        value = tags.get(key)
        if isinstance(value, str):
            try:
                captured = datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
                break
            except ValueError:
                continue
    software = str(tags["Software"]).strip("\x00 ") if tags.get("Software") else None
    edited = next((e for e in _EDITORS if software and e in software.lower()), None)
    camera = " ".join(str(tags[k]).strip("\x00 ") for k in ("Make", "Model") if tags.get(k))
    keep = ("DateTimeOriginal", "DateTime", "Software", "Make", "Model", "Orientation")
    return Metadata(
        has_exif=bool(tags),
        captured_at=captured,
        software=software,
        camera=camera or None,
        edited_with=edited,
        ai_marker=bool(_AI_MARKERS.search(data)),
        content_credentials=bool(_C2PA_MARKERS.search(data)),
        raw={k: str(tags[k]).strip("\x00 ") for k in keep if k in tags},
    )


def sanitise(data: bytes) -> SanitisedImage:
    if not data:
        raise EvidenceRejected("The file is empty.")
    if len(data) > MAX_BYTES:
        raise EvidenceRejected("The file is larger than 10 MB.")
    if sniff(data) is None:
        raise EvidenceRejected("Please upload a JPEG, PNG or WebP photo.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise EvidenceRejected("The image dimensions are too large.")
            probe.verify()  # structural check before decoding
        img = Image.open(io.BytesIO(data))
        img.load()
    except EvidenceRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise EvidenceRejected("The image could not be read. Please try another photo.") from exc
    metadata = _metadata(img, data)
    rgb = img.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, "JPEG", quality=90)  # no exif, no trailing bytes
    thumb = rgb.copy()
    thumb.thumbnail(THUMB_SIZE)
    thumb_out = io.BytesIO()
    thumb.save(thumb_out, "JPEG", quality=80)
    return SanitisedImage(
        mime="image/jpeg",
        data=out.getvalue(),
        thumbnail=thumb_out.getvalue(),
        width=rgb.width,
        height=rgb.height,
        sha256=hashlib.sha256(data).hexdigest(),
        metadata=metadata,
        image=rgb,
    )
