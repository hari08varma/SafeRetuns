"""Upload validation: type by content (never by name or declared type), size limits, a decode
check and a malware screen, then a thumbnail. Rejected files are reported, never stored."""

import io
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 6
MAX_PIXELS = 40_000_000  # decompression-bomb guard
THUMBNAIL_PX = 256

# Magic bytes of the accepted image types.
SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}
# Script content anywhere in the file, and executables or archives appended after the image
# data (polyglot files). Short binary markers are checked only in the trailer, since they
# can occur by chance inside compressed image data.
SCRIPT_MARKERS = (b"<script", b"<?php", b"<html")
TRAILER_MARKERS = (b"MZ", b"\x7fELF", b"PK\x03\x04", b"%PDF-", b"#!")


class UploadRejected(Exception):
    pass


class MalwareScanner(Protocol):
    def scan(self, data: bytes) -> str | None:
        """Return a finding, or None when the file is clean."""
        ...


class SignatureScanner:
    """Baseline screen for embedded executables and scripts. A full antivirus engine
    (for example ClamAV) plugs in through the same interface."""

    def scan(self, data: bytes) -> str | None:
        lowered = data.lower()
        for marker in SCRIPT_MARKERS:
            if marker in lowered:
                return "embedded script"
        trailer = data[_image_end(data) :].lstrip(b"\x00\r\n ")
        if any(trailer.startswith(m) for m in TRAILER_MARKERS):
            return "content appended after the image"
        return None


def _image_end(data: bytes) -> int:
    if data.startswith(b"\x89PNG"):
        end = data.find(b"IEND")
        return len(data) if end < 0 else end + 8  # chunk type + CRC
    end = data.rfind(b"\xff\xd9")
    return len(data) if end < 0 else end + 2


@dataclass(frozen=True)
class ValidFile:
    filename: str
    mime: str
    data: bytes
    thumbnail: bytes
    width: int
    height: int


def sniff(data: bytes) -> str | None:
    return next((mime for sig, mime in SIGNATURES.items() if data.startswith(sig)), None)


def validate(filename: str, data: bytes, scanner: MalwareScanner) -> ValidFile:
    if not data:
        raise UploadRejected("empty file")
    if len(data) > MAX_BYTES:
        raise UploadRejected(f"file larger than {MAX_BYTES // (1024 * 1024)} MB")
    mime = sniff(data)
    if mime is None:
        raise UploadRejected("only JPEG and PNG photos are accepted")
    finding = scanner.scan(data)
    if finding:
        raise UploadRejected(f"file failed the security scan: {finding}")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise UploadRejected("image dimensions too large")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            thumb = img.convert("RGB")
            thumb.thumbnail((THUMBNAIL_PX, THUMBNAIL_PX))
            out = io.BytesIO()
            thumb.save(out, format="JPEG", quality=80)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UploadRejected("the image could not be read") from exc
    return ValidFile(
        filename=filename[:200],
        mime=mime,
        data=data,
        thumbnail=out.getvalue(),
        width=width,
        height=height,
    )
