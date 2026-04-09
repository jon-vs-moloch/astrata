"""`llama.cpp`-oriented local backend helpers."""

from __future__ import annotations

from dataclasses import dataclass
import urllib.request
from typing import Any

from astrata.inference.contracts import BackendCapabilitySet
from astrata.local.backends.base import BackendHealth, BackendLaunchSpec, LocalBackend


@dataclass(frozen=True)
class LlamaCppLaunchConfig:
    binary_path: str = "llama-server"
    host: str = "127.0.0.1"
    port: int = 8080
    model_path: str = ""
    extra_args: tuple[str, ...] = ()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class LlamaCppBackend(LocalBackend):
    @property
    def backend_id(self) -> str:
        return "llama_cpp"

    def build_launch_spec(self, **kwargs: Any) -> BackendLaunchSpec:
        config = self._coerce_config(kwargs.get("config"))
        command = [
            config.binary_path,
            "--host",
            config.host,
            "--port",
            str(config.port),
        ]
        if config.model_path:
            command.extend(["-m", config.model_path])
        command.extend(list(config.extra_args))
        return BackendLaunchSpec(
            command=command,
            endpoint=f"{config.base_url}/health",
            metadata={
                "backend_id": self.backend_id,
                "base_url": config.base_url,
                "model_path": config.model_path,
            },
        )

    def capabilities(self) -> BackendCapabilitySet:
        return BackendCapabilitySet(
            backend_id=self.backend_id,
            multi_model_residency=True,
            native_prefix_cache=False,
            native_checkpoint_restore=False,
            native_branch_fork=False,
            edit_tail_invalidation=False,
            streaming=True,
            ephemeral_sessions=True,
            managed_processes=True,
            notes=[
                "Current Astrata integration can host multiple named managed runtimes behind one manager.",
                "Prefix reuse, checkpoints, and branch fork are currently emulated above the backend rather than provided natively.",
            ],
        )

    def healthcheck(self, **kwargs: Any) -> BackendHealth:
        config = self._coerce_config(kwargs.get("config"))
        endpoint = f"{config.base_url}/health"
        try:
            with urllib.request.urlopen(endpoint, timeout=1.5) as response:
                status_code = getattr(response, "status", 200)
            ok = int(status_code) < 500
            return BackendHealth(
                ok=ok,
                status="healthy" if ok else "degraded",
                endpoint=endpoint,
                detail=f"http_status={status_code}",
                metadata={"backend_id": self.backend_id},
            )
        except Exception as exc:
            return BackendHealth(
                ok=False,
                status="unreachable",
                endpoint=endpoint,
                detail=str(exc),
                metadata={"backend_id": self.backend_id},
            )

    def _coerce_config(self, value: Any) -> LlamaCppLaunchConfig:
        if isinstance(value, LlamaCppLaunchConfig):
            return value
        if isinstance(value, dict):
            return LlamaCppLaunchConfig(
                binary_path=str(value.get("binary_path") or "llama-server"),
                host=str(value.get("host") or "127.0.0.1"),
                port=int(value.get("port") or 8080),
                model_path=str(value.get("model_path") or ""),
                extra_args=tuple(str(item) for item in list(value.get("extra_args") or [])),
            )
        return LlamaCppLaunchConfig()
