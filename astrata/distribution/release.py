"""Release pipeline for Astrata distribution artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION_DIR = ROOT / "deploy" / "cloudflare" / "distribution"
SITE_DIR = DISTRIBUTION_DIR / "site"
DOWNLOADS_DIR = SITE_DIR / "downloads"
UPDATES_DIR = SITE_DIR / "api" / "updates"
DIST_WORKER_DIR = DISTRIBUTION_DIR
TAURI_APP_PATH = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Astrata.app"
TMP_DIR = ROOT / ".astrata" / "release"
ZIP_NAME = "Astrata-macos-app.zip"
R2_BUCKET = "astrata-releases"
WORKER_BASE_URL = "https://astrata-distribution.jonathan-c-meriwether.workers.dev"
PAGES_PROJECT = "astrata-downloads"

CHANNELS: dict[str, dict[str, object]] = {
    "edge": {
        "cadence": "every_build",
        "invite_required": True,
        "site_status": "live",
        "description": "Every successful build. Highest velocity, highest risk.",
    },
    "nightly": {
        "cadence": "nightly",
        "invite_required": True,
        "site_status": "live",
        "description": "Latest promoted daily build for fast-follow testers.",
    },
    "tester": {
        "cadence": "manual_promote",
        "invite_required": True,
        "site_status": "live",
        "description": "Friendly-tester channel before monetization.",
    },
    "stable": {
        "cadence": "manual_release",
        "invite_required": False,
        "site_status": "planned",
        "description": "General-availability release channel.",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ReleaseOptions:
    version: str
    channel: str = "tester"
    build_desktop: bool = True
    upload_r2: bool = True
    deploy_worker: bool = True
    deploy_pages: bool = True
    commit_dirty: bool = True


def _run(cmd: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(cmd)}")


def _ensure_exists(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def _build_desktop() -> None:
    _run(["npm", "run", "desktop:build"], cwd=ROOT)


def _zip_macos_app(*, source_app: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    _run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(source_app),
            str(zip_path),
        ],
        cwd=ROOT,
    )


def _stage_site_artifact(*, channel: str, zip_path: Path) -> Path:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    latest_destination = DOWNLOADS_DIR / ZIP_NAME
    channel_destination = DOWNLOADS_DIR / channel / "macos" / ZIP_NAME
    channel_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(zip_path, latest_destination)
    shutil.copy2(zip_path, channel_destination)
    return channel_destination


def _channel_config(channel: str) -> dict[str, object]:
    normalized = str(channel or "").strip().lower()
    if normalized not in CHANNELS:
        raise ValueError(f"Unknown release channel: {channel}")
    return CHANNELS[normalized]


def _worker_download_url(channel: str) -> str:
    return f"{WORKER_BASE_URL}/downloads/{channel}/macos/{ZIP_NAME}"


def _worker_update_url(channel: str) -> str:
    return f"{WORKER_BASE_URL}/updates/{channel}"


def _r2_object_key(channel: str) -> str:
    return f"{channel}/macos/{ZIP_NAME}"


def _update_manifest(*, channel: str, version: str, published_at: str) -> Path:
    manifest_path = UPDATES_DIR / f"{channel}.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = _channel_config(channel)
    payload["status"] = str(config["site_status"])
    payload["invite_required"] = bool(config["invite_required"])
    payload["release"]["version"] = version
    payload["release"]["published_at"] = published_at
    payload["artifacts"]["macos"]["url"] = _worker_download_url(channel)
    payload["release"]["cadence"] = config["cadence"]
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _upload_r2(*, channel: str, zip_path: Path) -> None:
    _run(
        [
            "npx",
            "wrangler",
            "r2",
            "object",
            "put",
            f"{R2_BUCKET}/{_r2_object_key(channel)}",
            "--file",
            str(zip_path),
            "--remote",
        ],
        cwd=ROOT,
    )


def _deploy_worker() -> None:
    _run(["npx", "wrangler", "deploy"], cwd=DIST_WORKER_DIR)


def _deploy_pages(*, commit_dirty: bool) -> None:
    cmd = [
        "npx",
        "wrangler",
        "pages",
        "deploy",
        str(SITE_DIR),
        "--project-name",
        PAGES_PROJECT,
    ]
    if commit_dirty:
        cmd.append("--commit-dirty=true")
    _run(cmd, cwd=ROOT)


def run_release(options: ReleaseOptions) -> dict[str, str | bool]:
    _channel_config(options.channel)
    if options.build_desktop:
        _build_desktop()
    _ensure_exists(TAURI_APP_PATH, label="Built macOS app bundle")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = TMP_DIR / ZIP_NAME
    _zip_macos_app(source_app=TAURI_APP_PATH, zip_path=zip_path)
    staged_path = _stage_site_artifact(channel=options.channel, zip_path=zip_path)
    published_at = _utc_now()
    manifest_path = _update_manifest(channel=options.channel, version=options.version, published_at=published_at)
    if options.upload_r2:
        _upload_r2(channel=options.channel, zip_path=zip_path)
    if options.deploy_worker:
        _deploy_worker()
    if options.deploy_pages:
        _deploy_pages(commit_dirty=options.commit_dirty)
    return {
        "status": "ok",
        "channel": options.channel,
        "version": options.version,
        "zip_path": str(zip_path),
        "staged_path": str(staged_path),
        "manifest_path": str(manifest_path),
        "worker_download_url": _worker_download_url(options.channel),
        "worker_update_url": _worker_update_url(options.channel),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-release")
    parser.add_argument("--version", required=True, help="Release version to publish, such as 0.1.0-dev1.")
    parser.add_argument(
        "--channel",
        default="tester",
        help="Release channel. Supported values: edge, nightly, tester, stable.",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip rebuilding the desktop app before packaging.")
    parser.add_argument("--skip-upload", action="store_true", help="Skip uploading the artifact to R2.")
    parser.add_argument("--skip-worker-deploy", action="store_true", help="Skip redeploying the distribution worker.")
    parser.add_argument("--skip-pages-deploy", action="store_true", help="Skip redeploying the Pages download site.")
    parser.add_argument("--no-commit-dirty", action="store_true", help="Do not pass --commit-dirty=true to Pages deploy.")
    args = parser.parse_args()

    result = run_release(
        ReleaseOptions(
            version=args.version,
            channel=args.channel,
            build_desktop=not args.skip_build,
            upload_r2=not args.skip_upload,
            deploy_worker=not args.skip_worker_deploy,
            deploy_pages=not args.skip_pages_deploy,
            commit_dirty=not args.no_commit_dirty,
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
