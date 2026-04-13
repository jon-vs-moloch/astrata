"""Local runtime helpers for maintaining hosted MCP relay links."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from astrata.config.settings import Settings
from astrata.mcp.projections import ConnectorProjectionService
from astrata.mcp.relay import HostedMCPRelayService
from astrata.mcp.triage import RemoteRequestTriagePolicy


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HostedMCPRelayRuntime:
    """Maintains Astrata's local outbound relationship to a hosted MCP relay."""

    def __init__(self, *, settings: Settings, relay_service: HostedMCPRelayService) -> None:
        self.settings = settings
        self.relay_service = relay_service
        self.projections = ConnectorProjectionService.from_settings(settings)
        self.triage_policy = RemoteRequestTriagePolicy()

    @classmethod
    def from_settings(cls, settings: Settings) -> "HostedMCPRelayRuntime":
        relay = HostedMCPRelayService.from_settings(settings)
        return cls(settings=settings, relay_service=relay)

    def heartbeat(
        self,
        *,
        profile_id: str,
        link_id: str | None = None,
        backend_url: str | None = None,
        push_remote: bool = False,
        drain_queue: bool = True,
    ) -> dict[str, Any]:
        profile = self.relay_service.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown hosted MCP relay profile: {profile_id}")
        links = self.relay_service.list_local_links(profile_id=profile_id)
        link = next((item for item in links if item.link_id == link_id), links[0] if links else None)
        if link is None:
            raise KeyError(f"No local relay link registered for profile `{profile_id}`.")
        updated = self.relay_service.update_local_link(
            link_id=link.link_id,
            status="online",
            backend_url=backend_url or link.backend_url or self._default_backend_url(),
            failure_reason="",
            last_heartbeat_at=_now_iso(),
        )
        delivered = self.relay_service.deliver_queued_requests(profile_id=profile_id, bridge_id=updated.bridge_id) if drain_queue else []
        remote_push: dict[str, Any] | None = None
        remote_consumed: list[dict[str, Any]] = []
        remote_ack: dict[str, Any] | None = None
        if push_remote and profile.relay_endpoint:
            remote_push = self.push_remote_heartbeat(profile_id=profile_id, link_id=updated.link_id)
            pending_requests = list(dict(remote_push.get("response") or {}).get("pending_requests") or [])
            if pending_requests:
                remote_consumed = self._consume_remote_requests(
                    profile_id=profile_id,
                    bridge_id=updated.bridge_id,
                    advertisement=self.relay_service.local_capability_advertisement(profile_id=profile_id),
                    remote_requests=pending_requests,
                )
                remote_request_ids = []
                for item in remote_consumed:
                    request = dict(item.get("request") or {})
                    external_request_id = str(request.get("external_request_id") or "")
                    if external_request_id:
                        remote_request_ids.append(external_request_id)
                    tool_name = str(request.get("tool_name") or "")
                    if item.get("result") is not None and external_request_id:
                        self.push_remote_result(
                            profile_id=profile_id,
                            request_id=external_request_id,
                            session_id=str(request.get("arguments", {}).get("session_id") or f"session:{profile_id}"),
                            result={
                                "tool_name": tool_name,
                                "content": item.get("result"),
                            },
                        )
                if remote_request_ids:
                    remote_ack = self.push_remote_ack(
                        profile_id=profile_id,
                        link_id=updated.link_id,
                        request_ids=remote_request_ids,
                    )
        return {
            "status": "ok",
            "profile": profile.model_dump(mode="json"),
            "link": updated.model_dump(mode="json"),
            "advertisement": self.relay_service.local_capability_advertisement(profile_id=profile_id),
            "delivered": delivered,
            "remote_push": remote_push,
            "remote_consumed": remote_consumed,
            "remote_ack": remote_ack,
        }

    def push_remote_heartbeat(self, *, profile_id: str, link_id: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
        profile = self.relay_service.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown hosted MCP relay profile: {profile_id}")
        if not profile.relay_endpoint:
            return {"status": "skipped", "reason": "missing_relay_endpoint"}
        links = self.relay_service.list_local_links(profile_id=profile_id)
        link = next((item for item in links if item.link_id == link_id), links[0] if links else None)
        if link is None:
            raise KeyError(f"No local relay link registered for profile `{profile_id}`.")
        endpoint = profile.relay_endpoint.rstrip("/") + "/relay/local/heartbeat"
        payload = {
            "profile_id": profile_id,
            "link_id": link.link_id,
            "advertisement": self.relay_service.local_capability_advertisement(profile_id=profile_id),
        }
        request = self._json_request(endpoint, payload=payload, auth_token=profile.auth_token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            return {"status": "pushed", "endpoint": endpoint, "response": json.loads(body or "{}")}
        except urllib.error.HTTPError as exc:
            self.relay_service.update_local_link(
                link_id=link.link_id,
                status="degraded",
                failure_reason=f"http_{exc.code}",
                last_heartbeat_at=_now_iso(),
            )
            return {"status": "failed", "endpoint": endpoint, "reason": f"http_{exc.code}"}
        except Exception as exc:
            self.relay_service.update_local_link(
                link_id=link.link_id,
                status="degraded",
                failure_reason=str(exc),
                last_heartbeat_at=_now_iso(),
            )
            return {"status": "failed", "endpoint": endpoint, "reason": str(exc)}

    def push_remote_ack(
        self,
        *,
        profile_id: str,
        link_id: str | None,
        request_ids: list[str],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        profile = self.relay_service.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown hosted MCP relay profile: {profile_id}")
        endpoint = profile.relay_endpoint.rstrip("/") + "/relay/local/ack"
        payload = {
            "profile_id": profile_id,
            "link_id": link_id,
            "request_ids": list(request_ids),
        }
        request = self._json_request(endpoint, payload=payload, auth_token=profile.auth_token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            return {"status": "acked", "endpoint": endpoint, "response": json.loads(body or "{}")}
        except Exception as exc:
            return {"status": "failed", "endpoint": endpoint, "reason": str(exc)}

    def push_remote_result(
        self,
        *,
        profile_id: str,
        request_id: str,
        result: dict[str, Any],
        session_id: str = "",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        profile = self.relay_service.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown hosted MCP relay profile: {profile_id}")
        endpoint = profile.relay_endpoint.rstrip("/") + "/relay/local/result"
        payload = {
            "profile_id": profile_id,
            "request_id": request_id,
            "session_id": session_id,
            "result": dict(result or {}),
        }
        request = self._json_request(endpoint, payload=payload, auth_token=profile.auth_token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            return {"status": "posted", "endpoint": endpoint, "response": json.loads(body or "{}")}
        except Exception as exc:
            return {"status": "failed", "endpoint": endpoint, "reason": str(exc)}

    def _json_request(self, endpoint: str, *, payload: dict[str, Any], auth_token: str) -> urllib.request.Request:
        return urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "astrata-relay/0.1 (+https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev)",
                **({"Authorization": f"Bearer {auth_token}"} if auth_token else {}),
            },
            method="POST",
        )

    def _default_backend_url(self) -> str:
        return "http://127.0.0.1:8891/"

    def _consume_remote_requests(
        self,
        *,
        profile_id: str,
        bridge_id: str,
        advertisement: dict[str, Any],
        remote_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        consumed: list[dict[str, Any]] = []
        for remote in list(remote_requests or []):
            tool_name = str(remote.get("tool_name") or "")
            triage = self.triage_policy.classify(remote)
            triage_metadata = triage.metadata()
            if triage.action == "reject":
                consumed.append(
                    {
                        "delivery": "rejected",
                        "request": {
                            "profile_id": profile_id,
                            "bridge_id": bridge_id,
                            "tool_name": tool_name,
                            "arguments": dict(remote.get("arguments") or {}),
                            "source_connector": str(remote.get("source_connector") or "hosted_relay"),
                            "external_request_id": str(remote.get("request_id") or ""),
                            "triage": triage_metadata,
                            "status": "rejected",
                        },
                        "handoff": None,
                        "triage": triage_metadata,
                        "result": {
                            "status": "rejected",
                            "error": triage.reason,
                            "triage": triage_metadata,
                        },
                    }
                )
                continue
            if tool_name in {"list_capabilities", "get_task_status", "search", "fetch"}:
                request_payload = self.relay_service.submit_connector_request(
                    profile_id=profile_id,
                    tool_name=tool_name,
                    arguments=dict(remote.get("arguments") or {}),
                    source_connector=str(remote.get("source_connector") or "hosted_relay"),
                    bridge_id=bridge_id,
                    task_id=str(remote.get("task_id") or ""),
                    target_controller=str(remote.get("target_controller") or "prime"),
                    external_request_id=str(remote.get("request_id") or ""),
                    triage=triage_metadata,
                )
                result = self.projections.handle_tool(
                    tool_name=tool_name,
                    arguments=dict(remote.get("arguments") or {}),
                    advertisement=advertisement,
                )
                consumed.append({**request_payload, "triage": triage_metadata, "result": result})
                continue
            accepted = self.relay_service.accept_remote_requests(
                profile_id=profile_id,
                remote_requests=[remote],
                bridge_id=bridge_id,
                triage_decisions={str(remote.get("request_id") or ""): triage_metadata},
            )
            for item in accepted:
                if triage.action == "request_attention":
                    item["result"] = {
                        "status": "attention_required",
                        "message": "Astrata accepted the request and routed it to a local attention lane.",
                        "triage": triage_metadata,
                    }
                item["triage"] = triage_metadata
            consumed.extend(accepted)
        return consumed
