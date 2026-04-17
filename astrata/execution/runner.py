from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Generic, Mapping, MutableMapping, Protocol, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class Executable(Protocol[InputT, OutputT]):
    """Protocol for executable units used by the runtime.

    The runner intentionally depends on a very small contract so it can work
    with simple callables, lightweight task objects, or richer pipeline steps.
    """

    def __call__(self, payload: InputT, **kwargs: Any) -> OutputT:
        ...


@dataclass(slots=True)
class ExecutionResult(Generic[OutputT]):
    """Structured result returned by :class:`ExecutionRunner`.

    Attributes:
        value: The value produced by the executable.
        duration_seconds: Wall-clock execution time.
        metadata: Optional execution metadata collected by the runner.
    """

    value: OutputT
    duration_seconds: float
    metadata: MutableMapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionRunner(Generic[InputT, OutputT]):
    """Small runtime helper for invoking executable units consistently.

    The runner centralizes a few behaviors that are useful even in an MVP:
    timing, metadata propagation, and a stable surface for future extensions
    such as logging, cancellation, retries, or tracing hooks.
    """

    name: str = "default"
    collect_timing: bool = True

    def run(
        self,
        executable: Executable[InputT, OutputT],
        payload: InputT,
        *,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ExecutionResult[OutputT]:
        """Execute a unit of work and normalize its result.

        Args:
            executable: Callable-like unit of work.
            payload: Input passed as the first argument to the executable.
            metadata: Optional metadata copied into the result.
            **kwargs: Additional keyword arguments forwarded to the executable.

        Returns:
            A structured execution result containing the produced value and
            optional timing information.
        """

        result_metadata: MutableMapping[str, Any] = dict(metadata or {})
        result_metadata.setdefault("runner", self.name)

        started_at = perf_counter()
        value = executable(payload, **kwargs)
        duration = perf_counter() - started_at if self.collect_timing else 0.0

        if self.collect_timing:
            result_metadata.setdefault("timed", True)

        return ExecutionResult(
            value=value,
            duration_seconds=duration,
            metadata=result_metadata,
        )


def run_execution(
    executable: Executable[InputT, OutputT],
    payload: InputT,
    *,
    runner: ExecutionRunner[InputT, OutputT] | None = None,
    metadata: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> ExecutionResult[OutputT]:
    """Convenience function for one-off execution.

    This keeps call sites concise while still returning the richer structured
    result exposed by :class:`ExecutionRunner`.
    """

    active_runner = runner or ExecutionRunner()
    return active_runner.run(executable, payload, metadata=metadata, **kwargs)
