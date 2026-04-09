from astrata.eval.cyclone import (
    CycloneExperiment,
    CycloneTask,
    _parse_steering_decision,
    load_tasks,
    parse_route_spec,
)
from astrata.local.models.registry import LocalModelRegistry


def test_parse_route_spec_supports_local_cli_and_provider():
    assert parse_route_spec("local:auto-smallest").provider == "local"
    cli = parse_route_spec("cli:codex-cli:gpt-5.4-mini")
    assert cli.provider == "cli"
    assert cli.cli_tool == "codex-cli"
    assert cli.model == "gpt-5.4-mini"
    codex = parse_route_spec("codex:gpt-5.4")
    assert codex.provider == "codex"
    assert codex.model == "gpt-5.4"


def test_parse_steering_decision_falls_back_to_fail():
    decision = _parse_steering_decision(
        '{"verdict":"maybe","rationale":"Needs work","priorities":["too long"],"edit_brief":"Shorten it."}'
    )
    assert decision.verdict == "FAIL"
    assert decision.priorities == ["too long"]


def test_load_tasks_uses_defaults_when_no_file():
    tasks = load_tasks(None, limit=2)
    assert len(tasks) == 2
    assert all(isinstance(task, CycloneTask) for task in tasks)


def test_cyclone_resolves_auto_local_models_without_uncensored_bias():
    registry = LocalModelRegistry()
    registry.adopt("/tmp/Gemma-4-E2B-it-Q4_K_M.gguf")
    registry.adopt("/tmp/Qwen3.5-9B.Q4_K_M.gguf")
    registry.adopt("/tmp/Gemma-4-E4B-Uncensored.gguf")

    class FakeRuntime:
        def __init__(self):
            self._registry = registry

        def model_registry(self):
            return self._registry

    experiment = CycloneExperiment.__new__(CycloneExperiment)
    experiment._local_runtime = FakeRuntime()
    experiment._local_models_discovered = True

    small = CycloneExperiment._pick_auto_local_model(experiment, "auto-smallest")
    big = CycloneExperiment._pick_auto_local_model(experiment, "auto-largest")
    assert "Uncensored" not in small.display_name
    assert "Uncensored" not in big.display_name
