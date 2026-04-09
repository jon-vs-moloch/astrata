"""Bounded local-model pair evaluation.

This is one arena implementation over the broader Astrata eval substrate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrata.eval.ratings import RatingStore
from astrata.eval.substrate import build_eval_domain
from astrata.local.lmstudio import LmStudioCli, LmStudioGeneration
from astrata.local.telemetry import LocalModelTelemetryStore
from astrata.providers.base import CompletionRequest, Message, Provider


@dataclass(frozen=True)
class LocalModelArenaResult:
    task_class: str
    left: LmStudioGeneration
    right: LmStudioGeneration
    left_score: float
    rationale: str
    judge_provider: str


class LocalModelArena:
    def __init__(
        self,
        *,
        lmstudio: LmStudioCli,
        telemetry: LocalModelTelemetryStore,
        ratings: RatingStore,
    ) -> None:
        self._lmstudio = lmstudio
        self._telemetry = telemetry
        self._ratings = ratings

    def run_pair_eval(
        self,
        *,
        task_class: str,
        prompt: str,
        left_model_key: str,
        right_model_key: str,
        judge: Provider,
        judge_metadata: dict[str, object] | None = None,
        system_prompt: str | None = None,
        ttl_seconds: int = 300,
    ) -> LocalModelArenaResult:
        eval_domain = build_eval_domain(
            subject_kind="local_model",
            task_class=task_class,
            mutation_surface="model_profile",
            environment="local_runtime",
        )
        left = self._lmstudio.generate(
            model_key=left_model_key,
            prompt=prompt,
            system_prompt=system_prompt,
            ttl_seconds=ttl_seconds,
        )
        right = self._lmstudio.generate(
            model_key=right_model_key,
            prompt=prompt,
            system_prompt=system_prompt,
            ttl_seconds=ttl_seconds,
        )
        left_score, rationale = self._judge_pair(
            judge=judge,
            task_class=task_class,
            prompt=prompt,
            left=left,
            right=right,
            judge_metadata=judge_metadata,
        )
        right_score = 1.0 - left_score
        self._telemetry.record_observation(
            model_path=left.model_key,
            task_class=task_class,
            score=self._utility_score(left_score, left.duration_seconds),
            success=left_score > 0.5,
            source="arena",
            note=rationale,
        )
        self._telemetry.record_observation(
            model_path=right.model_key,
            task_class=task_class,
            score=self._utility_score(right_score, right.duration_seconds),
            success=right_score > 0.5,
            source="arena",
            note=rationale,
        )
        self._ratings.record_matchup(
            domain=eval_domain.rating_domain,
            left_variant_id=left.model_key,
            right_variant_id=right.model_key,
            left_score=left_score,
            context={"prompt": prompt[:160], "rationale": rationale},
        )
        return LocalModelArenaResult(
            task_class=task_class,
            left=left,
            right=right,
            left_score=left_score,
            rationale=rationale,
            judge_provider=judge.name,
        )

    def _judge_pair(
        self,
        *,
        judge: Provider,
        task_class: str,
        prompt: str,
        left: LmStudioGeneration,
        right: LmStudioGeneration,
        judge_metadata: dict[str, object] | None = None,
    ) -> tuple[float, str]:
        request = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Judge these two model outputs for the given task. "
                        "Return strict JSON with keys left_score and rationale. "
                        "left_score must be 1.0 if left wins, 0.0 if right wins, or 0.5 for a tie. "
                        "Prefer useful output per unit time inside a harnessed agent workflow, not raw verbosity."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "task_class": task_class,
                            "prompt": prompt,
                            "left": {
                                "model": left.model_key,
                                "duration_seconds": left.duration_seconds,
                                "content": left.content,
                            },
                            "right": {
                                "model": right.model_key,
                                "duration_seconds": right.duration_seconds,
                                "content": right.content,
                            },
                        },
                        indent=2,
                    ),
                ),
            ],
            metadata=dict(judge_metadata or {}),
        )
        response = judge.complete(request)
        payload = self._parse_json_payload(response.content)
        left_score = max(0.0, min(1.0, float(payload.get("left_score", 0.5))))
        rationale = str(payload.get("rationale") or "").strip() or "No rationale supplied."
        return left_score, rationale

    def _parse_json_payload(self, content: str) -> dict[str, object]:
        stripped = content.strip()
        try:
            return json.loads(stripped)
        except Exception:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(stripped[start : end + 1])
                except Exception:
                    pass
        raise RuntimeError("Judge did not return valid JSON.")

    def _utility_score(self, win_score: float, duration_seconds: float) -> float:
        duration = max(1.0, duration_seconds)
        return float(win_score) / duration
