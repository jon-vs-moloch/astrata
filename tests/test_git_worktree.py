from pathlib import Path
import subprocess

from astrata.git import GitWorkspaceManager


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "astrata@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Astrata"], check=True, capture_output=True)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)


def test_git_workspace_manager_creates_worktree(tmp_path: Path):
    _init_git_repo(tmp_path)
    manager = GitWorkspaceManager(tmp_path)
    workspace = manager.prepare_workspace(task_name="Improve intake", task_id="task-123")
    assert workspace.mode == "git"
    assert workspace.path.exists()
    assert workspace.branch.startswith("astrata-task-improve-intake-task-123")
    probe = subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert str(workspace.path) in probe.stdout
