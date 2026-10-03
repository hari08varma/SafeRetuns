"""Evidence upload pipeline: validate and sanitise every file, run the deterministic checks,
store the sanitised copy and a thumbnail, record `evidence` rows, then (when a model is
configured) run the vision assessment over the case's photos."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import Evidence, ReturnCase
from returns_agent.evidence.checks import run_checks
from returns_agent.evidence.hashing import orientation_hashes
from returns_agent.evidence.intake import MAX_FILES, EvidenceRejected, sanitise
from returns_agent.evidence.store import EvidenceStore
from returns_agent.evidence.vision import MAX_IMAGES, assess
from returns_agent.llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Upload:
    filename: str
    data: bytes


def ingest(
    session: Session,
    store: EvidenceStore,
    case: ReturnCase,
    uploads: list[Upload],
    delivered_at: datetime | None,
) -> tuple[list[Evidence], list[dict[str, Any]]]:
    """All-or-nothing: one unreadable file rejects the whole upload before anything is kept."""
    if not 1 <= len(uploads) <= MAX_FILES:
        raise EvidenceRejected(f"Please upload between 1 and {MAX_FILES} photos.")
    images = [sanitise(u.data) for u in uploads]
    rows, checks = [], []
    for image in images:
        hashes = orientation_hashes(image.image)
        result = run_checks(session, case, image, hashes, delivered_at)
        key = f"{case.id}/{uuid.uuid4().hex}"
        row = Evidence(
            case_id=case.id,
            uri=store.put(f"{key}.jpg", image.data),
            thumb_uri=store.put(f"{key}.thumb.jpg", image.thumbnail),
            mime=image.mime,
            sha256=image.sha256,
            phash=hashes[0],
            exif=image.metadata.raw,
            checks=result,
        )
        session.add(row)
        rows.append(row)
        checks.append(result)
    session.flush()
    return rows, checks


def vision_assessment(
    session: Session,
    store: EvidenceStore,
    llm: LLMClient,
    case_id: uuid.UUID,
    facts: dict[str, Any],
    new_rows: list[Evidence],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Assesses the newest photos of the case. A failed or invalid answer means no vision
    result (lower confidence, so the case goes to a person) — never a crash."""
    rows = session.scalars(
        select(Evidence).where(Evidence.case_id == case_id).order_by(Evidence.created_at.desc())
    ).all()
    chosen = (new_rows + [r for r in rows if r not in new_rows])[:MAX_IMAGES]
    try:
        result, refs = assess(llm, facts, [store.get(r.uri) for r in chosen])
    except Exception:
        logger.warning("vision assessment unavailable for case %s", case_id, exc_info=True)
        return None, []
    for row in new_rows:
        row.assessment = result.model_dump()
    return result.model_dump(), refs
