import json
from pathlib import Path

from astrata.distribution import release


def test_update_manifest_sets_version_and_worker_url(tmp_path: Path, monkeypatch):
    updates_dir = tmp_path / "api" / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    manifest = updates_dir / "tester.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "Astrata",
                "channel": "tester",
                "status": "planned",
                "invite_required": True,
                "release": {"version": None, "published_at": None, "notes_url": "/"},
                "artifacts": {"macos": {"url": None, "format": "zip", "signature_url": None}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "UPDATES_DIR", updates_dir)

    path = release._update_manifest(channel="tester", version="0.1.0-dev1", published_at="2026-04-12T16:30:00Z")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["status"] == "live"
    assert payload["release"]["version"] == "0.1.0-dev1"
    assert payload["release"]["published_at"] == "2026-04-12T16:30:00Z"
    assert payload["artifacts"]["macos"]["url"] == release._worker_download_url("tester")
    assert payload["release"]["cadence"] == "manual_promote"


def test_stage_site_artifact_copies_zip(tmp_path: Path, monkeypatch):
    downloads_dir = tmp_path / "downloads"
    zip_path = tmp_path / "Astrata-macos-app.zip"
    zip_path.write_bytes(b"astrata")
    monkeypatch.setattr(release, "DOWNLOADS_DIR", downloads_dir)

    staged = release._stage_site_artifact(channel="nightly", zip_path=zip_path)

    assert staged.exists()
    assert staged.read_bytes() == b"astrata"
    assert (downloads_dir / "Astrata-macos-app.zip").read_bytes() == b"astrata"
    assert (downloads_dir / "nightly" / "macos" / "Astrata-macos-app.zip").read_bytes() == b"astrata"


def test_channel_urls_are_derived_from_channel_name():
    assert release._worker_download_url("nightly").endswith("/downloads/nightly/macos/Astrata-macos-app.zip")
    assert release._worker_update_url("edge").endswith("/updates/edge")
