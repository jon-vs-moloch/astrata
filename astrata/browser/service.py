"""Internal browser service for Astrata."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys
from typing import Any, Protocol
from urllib.parse import urlparse

from astrata.bootstrap import DependencyBootstrapService
from astrata.browser.models import BrowserInteractionRecord, BrowserPageSnapshot, BrowserSession


class BrowserBackend(Protocol):
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
    ) -> dict[str, Any]:
        ...

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
    ) -> dict[str, Any]:
        ...


class PlaywrightBrowserBackend:
    """Playwright-backed browser capture backend."""

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        auto_install_assets: bool = True,
        bootstrap_service: DependencyBootstrapService | None = None,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        self._auto_install_assets = auto_install_assets
        self._playwright_requirement = "playwright>=1.40.0"
        self._bootstrap = bootstrap_service or DependencyBootstrapService(
            python_executable=self._python_executable,
            auto_install=auto_install_assets,
        )

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
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are allowed.")
        if not parsed.hostname:
            raise ValueError("URL host is required.")

        width = max(320, min(int(width), 3840))
        height = max(240, min(int(height), 2160))
        wait_ms = max(0, min(int(wait_ms), 10000))

        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "page.png"
        html_path = out_dir / "page.html"

        try:
            return self._run_with_bootstrap(
                operation=self._run_capture,
                url=url,
                screenshot_path=screenshot_path,
                html_path=html_path,
                full_page=full_page,
                wait_ms=wait_ms,
                width=width,
                height=height,
                selector=selector,
                include_html=include_html,
            )
        except Exception as exc:  # pragma: no cover - wrapper error path exercised via tests
            raise RuntimeError(f"browser capture failed: {self._format_browser_error(exc)}") from exc

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
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are allowed.")
        if not parsed.hostname:
            raise ValueError("URL host is required.")

        width = max(320, min(int(width), 3840))
        height = max(240, min(int(height), 2160))
        wait_ms = max(0, min(int(wait_ms), 10000))
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "page.png"
        html_path = out_dir / "page.html"
        try:
            return self._run_with_bootstrap(
                operation=self._run_action,
                url=url,
                screenshot_path=screenshot_path,
                html_path=html_path,
                action=action,
                selector=selector,
                text=text,
                delta_y=delta_y,
                wait_ms=wait_ms,
                width=width,
                height=height,
                include_html=include_html,
            )
        except Exception as exc:  # pragma: no cover - wrapper error path exercised via tests
            raise RuntimeError(f"browser interaction failed: {self._format_browser_error(exc)}") from exc

    def asset_status(self) -> dict[str, Any]:
        bootstrap_status = self._bootstrap.status()
        return {
            "playwright_module_available": self._playwright_module_available(),
            "python_executable": self._python_executable,
            "auto_install_assets": self._auto_install_assets,
            "auto_install_python_package": self._auto_install_assets,
            "playwright_requirement": self._playwright_requirement,
            "bootstrap": bootstrap_status,
        }

    def _run_with_bootstrap(self, operation, **kwargs) -> dict[str, Any]:
        try:
            return operation(**kwargs)
        except Exception as exc:
            if self._should_install_playwright_package(exc):
                self._install_playwright_package()
                return operation(**kwargs)
            if self._should_install_assets(exc):
                self._install_chromium()
                return operation(**kwargs)
            raise

    def _run_capture(
        self,
        *,
        url: str,
        screenshot_path: Path,
        html_path: Path,
        full_page: bool,
        wait_ms: int,
        width: int,
        height: int,
        selector: str | None,
        include_html: bool,
    ) -> dict[str, Any]:
        browser = None
        context = None
        page = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                if selector:
                    page.wait_for_selector(selector, state="visible", timeout=8_000)
                if wait_ms > 0:
                    page.wait_for_timeout(wait_ms)
                page.screenshot(path=str(screenshot_path), full_page=bool(full_page))
                html = page.content() if include_html else None
                if html is not None:
                    html_path.write_text(html, encoding="utf-8")
                return {
                    "requested_url": url,
                    "final_url": page.url,
                    "title": page.title(),
                    "screenshot_path": str(screenshot_path),
                    "html_path": str(html_path) if html is not None else None,
                    "html": html,
                    "viewport": {"width": width, "height": height},
                    "full_page": bool(full_page),
                    "selector": selector,
                    "wait_ms": wait_ms,
                }
        finally:
            self._close_page_objects(page=page, context=context, browser=browser)

    def _run_action(
        self,
        *,
        url: str,
        screenshot_path: Path,
        html_path: Path,
        action: str,
        selector: str | None,
        text: str | None,
        delta_y: int | None,
        wait_ms: int,
        width: int,
        height: int,
        include_html: bool,
    ) -> dict[str, Any]:
        browser = None
        context = None
        page = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                if action == "click":
                    if not selector:
                        raise ValueError("selector is required for click")
                    page.click(selector, timeout=8_000)
                elif action == "type":
                    if not selector:
                        raise ValueError("selector is required for type")
                    page.fill(selector, str(text or ""), timeout=8_000)
                elif action == "scroll":
                    page.mouse.wheel(0, int(delta_y or 800))
                else:
                    raise ValueError(f"Unsupported browser action: {action}")
                if wait_ms > 0:
                    page.wait_for_timeout(wait_ms)
                page.screenshot(path=str(screenshot_path), full_page=True)
                html = page.content() if include_html else None
                if html is not None:
                    html_path.write_text(html, encoding="utf-8")
                return {
                    "requested_url": url,
                    "final_url": page.url,
                    "title": page.title(),
                    "screenshot_path": str(screenshot_path),
                    "html_path": str(html_path) if html is not None else None,
                    "html": html,
                    "viewport": {"width": width, "height": height},
                    "action": action,
                    "selector": selector,
                    "text": text,
                    "delta_y": delta_y,
                    "wait_ms": wait_ms,
                }
        finally:
            self._close_page_objects(page=page, context=context, browser=browser)

    def _close_page_objects(self, *, page: Any, context: Any, browser: Any) -> None:
        try:
            if page is not None:
                page.close()
        except Exception:
            pass
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass

    def _should_install_assets(self, exc: Exception) -> bool:
        return self._auto_install_assets and (
            "Executable doesn't exist" in str(exc) or "playwright install" in str(exc)
        )

    def _playwright_module_available(self) -> bool:
        return self._bootstrap.is_python_package_available("playwright")

    def _should_install_playwright_package(self, exc: Exception) -> bool:
        if not self._auto_install_assets:
            return False
        message = str(exc)
        if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", "") == "playwright":
            return True
        return "No module named 'playwright'" in message or 'No module named "playwright"' in message

    def _install_playwright_package(self) -> None:
        self._bootstrap.ensure_python_package(
            module_name="playwright",
            requirement=self._playwright_requirement,
        )

    def _install_chromium(self) -> None:
        self._bootstrap.ensure_playwright_browser("chromium")

    def _format_browser_error(self, exc: Exception) -> str:
        message = str(exc)
        if self._should_install_playwright_package(exc):
            return (
                f"{message}. Astrata attempted to ensure the Playwright Python package is installed automatically. "
                f"If this still fails, run: {self._python_executable} -m pip install {self._playwright_requirement}"
            )
        if "Executable doesn't exist" in message or "playwright install" in message:
            return (
                f"{message}. Astrata attempted to ensure Chromium is installed automatically. "
                f"If this still fails, run: {self._python_executable} -m playwright install chromium"
            )
        return message


class BrowserService:
    def __init__(
        self,
        *,
        state_path: Path,
        working_dir: Path,
        backend: BrowserBackend | None = None,
    ) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._working_dir = working_dir
        self._working_dir.mkdir(parents=True, exist_ok=True)
        self._backend = backend or PlaywrightBrowserBackend()

    @classmethod
    def from_settings(cls, settings, *, backend: BrowserBackend | None = None) -> "BrowserService":
        if backend is None:
            backend = PlaywrightBrowserBackend(
                bootstrap_service=DependencyBootstrapService(
                    state_path=settings.paths.data_dir / "dependency_bootstrap.json",
                    auto_install=True,
                )
            )
        return cls(
            state_path=settings.paths.data_dir / "browser_state.json",
            working_dir=settings.paths.data_dir / "browser",
            backend=backend,
        )

    def status(self) -> dict[str, Any]:
        payload = self._load()
        sessions = payload.get("sessions", {})
        snapshots = payload.get("snapshots", {})
        interactions = payload.get("interactions", {})
        backend_status = self._backend.asset_status() if hasattr(self._backend, "asset_status") else {}
        return {
            "session_count": len(sessions),
            "snapshot_count": len(snapshots),
            "interaction_count": len(interactions),
            "sessions": list(sessions.values())[-8:],
            "working_dir": str(self._working_dir),
            "state_path": str(self._state_path),
            "backend": backend_status,
        }

    def inspect_page(
        self,
        *,
        url: str,
        session_id: str | None = None,
        label: str = "",
        full_page: bool = False,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        selector: str | None = None,
        include_html: bool = True,
    ) -> BrowserPageSnapshot:
        payload = self._load()
        session = self._resolve_session(payload=payload, session_id=session_id, url=url, label=label)
        snapshot_dir = self._working_dir / session.session_id / f"snap-{len(session.snapshot_ids) + 1:04d}"
        capture = self._backend.capture_page(
            url=url,
            out_dir=snapshot_dir,
            full_page=full_page,
            wait_ms=wait_ms,
            width=width,
            height=height,
            selector=selector,
            include_html=include_html,
        )
        html = capture.get("html")
        readable_text = self._readable_text_from_html(str(html or ""))
        snapshot = BrowserPageSnapshot(
            session_id=session.session_id,
            requested_url=str(capture.get("requested_url") or url),
            final_url=str(capture.get("final_url") or url),
            title=str(capture.get("title") or ""),
            selector=selector,
            wait_ms=wait_ms,
            viewport=dict(capture.get("viewport") or {"width": width, "height": height}),
            full_page=bool(capture.get("full_page")),
            screenshot_path=capture.get("screenshot_path"),
            html_path=capture.get("html_path"),
            readable_text=readable_text,
            metadata={
                "backend": type(self._backend).__name__,
                "include_html": include_html,
            },
        )
        session.last_url = snapshot.final_url
        session.status = "captured"
        session.latest_snapshot_id = snapshot.snapshot_id
        session.snapshot_ids.append(snapshot.snapshot_id)
        session.updated_at = snapshot.created_at
        payload.setdefault("sessions", {})[session.session_id] = session.model_dump(mode="json")
        payload.setdefault("snapshots", {})[snapshot.snapshot_id] = snapshot.model_dump(mode="json")
        self._store(payload)
        return snapshot

    def get_session(self, session_id: str) -> BrowserSession | None:
        payload = self._load()
        raw = dict(payload.get("sessions", {}).get(session_id) or {})
        return BrowserSession(**raw) if raw else None

    def get_snapshot(self, snapshot_id: str) -> BrowserPageSnapshot | None:
        payload = self._load()
        raw = dict(payload.get("snapshots", {}).get(snapshot_id) or {})
        return BrowserPageSnapshot(**raw) if raw else None

    def click(
        self,
        *,
        session_id: str,
        selector: str,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        include_html: bool = True,
    ) -> BrowserInteractionRecord:
        return self._interact(
            session_id=session_id,
            action="click",
            selector=selector,
            wait_ms=wait_ms,
            width=width,
            height=height,
            include_html=include_html,
        )

    def type_text(
        self,
        *,
        session_id: str,
        selector: str,
        text: str,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        include_html: bool = True,
    ) -> BrowserInteractionRecord:
        return self._interact(
            session_id=session_id,
            action="type",
            selector=selector,
            text=text,
            wait_ms=wait_ms,
            width=width,
            height=height,
            include_html=include_html,
        )

    def scroll(
        self,
        *,
        session_id: str,
        delta_y: int = 800,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        include_html: bool = True,
    ) -> BrowserInteractionRecord:
        return self._interact(
            session_id=session_id,
            action="scroll",
            delta_y=delta_y,
            wait_ms=wait_ms,
            width=width,
            height=height,
            include_html=include_html,
        )

    def _resolve_session(
        self,
        *,
        payload: dict[str, Any],
        session_id: str | None,
        url: str,
        label: str,
    ) -> BrowserSession:
        if session_id:
            existing = dict(payload.get("sessions", {}).get(session_id) or {})
            if existing:
                return BrowserSession(**existing)
        return BrowserSession(
            label=label,
            start_url=url,
            last_url=url,
            status="navigating",
        )

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"sessions": {}, "snapshots": {}, "interactions": {}}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"sessions": {}, "snapshots": {}, "interactions": {}}
        if not isinstance(payload, dict):
            return {"sessions": {}, "snapshots": {}, "interactions": {}}
        payload.setdefault("sessions", {})
        payload.setdefault("snapshots", {})
        payload.setdefault("interactions", {})
        return payload

    def _store(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _readable_text_from_html(self, html: str) -> str:
        if not html:
            return ""
        cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"&nbsp;", " ", cleaned)
        cleaned = re.sub(r"&amp;", "&", cleaned)
        cleaned = " ".join(cleaned.split())
        if len(cleaned) > 50_000:
            return cleaned[:50_000] + " ...[content truncated]..."
        return cleaned

    def _interact(
        self,
        *,
        session_id: str,
        action: str,
        selector: str | None = None,
        text: str | None = None,
        delta_y: int | None = None,
        wait_ms: int = 350,
        width: int = 1440,
        height: int = 900,
        include_html: bool = True,
    ) -> BrowserInteractionRecord:
        payload = self._load()
        session_raw = dict(payload.get("sessions", {}).get(session_id) or {})
        if not session_raw:
            raise ValueError(f"Unknown browser session: {session_id}")
        session = BrowserSession(**session_raw)
        url = str(session.last_url or session.start_url or "").strip()
        if not url:
            raise ValueError(f"Browser session has no navigable URL: {session_id}")
        interaction_dir = self._working_dir / session.session_id / f"action-{len(payload.get('interactions', {})) + 1:04d}"
        result = self._backend.interact_page(
            url=url,
            out_dir=interaction_dir,
            action=action,
            selector=selector,
            text=text,
            delta_y=delta_y,
            wait_ms=wait_ms,
            width=width,
            height=height,
            include_html=include_html,
        )
        html = result.get("html")
        record = BrowserInteractionRecord(
            session_id=session.session_id,
            action=action,
            selector=selector,
            text=text,
            delta_y=delta_y,
            requested_url=str(result.get("requested_url") or url),
            final_url=str(result.get("final_url") or url),
            title=str(result.get("title") or ""),
            screenshot_path=result.get("screenshot_path"),
            html_path=result.get("html_path"),
            readable_text=self._readable_text_from_html(str(html or "")),
            metadata={
                "backend": type(self._backend).__name__,
                "wait_ms": wait_ms,
                "include_html": include_html,
            },
        )
        session.last_url = record.final_url
        session.updated_at = record.created_at
        session.status = f"interacted:{action}"
        payload.setdefault("sessions", {})[session.session_id] = session.model_dump(mode="json")
        payload.setdefault("interactions", {})[record.interaction_id] = record.model_dump(mode="json")
        self._store(payload)
        return record
