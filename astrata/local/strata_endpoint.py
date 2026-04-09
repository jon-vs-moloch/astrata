"""Native Strata-style persistent endpoint for Astrata's local runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from astrata.inference.planner import InferencePlanner
from astrata.inference.strategies import FastThenPersistentStrategy, SinglePassStrategy, StrategyContext
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
    mode: str = "persistent"
    initial_mode: str = "persistent"
    mode_source: str = "heuristic"
    escalated: bool = False
    degraded_fallback: bool = False
    strategy_id: str = "single_pass"
    runtime_key: str = "default"


@dataclass(frozen=True)
class StrataEndpointPromptConfig:
    route_selector_prompt: str
    fast_system_prompt: str
    persistent_system_prompt: str


@dataclass(frozen=True)
class StrataModeDecision:
    mode: str
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
        self._fast_then_persistent = FastThenPersistentStrategy()

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
                "route_selector_prompt": prompts.route_selector_prompt,
                "fast_system_prompt": prompts.fast_system_prompt,
                "persistent_system_prompt": prompts.persistent_system_prompt,
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
        mode: str | None = None,
        response_budget: str = "normal",
    ) -> StrataEndpointReply:
        payload = self._load()
        active_thread_id = thread_id or f"thread-{uuid4()}"
        thread = list(payload.setdefault("threads", {}).get(active_thread_id, []))
        thread.append({"role": "user", "content": content})
        selected_mode = self._choose_mode(
            content=content,
            thread=thread,
            mode=mode,
            response_budget=response_budget,
        )
        execution_plan = self._planner.plan_for_endpoint(
            endpoint_type=self.profile().endpoint_type,
            backend=self._runtime.backend_capabilities("llama_cpp"),
        )
        fast_request = self._build_request(
            thread=thread,
            content=content,
            selected_mode="fast",
            system_prompt=system_prompt,
            response_budget=response_budget,
        )
        persistent_request = self._build_request(
            thread=thread,
            content=content,
            selected_mode="persistent",
            system_prompt=system_prompt,
            response_budget="deep" if response_budget == "instant" else response_budget,
        )
        strategy_result = self._execute_strategy(
            selected_mode=selected_mode.mode,
            fast_request=fast_request,
            persistent_request=persistent_request,
            model_id=model_id,
            thread_id=active_thread_id,
            allow_degraded_fallback=allow_degraded_fallback,
            execution_plan=execution_plan,
        )
        reply = strategy_result.content
        escalated = bool(strategy_result.metadata.get("escalated"))
        initial_mode = selected_mode.mode
        final_mode = "persistent" if escalated else selected_mode.mode
        thread.append(
            {
                "role": "assistant",
                "content": reply,
                "mode": final_mode,
                "initial_mode": initial_mode,
                "mode_source": selected_mode.source,
                "escalated": escalated,
                "response_budget": response_budget,
                "strategy_id": strategy_result.strategy_id,
                "runtime_key": strategy_result.runtime_key,
            }
        )
        payload["threads"][active_thread_id] = thread
        self._store(payload)
        selection = self._runtime.current_selection(strategy_result.runtime_key)
        return StrataEndpointReply(
            thread_id=active_thread_id,
            content=reply,
            model_id=None if selection is None else selection.model_id,
            mode=final_mode,
            initial_mode=initial_mode,
            mode_source=selected_mode.source,
            escalated=escalated,
            degraded_fallback=allow_degraded_fallback,
            strategy_id=strategy_result.strategy_id,
            runtime_key=strategy_result.runtime_key,
        )

    def _build_request(
        self,
        *,
        thread: list[dict[str, Any]],
        content: str,
        selected_mode: str,
        system_prompt: str | None,
        response_budget: str,
    ) -> CompletionRequest:
        prompts = self._load_prompt_config()
        if selected_mode == "fast":
            return CompletionRequest(
                messages=[
                    Message(
                        role="system",
                        content=system_prompt
                        or prompts.fast_system_prompt,
                    ),
                    Message(role="user", content=content),
                ],
                model="local",
                temperature=0.1,
                metadata={
                    "max_tokens": 80 if response_budget == "instant" else 160,
                    "execution_mode": "fast",
                    "response_budget": response_budget,
                },
            )
        recent_thread = thread[-8:]
        return CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=system_prompt or prompts.persistent_system_prompt,
                ),
                *[
                    Message(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                    for item in recent_thread
                ],
            ],
            model="local",
            temperature=0.2 if response_budget != "deep" else 0.15,
            metadata={
                "max_tokens": 400 if response_budget != "deep" else 700,
                "execution_mode": "persistent",
                "response_budget": response_budget,
            },
        )

    def _choose_mode(
        self,
        *,
        content: str,
        thread: list[dict[str, Any]],
        mode: str | None,
        response_budget: str,
    ) -> StrataModeDecision:
        if mode in {"fast", "persistent"}:
            return StrataModeDecision(mode=mode, source="forced")
        if response_budget == "instant":
            return StrataModeDecision(mode="fast", source="budget")
        if response_budget == "deep":
            return StrataModeDecision(mode="persistent", source="budget")
        decision = self._select_mode_with_model(content=content, thread=thread)
        if decision:
            return StrataModeDecision(mode=decision, source="self_routed")
        return StrataModeDecision(mode=self._heuristic_mode(content=content, thread=thread), source="heuristic")

    def _heuristic_mode(self, *, content: str, thread: list[dict[str, Any]]) -> str:
        prompt = str(content or "").strip()
        lowered = prompt.lower()
        if len(thread) > 2:
            return "persistent"
        if len(prompt) <= 140 and _looks_trivial_request(lowered):
            return "fast"
        if any(token in lowered for token in ("continue", "rewrite", "refine", "compare", "why", "plan", "strategy")):
            return "persistent"
        if prompt.count("\n") >= 3 or len(prompt) >= 300:
            return "persistent"
        return "fast"

    def _select_mode_with_model(self, *, content: str, thread: list[dict[str, Any]]) -> str | None:
        endpoint = self._ensure_runtime(runtime_key="fast")
        prompts = self._load_prompt_config()
        recent_thread = thread[-4:]
        request = CompletionRequest(
            messages=[
                Message(role="system", content=prompts.route_selector_prompt),
                *[
                    Message(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                    for item in recent_thread[:-1]
                ],
                Message(role="user", content=content),
            ],
            model="local",
            temperature=0.0,
            metadata={"max_tokens": 8, "execution_mode": "route_selector", "response_budget": "instant"},
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
        if "persistent" in decision or "slow" in decision or "deep" in decision:
            return "persistent"
        if "fast" in decision or "quick" in decision or "instant" in decision:
            return "fast"
        return None

    def _ensure_runtime(self, *, runtime_key: str = "default", model_id: str | None = None) -> str:
        current = self._runtime.current_selection(runtime_key)
        current_metadata = getattr(current, "metadata", {}) if current is not None else {}
        health = self._runtime.health(
            runtime_key=runtime_key,
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
            runtime_key=runtime_key,
            backend_id="llama_cpp",
            model_id=chosen_model_id,
            profile_id="quiet",
            port=self._runtime_port(runtime_key),
            activate=runtime_key != "fast",
        )
        current = self._runtime.current_selection(runtime_key)
        if current is None or not current.endpoint:
            raise RuntimeError("Native Strata endpoint could not acquire a local runtime endpoint.")
        return current.endpoint.removesuffix("/health")

    def _execute_strategy(
        self,
        *,
        selected_mode: str,
        fast_request: CompletionRequest,
        persistent_request: CompletionRequest,
        model_id: str | None,
        thread_id: str | None,
        allow_degraded_fallback: bool,
        execution_plan,
    ):
        if execution_plan.strategy == "fast_then_persistent":
            if selected_mode == "persistent":
                endpoint = self._ensure_runtime(runtime_key="persistent", model_id=model_id)
                return self._single_pass.execute(
                    StrategyContext(
                        request=persistent_request,
                        endpoint_type=execution_plan.endpoint.endpoint_type,
                        strategy_id="single_pass",
                        memory_policy=execution_plan.memory_policy,
                        continuity=execution_plan.endpoint.continuity,
                        runtime_key="persistent",
                        metadata={
                            "executor": lambda req, runtime_key: self._client.complete(
                                base_url=endpoint,
                                request=req,
                                thread_id=thread_id,
                                allow_degraded_fallback=allow_degraded_fallback,
                            )
                        },
                    )
                )
            fast_endpoint = self._ensure_runtime(runtime_key="fast", model_id=model_id)
            persistent_endpoint = self._ensure_runtime(runtime_key="persistent", model_id=model_id)
            return self._fast_then_persistent.execute(
                StrategyContext(
                    request=fast_request,
                    endpoint_type=execution_plan.endpoint.endpoint_type,
                    strategy_id=execution_plan.strategy,
                    memory_policy=execution_plan.memory_policy,
                    continuity=execution_plan.endpoint.continuity,
                    runtime_key="fast",
                    metadata={
                        "fast_request": fast_request,
                        "persistent_request": persistent_request,
                        "fast_runtime_key": "fast",
                        "persistent_runtime_key": "persistent",
                        "fast_executor": lambda req, runtime_key: self._client.complete(
                            base_url=fast_endpoint,
                            request=req,
                            thread_id=thread_id,
                            allow_degraded_fallback=allow_degraded_fallback,
                        ),
                        "persistent_executor": lambda req, runtime_key: self._client.complete(
                            base_url=persistent_endpoint,
                            request=req,
                            thread_id=thread_id,
                            allow_degraded_fallback=allow_degraded_fallback,
                        ),
                    },
                )
            )
        if execution_plan.strategy != "single_pass":
            raise RuntimeError(f"Unsupported strategy for current endpoint: {execution_plan.strategy}")
        endpoint = self._ensure_runtime(runtime_key="default", model_id=model_id)
        return self._single_pass.execute(
            StrategyContext(
                request=persistent_request,
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

    def _runtime_port(self, runtime_key: str) -> int:
        base = 8080
        current = self._runtime.current_selection(runtime_key)
        if current is not None and current.endpoint:
            match = re.search(r":(\d+)(?:/health)?$", current.endpoint)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    pass
        offsets = {"default": 0, "persistent": 0, "fast": 1}
        return base + offsets.get(runtime_key, 2)

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
            route_selector_prompt=(
                "You are Astrata's route selector. "
                "Choose the cheapest adequate execution mode for this request. "
                "Return exactly one word: FAST or PERSISTENT. "
                "Choose FAST for trivial, one-shot, stateless requests. "
                "Choose PERSISTENT for requests that need continuity, planning, refinement, or deeper reasoning. "
                "Do not explain your answer."
            ),
            fast_system_prompt=(
                "You are Astrata's fast local execution mode. "
                "Answer directly and tersely. "
                "Prefer the shortest correct useful response. "
                "Do not add extra explanation, docstrings, or examples unless required. "
                "If the task unexpectedly requires deeper reasoning than fast mode supports, respond with exactly ESCALATE_THINKING."
            ),
            persistent_system_prompt="You are Astrata's native persistent Strata-style endpoint.",
        )
        if not self._prompt_config_path.exists():
            self._prompt_config_path.write_text(
                json.dumps(
                    {
                        "route_selector_prompt": defaults.route_selector_prompt,
                        "fast_system_prompt": defaults.fast_system_prompt,
                        "persistent_system_prompt": defaults.persistent_system_prompt,
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
            route_selector_prompt=str(payload.get("route_selector_prompt") or defaults.route_selector_prompt),
            fast_system_prompt=str(payload.get("fast_system_prompt") or defaults.fast_system_prompt),
            persistent_system_prompt=str(payload.get("persistent_system_prompt") or defaults.persistent_system_prompt),
        )

    def set_prompt(self, *, prompt_kind: str, value: str) -> StrataEndpointPromptConfig:
        current = self._load_prompt_config()
        updates = {
            "route_selector": {
                "route_selector_prompt": value,
                "fast_system_prompt": current.fast_system_prompt,
                "persistent_system_prompt": current.persistent_system_prompt,
            },
            "fast_system": {
                "route_selector_prompt": current.route_selector_prompt,
                "fast_system_prompt": value,
                "persistent_system_prompt": current.persistent_system_prompt,
            },
            "persistent_system": {
                "route_selector_prompt": current.route_selector_prompt,
                "fast_system_prompt": current.fast_system_prompt,
                "persistent_system_prompt": value,
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
