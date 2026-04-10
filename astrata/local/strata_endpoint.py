"""Native Strata-style persistent endpoint for Astrata's local runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from astrata.inference.planner import InferencePlanner
from astrata.inference.strategies import SinglePassStrategy, StrategyContext
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.providers.base import CompletionRequest, Message


@dataclass(frozen=True)
class StrataEndpointReply:
    thread_id: str
    content: str
    model_id: str | None
    reasoning_effort: str = "medium"
    requested_reasoning_effort: str = "auto"
    reasoning_effort_source: str = "auto_selector"
    degraded_fallback: bool = False
    strategy_id: str = "single_pass"


@dataclass(frozen=True)
class StrataEndpointPromptConfig:
    reasoning_effort_selector_prompt: str
    default_system_prompt: str


@dataclass(frozen=True)
class StrataReasoningDecision:
    reasoning_effort: str
    source: str


class StrataEndpointService:
    def __init__(
        self,
        *,
        state_path: Path,
        runtime_manager: LocalRuntimeManager,
        runtime_client: LocalRuntimeClient | None = None,
        prompt_config_path: Path | None = None,
    ) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._runtime = runtime_manager
        self._client = runtime_client or LocalRuntimeClient()
        self._prompt_config_path = prompt_config_path or (self._state_path.parent / "strata_endpoint_prompts.json")
        self._planner = InferencePlanner()
        self._single_pass = SinglePassStrategy()

    @classmethod
    def from_settings(cls, settings) -> "StrataEndpointService":
        process_controller = ManagedProcessController(
            state_path=settings.paths.data_dir / "local_runtime.json",
            log_path=settings.paths.data_dir / "local_runtime.log",
        )
        manager = LocalRuntimeManager(
            backends={"llama_cpp": LlamaCppBackend()},
            process_controller=process_controller,
        )
        manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
        return cls(
            state_path=settings.paths.data_dir / "strata_threads.json",
            runtime_manager=manager,
            prompt_config_path=settings.paths.data_dir / "strata_endpoint_prompts.json",
        )

    def status(self) -> dict[str, Any]:
        payload = self._load()
        selection = self._runtime.current_selection()
        prompts = self._load_prompt_config()
        endpoint_profile = self.profile()
        execution_plan = self._planner.plan_for_endpoint(
            endpoint_type=endpoint_profile.endpoint_type,
            backend=self._runtime.backend_capabilities("llama_cpp"),
        )
        return {
            "thread_count": len(payload.get("threads", {})),
            "endpoint_profile": endpoint_profile.model_dump(mode="json"),
            "execution_plan": execution_plan.model_dump(mode="json"),
            "prompt_config_path": str(self._prompt_config_path),
            "prompt_config": {
                "reasoning_effort_selector_prompt": prompts.reasoning_effort_selector_prompt,
                "default_system_prompt": prompts.default_system_prompt,
            },
            "selection": None if selection is None else selection.model_dump(mode="json"),
            "selections": [selection.model_dump(mode="json") for selection in self._runtime.list_selections()],
            "managed_process": None if self._runtime.managed_status() is None else {
                "running": self._runtime.managed_status().running,
                "pid": self._runtime.managed_status().pid,
                "endpoint": self._runtime.managed_status().endpoint,
                "detail": self._runtime.managed_status().detail,
            },
            "managed_processes": {
                key: {
                    "running": value.running,
                    "pid": value.pid,
                    "endpoint": value.endpoint,
                    "detail": value.detail,
                }
                for key, value in self._runtime.list_managed_statuses().items()
            },
            "backend_capabilities": [
                capabilities.model_dump(mode="json")
                for capabilities in self._runtime.list_backend_capabilities()
            ],
        }

    def profile(self):
        return self._planner.endpoint_profile("agent_session")

    def chat(
        self,
        *,
        content: str,
        thread_id: str | None = None,
        model_id: str | None = None,
        allow_degraded_fallback: bool = False,
        system_prompt: str | None = None,
        reasoning_effort: str = "auto",
        response_budget: str = "normal",
    ) -> StrataEndpointReply:
        payload = self._load()
        active_thread_id = thread_id or f"thread-{uuid4()}"
        thread = list(payload.setdefault("threads", {}).get(active_thread_id, []))
        thread.append({"role": "user", "content": content})
        selected_reasoning = self._choose_reasoning_effort(
            content=content,
            thread=thread,
            reasoning_effort=reasoning_effort,
            response_budget=response_budget,
            model_id=model_id,
        )
        execution_plan = self._planner.plan_for_endpoint(
            endpoint_type=self.profile().endpoint_type,
            backend=self._runtime.backend_capabilities("llama_cpp"),
        )
        request = self._build_request(
            thread=thread,
            system_prompt=system_prompt,
            response_budget=response_budget,
            reasoning_effort=selected_reasoning.reasoning_effort,
        )
        strategy_result = self._execute_request(
            request=request,
            model_id=model_id,
            thread_id=active_thread_id,
            allow_degraded_fallback=allow_degraded_fallback,
            execution_plan=execution_plan,
        )
        reply = strategy_result.content
        thread.append(
            {
                "role": "assistant",
                "content": reply,
                "reasoning_effort": selected_reasoning.reasoning_effort,
                "requested_reasoning_effort": reasoning_effort,
                "reasoning_effort_source": selected_reasoning.source,
                "response_budget": response_budget,
                "strategy_id": strategy_result.strategy_id,
            }
        )
        payload["threads"][active_thread_id] = thread
        self._store(payload)
        selection = self._runtime.current_selection()
        return StrataEndpointReply(
            thread_id=active_thread_id,
            content=reply,
            model_id=None if selection is None else selection.model_id,
            reasoning_effort=selected_reasoning.reasoning_effort,
            requested_reasoning_effort=reasoning_effort,
            reasoning_effort_source=selected_reasoning.source,
            degraded_fallback=allow_degraded_fallback,
            strategy_id=strategy_result.strategy_id,
        )

    def _build_request(
        self,
        *,
        thread: list[dict[str, Any]],
        system_prompt: str | None,
        response_budget: str,
        reasoning_effort: str,
    ) -> CompletionRequest:
        prompts = self._load_prompt_config()
        recent_thread = thread[-8:]
        return CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=system_prompt or prompts.default_system_prompt,
                ),
                *[
                    Message(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                    for item in recent_thread
                ],
            ],
            model="local",
            temperature=0.2 if reasoning_effort in {"low", "medium"} else 0.15,
            metadata={
                "max_tokens": self._max_tokens_for_budget(response_budget, reasoning_effort),
                "reasoning_effort": reasoning_effort,
                "response_budget": response_budget,
            },
        )

    def _choose_reasoning_effort(
        self,
        *,
        content: str,
        thread: list[dict[str, Any]],
        reasoning_effort: str,
        response_budget: str,
        model_id: str | None = None,
    ) -> StrataReasoningDecision:
        normalized = str(reasoning_effort or "auto").strip().lower() or "auto"
        if normalized in {"low", "medium", "high"}:
            return StrataReasoningDecision(reasoning_effort=normalized, source="forced")
        if response_budget == "instant":
            return StrataReasoningDecision(reasoning_effort="low", source="budget")
        if response_budget == "deep":
            return StrataReasoningDecision(reasoning_effort="high", source="budget")
        decision = self._select_reasoning_effort_with_model(content=content, thread=thread, model_id=model_id)
        if decision:
            return StrataReasoningDecision(reasoning_effort=decision, source="auto_selector")
        return StrataReasoningDecision(
            reasoning_effort=self._heuristic_reasoning_effort(content=content, thread=thread),
            source="heuristic",
        )

    def _heuristic_reasoning_effort(self, *, content: str, thread: list[dict[str, Any]]) -> str:
        prompt = str(content or "").strip()
        lowered = prompt.lower()
        if len(thread) > 2:
            return "medium"
        if len(prompt) <= 140 and _looks_trivial_request(lowered):
            return "low"
        if any(token in lowered for token in ("continue", "rewrite", "refine", "compare", "why", "plan", "strategy")):
            return "high"
        if prompt.count("\n") >= 3 or len(prompt) >= 300:
            return "high"
        return "medium"

    def _select_reasoning_effort_with_model(
        self,
        *,
        content: str,
        thread: list[dict[str, Any]],
        model_id: str | None,
    ) -> str | None:
        endpoint = self._ensure_runtime(model_id=model_id)
        prompts = self._load_prompt_config()
        recent_thread = thread[-4:]
        request = CompletionRequest(
            messages=[
                Message(role="system", content=prompts.reasoning_effort_selector_prompt),
                *[
                    Message(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                    for item in recent_thread[:-1]
                ],
                Message(role="user", content=content),
            ],
            model="local",
            temperature=0.0,
            metadata={"max_tokens": 8, "reasoning_effort": "low", "response_budget": "instant"},
        )
        try:
            decision = self._client.complete(
                base_url=endpoint,
                request=request,
                thread_id=None,
                allow_degraded_fallback=False,
            ).strip().lower()
        except Exception:
            return None
        if "high" in decision or "deep" in decision:
            return "high"
        if "low" in decision or "light" in decision or "quick" in decision:
            return "low"
        if "medium" in decision or "normal" in decision:
            return "medium"
        return None

    def _ensure_runtime(self, *, model_id: str | None = None) -> str:
        current = self._runtime.current_selection()
        current_metadata = getattr(current, "metadata", {}) if current is not None else {}
        health = self._runtime.health(
            config=current_metadata if current_metadata else None
        ) if current is not None else None
        if current is not None and current.endpoint and health is not None and health.ok:
            return current.endpoint.removesuffix("/health")

        chosen_model_id = model_id
        if not chosen_model_id:
            models = [model for model in self._runtime.model_registry().list_models() if model.role == "model"]
            if not models:
                raise RuntimeError("No local model is available for native Strata endpoint.")
            chosen_model_id = models[0].model_id
        else:
            existing = self._runtime.model_registry().get(chosen_model_id)
            if existing is None:
                by_path = None
                if hasattr(self._runtime.model_registry(), "find_by_path"):
                    by_path = self._runtime.model_registry().find_by_path(chosen_model_id)
                if by_path is not None:
                    chosen_model_id = by_path.model_id
                else:
                    adopted = self._runtime.model_registry().adopt(chosen_model_id)
                    chosen_model_id = adopted.model_id
        self._runtime.start_managed(
            runtime_key="default",
            backend_id="llama_cpp",
            model_id=chosen_model_id,
            profile_id="quiet",
            port=self._runtime_port(),
            activate=True,
        )
        current = self._runtime.current_selection()
        if current is None or not current.endpoint:
            raise RuntimeError("Native Strata endpoint could not acquire a local runtime endpoint.")
        return current.endpoint.removesuffix("/health")

    def _execute_request(
        self,
        *,
        request: CompletionRequest,
        model_id: str | None,
        thread_id: str | None,
        allow_degraded_fallback: bool,
        execution_plan,
    ):
        if execution_plan.strategy != "single_pass":
            raise RuntimeError(f"Unsupported strategy for current endpoint: {execution_plan.strategy}")
        endpoint = self._ensure_runtime(model_id=model_id)
        return self._single_pass.execute(
            StrategyContext(
                request=request,
                endpoint_type=execution_plan.endpoint.endpoint_type,
                strategy_id=execution_plan.strategy,
                memory_policy=execution_plan.memory_policy,
                continuity=execution_plan.endpoint.continuity,
                runtime_key="default",
                metadata={
                    "executor": lambda req, _runtime_key: self._client.complete(
                        base_url=endpoint,
                        request=req,
                        thread_id=thread_id,
                        allow_degraded_fallback=allow_degraded_fallback,
                    )
                },
            )
        )

    def _runtime_port(self) -> int:
        base = 8080
        current = self._runtime.current_selection()
        if current is not None and current.endpoint:
            match = re.search(r":(\d+)(?:/health)?$", current.endpoint)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    pass
        return base

    def _max_tokens_for_budget(self, response_budget: str, reasoning_effort: str) -> int:
        if response_budget == "instant":
            return 120 if reasoning_effort == "low" else 180
        if response_budget == "deep":
            return 900 if reasoning_effort == "high" else 700
        return {"low": 220, "medium": 420, "high": 700}.get(reasoning_effort, 420)

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"threads": {}}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"threads": {}}
        if not isinstance(payload, dict):
            return {"threads": {}}
        payload.setdefault("threads", {})
        return payload

    def _store(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_prompt_config(self) -> StrataEndpointPromptConfig:
        defaults = StrataEndpointPromptConfig(
            reasoning_effort_selector_prompt=(
                "You are Astrata's reasoning-effort selector. "
                "Choose the lightest adequate reasoning effort for this request. "
                "Return exactly one word: LOW, MEDIUM, or HIGH. "
                "Choose LOW for trivial, one-shot, or obvious requests. "
                "Choose MEDIUM for normal requests that benefit from some thought. "
                "Choose HIGH for requests that need planning, refinement, comparison, or deeper reasoning. "
                "Do not explain your answer."
            ),
            default_system_prompt="You are Astrata's native local Strata-style endpoint.",
        )
        if not self._prompt_config_path.exists():
            self._prompt_config_path.write_text(
                json.dumps(
                    {
                        "reasoning_effort_selector_prompt": defaults.reasoning_effort_selector_prompt,
                        "default_system_prompt": defaults.default_system_prompt,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return defaults
        try:
            payload = json.loads(self._prompt_config_path.read_text(encoding="utf-8"))
        except Exception:
            return defaults
        return StrataEndpointPromptConfig(
            reasoning_effort_selector_prompt=str(
                payload.get("reasoning_effort_selector_prompt")
                or payload.get("route_selector_prompt")
                or defaults.reasoning_effort_selector_prompt
            ),
            default_system_prompt=str(
                payload.get("default_system_prompt")
                or payload.get("persistent_system_prompt")
                or defaults.default_system_prompt
            ),
        )

    def set_prompt(self, *, prompt_kind: str, value: str) -> StrataEndpointPromptConfig:
        current = self._load_prompt_config()
        updates = {
            "reasoning_effort_selector": {
                "reasoning_effort_selector_prompt": value,
                "default_system_prompt": current.default_system_prompt,
            },
            "default_system": {
                "reasoning_effort_selector_prompt": current.reasoning_effort_selector_prompt,
                "default_system_prompt": value,
            },
        }
        if prompt_kind not in updates:
            raise ValueError(f"Unsupported prompt kind: {prompt_kind}")
        payload = updates[prompt_kind]
        self._prompt_config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self._load_prompt_config()


def _looks_trivial_request(text: str) -> bool:
    patterns = (
        r"\bwrite a python function\b",
        r"\bwrite a function\b",
        r"\bwhat is\b",
        r"\bsum of\b",
        r"\breturn the sum\b",
        r"\bhello\b",
        r"\bhow many\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)
