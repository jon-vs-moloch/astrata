"""Minimal verification substrate for Loop 0."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    result: str
    confidence: float
    summary: str
    evidence: dict[str, object] = Field(default_factory=dict)


def inspect_expected_paths(project_root: Path, expected_paths: list[str]) -> dict[str, object]:
    missing: list[str] = []
    existing: list[str] = []
    python_syntax: dict[str, str] = {}
    module_quality: dict[str, dict[str, Any]] = {}

    for rel in expected_paths:
        path = project_root / rel
        if path.exists():
            existing.append(rel)
            if path.suffix == ".py":
                python_syntax[rel] = _check_python_syntax(path)
                module_quality[rel] = inspect_python_module_quality(path)
        else:
            missing.append(rel)

    return {
        "existing": existing,
        "missing": missing,
        "python_syntax": python_syntax,
        "module_quality": module_quality,
    }


def verify_expected_paths(project_root: Path, expected_paths: list[str]) -> VerificationResult:
    inspection = inspect_expected_paths(project_root, expected_paths)
    missing = inspection["missing"]
    existing = inspection["existing"]

    if not missing:
        return VerificationResult(
            result="pass",
            confidence=0.95,
            summary="All expected paths are present.",
            evidence=inspection,
        )

    confidence = 0.85 if existing else 0.65
    return VerificationResult(
        result="fail",
        confidence=confidence,
        summary=f"Missing {len(missing)} expected path(s).",
        evidence=inspection,
    )


def verify_gap_candidate(project_root: Path, expected_paths: list[str]) -> VerificationResult:
    inspection = inspect_expected_paths(project_root, expected_paths)
    missing = inspection["missing"]
    syntax_errors = {
        path: status
        for path, status in inspection["python_syntax"].items()
        if status != "ok"
    }

    if not missing:
        return VerificationResult(
            result="fail",
            confidence=0.9,
            summary="Candidate is no longer missing any expected paths.",
            evidence=inspection,
        )

    if syntax_errors:
        return VerificationResult(
            result="uncertain",
            confidence=0.5,
            summary="Candidate gap exists, but one or more existing Python files do not parse cleanly.",
            evidence={**inspection, "syntax_errors": syntax_errors},
        )

    return VerificationResult(
        result="pass",
        confidence=0.85 if inspection["existing"] else 0.75,
        summary="Candidate represents a real unmet implementation slice.",
        evidence=inspection,
    )


def inspect_weak_expected_paths(project_root: Path, expected_paths: list[str]) -> dict[str, object]:
    inspection = inspect_expected_paths(project_root, expected_paths)
    module_quality = dict(inspection.get("module_quality") or {})
    weak_paths = {
        rel_path: quality
        for rel_path, quality in module_quality.items()
        if bool(quality.get("is_weak"))
    }
    inspection["weak_paths"] = weak_paths
    return inspection


def verify_weak_candidate(project_root: Path, expected_paths: list[str]) -> VerificationResult:
    inspection = inspect_weak_expected_paths(project_root, expected_paths)
    weak_paths = dict(inspection.get("weak_paths") or {})
    if weak_paths:
        return VerificationResult(
            result="pass",
            confidence=0.8,
            summary=f"Detected {len(weak_paths)} thin implementation slice(s) worth strengthening.",
            evidence=inspection,
        )
    return VerificationResult(
        result="fail",
        confidence=0.85,
        summary="Candidate no longer appears thin enough to justify a strengthening pass.",
        evidence=inspection,
    )


def verify_strengthening_candidate(
    project_root: Path,
    expected_paths: list[str],
    *,
    baseline_inspection: dict[str, object],
    written_paths: list[str] | None = None,
) -> VerificationResult:
    before = dict(baseline_inspection.get("weak_paths") or {})
    after_inspection = inspect_weak_expected_paths(project_root, expected_paths)
    after = dict(after_inspection.get("weak_paths") or {})
    if not before:
        return VerificationResult(
            result="uncertain",
            confidence=0.45,
            summary="Strengthening candidate had no baseline weakness evidence to compare against.",
            evidence={"before": baseline_inspection, "after": after_inspection},
        )
    if not after:
        return VerificationResult(
            result="pass",
            confidence=0.9,
            summary="Strengthening pass cleared the previously weak implementation slice.",
            evidence={"before": baseline_inspection, "after": after_inspection, "written_paths": written_paths or []},
        )
    improved_paths: dict[str, dict[str, Any]] = {}
    for rel_path, before_quality in before.items():
        after_quality = dict(after.get(rel_path) or {})
        if not after_quality:
            improved_paths[rel_path] = {"before": before_quality, "after": None}
            continue
        if int(after_quality.get("weakness_score") or 0) < int(before_quality.get("weakness_score") or 0):
            improved_paths[rel_path] = {"before": before_quality, "after": after_quality}
    if improved_paths:
        return VerificationResult(
            result="pass",
            confidence=0.72,
            summary="Strengthening pass improved weakness metrics, but the slice still looks somewhat thin.",
            evidence={
                "before": baseline_inspection,
                "after": after_inspection,
                "improved_paths": improved_paths,
                "written_paths": written_paths or [],
            },
        )
    return VerificationResult(
        result="fail",
        confidence=0.8,
        summary="Strengthening pass did not measurably improve the weak implementation slice.",
        evidence={"before": baseline_inspection, "after": after_inspection, "written_paths": written_paths or []},
    )


def _check_python_syntax(path: Path) -> str:
    try:
        ast.parse(path.read_text())
    except SyntaxError as exc:
        return f"syntax_error:{exc.lineno}:{exc.offset}"
    return "ok"


def inspect_python_module_quality(path: Path) -> dict[str, Any]:
    text = path.read_text()
    parsed = ast.parse(text)
    total_lines = len(text.splitlines())
    code_lines = _count_code_lines(text)
    top_level_defs = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in parsed.body)
    method_count = 0
    pass_statements = 0
    placeholder_terms = 0
    constant_returns_input = 0
    lowered = text.lower()
    for marker in ("minimal", "stub", "placeholder", "todo"):
        placeholder_terms += lowered.count(marker)
    for node in ast.walk(parsed):
        if isinstance(node, ast.ClassDef):
            method_count += sum(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                for child in node.body
            )
        elif isinstance(node, ast.Pass):
            pass_statements += 1
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
            if node.value.id in {"request", "value", "result", "payload"}:
                constant_returns_input += 1
    weakness_score = 0
    reasons: list[str] = []
    if code_lines <= 12:
        weakness_score += 2
        reasons.append("very_small_module")
    if total_lines <= 24:
        weakness_score += 1
        reasons.append("short_file")
    if top_level_defs <= 2:
        weakness_score += 1
        reasons.append("few_top_level_defs")
    if pass_statements:
        weakness_score += 2
        reasons.append("pass_statements_present")
    if placeholder_terms:
        weakness_score += 1
        reasons.append("placeholder_language")
    if constant_returns_input:
        weakness_score += 1
        reasons.append("identity_wrapper_pattern")
    is_weak = weakness_score >= 3
    return {
        "path": str(path),
        "total_lines": total_lines,
        "code_lines": code_lines,
        "top_level_defs": top_level_defs,
        "method_count": method_count,
        "pass_statements": pass_statements,
        "placeholder_terms": placeholder_terms,
        "identity_wrapper_pattern": constant_returns_input,
        "weakness_score": weakness_score,
        "weakness_reasons": reasons,
        "is_weak": is_weak,
    }


def _count_code_lines(text: str) -> int:
    count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in {'"""', "'''"}:
            continue
        count += 1
    return count
