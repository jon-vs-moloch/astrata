"""Registry for durable Astrata agents."""

from __future__ import annotations

import json
from pathlib import Path

from astrata.agents.models import DurableAgentRecord


class DurableAgentRegistry:
    """Stores durable agent definitions outside the transient runtime."""

    def __init__(self, *, state_path: Path) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "DurableAgentRegistry":
        return cls(state_path=settings.paths.data_dir / "durable_agents.json")

    def ensure_bootstrap_agents(self) -> list[DurableAgentRecord]:
        payload = self._load()
        agents = dict(payload.get("agents") or {})
        changed = False
        defaults = (
            DurableAgentRecord(
                agent_id="prime",
                name="",
                title="Prime",
                role="prime",
                persona_prompt="Primary coordinating intelligence for the principal.",
                responsibilities=["top-level coordination", "judgment", "delegation"],
                permissions_profile={"network": True, "local_memory": True},
                inference_binding={"lane": "prime", "provider": "codex"},
                message_policy={"may_message_principal": True, "may_receive_principal": True},
                fallback_policy={"fallback_agent_id": "reception", "queue_if_unavailable": True},
                allowed_recipients=["principal", "local", "reception"],
                created_by="system",
            ),
            DurableAgentRecord(
                agent_id="reception",
                name="Reception",
                title="Reception",
                role="assistant",
                persona_prompt="Personal assistant and second point of contact when Prime is unavailable.",
                responsibilities=["continuity replies", "intake triage", "queue stewardship", "bounded startup help"],
                permissions_profile={"network": True, "local_memory": True, "may_act_for_prime": False},
                inference_binding={"lane": "assistant", "provider": "cli", "cli_tool": "kilocode"},
                message_policy={"may_message_principal": True, "may_receive_principal": True},
                fallback_policy={"fallback_agent_id": "local", "queue_if_unavailable": True},
                allowed_recipients=["principal", "prime", "local"],
                created_by="system",
            ),
            DurableAgentRecord(
                agent_id="local",
                name="Local",
                title="Local",
                role="local",
                persona_prompt="Local fallback agent with degraded but privacy-preserving capability.",
                responsibilities=["local continuity", "offline fallback", "sensitive local handling"],
                permissions_profile={"network": False, "local_memory": True},
                inference_binding={"lane": "local", "provider": "local-runtime"},
                message_policy={"may_message_principal": True, "may_receive_principal": True},
                fallback_policy={"fallback_agent_id": "fallback", "queue_if_unavailable": True},
                allowed_recipients=["principal", "prime"],
                created_by="system",
            ),
            DurableAgentRecord(
                agent_id="fallback",
                name="Fallback",
                title="Fallback",
                role="fallback",
                persona_prompt="Bounded continuity responder that explains degraded conditions honestly.",
                responsibilities=["availability notices", "queue preservation", "handoff explanations"],
                permissions_profile={"network": False, "local_memory": False},
                inference_binding={"lane": "fallback", "provider": "none"},
                message_policy={"may_message_principal": True, "may_receive_principal": True},
                fallback_policy={"queue_if_unavailable": True},
                allowed_recipients=["principal", "prime", "local"],
                created_by="system",
            ),
        )
        for record in defaults:
            existing = dict(agents.get(record.agent_id) or {})
            if record.agent_id not in agents:
                agents[record.agent_id] = record.model_dump(mode="json")
                changed = True
                continue
            if not str(existing.get("name") or "").strip() and str(record.name or "").strip():
                existing["name"] = record.name
                agents[record.agent_id] = existing
                changed = True
        if changed:
            payload["agents"] = agents
            self._save(payload)
        return [DurableAgentRecord(**item) for item in agents.values()]

    def upsert(self, agent: DurableAgentRecord) -> DurableAgentRecord:
        payload = self._load()
        agents = dict(payload.get("agents") or {})
        agents[agent.agent_id] = agent.model_dump(mode="json")
        payload["agents"] = agents
        self._save(payload)
        return agent

    def create_agent(
        self,
        *,
        name: str = "",
        title: str,
        role: str,
        created_by: str,
        persona_prompt: str = "",
        responsibilities: list[str] | None = None,
        permissions_profile: dict | None = None,
        inference_binding: dict | None = None,
        message_policy: dict | None = None,
        fallback_policy: dict | None = None,
        allowed_recipients: list[str] | None = None,
        agent_id: str | None = None,
    ) -> DurableAgentRecord:
        self.ensure_bootstrap_agents()
        if agent_id and self.get(agent_id) is not None:
            raise ValueError(f"Agent `{agent_id}` already exists.")
        record = DurableAgentRecord(
            agent_id=agent_id or DurableAgentRecord(title=title).agent_id,
            name=name,
            title=title,
            role=role,
            persona_prompt=persona_prompt,
            responsibilities=list(responsibilities or []),
            permissions_profile=dict(permissions_profile or {}),
            inference_binding=dict(inference_binding or {}),
            message_policy=dict(message_policy or {}),
            fallback_policy=dict(fallback_policy or {}),
            allowed_recipients=list(allowed_recipients or []),
            created_by=created_by,
        )
        return self.upsert(record)

    def update_agent(
        self,
        agent_id: str,
        *,
        patch: dict,
        updated_by: str,
        allow_system_update: bool = False,
    ) -> DurableAgentRecord:
        self.ensure_bootstrap_agents()
        current = self.get(agent_id)
        if current is None:
            raise KeyError(f"Unknown durable agent `{agent_id}`.")
        if current.created_by == "system" and not allow_system_update and updated_by != "system":
            raise PermissionError(f"Durable agent `{agent_id}` is system-managed and cannot be edited directly.")
        updated = current.model_copy(
            update={
                **dict(patch or {}),
                "updated_at": DurableAgentRecord(title=current.title).updated_at,
            }
        )
        return self.upsert(updated)

    def assign_task(
        self,
        task,
        *,
        agent_id: str,
        assigned_by: str,
        mode: str = "durable_agent",
        template_agent_id: str | None = None,
    ):
        agent = self.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown durable agent `{agent_id}`.")
        if agent.status not in {"active", "degraded"}:
            raise ValueError(f"Durable agent `{agent_id}` is not available for assignment.")
        updated_provenance = dict(getattr(task, "provenance", {}) or {})
        updated_provenance["assigned_by"] = assigned_by
        updated_provenance["assigned_agent_id"] = agent_id
        if template_agent_id:
            updated_provenance["assignment_template_agent_id"] = template_agent_id
        return task.model_copy(
            update={
                "assignee_agent_id": agent_id,
                "assignment_mode": mode,
                "assignment_template_agent_id": template_agent_id,
                "provenance": updated_provenance,
            }
        )

    def get(self, agent_id: str) -> DurableAgentRecord | None:
        payload = self._load()
        record = dict(payload.get("agents", {}).get(agent_id) or {})
        if not record:
            return None
        return DurableAgentRecord(**record)

    def list_agents(self) -> list[DurableAgentRecord]:
        payload = self._load()
        return sorted(
            [DurableAgentRecord(**item) for item in dict(payload.get("agents") or {}).values()],
            key=lambda agent: (agent.role, agent.title, agent.agent_id),
        )

    def choose_fallback(self, *, unavailable_agent_id: str, security_level: str = "normal") -> DurableAgentRecord | None:
        self.ensure_bootstrap_agents()
        current = self.get(unavailable_agent_id)
        if current is None:
            return self.get("fallback")
        fallback_id = str(current.fallback_policy.get("fallback_agent_id") or "").strip()
        if not fallback_id:
            return None
        fallback = self.get(fallback_id)
        if fallback is None or fallback.status not in {"active", "degraded"}:
            if unavailable_agent_id == "prime":
                return self.get("local") or self.get("fallback")
            return self.get("fallback")
        if security_level in {"sensitive", "secret", "enclave"} and fallback.agent_id != "local":
            return self.get("local")
        return fallback

    def _load(self) -> dict:
        if not self._state_path.exists():
            return {"agents": {}}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"agents": {}}

    def _save(self, payload: dict) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
