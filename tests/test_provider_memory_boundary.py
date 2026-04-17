from pathlib import Path

import pytest

from astrata.providers.base import CompletionRequest, assert_projected_memory_request
from astrata.providers.cli import CliProvider
from astrata.providers.google_ai_studio import GoogleAiStudioProvider
from astrata.providers.http_openai_compatible import OpenAICompatibleProvider


def test_remote_memory_boundary_rejects_raw_memory_records():
    request = CompletionRequest(
        messages=[],
        metadata={
            "memory_pages": [
                {
                    "slug": "tax-records",
                    "title": "Tax Records",
                    "body": "Sensitive financial material.",
                }
            ]
        },
    )

    with pytest.raises(RuntimeError, match="raw memory records"):
        assert_projected_memory_request(request, provider_name="openai")


def test_remote_memory_boundary_allows_projected_memory_snippets():
    request = CompletionRequest(
        messages=[],
        metadata={
            "memory_context": [
                "[public] Board Minutes: A restricted governance record exists.",
            ]
        },
    )

    assert_projected_memory_request(request, provider_name="openai")


def test_openai_compatible_provider_rejects_raw_memory_for_remote_endpoint(monkeypatch):
    monkeypatch.setenv("ASTRATA_OPENAI_ENDPOINT", "https://api.openai.com/v1/chat/completions")
    provider = OpenAICompatibleProvider(
        name="openai",
        endpoint_env="ASTRATA_OPENAI_ENDPOINT",
        api_key_env=None,
        model_env="ASTRATA_OPENAI_MODEL",
    )

    with pytest.raises(RuntimeError, match="raw memory records"):
        provider.complete(
            CompletionRequest(
                messages=[],
                metadata={"memory_records": [{"slug": "secret", "body": "nope"}]},
            )
        )


def test_cli_provider_rejects_raw_memory_for_remote_tool(monkeypatch):
    provider = CliProvider()
    provider._tool_is_usable = lambda tool: tool == "codex-cli"  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="raw memory records"):
        provider.complete(
            CompletionRequest(
                messages=[],
                metadata={
                    "cli_tool": "codex-cli",
                    "raw_memory": [{"slug": "secret", "body": "nope"}],
                },
            )
        )


def test_google_provider_rejects_structured_memory_context(tmp_path: Path):
    provider = GoogleAiStudioProvider(
        api_key="demo-key",
        catalog_path=tmp_path / "google_models.json",
        quota_state_path=tmp_path / "google_quota_state.json",
    )

    with pytest.raises(RuntimeError, match="projected text snippets"):
        provider.complete(
            CompletionRequest(
                messages=[],
                metadata={"memory_context": [{"title": "secret", "body": "nope"}]},
            )
        )
