"""Inbound communication intake for turning messages into governed work."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, Field

from astrata.governance.documents import GovernanceBundle, load_governance_bundle
from astrata.comms.lanes import OperatorMessageLane
from astrata.records.communications import CommunicationRecord
from astrata.records.models import ArtifactRecord
from astrata.records.models import TaskRecord
from astrata.storage.db import AstrataDatabase


class RequestSpec(BaseModel):
    source_communication_id: str
    sender: str
    recipient: str
    intent: str
    summary: str
    raw_message: str
    needs_clarification: bool = False
    request_kind: str = "execution"
    delta_kind: str = "spec_vs_reality"
    delta_summary: str = ""
    domains: list[str] = Field(default_factory=list)
    authority_chain: list[str] = Field(default_factory=list)
    supporting_specs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskProposal(BaseModel):
    title: str
    description: str
    priority: int = 3
    urgency: int = 2
    risk: str = "low"
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    route_preferences: dict[str, Any] = Field(default_factory=dict)


class MessageIntake:
    """Derive request specs and task proposals from inbound communications."""

    def __init__(self, *, project_root: Path) -> None:
        self._bundle: GovernanceBundle = load_governance_bundle(project_root)

    def build_request_spec(self, message: CommunicationRecord) -> RequestSpec:
        raw_message = str(message.payload.get("message") or "").strip()
        summary = _summarize_message(raw_message)
        request_kind = _classify_request_kind(raw_message)
        delta = _infer_delta(raw_message, request_kind=request_kind, summary=summary)
        domains = _classify_domains(raw_message)
        supporting_specs = [name for name, doc in self._bundle.planning_docs.items() if doc.exists]
        return RequestSpec(
            source_communication_id=message.communication_id,
            sender=message.sender,
            recipient=message.recipient,
            intent=message.intent or message.kind,
            summary=summary,
            raw_message=raw_message,
            needs_clarification=_needs_clarification(raw_message),
            request_kind=request_kind,
            delta_kind=delta["delta_kind"],
            delta_summary=delta["delta_summary"],
            domains=domains,
            authority_chain=["user", "constitution"],
            supporting_specs=supporting_specs,
            metadata={
                "channel": message.channel,
                "kind": message.kind,
                "conversation_id": message.conversation_id,
                "target_lane": message.recipient,
            },
        )

    def propose_tasks(self, request_spec: RequestSpec) -> list[TaskProposal]:
        if request_spec.needs_clarification:
            return [
                TaskProposal(
                    title="Clarify inbound operator request",
                    description=(
                        "Ask a narrower follow-up question before creating execution work from the inbound operator message."
                    ),
                    priority=4,
                    urgency=3,
                    risk="low",
                    success_criteria={"clarify": True},
                    completion_policy={"type": "request_clarification"},
                    provenance={
                        "source": "message_intake",
                        "source_communication_id": request_spec.source_communication_id,
                        "request_spec_intent": request_spec.intent,
                        "request_kind": request_spec.request_kind,
                        "delta_kind": request_spec.delta_kind,
                        "delta_summary": request_spec.delta_summary,
                        "source_conversation_id": request_spec.metadata.get("conversation_id"),
                        "target_lane": request_spec.metadata.get("target_lane"),
                    },
                    route_preferences=_route_preferences_for_request(request_spec),
                )
            ]
        proposals: list[TaskProposal] = []
        fragments = _task_fragments(request_spec.raw_message)
        if request_spec.request_kind == "spec_hardening":
            for fragment in fragments[:2]:
                proposals.append(
                    self._proposal_from_fragment(
                        request_spec,
                        fragment,
                        completion_type="review_or_rewrite_spec",
                        title_prefix="Spec",
                    )
                )
        elif request_spec.request_kind == "review":
            for fragment in fragments[:2]:
                proposals.append(
                    self._proposal_from_fragment(
                        request_spec,
                        fragment,
                        completion_type="review_or_audit",
                        title_prefix="Review",
                    )
                )
        else:
            for fragment in fragments[:2]:
                proposals.append(
                    self._proposal_from_fragment(
                        request_spec,
                        fragment,
                        completion_type="respond_or_execute",
                        title_prefix="Execute",
                    )
                )
            if "communication" in request_spec.domains or "operator" in request_spec.domains:
                proposals.append(
                    TaskProposal(
                        title="Review communication/task translation path",
                        description=(
                            "Inspect whether the inbound operator request should also improve Astrata's communication-to-task intake path."
                        ),
                        priority=4,
                        urgency=2,
                        risk="low",
                        success_criteria={"intake_path_reviewed": True},
                        completion_policy={"type": "review_or_execute"},
                        provenance={
                            "source": "message_intake",
                            "source_communication_id": request_spec.source_communication_id,
                            "request_spec_intent": request_spec.intent,
                            "request_kind": request_spec.request_kind,
                            "delta_kind": request_spec.delta_kind,
                            "delta_summary": request_spec.delta_summary,
                            "source_conversation_id": request_spec.metadata.get("conversation_id"),
                            "target_lane": request_spec.metadata.get("target_lane"),
                            "supporting_specs": request_spec.supporting_specs,
                            "domains": request_spec.domains,
                        },
                        route_preferences=_route_preferences_for_request(request_spec),
                    )
                )
        return _dedupe_proposals(proposals)

    def materialize_task(self, proposal: TaskProposal) -> TaskRecord:
        return TaskRecord(
            title=proposal.title,
            description=proposal.description,
            priority=proposal.priority,
            urgency=proposal.urgency,
            risk=proposal.risk,
            provenance=proposal.provenance,
            success_criteria=proposal.success_criteria,
            completion_policy={
                **proposal.completion_policy,
                "route_preferences": proposal.route_preferences,
            },
        )

    def _proposal_from_fragment(
        self,
        request_spec: RequestSpec,
        fragment: str,
        *,
        completion_type: str,
        title_prefix: str,
    ) -> TaskProposal:
        description = fragment.strip() or request_spec.summary
        return TaskProposal(
            title=f"{title_prefix}: {_title_from_summary(description)}",
            description=description,
            priority=5,
            urgency=3,
            risk="low",
            success_criteria={"message_addressed": True, "request_kind": request_spec.request_kind},
            completion_policy={"type": completion_type},
            provenance={
                "source": "message_intake",
                "source_communication_id": request_spec.source_communication_id,
                "request_spec_intent": request_spec.intent,
                "request_kind": request_spec.request_kind,
                "delta_kind": request_spec.delta_kind,
                "delta_summary": request_spec.delta_summary,
                "source_conversation_id": request_spec.metadata.get("conversation_id"),
                "target_lane": request_spec.metadata.get("target_lane"),
                "supporting_specs": request_spec.supporting_specs,
                "domains": request_spec.domains,
            },
            route_preferences=_route_preferences_for_request(request_spec),
        )


def normalize_derived_task_proposal(
    *,
    title: str,
    description: str,
    parent_provenance: dict[str, Any] | None = None,
    suggested_completion_type: str | None = None,
    priority: int = 4,
    urgency: int = 2,
    risk: str = "low",
    success_criteria: dict[str, Any] | None = None,
    route_preferences: dict[str, Any] | None = None,
    delta_kind: str | None = None,
    delta_summary: str | None = None,
) -> TaskProposal:
    normalized_title = title.strip() or _title_from_summary(description)
    normalized_description = description.strip() or normalized_title
    request_kind = _completion_type_to_request_kind(suggested_completion_type) or _classify_request_kind(
        f"{normalized_title}. {normalized_description}"
    )
    inferred_delta = _infer_delta(
        f"{normalized_title}. {normalized_description}",
        request_kind=request_kind,
        summary=normalized_description,
    )
    normalized_route_preferences = (
        dict(route_preferences)
        if route_preferences
        else _route_preferences_for_kind(request_kind=request_kind, needs_clarification=False)
    )
    return TaskProposal(
        title=normalized_title,
        description=normalized_description,
        priority=priority,
        urgency=urgency,
        risk=risk,
        success_criteria=dict(success_criteria or {"message_addressed": True}),
        completion_policy={"type": _completion_type_for_request_kind(request_kind)},
        provenance={
            **dict(parent_provenance or {}),
            "derived_request_kind": request_kind,
            "delta_kind": delta_kind or inferred_delta["delta_kind"],
            "delta_summary": delta_summary or inferred_delta["delta_summary"],
            "domains": _classify_domains(f"{normalized_title}. {normalized_description}"),
        },
        route_preferences=normalized_route_preferences,
    )


def process_inbound_messages(
    *,
    db: AstrataDatabase,
    project_root: Path,
    recipient: str = "astrata",
    limit: int = 5,
) -> list[dict[str, Any]]:
    lane = OperatorMessageLane(db=db)
    intake = MessageIntake(project_root=project_root)
    messages = lane.list_messages(recipient=recipient, include_acknowledged=False)[: max(1, limit)]
    created_tasks = []
    for message in messages:
        materialized = materialize_inbound_message(
            db=db,
            intake=intake,
            message=message,
        )
        lane.acknowledge(message.communication_id)
        created_tasks.append(materialized)
    return created_tasks


def materialize_inbound_message(
    *,
    db: AstrataDatabase,
    intake: MessageIntake,
    message: CommunicationRecord,
) -> dict[str, Any]:
    request_spec = intake.build_request_spec(message)
    proposals = intake.propose_tasks(request_spec)
    request_spec_artifact = ArtifactRecord(
        artifact_type="request_spec",
        title=f"Request spec: {request_spec.summary}",
        description="Durable interpreted intent derived from an inbound communication.",
        content_summary=request_spec.model_dump_json(indent=2),
        provenance={
            "source": "message_intake",
            "source_communication_id": request_spec.source_communication_id,
        },
    )
    db.upsert_artifact(request_spec_artifact)
    tasks = []
    for proposal in proposals:
        task = intake.materialize_task(proposal)
        db.upsert_task(task)
        tasks.append(task.model_dump(mode="json"))
    proposal_artifact = ArtifactRecord(
        artifact_type="task_proposal_bundle",
        title=f"Task proposals: {request_spec.summary}",
        description="Durable task proposals derived from a request spec.",
        content_summary=json.dumps(
            {
                "request_spec": request_spec.model_dump(mode="json"),
                "task_proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            },
            indent=2,
        ),
        provenance={
            "source": "message_intake",
            "source_communication_id": request_spec.source_communication_id,
            "request_spec_artifact_id": request_spec_artifact.artifact_id,
        },
    )
    db.upsert_artifact(proposal_artifact)
    return {
        "message": message.model_dump(mode="json"),
        "request_spec": request_spec.model_dump(mode="json"),
        "task_proposals": [proposal.model_dump(mode="json") for proposal in proposals],
        "tasks": tasks,
        "request_spec_artifact": request_spec_artifact.model_dump(mode="json"),
        "task_proposal_artifact": proposal_artifact.model_dump(mode="json"),
    }


def _summarize_message(message: str) -> str:
    collapsed = " ".join(message.split())
    if not collapsed:
        return "Review the inbound operator message and determine the next bounded action."
    first_sentence = collapsed.split(".", 1)[0].strip()
    return first_sentence or collapsed[:160]


def _title_from_summary(summary: str) -> str:
    cleaned = summary.strip().rstrip(".")
    if not cleaned:
        return "Process inbound operator request"
    if len(cleaned) <= 72:
        return cleaned
    return cleaned[:69].rstrip() + "..."


def _needs_clarification(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return True
    if lowered in {"hello", "hello there", "hi", "hey", "yo", "sup"}:
        return True
    if any(
        lowered.startswith(prefix)
        for prefix in ("hello ", "hi ", "hey ", "yo ")
    ) and len(lowered.split()) <= 4:
        return True
    return len(lowered.split()) <= 2


def _classify_request_kind(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ("spec", "constitution", "doc", "document", "docs")):
        return "spec_hardening"
    if any(token in lowered for token in ("review", "audit", "inspect", "check")):
        return "review"
    return "execution"


def _infer_delta(message: str, *, request_kind: str, summary: str) -> dict[str, str]:
    lowered = message.lower()
    spec_tokens = ("spec", "constitution", "doc", "docs", "document")
    if request_kind == "spec_hardening":
        return {
            "delta_kind": "input_vs_spec",
            "delta_summary": f"Bring governing docs into alignment with operator intent: {summary}",
        }
    if request_kind == "review" and any(token in lowered for token in spec_tokens):
        return {
            "delta_kind": "input_vs_spec",
            "delta_summary": f"Review whether governing docs reflect the requested intent: {summary}",
        }
    return {
        "delta_kind": "spec_vs_reality",
        "delta_summary": f"Bring system behavior or implementation closer to the governing shape: {summary}",
    }


def _classify_domains(message: str) -> list[str]:
    lowered = message.lower()
    domains: list[str] = []
    if any(token in lowered for token in ("message", "inbox", "comms", "communication", "operator")):
        domains.append("communication")
    if any(token in lowered for token in ("task", "queue", "scheduler", "priority")):
        domains.append("tasking")
    if any(token in lowered for token in ("spec", "constitution", "doc", "docs")):
        domains.append("governance")
    if any(token in lowered for token in ("build", "implement", "wire", "create", "fix", "strengthen")):
        domains.append("implementation")
    return domains


def _task_fragments(message: str) -> list[str]:
    collapsed = " ".join(message.split())
    if not collapsed:
        return ["Review the inbound operator message and determine the next bounded action."]
    parts = [
        part.strip(" ,.")
        for part in re.split(r"\b(?:and|then)\b|[.;]", collapsed, maxsplit=3)
        if part.strip(" ,.")
    ]
    return parts or [collapsed]


def _dedupe_proposals(proposals: list[TaskProposal]) -> list[TaskProposal]:
    deduped: list[TaskProposal] = []
    seen: set[tuple[str, str]] = set()
    for proposal in proposals:
        key = (proposal.title.strip().lower(), proposal.description.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(proposal)
    return deduped


def _route_preferences_for_request(request_spec: RequestSpec) -> dict[str, Any]:
    return _route_preferences_for_kind(
        request_kind=request_spec.request_kind,
        needs_clarification=request_spec.needs_clarification,
    )


def _route_preferences_for_kind(*, request_kind: str, needs_clarification: bool) -> dict[str, Any]:
    if needs_clarification:
        return {}
    if request_kind in {"execution", "review", "spec_hardening"}:
        return {
            "preferred_cli_tools": ["kilocode", "gemini-cli", "claude-code"],
            "avoided_providers": [],
            "preferred_providers": [],
        }
    return {}


def _completion_type_for_request_kind(request_kind: str) -> str:
    if request_kind == "spec_hardening":
        return "review_or_rewrite_spec"
    if request_kind == "review":
        return "review_or_audit"
    return "respond_or_execute"


def _completion_type_to_request_kind(completion_type: str | None) -> str | None:
    normalized = str(completion_type or "").strip().lower()
    if normalized == "request_clarification":
        return "execution"
    if normalized == "review_or_rewrite_spec":
        return "spec_hardening"
    if normalized in {"review_or_audit", "review_or_execute"}:
        return "review"
    if normalized == "respond_or_execute":
        return "execution"
    return None
