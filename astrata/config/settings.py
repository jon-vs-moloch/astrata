"""Minimal settings for Astrata Phase 0."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AstrataPaths:
    project_root: Path
    data_dir: Path
    docs_dir: Path
    provider_secrets_path: Path


@dataclass(frozen=True)
class RuntimeLimits:
    codex_requests_per_hour: int = 12
    kilocode_requests_per_hour: int = 200
    gemini_requests_per_hour: int = 60
    claude_requests_per_hour: int = 30
    openai_requests_per_hour: int = 60
    google_requests_per_hour: int = 60
    anthropic_requests_per_hour: int = 40
    custom_requests_per_hour: int = 60


@dataclass(frozen=True)
class LocalRuntimeSettings:
    model_search_paths: tuple[str, ...] = ()
    model_install_dir: Path | None = None
    llama_cpp_binary: str = "llama-server"
    llama_cpp_host: str = "127.0.0.1"
    llama_cpp_port: int = 8080
    llama_cpp_managed: bool = False
    llama_cpp_base_url: str | None = None
    strata_endpoint_base_url: str | None = None
    thermal_preference: str = "quiet"


@dataclass(frozen=True)
class Settings:
    paths: AstrataPaths
    runtime_limits: RuntimeLimits
    local_runtime: LocalRuntimeSettings


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    install_dir = (
        Path(os.environ["ASTRATA_LOCAL_MODEL_INSTALL_DIR"]).expanduser()
        if os.environ.get("ASTRATA_LOCAL_MODEL_INSTALL_DIR")
        else data_dir / "models"
    )
    configured_search_paths = [
        path
        for path in os.environ.get("ASTRATA_LOCAL_MODEL_SEARCH_PATHS", "").split(":")
        if path
    ]
    if str(install_dir) not in configured_search_paths:
        configured_search_paths.append(str(install_dir))
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(
            codex_requests_per_hour=int(os.environ.get("ASTRATA_CODEX_REQUESTS_PER_HOUR", "12")),
            kilocode_requests_per_hour=int(os.environ.get("ASTRATA_KILOCODE_REQUESTS_PER_HOUR", "200")),
            gemini_requests_per_hour=int(os.environ.get("ASTRATA_GEMINI_REQUESTS_PER_HOUR", "60")),
            claude_requests_per_hour=int(os.environ.get("ASTRATA_CLAUDE_REQUESTS_PER_HOUR", "30")),
            openai_requests_per_hour=int(os.environ.get("ASTRATA_OPENAI_REQUESTS_PER_HOUR", "60")),
            google_requests_per_hour=int(os.environ.get("ASTRATA_GOOGLE_REQUESTS_PER_HOUR", "60")),
            anthropic_requests_per_hour=int(os.environ.get("ASTRATA_ANTHROPIC_REQUESTS_PER_HOUR", "40")),
            custom_requests_per_hour=int(os.environ.get("ASTRATA_CUSTOM_REQUESTS_PER_HOUR", "60")),
        ),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=tuple(configured_search_paths),
            model_install_dir=install_dir,
            llama_cpp_binary=os.environ.get("ASTRATA_LLAMA_CPP_BINARY", "llama-server"),
            llama_cpp_host=os.environ.get("ASTRATA_LLAMA_CPP_HOST", "127.0.0.1"),
            llama_cpp_port=int(os.environ.get("ASTRATA_LLAMA_CPP_PORT", "8080")),
            llama_cpp_managed=os.environ.get("ASTRATA_LLAMA_CPP_MANAGED", "0") in {"1", "true", "TRUE"},
            llama_cpp_base_url=os.environ.get("ASTRATA_LLAMA_CPP_BASE_URL"),
            strata_endpoint_base_url=os.environ.get("ASTRATA_STRATA_ENDPOINT_BASE_URL"),
            thermal_preference=os.environ.get("ASTRATA_THERMAL_PREFERENCE", "quiet"),
        ),
    )
