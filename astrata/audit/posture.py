"""Durable verification/audit posture with simple annealing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class VerificationPostureStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_review(
        self,
        *,
        subject_kind: str,
        findings_count: int,
        status: str,
    ) -> dict[str, Any]:
        payload = self._load()
        bucket_key = self._bucket_key(subject_kind)
        buckets = dict(payload.get("by_subject_kind") or {})
        bucket = dict(buckets.get(bucket_key) or {})
        history = list(bucket.get("recent_reviews") or [])
        history.append(
            {
                "findings_count": max(0, int(findings_count or 0)),
                "status": str(status or "").strip().lower() or "open",
            }
        )
        history = history[-48:]
        bucket["recent_reviews"] = history
        bucket["updated_at"] = _now_marker()
        buckets[bucket_key] = bucket
        payload["by_subject_kind"] = buckets
        self._save(payload)
        return self.current_posture(subject_kind=subject_kind)

    def current_posture(self, *, subject_kind: str) -> dict[str, Any]:
        payload = self._load()
        bucket_key = self._bucket_key(subject_kind)
        bucket = dict((payload.get("by_subject_kind") or {}).get(bucket_key) or {})
        recent = list(bucket.get("recent_reviews") or [])
        sample_count = len(recent)
        flawed_count = sum(1 for item in recent if int(item.get("findings_count") or 0) > 0)
        failure_rate = flawed_count / sample_count if sample_count else 1.0
        if sample_count < 4 or failure_rate >= 0.25:
            level = "strict"
            sample_rate = 1
        elif sample_count < 12 or failure_rate >= 0.10:
            level = "elevated"
            sample_rate = 2
        else:
            level = "relaxed"
            sample_rate = 4
        return {
            "subject_kind": bucket_key,
            "level": level,
            "sample_rate": sample_rate,
            "sample_count": sample_count,
            "flawed_count": flawed_count,
            "failure_rate": round(failure_rate, 4),
        }

    def _bucket_key(self, subject_kind: str) -> str:
        normalized = str(subject_kind or "").strip().lower()
        return normalized or "unknown"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"by_subject_kind": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"by_subject_kind": {}}
        if not isinstance(payload, dict):
            return {"by_subject_kind": {}}
        payload.setdefault("by_subject_kind", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _now_marker() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
