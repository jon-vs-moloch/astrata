"""Google AI Studio provider with model sync and observed quota memory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from astrata.providers.base import CompletionRequest, CompletionResponse, Provider


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GoogleAiStudioProvider(Provider):
    def __init__(
        self,
        *,
        name: str = "google",
        api_key: str | None = None,
        default_model: str | None = None,
        catalog_path: Path,
        quota_state_path: Path,
    ) -> None:
        self._name = name
        self._api_key = str(api_key or os.environ.get("ASTRATA_GOOGLE_API_KEY") or "").strip() or None
        self._default_model = str(default_model or os.environ.get("ASTRATA_GOOGLE_MODEL") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        self._chat_endpoint = str(
            os.environ.get("ASTRATA_GOOGLE_ENDPOINT")
            or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        ).strip()
        self._models_endpoint = "https://generativelanguage.googleapis.com/v1beta/models"
        self._catalog_path = catalog_path
        self._quota_state_path = quota_state_path
        self._catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self._quota_state_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self.api_key() is not None

    def api_key(self) -> str | None:
        return self._api_key

    def default_model(self) -> str | None:
        return self._default_model

    def describe(self) -> dict[str, Any]:
        cached = self.cached_models()
        return {
            "name": self.name,
            "is_configured": self.is_configured(),
            "default_model": self.default_model(),
            "cached_model_count": len(cached),
            "last_model_sync_at": self._load_catalog().get("last_synced_at"),
        }

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        api_key = self.api_key()
        if not api_key:
            raise RuntimeError("Google AI Studio API key is not configured")
        model = request.model or self.default_model()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(
            self._chat_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                body = response.read().decode("utf-8")
                raw = json.loads(body)
                content = _extract_openai_compatible_content(raw)
                return CompletionResponse(provider=self.name, model=model, content=content, raw=raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                self._record_rate_limit(model=model, headers=dict(exc.headers.items()))
            raise RuntimeError(f"{self.name} request failed with HTTP {exc.code}: {detail}") from exc

    def sync_models(self) -> list[dict[str, Any]]:
        api_key = self.api_key()
        if not api_key:
            raise RuntimeError("Google AI Studio API key is not configured")
        query = urllib.parse.urlencode({"key": api_key, "pageSize": 1000})
        url = f"{self._models_endpoint}?{query}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = []
        for model in payload.get("models") or []:
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or "")
            model_id = name.split("/")[-1] if "/" in name else name
            models.append(
                {
                    "name": name,
                    "model_id": model_id,
                    "display_name": model.get("displayName"),
                    "description": model.get("description"),
                    "input_token_limit": model.get("inputTokenLimit"),
                    "output_token_limit": model.get("outputTokenLimit"),
                    "supported_generation_methods": list(model.get("supportedGenerationMethods") or []),
                    "version": model.get("version"),
                }
            )
        catalog = {
            "last_synced_at": _now().isoformat(),
            "models": models,
        }
        self._catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        return models

    def cached_models(self) -> list[dict[str, Any]]:
        return list(self._load_catalog().get("models") or [])

    def get_quota_windows(self, route: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        payload = self._load_quota_state()
        now = _now()
        model = str((route or {}).get("model") or self.default_model() or "").strip()
        windows = []
        for record in payload.get("cooldowns") or []:
            if not isinstance(record, dict):
                continue
            if model and str(record.get("model") or "").strip() not in {"", model}:
                continue
            reset_time = _coerce_time(record.get("reset_time"))
            if reset_time is None or reset_time <= now:
                continue
            windows.append(
                {
                    "requests_remaining": 0,
                    "requests_limit": 1,
                    "reset_time": reset_time.isoformat(),
                    "window_duration_seconds": max(1, int((reset_time - now).total_seconds())),
                    "source": str(record.get("source") or "google_observed_rate_limit"),
                    "model": record.get("model"),
                }
            )
        return windows or None

    def _record_rate_limit(self, *, model: str | None, headers: dict[str, str]) -> None:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        reset_time: datetime | None = None
        if retry_after:
            try:
                reset_time = _now() + timedelta(seconds=max(1, int(float(retry_after))))
            except Exception:
                reset_time = None
        if reset_time is None:
            reset_time = _now() + timedelta(minutes=1)
        payload = self._load_quota_state()
        cooldowns = [item for item in (payload.get("cooldowns") or []) if isinstance(item, dict)]
        cooldowns.append(
            {
                "model": model,
                "reset_time": reset_time.isoformat(),
                "source": "google_http_429",
                "recorded_at": _now().isoformat(),
            }
        )
        if len(cooldowns) > 200:
            cooldowns = cooldowns[-200:]
        payload["cooldowns"] = cooldowns
        self._quota_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_catalog(self) -> dict[str, Any]:
        if not self._catalog_path.exists():
            return {"models": []}
        try:
            payload = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return {"models": []}
        if not isinstance(payload, dict):
            return {"models": []}
        payload.setdefault("models", [])
        return payload

    def _load_quota_state(self) -> dict[str, Any]:
        if not self._quota_state_path.exists():
            return {"cooldowns": []}
        try:
            payload = json.loads(self._quota_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"cooldowns": []}
        if not isinstance(payload, dict):
            return {"cooldowns": []}
        payload.setdefault("cooldowns", [])
        return payload


def _coerce_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except Exception:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


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
