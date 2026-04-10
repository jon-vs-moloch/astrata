"""Minimal CLI entrypoint for Astrata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.request

from astrata.comms.intake import process_inbound_messages
from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.secrets import SecretStore
from astrata.config.settings import load_settings
from astrata.eval.local_model_arena import LocalModelArena
from astrata.eval.local_models import summarize_local_model_evals
from astrata.eval.observations import EvalObservationStore
from astrata.eval.provider_routes import ProviderRouteArena
from astrata.eval.ratings import RatingStore
from astrata.eval.substrate import build_eval_domain
from astrata.governance.documents import GovernanceBundle, load_governance_bundle
from astrata.inference.planner import InferencePlanner
from astrata.local.backends.llama_cpp import LlamaCppBackend, LlamaCppLaunchConfig
from astrata.local.catalog import StarterCatalog
from astrata.local.hardware import probe_thermal_state
from astrata.local.lmstudio import LmStudioCli
from astrata.local.models.discovery import effective_search_paths
from astrata.local.operations import OperationProgress, OperationTracker
from astrata.local.telemetry import LocalModelTelemetryStore
from astrata.local.thermal import ThermalController
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.strata_endpoint import StrataEndpointService
from astrata.loop0.runner import Loop0Runner
from astrata.providers.registry import build_default_registry
from astrata.records.models import ArtifactRecord
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.storage.db import AstrataDatabase
from astrata.startup.diagnostics import (
    generate_python_preflight_report,
    load_runtime_report,
    run_startup_reflection,
)


def _cmd_init_db() -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    print(f"Initialized database at {db.path}")
    return 0


def _cmd_doctor() -> int:
    settings = load_settings()
    inference_planner = InferencePlanner()
    preflight = generate_python_preflight_report(settings, python_executable=sys.executable)
    db_path = settings.paths.data_dir / "astrata.db"
    bundle: GovernanceBundle = load_governance_bundle(settings.paths.project_root)
    registry = build_default_registry()
    chooser = RouteChooser(registry)
    default_route = None
    try:
        default_route = chooser.choose(priority=0, urgency=0, risk="moderate").__dict__
    except Exception:
        default_route = None
    db = AstrataDatabase(db_path)
    db.initialize()
    runtime_report = run_startup_reflection(settings, db=db).report
    limits = default_source_limits()
    limits["codex"] = settings.runtime_limits.codex_direct_requests_per_hour
    limits["cli:codex-cli"] = settings.runtime_limits.codex_cli_requests_per_hour
    limits["cli:kilocode"] = settings.runtime_limits.kilocode_requests_per_hour
    limits["cli:gemini-cli"] = settings.runtime_limits.gemini_requests_per_hour
    limits["cli:claude-code"] = settings.runtime_limits.claude_requests_per_hour
    limits["openai"] = settings.runtime_limits.openai_requests_per_hour
    limits["google"] = settings.runtime_limits.google_requests_per_hour
    limits["anthropic"] = settings.runtime_limits.anthropic_requests_per_hour
    limits["custom"] = settings.runtime_limits.custom_requests_per_hour
    quota = QuotaPolicy(db=db, limits_per_source=limits, registry=registry)
    default_quota = quota.assess(default_route or {})
    process_controller = ManagedProcessController(
        state_path=settings.paths.data_dir / "local_runtime.json",
        log_path=settings.paths.data_dir / "local_runtime.log",
    )
    local_runtime = LocalRuntimeManager(
        backends={"llama_cpp": LlamaCppBackend()},
        process_controller=process_controller,
    )
    discovered_paths = local_runtime.discover_models(
        search_paths=settings.local_runtime.model_search_paths
    )
    if settings.local_runtime.llama_cpp_base_url:
        local_runtime.select_runtime(
            backend_id="llama_cpp",
            mode="external",
            endpoint=settings.local_runtime.llama_cpp_base_url,
        )
        local_health = local_runtime.health(
            config={
                "host": settings.local_runtime.llama_cpp_host,
                "port": settings.local_runtime.llama_cpp_port,
            }
        )
    else:
        local_runtime.select_runtime(
            backend_id="llama_cpp",
            mode="managed" if settings.local_runtime.llama_cpp_managed else "managed",
            endpoint=f"http://{settings.local_runtime.llama_cpp_host}:{settings.local_runtime.llama_cpp_port}/health",
        )
        local_health = local_runtime.health(
            config=LlamaCppLaunchConfig(
                binary_path=settings.local_runtime.llama_cpp_binary,
                host=settings.local_runtime.llama_cpp_host,
                port=settings.local_runtime.llama_cpp_port,
            )
        )
    thermal_state = probe_thermal_state(preference=settings.local_runtime.thermal_preference)
    thermal_controller = ThermalController(state_path=settings.paths.data_dir / "thermal_state.json")
    thermal_decision = thermal_controller.evaluate(thermal_state)
    local_recommendation = local_runtime.recommend(
        thermal_preference=settings.local_runtime.thermal_preference
    )
    runtime_client = LocalRuntimeClient()
    native_strata = StrataEndpointService(
        state_path=settings.paths.data_dir / "strata_threads.json",
        runtime_manager=local_runtime,
        runtime_client=runtime_client,
    )
    strata_endpoint_health = None
    if settings.local_runtime.strata_endpoint_base_url:
        strata_endpoint_health = runtime_client.health(
            base_url=settings.local_runtime.strata_endpoint_base_url
        )
    payload = {
        "project_root": str(settings.paths.project_root),
        "data_dir": str(settings.paths.data_dir),
        "db_exists": db_path.exists(),
        "providers": registry.list_available_providers(),
        "inference_sources": registry.list_available_inference_sources(),
        "default_route": default_route,
        "default_route_quota": {
            "allowed": default_quota.allowed,
            "reason": default_quota.reason,
            "usage_last_hour": default_quota.usage_last_hour,
            "limit_per_hour": default_quota.limit_per_hour,
            "next_allowed_at": default_quota.next_allowed_at,
        },
        "local_runtime": {
            "backend_capabilities": [
                capabilities.model_dump(mode="json")
                for capabilities in local_runtime.list_backend_capabilities()
            ],
            "endpoint_profiles": {
                "chat_completions": inference_planner.endpoint_profile("chat_completions").model_dump(mode="json"),
                "agent_session": inference_planner.endpoint_profile("agent_session").model_dump(mode="json"),
            },
            "thermal_preference": settings.local_runtime.thermal_preference,
            "thermal_state": {
                "preference": thermal_state.preference,
                "telemetry_available": thermal_state.telemetry_available,
                "thermal_pressure": thermal_state.thermal_pressure,
                "fans_allowed": thermal_state.fans_allowed,
                "detail": thermal_state.detail,
            },
            "profiles": [
                {
                    "profile_id": profile.profile_id,
                    "label": profile.label,
                    "description": profile.description,
                    "llama_cpp_args": list(profile.llama_cpp_args),
                    "background_aggression": profile.background_aggression,
                    "fan_policy": profile.fan_policy,
                }
                for profile in local_runtime.list_profiles()
            ],
            "search_paths": effective_search_paths(settings.local_runtime.model_search_paths),
            "install_dir": str(settings.local_runtime.model_install_dir),
            "discovered_paths": discovered_paths,
            "models": [model.model_dump(mode="json") for model in local_runtime.model_registry().list_models()],
            "thermal_decision": {
                "sample": thermal_decision.sample,
                "latched": thermal_decision.latched,
                "action": thermal_decision.action,
                "should_start_new_local_work": thermal_decision.should_start_new_local_work,
                "should_throttle_background": thermal_decision.should_throttle_background,
                "reason": thermal_decision.reason,
            },
            "recommendation": {
                "model": None if local_recommendation.model is None else local_recommendation.model.model_dump(mode="json"),
                "profile_id": local_recommendation.profile_id,
                "reason": local_recommendation.reason,
            },
            "selection": None if local_runtime.current_selection() is None else local_runtime.current_selection().model_dump(mode="json"),
            "selections": [selection.model_dump(mode="json") for selection in local_runtime.list_selections()],
            "strata_endpoint": {
                "base_url": settings.local_runtime.strata_endpoint_base_url,
                "health": strata_endpoint_health,
                "native": native_strata.status(),
            },
            "managed_process": None if local_runtime.managed_status() is None else {
                "running": local_runtime.managed_status().running,
                "pid": local_runtime.managed_status().pid,
                "endpoint": local_runtime.managed_status().endpoint,
                "command": local_runtime.managed_status().command,
                "log_path": local_runtime.managed_status().log_path,
                "started_at": local_runtime.managed_status().started_at,
                "detail": local_runtime.managed_status().detail,
            },
            "managed_processes": {
                key: {
                    "running": value.running,
                    "pid": value.pid,
                    "endpoint": value.endpoint,
                    "command": value.command,
                    "log_path": value.log_path,
                    "started_at": value.started_at,
                    "detail": value.detail,
                }
                for key, value in local_runtime.list_managed_statuses().items()
            },
            "health": None if local_health is None else local_health.model_dump(mode="json"),
        },
        "governance": bundle.model_dump(mode="json"),
        "startup": {
            "preflight": preflight,
            "runtime": runtime_report or load_runtime_report(settings),
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def _build_local_runtime_manager() -> tuple[LocalRuntimeManager, object]:
    settings = load_settings()
    process_controller = ManagedProcessController(
        state_path=settings.paths.data_dir / "local_runtime.json",
        log_path=settings.paths.data_dir / "local_runtime.log",
    )
    manager = LocalRuntimeManager(
        backends={"llama_cpp": LlamaCppBackend()},
        process_controller=process_controller,
    )
    return manager, settings


def _build_local_operation_tracker() -> tuple[OperationTracker, object]:
    settings = load_settings()
    tracker = OperationTracker(state_path=settings.paths.data_dir / "local_operations.json")
    return tracker, settings


def _build_local_telemetry_store() -> tuple[LocalModelTelemetryStore, object]:
    settings = load_settings()
    store = LocalModelTelemetryStore(state_path=settings.paths.data_dir / "local_model_telemetry.json")
    return store, settings


def _build_local_rating_store() -> tuple[RatingStore, object]:
    settings = load_settings()
    store = RatingStore(state_path=settings.paths.data_dir / "local_model_ratings.json")
    return store, settings


def _build_eval_observation_store() -> tuple[EvalObservationStore, object]:
    settings = load_settings()
    store = EvalObservationStore(state_path=settings.paths.data_dir / "eval_observations.json")
    return store, settings


def _build_secret_store() -> tuple[SecretStore, object]:
    settings = load_settings()
    store = SecretStore(path=settings.paths.provider_secrets_path)
    return store, settings


def _enrich_local_models_with_telemetry(
    manager: LocalRuntimeManager,
    telemetry: LocalModelTelemetryStore,
) -> list[object]:
    enriched = []
    for model in manager.model_registry().list_models():
        summary = telemetry.summarize(model.path)
        updated = model.model_copy(
            update={
                "benchmark_score": summary.benchmark_score,
                "benchmark_source": summary.benchmark_source,
                "observed_success_rate": summary.observed_success_rate,
                "observed_average_score": summary.observed_average_score,
                "observed_sample_count": summary.observed_sample_count,
            }
        )
        manager.model_registry().replace(updated)
        enriched.append(updated)
    return enriched


def _cmd_local_runtime_start(model_id: str | None, profile_id: str | None) -> int:
    manager, settings = _build_local_runtime_manager()
    manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
    recommendation = manager.recommend(thermal_preference=settings.local_runtime.thermal_preference)
    thermal_state = probe_thermal_state(preference=settings.local_runtime.thermal_preference)
    thermal_controller = ThermalController(state_path=settings.paths.data_dir / "thermal_state.json")
    thermal_decision = thermal_controller.evaluate(thermal_state)
    model = manager.model_registry().get(model_id) if model_id else recommendation.model
    if model is None:
        print(
            json.dumps(
                {
                    "status": "no_model",
                    "message": "No local model is available to start.",
                    "recommendation": {
                        "model": None if recommendation.model is None else recommendation.model.model_dump(mode="json"),
                        "profile_id": recommendation.profile_id,
                        "reason": recommendation.reason,
                    },
                },
                indent=2,
            )
        )
        return 1
    if not thermal_decision.should_start_new_local_work:
        print(
            json.dumps(
                {
                    "status": "deferred_for_thermal",
                    "model": model.model_dump(mode="json"),
                    "thermal_state": {
                        "preference": thermal_state.preference,
                        "telemetry_available": thermal_state.telemetry_available,
                        "thermal_pressure": thermal_state.thermal_pressure,
                        "fans_allowed": thermal_state.fans_allowed,
                        "detail": thermal_state.detail,
                    },
                    "thermal_decision": {
                        "sample": thermal_decision.sample,
                        "latched": thermal_decision.latched,
                        "action": thermal_decision.action,
                        "should_start_new_local_work": thermal_decision.should_start_new_local_work,
                        "should_throttle_background": thermal_decision.should_throttle_background,
                        "reason": thermal_decision.reason,
                    },
                },
                indent=2,
            )
        )
        return 1
    profile = profile_id or recommendation.profile_id
    status = manager.start_managed(
        backend_id="llama_cpp",
        model_id=model.model_id,
        profile_id=profile,
        binary_path=settings.local_runtime.llama_cpp_binary,
        host=settings.local_runtime.llama_cpp_host,
        port=settings.local_runtime.llama_cpp_port,
    )
    print(
        json.dumps(
            {
                "status": "started",
                "model": model.model_dump(mode="json"),
                "profile_id": profile,
                "managed_process": {
                    "running": status.running,
                    "pid": status.pid,
                    "endpoint": status.endpoint,
                    "command": status.command,
                    "log_path": status.log_path,
                    "started_at": status.started_at,
                    "detail": status.detail,
                },
            },
            indent=2,
        )
    )
    return 0


def _cmd_local_runtime_stop() -> int:
    manager, _settings = _build_local_runtime_manager()
    status = manager.stop_managed()
    print(
        json.dumps(
            {
                "status": "stopped",
                "managed_process": {
                    "running": status.running,
                    "pid": status.pid,
                    "endpoint": status.endpoint,
                    "command": status.command,
                    "log_path": status.log_path,
                    "started_at": status.started_at,
                    "detail": status.detail,
                },
            },
            indent=2,
        )
    )
    return 0


def _cmd_local_runtime_status() -> int:
    manager, settings = _build_local_runtime_manager()
    tracker, _ = _build_local_operation_tracker()
    telemetry, _ = _build_local_telemetry_store()
    manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
    models = _enrich_local_models_with_telemetry(manager, telemetry)
    recommendation = manager.recommend(thermal_preference=settings.local_runtime.thermal_preference)
    status = manager.managed_status()
    thermal_state = probe_thermal_state(preference=settings.local_runtime.thermal_preference)
    thermal_controller = ThermalController(state_path=settings.paths.data_dir / "thermal_state.json")
    thermal_decision = thermal_controller.evaluate(thermal_state)
    print(
        json.dumps(
            {
                "thermal_preference": settings.local_runtime.thermal_preference,
                "search_paths": effective_search_paths(settings.local_runtime.model_search_paths),
                "thermal_state": {
                    "preference": thermal_state.preference,
                    "telemetry_available": thermal_state.telemetry_available,
                    "thermal_pressure": thermal_state.thermal_pressure,
                    "fans_allowed": thermal_state.fans_allowed,
                    "detail": thermal_state.detail,
                },
                "thermal_decision": {
                    "sample": thermal_decision.sample,
                    "latched": thermal_decision.latched,
                    "action": thermal_decision.action,
                    "should_start_new_local_work": thermal_decision.should_start_new_local_work,
                    "should_throttle_background": thermal_decision.should_throttle_background,
                    "reason": thermal_decision.reason,
                },
                "recommendation": {
                    "model": None if recommendation.model is None else recommendation.model.model_dump(mode="json"),
                    "profile_id": recommendation.profile_id,
                    "reason": recommendation.reason,
                },
                "models": [model.model_dump(mode="json") for model in models],
                "selections": [selection.model_dump(mode="json") for selection in manager.list_selections()],
                "operations": [record.model_dump(mode="json") for record in tracker.list_operations()[:10]],
                "managed_process": None if status is None else {
                    "running": status.running,
                    "pid": status.pid,
                    "endpoint": status.endpoint,
                    "command": status.command,
                    "log_path": status.log_path,
                    "started_at": status.started_at,
                    "detail": status.detail,
                },
                "managed_processes": {
                    key: {
                        "running": value.running,
                        "pid": value.pid,
                        "endpoint": value.endpoint,
                        "command": value.command,
                        "log_path": value.log_path,
                        "started_at": value.started_at,
                        "detail": value.detail,
                    }
                    for key, value in manager.list_managed_statuses().items()
                },
            },
            indent=2,
        )
    )
    return 0


def _cmd_local_model_catalog() -> int:
    catalog = StarterCatalog()
    print(json.dumps([model.__dict__ for model in catalog.list_models()], indent=2))
    return 0


def _cmd_local_model_install(catalog_id: str | None, url: str | None) -> int:
    manager, settings = _build_local_runtime_manager()
    tracker, _ = _build_local_operation_tracker()
    catalog = StarterCatalog()
    chosen = None if catalog_id is None else catalog.get_model(catalog_id)
    source_url = str(url or (None if chosen is None else chosen.download_url) or "").strip()
    if not source_url:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "No download URL provided. Pass --catalog-id for an installable catalog entry or --url directly.",
                },
                indent=2,
            )
        )
        return 1
    filename = None if chosen is None else chosen.filename
    if not filename:
        filename = Path(source_url.split("?", 1)[0]).name or "model.gguf"
    install_dir = settings.local_runtime.model_install_dir or (settings.paths.data_dir / "models")
    destination_dir = install_dir / ((chosen.catalog_id if chosen else "manual").replace("/", "-"))
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / filename
    op = tracker.start_operation(
        "local_model_install",
        progress=OperationProgress(message=f"Downloading {filename} into Astrata-managed local model storage."),
    )
    try:
        _download_file(source_url, destination, tracker=tracker, operation_id=op.operation_id)
        model = manager.model_registry().adopt(
            str(destination),
            display_name=None if chosen is None else chosen.label,
        )
        completed = tracker.complete_operation(
            op.operation_id,
            result={
                "model_id": model.model_id,
                "path": model.path,
                "family": model.family,
                "catalog_id": None if chosen is None else chosen.catalog_id,
                "download_url": source_url,
            },
        )
        print(
            json.dumps(
                {
                    "status": "installed",
                    "catalog_model": None if chosen is None else chosen.__dict__,
                    "model": model.model_dump(mode="json"),
                    "operation": completed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        failed = tracker.fail_operation(op.operation_id, str(exc))
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "catalog_model": None if chosen is None else chosen.__dict__,
                    "operation": failed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 1


def _cmd_local_model_adopt(path: str) -> int:
    manager, settings = _build_local_runtime_manager()
    tracker, _ = _build_local_operation_tracker()
    op = tracker.start_operation(
        "local_model_adopt",
        progress=OperationProgress(message="Adopting local model into Astrata inventory."),
    )
    try:
        model = manager.model_registry().adopt(path)
        completed = tracker.complete_operation(
            op.operation_id,
            result={
                "model_id": model.model_id,
                "path": model.path,
                "family": model.family,
                "source": model.source,
            },
        )
        print(
            json.dumps(
                {
                    "status": "adopted",
                    "model": model.model_dump(mode="json"),
                    "operation": completed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        failed = tracker.fail_operation(op.operation_id, str(exc))
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "operation": failed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 1


def _download_file(
    url: str,
    destination: Path,
    *,
    tracker: OperationTracker,
    operation_id: str,
    chunk_size: int = 1024 * 1024,
) -> None:
    tmp_destination = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=300) as response, tmp_destination.open("wb") as handle:
        total_bytes = response.headers.get("Content-Length")
        total = None
        if total_bytes:
            try:
                total = int(total_bytes)
            except Exception:
                total = None
        current = 0
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            handle.write(chunk)
            current += len(chunk)
            percent = None if total in {None, 0} else round((current / total) * 100.0, 2)
            tracker.update_operation(
                operation_id,
                OperationProgress(
                    current_bytes=current,
                    total_bytes=total,
                    percent=percent,
                    message=f"Downloaded {current} bytes.",
                ),
            )
    tmp_destination.replace(destination)


def _cmd_local_model_observe(path: str, task_class: str, score: float, success: bool, source: str, note: str | None) -> int:
    telemetry, _settings = _build_local_telemetry_store()
    observation = telemetry.record_observation(
        model_path=path,
        task_class=task_class,
        score=score,
        success=success,
        source=source,
        note=note,
    )
    summary = telemetry.summarize(path)
    print(
        json.dumps(
            {
                "status": "recorded",
                "observation": observation.__dict__,
                "summary": summary.__dict__,
            },
            indent=2,
        )
    )
    return 0


def _cmd_local_model_rank(task_class: str) -> int:
    manager, settings = _build_local_runtime_manager()
    telemetry, _ = _build_local_telemetry_store()
    ratings, _ = _build_local_rating_store()
    manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
    models = _enrich_local_models_with_telemetry(manager, telemetry)
    recommendation = manager.recommend(thermal_preference=settings.local_runtime.thermal_preference)
    evaluation = summarize_local_model_evals(telemetry=telemetry, task_class=task_class, ratings=ratings)
    empiric_winner_path = evaluation.decision.winner_variant_id or evaluation.rating_leader_variant_id
    eval_domain = build_eval_domain(
        subject_kind="local_model",
        task_class=task_class,
        mutation_surface="model_profile",
        environment="local_runtime",
    )
    domain_bucket = ((evaluation.rating_snapshot or {}).get("ratings", {}).get("by_domain", {}).get(eval_domain.rating_domain, {}))
    effective = recommendation.model
    if empiric_winner_path:
        for model in models:
            if model.path == empiric_winner_path:
                effective = model
                break
    ranked = []
    for model in models:
        ranked.append(
            {
                "display_name": model.display_name,
                "path": model.path,
                "family": model.family,
                "role": model.role,
                "tags": list(model.tags),
                "benchmark_score": model.benchmark_score,
                "benchmark_source": model.benchmark_source,
                "observed_success_rate": model.observed_success_rate,
                "observed_average_score": model.observed_average_score,
                "observed_sample_count": model.observed_sample_count,
                "domain_rating": (domain_bucket.get(model.path) or {}).get("rating"),
                "domain_matches": (domain_bucket.get(model.path) or {}).get("matches"),
                "empirical_winner": empiric_winner_path == model.path,
                "recommended": recommendation.model is not None and model.model_id == recommendation.model.model_id,
                "effective_recommended": effective is not None and model.model_id == effective.model_id,
            }
        )
    print(
        json.dumps(
            {
                "thermal_preference": settings.local_runtime.thermal_preference,
                "task_class": task_class,
                "recommended_model_id": None if recommendation.model is None else recommendation.model.model_id,
                "recommended_display_name": None if recommendation.model is None else recommendation.model.display_name,
                "effective_model_id": None if effective is None else effective.model_id,
                "effective_display_name": None if effective is None else effective.display_name,
                "evaluation": {
                    "winner_variant_id": evaluation.decision.winner_variant_id,
                    "rating_leader_variant_id": evaluation.rating_leader_variant_id,
                    "margin": evaluation.decision.margin,
                    "rationale": evaluation.decision.rationale,
                    "summaries": [summary.model_dump(mode="json") for summary in evaluation.summaries],
                },
                "ranked_models": ranked,
            },
            indent=2,
        )
    )
    return 0


def _cmd_local_model_matchup(left_path: str, right_path: str, task_class: str, left_score: float, note: str | None) -> int:
    ratings, _settings = _build_local_rating_store()
    eval_domain = build_eval_domain(
        subject_kind="local_model",
        task_class=task_class,
        mutation_surface="model_profile",
        environment="local_runtime",
    )
    snapshot = ratings.record_matchup(
        domain=eval_domain.rating_domain,
        left_variant_id=left_path,
        right_variant_id=right_path,
        left_score=left_score,
        context={"task_class": task_class, "note": note},
    )
    print(json.dumps(snapshot, indent=2))
    return 0


def _cmd_local_model_eval_pair(
    left_model: str,
    right_model: str,
    task_class: str,
    prompt: str,
    judge_provider_name: str | None,
    judge_cli_tool: str | None,
    allow_thermal_override: bool,
) -> int:
    settings = load_settings()
    thermal_state = probe_thermal_state(preference=settings.local_runtime.thermal_preference)
    thermal_controller = ThermalController(state_path=settings.paths.data_dir / "thermal_state.json")
    thermal_decision = thermal_controller.evaluate(thermal_state)
    if not allow_thermal_override and not thermal_decision.should_start_new_local_work:
        print(
            json.dumps(
                {
                    "status": "deferred_for_thermal",
                    "thermal_state": {
                        "preference": thermal_state.preference,
                        "telemetry_available": thermal_state.telemetry_available,
                        "thermal_pressure": thermal_state.thermal_pressure,
                        "fans_allowed": thermal_state.fans_allowed,
                        "detail": thermal_state.detail,
                    },
                    "thermal_decision": {
                        "sample": thermal_decision.sample,
                        "latched": thermal_decision.latched,
                        "action": thermal_decision.action,
                        "should_start_new_local_work": thermal_decision.should_start_new_local_work,
                        "should_throttle_background": thermal_decision.should_throttle_background,
                        "reason": thermal_decision.reason,
                    },
                },
                indent=2,
            )
        )
        return 1

    registry = build_default_registry()
    judge = registry.get_provider(judge_provider_name)
    if judge is None:
        raise RuntimeError(f"Judge provider {judge_provider_name or 'default'} is not configured.")
    telemetry, _ = _build_local_telemetry_store()
    ratings, _ = _build_local_rating_store()
    tracker, _ = _build_local_operation_tracker()
    lmstudio = LmStudioCli()
    if not lmstudio.is_available():
        raise RuntimeError("LM Studio CLI is not available.")
    op = tracker.start_operation(
        "local_model_eval_pair",
        progress=OperationProgress(message=f"Evaluating {left_model} vs {right_model} for {task_class}."),
    )
    arena = LocalModelArena(lmstudio=lmstudio, telemetry=telemetry, ratings=ratings)
    try:
        result = arena.run_pair_eval(
            task_class=task_class,
            prompt=prompt,
            left_model_key=left_model,
            right_model_key=right_model,
            judge=judge,
            judge_metadata={"cli_tool": judge_cli_tool} if judge_cli_tool else None,
        )
        completed = tracker.complete_operation(
            op.operation_id,
            result={
                "task_class": task_class,
                "left_model": left_model,
                "right_model": right_model,
                "left_score": result.left_score,
                "judge_provider": result.judge_provider,
                "rationale": result.rationale,
            },
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "result": {
                        "task_class": result.task_class,
                        "left_model": result.left.model_key,
                        "right_model": result.right.model_key,
                        "left_duration_seconds": result.left.duration_seconds,
                        "right_duration_seconds": result.right.duration_seconds,
                        "left_score": result.left_score,
                        "judge_provider": result.judge_provider,
                        "rationale": result.rationale,
                    },
                    "operation": completed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        failed = tracker.fail_operation(op.operation_id, str(exc))
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "operation": failed.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 1


def _cmd_loop0_next() -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    runner = Loop0Runner(settings=settings, db=db)
    candidate = runner.next_candidate()
    if candidate is None:
        print(json.dumps({"status": "complete", "message": "No missing Loop 0 candidate paths found."}, indent=2))
        return 0
    print(json.dumps(candidate.__dict__, indent=2))
    return 0


def _cmd_google_sync_models() -> int:
    registry = build_default_registry()
    provider = registry.get_provider("google")
    if provider is None or not hasattr(provider, "sync_models"):
        raise RuntimeError("Google AI Studio provider is not configured.")
    models = provider.sync_models()  # type: ignore[call-arg]
    print(json.dumps({"status": "synced", "count": len(models), "models": models}, indent=2))
    return 0


def _cmd_google_list_models() -> int:
    registry = build_default_registry()
    provider = registry.get_provider("google")
    if provider is None or not hasattr(provider, "cached_models"):
        raise RuntimeError("Google AI Studio provider is not configured.")
    models = provider.cached_models()  # type: ignore[call-arg]
    print(json.dumps({"count": len(models), "models": models}, indent=2))
    return 0


def _cmd_google_set_default_model(model: str) -> int:
    secrets, _settings = _build_secret_store()
    secrets.set_provider_secret("google", "default_model", model)
    print(json.dumps({"status": "stored", "provider": "google", "default_model": model}, indent=2))
    return 0


def _cmd_provider_route_eval_pair(
    left_provider: str,
    right_provider: str,
    task_class: str,
    prompt: str,
    left_model: str | None,
    right_model: str | None,
    left_cli_tool: str | None,
    right_cli_tool: str | None,
    left_base_url: str | None,
    right_base_url: str | None,
    left_thread_id: str | None,
    right_thread_id: str | None,
    allow_degraded_fallback: bool,
    allow_scarce_judge: bool,
    judge_provider_name: str | None,
    judge_cli_tool: str | None,
) -> int:
    registry = build_default_registry()
    observations, _settings = _build_eval_observation_store()
    ratings, settings = _build_local_rating_store()
    arena = ProviderRouteArena(registry=registry, observations=observations, ratings=ratings)
    judge = registry.get_provider(judge_provider_name)
    if judge is None:
        raise RuntimeError(f"Judge provider {judge_provider_name or 'default'} is not configured.")
    result = arena.run_pair_eval(
        task_class=task_class,
        prompt=prompt,
        left_route={
            "provider": left_provider,
            "model": left_model,
            "cli_tool": left_cli_tool,
            "base_url": left_base_url,
            "thread_id": left_thread_id,
            "allow_degraded_fallback": allow_degraded_fallback,
        },
        right_route={
            "provider": right_provider,
            "model": right_model,
            "cli_tool": right_cli_tool,
            "base_url": right_base_url,
            "thread_id": right_thread_id,
            "allow_degraded_fallback": allow_degraded_fallback,
        },
        judge=judge,
        judge_metadata={"cli_tool": judge_cli_tool} if judge_cli_tool else None,
        allow_scarce_judge=allow_scarce_judge,
    )
    summary = arena.summarize(task_class=task_class)
    print(
        json.dumps(
            {
                "result": {
                    "task_class": result.task_class,
                    "left_variant_id": result.left_variant_id,
                    "right_variant_id": result.right_variant_id,
                    "left_score": result.left_score,
                    "rationale": result.rationale,
                    "judge_provider": result.judge_provider,
                    "left_duration_seconds": result.left_duration_seconds,
                    "right_duration_seconds": result.right_duration_seconds,
                    "left_startup_seconds": result.left_startup_seconds,
                    "right_startup_seconds": result.right_startup_seconds,
                    "left_total_wall_seconds": result.left_total_wall_seconds,
                    "right_total_wall_seconds": result.right_total_wall_seconds,
                },
                "summary": {
                    "domain": summary.domain.__dict__,
                    "winner_variant_id": summary.decision.winner_variant_id,
                    "rating_leader_variant_id": summary.rating_leader_variant_id,
                    "margin": summary.decision.margin,
                    "rationale": summary.decision.rationale,
                    "summaries": [item.model_dump(mode="json") for item in summary.summaries],
                },
            },
            indent=2,
        )
    )
    return 0


def _cmd_strata_endpoint_status() -> int:
    settings = load_settings()
    service = StrataEndpointService.from_settings(settings)
    print(json.dumps(service.status(), indent=2))
    return 0


def _cmd_strata_endpoint_chat(message: str, thread_id: str | None, model_id: str | None, allow_degraded_fallback: bool, reasoning_effort: str, response_budget: str) -> int:
    settings = load_settings()
    service = StrataEndpointService.from_settings(settings)
    reply = service.chat(
        content=message,
        thread_id=thread_id,
        model_id=model_id,
        allow_degraded_fallback=allow_degraded_fallback,
        reasoning_effort=reasoning_effort,
        response_budget=response_budget,
    )
    print(
        json.dumps(
            {
                "thread_id": reply.thread_id,
                "content": reply.content,
                "model_id": reply.model_id,
                "reasoning_effort": reply.reasoning_effort,
                "requested_reasoning_effort": reply.requested_reasoning_effort,
                "reasoning_effort_source": reply.reasoning_effort_source,
                "degraded_fallback": reply.degraded_fallback,
                "response_budget": response_budget,
            },
            indent=2,
        )
    )
    return 0


def _cmd_strata_endpoint_set_prompt(prompt_kind: str, value: str) -> int:
    settings = load_settings()
    service = StrataEndpointService.from_settings(settings)
    updated = service.set_prompt(prompt_kind=prompt_kind, value=value)
    print(
        json.dumps(
            {
                    "status": "updated",
                    "prompt_kind": prompt_kind,
                    "prompt_config": {
                    "reasoning_effort_selector_prompt": updated.reasoning_effort_selector_prompt,
                    "default_system_prompt": updated.default_system_prompt,
                },
            },
            indent=2,
        )
    )
    return 0


def _cmd_loop0_run(steps: int) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    payload = _run_loop0_cycle(settings=settings, db=db, steps=steps)
    print(json.dumps(payload, indent=2))
    return 0


def _run_loop0_cycle(*, settings, db: AstrataDatabase, steps: int) -> dict[str, object]:
    lane_runtime = LaneRuntime(settings=settings, db=db)
    lane_turns = lane_runtime.process_pending_turns(lane="prime", limit=5)
    lane_turns.extend(lane_runtime.process_pending_turns(lane="local", limit=5))
    inbox_results = process_inbound_messages(
        db=db,
        project_root=settings.paths.project_root,
        recipient="astrata",
        limit=5,
    )
    runner = Loop0Runner(settings=settings, db=db)
    result = runner.run_steps(steps)
    return {"inbox": inbox_results, "lane_turns": lane_turns, "loop0": result}


def _record_loop0_daemon_heartbeat(
    *,
    db: AstrataDatabase,
    cycle_index: int,
    interval_seconds: int,
    started_at: str,
    finished_at: str,
    status: str,
    payload: dict[str, object],
    error: str | None = None,
) -> ArtifactRecord:
    heartbeat = ArtifactRecord(
        artifact_type="loop0_daemon_heartbeat",
        title=f"Loop0 daemon heartbeat #{cycle_index}",
        description="Periodic runtime heartbeat for overnight Loop 0 execution.",
        content_summary=json.dumps(
            {
                "cycle_index": cycle_index,
                "interval_seconds": interval_seconds,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": status,
                "error": error,
                "summary": {
                    "inbox_count": len(list(payload.get("inbox") or [])),
                    "lane_turns": len(list(payload.get("lane_turns") or [])),
                    "loop0_status": dict(payload.get("loop0") or {}).get("status"),
                    "step_count": len(list(dict(payload.get("loop0") or {}).get("steps") or [])),
                },
            },
            indent=2,
        ),
        provenance={
            "source": "loop0_daemon",
            "cycle_index": cycle_index,
            "interval_seconds": interval_seconds,
            "status": status,
        },
        status="good" if status == "ok" else "degraded",
    )
    db.upsert_artifact(heartbeat)
    return heartbeat


def _cmd_loop0_daemon(steps: int, interval_seconds: int, max_cycles: int | None) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    cycle_index = 0
    try:
        while max_cycles is None or cycle_index < max_cycles:
            cycle_index += 1
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                payload = _run_loop0_cycle(settings=settings, db=db, steps=steps)
                finished_at = datetime.now(timezone.utc).isoformat()
                heartbeat = _record_loop0_daemon_heartbeat(
                    db=db,
                    cycle_index=cycle_index,
                    interval_seconds=interval_seconds,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="ok",
                    payload=payload,
                )
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "cycle_index": cycle_index,
                            "heartbeat_artifact_id": heartbeat.artifact_id,
                            "finished_at": finished_at,
                            "loop0_status": dict(payload.get("loop0") or {}).get("status"),
                        },
                        indent=2,
                    ),
                    flush=True,
                )
            except Exception as exc:
                finished_at = datetime.now(timezone.utc).isoformat()
                heartbeat = _record_loop0_daemon_heartbeat(
                    db=db,
                    cycle_index=cycle_index,
                    interval_seconds=interval_seconds,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="failed",
                    payload={},
                    error=str(exc),
                )
                print(
                    json.dumps(
                        {
                            "status": "failed",
                            "cycle_index": cycle_index,
                            "heartbeat_artifact_id": heartbeat.artifact_id,
                            "finished_at": finished_at,
                            "error": str(exc),
                        },
                        indent=2,
                    ),
                    flush=True,
                )
            if max_cycles is not None and cycle_index >= max_cycles:
                break
            time.sleep(max(1, interval_seconds))
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped", "cycle_index": cycle_index}, indent=2))
        return 0
    return 0


def _cmd_comms_send(recipient: str, intent: str, message: str, kind: str, conversation_id: str) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    lane = PrincipalMessageLane(db=db)
    record = lane.send(
        sender="principal",
        recipient=recipient,
        conversation_id=conversation_id or lane.default_conversation_id(recipient),
        kind=kind,
        intent=intent,
        payload={"message": message},
    )
    print(json.dumps(record.model_dump(mode="json"), indent=2))
    return 0


def _cmd_comms_inbox(recipient: str, unread_only: bool) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    lane = PrincipalMessageLane(db=db)
    messages = lane.list_messages(recipient=recipient, include_acknowledged=not unread_only)
    print(json.dumps([message.model_dump(mode="json") for message in messages], indent=2))
    return 0


def _cmd_comms_ack(communication_id: str) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    lane = PrincipalMessageLane(db=db)
    message = lane.acknowledge(communication_id)
    payload = {"status": "not_found", "communication_id": communication_id} if message is None else message.model_dump(mode="json")
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_comms_process(recipient: str, limit: int) -> int:
    settings = load_settings()
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    created_tasks = process_inbound_messages(
        db=db,
        project_root=settings.paths.project_root,
        recipient=recipient,
        limit=limit,
    )
    print(json.dumps(created_tasks, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrata")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Initialize Astrata's durable record store.")
    sub.add_parser("doctor", help="Print project and governance status.")
    sub.add_parser("loop0-next", help="Show the next bounded Loop 0 implementation candidate.")
    loop0_run = sub.add_parser("loop0-run", help="Run one or more Loop 0 planning/recording cycles.")
    loop0_run.add_argument("--steps", type=int, default=1, help="Number of Loop 0 steps to attempt.")
    loop0_daemon = sub.add_parser("loop0-daemon", help="Run Loop 0 continuously with periodic heartbeat artifacts.")
    loop0_daemon.add_argument("--steps", type=int, default=1, help="Number of Loop 0 steps per cycle.")
    loop0_daemon.add_argument("--interval", type=int, default=60, help="Seconds to sleep between cycles.")
    loop0_daemon.add_argument("--max-cycles", type=int, default=None, help="Optional limit for bounded runs or tests.")
    comms_send = sub.add_parser("comms-send", help="Send a durable principal message into Astrata.")
    comms_send.add_argument("message", help="Message payload to send.")
    comms_send.add_argument("--recipient", default="prime", help="Recipient identity.")
    comms_send.add_argument("--conversation-id", default="", help="Optional durable conversation/thread id.")
    comms_send.add_argument("--intent", default="principal_message", help="Intent label for the message.")
    comms_send.add_argument("--kind", default="request", help="Message kind.")
    comms_inbox = sub.add_parser("comms-inbox", help="Read durable messages for a recipient.")
    comms_inbox.add_argument("--recipient", default="principal", help="Recipient inbox to inspect.")
    comms_inbox.add_argument("--unread-only", action="store_true", help="Hide acknowledged/resolved messages.")
    comms_ack = sub.add_parser("comms-ack", help="Acknowledge a durable message.")
    comms_ack.add_argument("communication_id", help="Message ID to acknowledge.")
    comms_process = sub.add_parser("comms-process", help="Turn inbound messages into request specs and tasks.")
    comms_process.add_argument("--recipient", default="astrata", help="Recipient inbox to process.")
    comms_process.add_argument("--limit", type=int, default=5, help="Maximum number of messages to process.")
    local_start = sub.add_parser("local-runtime-start", help="Start a managed local inference runtime.")
    local_start.add_argument("--model-id", default=None, help="Specific local model ID to run.")
    local_start.add_argument("--profile", default=None, help="Runtime profile override.")
    sub.add_parser("local-runtime-stop", help="Stop the managed local inference runtime.")
    sub.add_parser("local-runtime-status", help="Inspect managed local runtime status.")
    sub.add_parser("local-model-catalog", help="Show the curated local model starter catalog.")
    local_install = sub.add_parser("local-model-install", help="Download a catalog or explicit GGUF model into Astrata-managed storage.")
    local_install.add_argument("--catalog-id", default=None, help="Installable catalog entry to download, such as qwen3.5-0.8b-q4_k_m.")
    local_install.add_argument("--url", default=None, help="Explicit GGUF download URL. Used when no installable catalog entry is provided.")
    local_adopt = sub.add_parser("local-model-adopt", help="Adopt a local model path into Astrata inventory.")
    local_adopt.add_argument("path", help="Path to a local model file.")
    local_observe = sub.add_parser("local-model-observe", help="Record observed performance for a local model.")
    local_observe.add_argument("path", help="Path to the local model file.")
    local_observe.add_argument("--task-class", default="general", help="Task class for the observation.")
    local_observe.add_argument("--score", type=float, required=True, help="Normalized quality score for the run.")
    local_observe.add_argument("--success", action="store_true", help="Mark the observation as successful.")
    local_observe.add_argument("--source", default="observed", help="Observation source label.")
    local_observe.add_argument("--note", default=None, help="Optional note.")
    local_matchup = sub.add_parser("local-model-matchup", help="Record a pairwise local model matchup result.")
    local_matchup.add_argument("left_path", help="Left model path.")
    local_matchup.add_argument("right_path", help="Right model path.")
    local_matchup.add_argument("--task-class", default="general", help="Task class for the matchup.")
    local_matchup.add_argument("--left-score", type=float, required=True, help="Left-side result: 1 win, 0 loss, 0.5 tie.")
    local_matchup.add_argument("--note", default=None, help="Optional matchup note.")
    local_eval_pair = sub.add_parser("local-model-eval-pair", help="Run a bounded pair eval between two local models.")
    local_eval_pair.add_argument("left_model", help="Left local model identifier/path for LM Studio.")
    local_eval_pair.add_argument("right_model", help="Right local model identifier/path for LM Studio.")
    local_eval_pair.add_argument("--task-class", default="general", help="Task class for the eval.")
    local_eval_pair.add_argument("--prompt", required=True, help="Prompt to evaluate both models on.")
    local_eval_pair.add_argument("--judge-provider", default="codex", help="Astrata provider to use as the judge.")
    local_eval_pair.add_argument("--judge-cli-tool", default=None, help="Optional CLI tool hint for judge requests.")
    local_eval_pair.add_argument("--allow-thermal-override", action="store_true", help="Run even if quiet-mode thermal policy would defer.")
    local_rank = sub.add_parser("local-model-rank", help="Show local model ranking inputs and current recommendation.")
    local_rank.add_argument("--task-class", default="general", help="Task class to evaluate, such as coding.")
    sub.add_parser("google-models-sync", help="Sync the Google AI Studio model catalog into Astrata.")
    sub.add_parser("google-models-list", help="List cached Google AI Studio models.")
    google_default = sub.add_parser("google-set-default-model", help="Set Astrata's default Google AI Studio model.")
    google_default.add_argument("model", help="Model identifier to use by default.")
    provider_eval_pair = sub.add_parser("provider-route-eval-pair", help="Run a bounded eval between two provider routes.")
    provider_eval_pair.add_argument("left_provider", help="Left provider name, such as google, cli, codex, or local-model.")
    provider_eval_pair.add_argument("right_provider", help="Right provider name, such as google, cli, codex, or local-model.")
    provider_eval_pair.add_argument("--task-class", default="general", help="Task class for the eval.")
    provider_eval_pair.add_argument("--prompt", required=True, help="Prompt to evaluate both routes on.")
    provider_eval_pair.add_argument("--left-model", default=None, help="Optional left model override.")
    provider_eval_pair.add_argument("--right-model", default=None, help="Optional right model override.")
    provider_eval_pair.add_argument("--left-cli-tool", default=None, help="Optional left CLI tool, when provider is cli.")
    provider_eval_pair.add_argument("--right-cli-tool", default=None, help="Optional right CLI tool, when provider is cli.")
    provider_eval_pair.add_argument("--left-base-url", default=None, help="Optional left base URL, useful for strata-endpoint routes.")
    provider_eval_pair.add_argument("--right-base-url", default=None, help="Optional right base URL, useful for strata-endpoint routes.")
    provider_eval_pair.add_argument("--left-thread-id", default=None, help="Optional persistent thread id for the left route.")
    provider_eval_pair.add_argument("--right-thread-id", default=None, help="Optional persistent thread id for the right route.")
    provider_eval_pair.add_argument("--allow-degraded-fallback", action="store_true", help="Allow explicit degraded fallback on persistent endpoint routes.")
    provider_eval_pair.add_argument("--allow-scarce-judge", action="store_true", help="Allow using a scarce judge route like Codex for side-quest benchmarking.")
    provider_eval_pair.add_argument("--judge-provider", default="codex", help="Astrata provider to use as judge.")
    provider_eval_pair.add_argument("--judge-cli-tool", default=None, help="Optional CLI tool hint for judge requests.")
    sub.add_parser("strata-endpoint-status", help="Inspect Astrata's native persistent Strata-style endpoint.")
    strata_chat = sub.add_parser("strata-endpoint-chat", help="Send a message through Astrata's native persistent Strata-style endpoint.")
    strata_chat.add_argument("message", help="Message content to append.")
    strata_chat.add_argument("--thread-id", default=None, help="Persistent thread id to continue.")
    strata_chat.add_argument("--model-id", default=None, help="Optional local model id override.")
    strata_chat.add_argument("--allow-degraded-fallback", action="store_true", help="Allow explicit degraded fallback semantics.")
    strata_chat.add_argument("--reasoning-effort", choices=("auto", "low", "medium", "high"), default="auto", help="Requested reasoning effort. `auto` asks the local model to choose the lightest adequate effort.")
    strata_chat.add_argument("--response-budget", choices=("instant", "normal", "deep"), default="normal", help="Requested response budget / latency preference.")
    strata_prompt = sub.add_parser("strata-endpoint-set-prompt", help="Update one native Strata-endpoint routing or execution prompt.")
    strata_prompt.add_argument("--prompt-kind", choices=("reasoning_effort_selector", "default_system"), required=True, help="Prompt slot to update.")
    strata_prompt.add_argument("--value", required=True, help="New prompt text.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "init-db":
        return _cmd_init_db()
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "loop0-next":
        return _cmd_loop0_next()
    if args.command == "loop0-run":
        return _cmd_loop0_run(max(1, args.steps))
    if args.command == "loop0-daemon":
        return _cmd_loop0_daemon(max(1, args.steps), max(1, args.interval), args.max_cycles)
    if args.command == "comms-send":
        return _cmd_comms_send(args.recipient, args.intent, args.message, args.kind, args.conversation_id)
    if args.command == "comms-inbox":
        return _cmd_comms_inbox(args.recipient, args.unread_only)
    if args.command == "comms-ack":
        return _cmd_comms_ack(args.communication_id)
    if args.command == "comms-process":
        return _cmd_comms_process(args.recipient, args.limit)
    if args.command == "local-runtime-start":
        return _cmd_local_runtime_start(args.model_id, args.profile)
    if args.command == "local-runtime-stop":
        return _cmd_local_runtime_stop()
    if args.command == "local-runtime-status":
        return _cmd_local_runtime_status()
    if args.command == "local-model-catalog":
        return _cmd_local_model_catalog()
    if args.command == "local-model-install":
        return _cmd_local_model_install(args.catalog_id, args.url)
    if args.command == "local-model-adopt":
        return _cmd_local_model_adopt(args.path)
    if args.command == "local-model-observe":
        return _cmd_local_model_observe(args.path, args.task_class, args.score, args.success, args.source, args.note)
    if args.command == "local-model-matchup":
        return _cmd_local_model_matchup(args.left_path, args.right_path, args.task_class, args.left_score, args.note)
    if args.command == "local-model-eval-pair":
        return _cmd_local_model_eval_pair(
            args.left_model,
            args.right_model,
            args.task_class,
            args.prompt,
            args.judge_provider,
            args.judge_cli_tool,
            args.allow_thermal_override,
        )
    if args.command == "local-model-rank":
        return _cmd_local_model_rank(args.task_class)
    if args.command == "google-models-sync":
        return _cmd_google_sync_models()
    if args.command == "google-models-list":
        return _cmd_google_list_models()
    if args.command == "google-set-default-model":
        return _cmd_google_set_default_model(args.model)
    if args.command == "provider-route-eval-pair":
        return _cmd_provider_route_eval_pair(
            args.left_provider,
            args.right_provider,
            args.task_class,
            args.prompt,
            args.left_model,
            args.right_model,
            args.left_cli_tool,
            args.right_cli_tool,
            args.left_base_url,
            args.right_base_url,
            args.left_thread_id,
            args.right_thread_id,
            args.allow_degraded_fallback,
            args.allow_scarce_judge,
            args.judge_provider,
            args.judge_cli_tool,
        )
    if args.command == "strata-endpoint-status":
        return _cmd_strata_endpoint_status()
    if args.command == "strata-endpoint-chat":
        return _cmd_strata_endpoint_chat(
            args.message,
            args.thread_id,
            args.model_id,
            args.allow_degraded_fallback,
            args.reasoning_effort,
            args.response_budget,
        )
    if args.command == "strata-endpoint-set-prompt":
        return _cmd_strata_endpoint_set_prompt(args.prompt_kind, args.value)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
