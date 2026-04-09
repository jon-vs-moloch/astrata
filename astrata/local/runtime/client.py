"""Client for Astrata-owned local runtime chat completions."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

from astrata.providers.base import CompletionRequest


class LocalRuntimeClient:
    def complete(
        self,
        *,
        base_url: str,
        request: CompletionRequest,
        thread_id: str | None = None,
        allow_degraded_fallback: bool = False,
    ) -> str:
        endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": request.model or "local",
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        metadata = dict(request.metadata or {})
        max_tokens = metadata.pop("max_tokens", None) or metadata.pop("max_output_tokens", None)
        if max_tokens is not None:
            try:
                payload["max_tokens"] = max(1, int(max_tokens))
            except Exception:
                pass
        if allow_degraded_fallback:
            metadata["allow_degraded_fallback"] = True
        if metadata:
            payload["metadata"] = metadata
        if thread_id:
            payload["thread_id"] = thread_id
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return _extract_openai_compatible_content(raw)

    def health(self, *, base_url: str) -> dict[str, Any]:
        candidates = [
            f"{base_url.rstrip('/')}/healthz",
            f"{base_url.rstrip('/')}/health",
        ]
        last_error = "unreachable"
        for endpoint in candidates:
            req = urllib.request.Request(endpoint, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    raw = response.read().decode("utf-8")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"raw": raw}
                return {"ok": True, "endpoint": endpoint, "payload": payload}
            except urllib.error.HTTPError as exc:
                last_error = f"http_{exc.code}"
            except Exception as exc:
                last_error = str(exc)
        return {"ok": False, "endpoint": candidates[0], "error": last_error}


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
