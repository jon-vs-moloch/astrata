"""Utility helpers for execution-layer command handling.

The execution package needs a small set of consistent helpers for working
with shell-like command specifications without forcing the rest of the codebase
to care about whether a command started as a string, sequence, or richer
configuration object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import shlex
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CommandSpec:
    """Normalized command description used by execution components.

    Attributes:
        argv: Tokenized command arguments.
        cwd: Optional working directory.
        env: Environment overrides to apply when running the command.
    """

    argv: tuple[str, ...]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def as_list(self) -> list[str]:
        """Return the command as a mutable argv list."""
        return list(self.argv)

    def with_env(self, extra_env: Mapping[str, str] | None = None) -> "CommandSpec":
        """Return a copy of the command spec with merged environment values."""
        merged = dict(self.env)
        if extra_env:
            merged.update({str(k): str(v) for k, v in extra_env.items()})
        return CommandSpec(argv=self.argv, cwd=self.cwd, env=merged)


CommandInput = str | Sequence[str] | CommandSpec


def normalize_command(command: CommandInput) -> CommandSpec:
    """Normalize a command input into a :class:`CommandSpec`.

    Strings are split using shell-like parsing. Sequences are converted to a
    tuple of strings. Existing command specs are returned unchanged.
    """
    if isinstance(command, CommandSpec):
        return command
    if isinstance(command, str):
        argv = tuple(shlex.split(command))
    else:
        argv = tuple(str(part) for part in command)

    if not argv:
        raise ValueError("command must contain at least one argument")

    return CommandSpec(argv=argv)


def render_command(command: CommandInput) -> str:
    """Render a normalized command as a shell-safe display string."""
    spec = normalize_command(command)
    return shlex.join(spec.argv)


def resolve_cwd(cwd: str | os.PathLike[str] | None) -> Path | None:
    """Resolve an optional working directory into a :class:`Path`.

    The path is expanded for user home references and made absolute without
    requiring the target to already exist.
    """
    if cwd is None:
        return None
    return Path(cwd).expanduser().resolve()


def build_env(
    base: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
    *,
    include_process_env: bool = True,
) -> dict[str, str]:
    """Build an environment mapping for command execution."""
    env: dict[str, str] = {}
    if include_process_env:
        env.update(os.environ)
    if base:
        env.update({str(k): str(v) for k, v in base.items()})
    if overrides:
        env.update({str(k): str(v) for k, v in overrides.items()})
    return env


def prepare_command(
    command: CommandInput,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
    include_process_env: bool = True,
) -> CommandSpec:
    """Create a fully prepared command spec from loose inputs."""
    spec = normalize_command(command)
    prepared_cwd = resolve_cwd(cwd) if cwd is not None else spec.cwd
    prepared_env = build_env(
        base=base_env if base_env is not None else spec.env,
        overrides=env,
        include_process_env=include_process_env,
    )
    return CommandSpec(argv=spec.argv, cwd=prepared_cwd, env=prepared_env)


def iter_display_lines(commands: Iterable[CommandInput]) -> Iterable[str]:
    """Yield human-friendly command renderings for logs or plans."""
    for command in commands:
        yield render_command(command)
