from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.comms.lanes import PrincipalMessageLane
from astrata.config.settings import load_settings
from astrata.memory import MemoryStore
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.storage.db import AstrataDatabase
from astrata.workers.runtime import WorkerRuntime, worker_id_for_route


class _CheapCliProvider(Provider):
    def __init__(self) -> None:
        self.last_request: CompletionRequest | None = None

    @property
    def name(self) -> str:
        return "cli"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return None

    def available_tools(self) -> list[str]:
        return ["kilocode", "gemini-cli"]

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.last_request = request
        return CompletionResponse(
            provider="cli",
            model=str(request.metadata.get("cli_tool") or "kilocode"),
            content=(
                '{"operator_response":"Delegated response from worker lane.","followup_tasks":[],'
                '"artifact":{"title":"Worker artifact","summary":"worker ok","confidence":0.8,"findings":[]}}'
            ),
            raw={"cli_tool": request.metadata.get("cli_tool")},
        )


def test_worker_runtime_executes_delegated_message_task():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = load_settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        route = {"provider": "cli", "cli_tool": "kilocode", "model": None, "reason": "preferred_cli_tool"}
        worker_id = worker_id_for_route(route)
        inbound = lane.send(
            sender="prime",
            recipient=worker_id,
            conversation_id=lane.default_conversation_id(worker_id),
            kind="delegation",
            intent="worker_delegation_request",
            payload={
                "delegation_kind": "message_task",
                "task_id": "task-1",
                "title": "Do the thing",
                "description": "Do the thing carefully.",
                "message": "Do the thing carefully.",
                "task_payload": {"completion_policy": {"type": "respond_or_execute"}},
                "approval": {
                    "mode": "explicit",
                    "required": True,
                    "approver": "parent_task",
                    "delegated_by": "prime",
                    "authority_chain": ["prime", "constitution"],
                    "self_approval_allowed": False,
                    "consensus_allowed": False,
                    "override_required_for_protected_write": False,
                },
                "route": route,
            },
            related_task_ids=["task-1"],
        )
        runtime = WorkerRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"cli": _CheapCliProvider()}),
        )
        result = runtime.handle_message(inbound)
        assert result.worker_id == "worker.kilocode"
        messages = db.list_records("communications")
        worker_results = [item for item in messages if item.get("intent") == "worker_delegation_result"]
        assert worker_results
        assert worker_results[-1]["recipient"] == "astrata"
        assert worker_results[-1]["payload"]["route"]["cli_tool"] == "kilocode"
        prompt = runtime._delegated_message_task_prompt(dict(inbound.payload or {}))  # noqa: SLF001
        assert "approval" in prompt[-1].content
        assert "parent_task" in prompt[-1].content
        resolved = lane.get_message(inbound.communication_id)
        assert resolved is not None
        assert resolved.status == "resolved"


def test_worker_runtime_scopes_worker_id_to_model_variant():
    route = {
        "provider": "cli",
        "cli_tool": "gemini-cli",
        "model": "gemini-2.5-flash",
        "reason": "preferred_cli_tool",
    }
    assert worker_id_for_route(route) == "worker.gemini-cli.gemini-2-5-flash"


def test_worker_runtime_attaches_projected_memory_context():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = load_settings(root)
        store = MemoryStore(settings.paths.data_dir / "memory.db")
        store.create_or_update_page(
            slug="do-the-thing",
            title="Do the Thing",
            body="A standard operating note about doing the thing carefully.",
            summary="Thing procedure.",
            summary_public="A procedure note exists for doing the thing.",
            tags=["thing"],
            visibility="shared",
            confidentiality="normal",
        )
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        route = {"provider": "cli", "cli_tool": "kilocode", "model": None, "reason": "preferred_cli_tool"}
        worker_id = worker_id_for_route(route)
        inbound = lane.send(
            sender="prime",
            recipient=worker_id,
            conversation_id=lane.default_conversation_id(worker_id),
            kind="delegation",
            intent="worker_delegation_request",
            payload={
                "delegation_kind": "message_task",
                "task_id": "task-1",
                "title": "Do the thing",
                "description": "Do the thing carefully.",
                "message": "Do the thing carefully.",
                "task_payload": {"completion_policy": {"type": "respond_or_execute"}},
                "route": route,
            },
            related_task_ids=["task-1"],
        )
        provider = _CheapCliProvider()
        runtime = WorkerRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"cli": provider}),
        )

        runtime.handle_message(inbound)

        assert provider.last_request is not None
        assert provider.last_request.metadata["memory_context"] == [
            "[public] Do the Thing: A procedure note exists for doing the thing."
        ]
