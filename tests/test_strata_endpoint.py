from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.inference.contracts import BackendCapabilitySet
from astrata.local.strata_endpoint import StrataEndpointService


class _FakeSelection:
    def __init__(self, model_id="model-1", endpoint="http://127.0.0.1:8080/health"):
        self.model_id = model_id
        self.endpoint = endpoint
        self.backend_id = "llama_cpp"

    def model_dump(self, mode="json"):
        return {
            "model_id": self.model_id,
            "endpoint": self.endpoint,
            "backend_id": self.backend_id,
        }


class _FakeRuntimeManager:
    def __init__(self):
        self._selections = {}
        self.selected_calls = []

    def current_selection(self, runtime_key=None):
        if runtime_key is not None:
            return self._selections.get(runtime_key)
        return self._selections.get("persistent") or self._selections.get("default") or self._selections.get("fast")

    def list_selections(self):
        return list(self._selections.values())

    def health(self, *, runtime_key=None, config=None):
        if self.current_selection(runtime_key) is None:
            return None

        class Health:
            ok = True

        return Health()

    def start_managed(self, *, runtime_key="default", backend_id, model_id, profile_id="quiet", port=8080, activate=True):
        self._selections[runtime_key] = _FakeSelection(model_id=model_id, endpoint=f"http://127.0.0.1:{port}/health")

    def select_runtime(
        self,
        *,
        runtime_key="default",
        backend_id,
        model_id=None,
        mode="managed",
        profile_id=None,
        endpoint=None,
        metadata=None,
        activate=True,
    ):
        self.selected_calls.append(
            {
                "runtime_key": runtime_key,
                "backend_id": backend_id,
                "model_id": model_id,
                "mode": mode,
                "profile_id": profile_id,
                "endpoint": endpoint,
                "metadata": dict(metadata or {}),
                "activate": activate,
            }
        )
        self._selections[runtime_key] = _FakeSelection(model_id=model_id, endpoint=endpoint or "http://127.0.0.1:8080/health")
        return self._selections[runtime_key]

    def managed_status(self, runtime_key=None):
        selection = self.current_selection(runtime_key)
        if selection is None:
            return None

        class Status:
            running = True
            pid = 123
            endpoint = selection.endpoint
            detail = None

        return Status()

    def list_managed_statuses(self):
        statuses = {}
        for key in self._selections:
            statuses[key] = self.managed_status(key)
        return statuses

    def backend_capabilities(self, backend_id):
        return BackendCapabilitySet(backend_id=backend_id, ephemeral_sessions=True, managed_processes=True)

    def list_backend_capabilities(self):
        return [self.backend_capabilities("llama_cpp")]

    def backend(self, backend_id):
        if backend_id != "llama_cpp":
            return None

        class Backend:
            backend_id = "llama_cpp"

            def healthcheck(self, **kwargs):
                class Health:
                    ok = True
                    status = "healthy"
                    endpoint = "http://127.0.0.1:8080/health"
                    detail = "http_status=200"
                    metadata = {"backend_id": "llama_cpp"}

                return Health()

        return Backend()

    def model_registry(self):
        class Registry:
            def list_models(self):
                class Model:
                    role = "model"
                    model_id = "model-1"

                return [Model()]

            def get(self, model_id):
                if model_id == "model-1":
                    class Model:
                        role = "model"
                        model_id = "model-1"

                    return Model()
                return None

            def adopt(self, model_path):
                class Model:
                    role = "model"
                    model_id = str(model_path)

                return Model()

        return Registry()


class _FakeLocalClient:
    def __init__(self):
        self.calls = []
        self.empty_fast_once = False

    def complete(self, *, base_url, request, thread_id=None, allow_degraded_fallback=False):
        system_text = request.messages[0].content if request.messages else ""
        if "route selector" in str(system_text).lower():
            user_text = request.messages[-1].content if request.messages else ""
            self.calls.append(
                {
                    "base_url": base_url,
                    "thread_id": thread_id,
                    "allow_degraded_fallback": allow_degraded_fallback,
                    "message_count": len(request.messages),
                    "temperature": request.temperature,
                    "metadata": dict(request.metadata),
                    "kind": "selector",
                }
            )
            if "and then" in str(user_text).lower():
                return "PERSISTENT"
            return "FAST"
        self.calls.append(
            {
                "base_url": base_url,
                "thread_id": thread_id,
                "allow_degraded_fallback": allow_degraded_fallback,
                "message_count": len(request.messages),
                "temperature": request.temperature,
                "metadata": dict(request.metadata),
                "kind": "chat",
            }
        )
        if self.empty_fast_once and request.metadata.get("execution_mode") == "fast":
            self.empty_fast_once = False
            return ""
        return "assistant reply"


