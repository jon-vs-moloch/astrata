"""Task-scoped git workspace management with worktree-first behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Sequence
from uuid import uuid4


def _slug(value: str) -> str:
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    return collapsed[:48] or "task"


@dataclass(frozen=True)
class GitWorkspace:
    path: Path
    branch: str
    mode: str
    mirrored_to_project_root: bool = False


class GitWorkspaceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self._worktrees_root = self.project_root / ".astrata" / "worktrees"
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

    def prepare_workspace(self, *, task_name: str, task_id: str | None = None) -> GitWorkspace:
        branch = self._branch_name(task_name=task_name, task_id=task_id)
        workspace_path = self._worktrees_root / branch
        if workspace_path.exists():
            return GitWorkspace(path=workspace_path, branch=branch, mode="existing")
        if self._can_use_git_worktree():
            created = self._create_git_worktree(branch=branch, workspace_path=workspace_path)
            if created is not None:
                return created
        self._create_copy_workspace(workspace_path=workspace_path)
        return GitWorkspace(path=workspace_path, branch=branch, mode="copy")

    def _branch_name(self, *, task_name: str, task_id: str | None = None) -> str:
        suffix = _slug(task_id or str(uuid4())[:8])
        return f"astrata-task-{_slug(task_name)}-{suffix}"[:96]

    def _can_use_git_worktree(self) -> bool:
        probe = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return probe is not None and probe.returncode == 0 and probe.stdout.strip() == "true"

    def _create_git_worktree(self, *, branch: str, workspace_path: Path) -> GitWorkspace | None:
        result = self._run_git(["worktree", "add", "-b", branch, str(workspace_path)])
        if result is None or result.returncode != 0:
            return None
        return GitWorkspace(path=workspace_path, branch=branch, mode="git")

    def _create_copy_workspace(self, *, workspace_path: Path) -> None:
        shutil.copytree(
            self.project_root,
            workspace_path,
            ignore=shutil.ignore_patterns(".git", ".astrata", "__pycache__", ".pytest_cache", ".ruff_cache"),
        )

    def _run_git(self, args: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(self.project_root), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None
