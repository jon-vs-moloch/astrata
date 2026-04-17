"""Bounded procedure execution for early self-building tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from astrata.providers.base import CompletionRequest, Message
from astrata.procedures.health import RouteHealthStore
from astrata.procedures.registry import ProcedureCapability
from astrata.providers.registry import ProviderRegistry
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy


class ProcedureExecutionRequest(BaseModel):
    procedure_id: str
    procedure_variant_id: str | None = None
    title: str
    description: str
    expected_paths: list[str]
    available_docs: list[str] = Field(default_factory=list)
    inspection: dict[str, Any] = Field(default_factory=dict)
    actor_capability: ProcedureCapability = "basic"
    execution_mode: str = "careful"
    risk: str = "low"
    priority: int = 0
    urgency: int = 0
    preferred_provider: str | None = None
    avoided_providers: list[str] = Field(default_factory=list)
    preferred_cli_tool: str | None = None
    avoided_cli_tools: list[str] = Field(default_factory=list)
    procedure_metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureExecutionResult(BaseModel):
    status: str
    reason: str
    procedure_id: str
    procedure_variant_id: str | None = None
    actor_capability: ProcedureCapability = "basic"
    written_paths: list[str] = Field(default_factory=list)
    generation_mode: str = "fallback"
    requested_route: dict[str, Any] = Field(default_factory=dict)
    resolved_route: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] = Field(default_factory=dict)
    failure_kind: str | None = None
    degraded_reason: str | None = None
    provider_error: str | None = None
    attempt_count: int = 0
    procedure_metadata: dict[str, Any] = Field(default_factory=dict)


class BoundedFileGenerationProcedure:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        router: RouteChooser,
        health_store: RouteHealthStore,
        quota_policy: QuotaPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._health_store = health_store
        self._quota_policy = quota_policy

    def execute(
        self,
        *,
        project_root: Path,
        request: ProcedureExecutionRequest,
        fallback_builder: Callable[[ProcedureExecutionRequest], dict[str, str]] | None = None,
        force_fallback_only: bool = False,
    ) -> ProcedureExecutionResult:
        route = None
        try:
            route = self._router.choose(
                priority=request.priority,
                urgency=request.urgency,
                risk=request.risk,
                prefer_local=False,
                preferred_providers=((request.preferred_provider,) if request.preferred_provider else ()),
                avoided_providers=tuple(request.avoided_providers),
                preferred_cli_tools=((request.preferred_cli_tool,) if request.preferred_cli_tool else ()),
                avoided_cli_tools=tuple(request.avoided_cli_tools),
            )
        except Exception:
            route = None
        route_dict = route.__dict__ if route else {}
        generated_files: dict[str, str] | None = None
        provider_error: str | None = None
        failure_kind: str | None = None
        degraded_reason: str | None = None
        attempt_count = 0
        provider = self._registry.get_provider(route.provider) if route else None
        preflight = self._preflight_provider(provider, route_dict)
        health = self._health_store.assess(route_dict) if route_dict else {"status": "unknown"}
        quota = self._quota_policy.assess(route_dict) if (route_dict and self._quota_policy is not None) else None
        if (
            not force_fallback_only
            and provider
            and route
            and health.get("status") != "broken"
            and preflight.get("ok")
            and (quota is None or quota.allowed)
        ):
            max_attempts = 2
            for attempt_index in range(1, max_attempts + 1):
                attempt_count = attempt_index
                try:
                    generated_files = self._generate_via_provider(provider, route, request)
                    self._health_store.record_success(route_dict)
                    break
                except Exception as exc:
                    provider_error = str(exc)
                    failure_kind = _classify_failure(provider_error)
                    self._health_store.record_failure(
                        route_dict,
                        failure_kind=failure_kind,
                        error=provider_error,
                    )
                    if attempt_index >= max_attempts or failure_kind not in {"timeout", "connection", "rate_limit"}:
                        break
        elif route_dict and health.get("status") in {"broken", "degraded"}:
            degraded_reason = f"route_health:{health.get('status')}"
        elif route_dict and not preflight.get("ok"):
            degraded_reason = str(preflight.get("reason") or "preflight_failed")
        elif quota is not None and not quota.allowed:
            degraded_reason = f"quota:{quota.reason}"
        elif force_fallback_only:
            degraded_reason = "planner_selected_fallback_only"

        if generated_files is None and fallback_builder is not None:
            generated_files = fallback_builder(request)
            generation_mode = "fallback"
        elif generated_files is not None:
            generation_mode = "provider"
        else:
            return ProcedureExecutionResult(
                status="unsupported",
                reason="No generated files were available for this procedure.",
                procedure_id=request.procedure_id,
                procedure_variant_id=request.procedure_variant_id,
                actor_capability=request.actor_capability,
                generation_mode="none",
                requested_route=route_dict,
                resolved_route={},
                preflight=preflight,
                failure_kind=failure_kind,
                degraded_reason=degraded_reason,
                provider_error=provider_error,
                attempt_count=attempt_count,
                procedure_metadata=dict(request.procedure_metadata),
            )

        written_paths: list[str] = []
        for rel_path, content in generated_files.items():
            if rel_path not in request.expected_paths:
                continue
            path = project_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            written_paths.append(rel_path)

        return ProcedureExecutionResult(
            status="applied" if written_paths else "unsupported",
            reason="Procedure generated and wrote bounded file outputs." if written_paths else "No expected paths were written.",
            procedure_id=request.procedure_id,
            procedure_variant_id=request.procedure_variant_id,
            actor_capability=request.actor_capability,
            written_paths=written_paths,
            generation_mode=generation_mode,
            requested_route=route_dict,
            resolved_route=route_dict if generation_mode == "provider" else {},
            preflight=preflight,
            failure_kind=failure_kind,
            degraded_reason=degraded_reason,
            provider_error=provider_error,
            attempt_count=attempt_count,
            procedure_metadata=dict(request.procedure_metadata),
        )

    def _preflight_provider(self, provider: Any, route: dict[str, Any]) -> dict[str, Any]:
        if not route:
            return {"ok": False, "reason": "no_route"}
        provider_name = str(route.get("provider") or "").strip()
        if not provider:
            return {"ok": False, "reason": "provider_unavailable"}
        if not provider.is_configured():
            return {"ok": False, "reason": "provider_not_configured"}
        if provider_name == "cli":
            cli_tool = str(route.get("cli_tool") or "").strip().lower()
            available_tools = list(getattr(provider, "available_tools", lambda: [])())
            if not cli_tool:
                return {"ok": False, "reason": "cli_tool_missing"}
            return {"ok": cli_tool in available_tools, "reason": None if cli_tool in available_tools else "cli_missing"}
        endpoint = getattr(provider, "endpoint", None)
        if callable(endpoint):
            endpoint_value = str(endpoint() or "").strip()
            if not endpoint_value:
                return {"ok": False, "reason": "endpoint_missing"}
        return {"ok": True, "reason": None}

    def _generate_via_provider(
        self,
        provider: Any,
        route: Any,
        request: ProcedureExecutionRequest,
    ) -> dict[str, str] | None:
        completion = provider.complete(
            CompletionRequest(
                model=route.model,
                messages=[
                    Message(
                        role="system",
                        content=(
                            "Return strict JSON with a single top-level key 'files'. "
                            "Its value must be an object mapping expected relative paths to full file contents. "
                            "Do not include any paths outside the supplied expected_paths. "
                            "If the request says shortcut execution is allowed, you may take the most direct bounded route. "
                            "Otherwise prefer the careful, legible path."
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(request.model_dump(mode='json'), indent=2),
                    ),
                ],
                metadata={"cli_tool": route.cli_tool},
            )
        )
        payload = _try_parse_json(completion.content)
        if not isinstance(payload, dict):
            return None
        files = payload.get("files")
        if not isinstance(files, dict):
            return None
        cleaned: dict[str, str] = {}
        expected = set(request.expected_paths)
        for rel_path, content in files.items():
            if rel_path in expected and isinstance(content, str):
                cleaned[rel_path] = content
        return cleaned or None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _classify_failure(error: str) -> str:
    lowered = str(error or "").strip().lower()
    if not lowered:
        return "unknown"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "connection refused" in lowered or "temporarily unavailable" in lowered or "unreachable" in lowered:
        return "connection"
    if "rate limit" in lowered:
        return "rate_limit"
    if "not configured" in lowered or "missing" in lowered:
        return "missing"
    if "json" in lowered or "parse" in lowered:
        return "invalid_response"
    return "execution_error"
