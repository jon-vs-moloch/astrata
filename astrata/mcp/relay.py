"""Hosted MCP relay profile and queue registry."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrata.accounts import AccountControlPlaneRegistry
from astrata.mcp.models import (
    HostedMCPRelayLink,
    HostedMCPRelayProfile,
    HostedMCPRelayRequest,
    HostedMCPRelayResult,
    HostedMCPRelaySession,
    HostedMCPRelaySessionMessage,
)
from astrata.mcp.service import MCPBridgeService


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HostedMCPRelayService:
    def __init__(
        self,
        *,
        state_path: Path,
        bridge_service: MCPBridgeService,
        account_registry: AccountControlPlaneRegistry | None = None,
    ) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.bridge_service = bridge_service
        self.account_registry = account_registry

    @classmethod
    def from_settings(cls, settings) -> "HostedMCPRelayService":
        account_registry = AccountControlPlaneRegistry.from_settings(settings)
        bridge_service = MCPBridgeService(state_path=settings.paths.data_dir / "mcp_bridges.json")
        return cls(
            state_path=settings.paths.data_dir / "mcp_relay.json",
            bridge_service=bridge_service,
            account_registry=account_registry,
        )

    def register_profile(self, profile: HostedMCPRelayProfile) -> HostedMCPRelayProfile:
        if self.account_registry is not None:
            authz = self.account_registry.verify_device_link(
                profile_id=profile.profile_id,
                device_id=profile.default_device_id,
            )
            if profile.default_device_id and not authz.get("authorized"):
                raise PermissionError("Relay profile default device is not an active owned device link.")
        payload = self._load()
        profiles = dict(payload.get("profiles") or {})
        profiles[profile.profile_id] = profile.model_dump(mode="json")
        payload["profiles"] = profiles
        self._save(payload)
        return profile

    def get_profile(self, profile_id: str) -> HostedMCPRelayProfile | None:
        raw = dict(self._load().get("profiles") or {}).get(profile_id)
        return HostedMCPRelayProfile(**raw) if isinstance(raw, dict) else None

    def list_profiles(self) -> list[HostedMCPRelayProfile]:
        profiles = [
            HostedMCPRelayProfile(**raw)
            for raw in dict(self._load().get("profiles") or {}).values()
            if isinstance(raw, dict)
        ]
        return sorted(profiles, key=lambda item: item.updated_at, reverse=True)

    def register_local_link(self, link: HostedMCPRelayLink) -> HostedMCPRelayLink:
        if self.account_registry is not None:
            authz = self.account_registry.verify_device_link(
                profile_id=link.profile_id,
                device_id=link.device_id,
                link_token=link.link_token,
            )
            if not authz.get("authorized"):
                raise PermissionError("Local relay link is not owned by the relay profile user.")
        payload = self._load()
        links = dict(payload.get("links") or {})
        links[link.profile_id] = link.model_dump(mode="json")
        payload["links"] = links
        self._save(payload)
        return link

    def local_link(self, profile_id: str) -> HostedMCPRelayLink | None:
        raw = dict(self._load().get("links") or {}).get(profile_id)
        return HostedMCPRelayLink(**raw) if isinstance(raw, dict) else None

    def telemetry_summary(self, profile_id: str | None = None) -> dict[str, Any]:
        payload = self._load()
        profiles = self.list_profiles()
        if profile_id:
            profiles = [profile for profile in profiles if profile.profile_id == profile_id]
        links = [
            HostedMCPRelayLink(**raw)
            for raw in dict(payload.get("links") or {}).values()
            if isinstance(raw, dict)
        ]
        if profile_id:
            links = [link for link in links if link.profile_id == profile_id]
        queue = self.pending_requests(profile_id=profile_id) if profile_id else [
            item
            for items in dict(payload.get("pending_requests") or {}).values()
            if isinstance(items, list)
            for item in items
            if isinstance(item, dict)
        ]
        online_links = [link for link in links if link.status == "online"]
        access_policy = self._account_registry().access_policy()
        return {
            "profile_count": len(profiles),
            "link_count": len(links),
            "online_links": len(online_links),
            "queue_depth": len(queue),
            "access_policy": access_policy,
            "profiles": [profile.model_dump(mode="json") for profile in profiles[:12]],
            "links": [link.model_dump(mode="json") for link in links[:12]],
        }

    def local_capability_advertisement(self, *, profile_id: str) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown relay profile `{profile_id}`.")
        account_registry = self._account_registry()
        access_policy = account_registry.access_policy()
        remote_host_bash = account_registry.remote_host_bash_status(profile_id=profile_id)
        allowed_tools = list(profile.allowed_tools)
        if remote_host_bash.get("enabled"):
            if "run_command" not in allowed_tools:
                allowed_tools.append("run_command")
        else:
            allowed_tools = [tool for tool in allowed_tools if tool != "run_command"]
        return {
            "profile_id": profile.profile_id,
            "allowed_tools": allowed_tools,
            "access_policy": access_policy,
            "access_boundary_summary": access_policy["policy_rule"],
            "remote_host_bash": remote_host_bash,
        }

    def connector_tool_catalog(self, profile_id: str) -> list[dict[str, Any]]:
        advertisement = self.local_capability_advertisement(profile_id=profile_id)
        catalog = []
        for tool in advertisement["allowed_tools"]:
            description = _tool_description(tool)
            if tool == "list_capabilities":
                description = f"{description} It should also explain that {advertisement['access_boundary_summary']}."
            if tool == "run_command":
                description = (
                    "Run a generic host bash command through the paired local node. "
                    "This is shown only after a special acknowledgement because it gives any logged-in GPT for this profile the power to control connected computers."
                )
            catalog.append({"name": tool, "description": description})
        return catalog

    def connector_safe_tools(self, profile_id: str) -> list[dict[str, Any]]:
        profile = self.get_profile(profile_id)
        tools = tuple(profile.allowed_tools if profile is not None else ())
        return [{"name": name, "description": _tool_description(name)} for name in tools]

    def queue_tool_call(
        self,
        *,
        profile_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown relay profile `{profile_id}`.")
        if tool_name not in set(profile.allowed_tools):
            raise PermissionError(f"Tool `{tool_name}` is not allowed for relay profile `{profile_id}`.")
        link = self.local_link(profile_id)
        args = dict(arguments or {})
        request_id = str(uuid4())
        if link is None or link.status != "online":
            payload = self._load()
            queue = list(payload.get("queue") or [])
            queue.append(
                {
                    "request_id": request_id,
                    "profile_id": profile_id,
                    "tool_name": tool_name,
                    "arguments": args,
                    "meta": dict(meta or {}),
                }
            )
            payload["queue"] = queue
            self._save(payload)
            return {"delivery": "queued", "request_id": request_id, "handoff": None}
        delivery_authz = self._authorize_delivery(profile=profile, link=link)
        if not delivery_authz.get("authorized"):
            return {
                "delivery": "rejected",
                "request_id": request_id,
                "handoff": None,
                "reason": delivery_authz.get("status") or "unauthorized_device_link",
            }
        handoff = self.bridge_service.open_inbound_handoff(
            bridge_id=link.bridge_id,
            tool_name=tool_name,
            arguments=args,
            task_id=str(args.get("task_id") or args.get("task") or request_id),
            target_controller="prime",
            delegation_mode="supervisory",
            metadata={"profile_id": profile_id, **dict(meta or {})},
        )
        return {"delivery": "delivered", "request_id": request_id, "handoff": handoff.model_dump(mode="json")}

    def enqueue_remote_request(
        self,
        *,
        profile_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        source_connector: str = "remote_connector",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown relay profile `{profile_id}`.")
        if tool_name not in set(profile.allowed_tools):
            raise PermissionError(f"Tool `{tool_name}` is not allowed for relay profile `{profile_id}`.")
        args = dict(arguments or {})
        resolved_session_id = str(session_id or args.get("session_id") or f"session:{profile_id}").strip()
        record = HostedMCPRelayRequest(
            profile_id=profile_id,
            tool_name=tool_name,
            arguments=args,
            meta=dict(meta or {}),
            source_connector=source_connector,
            target_controller=str(args.get("target_controller") or "prime"),
            task_id=str(args.get("task_id") or f"relay:{profile_id}:{tool_name or 'request'}"),
            session_id=resolved_session_id,
        )
        payload = self._load()
        pending = dict(payload.get("pending_requests") or {})
        requests = list(pending.get(profile_id) or [])
        requests.append(record.model_dump(mode="json"))
        pending[profile_id] = requests
        payload["pending_requests"] = pending
        self._save(payload)
        self.append_session_message(
            profile_id=profile_id,
            session_id=resolved_session_id,
            request_id=record.request_id,
            sender="remote",
            kind="tool_call",
            content={"tool_name": tool_name, "arguments": args},
        )
        return record.model_dump(mode="json")

    def pending_requests(self, *, profile_id: str) -> list[dict[str, Any]]:
        payload = self._load()
        requests = list(dict(payload.get("pending_requests") or {}).get(profile_id) or [])
        return [item for item in requests if isinstance(item, dict)]

    def acked_requests(self, *, profile_id: str) -> list[dict[str, Any]]:
        payload = self._load()
        requests = list(dict(payload.get("acked_requests") or {}).get(profile_id) or [])
        return [item for item in requests if isinstance(item, dict)]

    def results(self, *, profile_id: str) -> list[dict[str, Any]]:
        payload = self._load()
        results = list(dict(payload.get("results") or {}).get(profile_id) or [])
        return [item for item in results if isinstance(item, dict)]

    def local_heartbeat(
        self,
        *,
        profile_id: str,
        device_id: str | None = None,
        link_token: str | None = None,
        advertised_capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize_local_link(profile_id=profile_id, device_id=device_id, link_token=link_token)
        self._update_local_link(
            profile_id=profile_id,
            device_id=device_id,
            status="online",
            advertised_capabilities=dict(advertised_capabilities or {}),
        )
        pending = self.pending_requests(profile_id=profile_id)
        if pending:
            self._mark_session_seen(profile_id=profile_id, actor="local")
        return {
            "ok": True,
            "accepted": True,
            "kind": "local_heartbeat",
            "pending_requests": pending,
            "advertisement_write": {
                "status": "updated",
                "profile_id": profile_id,
                "capabilities": dict(advertised_capabilities or {}),
            },
        }

    def acknowledge_requests(
        self,
        *,
        profile_id: str,
        request_ids: list[str] | tuple[str, ...],
        device_id: str | None = None,
        link_token: str | None = None,
    ) -> dict[str, Any]:
        self._authorize_local_link(profile_id=profile_id, device_id=device_id, link_token=link_token)
        payload = self._load()
        pending = dict(payload.get("pending_requests") or {})
        acked = dict(payload.get("acked_requests") or {})
        current_pending = list(pending.get(profile_id) or [])
        remaining: list[dict[str, Any]] = []
        moved: list[dict[str, Any]] = list(acked.get(profile_id) or [])
        normalized_ids = {str(item) for item in request_ids}
        for raw in current_pending:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("request_id") or "") in normalized_ids:
                request = HostedMCPRelayRequest(**raw).model_copy(
                    update={"status": "acknowledged", "acknowledged_at": _now_iso()}
                )
                moved.append(request.model_dump(mode="json"))
            else:
                remaining.append(raw)
        pending[profile_id] = remaining
        acked[profile_id] = moved
        payload["pending_requests"] = pending
        payload["acked_requests"] = acked
        self._save(payload)
        return {
            "ok": True,
            "accepted": True,
            "acknowledged_request_ids": sorted(normalized_ids),
            "remaining_queue_depth": len(remaining),
        }

    def record_result(
        self,
        *,
        profile_id: str,
        request_id: str,
        result: dict[str, Any] | None = None,
        session_id: str | None = None,
        device_id: str | None = None,
        link_token: str | None = None,
    ) -> dict[str, Any]:
        self._authorize_local_link(profile_id=profile_id, device_id=device_id, link_token=link_token)
        payload = self._load()
        stored_results = dict(payload.get("results") or {})
        results = list(stored_results.get(profile_id) or [])
        record = HostedMCPRelayResult(request_id=request_id, result=dict(result or {}))
        results.append(record.model_dump(mode="json"))
        stored_results[profile_id] = results
        payload["results"] = stored_results
        self._save(payload)
        self.append_session_message(
            profile_id=profile_id,
            session_id=str(session_id or f"session:{profile_id}"),
            request_id=request_id,
            sender="local",
            kind="tool_result",
            content=dict(result or {}),
        )
        return {"ok": True, "accepted": True, "request_id": request_id}

    def result_for_request(self, *, request_id: str, profile_id: str | None = None) -> dict[str, Any]:
        if profile_id:
            for raw in self.results(profile_id=profile_id):
                if str(raw.get("request_id") or "") == request_id:
                    return {"ok": True, **raw}
            return {"ok": False, "status": "not_found", "request_id": request_id}
        payload = self._load()
        for candidate_profile in dict(payload.get("results") or {}).keys():
            result = self.result_for_request(request_id=request_id, profile_id=str(candidate_profile))
            if result.get("ok"):
                return result
        return {"ok": False, "status": "not_found", "request_id": request_id}

    def session(
        self,
        *,
        profile_id: str,
        session_id: str,
        actor: str = "remote",
    ) -> dict[str, Any]:
        payload = self._load()
        sessions = dict(payload.get("sessions") or {})
        profile_sessions = dict(sessions.get(profile_id) or {})
        raw = profile_sessions.get(session_id)
        session = (
            HostedMCPRelaySession(**raw)
            if isinstance(raw, dict)
            else HostedMCPRelaySession(profile_id=profile_id, session_id=session_id)
        )
        updated = {"updated_at": _now_iso()}
        if actor == "local":
            updated["local_last_seen_at"] = _now_iso()
        if actor == "remote":
            updated["remote_last_seen_at"] = _now_iso()
        session = session.model_copy(update=updated)
        profile_sessions[session_id] = session.model_dump(mode="json")
        sessions[profile_id] = profile_sessions
        payload["sessions"] = sessions
        self._save(payload)
        return {"ok": True, "session": session.model_dump(mode="json")}

    def append_session_message(
        self,
        *,
        profile_id: str,
        session_id: str,
        request_id: str = "",
        sender: str,
        kind: str,
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._load()
        sessions = dict(payload.get("sessions") or {})
        profile_sessions = dict(sessions.get(profile_id) or {})
        raw = profile_sessions.get(session_id)
        session = (
            HostedMCPRelaySession(**raw)
            if isinstance(raw, dict)
            else HostedMCPRelaySession(profile_id=profile_id, session_id=session_id)
        )
        message = HostedMCPRelaySessionMessage(
            request_id=request_id,
            sender="local" if sender == "local" else "remote",
            kind=kind,
            content=dict(content or {}),
        )
        messages = list(session.messages)
        messages.append(message)
        updates: dict[str, Any] = {"messages": messages, "updated_at": _now_iso()}
        if sender == "local":
            updates["local_last_seen_at"] = _now_iso()
        else:
            updates["remote_last_seen_at"] = _now_iso()
        session = session.model_copy(update=updates)
        profile_sessions[session_id] = session.model_dump(mode="json")
        sessions[profile_id] = profile_sessions
        payload["sessions"] = sessions
        self._save(payload)
        return {"ok": True, "session": session.model_dump(mode="json"), "message": message.model_dump(mode="json")}

    def _authorize_delivery(
        self,
        *,
        profile: HostedMCPRelayProfile,
        link: HostedMCPRelayLink,
    ) -> dict[str, Any]:
        if self.account_registry is None:
            return {"status": "development_bridge", "authorized": True}
        return self.account_registry.verify_device_link(
            profile_id=profile.profile_id,
            device_id=link.device_id,
            link_token=link.link_token,
        )

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "profiles": {},
                "links": {},
                "queue": [],
                "pending_requests": {},
                "acked_requests": {},
                "results": {},
                "sessions": {},
            }
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "profiles": {},
                "links": {},
                "queue": [],
                "pending_requests": {},
                "acked_requests": {},
                "results": {},
                "sessions": {},
            }
        if not isinstance(payload, dict):
            return {
                "profiles": {},
                "links": {},
                "queue": [],
                "pending_requests": {},
                "acked_requests": {},
                "results": {},
                "sessions": {},
            }
        payload.setdefault("profiles", {})
        payload.setdefault("links", {})
        payload.setdefault("queue", [])
        payload.setdefault("pending_requests", {})
        payload.setdefault("acked_requests", {})
        payload.setdefault("results", {})
        payload.setdefault("sessions", {})
        return payload

    def _account_registry(self) -> AccountControlPlaneRegistry:
        if self.account_registry is not None:
            return self.account_registry
        return AccountControlPlaneRegistry(state_path=self.state_path.parent / "account_control_plane.json")

    def _save(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _authorize_local_link(
        self,
        *,
        profile_id: str,
        device_id: str | None = None,
        link_token: str | None = None,
    ) -> None:
        if self.account_registry is None:
            return
        authz = self.account_registry.verify_device_link(
            profile_id=profile_id,
            device_id=device_id,
            link_token=link_token,
        )
        if not authz.get("authorized"):
            raise PermissionError(authz.get("status") or "unauthorized_device_link")

    def _update_local_link(
        self,
        *,
        profile_id: str,
        device_id: str | None = None,
        status: str = "online",
        advertised_capabilities: dict[str, Any] | None = None,
    ) -> None:
        payload = self._load()
        links = dict(payload.get("links") or {})
        raw = links.get(profile_id)
        if not isinstance(raw, dict):
            return
        link = HostedMCPRelayLink(**raw).model_copy(
            update={
                "device_id": device_id or raw.get("device_id"),
                "status": status,
                "last_heartbeat_at": _now_iso(),
                "advertised_capabilities": dict(advertised_capabilities or raw.get("advertised_capabilities") or {}),
            }
        )
        links[profile_id] = link.model_dump(mode="json")
        payload["links"] = links
        self._save(payload)

    def _mark_session_seen(self, *, profile_id: str, actor: str) -> None:
        payload = self._load()
        sessions = dict(payload.get("sessions") or {})
        profile_sessions = dict(sessions.get(profile_id) or {})
        now = _now_iso()
        updated = False
        for session_id, raw in list(profile_sessions.items()):
            if not isinstance(raw, dict):
                continue
            session = HostedMCPRelaySession(**raw)
            updates = {"updated_at": now}
            if actor == "local":
                updates["local_last_seen_at"] = now
            if actor == "remote":
                updates["remote_last_seen_at"] = now
            profile_sessions[session_id] = session.model_copy(update=updates).model_dump(mode="json")
            updated = True
        if updated:
            sessions[profile_id] = profile_sessions
            payload["sessions"] = sessions
            self._save(payload)


def _tool_description(name: str) -> str:
    descriptions = {
        "search": "Search connector-safe Astrata task and memory projections.",
        "fetch": "Fetch a connector-safe projected item by id or slug.",
        "submit_task": "Submit work into Astrata's governed queue.",
        "get_task_status": "Check connector-safe task status.",
        "list_capabilities": "List capabilities available to this relay profile.",
        "message_prime": "Send a message to Prime through the remote operator lane.",
    }
    return descriptions.get(name, "Connector-safe Astrata tool.")
