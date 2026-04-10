"""Generic OpenAI-compatible HTTP provider."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
import urllib.error

from astrata.providers.base import CompletionRequest, CompletionResponse, Provider, assert_projected_memory_request


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        endpoint_env: str,
        api_key_env: str | None,
        model_env: str,
        default_endpoint: str | None = None,
    ) -> None:
        self._name = name
        self._endpoint_env = endpoint_env
        self._api_key_env = api_key_env
        self._model_env = model_env
        self._default_endpoint = default_endpoint

    @property
    def name(self) -> str:
        return self._name

    def endpoint(self) -> str | None:
        return str(os.environ.get(self._endpoint_env) or self._default_endpoint or "").strip() or None

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
        if not _looks_local_endpoint(endpoint):
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
            raise RuntimeError(f"{self.name} request failed with HTTP {exc.code}: {detail}") from exc
        raw = json.loads(body)
        content = _extract_openai_compatible_content(raw)
        return CompletionResponse(provider=self.name, model=model, content=content, raw=raw)


def _looks_local_endpoint(endpoint: str) -> bool:
    normalized = str(endpoint or "").strip().lower()
    return normalized.startswith("http://127.0.0.1") or normalized.startswith("http://localhost")


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
