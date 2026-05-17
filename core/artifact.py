"""Standard artifact metadata definitions for JARVIS.

This module provides a shared artifact schema and helper functions for
preserving runtime, quality, and semantic evidence across the system.
"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

ARTIFACT_SCHEMA = {
    "artifact_type": str,
    "path": str,
    "created": bool,
    "verified": bool,
    "quality_score": float,
    "semantic_confidence": float,
    "runtime_validated": bool,
    "issues": list,
    "warnings": list,
    "evidence": list,
    "timestamp": str,
}


def make_artifact_record(
    artifact_type: str,
    path: str,
    created: bool,
    verified: bool,
    quality_score: float = 0.0,
    semantic_confidence: float = 0.0,
    runtime_validated: bool = False,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "path": path,
        "created": created,
        "verified": verified,
        "quality_score": float(max(0.0, min(1.0, quality_score))),
        "semantic_confidence": float(max(0.0, min(1.0, semantic_confidence))),
        "runtime_validated": runtime_validated,
        "issues": issues or [],
        "warnings": warnings or [],
        "evidence": evidence or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def is_valid_artifact_record(record: Any) -> bool:
    return isinstance(record, dict) and "path" in record and "artifact_type" in record
