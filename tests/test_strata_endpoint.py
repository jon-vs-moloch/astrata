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

    def current_selection(self, runtime_key=None):
        return self._selections.get("default")

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

    def complete(self, *, base_url, request, thread_id=None, allow_degraded_fallback=False):
        system_text = request.messages[0].content if request.messages else ""
        if "reasoning-effort selector" in str(system_text).lower():
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
                return "HIGH"
            return "LOW"
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
        assert first.reasoning_effort == "low"
        assert second.reasoning_effort == "high"
        assert first.strategy_id == "single_pass"
        assert second.strategy_id == "single_pass"
        assert first.reasoning_effort_source == "auto_selector"
        assert second.reasoning_effort_source == "auto_selector"
        assert client.calls[0]["kind"] == "selector"
        assert client.calls[1]["kind"] == "chat"
        assert client.calls[1]["message_count"] == 2
        assert client.calls[2]["kind"] == "selector"
        assert client.calls[3]["kind"] == "chat"
        assert client.calls[3]["message_count"] == 4
        assert client.calls[1]["metadata"]["reasoning_effort"] == "low"
        assert client.calls[3]["metadata"]["reasoning_effort"] == "high"
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
        assert reply.reasoning_effort == "low"
        assert reply.requested_reasoning_effort == "auto"
        assert reply.reasoning_effort_source == "budget"
        assert reply.strategy_id == "single_pass"
        assert client.calls[-1]["metadata"]["response_budget"] == "instant"
        assert client.calls[-1]["metadata"]["max_tokens"] == 120


def test_strata_endpoint_service_accepts_forced_reasoning_effort():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        reply = service.chat(
            content="Explain the tradeoffs.",
            thread_id="forced-thread",
            reasoning_effort="high",
        )
        assert reply.reasoning_effort == "high"
        assert reply.reasoning_effort_source == "forced"
        assert client.calls[-1]["kind"] == "chat"
        assert client.calls[-1]["metadata"]["reasoning_effort"] == "high"


def test_strata_endpoint_service_can_update_prompt_config():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        updated = service.set_prompt(prompt_kind="default_system", value="Be concise.")
        assert updated.default_system_prompt == "Be concise."
        status = service.status()
        assert status["prompt_config"]["default_system_prompt"] == "Be concise."


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
        assert status["execution_plan"]["strategy"] == "single_pass"
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
        assert '"strategy_id": "single_pass"' in payload
        assert '"reasoning_effort": "low"' in payload


def test_strata_endpoint_service_uses_single_runtime_port():
    with TemporaryDirectory() as tmp:
        runtime = _FakeRuntimeManager()
        client = _FakeLocalClient()
        service = StrataEndpointService(
            state_path=Path(tmp) / "threads.json",
            runtime_manager=runtime,
            runtime_client=client,
        )
        assert service._runtime_port() == 8080
