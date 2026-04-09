from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.local.strata_endpoint import StrataEndpointReply
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.storage.db import AstrataDatabase


def _settings(root: Path) -> Settings:
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=(),
            model_install_dir=data_dir / "models",
        ),
    )


class _PrimeProvider(Provider):
    @property
    def name(self) -> str:
        return "codex"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return "gpt-5.4"

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(provider="codex", model="gpt-5.4", content="Prime reply.")


class _NoCliProvider(Provider):
    @property
    def name(self) -> str:
        return "cli"

    def is_configured(self) -> bool:
        return False

    def default_model(self) -> str | None:
        return None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise RuntimeError("unconfigured")


class _FakeLocalEndpoint:
    def chat(self, *, content: str, thread_id: str | None = None, **_: object) -> StrataEndpointReply:
        return StrataEndpointReply(
            thread_id=thread_id or "lane:local:default",
            content="Local reply.",
            model_id="local-model",
            mode="fast",
            initial_mode="fast",
            mode_source="forced",
            escalated=False,
            degraded_fallback=False,
        )


def test_lane_runtime_replies_directly_in_conversation():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        inbound = lane.send(
            sender="principal",
            recipient="prime",
            conversation_id="lane:prime:default",
            kind="request",
            intent="principal_message",
            payload={"message": "Hello Prime"},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _PrimeProvider(), "cli": _NoCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )
        result = runtime.handle_message(inbound)
        assert result.action == "direct_reply"
        communications = db.list_records("communications")
        replies = [item for item in communications if item.get("sender") == "prime"]
        assert replies
        assert replies[-1]["conversation_id"] == "lane:prime:default"
        resolved = lane.get_message(inbound.communication_id)
        assert resolved is not None
        assert resolved.status == "resolved"


def test_lane_runtime_materializes_work_for_execution_requests():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        inbound = lane.send(
            sender="principal",
            recipient="prime",
            conversation_id="lane:prime:default",
            kind="request",
            intent="principal_message",
            payload={"message": "Implement the intake path in intake.py and review the spec."},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _PrimeProvider(), "cli": _NoCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )
        result = runtime.handle_message(inbound)
        assert result.action == "materialize_work"
        tasks = db.list_records("tasks")
        assert tasks
        assert any(task.get("provenance", {}).get("source_communication_id") == inbound.communication_id for task in tasks)
        replies = [item for item in db.list_records("communications") if item.get("sender") == "prime"]
        assert replies
        assert "governed work" in str(replies[-1].get("payload", {}).get("message") or "").lower()


def test_lane_runtime_uses_local_endpoint_for_local_lane():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        inbound = lane.send(
            sender="principal",
            recipient="local",
            conversation_id="lane:local:default",
            kind="request",
            intent="principal_message",
            payload={"message": "What do you think?"},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _PrimeProvider(), "cli": _NoCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )
        result = runtime.handle_message(inbound)
        assert result.action == "direct_reply"
        replies = [item for item in db.list_records("communications") if item.get("sender") == "local"]
        assert replies
        assert replies[-1]["payload"]["message"] == "Local reply."
