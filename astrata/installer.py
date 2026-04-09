"""Bootstrap installer helpers for Astrata's local and desktop shells."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path

from astrata.config.settings import load_settings


def _run(cmd: list[str], *, cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _runtime_python_path(runtime_env_dir: Path) -> Path:
    return runtime_env_dir / "bin" / "python"


def _prepare_runtime_env(*, root: Path, data_dir: Path) -> dict[str, object]:
    runtime_env_dir = data_dir / "runtime-venv"
    created = False
    if not _runtime_python_path(runtime_env_dir).exists():
        builder = venv.EnvBuilder(with_pip=True, clear=False, symlinks=True, upgrade_deps=False)
        builder.create(runtime_env_dir)
        created = True
    runtime_python = _runtime_python_path(runtime_env_dir)
    steps = []
    steps.append(
        _run(
            [
                str(runtime_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ],
            cwd=root,
        )
    )
    steps.append(
        _run(
            [str(runtime_python), "-m", "pip", "install", "-e", "."],
            cwd=root,
        )
    )
    verify = _run(
        [
            str(runtime_python),
            "-c",
            "import fastapi, uvicorn, astrata; print('runtime_ok')",
        ],
        cwd=root,
    )
    steps.append(verify)
    return {
        "runtime_env_dir": str(runtime_env_dir),
        "runtime_python": str(runtime_python),
        "created": created,
        "verified": verify["returncode"] == 0,
        "steps": steps,
    }


def bootstrap(*, prepare_runtime: bool, install_desktop_deps: bool, build_desktop: bool) -> dict[str, object]:
    settings = load_settings()
    root = settings.paths.project_root
    manifest = {
        "project_root": str(root),
        "data_dir": str(settings.paths.data_dir),
        "python": sys.executable,
        "npm": shutil.which("npm"),
        "cargo": shutil.which("cargo"),
        "runtime_prepared": False,
        "runtime_verified": False,
        "runtime_python": None,
        "desktop_deps_installed": False,
        "desktop_built": False,
        "steps": [],
    }
    if prepare_runtime:
        runtime = _prepare_runtime_env(root=root, data_dir=settings.paths.data_dir)
        manifest["steps"].extend(runtime["steps"])
        manifest["runtime_prepared"] = True
        manifest["runtime_verified"] = runtime["verified"]
        manifest["runtime_python"] = runtime["runtime_python"]
    if install_desktop_deps:
        result = _run(["npm", "install"], cwd=root)
        manifest["steps"].append(result)
        manifest["desktop_deps_installed"] = result["returncode"] == 0
    if build_desktop:
        result = _run(["npm", "run", "desktop:build"], cwd=root)
        manifest["steps"].append(result)
        manifest["desktop_built"] = result["returncode"] == 0
    install_manifest_path = settings.paths.data_dir / "install_manifest.json"
    install_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["install_manifest_path"] = str(install_manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-install")
    parser.add_argument("--skip-runtime", action="store_true", help="Skip preparing Astrata's managed runtime environment.")
    parser.add_argument("--desktop-deps", action="store_true", help="Install desktop shell npm dependencies.")
    parser.add_argument("--build-desktop", action="store_true", help="Build the desktop shell after bootstrapping.")
    args = parser.parse_args()
    result = bootstrap(
        prepare_runtime=not args.skip_runtime,
        install_desktop_deps=args.desktop_deps or args.build_desktop,
        build_desktop=args.build_desktop,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
