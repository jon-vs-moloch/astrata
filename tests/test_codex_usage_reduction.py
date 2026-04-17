from astrata.config.settings import load_settings
from astrata.providers.base import CompletionRequest
from astrata.procedures.registry import build_default_procedure_registry
from astrata.providers.cli import CliProvider
from astrata.providers.codex_direct import CodexDirectProvider


def test_codex_direct_defaults_to_heavy_model(monkeypatch):
    monkeypatch.delenv("ASTRATA_CODEX_DIRECT_MODEL", raising=False)
    monkeypatch.delenv("ASTRATA_CODEX_MODEL", raising=False)

    provider = CodexDirectProvider()

    assert provider.default_model() == "gpt-5.4"


def test_cli_provider_deprioritizes_codex_and_defaults_codex_model_to_heavy(monkeypatch):
    monkeypatch.delenv("ASTRATA_CODEX_CLI_MODEL", raising=False)
    monkeypatch.delenv("ASTRATA_CODEX_MODEL", raising=False)
    provider = CliProvider()
    provider._tool_is_usable = lambda tool: tool in {"codex-cli", "kilocode", "gemini-cli"}  # type: ignore[method-assign]

    assert provider.available_tools() == ["kilocode", "gemini-cli", "codex-cli"]
    assert provider._default_tool() == "kilocode"

    captured: dict[str, object] = {}

    def _fake_build_args(*, tool, exec_path, prompt, model):
        captured["tool"] = tool
        captured["model"] = model
        return ["echo", "ok"]

    def _fake_run_command(*, args, cwd):
        class Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        captured["args"] = args
        captured["cwd"] = cwd
        return Result()

    provider._build_args = _fake_build_args  # type: ignore[method-assign]
    provider._run_command = _fake_run_command  # type: ignore[method-assign]

    response = provider.complete(CompletionRequest(messages=[], metadata={"cli_tool": "codex-cli"}))

    assert captured["tool"] == "codex-cli"
    assert captured["model"] == "gpt-5.4"
    assert response.model == "gpt-5.4"


def test_runtime_limits_split_codex_direct_and_cli_caps(monkeypatch, tmp_path):
    monkeypatch.delenv("ASTRATA_CODEX_REQUESTS_PER_HOUR", raising=False)
    monkeypatch.delenv("ASTRATA_CODEX_DIRECT_REQUESTS_PER_HOUR", raising=False)
    monkeypatch.delenv("ASTRATA_CODEX_CLI_REQUESTS_PER_HOUR", raising=False)

    settings = load_settings(tmp_path)

    assert settings.runtime_limits.codex_direct_requests_per_hour == 6
    assert settings.runtime_limits.codex_cli_requests_per_hour == 4


def test_shared_codex_limit_still_backfills_split_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRATA_CODEX_REQUESTS_PER_HOUR", "9")
    monkeypatch.delenv("ASTRATA_CODEX_DIRECT_REQUESTS_PER_HOUR", raising=False)
    monkeypatch.delenv("ASTRATA_CODEX_CLI_REQUESTS_PER_HOUR", raising=False)

    settings = load_settings(tmp_path)

    assert settings.runtime_limits.codex_direct_requests_per_hour == 9
    assert settings.runtime_limits.codex_cli_requests_per_hour == 9


def test_procedure_shortcuts_no_longer_prefer_codex_first():
    registry = build_default_procedure_registry()

    direct_patch = registry.get("loop0-bounded-file-generation").variant_map()["direct_patch"]
    direct_execution = registry.get("message-task-bounded-file-generation").variant_map()["direct_execution"]
    direct_decomposition = registry.get("task-decomposition").variant_map()["direct_decomposition"]

    assert direct_patch.preferred_providers[-1] == "codex"
    assert direct_execution.preferred_providers[-1] == "codex"
    assert direct_decomposition.preferred_providers[-1] == "codex"
    assert "codex-cli" not in direct_patch.preferred_cli_tools
    assert "codex-cli" not in direct_execution.preferred_cli_tools
    assert "codex-cli" not in direct_decomposition.preferred_cli_tools
