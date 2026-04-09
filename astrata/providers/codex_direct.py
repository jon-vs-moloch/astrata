"""Direct Codex-session-backed inference provider."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
import urllib.request
from pathlib import Path
from typing import Any

from astrata.providers.base import CompletionRequest, CompletionResponse, Provider


class CodexDirectProvider(Provider):
    def __init__(self, *, name: str = "codex") -> None:
        self._name = name
        self._endpoint = "https://chatgpt.com/backend-api/codex/responses"
        self._timeout_seconds = int(os.environ.get("ASTRATA_CODEX_DIRECT_TIMEOUT_SECONDS", "90"))
        self._default_model = str(os.environ.get("ASTRATA_CODEX_MODEL") or "gpt-5.4").strip() or "gpt-5.4"

    @property
    def name(self) -> str:
        return self._name

    def default_model(self) -> str | None:
        return self._default_model

    def is_configured(self) -> bool:
        token, _account_id = self._read_codex_auth()
        return bool(token)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        token, account_id = self._read_codex_auth()
        if not token:
            raise RuntimeError("Codex session token is not available")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "Astrata/0.1",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        instructions, input_items = _messages_to_codex_payload(request)
        payload: dict[str, Any] = {
            "model": request.model or self.default_model(),
            "instructions": instructions,
            "input": input_items,
            "stream": True,
            "store": False,
        }
        req = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        text_chunks: list[str] = []
        fallback_text: str = ""
        with urllib.request.urlopen(req, timeout=self._timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except Exception:
                    continue
                event_type = str(event.get("type") or "")
                if event_type == "response.output_text.delta":
                    delta = str(event.get("delta") or "")
                    if delta:
                        text_chunks.append(delta)
                elif event_type == "response.output_item.done":
                    item = event.get("item") or {}
                    if item.get("type") == "message" and not text_chunks:
                        for part in item.get("content") or []:
                            if part.get("type") == "output_text" and part.get("text"):
                                fallback_text = str(part.get("text"))
                elif event_type == "response.completed":
                    break

        content = "".join(text_chunks).strip() or fallback_text.strip()
        return CompletionResponse(
            provider=self.name,
            model=payload["model"],
            content=content,
            raw={"endpoint": self._endpoint},
        )

    def _read_codex_auth(self) -> tuple[str | None, str | None]:
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        auth_path = codex_home / "auth.json"
        try:
            if not auth_path.exists():
                return None, None
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception:
            return None, None
        tokens = payload.get("tokens") or {}
        if not isinstance(tokens, dict):
            tokens = {}
        access_token = str(tokens.get("access_token") or "").strip() or None
        account_id = str(tokens.get("account_id") or "").strip() or None
        return access_token, account_id

    def get_quota_windows(self, route: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        token, account_id = self._read_codex_auth()
        if not token:
            return None
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Astrata/0.1",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        req = urllib.request.Request(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        rate_limit = payload.get("rate_limit") or {}
        if not isinstance(rate_limit, dict):
            return None
        windows: list[dict[str, Any]] = []
        primary = rate_limit.get("primary_window")
        if isinstance(primary, dict):
            record = _window_to_quota_record(primary, default_window_seconds=5 * 3600)
            if record:
                windows.append(record)
        secondary = rate_limit.get("secondary_window")
        if isinstance(secondary, dict):
            record = _window_to_quota_record(secondary, default_window_seconds=14 * 24 * 3600)
            if record:
                windows.append(record)
        return windows or None


def _messages_to_codex_payload(request: CompletionRequest) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in request.messages:
        role = str(message.role or "").strip().lower()
        content = str(message.content or "")
        if role == "system":
            if content.strip():
                instructions.append(content.strip())
            continue
        if role not in {"user", "assistant"} or not content.strip():
            continue
        part_type = "input_text" if role == "user" else "output_text"
        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": [{"type": part_type, "text": content}],
            }
        )
    merged_instructions = "\n\n".join(instructions).strip() or "You are Astrata Prime."
    return merged_instructions, input_items


def _window_to_quota_record(
    window: dict[str, Any],
    *,
    default_window_seconds: int,
) -> dict[str, Any] | None:
    try:
        used_percent = float(window.get("used_percent"))
    except Exception:
        return None
    used_percent = max(0.0, min(100.0, used_percent))
    remaining_percent = max(0.0, 100.0 - used_percent)
    reset_at = window.get("reset_at")
    if not isinstance(reset_at, (int, float)):
        return None
    window_seconds = int(window.get("limit_window_seconds") or default_window_seconds)
    limit_units = 10000
    remaining_units = int(round(limit_units * (remaining_percent / 100.0)))
    return {
        "requests_remaining": remaining_units,
        "requests_limit": limit_units,
        "reset_time": datetime.fromtimestamp(float(reset_at), tz=timezone.utc).isoformat(),
        "window_duration_seconds": max(60, window_seconds),
        "source": "codex_usage_api",
    }
