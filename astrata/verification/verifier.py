"""Verification helpers for Astrata runtime outputs.

This module provides a small, dependency-light verification layer that can be
used by bootstrap and MVP flows to assert basic correctness properties over
structured results. The implementation is intentionally conservative: it favors
clear diagnostics and predictable behavior over framework-specific abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence


Predicate = Callable[[Any], bool]


@dataclass(frozen=True)
class VerificationIssue:
    """Represents a single verification failure or warning."""

    path: str
    message: str
    expected: Any | None = None
    actual: Any | None = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable representation of the issue."""
        return {
            "path": self.path,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class VerificationResult:
    """Aggregate result returned by verifier entry points."""

    ok: bool
    issues: tuple[VerificationIssue, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        """Raise a ValueError when the result contains errors."""
        if self.ok:
            return
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in self.issues)
        raise ValueError(details or "verification failed")

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable representation of the result."""
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class Verifier:
    """Simple verifier for nested mapping-based payloads.

    Rules are intentionally small and composable so callers can use this class in
    early-stage runtime flows without introducing heavyweight validation
    dependencies.
    """

    def __init__(self) -> None:
        self._issues: list[VerificationIssue] = []

    def verify_required_fields(
        self,
        payload: Mapping[str, Any],
        required_fields: Sequence[str],
        *,
        prefix: str = "",
    ) -> VerificationResult:
        """Verify that all required field names are present in the payload."""
        self._issues.clear()
        for field_name in required_fields:
            if field_name not in payload:
                self._issues.append(
                    VerificationIssue(
                        path=_join_path(prefix, field_name),
                        message="missing required field",
                        expected="present",
                        actual=None,
                    )
                )
        return self._result()

    def verify_equal(self, actual: Any, expected: Any, *, path: str = "value") -> VerificationResult:
        """Verify that two values are equal."""
        self._issues.clear()
        if actual != expected:
            self._issues.append(
                VerificationIssue(
                    path=path,
                    message="values do not match",
                    expected=expected,
                    actual=actual,
                )
            )
        return self._result()

    def verify_predicates(
        self,
        payload: Mapping[str, Any],
        predicates: Mapping[str, Predicate],
        *,
        prefix: str = "",
    ) -> VerificationResult:
        """Verify named fields against predicate callables."""
        self._issues.clear()
        for field_name, predicate in predicates.items():
            path = _join_path(prefix, field_name)
            if field_name not in payload:
                self._issues.append(
                    VerificationIssue(
                        path=path,
                        message="missing field for predicate check",
                        expected="present",
                        actual=None,
                    )
                )
                continue
            value = payload[field_name]
            try:
                passed = predicate(value)
            except Exception as exc:  # pragma: no cover - defensive guard
                self._issues.append(
                    VerificationIssue(
                        path=path,
                        message=f"predicate raised {exc.__class__.__name__}: {exc}",
                        actual=value,
                    )
                )
                continue
            if not passed:
                self._issues.append(
                    VerificationIssue(
                        path=path,
                        message="predicate check failed",
                        expected=getattr(predicate, "__name__", "predicate"),
                        actual=value,
                    )
                )
        return self._result()

    def verify_nested_subset(
        self,
        payload: Mapping[str, Any],
        expected_subset: Mapping[str, Any],
        *,
        prefix: str = "",
    ) -> VerificationResult:
        """Verify that payload contains the expected nested subset."""
        self._issues.clear()
        self._walk_subset(payload, expected_subset, prefix=prefix)
        return self._result()

    def verify_all(self, results: Iterable[VerificationResult]) -> VerificationResult:
        """Combine multiple verification results into one aggregate result."""
        issues: list[VerificationIssue] = []
        for result in results:
            issues.extend(result.issues)
        return VerificationResult(ok=not issues, issues=tuple(issues))

    def _walk_subset(
        self,
        payload: Mapping[str, Any],
        expected_subset: Mapping[str, Any],
        *,
        prefix: str,
    ) -> None:
        for key, expected in expected_subset.items():
            path = _join_path(prefix, key)
            if key not in payload:
                self._issues.append(
                    VerificationIssue(
                        path=path,
                        message="missing required nested field",
                        expected=expected,
                        actual=None,
                    )
                )
                continue
            actual = payload[key]
            if isinstance(expected, Mapping) and isinstance(actual, Mapping):
                self._walk_subset(actual, expected, prefix=path)
                continue
            if actual != expected:
                self._issues.append(
                    VerificationIssue(
                        path=path,
                        message="nested value does not match",
                        expected=expected,
                        actual=actual,
                    )
                )

    def _result(self) -> VerificationResult:
        return VerificationResult(ok=not self._issues, issues=tuple(self._issues))


def verify_output(
    payload: Mapping[str, Any],
    *,
    required_fields: Sequence[str] | None = None,
    predicates: Mapping[str, Predicate] | None = None,
    expected_subset: Mapping[str, Any] | None = None,
) -> VerificationResult:
    """Convenience entry point for common output verification flows."""
    verifier = Verifier()
    results: list[VerificationResult] = []
    if required_fields:
        results.append(verifier.verify_required_fields(payload, required_fields))
    if predicates:
        results.append(verifier.verify_predicates(payload, predicates))
    if expected_subset:
        results.append(verifier.verify_nested_subset(payload, expected_subset))
    if not results:
        return VerificationResult(ok=True)
    return verifier.verify_all(results)


def _join_path(prefix: str, field_name: str) -> str:
    if not prefix:
        return field_name
    if not field_name:
        return prefix
    return f"{prefix}.{field_name}"
