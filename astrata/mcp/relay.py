"""Hosted MCP relay scaffolding for connector-safe remote access."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from astrata.accounts import AccountControlPlaneRegistry
from astrata.mcp.models import (
    HostedMCPRelayEvent,
    HostedMCPRelayLink,
    HostedMCPRelayProfile,
    HostedMCPRelayRequest,
)
from astrata.mcp.service import MCPBridgeService

_DISCLOSURE_ORDER = ("public", "connector_safe", "trusted_remote", "local_only", "enclave_only")

_POSTURE_TOOL_DEFAULTS: dict[str, tuple[str, ...]] = {
    "true_remote_prime": (
        "search",
        "fetch",
        "submit_task",
        "get_task_status",
        "list_capabilities",
        "message_prime",
        "search_files",
        "read_file",
        "propose_patch",
        "request_elevation",
        "apply_patch",
        "run_tests",
        "run_command",
        "delegate_subtasks",
        "handoff_to_controller",
        "request_browser_action",
    ),
    "peer": (
        "search",
        "fetch",
        "submit_task",
        "get_task_status",
        "list_capabilities",
        "message_prime",
        "search_files",
        "read_file",
        "propose_patch",
        "request_elevation",
        "apply_patch",
        "run_tests",
        "run_command",
    ),
    "local_prime_delegate": (
        "search",
        "fetch",
        "submit_task",
        "get_task_status",
        "list_capabilities",
        "message_prime",
        "search_files",
        "read_file",
        "propose_patch",
        "request_elevation",
        "apply_patch",
        "run_tests",
        "run_command",
    ),
    "local_prime_customer": (
        "search",
        "fetch",
        "get_task_status",
        "list_capabilities",
    ),
}

_TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "search": {
        "name": "search",
        "description": "Search connector-safe Astrata summaries and public metadata.",
        "inputSchema": {"type": "object"},
    },
    "fetch": {
        "name": "fetch",
        "description": "Fetch connector-safe task, memory, or capability views from Astrata.",
        "inputSchema": {"type": "object"},
    },
    "submit_task": {
        "name": "submit_task",
        "description": "Submit a governed task request to Astrata.",
        "inputSchema": {"type": "object"},
    },
    "get_task_status": {
        "name": "get_task_status",
        "description": "Inspect the status of a previously submitted Astrata task.",
        "inputSchema": {"type": "object"},
    },
    "list_capabilities": {
        "name": "list_capabilities",
        "description": "List connector-safe Astrata capabilities visible to this relay profile.",
        "inputSchema": {"type": "object"},
    },
    "message_prime": {
        "name": "message_prime",
        "description": "Deliver a connector-safe message into Prime's durable inbox.",
        "inputSchema": {"type": "object"},
    },
    "search_files": {
        "name": "search_files",
        "description": "Ask the paired local node to search the user's security-scoped workspace.",
        "inputSchema": {"type": "object"},
    },
    "read_file": {
        "name": "read_file",
        "description": "Ask the paired local node to read a security-scoped workspace file.",
        "inputSchema": {"type": "object"},
    },
    "propose_patch": {
        "name": "propose_patch",
        "description": "Submit a proposed code patch for quick local review.",
        "inputSchema": {"type": "object"},
    },
    "request_elevation": {
        "name": "request_elevation",
        "description": "Request a higher-trust local session before sensitive tools run.",
        "inputSchema": {"type": "object"},
    },
    "apply_patch": {
        "name": "apply_patch",
        "description": "Apply an approved patch through the paired local node.",
        "inputSchema": {"type": "object"},
    },
    "run_tests": {
        "name": "run_tests",
        "description": "Run an approved local test command through the paired local node.",
        "inputSchema": {"type": "object"},
    },
    "run_command": {
        "name": "run_command",
        "description": "Run an approved local command through the paired local node.",
        "inputSchema": {"type": "object"},
    },
    "delegate_subtasks": {
        "name": "delegate_subtasks",
        "description": "Request Astrata to decompose and supervise bounded work.",
        "inputSchema": {"type": "object"},
    },
    "handoff_to_controller": {
        "name": "handoff_to_controller",
        "description": "Route work toward a specific Astrata controller when policy allows.",
        "inputSchema": {"type": "object"},
    },
    "request_browser_action": {
        "name": "request_browser_action",
        "description": "Request a governed browser action through Astrata's internal browser substrate.",
        "inputSchema": {"type": "object"},
    },
}


class HostedMCPRelayService:
    """Maintains hosted MCP connector profiles, local links, queueing, and telemetry."""

    def __init__(self, *, state_path: Path, bridge_service: MCPBridgeService) -> None:
        self._state_path = state_path
        self._bridge_service = bridge_service
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings, *, bridge_service: MCPBridgeService | None = None) -> "HostedMCPRelayService":
        service = bridge_service or MCPBridgeService.from_settings(settings)
        return cls(state_path=settings.paths.data_dir / "mcp_relay.json", bridge_service=service)

    def register_profile(self, profile: HostedMCPRelayProfile) -> HostedMCPRelayProfile:
        payload = self._load()
        profiles = dict(payload.get("profiles") or {})
        requested_tier = profile.max_disclosure_tier
        updated = profile.model_copy(
            update={
                "allowed_tools": profile.allowed_tools or self._default_tools_for_posture(profile.control_posture),
                "max_disclosure_tier": self._applied_tier_for_profile(
                    posture=profile.control_posture,
                    requested=requested_tier,
                ),
                "updated_at": profile.updated_at,
            }
        )
        profiles[updated.profile_id] = updated.model_dump(mode="json")
        payload["profiles"] = profiles
        self._save(payload)
        self._append_event(
            HostedMCPRelayEvent(
                profile_id=updated.profile_id,
                event_type="profile_registered",
                payload={
                    "label": updated.label,
                    "exposure": updated.exposure,
                    "control_posture": updated.control_posture,
                    "local_prime_behavior": updated.local_prime_behavior,
                },
            )
        )
        return updated

    def get_profile(self, profile_id: str) -> HostedMCPRelayProfile | None:
        payload = self._load()
        record = dict(payload.get("profiles", {}).get(profile_id) or {})
        if not record:
            return None
        return HostedMCPRelayProfile(**record)

    def list_profiles(self) -> list[HostedMCPRelayProfile]:
        payload = self._load()
        profiles = [HostedMCPRelayProfile(**record) for record in dict(payload.get("profiles") or {}).values()]
        return sorted(profiles, key=lambda profile: (profile.exposure, profile.label, profile.created_at))

    def register_local_link(self, link: HostedMCPRelayLink) -> HostedMCPRelayLink:
        self._require_profile(link.profile_id)
        payload = self._load()
        links = dict(payload.get("links") or {})
        updated = link.model_copy(update={"queue_depth": self._queue_depth(link.profile_id), "updated_at": link.updated_at})
        links[updated.link_id] = updated.model_dump(mode="json")
        payload["links"] = links
        self._save(payload)
        self._append_event(
            HostedMCPRelayEvent(
                profile_id=updated.profile_id,
                link_id=updated.link_id,
                event_type="local_link_registered",
                payload={"bridge_id": updated.bridge_id, "status": updated.status},
            )
        )
        return updated

    def update_local_link(
        self,
        *,
        link_id: str,
        status: str | None = None,
        backend_url: str | None = None,
        failure_reason: str | None = None,
        last_heartbeat_at: str | None = None,
    ) -> HostedMCPRelayLink:
        payload = self._load()
        links = dict(payload.get("links") or {})
        record = dict(links.get(link_id) or {})
        if not record:
            raise KeyError(f"Unknown hosted MCP relay link: {link_id}")
        if status is not None:
            record["status"] = status
        if backend_url is not None:
            record["backend_url"] = backend_url
        if failure_reason is not None:
            record["failure_reason"] = failure_reason
        if last_heartbeat_at is not None:
            record["last_heartbeat_at"] = last_heartbeat_at
        record["queue_depth"] = self._queue_depth(str(record.get("profile_id") or ""))
        links[link_id] = record
        payload["links"] = links
        self._save(payload)
        return HostedMCPRelayLink(**record)

    def list_local_links(self, *, profile_id: str | None = None) -> list[HostedMCPRelayLink]:
        payload = self._load()
        links = [HostedMCPRelayLink(**record) for record in dict(payload.get("links") or {}).values()]
        if profile_id:
            links = [link for link in links if link.profile_id == profile_id]
        return sorted(links, key=lambda link: (link.profile_id, link.created_at))

    def list_requests(self, *, profile_id: str | None = None, status: str | None = None) -> list[HostedMCPRelayRequest]:
        payload = self._load()
        requests = [HostedMCPRelayRequest(**record) for record in list(payload.get("requests") or [])]
        if profile_id:
            requests = [request for request in requests if request.profile_id == profile_id]
        if status:
            requests = [request for request in requests if request.status == status]
        return sorted(requests, key=lambda request: request.created_at)

    def list_events(self, *, profile_id: str | None = None) -> list[HostedMCPRelayEvent]:
        payload = self._load()
        events = [HostedMCPRelayEvent(**record) for record in list(payload.get("events") or [])]
        if profile_id:
            events = [event for event in events if event.profile_id == profile_id]
        return sorted(events, key=lambda event: event.created_at)

    def local_capability_advertisement(self, *, profile_id: str) -> dict[str, Any]:
        profile = self._require_profile(profile_id)
        links = self.list_local_links(profile_id=profile_id)
        registry = self._account_registry()
        access_policy = registry.access_policy()
        remote_host_bash = registry.remote_host_bash_status(profile_id=profile_id)
        allowed_tools = list(profile.allowed_tools)
        if remote_host_bash["enabled"]:
            if "run_command" not in allowed_tools:
                allowed_tools.append("run_command")
        else:
            allowed_tools = [tool for tool in allowed_tools if tool != "run_command"]
        return {
            "profile_id": profile.profile_id,
            "control_posture": profile.control_posture,
            "local_prime_behavior": profile.local_prime_behavior,
            "max_disclosure_tier": profile.max_disclosure_tier,
            "allowed_tools": allowed_tools,
            "links": [link.model_dump(mode="json") for link in links],
            "queue_depth": self._queue_depth(profile_id),
            "access_policy": access_policy,
            "access_boundary_summary": access_policy["policy_rule"],
            "remote_host_bash": remote_host_bash,
        }

    def connector_tool_catalog(self, profile_id: str) -> list[dict[str, Any]]:
        advertisement = self.local_capability_advertisement(profile_id=profile_id)
        access_policy = advertisement["access_policy"]
        catalog: list[dict[str, Any]] = []
        for tool_name in advertisement["allowed_tools"]:
            if tool_name not in _TOOL_CATALOG:
                continue
            record = dict(_TOOL_CATALOG[tool_name])
            if tool_name == "list_capabilities":
                record["description"] = (
                    f"{record['description']} It should also explain that {access_policy['policy_rule']}."
                )
            if tool_name == "run_command":
                record["description"] = (
                    "Run a generic host bash command through the paired local node. "
                    "This is shown only after a special acknowledgement because it gives any logged-in GPT for this profile the power to control connected computers."
                )
            catalog.append(record)
        return catalog

    def authorize_profile(self, *, profile_id: str, authorization: str = "") -> HostedMCPRelayProfile:
        profile = self._require_profile(profile_id)
        if profile.auth_mode == "none":
            return profile
        if profile.auth_mode == "token":
            expected = profile.auth_token.strip()
            provided = self._extract_bearer_token(authorization)
            if not expected or provided != expected:
                raise PermissionError(f"Hosted MCP relay profile `{profile_id}` rejected unauthorized access.")
            return profile
        raise PermissionError(f"Unsupported auth mode `{profile.auth_mode}` for hosted relay profile `{profile_id}`.")

    def submit_connector_request(
        self,
        *,
        profile_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        source_connector: str = "",
        bridge_id: str = "",
        task_id: str = "",
        target_controller: str = "prime",
        external_request_id: str = "",
        triage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = self._require_profile(profile_id)
        if tool_name not in profile.allowed_tools:
            raise ValueError(f"Tool `{tool_name}` is not allowed for hosted relay profile `{profile_id}`.")
        triage_payload = dict(triage or {})
        request = HostedMCPRelayRequest(
            profile_id=profile_id,
            tool_name=tool_name,
            task_id=task_id or f"relay:{profile_id}:{tool_name}",
            external_request_id=external_request_id,
            bridge_id=bridge_id,
            arguments=dict(arguments or {}),
            source_connector=source_connector,
            target_controller=target_controller,
            requested_disclosure_tier=self._normalize_tier(
                str((arguments or {}).get("disclosure_tier") or profile.max_disclosure_tier)
            ),
            triage_lane=str(triage_payload.get("lane") or ""),
            triage_urgency=str(triage_payload.get("urgency") or ""),
            triage_action=str(triage_payload.get("action") or ""),
            triage_reason=str(triage_payload.get("reason") or ""),
            triage_sla_seconds=int(triage_payload.get("sla_seconds") or 0),
            requires_attention=bool(triage_payload.get("requires_attention") or False),
            triage_audit_tags=tuple(str(tag) for tag in list(triage_payload.get("audit_tags") or [])),
        )
        link = self._resolve_link(profile_id=profile_id, bridge_id=bridge_id)
        if link is None or link.status != "online" or not profile.online:
            reason = "local_link_unavailable" if link is None or link.status == "offline" else "local_link_degraded"
            queued = request.model_copy(update={"status": "queued", "queue_reason": reason})
            self._store_request(queued)
            self._refresh_queue_depth(profile_id)
            self._append_event(
                HostedMCPRelayEvent(
                    profile_id=profile_id,
                    link_id="" if link is None else link.link_id,
                    request_id=queued.request_id,
                    event_type="request_queued",
                    payload={"tool_name": tool_name, "reason": reason, "triage": triage_payload},
                )
            )
            return {
                "delivery": "queued",
                "request": queued.model_dump(mode="json"),
                "handoff": None,
            }
        result = self._forward_request(profile=profile, link=link, request=request)
        self._store_request(HostedMCPRelayRequest(**dict(result["request"] or {})))
        return result

    def accept_remote_requests(
        self,
        *,
        profile_id: str,
        remote_requests: list[dict[str, Any]],
        bridge_id: str | None = None,
        triage_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        triage_map = dict(triage_decisions or {})
        for remote in list(remote_requests or []):
            external_request_id = str(remote.get("request_id") or "")
            accepted.append(
                self.submit_connector_request(
                    profile_id=profile_id,
                    tool_name=str(remote.get("tool_name") or ""),
                    arguments=dict(remote.get("arguments") or {}),
                    source_connector=str(remote.get("source_connector") or "hosted_relay"),
                    bridge_id=bridge_id or str(remote.get("bridge_id") or ""),
                    task_id=str(remote.get("task_id") or ""),
                    target_controller=str(remote.get("target_controller") or "prime"),
                    external_request_id=external_request_id,
                    triage=triage_map.get(external_request_id) or triage_map.get(str(remote.get("tool_name") or "")),
                )
            )
        return accepted

    def deliver_queued_requests(self, *, profile_id: str, bridge_id: str | None = None) -> list[dict[str, Any]]:
        profile = self._require_profile(profile_id)
        link = self._resolve_link(profile_id=profile_id, bridge_id=bridge_id or "")
        if link is None or link.status != "online" or not profile.online:
            return []
        delivered: list[dict[str, Any]] = []
        payload = self._load()
        requests = [HostedMCPRelayRequest(**record) for record in list(payload.get("requests") or [])]
        updated_records: list[dict[str, Any]] = []
        for request in requests:
            if request.profile_id != profile_id or request.status != "queued":
                updated_records.append(request.model_dump(mode="json"))
                continue
            result = self._forward_request(profile=profile, link=link, request=request)
            delivered.append(result)
            updated = HostedMCPRelayRequest(**dict(result["request"] or {}))
            updated_records.append(updated.model_dump(mode="json"))
        payload["requests"] = updated_records[-256:]
        self._save(payload)
        self._refresh_queue_depth(profile_id)
        return delivered

    def _forward_request(
        self,
        *,
        profile: HostedMCPRelayProfile,
        link: HostedMCPRelayLink,
        request: HostedMCPRelayRequest,
    ) -> dict[str, Any]:
        handoff = self._bridge_service.open_inbound_handoff(
            bridge_id=link.bridge_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            task_id=request.task_id,
            target_controller=request.target_controller,
            delegation_mode="connector_relay",
            envelope={
                "relay_profile_id": profile.profile_id,
                "connector_exposure": profile.exposure,
                "security_level": request.requested_disclosure_tier,
                "source_connector": request.source_connector,
                "triage_lane": request.triage_lane,
                "triage_urgency": request.triage_urgency,
                "requires_attention": request.requires_attention,
            },
            metadata={
                "relay_profile_id": profile.profile_id,
                "source_connector": request.source_connector,
                "requested_disclosure_tier": request.requested_disclosure_tier,
                "connector_tool": request.tool_name,
                "triage_lane": request.triage_lane,
                "triage_urgency": request.triage_urgency,
                "triage_action": request.triage_action,
                "triage_reason": request.triage_reason,
                "triage_sla_seconds": request.triage_sla_seconds,
                "requires_attention": request.requires_attention,
                "triage_audit_tags": list(request.triage_audit_tags),
                **({"external_request_id": request.external_request_id} if request.external_request_id else {}),
            },
        )
        forwarded = request.model_copy(update={"status": "forwarded", "bridge_id": link.bridge_id, "queue_reason": ""})
        self._append_event(
            HostedMCPRelayEvent(
                profile_id=profile.profile_id,
                link_id=link.link_id,
                request_id=forwarded.request_id,
                event_type="request_forwarded",
                payload={
                    "tool_name": request.tool_name,
                    "handoff_id": handoff.handoff_id,
                    "bridge_id": link.bridge_id,
                    "triage_lane": request.triage_lane,
                    "triage_urgency": request.triage_urgency,
                    "requires_attention": request.requires_attention,
                },
            )
        )
        return {
            "delivery": "forwarded",
            "request": forwarded.model_dump(mode="json"),
            "handoff": handoff.model_dump(mode="json"),
        }

    def shape_connector_result(
        self,
        *,
        profile_id: str,
        payload: dict[str, Any],
        requested_tier: str | None = None,
    ) -> dict[str, Any]:
        profile = self._require_profile(profile_id)
        allowed_tier = self._normalize_tier(requested_tier or profile.max_disclosure_tier)
        allowed_index = _DISCLOSURE_ORDER.index(allowed_tier)
        shaped: dict[str, Any] = {}
        redacted_fields: list[str] = []
        for key, value in dict(payload or {}).items():
            if key in _DISCLOSURE_ORDER:
                if _DISCLOSURE_ORDER.index(key) <= allowed_index:
                    shaped[key] = value
                else:
                    redacted_fields.append(key)
                continue
            if key == "summary_public":
                shaped["summary"] = value
                continue
            if key == "summary_sensitive":
                if allowed_index >= _DISCLOSURE_ORDER.index("trusted_remote"):
                    shaped["summary_sensitive"] = value
                else:
                    redacted_fields.append(key)
                continue
            shaped[key] = value
        return {
            "profile_id": profile_id,
            "max_disclosure_tier": allowed_tier,
            "content": shaped,
            "redacted_fields": redacted_fields,
        }

    def telemetry_summary(self, *, profile_id: str | None = None) -> dict[str, Any]:
        profiles = self.list_profiles() if profile_id is None else [self._require_profile(profile_id)]
        links = self.list_local_links(profile_id=profile_id)
        requests = self.list_requests(profile_id=profile_id)
        events = self.list_events(profile_id=profile_id)
        access_policy = self._account_registry().access_policy()
        return {
            "profiles": [profile.model_dump(mode="json") for profile in profiles],
            "links": [link.model_dump(mode="json") for link in links],
            "queue_depth": len([request for request in requests if request.status == "queued"]),
            "request_counts": dict(Counter(request.status for request in requests)),
            "event_counts": dict(Counter(event.event_type for event in events)),
            "link_status_counts": dict(Counter(link.status for link in links)),
            "access_policy": access_policy,
            "access_boundary_summary": access_policy["policy_rule"],
            "remote_host_bash": None if profile_id is None else self._account_registry().remote_host_bash_status(profile_id=profile_id),
        }

    def _require_profile(self, profile_id: str) -> HostedMCPRelayProfile:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown hosted MCP relay profile: {profile_id}")
        return profile

    def _resolve_link(self, *, profile_id: str, bridge_id: str) -> HostedMCPRelayLink | None:
        links = self.list_local_links(profile_id=profile_id)
        if bridge_id:
            for link in links:
                if link.bridge_id == bridge_id:
                    return link
        return links[0] if links else None

    def _store_request(self, request: HostedMCPRelayRequest) -> None:
        payload = self._load()
        requests = list(payload.get("requests") or [])
        requests.append(request.model_dump(mode="json"))
        payload["requests"] = requests[-256:]
        self._save(payload)

    def _append_event(self, event: HostedMCPRelayEvent) -> None:
        payload = self._load()
        events = list(payload.get("events") or [])
        events.append(event.model_dump(mode="json"))
        payload["events"] = events[-256:]
        self._save(payload)

    def _refresh_queue_depth(self, profile_id: str) -> None:
        payload = self._load()
        links = dict(payload.get("links") or {})
        for link_id, record in list(links.items()):
            if record.get("profile_id") == profile_id:
                record["queue_depth"] = self._queue_depth(profile_id)
                links[link_id] = record
        payload["links"] = links
        self._save(payload)

    def _queue_depth(self, profile_id: str) -> int:
        return len([request for request in self.list_requests(profile_id=profile_id) if request.status == "queued"])

    def _normalize_tier(self, value: str) -> str:
        normalized = str(value or "connector_safe").strip().lower()
        if normalized not in _DISCLOSURE_ORDER:
            return "connector_safe"
        return normalized

    def _default_tools_for_posture(self, posture: str) -> tuple[str, ...]:
        return _POSTURE_TOOL_DEFAULTS.get(str(posture or "").strip().lower(), _POSTURE_TOOL_DEFAULTS["local_prime_customer"])

    def _applied_tier_for_profile(self, *, posture: str, requested: str) -> str:
        normalized_posture = str(posture or "").strip().lower()
        requested_tier = self._normalize_tier(requested)
        posture_default = {
            "true_remote_prime": "trusted_remote",
            "peer": "connector_safe",
            "local_prime_delegate": "connector_safe",
            "local_prime_customer": "public",
        }.get(normalized_posture, "connector_safe")
        model_default = HostedMCPRelayProfile.model_fields["max_disclosure_tier"].default
        if requested_tier == model_default:
            return posture_default
        if _DISCLOSURE_ORDER.index(requested_tier) > _DISCLOSURE_ORDER.index(posture_default):
            return posture_default
        return requested_tier

    def _extract_bearer_token(self, authorization: str) -> str:
        value = str(authorization or "").strip()
        if not value:
            return ""
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"profiles": {}, "links": {}, "requests": [], "events": []}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"profiles": {}, "links": {}, "requests": [], "events": []}

    def _save(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _account_registry(self) -> AccountControlPlaneRegistry:
        return AccountControlPlaneRegistry(state_path=self._state_path.parent / "account_control_plane.json")
