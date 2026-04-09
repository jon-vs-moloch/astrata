"""Execution helpers for running pipeline steps.

This module provides a small, dependency-light executor abstraction that can
run callables in sequence while consistently capturing results, timing, and
errors. The implementation is intentionally modest for the MVP, but it is
useful enough to serve as the common execution surface for higher-level
runtime orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping


StepCallable = Callable[..., Any]


@dataclass(slots=True)
class ExecutionResult:
    """Represents the outcome of a single executed step."""

    name: str
    success: bool
    value: Any = None
    error: Exception | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result into a JSON-friendly dictionary."""
        payload = {
            "name": self.name,
            "success": self.success,
            "value": self.value,
            "duration_seconds": self.duration_seconds,
            "metadata": dict(self.metadata),
        }
        if self.error is not None:
            payload["error"] = {
                "type": type(self.error).__name__,
                "message": str(self.error),
            }
        else:
            payload["error"] = None
        return payload


@dataclass(slots=True)
class ExecutionSummary:
    """Aggregate execution information for a collection of steps."""

    results: list[ExecutionResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return ``True`` when all recorded results are successful."""
        return all(result.success for result in self.results)

    @property
    def total_duration_seconds(self) -> float:
        """Return the sum of all step durations."""
        return sum(result.duration_seconds for result in self.results)

    def append(self, result: ExecutionResult) -> None:
        """Add a result to the summary."""
        self.results.append(result)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the summary into a JSON-friendly dictionary."""
        return {
            "success": self.success,
            "total_duration_seconds": self.total_duration_seconds,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(slots=True)
class ExecutionStep:
    """A named unit of work accepted by :class:`Executor`."""

    name: str
    func: StepCallable
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Executor:
    """Execute named callables and capture consistent result data."""

    def __init__(self, *, raise_on_error: bool = False) -> None:
        self.raise_on_error = raise_on_error

    def execute(
        self,
        name: str,
        func: StepCallable,
        *args: Any,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute a single callable and return a structured result.

        Parameters
        ----------
        name:
            Human-readable step name.
        func:
            Callable to invoke.
        metadata:
            Optional metadata copied onto the returned result.
        *args, **kwargs:
            Positional and keyword arguments passed to ``func``.
        """
        start = perf_counter()
        copied_metadata = dict(metadata or {})
        try:
            value = func(*args, **kwargs)
        except Exception as exc:
            duration = perf_counter() - start
            result = ExecutionResult(
                name=name,
                success=False,
                error=exc,
                duration_seconds=duration,
                metadata=copied_metadata,
            )
            if self.raise_on_error:
                raise
            return result

        duration = perf_counter() - start
        return ExecutionResult(
            name=name,
            success=True,
            value=value,
            duration_seconds=duration,
            metadata=copied_metadata,
        )

    def execute_step(self, step: ExecutionStep) -> ExecutionResult:
        """Execute a preconfigured :class:`ExecutionStep`."""
        return self.execute(
            step.name,
            step.func,
            *step.args,
            metadata=step.metadata,
            **step.kwargs,
        )

    def execute_all(self, steps: Iterable[ExecutionStep]) -> ExecutionSummary:
        """Execute all supplied steps in order.

        If ``raise_on_error`` is false, execution continues after failures and
        the returned summary contains both successes and failures.
        """
        summary = ExecutionSummary()
        for step in steps:
            result = self.execute_step(step)
            summary.append(result)
            if not result.success and self.raise_on_error:
                break
        return summary


def execute_callable(
    func: StepCallable,
    *args: Any,
    name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> ExecutionResult:
    """Convenience wrapper for one-off execution.

    The default step name is derived from ``func.__name__`` when available.
    """
    executor = Executor()
    step_name = name or getattr(func, "__name__", "anonymous")
    return executor.execute(step_name, func, *args, metadata=metadata, **kwargs)
