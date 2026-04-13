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


class _ReceptionCliProvider(Provider):
    def __init__(self) -> None:
        self.last_request: CompletionRequest | None = None

    @property
    def name(self) -> str:
        return "cli"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.last_request = request
        return CompletionResponse(provider="cli", model="", content="Reception reply.")


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


def test_lane_runtime_attaches_projected_memory_context_for_remote_reply():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        store = MemoryStore(settings.paths.data_dir / "memory.db")
        store.create_or_update_page(
            slug="astrata",
            title="Astrata",
            body="Astrata is a local-first coordination system.",
            summary="Local-first coordination system.",
            summary_public="Astrata is a coordination system.",
            tags=["astrata", "coordination"],
            visibility="shared",
            confidentiality="normal",
        )
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        lane = PrincipalMessageLane(db=db)
        inbound = lane.send(
            sender="principal",
            recipient="prime",
            conversation_id="lane:prime:default",
            kind="request",
            intent="principal_message",
            payload={"message": "Tell me about Astrata."},
        )
        provider = _PrimeProvider()
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": provider, "cli": _NoCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )

        runtime.handle_message(inbound)

        assert provider.last_request is not None
        assert provider.last_request.metadata["memory_context"] == [
            "[public] Astrata: Astrata is a coordination system."
        ]
        assert any(
            "Relevant memory context below is already projected" in str(message.content or "")
            for message in provider.last_request.messages
        )


def test_lane_runtime_fails_over_to_reception_when_prime_is_unavailable():
    class _UnavailablePrimeProvider(_PrimeProvider):
        def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise RuntimeError("network unavailable")

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
            payload={"message": "Can you handle this while Prime is away?"},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _UnavailablePrimeProvider(), "cli": _ReceptionCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )

        result = runtime.handle_message(inbound)

        assert result.action == "failover_reply"
        replies = [item for item in db.list_records("communications") if item.get("sender") == "reception"]
        assert replies
        assert replies[-1]["payload"]["message"] == "Reception reply."
        assert replies[-1]["payload"]["handoff_occurred"] is True
        assert replies[-1]["payload"]["intended_recipient"] == "prime"
        assert replies[-1]["payload"]["responding_agent"] == "reception"
        assert replies[-1]["payload"]["response_frame"] == "fallback_continuity"


def test_lane_runtime_falls_back_to_local_when_reception_is_unavailable():
    class _UnavailablePrimeProvider(_PrimeProvider):
        def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise RuntimeError("network unavailable")

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
            payload={"message": "Can someone else pick this up?"},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _UnavailablePrimeProvider(), "cli": _NoCliProvider()}),
            local_endpoint=_FakeLocalEndpoint(),
        )

        result = runtime.handle_message(inbound)

        assert result.action == "failover_reply"
        replies = [item for item in db.list_records("communications") if item.get("sender") == "local"]
        assert replies
        assert replies[-1]["payload"]["message"] == "Local reply."
        assert replies[-1]["payload"]["handoff_occurred"] is True
        assert replies[-1]["payload"]["intended_recipient"] == "prime"
        assert replies[-1]["payload"]["responding_agent"] == "local"


def test_lane_runtime_uses_bounded_fallback_when_prime_and_local_are_unavailable():
    class _UnavailablePrimeProvider(_PrimeProvider):
        def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise RuntimeError("network unavailable")

    class _BrokenLocalEndpoint:
        def chat(self, *, content: str, thread_id: str | None = None, **_: object) -> StrataEndpointReply:
            raise RuntimeError("local offline")

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
            payload={"message": "Hello?", "security_level": "normal"},
        )
        runtime = LaneRuntime(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _UnavailablePrimeProvider(), "cli": _NoCliProvider()}),
            local_endpoint=_BrokenLocalEndpoint(),
        )

        result = runtime.handle_message(inbound)

        assert result.action == "degraded_reply"
        replies = [item for item in db.list_records("communications") if item.get("sender") == "fallback"]
        assert replies
        assert "Prime is unavailable" in str(replies[-1]["payload"]["message"])
