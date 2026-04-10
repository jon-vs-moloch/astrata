"""Bounded execution-route evaluation arena.

This arena can compare across route boundaries: cloud providers, CLI lanes, and
local or persistent Strata-style runtime endpoints.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from astrata.eval.observations import EvalObservation, EvalObservationStore
from astrata.eval.ratings import RatingStore
from astrata.eval.substrate import EvalSummary, build_eval_domain
from astrata.config.settings import load_settings
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.hardware import probe_thermal_state
from astrata.local.strata_endpoint import StrataEndpointService
from astrata.memory import build_memory_augmented_request, default_memory_store_path
from astrata.providers.base import CompletionRequest, Message, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.variants.trials import TrialResult, decide_trial_winner, summarize_trials


@dataclass(frozen=True)
class ProviderRouteArenaResult:
    task_class: str
    left_variant_id: str
    right_variant_id: str
    left_score: float
    rationale: str
    judge_provider: str
    left_duration_seconds: float
    right_duration_seconds: float
    left_startup_seconds: float = 0.0
    right_startup_seconds: float = 0.0
    left_total_wall_seconds: float = 0.0
    right_total_wall_seconds: float = 0.0


@dataclass(frozen=True)
class RouteExecutionMetrics:
    content: str
    execution_seconds: float
    startup_seconds: float = 0.0
    total_wall_seconds: float = 0.0
    output_units: int = 0
    throughput_units_per_second: float = 0.0
    thermal_pressure: str | None = None
    degraded_fallback: bool = False


class ProviderRouteArena:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        observations: EvalObservationStore,
        ratings: RatingStore,
        local_runtime: LocalRuntimeManager | None = None,
        local_client: LocalRuntimeClient | None = None,
        strata_service: StrataEndpointService | None = None,
    ) -> None:
        self._settings = load_settings()
        self._registry = registry
        self._observations = observations
        self._ratings = ratings
        self._local_runtime = local_runtime or _build_local_runtime_manager()
        self._local_client = local_client or LocalRuntimeClient()
        self._strata_service = strata_service or _build_strata_service(self._local_runtime, self._local_client)

    def run_pair_eval(
        self,
        *,
        task_class: str,
        prompt: str,
        left_route: dict[str, object],
        right_route: dict[str, object],
        judge: Provider,
        judge_metadata: dict[str, object] | None = None,
        system_prompt: str | None = None,
        allow_scarce_judge: bool = False,
    ) -> ProviderRouteArenaResult:
        self._assert_judge_policy(
            left_route=left_route,
            right_route=right_route,
            judge=judge,
            judge_metadata=judge_metadata,
            allow_scarce_judge=allow_scarce_judge,
        )
        left_run = self._run_route(
            route=left_route,
            prompt=prompt,
            system_prompt=system_prompt,
        )
        right_run = self._run_route(
            route=right_route,
            prompt=prompt,
            system_prompt=system_prompt,
        )
        left_score, rationale = self._judge_pair(
            judge=judge,
            task_class=task_class,
            prompt=prompt,
            left_route=left_route,
            right_route=right_route,
            left_content=left_run.content,
            right_content=right_run.content,
            left_duration=left_run.execution_seconds,
            right_duration=right_run.execution_seconds,
            judge_metadata=judge_metadata,
        )
        right_score = 1.0 - left_score
        domain = build_eval_domain(
            subject_kind="execution_route",
            task_class=task_class,
            mutation_surface="route_choice",
            environment="mixed",
        )
        left_variant_id = _variant_id(left_route)
        right_variant_id = _variant_id(right_route)
        self._observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id=left_variant_id,
                variant_id=left_variant_id,
                task_class=task_class,
                score=self._utility_score(left_score, left_run.total_wall_seconds or left_run.execution_seconds),
                passed=left_score > 0.5,
                confidence=0.65,
                startup_seconds=left_run.startup_seconds,
                execution_seconds=left_run.execution_seconds,
                total_wall_seconds=left_run.total_wall_seconds,
                output_units=left_run.output_units,
                throughput_units_per_second=left_run.throughput_units_per_second,
                thermal_pressure=left_run.thermal_pressure,
                evidence=[
                    rationale,
                    f"startup:{left_run.startup_seconds:.3f}s",
                    f"execution:{left_run.execution_seconds:.3f}s",
                    f"total:{left_run.total_wall_seconds:.3f}s",
                ],
                metadata={"route": dict(left_route)},
            )
        )
        self._observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id=right_variant_id,
                variant_id=right_variant_id,
                task_class=task_class,
                score=self._utility_score(right_score, right_run.total_wall_seconds or right_run.execution_seconds),
                passed=right_score > 0.5,
                confidence=0.65,
                startup_seconds=right_run.startup_seconds,
                execution_seconds=right_run.execution_seconds,
                total_wall_seconds=right_run.total_wall_seconds,
                output_units=right_run.output_units,
                throughput_units_per_second=right_run.throughput_units_per_second,
                thermal_pressure=right_run.thermal_pressure,
                evidence=[
                    rationale,
                    f"startup:{right_run.startup_seconds:.3f}s",
                    f"execution:{right_run.execution_seconds:.3f}s",
                    f"total:{right_run.total_wall_seconds:.3f}s",
                ],
                metadata={"route": dict(right_route)},
            )
        )
        self._ratings.record_matchup(
            domain=domain.rating_domain,
            left_variant_id=left_variant_id,
            right_variant_id=right_variant_id,
            left_score=left_score,
            context={
                "prompt": prompt[:160],
                "rationale": rationale,
                "left_route": dict(left_route),
                "right_route": dict(right_route),
            },
        )
        return ProviderRouteArenaResult(
            task_class=task_class,
            left_variant_id=left_variant_id,
            right_variant_id=right_variant_id,
            left_score=left_score,
            rationale=rationale,
            judge_provider=judge.name,
            left_duration_seconds=left_run.execution_seconds,
            right_duration_seconds=right_run.execution_seconds,
            left_startup_seconds=left_run.startup_seconds,
            right_startup_seconds=right_run.startup_seconds,
            left_total_wall_seconds=left_run.total_wall_seconds,
            right_total_wall_seconds=right_run.total_wall_seconds,
        )

    def summarize(self, *, task_class: str) -> EvalSummary:
        domain = build_eval_domain(
            subject_kind="execution_route",
            task_class=task_class,
            mutation_surface="route_choice",
            environment="mixed",
        )
        results: list[TrialResult] = []
        for item in self._observations.list(subject_kind="execution_route", task_class=task_class):
            score = item.get("score")
            if not isinstance(score, (int, float)):
                continue
            variant_id = str(item.get("variant_id") or "")
            if not variant_id:
                continue
            results.append(
                TrialResult(
                    variant_id=variant_id,
                    subject_kind="execution_route",
                    subject_id=str(item.get("subject_id") or variant_id),
                    score=float(score),
                    passed=bool(item.get("passed")),
                    confidence=float(item.get("confidence") or 0.0),
                    evidence=[str(part) for part in (item.get("evidence") or [])],
                    metadata={
                        **dict(item.get("metadata") or {}),
                        "startup_seconds": item.get("startup_seconds"),
                        "execution_seconds": item.get("execution_seconds"),
                        "total_wall_seconds": item.get("total_wall_seconds"),
                        "output_units": item.get("output_units"),
                        "throughput_units_per_second": item.get("throughput_units_per_second"),
                        "thermal_pressure": item.get("thermal_pressure"),
                    },
                )
            )
        summaries = summarize_trials(results)
        decision = decide_trial_winner(results, min_observations=2, min_margin=0.03)
        rating_snapshot = self._ratings.get_snapshot()
        rating_leader = self._ratings.get_domain_leader(domain=domain.rating_domain, min_matches=2)
        return EvalSummary(
            domain=domain,
            summaries=summaries,
            decision=decision,
            rating_leader_variant_id=rating_leader,
            rating_snapshot=rating_snapshot,
        )

    def _assert_judge_policy(
        self,
        *,
        left_route: dict[str, object],
        right_route: dict[str, object],
        judge: Provider,
        judge_metadata: dict[str, object] | None,
        allow_scarce_judge: bool,
    ) -> None:
        judge_variant = _judge_variant_id(judge=judge, judge_metadata=judge_metadata)
        if not judge_variant:
            return
        if allow_scarce_judge or not _is_scarce_variant(judge_variant):
            return
        left_variant = _variant_id(left_route)
        right_variant = _variant_id(right_route)
        if judge_variant in {left_variant, right_variant}:
            return
        raise RuntimeError(
            f"Scarce judge route {judge_variant} is reserved; use an abundant judge or pass allow_scarce_judge."
        )

    def _run_route(
        self,
        *,
        route: dict[str, object],
        prompt: str,
        system_prompt: str | None,
    ) -> RouteExecutionMetrics:
        provider_name = str(route.get("provider") or "").strip()
        if provider_name in {"local-model", "local_model", "local"}:
            model_key = str(route.get("model") or "").strip()
            if not model_key:
                raise RuntimeError("Local-model route requires a model identifier or path.")
            thermal_state = probe_thermal_state(preference=self._settings.local_runtime.thermal_preference)
            ready = _ensure_local_route_ready(self._local_runtime, model_key=model_key)
            request = CompletionRequest(
                messages=[
                    Message(role="system", content=system_prompt or "You are a helpful Astrata evaluation worker."),
                    Message(role="user", content=prompt),
                ],
                model="local",
            )
            started = time.monotonic()
            content = self._local_client.complete(base_url=ready.base_url, request=request)
            execution = max(0.001, time.monotonic() - started)
            output_units = len(content)
            return RouteExecutionMetrics(
                content=content,
                execution_seconds=execution,
                startup_seconds=ready.startup_seconds,
                total_wall_seconds=ready.startup_seconds + execution,
                output_units=output_units,
                throughput_units_per_second=output_units / max(0.001, execution),
                thermal_pressure=thermal_state.thermal_pressure,
            )
        if provider_name in {"strata-endpoint", "strata_endpoint", "strata", "lightning"}:
            base_url = str(route.get("base_url") or self._settings.local_runtime.strata_endpoint_base_url or "").strip()
            thread_id = str(route.get("thread_id") or "").strip() or None
            allow_degraded_fallback = bool(route.get("allow_degraded_fallback"))
            started = time.monotonic()
            if base_url:
                request = CompletionRequest(
                    messages=[
                        Message(role="system", content=system_prompt or "You are a helpful Astrata evaluation worker."),
                        Message(role="user", content=prompt),
                    ],
                    model=str(route.get("model") or "").strip() or None,
                )
                content = self._local_client.complete(
                    base_url=base_url,
                    request=request,
                    thread_id=thread_id,
                    allow_degraded_fallback=allow_degraded_fallback,
                )
            else:
                reply = self._strata_service.chat(
                    content=prompt,
                    thread_id=thread_id,
                    model_id=str(route.get("model") or "").strip() or None,
                    allow_degraded_fallback=allow_degraded_fallback,
                    system_prompt=system_prompt,
                    reasoning_effort=str(route.get("reasoning_effort") or "auto"),
                )
                content = reply.content
            execution = max(0.001, time.monotonic() - started)
            output_units = len(content)
            return RouteExecutionMetrics(
                content=content,
                execution_seconds=execution,
                startup_seconds=0.0,
                total_wall_seconds=execution,
                output_units=output_units,
                throughput_units_per_second=output_units / max(0.001, execution),
                thermal_pressure=None,
                degraded_fallback=allow_degraded_fallback,
            )
        provider = self._registry.get_provider(str(route.get("provider") or ""))
        if provider is None:
            raise RuntimeError(f"Provider {route.get('provider')!r} is not configured.")
        request = build_memory_augmented_request(
            messages=[
                Message(role="system", content=system_prompt or "You are a helpful Astrata evaluation worker."),
                Message(role="user", content=prompt),
            ],
            model=str(route.get("model") or "").strip() or None,
            metadata={
                "cli_tool": route.get("cli_tool"),
            },
            memory_store_path=default_memory_store_path(data_dir=self._settings.paths.data_dir),
            memory_query=prompt,
            accessor="local",
            destination="remote",
        )
        started = time.monotonic()
        response = provider.complete(request)
        execution = max(0.001, time.monotonic() - started)
        output_units = len(response.content)
        return RouteExecutionMetrics(
            content=response.content,
            execution_seconds=execution,
            startup_seconds=0.0,
            total_wall_seconds=execution,
            output_units=output_units,
            throughput_units_per_second=output_units / max(0.001, execution),
            thermal_pressure=None,
        )

    def _judge_pair(
        self,
        *,
        judge: Provider,
        task_class: str,
        prompt: str,
        left_route: dict[str, object],
        right_route: dict[str, object],
        left_content: str,
        right_content: str,
        left_duration: float,
        right_duration: float,
        judge_metadata: dict[str, object] | None = None,
    ) -> tuple[float, str]:
        request = build_memory_augmented_request(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Judge these two route outputs for the task. "
                        "Return strict JSON with keys left_score and rationale. "
                        "left_score must be 1.0 if left wins, 0.0 if right wins, or 0.5 for a tie. "
                        "Optimize for useful output per unit time inside the Astrata harness."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "task_class": task_class,
                            "prompt": prompt,
                            "left": {
                                "route": dict(left_route),
                                "duration_seconds": left_duration,
                                "content": left_content,
                            },
                            "right": {
                                "route": dict(right_route),
                                "duration_seconds": right_duration,
                                "content": right_content,
                            },
                        },
                        indent=2,
                    ),
                ),
            ],
            metadata=dict(judge_metadata or {}),
            memory_store_path=default_memory_store_path(data_dir=self._settings.paths.data_dir),
            memory_query=prompt,
            accessor="local",
            destination="remote",
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


def _variant_id(route: dict[str, object]) -> str:
    provider = str(route.get("provider") or "").strip()
    cli_tool = str(route.get("cli_tool") or "").strip()
    model = str(route.get("model") or "").strip()
    if provider in {"local-model", "local_model", "local"}:
        return f"local:{model}" if model else "local"
    if provider in {"strata-endpoint", "strata_endpoint", "strata", "lightning"}:
        return f"strata-endpoint:{model}" if model else "strata-endpoint"
    if provider == "cli" and cli_tool:
        return f"cli:{cli_tool}:{model}" if model else f"cli:{cli_tool}"
    return f"{provider}:{model}" if model else provider


def _judge_variant_id(*, judge: Provider, judge_metadata: dict[str, object] | None) -> str:
    if judge.name == "cli":
        cli_tool = str((judge_metadata or {}).get("cli_tool") or "").strip()
        if cli_tool:
            return f"cli:{cli_tool}"
    return judge.name


def _is_scarce_variant(variant_id: str) -> bool:
    text = str(variant_id or "").strip().lower()
    return text in {"codex", "cli:codex-cli"}


def _build_local_runtime_manager() -> LocalRuntimeManager:
    settings = load_settings()
    process_controller = ManagedProcessController(
        state_path=settings.paths.data_dir / "local_runtime.json",
        log_path=settings.paths.data_dir / "local_runtime.log",
    )
    manager = LocalRuntimeManager(
        backends={"llama_cpp": LlamaCppBackend()},
        process_controller=process_controller,
    )
    manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
    return manager


def _build_strata_service(
    local_runtime: LocalRuntimeManager,
    local_client: LocalRuntimeClient,
) -> StrataEndpointService:
    settings = load_settings()
    return StrataEndpointService(
        state_path=settings.paths.data_dir / "strata_threads.json",
        runtime_manager=local_runtime,
        runtime_client=local_client,
    )


@dataclass(frozen=True)
class LocalRouteReady:
    base_url: str
    model_id: str
    startup_seconds: float


def _ensure_local_route_ready(local_runtime: LocalRuntimeManager, *, model_key: str) -> LocalRouteReady:
    settings = load_settings()
    registry = local_runtime.model_registry()
    model = registry.get(model_key)
    if model is None:
        model = registry.adopt(model_key)
    current = local_runtime.current_selection()
    health = local_runtime.health(
        config={
            "binary_path": settings.local_runtime.llama_cpp_binary,
            "host": settings.local_runtime.llama_cpp_host,
            "port": settings.local_runtime.llama_cpp_port,
        }
    ) if current is not None else None
    if (
        current is not None
        and current.backend_id == "llama_cpp"
        and current.model_id == model.model_id
        and current.endpoint
        and health is not None
        and health.ok
    ):
        base_url = current.endpoint.removesuffix("/health")
        return LocalRouteReady(base_url=base_url, model_id=model.model_id, startup_seconds=0.0)
    started = time.monotonic()
    local_runtime.start_managed(
        backend_id="llama_cpp",
        model_id=model.model_id,
        binary_path=settings.local_runtime.llama_cpp_binary,
        host=settings.local_runtime.llama_cpp_host,
        port=settings.local_runtime.llama_cpp_port,
        profile_id="quiet",
    )
    current = local_runtime.current_selection()
    if current is None or not current.endpoint:
        raise RuntimeError("Local runtime started without a usable endpoint.")
    return LocalRouteReady(
        base_url=current.endpoint.removesuffix("/health"),
        model_id=model.model_id,
        startup_seconds=max(0.0, time.monotonic() - started),
    )
