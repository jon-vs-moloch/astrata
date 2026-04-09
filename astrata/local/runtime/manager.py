"""Minimal local runtime manager for Astrata's Lightning substrate."""

from __future__ import annotations

import time
from typing import Any
from pathlib import Path

from astrata.inference.contracts import BackendCapabilitySet
from astrata.local.backends.base import BackendLaunchSpec, LocalBackend
from astrata.local.backends.llama_cpp import LlamaCppLaunchConfig
from astrata.local.hardware import probe_hardware_profile, probe_thermal_state
from astrata.local.models.discovery import discover_local_models
from astrata.local.models.registry import LocalModelRegistry
from astrata.local.recommendation import RuntimeRecommendation, recommend_runtime_selection
from astrata.local.profiles import RuntimeProfile, RuntimeProfileRegistry
from astrata.local.runtime.models import RuntimeHealthSnapshot, RuntimeSelection
from astrata.local.runtime.processes import ManagedProcessController, ManagedProcessStatus


class LocalRuntimeManager:
    def __init__(
        self,
        *,
        backends: dict[str, LocalBackend] | None = None,
        model_registry: LocalModelRegistry | None = None,
        profile_registry: RuntimeProfileRegistry | None = None,
        process_controller: ManagedProcessController | None = None,
    ) -> None:
        self._backends = dict(backends or {})
        self._model_registry = model_registry or LocalModelRegistry()
        self._profile_registry = profile_registry or RuntimeProfileRegistry()
        self._process_controller_template = process_controller
        self._process_controllers: dict[str, ManagedProcessController] = {}
        if process_controller is not None:
            self._process_controllers["default"] = process_controller
        self._selections: dict[str, RuntimeSelection] = {}
        self._active_runtime_key: str | None = None

    def register_backend(self, backend: LocalBackend) -> None:
        self._backends[backend.backend_id] = backend

    def backend(self, backend_id: str) -> LocalBackend | None:
        return self._backends.get(backend_id)

    def model_registry(self) -> LocalModelRegistry:
        return self._model_registry

    def profile_registry(self) -> RuntimeProfileRegistry:
        return self._profile_registry

    def list_profiles(self) -> list[RuntimeProfile]:
        return self._profile_registry.list_profiles()

    def backend_capabilities(self, backend_id: str) -> BackendCapabilitySet:
        backend = self.backend(backend_id)
        if backend is None:
            return BackendCapabilitySet(backend_id=backend_id, notes=["Backend is not registered."])
        return backend.capabilities()

    def list_backend_capabilities(self) -> list[BackendCapabilitySet]:
        return [self.backend_capabilities(backend_id) for backend_id in sorted(self._backends)]

    def select_runtime(
        self,
        *,
        runtime_key: str = "default",
        backend_id: str,
        model_id: str | None = None,
        mode: str = "managed",
        profile_id: str | None = None,
        endpoint: str | None = None,
        metadata: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> RuntimeSelection:
        selection = RuntimeSelection(
            runtime_key=runtime_key,
            backend_id=backend_id,
            model_id=model_id,
            mode="external" if mode == "external" else "managed",
            profile_id=profile_id,
            endpoint=endpoint,
            metadata=dict(metadata or {}),
        )
        self._selections[runtime_key] = selection
        if activate:
            self._active_runtime_key = runtime_key
        return selection

    def current_selection(self, runtime_key: str | None = None) -> RuntimeSelection | None:
        if runtime_key is not None:
            return self._selections.get(runtime_key)
        if self._active_runtime_key:
            return self._selections.get(self._active_runtime_key)
        return self._selections.get("default")

    def list_selections(self) -> list[RuntimeSelection]:
        return sorted(self._selections.values(), key=lambda selection: selection.runtime_key)

    def discover_models(self, *, search_paths: tuple[str, ...]) -> list[str]:
        return discover_local_models(self._model_registry, search_paths=search_paths)

    def recommend(self, *, thermal_preference: str = "quiet") -> RuntimeRecommendation:
        hardware = probe_hardware_profile()
        thermal = probe_thermal_state(preference=thermal_preference)
        return recommend_runtime_selection(
            hardware=hardware,
            models=self._model_registry.list_models(),
            thermal=thermal,
        )

    def start_managed(
        self,
        *,
        runtime_key: str = "default",
        backend_id: str,
        model_id: str,
        binary_path: str = "llama-server",
        host: str = "127.0.0.1",
        port: int = 8080,
        profile_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        activate: bool = True,
    ) -> ManagedProcessStatus:
        controller = self._controller_for_runtime(runtime_key)
        if controller is None:
            raise RuntimeError("Managed local process control is not configured.")
        backend = self.backend(backend_id)
        if backend is None:
            raise RuntimeError(f"Unknown backend: {backend_id}")
        model = self._model_registry.get(model_id)
        if model is None:
            raise RuntimeError(f"Unknown local model: {model_id}")
        profile = self._resolve_profile(profile_id)
        launch_spec = self._build_launch_spec(
            backend_id=backend_id,
            backend=backend,
            binary_path=binary_path,
            model_path=model.path,
            host=host,
            port=port,
            profile=profile,
            extra_args=extra_args,
        )
        self.select_runtime(
            runtime_key=runtime_key,
            backend_id=backend_id,
            model_id=model_id,
            mode="managed",
            profile_id=profile.profile_id,
            endpoint=launch_spec.endpoint,
            metadata={
                "runtime_key": runtime_key,
                "model_path": model.path,
                "profile_args": list(profile.llama_cpp_args),
                "extra_args": list(extra_args),
            },
            activate=activate,
        )
        status = controller.start(launch_spec)
        self._wait_until_healthy(
            backend=backend,
            config=LlamaCppLaunchConfig(
                binary_path=binary_path,
                host=host,
                port=port,
                model_path=model.path,
                extra_args=tuple(profile.llama_cpp_args) + tuple(extra_args),
            ) if backend_id == "llama_cpp" else {
                "binary_path": binary_path,
                "host": host,
                "port": port,
                "model_path": model.path,
                "extra_args": tuple(profile.llama_cpp_args) + tuple(extra_args),
            },
        )
        return status

    def stop_managed(self, runtime_key: str | None = None) -> ManagedProcessStatus:
        key = runtime_key or self._active_runtime_key or "default"
        controller = self._controller_for_runtime(key)
        if controller is None:
            raise RuntimeError("Managed local process control is not configured.")
        status = controller.stop()
        self._selections.pop(key, None)
        if self._active_runtime_key == key:
            remaining = sorted(self._selections)
            self._active_runtime_key = remaining[0] if remaining else None
        return status

    def managed_status(self, runtime_key: str | None = None) -> ManagedProcessStatus | None:
        key = runtime_key or self._active_runtime_key or "default"
        controller = self._controller_for_runtime(key, create=False)
        if controller is None:
            return None
        return controller.status()

    def list_managed_statuses(self) -> dict[str, ManagedProcessStatus]:
        statuses: dict[str, ManagedProcessStatus] = {}
        for key, controller in self._process_controllers.items():
            status = controller.status()
            if key not in self._selections and not status.running and not status.endpoint and not status.command:
                continue
            statuses[key] = status
        return dict(sorted(statuses.items()))

    def health(self, *, runtime_key: str | None = None, config: Any | None = None) -> RuntimeHealthSnapshot | None:
        selection = self.current_selection(runtime_key)
        if selection is None:
            return None
        backend = self.backend(selection.backend_id)
        if backend is None:
            return RuntimeHealthSnapshot(
                backend_id=selection.backend_id,
                ok=False,
                status="missing_backend",
                endpoint=selection.endpoint,
                detail="Selected backend is not registered.",
            )
        health = backend.healthcheck(config=config)
        return RuntimeHealthSnapshot(
            backend_id=backend.backend_id,
            ok=health.ok,
            status=health.status,
            endpoint=health.endpoint or selection.endpoint,
            detail=health.detail,
            metadata=dict(health.metadata or {}),
        )

    def _controller_for_runtime(
        self,
        runtime_key: str,
        *,
        create: bool = True,
    ) -> ManagedProcessController | None:
        normalized = _normalize_runtime_key(runtime_key)
        existing = self._process_controllers.get(normalized)
        if existing is not None or not create:
            return existing
        template = self._process_controller_template
        if template is None:
            return None
        derived = ManagedProcessController(
            state_path=_derive_runtime_path(template.state_path, normalized),
            log_path=_derive_runtime_path(template.log_path, normalized),
        )
        self._process_controllers[normalized] = derived
        return derived

    def _resolve_profile(self, profile_id: str | None) -> RuntimeProfile:
        if profile_id:
            return self._profile_registry.get(profile_id)
        recommendation = self.recommend()
        return self._profile_registry.get(recommendation.profile_id)

    def _build_launch_spec(
        self,
        *,
        backend_id: str,
        backend: LocalBackend,
        binary_path: str,
        model_path: str,
        host: str,
        port: int,
        profile: RuntimeProfile,
        extra_args: tuple[str, ...],
    ) -> BackendLaunchSpec:
        if backend_id == "llama_cpp":
            return backend.build_launch_spec(
                config=LlamaCppLaunchConfig(
                    binary_path=binary_path,
                    host=host,
                    port=port,
                    model_path=model_path,
                    extra_args=tuple(profile.llama_cpp_args) + tuple(extra_args),
                )
            )
        return backend.build_launch_spec(
            config={
                "binary_path": binary_path,
                "host": host,
                "port": port,
                "model_path": model_path,
                "extra_args": tuple(extra_args),
                "profile_id": profile.profile_id,
            }
        )

    def _wait_until_healthy(
        self,
        *,
        backend: LocalBackend,
        config: Any,
        timeout_seconds: float = 60.0,
        poll_seconds: float = 0.25,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_detail = "unknown"
        while time.monotonic() < deadline:
            health = backend.healthcheck(config=config)
            if health.ok:
                return
            last_detail = health.detail or health.status
            time.sleep(poll_seconds)
        raise RuntimeError(f"Timed out waiting for local runtime health: {last_detail}")


def _normalize_runtime_key(runtime_key: str | None) -> str:
    raw = str(runtime_key or "default").strip().lower() or "default"
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in raw).strip("-")
    return cleaned or "default"


def _derive_runtime_path(path: Path, runtime_key: str) -> Path:
    if runtime_key == "default":
        return path
    suffix = "".join(path.suffixes)
    stem = path.name[: -len(suffix)] if suffix else path.name
    filename = f"{stem}-{runtime_key}{suffix}"
    return path.with_name(filename)
