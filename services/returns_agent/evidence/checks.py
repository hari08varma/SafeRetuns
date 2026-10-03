"""Deterministic authenticity checks (no model). Each finding is a named SIGNAL that raises
risk or lowers confidence; none of them rejects a return on its own."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from returns_agent.db.models import Evidence, Product, ReturnCase
from returns_agent.evidence.hashing import DUPLICATE_DISTANCE, closest
from returns_agent.evidence.intake import SanitisedImage

IST = ZoneInfo("Asia/Kolkata")  # phone cameras write local time without a zone
CAPTURE_TOLERANCE = timedelta(days=1)

# Signals that feed the risk score (weights in config/decision.yaml).
EVIDENCE_SIGNALS = (
    "duplicate_photo_other_customer",
    "catalogue_photo",
    "reused_own_photo",
    "photo_before_delivery",
    "ai_generated_marker",
    "edited_photo",
)


def run_checks(
    session: Session,
    case: ReturnCase,
    image: SanitisedImage,
    hashes: list[str],
    delivered_at: datetime | None,
) -> dict[str, Any]:
    """`hashes`: the photo's perceptual hash in every orientation (hashes[0] = as uploaded).
    Earlier evidence is scanned linearly; past ~10^6 photos use a BK-tree or pg extension."""
    signals: set[str] = set()
    earlier = session.execute(
        select(Evidence.phash, Evidence.sha256, ReturnCase.customer_id)
        .join(ReturnCase, ReturnCase.id == Evidence.case_id)
        .where(Evidence.case_id != case.id, Evidence.phash.is_not(None))
    ).all()
    for phash, sha256, owner in earlier:
        if sha256 == image.sha256 or closest(hashes, str(phash)) <= DUPLICATE_DISTANCE:
            same_customer = owner == case.customer_id
            signals.add("reused_own_photo" if same_customer else "duplicate_photo_other_customer")
    catalogue = session.scalars(
        select(Product.image_phashes).where(func.jsonb_array_length(Product.image_phashes) > 0)
    ).all()
    if any(closest(hashes, str(h)) <= DUPLICATE_DISTANCE for row in catalogue for h in row):
        signals.add("catalogue_photo")

    meta = image.metadata
    if meta.captured_at and delivered_at:
        delivered_local = delivered_at.astimezone(IST).replace(tzinfo=None)
        if meta.captured_at < delivered_local - CAPTURE_TOLERANCE:
            signals.add("photo_before_delivery")
    if meta.edited_with:
        signals.add("edited_photo")
    if meta.ai_marker:
        signals.add("ai_generated_marker")
    return {
        "signals": sorted(signals),
        "info": {
            # Messaging apps strip metadata, so its absence is noted, never penalised.
            "has_metadata": meta.has_exif,
            "content_credentials": meta.content_credentials,
            "captured_at": meta.captured_at.isoformat() if meta.captured_at else None,
            "software": meta.software,
            "camera": meta.camera,
        },
    }