def test_strata_endpoint_service_persists_thread_messages():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        first = service.chat(content="Hello Astrata", thread_id="demo-thread")
        second = service.chat(content="And then?", thread_id="demo-thread")
        assert first.thread_id == "demo-thread"
        assert second.thread_id == "demo-thread"
        assert first.mode == "fast"
        assert second.mode == "persistent"
        assert first.strategy_id == "fast_then_persistent"
        assert second.strategy_id == "single_pass"
        assert first.runtime_key == "fast"
        assert second.runtime_key == "persistent"
        assert client.calls[0]["kind"] == "selector"
        assert client.calls[1]["kind"] == "chat"
        assert client.calls[1]["message_count"] == 2
        assert client.calls[2]["kind"] == "selector"
        assert client.calls[3]["kind"] == "chat"
        assert client.calls[3]["message_count"] == 4
        assert client.calls[1]["metadata"]["execution_mode"] == "fast"
        assert client.calls[3]["metadata"]["execution_mode"] == "persistent"
        status = service.status()
        assert status["thread_count"] == 1


def test_strata_endpoint_service_honors_instant_budget():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        reply = service.chat(
            content="Explain recursion in depth.",
            thread_id="budget-thread",
            response_budget="instant",
        )
        assert reply.mode == "fast"
        assert reply.initial_mode == "fast"
        assert reply.mode_source == "budget"
        assert reply.escalated is False
        assert reply.strategy_id == "fast_then_persistent"
        assert reply.runtime_key == "fast"
        assert client.calls[-1]["metadata"]["response_budget"] == "instant"
        assert client.calls[-1]["metadata"]["max_tokens"] == 80


def test_strata_endpoint_service_escalates_empty_fast_reply():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        client.empty_fast_once = True
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        reply = service.chat(
            content="Write a Python function that returns the sum of two integers.",
            thread_id="escalate-thread",
        )
        assert reply.initial_mode == "fast"
        assert reply.mode == "persistent"
        assert reply.mode_source == "self_routed"
        assert reply.escalated is True
        assert reply.strategy_id == "fast_then_persistent"
        assert reply.runtime_key == "persistent"
        assert client.calls[-2]["metadata"]["execution_mode"] == "fast"
        assert client.calls[-1]["metadata"]["execution_mode"] == "persistent"


def test_strata_endpoint_service_can_update_prompt_config():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        updated = service.set_prompt(prompt_kind="fast_system", value="Be concise.")
        assert updated.fast_system_prompt == "Be concise."
        status = service.status()
        assert status["prompt_config"]["fast_system_prompt"] == "Be concise."


def test_strata_endpoint_service_reports_endpoint_profile_and_backend_capabilities():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        status = service.status()
        assert status["endpoint_profile"]["endpoint_type"] == "agent_session"
        assert status["execution_plan"]["strategy"] == "fast_then_persistent"
        assert status["backend_capabilities"][0]["backend_id"] == "llama_cpp"


def test_strata_endpoint_service_records_strategy_id_in_thread_state():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        service.chat(content="Hello Astrata", thread_id="strategy-thread")
        payload = (Path(tmp) / "threads.json").read_text(encoding="utf-8")
        assert '"strategy_id": "fast_then_persistent"' in payload
        assert '"runtime_key": "fast"' in payload


def test_strata_endpoint_service_uses_single_runtime_port():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        assert service._runtime_port("default") == 8080


def test_strata_endpoint_service_adopts_existing_runtime_before_starting():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )

        endpoint = service._ensure_runtime(model_id="model-1")

        assert endpoint == "http://127.0.0.1:8080"
        assert runtime.selected_calls[-1]["mode"] == "external"
        assert runtime.selected_calls[-1]["metadata"]["adopted_existing_endpoint"] is True
