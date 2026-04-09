"""Domain-scoped pairwise rating helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_RATING = 1500.0
DEFAULT_K_FACTOR = 32.0


class RatingStore:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._payload = self._load()

    def record_matchup(
        self,
        *,
        domain: str,
        left_variant_id: str,
        right_variant_id: str,
        left_score: float,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ratings = self._payload.setdefault("ratings", {})
        by_domain = ratings.setdefault("by_domain", {})
        bucket = by_domain.setdefault(domain, {})

        left_entry = dict(bucket.get(left_variant_id) or {"rating": DEFAULT_RATING, "matches": 0})
        right_entry = dict(bucket.get(right_variant_id) or {"rating": DEFAULT_RATING, "matches": 0})

        left_rating = float(left_entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
        right_rating = float(right_entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
        expected_left = 1.0 / (1.0 + (10.0 ** ((right_rating - left_rating) / 400.0)))
        expected_right = 1.0 - expected_left
        bounded_left_score = max(0.0, min(1.0, float(left_score)))
        bounded_right_score = 1.0 - bounded_left_score

        left_entry["rating"] = left_rating + DEFAULT_K_FACTOR * (bounded_left_score - expected_left)
        right_entry["rating"] = right_rating + DEFAULT_K_FACTOR * (bounded_right_score - expected_right)
        left_entry["matches"] = int(left_entry.get("matches", 0) or 0) + 1
        right_entry["matches"] = int(right_entry.get("matches", 0) or 0) + 1
        bucket[left_variant_id] = left_entry
        bucket[right_variant_id] = right_entry

        recent = self._payload.setdefault("recent_matchups", [])
        recent.append(
            {
                "domain": domain,
                "left_variant_id": left_variant_id,
                "right_variant_id": right_variant_id,
                "left_score": bounded_left_score,
                "context": dict(context or {}),
            }
        )
        if len(recent) > 200:
            del recent[:-200]
        self._store()
        return {
            "left": dict(left_entry),
            "right": dict(right_entry),
            "domain": domain,
        }

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "ratings": json.loads(json.dumps(self._payload.get("ratings", {}))),
            "recent_matchups": list(self._payload.get("recent_matchups", [])),
        }

    def get_domain_leader(self, *, domain: str, min_matches: int = 2) -> str | None:
        bucket = (
            self._payload.get("ratings", {})
            .get("by_domain", {})
            .get(domain, {})
        )
        best_variant_id = None
        best_rating = None
        for variant_id, entry in bucket.items():
            if int(entry.get("matches", 0) or 0) < min_matches:
                continue
            rating = float(entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
            if best_rating is None or rating > best_rating:
                best_variant_id = str(variant_id)
                best_rating = rating
        return best_variant_id

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"ratings": {"by_domain": {}}, "recent_matchups": []}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"ratings": {"by_domain": {}}, "recent_matchups": []}
        if not isinstance(payload, dict):
            return {"ratings": {"by_domain": {}}, "recent_matchups": []}
        payload.setdefault("ratings", {"by_domain": {}})
        payload.setdefault("recent_matchups", [])
        return payload

    def _store(self) -> None:
        self.state_path.write_text(json.dumps(self._payload, indent=2), encoding="utf-8")
