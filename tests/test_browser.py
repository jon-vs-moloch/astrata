from pathlib import Path


from astrata.bootstrap import DependencyBootstrapService
from astrata.browser.service import BrowserService, PlaywrightBrowserBackend


class FakeBrowserBackend:
    def capture_page(
        self,
        *,
        url: str,
        out_dir: Path,
        full_page: bool = False,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        selector: str | None = None,
        include_html: bool = True,
    ) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "page.png"
        screenshot_path.write_bytes(b"png")
        html = "<html><body><main>Hello <b>Astrata</b><script>ignore()</script></main></body></html>"
        html_path = out_dir / "page.html"
        if include_html:
            html_path.write_text(html, encoding="utf-8")
        return {
            "requested_url": url,
            "final_url": url + "/final",
            "title": "Astrata Test Page",
            "screenshot_path": str(screenshot_path),
            "html_path": str(html_path) if include_html else None,
            "html": html if include_html else None,
            "viewport": {"width": width, "height": height},
            "full_page": full_page,
            "selector": selector,
            "wait_ms": wait_ms,
        }

    def interact_page(
        self,
        *,
        url: str,
        out_dir: Path,
        action: str,
        selector: str | None = None,
        text: str | None = None,
        delta_y: int | None = None,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        include_html: bool = True,
    ) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "page.png"
        screenshot_path.write_bytes(b"png")
        html = (
            f"<html><body><main>Action {action} selector={selector or ''} "
            f"text={text or ''} delta={delta_y or 0}</main></body></html>"
        )
        html_path = out_dir / "page.html"
        if include_html:
            html_path.write_text(html, encoding="utf-8")
        return {
            "requested_url": url,
            "final_url": url + f"/{action}",
            "title": f"Astrata {action}",
            "screenshot_path": str(screenshot_path),
            "html_path": str(html_path) if include_html else None,
            "html": html if include_html else None,
            "action": action,
            "selector": selector,
            "text": text,
            "delta_y": delta_y,
            "wait_ms": wait_ms,
        }


def test_browser_service_persists_session_and_snapshot(tmp_path: Path):
    service = BrowserService(
        state_path=tmp_path / "browser_state.json",
        working_dir=tmp_path / "browser",
        backend=FakeBrowserBackend(),
    )

    snapshot = service.inspect_page(url="https://example.com", label="Example", include_html=True)
    session = service.get_session(snapshot.session_id)
    persisted_snapshot = service.get_snapshot(snapshot.snapshot_id)
    status = service.status()

    assert session is not None
    assert session.label == "Example"
    assert session.latest_snapshot_id == snapshot.snapshot_id
    assert persisted_snapshot is not None
    assert persisted_snapshot.title == "Astrata Test Page"
    assert "Hello Astrata" in persisted_snapshot.readable_text
    assert status["session_count"] == 1
    assert status["snapshot_count"] == 1


def test_browser_service_continues_existing_session(tmp_path: Path):
    service = BrowserService(
        state_path=tmp_path / "browser_state.json",
        working_dir=tmp_path / "browser",
        backend=FakeBrowserBackend(),
    )

    first = service.inspect_page(url="https://example.com", label="Example", include_html=False)
    second = service.inspect_page(
        url="https://example.com/docs",
        session_id=first.session_id,
        include_html=False,
    )
    session = service.get_session(first.session_id)

    assert second.session_id == first.session_id
    assert session is not None
    assert session.last_url.endswith("/docs/final")
    assert len(session.snapshot_ids) == 2


def test_browser_service_persists_interactions(tmp_path: Path):
    service = BrowserService(
        state_path=tmp_path / "browser_state.json",
        working_dir=tmp_path / "browser",
        backend=FakeBrowserBackend(),
    )

    snapshot = service.inspect_page(url="https://example.com", label="Example", include_html=False)
    click = service.click(session_id=snapshot.session_id, selector="#go", include_html=True)
    typed = service.type_text(session_id=snapshot.session_id, selector="#box", text="hello", include_html=True)
    scrolled = service.scroll(session_id=snapshot.session_id, delta_y=500, include_html=True)
    status = service.status()
    session = service.get_session(snapshot.session_id)

    assert click.action == "click"
    assert typed.action == "type"
    assert scrolled.action == "scroll"
    assert "Action click" in click.readable_text
    assert status["interaction_count"] == 3
    assert session is not None
    assert session.last_url.endswith("/scroll")


