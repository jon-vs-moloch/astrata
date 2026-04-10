"""Cyclone: proposal-edit-verify route experiment.

This module benchmarks a sparse-review generation loop against naive small and
naive big baselines.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from astrata.config.settings import load_settings
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.memory import build_memory_augmented_request, default_memory_store_path
from astrata.local.models.discovery import discover_local_models
from astrata.local.models.registry import LocalModelRecord, LocalModelRegistry
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.providers.base import CompletionRequest, Message, Provider
from astrata.providers.registry import ProviderRegistry, build_default_registry


DEFAULT_TASKS: list[dict[str, str]] = [
    {
        "name": "constrained-summary",
        "task_class": "writing",
        "prompt": (
            "Summarize the value of a local-first agent runtime for a technical founder. "
            "Use exactly 3 bullet points. Each bullet must be 12 words or fewer."
        ),
    },
    {
        "name": "rewrite-with-constraints",
        "task_class": "editing",
        "prompt": (
            "Rewrite this update to be calmer and more precise in 90 words or fewer:\n"
            "\"We need to completely rebuild the local runtime soon because the current "
            "version is kind of chaotic and everything is fragile. The main issue is that "
            "model switching is too expensive, and it makes every experiment feel flaky.\""
        ),
    },
    {
        "name": "structured-design-note",
        "task_class": "reasoning",
        "prompt": (
            "Propose a lightweight experiment to test whether sparse verification improves "
            "small-model output quality. Respond with exactly four sections titled "
            "Goal, Method, Metrics, Risks."
        ),
    },
]


@dataclass(frozen=True)
class CycloneTask:
    name: str
    task_class: str
    prompt: str
    system_prompt: str | None = None


@dataclass(frozen=True)
class RouteSpec:
    provider: str
    model: str | None = None
    cli_tool: str | None = None
    label: str | None = None

    def as_route_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"provider": self.provider}
        if self.model:
            payload["model"] = self.model
        if self.cli_tool:
            payload["cli_tool"] = self.cli_tool
        return payload

    def display_name(self) -> str:
        if self.label:
            return self.label
        if self.provider == "cli" and self.cli_tool:
            return f"cli:{self.cli_tool}:{self.model}" if self.model else f"cli:{self.cli_tool}"
        return f"{self.provider}:{self.model}" if self.model else self.provider


@dataclass(frozen=True)
class RouteExecution:
    route: str
    content: str
    execution_seconds: float
    startup_seconds: float = 0.0
    total_wall_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SteeringDecision:
    verdict: str
    rationale: str
    priorities: list[str]
    edit_brief: str


@dataclass(frozen=True)
class CycloneRun:
    final: RouteExecution
    draft: RouteExecution
    steering_rounds: list[dict[str, Any]]
    accepted: bool
    total_wall_seconds: float


@dataclass(frozen=True)
class CandidateScore:
    name: str
    score: float
    rationale: str


@dataclass(frozen=True)
class TaskResult:
    task: CycloneTask
    small: RouteExecution
    big: RouteExecution
    cyclone: CycloneRun
    scores: dict[str, CandidateScore]
    winner: str
    judge_rationale: str


@dataclass(frozen=True)
class AggregateSummary:
    average_score: dict[str, float]
    average_total_seconds: dict[str, float]
    wins: dict[str, int]
    cyclone_quality_vs_small: float
    cyclone_speed_vs_big_seconds: float
    cyclone_quality_gap_vs_big: float
    has_legs: bool


class CycloneExperiment:
    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        local_runtime: LocalRuntimeManager | None = None,
        local_client: LocalRuntimeClient | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._settings = load_settings()
        self._local_runtime = local_runtime or self._build_local_runtime()
        self._local_client = local_client or LocalRuntimeClient()
        self._local_models_discovered = False
        self._local_port = _pick_experiment_port(self._settings.local_runtime.llama_cpp_port)

    def run(
        self,
        *,
        tasks: list[CycloneTask],
        small_route: RouteSpec,
        big_route: RouteSpec,
        judge_route: RouteSpec,
        max_rounds: int,
        max_small_tokens: int,
        max_big_tokens: int,
    ) -> tuple[list[TaskResult], AggregateSummary]:
        resolved_small = self.resolve_route(small_route)
        resolved_big = self.resolve_route(big_route)
        resolved_judge = self.resolve_route(judge_route)
        results: list[TaskResult] = []
        for task in tasks:
            small = self._execute_prompt_route(
                resolved_small,
                prompt=task.prompt,
                system_prompt=task.system_prompt,
                max_tokens=max_small_tokens,
                temperature=0.2,
            )
            big = self._execute_prompt_route(
                resolved_big,
                prompt=task.prompt,
                system_prompt=task.system_prompt,
                max_tokens=max_big_tokens,
                temperature=0.2,
            )
            cyclone = self._run_cyclone(
                task=task,
                small_route=resolved_small,
                big_route=resolved_big,
                max_rounds=max_rounds,
                max_small_tokens=max_small_tokens,
                max_big_tokens=max_big_tokens,
            )
            scores, winner, rationale = self._judge_outputs(
                task=task,
                judge_route=resolved_judge,
                small=small,
                big=big,
                cyclone=cyclone.final,
            )
            results.append(
                TaskResult(
                    task=task,
                    small=small,
                    big=big,
                    cyclone=cyclone,
                    scores=scores,
                    winner=winner,
                    judge_rationale=rationale,
                )
            )
        return results, self._summarize(results)

    def resolve_route(self, route: RouteSpec) -> RouteSpec:
        if route.provider != "local":
            return route
        model = str(route.model or "").strip()
        if not model:
            raise ValueError("Local routes require a model selector or path.")
        if model not in {"auto-smallest", "auto-largest"}:
            return RouteSpec(provider="local", model=self._resolve_local_model_ref(model), label=route.label)
        chosen = self._pick_auto_local_model(model)
        return RouteSpec(
            provider="local",
            model=chosen.path,
            label=route.label or f"local:{chosen.display_name}",
        )

    def default_judge_route(self) -> RouteSpec:
        for name in ("codex", "google", "cli"):
            provider = self._registry.get_provider(name)
            if provider and provider.is_configured():
                if name == "cli":
                    return RouteSpec(provider="cli", cli_tool="codex-cli", label="cli:codex-cli")
                return RouteSpec(provider=name, model=provider.default_model(), label=name)
        raise RuntimeError("No configured judge provider is available.")

    def _run_cyclone(
        self,
        *,
        task: CycloneTask,
        small_route: RouteSpec,
        big_route: RouteSpec,
        max_rounds: int,
        max_small_tokens: int,
        max_big_tokens: int,
    ) -> CycloneRun:
        draft = self._execute_prompt_route(
            small_route,
            prompt=task.prompt,
            system_prompt=task.system_prompt,
            max_tokens=max_small_tokens,
            temperature=0.2,
            mode="draft",
        )
        current = draft
        accepted = False
        rounds: list[dict[str, Any]] = []
        total_wall_seconds = draft.total_wall_seconds
        for round_index in range(max(1, max_rounds)):
            steering_exec = self._execute_messages_route(
                big_route,
                messages=[
                    Message(
                        role="system",
                        content=(
                            "You are Cyclone's sparse verifier. Review the candidate answer against the task. "
                            "Return strict JSON with keys verdict, rationale, priorities, edit_brief. "
                            "verdict must be PASS or FAIL. priorities must be a short list of concrete issues. "
                            "If verdict is PASS, edit_brief should be an empty string."
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(
                            {
                                "task": task.prompt,
                                "candidate": current.content,
                            },
                            indent=2,
                        ),
                    ),
                ],
                max_tokens=max_big_tokens,
                temperature=0.0,
                mode="verify",
            )
            decision = _parse_steering_decision(steering_exec.content)
            rounds.append(
                {
                    "round": round_index + 1,
                    "steering": asdict(decision),
                    "steering_timing": {
                        "execution_seconds": steering_exec.execution_seconds,
                        "startup_seconds": steering_exec.startup_seconds,
                        "total_wall_seconds": steering_exec.total_wall_seconds,
                    },
                }
            )
            total_wall_seconds += steering_exec.total_wall_seconds
            if decision.verdict == "PASS":
                accepted = True
                break
            if round_index == max_rounds - 1:
                break
            revised = self._execute_messages_route(
                small_route,
                messages=[
                    Message(
                        role="system",
                        content=(
                            "You are Cyclone's fast editor. Revise the candidate answer using the steering. "
                            "Preserve all good material. Make the minimum edits needed. "
                            "Return only the final revised answer."
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(
                            {
                                "task": task.prompt,
                                "candidate": current.content,
                                "steering": asdict(decision),
                            },
                            indent=2,
                        ),
                    ),
                ],
                max_tokens=max_small_tokens,
                temperature=0.1,
                mode="edit",
            )
            rounds[-1]["revision_timing"] = {
                "execution_seconds": revised.execution_seconds,
                "startup_seconds": revised.startup_seconds,
                "total_wall_seconds": revised.total_wall_seconds,
            }
            total_wall_seconds += revised.total_wall_seconds
            current = revised
        return CycloneRun(
            final=current,
            draft=draft,
            steering_rounds=rounds,
            accepted=accepted,
            total_wall_seconds=total_wall_seconds,
        )

    def _judge_outputs(
        self,
        *,
        task: CycloneTask,
        judge_route: RouteSpec,
        small: RouteExecution,
        big: RouteExecution,
        cyclone: RouteExecution,
    ) -> tuple[dict[str, CandidateScore], str, str]:
        judge_exec = self._execute_messages_route(
            judge_route,
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are evaluating three candidate answers for the same task. "
                        "Return strict JSON with keys scores, winner, rationale. "
                        "scores must be an object with keys small, big, cyclone. "
                        "Each score object must contain score and rationale. "
                        "Score each candidate from 0 to 10. Favor usefulness, correctness, "
                        "constraint satisfaction, and clarity. Do not reward verbosity."
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "task_class": task.task_class,
                            "task": task.prompt,
                            "candidates": {
                                "small": {
                                    "content": small.content,
                                    "latency_seconds": small.total_wall_seconds,
                                },
                                "big": {
                                    "content": big.content,
                                    "latency_seconds": big.total_wall_seconds,
                                },
                                "cyclone": {
                                    "content": cyclone.content,
                                    "latency_seconds": cyclone.total_wall_seconds,
                                },
                            },
                        },
                        indent=2,
                    ),
                ),
            ],
            max_tokens=400,
            temperature=0.0,
            mode="judge",
        )
        payload = _parse_json_payload(judge_exec.content)
        raw_scores = dict(payload.get("scores") or {})
        scores = {
            name: CandidateScore(
                name=name,
                score=float((raw_scores.get(name) or {}).get("score", 0.0)),
                rationale=str((raw_scores.get(name) or {}).get("rationale") or "").strip(),
            )
            for name in ("small", "big", "cyclone")
        }
        winner = str(payload.get("winner") or "").strip().lower()
        if winner not in scores:
            winner = max(scores.values(), key=lambda item: item.score).name
        rationale = str(payload.get("rationale") or "").strip() or "No overall rationale supplied."
        return scores, winner, rationale

    def _execute_prompt_route(
        self,
        route: RouteSpec,
        *,
        prompt: str,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
        mode: str | None = None,
    ) -> RouteExecution:
        messages = [
            Message(role="system", content=system_prompt or "You are a helpful Astrata evaluation worker."),
            Message(role="user", content=prompt),
        ]
        return self._execute_messages_route(
            route,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            mode=mode,
        )

    def _execute_messages_route(
        self,
        route: RouteSpec,
        *,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
        mode: str | None,
    ) -> RouteExecution:
        started = time.monotonic()
        if route.provider == "local":
            base_url, startup_seconds, model_path = self._ensure_local_ready(route.model or "")
            content = self._local_client.complete(
                base_url=base_url,
                request=CompletionRequest(
                    messages=messages,
                    model="local",
                    temperature=temperature,
                    metadata={"max_tokens": max_tokens, "cyclone_mode": mode or "default"},
                ),
            )
            execution_seconds = max(0.001, time.monotonic() - started - startup_seconds)
            total_wall_seconds = max(0.001, time.monotonic() - started)
            return RouteExecution(
                route=route.display_name(),
                content=content,
                execution_seconds=execution_seconds,
                startup_seconds=startup_seconds,
                total_wall_seconds=total_wall_seconds,
                metadata={"model_path": model_path, "mode": mode},
            )
        provider = self._provider_for_route(route)
        request = build_memory_augmented_request(
            messages=messages,
            model=route.model,
            temperature=temperature,
            metadata={"cli_tool": route.cli_tool, "cyclone_mode": mode or "default"},
            memory_store_path=default_memory_store_path(data_dir=self._settings.paths.data_dir),
            memory_query="\n".join(
                str(message.content or "")
                for message in messages
                if str(message.role or "").lower() != "system"
            ),
            accessor="local",
            destination="remote",
        )
        response = provider.complete(request)
        total_wall_seconds = max(0.001, time.monotonic() - started)
        return RouteExecution(
            route=route.display_name(),
            content=response.content,
            execution_seconds=total_wall_seconds,
            startup_seconds=0.0,
            total_wall_seconds=total_wall_seconds,
            metadata={"provider": provider.name, "mode": mode},
        )

    def _provider_for_route(self, route: RouteSpec) -> Provider:
        provider = self._registry.get_provider(route.provider)
        if provider is None:
            raise RuntimeError(f"Provider {route.provider!r} is not configured.")
        return provider

    def _ensure_local_ready(self, model_ref: str) -> tuple[str, float, str]:
        model_path = self._resolve_local_model_ref(model_ref)
        registry = self._discover_local_models()
        model = registry.find_by_path(model_path)
        if model is None:
            model = registry.adopt(model_path)
        current = self._local_runtime.current_selection()
        status = self._local_runtime.managed_status()
        if current is not None and current.model_id != model.model_id and status is not None and status.running:
            self._local_runtime.stop_managed()
        config = {
            "binary_path": self._settings.local_runtime.llama_cpp_binary,
            "host": self._settings.local_runtime.llama_cpp_host,
            "port": self._local_port,
            "model_path": model.path,
        }
        health = self._local_runtime.health(config=config) if self._local_runtime.current_selection() is not None else None
        current = self._local_runtime.current_selection()
        if (
            current is not None
            and current.model_id == model.model_id
            and current.endpoint
            and health is not None
            and health.ok
        ):
            return current.endpoint.removesuffix("/health"), 0.0, model.path
        started = time.monotonic()
        self._local_runtime.start_managed(
            backend_id="llama_cpp",
            model_id=model.model_id,
            binary_path=self._settings.local_runtime.llama_cpp_binary,
            host=self._settings.local_runtime.llama_cpp_host,
            port=self._local_port,
            profile_id="quiet",
        )
        current = self._local_runtime.current_selection()
        if current is None or not current.endpoint:
            raise RuntimeError("Local runtime started without a usable endpoint.")
        return current.endpoint.removesuffix("/health"), max(0.0, time.monotonic() - started), model.path

    def _resolve_local_model_ref(self, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("Local model reference cannot be empty.")
        path_candidate = Path(candidate).expanduser()
        if path_candidate.exists():
            return str(path_candidate)
        registry = self._discover_local_models()
        by_path = registry.find_by_path(candidate)
        if by_path is not None:
            return by_path.path
        by_id = registry.get(candidate)
        if by_id is not None:
            return by_id.path
        lowered = candidate.lower()
        display_matches = [
            model for model in registry.list_models()
            if lowered == model.display_name.lower() or lowered in model.display_name.lower()
        ]
        if len(display_matches) == 1:
            return display_matches[0].path
        raise ValueError(f"Could not resolve local model reference: {candidate}")

    def _pick_auto_local_model(self, selector: str) -> LocalModelRecord:
        registry = self._discover_local_models()
        candidates = [
            model for model in registry.list_models()
            if model.role == "model" and "support-artifact" not in model.tags
        ]
        if not candidates:
            raise RuntimeError("No local generation models were discovered.")
        safe = [model for model in candidates if "uncensored" not in model.display_name.lower()]
        if safe:
            candidates = safe
        candidates = sorted(candidates, key=lambda model: (model.size_bytes or 0, model.display_name.lower()))
        return candidates[0] if selector == "auto-smallest" else candidates[-1]

    def _discover_local_models(self) -> LocalModelRegistry:
        if self._local_models_discovered:
            return self._local_runtime.model_registry()
        discover_local_models(
            self._local_runtime.model_registry(),
            search_paths=self._settings.local_runtime.model_search_paths,
        )
        self._local_models_discovered = True
        return self._local_runtime.model_registry()

    def _build_local_runtime(self) -> LocalRuntimeManager:
        process_controller = ManagedProcessController(
            state_path=self._settings.paths.data_dir / "local_runtime.json",
            log_path=self._settings.paths.data_dir / "local_runtime.log",
        )
        return LocalRuntimeManager(
            backends={"llama_cpp": LlamaCppBackend()},
            process_controller=process_controller,
        )

    def _summarize(self, results: list[TaskResult]) -> AggregateSummary:
        if not results:
            raise ValueError("Cyclone summary requires at least one task result.")
        score_totals = {"small": 0.0, "big": 0.0, "cyclone": 0.0}
        time_totals = {"small": 0.0, "big": 0.0, "cyclone": 0.0}
        wins = {"small": 0, "big": 0, "cyclone": 0}
        for result in results:
            for name in score_totals:
                score_totals[name] += result.scores[name].score
            time_totals["small"] += result.small.total_wall_seconds
            time_totals["big"] += result.big.total_wall_seconds
            time_totals["cyclone"] += result.cyclone.total_wall_seconds
            wins[result.winner] = wins.get(result.winner, 0) + 1
        count = float(len(results))
        average_score = {name: total / count for name, total in score_totals.items()}
        average_total_seconds = {name: total / count for name, total in time_totals.items()}
        cyclone_quality_vs_small = average_score["cyclone"] - average_score["small"]
        cyclone_speed_vs_big_seconds = average_total_seconds["big"] - average_total_seconds["cyclone"]
        cyclone_quality_gap_vs_big = average_score["cyclone"] - average_score["big"]
        return AggregateSummary(
            average_score=average_score,
            average_total_seconds=average_total_seconds,
            wins=wins,
            cyclone_quality_vs_small=cyclone_quality_vs_small,
            cyclone_speed_vs_big_seconds=cyclone_speed_vs_big_seconds,
            cyclone_quality_gap_vs_big=cyclone_quality_gap_vs_big,
            has_legs=cyclone_quality_vs_small > 0.0 and cyclone_speed_vs_big_seconds > 0.0,
        )


def parse_route_spec(raw: str) -> RouteSpec:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Route spec cannot be empty.")
    parts = text.split(":")
    head = parts[0].strip().lower()
    if head == "local":
        model = ":".join(parts[1:]).strip() if len(parts) > 1 else ""
        return RouteSpec(provider="local", model=model or None)
    if head == "cli":
        if len(parts) < 2:
            raise ValueError("CLI route spec must include a tool, for example cli:codex-cli.")
        cli_tool = parts[1].strip()
        model = ":".join(parts[2:]).strip() if len(parts) > 2 else None
        return RouteSpec(provider="cli", cli_tool=cli_tool, model=model or None)
    model = ":".join(parts[1:]).strip() if len(parts) > 1 else None
    return RouteSpec(provider=head, model=model or None)


def load_tasks(task_file: Path | None, *, limit: int | None = None) -> list[CycloneTask]:
    if task_file is None:
        items = DEFAULT_TASKS
    else:
        items = json.loads(task_file.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("Task file must contain a JSON list.")
    tasks = [
        CycloneTask(
            name=str(item.get("name") or f"task-{index + 1}"),
            task_class=str(item.get("task_class") or "general"),
            prompt=str(item.get("prompt") or ""),
            system_prompt=None if item.get("system_prompt") is None else str(item.get("system_prompt")),
        )
        for index, item in enumerate(items)
        if str(item.get("prompt") or "").strip()
    ]
    if limit is not None:
        tasks = tasks[: max(0, limit)]
    if not tasks:
        raise ValueError("No usable tasks were loaded.")
    return tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m astrata.eval.cyclone")
    parser.add_argument("--small-route", default="local:auto-smallest")
    parser.add_argument("--big-route", default="local:auto-largest")
    parser.add_argument("--judge-route", default=None)
    parser.add_argument("--task-file", type=Path, default=None)
    parser.add_argument("--task-limit", type=int, default=None)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--max-small-tokens", type=int, default=220)
    parser.add_argument("--max-big-tokens", type=int, default=320)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    experiment = CycloneExperiment()
    tasks = load_tasks(args.task_file, limit=args.task_limit)
    small_route = parse_route_spec(args.small_route)
    big_route = parse_route_spec(args.big_route)
    judge_route = parse_route_spec(args.judge_route) if args.judge_route else experiment.default_judge_route()
    results, summary = experiment.run(
        tasks=tasks,
        small_route=small_route,
        big_route=big_route,
        judge_route=judge_route,
        max_rounds=max(1, args.max_rounds),
        max_small_tokens=max(32, args.max_small_tokens),
        max_big_tokens=max(64, args.max_big_tokens),
    )
    payload = {
        "small_route": experiment.resolve_route(small_route).display_name(),
        "big_route": experiment.resolve_route(big_route).display_name(),
        "judge_route": experiment.resolve_route(judge_route).display_name(),
        "results": [_task_result_to_dict(item) for item in results],
        "summary": asdict(summary),
    }
    formatted = json.dumps(payload, indent=2)
    print(formatted)
    if args.output is not None:
        args.output.write_text(formatted + "\n", encoding="utf-8")
    return 0


def _task_result_to_dict(result: TaskResult) -> dict[str, Any]:
    return {
        "task": asdict(result.task),
        "small": asdict(result.small),
        "big": asdict(result.big),
        "cyclone": {
            "accepted": result.cyclone.accepted,
            "total_wall_seconds": result.cyclone.total_wall_seconds,
            "draft": asdict(result.cyclone.draft),
            "final": asdict(result.cyclone.final),
            "steering_rounds": result.cyclone.steering_rounds,
        },
        "scores": {name: asdict(score) for name, score in result.scores.items()},
        "winner": result.winner,
        "judge_rationale": result.judge_rationale,
    }


def _parse_steering_decision(content: str) -> SteeringDecision:
    payload = _parse_json_payload(content)
    verdict = str(payload.get("verdict") or "").strip().upper()
    if verdict not in {"PASS", "FAIL"}:
        verdict = "FAIL"
    priorities = payload.get("priorities") or []
    if not isinstance(priorities, list):
        priorities = [str(priorities)]
    return SteeringDecision(
        verdict=verdict,
        rationale=str(payload.get("rationale") or "").strip(),
        priorities=[str(item).strip() for item in priorities if str(item).strip()],
        edit_brief=str(payload.get("edit_brief") or "").strip(),
    )


def _parse_json_payload(content: str) -> dict[str, Any]:
    stripped = str(content or "").strip()
    if not stripped:
        raise RuntimeError("Expected JSON payload but received an empty response.")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(stripped[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Model did not return valid JSON.")


def _pick_experiment_port(preferred_port: int) -> int:
    if _port_is_free(preferred_port):
        return preferred_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
