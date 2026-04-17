"""Fast local web shell for Astrata."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
import uvicorn

from astrata.ui.service import AstrataUIService, MessageDraft


STATIC_DIR = Path(__file__).resolve().parent / "static"


class UISendMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    recipient: str = "prime"
    conversation_id: str = ""
    intent: str = "principal_message"
    kind: str = "request"


class UIActionResponse(BaseModel):
    status: str
    detail: dict


class UISettingsRequest(BaseModel):
    update_channel: str | None = None


class UIInviteRedeemRequest(BaseModel):
    email: str = Field(min_length=1)
    display_name: str = ""
    invite_code: str = Field(min_length=1)


class UILinkDesktopRequest(BaseModel):
    email: str = Field(min_length=1)
    label: str = "Astrata Desktop"
    relay_endpoint: str = ""


class UIConnectorOAuthSetupRequest(BaseModel):
    callback_url: str = Field(min_length=1)
    label: str = "ChatGPT Connector"
    email: str = ""
    relay_endpoint: str = ""


class UIRelayPairingRequest(BaseModel):
    label: str = "Astrata Desktop"
    ttl_minutes: int = 15


def create_app() -> FastAPI:
    app = FastAPI(title="Astrata UI", version="0.1.0")
    service = AstrataUIService()

    @app.middleware("http")
    async def disable_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/static/{asset_path:path}")
    def static_asset(asset_path: str) -> Response:
        path = (STATIC_DIR / asset_path).resolve()
        if not path.is_file() or STATIC_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=404, detail={"status": "not_found", "asset": asset_path})
        return FileResponse(
            path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    @app.on_event("startup")
    def startup_reflection() -> None:
        service.ensure_startup_reports()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/summary")
    def summary() -> dict:
        return service.snapshot()

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str) -> dict:
        result = service.task_detail(task_id)
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result)
        return result

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "service": "astrata-ui"}

    @app.get("/api/startup")
    def startup() -> dict:
        return service.ensure_startup_reports()

    @app.get("/api/settings")
    def settings() -> dict:
        return service.get_preferences()

    @app.post("/api/settings")
    def set_settings(payload: UISettingsRequest) -> UIActionResponse:
        result = service.set_preferences(payload.model_dump(exclude_none=True))
        return UIActionResponse(status="ok", detail=result)

    @app.post("/api/messages")
    def send_message(payload: UISendMessageRequest) -> UIActionResponse:
        record = service.send_message(
            MessageDraft(
                message=payload.message,
                recipient=payload.recipient,
                conversation_id=payload.conversation_id,
                intent=payload.intent,
                kind=payload.kind,
            )
        )
        return UIActionResponse(status="sent", detail=record)

    @app.post("/api/messages/{communication_id}/ack")
    def acknowledge_message(communication_id: str) -> UIActionResponse:
        result = service.acknowledge_message(communication_id)
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result)
        return UIActionResponse(status="acknowledged", detail=result)

    @app.post("/api/loop0/run")
    def run_loop(steps: int = 1) -> UIActionResponse:
        result = service.run_loop(steps=max(1, steps))
        return UIActionResponse(status="ok", detail=result)

    @app.post("/api/local-runtime/start")
    def start_local_runtime(model_id: str | None = None, profile_id: str | None = None) -> UIActionResponse:
        result = service.start_local_runtime(model_id=model_id, profile_id=profile_id)
        return UIActionResponse(status=str(result.get("status") or "ok"), detail=result)

    @app.post("/api/local-runtime/stop")
    def stop_local_runtime() -> UIActionResponse:
        result = service.stop_local_runtime()
        return UIActionResponse(status="stopped", detail=result)

    @app.post("/api/account/invite/redeem")
    def redeem_invite(payload: UIInviteRedeemRequest) -> UIActionResponse:
        result = service.redeem_invite_code(
            email=payload.email,
            display_name=payload.display_name,
            invite_code=payload.invite_code,
        )
        return UIActionResponse(status=str(result.get("status") or "ok"), detail=result)

    @app.post("/api/account/device/link")
    def link_desktop(payload: UILinkDesktopRequest) -> UIActionResponse:
        result = service.pair_desktop_device(
            email=payload.email,
            label=payload.label,
            relay_endpoint=payload.relay_endpoint,
        )
        return UIActionResponse(status=str(result.get("status") or "ok"), detail=result)

    @app.post("/api/connector/oauth/setup")
    def connector_oauth_setup(payload: UIConnectorOAuthSetupRequest) -> UIActionResponse:
        result = service.connector_oauth_setup(
            callback_url=payload.callback_url,
            label=payload.label,
            email=payload.email,
            relay_endpoint=payload.relay_endpoint,
        )
        return UIActionResponse(status=str(result.get("status") or "ok"), detail=result)

    @app.post("/api/relay/pairing")
    def relay_pairing(payload: UIRelayPairingRequest) -> UIActionResponse:
        result = {
            "status": "replaced_by_oauth",
            "label": payload.label,
            "ttl_minutes": payload.ttl_minutes,
            "reason": "Remote connector setup now uses account-bound OAuth and the paired desktop device link.",
        }
        return UIActionResponse(status="replaced_by_oauth", detail=result)

    return app


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-ui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    args = parser.parse_args()

    if not args.no_open:
        threading.Timer(0.75, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
