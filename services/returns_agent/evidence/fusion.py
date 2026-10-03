"""Fusion: deterministic signals + the vision result -> evidence confidence, risk signals and
whether to ask for specific extra photos. Evidence is advisory: it moves confidence and
risk (and so the route to a human), never the eligibility decision."""

from typing import Any

from pydantic import BaseModel

from returns_agent.evidence.checks import EVIDENCE_SIGNALS

MAX_EVIDENCE_REQUESTS = 2
NO_VISION_CONFIDENCE = 0.8  # clean photos, unassessed: enough for low-value automation only
FLAG_PENALTY = 0.2  # confidence lost per distinct authenticity signal


class EvidenceResult(BaseModel):
    confidence: float
    signals: list[str]  # risk signals (named in config/decision.yaml)
    needs_more: bool
    missing_views: list[str]
    condition_grade: str | None
    consistent_with_claim: bool | None
    vision_used: bool


def fuse(
    checks: list[dict[str, Any]], vision: dict[str, Any] | None, evidence_requests: int
) -> EvidenceResult:
    signals = sorted({s for c in checks for s in c.get("signals", []) if s in EVIDENCE_SIGNALS})
    missing: list[str] = []
    grade = consistent = None
    if vision:
        confidence = float(vision["confidence"])
        consistent = vision.get("consistent_with_claim")
        grade = vision.get("condition_grade")
        if consistent is False:
            signals.append("reason_evidence_mismatch")
            confidence = min(confidence, 0.5)
        if vision.get("matches_catalog_item") is False:
            signals.append("item_mismatch")
            confidence = min(confidence, 0.5)
        missing = list(vision.get("missing_views") or [])
    else:
        confidence = NO_VISION_CONFIDENCE
    confidence = max(0.0, confidence - FLAG_PENALTY * len(set(signals) & set(EVIDENCE_SIGNALS)))
    # Ask for exactly the missing photos, at most twice; then decide with what we have.
    needs_more = bool(missing) and evidence_requests < MAX_EVIDENCE_REQUESTS
    return EvidenceResult(
        confidence=round(confidence, 4),
        signals=sorted(set(signals)),
        needs_more=needs_more,
        missing_views=missing if needs_more else [],
        condition_grade=grade,
        consistent_with_claim=consistent,
        vision_used=vision is not None,
    )
