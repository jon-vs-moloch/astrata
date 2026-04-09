"""Procedure registry for durable self-hosted execution patterns."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ProcedureCapability = Literal["basic", "strong", "expert"]


class ProcedureVariantTemplate(BaseModel):
    variant_id: str
    title: str
    description: str = ""
    min_capability: ProcedureCapability = "basic"
    preferred_providers: list[str] = Field(default_factory=list)
    avoided_providers: list[str] = Field(default_factory=list)
    preferred_cli_tools: list[str] = Field(default_factory=list)
    avoided_cli_tools: list[str] = Field(default_factory=list)
    force_fallback_only: bool = False
    execution_mode: Literal["careful", "shortcut"] = "careful"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureTemplate(BaseModel):
    procedure_id: str
    title: str
    description: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    default_variant_id: str
    variants: list[ProcedureVariantTemplate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def variant_map(self) -> dict[str, ProcedureVariantTemplate]:
        return {variant.variant_id: variant for variant in self.variants}


class ResolvedProcedure(BaseModel):
    procedure: ProcedureTemplate
    variant: ProcedureVariantTemplate
    requested_variant_id: str | None = None
    actor_capability: ProcedureCapability = "basic"
    fallback_from_variant_id: str | None = None

    @property
    def procedure_id(self) -> str:
        return self.procedure.procedure_id

    @property
    def variant_id(self) -> str:
        return self.variant.variant_id


class ProcedureRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, ProcedureTemplate] = {}

    def register(self, template: ProcedureTemplate) -> None:
        self._templates[template.procedure_id] = template

    def get(self, procedure_id: str) -> ProcedureTemplate | None:
        return self._templates.get(procedure_id)

    def list_ids(self) -> list[str]:
        return sorted(self._templates)

    def resolve(
        self,
        procedure_id: str,
        *,
        actor_capability: ProcedureCapability = "basic",
        requested_variant_id: str | None = None,
    ) -> ResolvedProcedure:
        template = self.get(procedure_id)
        if template is None:
            raise KeyError(f"Unknown procedure `{procedure_id}`")
        variants = template.variant_map()
        requested_id = requested_variant_id or template.default_variant_id
        requested_variant = variants.get(requested_id)
        if requested_variant is None:
            requested_id = template.default_variant_id
            requested_variant = variants[requested_id]
        if _capability_rank(actor_capability) >= _capability_rank(requested_variant.min_capability):
            return ResolvedProcedure(
                procedure=template,
                variant=requested_variant,
                requested_variant_id=requested_variant_id,
                actor_capability=actor_capability,
            )
        safe_variant = variants.get(template.default_variant_id, requested_variant)
        return ResolvedProcedure(
            procedure=template,
            variant=safe_variant,
            requested_variant_id=requested_variant_id,
            actor_capability=actor_capability,
            fallback_from_variant_id=requested_variant.variant_id,
        )


def build_default_procedure_registry() -> ProcedureRegistry:
    registry = ProcedureRegistry()
    registry.register(
        ProcedureTemplate(
            procedure_id="loop0-bounded-file-generation",
            title="Loop0 Bounded File Generation",
            description=(
                "Generate or strengthen a tightly bounded set of repo files while preserving "
                "legible evidence about the route and procedure variant used."
            ),
            expected_outputs=["code_artifact", "route_evidence"],
            default_variant_id="careful_patch",
            variants=[
                ProcedureVariantTemplate(
                    variant_id="careful_patch",
                    title="Careful Patch",
                    description=(
                        "Use the slow, legible path. Favor explicit intermediate reasoning and "
                        "bounded outputs that weaker actors can reliably produce."
                    ),
                    min_capability="basic",
                    execution_mode="careful",
                    preferred_cli_tools=["kilocode", "gemini-cli", "claude-code"],
                    avoided_providers=["codex"],
                    metadata={"shortcut_allowed": False},
                ),
                ProcedureVariantTemplate(
                    variant_id="direct_patch",
                    title="Direct Patch",
                    description=(
                        "Allow stronger actors to skip fine-grained intermediate steps and produce "
                        "the final bounded patch directly while preserving reusable evidence."
                    ),
                    min_capability="expert",
                    execution_mode="shortcut",
                    preferred_providers=["codex", "openai", "google"],
                    preferred_cli_tools=["codex-cli", "claude-code"],
                    metadata={"shortcut_allowed": True, "capture_shortcut_candidate": True},
                ),
                ProcedureVariantTemplate(
                    variant_id="fallback_patch",
                    title="Fallback Patch",
                    description="Use deterministic fallback content when routed inference is unavailable.",
                    min_capability="basic",
                    execution_mode="careful",
                    force_fallback_only=True,
                    metadata={"shortcut_allowed": False, "fallback_only": True},
                ),
            ],
            metadata={"task_class": "coding"},
        )
    )
    registry.register(
        ProcedureTemplate(
            procedure_id="message-task-bounded-file-generation",
            title="Message Task Bounded File Generation",
            description=(
                "Execute inbound bounded implementation work while preserving the safe-path option "
                "for weaker actors and a shortcut path for stronger ones."
            ),
            expected_outputs=["code_artifact", "principal_notice"],
            default_variant_id="careful_execution",
            variants=[
                ProcedureVariantTemplate(
                    variant_id="careful_execution",
                    title="Careful Execution",
                    description="Conservative execution path for bounded principal-derived implementation tasks.",
                    min_capability="basic",
                    execution_mode="careful",
                    preferred_cli_tools=["kilocode", "gemini-cli"],
                    metadata={"shortcut_allowed": False},
                ),
                ProcedureVariantTemplate(
                    variant_id="direct_execution",
                    title="Direct Execution",
                    description="Permit strong actors to resolve the bounded task directly and then cash out the shortcut used.",
                    min_capability="strong",
                    execution_mode="shortcut",
                    preferred_providers=["codex", "openai", "google"],
                    preferred_cli_tools=["codex-cli", "claude-code"],
                    metadata={"shortcut_allowed": True, "capture_shortcut_candidate": True},
                ),
            ],
            metadata={"task_class": "execution"},
        )
    )
    registry.register(
        ProcedureTemplate(
            procedure_id="task-decomposition",
            title="Task Decomposition",
            description=(
                "Break a non-oneshottable task into bounded, dependency-aware leaf work and "
                "preserve the resulting workflow structure as a reusable draft procedure candidate."
            ),
            expected_outputs=["subtask_dag", "draft_procedure_candidate"],
            default_variant_id="careful_decomposition",
            variants=[
                ProcedureVariantTemplate(
                    variant_id="careful_decomposition",
                    title="Careful Decomposition",
                    description="Conservative decomposition path that favors explicit leaf tasks and clear dependency edges.",
                    min_capability="basic",
                    execution_mode="careful",
                    preferred_cli_tools=["kilocode", "gemini-cli"],
                    metadata={"shortcut_allowed": False, "preserve_workflow": True},
                ),
                ProcedureVariantTemplate(
                    variant_id="direct_decomposition",
                    title="Direct Decomposition",
                    description="Permit stronger actors to produce a compact leaf-task DAG directly and capture the shortcut used.",
                    min_capability="strong",
                    execution_mode="shortcut",
                    preferred_providers=["codex", "openai", "google"],
                    preferred_cli_tools=["codex-cli", "claude-code", "gemini-cli"],
                    metadata={"shortcut_allowed": True, "capture_shortcut_candidate": True, "preserve_workflow": True},
                ),
            ],
            metadata={"task_class": "decomposition"},
        )
    )
    return registry


def infer_actor_capability(*, provider: str | None = None, cli_tool: str | None = None) -> ProcedureCapability:
    normalized_provider = str(provider or "").strip().lower()
    normalized_tool = str(cli_tool or "").strip().lower()
    if normalized_provider == "codex" or normalized_tool in {"codex-cli", "claude-code"}:
        return "expert"
    if normalized_provider in {"openai", "google", "anthropic"} or normalized_tool == "gemini-cli":
        return "strong"
    return "basic"


def _capability_rank(capability: ProcedureCapability) -> int:
    return {"basic": 0, "strong": 1, "expert": 2}[capability]


__all__ = [
    "ProcedureCapability",
    "ProcedureRegistry",
    "ProcedureTemplate",
    "ProcedureVariantTemplate",
    "ResolvedProcedure",
    "build_default_procedure_registry",
    "infer_actor_capability",
]
