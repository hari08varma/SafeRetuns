"""Fuse deterministic check flags and the vision assessment into the case's evidence facts.

Evidence is advisory: it sets evidence confidence (part of the gate's confidence) and adds
risk signals; it never rejects a claim. A targeted re-request asks for exactly the views the
assessment says are missing."""

from typing import Any

from returns_agent.evidence.vision import VisionAssessment

# Flags that become risk signals (the risk service owns the weights).
RISK_FLAGS = {
    "duplicate_other_customer": "evidence_reused_photo",
    "duplicate_own_photo": "evidence_reused_photo",
    "captured_before_delivery": "evidence_before_delivery",
    "editing_software": "evidence_edited",
}
MISMATCH_CONFIDENCE_CAP = 0.5


def fuse(
    files: list[dict[str, Any]],
    vision: VisionAssessment | None,
    requests_so_far: int,
    max_requests: int,
) -> dict[str, Any]:
    flags = sorted({f for file in files for f in file.get("flags", [])})
    signals = sorted({RISK_FLAGS[f] for f in flags if f in RISK_FLAGS})
    can_ask = requests_so_far < max_requests
    if not files:
        return {
            "needs_more": can_ask,
            "missing_views": ["full_item", "close_up_of_defect"],
            "confidence": 0.0,
            "flags": [],
            "risk_signals": [],
            "assessment": None,
            "file_ids": [],
        }
    if vision is None:
        # No model configured: nothing was assessed, so the evidence does not lower
        # confidence (rule-based paths count as 1.0); flags still reach risk and humans.
        confidence = 1.0
        missing: list[str] = []
    else:
        confidence = vision.confidence
        missing = list(vision.missing_views)
        if not vision.consistent_with_claim or not vision.matches_catalog_item:
            signals = sorted({*signals, "reason_evidence_mismatch"})
            confidence = min(confidence, MISMATCH_CONFIDENCE_CAP)
    return {
        "needs_more": bool(missing) and can_ask,
        "missing_views": missing,
        "confidence": round(confidence, 4),
        "flags": flags,
        "risk_signals": signals,
        "assessment": vision.model_dump() if vision else None,
        "file_ids": [f["id"] for f in files if f.get("id")],
    }
