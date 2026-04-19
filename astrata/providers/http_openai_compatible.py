"""Generic OpenAI-compatible HTTP provider."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
import urllib.error

from astrata.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Provider,
    assert_projected_memory_request,
)
from astrata.providers.model_catalog import ModelCatalogRecord, catalog_record


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        endpoint_env: str,
        api_key_env: str | None,
        model_env: str,
        default_endpoint: str | None = None,
        models_endpoint: str | None = None,
        capabilities: list[str] | None = None,
        input_modalities: list[str] | None = None,
        output_modalities: list[str] | None = None,
    ) -> None:
        self._name = name
        self._endpoint_env = endpoint_env
        self._api_key_env = api_key_env
        self._model_env = model_env
        self._default_endpoint = default_endpoint
        self._models_endpoint = models_endpoint
        self._capabilities = list(capabilities or ["chat"])
        self._input_modalities = list(input_modalities or ["text"])
        self._output_modalities = list(output_modalities or ["text"])

    @property
    def name(self) -> str:
        return self._name

    def endpoint(self) -> str | None:
        return (
            str(os.environ.get(self._endpoint_env) or self._default_endpoint or "").strip() or None
        )

    def api_key(self) -> str | None:
        if not self._api_key_env:
            return None
        return str(os.environ.get(self._api_key_env) or "").strip() or None

    def default_model(self) -> str | None:
        return str(os.environ.get(self._model_env) or "").strip() or None

    def is_configured(self) -> bool:
        endpoint = self.endpoint()
        if not endpoint:
            return False
        if self._api_key_env:
            return self.api_key() is not None
        return True

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        endpoint = self.endpoint()
        if not endpoint:
            raise RuntimeError(f"{self.name} endpoint is not configured")
        assert_projected_memory_request(request, provider_name=self.name)

        model = request.model or self.default_model()
        payload = {
            "model": model,
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        headers = {"Content-Type": "application/json"}
        if self.api_key():
            headers["Authorization"] = f"Bearer {self.api_key()}"
        headers.update(_extra_headers_for_provider(self.name, request))

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{self.name} request failed with HTTP {exc.code}: {detail}"
            ) from exc
        raw = json.loads(body)
        content = _extract_openai_compatible_content(raw)
        return CompletionResponse(provider=self.name, model=model, content=content, raw=raw)

    def list_model_catalog(self) -> list[ModelCatalogRecord]:
        default = self.default_model()
        records: list[ModelCatalogRecord] = []
        if default:
            records.append(
                catalog_record(
                    provider_id=self.name,
                    model_id=default,
                    display_name=default,
                    capabilities=self._capabilities,  # type: ignore[arg-type]
                    input_modalities=self._input_modalities,
                    output_modalities=self._output_modalities,
                    status="configured" if self.is_configured() else "unconfigured",
                    source="provider_default",
                )
            )
        if self._models_endpoint and self.name in {"kilo-gateway", "pollinations"}:
            records.extend(_static_gateway_records(self.name))
        return records


def _extract_openai_compatible_content(raw: dict[str, Any]) -> str:
    choices = raw.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    message = first.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks)
    return str(content or "")


def _extra_headers_for_provider(provider_name: str, request: CompletionRequest) -> dict[str, str]:
    metadata = dict(request.metadata or {})
    if provider_name == "kilo-gateway":
        mode = str(metadata.get("kilocode_mode") or metadata.get("task_mode") or "").strip()
        return {"x-kilocode-mode": mode} if mode else {}
    return {}


def _static_gateway_records(provider_name: str) -> list[ModelCatalogRecord]:
    if provider_name == "kilo-gateway":
        return [
            catalog_record(
                provider_id="kilo-gateway",
                model_id="kilo-auto/frontier",
                display_name="Kilo Auto Frontier",
                capabilities=["chat", "tool_use"],  # type: ignore[list-item]
                context_length=None,
                quota={"anonymous_free_models_per_hour": 200},
                task_fit={"coding": 0.95, "planning": 0.95, "general": 0.9},
                source="kilo_gateway_docs",
                notes="Auto-routed model tier selected by Kilo Gateway using x-kilocode-mode.",
            ),
            catalog_record(
                provider_id="kilo-gateway",
                model_id="kilo-auto/balanced",
                display_name="Kilo Auto Balanced",
                capabilities=["chat", "tool_use"],  # type: ignore[list-item]
                task_fit={"coding": 0.85, "background": 0.75, "general": 0.85},
                source="kilo_gateway_docs",
            ),
            catalog_record(
                provider_id="kilo-gateway",
                model_id="kilo-auto/free",
                display_name="Kilo Auto Free",
                capabilities=["chat"],  # type: ignore[list-item]
                quota={"anonymous_free_models_per_hour": 200},
                task_fit={"background": 0.75, "general": 0.65},
                source="kilo_gateway_docs",
            ),
            catalog_record(
                provider_id="kilo-gateway",
                model_id="x-ai/grok-code-fast-1:optimized:free",
                display_name="Grok Code Fast Optimized Free",
                capabilities=["chat"],  # type: ignore[list-item]
                quota={"free": True},
                task_fit={"coding": 0.8, "background": 0.8},
                source="kilo_gateway_docs",
            ),
        ]
    if provider_name == "pollinations":
        return [
            catalog_record(
                provider_id="pollinations",
                model_id="openai",
                display_name="Pollinations Text Default",
                capabilities=["chat", "text"],  # type: ignore[list-item]
                source="pollinations_docs",
                notes="OpenAI-compatible text endpoint.",
            ),
            catalog_record(
                provider_id="pollinations",
                model_id="flux",
                display_name="Pollinations Flux Image",
                capabilities=["image"],  # type: ignore[list-item]
                input_modalities=["text"],
                output_modalities=["image"],
                source="pollinations_docs",
            ),
            catalog_record(
                provider_id="pollinations",
                model_id="seedance",
                display_name="Pollinations Seedance Video",
                capabilities=["video"],  # type: ignore[list-item]
                input_modalities=["text", "image"],
                output_modalities=["video"],
                status="experimental",
                source="pollinations_docs",
            ),
        ]
    return []
