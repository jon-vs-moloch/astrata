from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.loop0.planner import Loop0Planner


def test_planner_derives_fallback_remediation_for_degraded_route():
    planner = Loop0Planner()
    attempts = [
        {
            "outcome": "failed",
            "failure_kind": "connection",
            "provenance": {
                "candidate_key": "astrata-procedures-models",
                "inspection": {"missing": ["astrata/procedures/models.py"], "existing": []},
            },
            "resource_usage": {
                "implementation": {
                    "requested_route": {"provider": "ollama", "model": None, "cli_tool": None},
                }
            },
        }
    ]
    route_health = {
        "ollama||": {
            "recent_failures": 2,
            "last_failure_kind": "connection",
            "last_error": "connection refused",
            "updated_at": "now",
        }
    }
    snapshots = planner.derive_remediation_candidates(
        project_root=Path("/tmp"),
        attempts=attempts,
        route_health=route_health,
        available_providers=["ollama"],
    )
    assert snapshots
    assert snapshots[0].strategy == "fallback_only"
    assert "currently degraded" in snapshots[0].reason


def test_planner_prefers_alternate_provider_when_one_is_available():
    planner = Loop0Planner()
    attempts = [
        {
            "outcome": "failed",
            "failure_kind": "connection",
            "provenance": {
                "candidate_key": "astrata-procedures-models",
                "inspection": {"missing": ["astrata/procedures/models.py"], "existing": []},
            },
            "resource_usage": {
                "implementation": {
                    "requested_route": {"provider": "ollama", "model": None, "cli_tool": None},
                }
            },
        }
    ]
    route_health = {
        "ollama||": {
            "recent_failures": 3,
            "last_failure_kind": "connection",
            "last_error": "connection refused",
            "updated_at": "now",
        },
        "openai|gpt-test|": {
            "recent_failures": 0,
            "last_failure_kind": None,
            "last_error": None,
            "updated_at": "now",
        },
    }
    snapshots = planner.derive_remediation_candidates(
        project_root=Path("/tmp"),
        attempts=attempts,
        route_health=route_health,
        available_providers=["ollama", "openai"],
    )
    assert snapshots
    assert snapshots[0].strategy == "alternate_provider"
    assert "alternate provider `openai`" in snapshots[0].reason


def test_planner_skips_remediation_when_gap_is_already_closed(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        planner = Loop0Planner()
        target = base / "astrata/procedures/models.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('VALUE = "ok"\n')
        attempts = [
            {
                "outcome": "failed",
                "provenance": {
                    "candidate_key": "astrata-procedures-models",
                    "inspection": {"missing": ["astrata/procedures/models.py"], "existing": []},
                },
                "resource_usage": {
                    "implementation": {
                        "requested_route": {"provider": "ollama", "model": None, "cli_tool": None},
                    }
                },
            }
        ]
        snapshots = planner.derive_remediation_candidates(
            project_root=base,
            attempts=attempts,
            route_health={"ollama||": {"recent_failures": 3}},
            available_providers=["ollama", "openai"],
        )
        assert snapshots == []

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_planner_derives_strengthening_candidate_for_thin_module(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        planner = Loop0Planner()
        target = base / "astrata/execution/runner.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '"""Minimal execution runner."""\n\n'
            "class ExecutionRunner:\n"
            "    def normalize(self, request):\n"
            "        return request\n"
        )
        plan_text = "`astrata/execution/runner.py`"
        snapshots = planner.derive_weak_candidates(base, plan_text)
        assert snapshots
        assert snapshots[0].strategy == "strengthen"
        assert snapshots[0].expected_paths == ["astrata/execution/runner.py"]
        assert "thin enough to justify strengthening" in snapshots[0].reason
        assert "Leverage:" in snapshots[0].reason

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_planner_prefers_trials_substrate_as_high_leverage_strengthening_target(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        planner = Loop0Planner()
        variants_dir = base / "astrata/variants"
        variants_dir.mkdir(parents=True, exist_ok=True)
        (variants_dir / "trials.py").write_text(
            '"""Minimal variant trial helpers."""\n\nfrom pydantic import BaseModel\n\nclass TrialResult(BaseModel):\n    variant_id: str\n    score: float\n',
            encoding="utf-8",
        )
        (base / "astrata/context").mkdir(parents=True, exist_ok=True)
        (base / "astrata/context" / "telemetry.py").write_text(
            '"""Minimal context telemetry."""\n\nVALUE = "ok"\n',
            encoding="utf-8",
        )
        plan_text = "`astrata/variants/trials.py`\n`astrata/context/telemetry.py`"
        snapshots = planner.derive_weak_candidates(base, plan_text)
        assert snapshots
        assert snapshots[0].expected_paths == ["astrata/variants/trials.py"]
        leverage = dict(snapshots[0].metadata.get("leverage") or {})
        assert int(leverage.get("score") or 0) > 0
        assert "improvement lever" in str(leverage.get("summary") or "")

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))
