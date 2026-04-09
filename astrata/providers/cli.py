"""CLI-backed provider lanes for Codex, Kilo, and sibling tools."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from astrata.providers.base import CompletionRequest, CompletionResponse, Provider


CLI_TOOL_SPECS: dict[str, dict[str, str]] = {
    "codex-cli": {"exec": "codex", "underlying_provider": "openai"},
    "gemini-cli": {"exec": "gemini", "underlying_provider": "google"},
    "claude-code": {"exec": "claude", "underlying_provider": "anthropic"},
    "kilocode": {"exec": "kilo", "underlying_provider": "custom"},
}


class CliProvider(Provider):
    def __init__(self, *, name: str = "cli") -> None:
        self._name = name
        self._completion_timeout_seconds = int(os.environ.get("ASTRATA_CLI_TIMEOUT_SECONDS", "90"))

    @property
    def name(self) -> str:
        return self._name

    def default_model(self) -> str | None:
        return None

    def is_configured(self) -> bool:
        return bool(self.available_tools())

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "is_configured": self.is_configured(),
            "default_model": self.default_model(),
            "available_tools": self.available_tools(),
        }

    def available_tools(self) -> list[str]:
        ordered: list[str] = []
        for tool in ("codex-cli", "kilocode", "gemini-cli", "claude-code"):
            if self._tool_is_usable(tool):
                ordered.append(tool)
        return ordered

    def _run_command(
        self,
        *,
        args: list[str],
        cwd: str | None,
    ) -> subprocess.CompletedProcess[str]:
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                args,
                text=True,
                cwd=cwd or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=self._completion_timeout_seconds)
            return subprocess.CompletedProcess(
                args=args,
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired as exc:
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass
            raise RuntimeError(
                f"CLI command timed out after {self._completion_timeout_seconds}s"
            ) from exc

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        cli_tool = str(request.metadata.get("cli_tool") or "").strip().lower() or None
        tool = cli_tool or self._default_tool()
        if not tool:
            raise RuntimeError("No CLI inference tool is configured")
        if not self._tool_is_usable(tool):
            raise RuntimeError(f"CLI tool {tool} is not configured")

        spec = CLI_TOOL_SPECS[tool]
        exec_path = shutil.which(spec["exec"]) or spec["exec"]
        prompt = _render_prompt(request)
        model = request.model or str(request.metadata.get("model") or "").strip() or None
        cwd = str(request.metadata.get("cwd") or "").strip() or None
        args = self._build_args(tool=tool, exec_path=exec_path, prompt=prompt, model=model)
        proc = self._run_command(args=args, cwd=cwd)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            detail = stderr or stdout or f"exit code {proc.returncode}"
            raise RuntimeError(f"{tool} command failed: {detail}")
        content = self._extract_content(tool, proc.stdout)
        return CompletionResponse(
            provider=self.name,
            model=model,
            content=content,
            raw={
                "cli_tool": tool,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            },
        )

    def _default_tool(self) -> str | None:
        available = self.available_tools()
        if "codex-cli" in available:
            return "codex-cli"
        if "kilocode" in available:
            return "kilocode"
        return available[0] if available else None

    def _tool_is_usable(self, tool: str) -> bool:
        spec = CLI_TOOL_SPECS.get(tool)
        if not spec:
            return False
        if not shutil.which(spec["exec"]):
            return False
        if tool == "codex-cli":
            return self._codex_authenticated()
        return True

    def _codex_authenticated(self) -> bool:
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        auth_path = codex_home / "auth.json"
        try:
            if not auth_path.exists():
                return False
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        tokens = payload.get("tokens") or {}
        if not isinstance(tokens, dict):
            return False
        return bool(str(tokens.get("access_token") or "").strip() or str(payload.get("OPENAI_API_KEY") or "").strip())

    def _build_args(
        self,
        *,
        tool: str,
        exec_path: str,
        prompt: str,
        model: str | None,
    ) -> list[str]:
        if tool == "codex-cli":
            args = [
                exec_path,
                "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
            ]
            if model:
                args.extend(["-m", model])
            args.append(prompt)
            return args
        if tool == "kilocode":
            args = [exec_path, "run", prompt, "--auto", "--format", "json"]
            if model:
                args.extend(["-m", model])
            return args
        if tool == "gemini-cli":
            args = [exec_path, "-p", prompt, "-y", "--output-format", "text"]
            if model:
                args[1:1] = ["-m", model]
            return args
        if tool == "claude-code":
            args = [exec_path, "-p", prompt, "--output-format", "text", "--dangerously-skip-permissions"]
            if model:
                args.extend(["--model", model])
            return args
        raise RuntimeError(f"Unsupported CLI tool: {tool}")

    def _extract_content(self, tool: str, stdout: str) -> str:
        if tool == "kilocode":
            text = _extract_kilo_text(stdout)
            if text:
                return text
        text = _extract_text_after_marker(stdout, marker="codex")
        if text:
            return text
        return stdout.strip()


def _render_prompt(request: CompletionRequest) -> str:
    lines: list[str] = []
    for message in request.messages:
        role = message.role.upper()
        content = message.content or ""
        if not content.strip():
            continue
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines).strip() or "Hello."


def _extract_text_after_marker(transcript: str, *, marker: str) -> str:
    lines = [line.rstrip() for line in transcript.splitlines()]
    last_idx = -1
    for index, line in enumerate(lines):
        if line.strip().lower() == marker:
            last_idx = index
    if last_idx < 0:
        return ""
    collected: list[str] = []
    for line in lines[last_idx + 1 :]:
        lowered = line.strip().lower()
        if lowered in {"tokens used", "context window"}:
            break
        if re.match(r"^[0-9][0-9,]*$", line.strip()):
            break
        if line.strip():
            collected.append(line)
    return "\n".join(collected).strip()


def _extract_kilo_text(transcript: str) -> str:
    chunks: list[str] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if payload.get("type") != "text":
            continue
        part = payload.get("part") or {}
        text = str(part.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()