def test_playwright_backend_retries_after_install(monkeypatch, tmp_path: Path):
    backend = PlaywrightBrowserBackend(python_executable="python-test", auto_install_assets=True)
    calls = {"count": 0, "installed": 0}

    def fake_run_capture(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Executable doesn't exist")
        return {
            "requested_url": kwargs["url"],
            "final_url": kwargs["url"],
            "title": "ok",
            "screenshot_path": str(kwargs["screenshot_path"]),
            "html_path": None,
            "html": None,
            "viewport": {"width": kwargs["width"], "height": kwargs["height"]},
            "full_page": kwargs["full_page"],
            "selector": kwargs["selector"],
            "wait_ms": kwargs["wait_ms"],
        }

    def fake_install():
        calls["installed"] += 1

    monkeypatch.setattr(backend, "_run_capture", fake_run_capture)
    monkeypatch.setattr(backend, "_install_chromium", fake_install)

    result = backend.capture_page(url="https://example.com", out_dir=tmp_path)

    assert result["final_url"] == "https://example.com"
    assert calls["count"] == 2
    assert calls["installed"] == 1


def test_playwright_backend_installs_python_package_when_module_is_missing(monkeypatch, tmp_path: Path):
    backend = PlaywrightBrowserBackend(python_executable="python-test", auto_install_assets=True)
    calls = {"count": 0, "package_installed": 0}

    def fake_run_capture(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ModuleNotFoundError("No module named 'playwright'")
        return {
            "requested_url": kwargs["url"],
            "final_url": kwargs["url"],
            "title": "ok",
            "screenshot_path": str(kwargs["screenshot_path"]),
            "html_path": None,
            "html": None,
            "viewport": {"width": kwargs["width"], "height": kwargs["height"]},
            "full_page": kwargs["full_page"],
            "selector": kwargs["selector"],
            "wait_ms": kwargs["wait_ms"],
        }

    def fake_install():
        calls["package_installed"] += 1

    monkeypatch.setattr(backend, "_run_capture", fake_run_capture)
    monkeypatch.setattr(backend, "_install_playwright_package", fake_install)

    result = backend.capture_page(url="https://example.com", out_dir=tmp_path)

    assert result["final_url"] == "https://example.com"
    assert calls["count"] == 2
    assert calls["package_installed"] == 1


def test_playwright_backend_status_reports_package_bootstrap(monkeypatch):
    backend = PlaywrightBrowserBackend(python_executable="python-test", auto_install_assets=True)
    monkeypatch.setattr(backend, "_playwright_module_available", lambda: False)

    status = backend.asset_status()

    assert status["playwright_module_available"] is False
    assert status["auto_install_assets"] is True
    assert status["auto_install_python_package"] is True
    assert status["playwright_requirement"] == "playwright>=1.40.0"
    assert status["bootstrap"]["auto_install"] is True


def test_dependency_bootstrap_service_records_installs(tmp_path: Path):
    state_path = tmp_path / "dependency_bootstrap.json"
    service = DependencyBootstrapService(state_path=state_path, python_executable="python-test", auto_install=True)

    installed = {"count": 0}

    changed = service.ensure_dependency(
        key="example:resource",
        install=lambda: installed.__setitem__("count", installed["count"] + 1),
        metadata={"kind": "example", "name": "resource"},
    )

    assert changed is True
    assert installed["count"] == 1
    status = service.status()
    assert status["resources"]["example:resource"]["status"] == "installed"
    assert status["resources"]["example:resource"]["attempts"] == 1


def test_dependency_bootstrap_service_tracks_python_package_availability(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "dependency_bootstrap.json"
    service = DependencyBootstrapService(state_path=state_path, python_executable="python-test", auto_install=True)
    monkeypatch.setattr(service, "is_python_package_available", lambda module_name: True)

    changed = service.ensure_python_package(module_name="playwright", requirement="playwright>=1.40.0")

    assert changed is False
    status = service.status()
    assert status["resources"]["python-package:playwright"]["status"] == "available"
