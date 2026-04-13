"""Small public web presence server for Astrata registries and metadata."""

from __future__ import annotations

import argparse

from fastapi import FastAPI
import uvicorn

from astrata.webpresence.service import WebPresenceService


def create_app(*, service: WebPresenceService | None = None) -> FastAPI:
    app = FastAPI(title="Astrata Web Presence", version="0.1.0")
    service = service or WebPresenceService()

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "service": "astrata-web-presence"}

    @app.get("/api/capabilities")
    def capabilities() -> dict:
        return service.capabilities()

    @app.get("/api/auth-control-plane")
    def auth_control_plane() -> dict:
        return service.auth_control_plane()

    @app.get("/api/auth-schema")
    def auth_schema() -> dict:
        return service.auth_schema()

    @app.get("/api/provider-registry")
    def provider_registry() -> dict:
        return service.provider_registry()

    @app.get("/api/model-registry")
    def model_registry() -> dict:
        return service.model_registry()

    @app.get("/api/voice-registry")
    def voice_registry() -> dict:
        return service.voice_registry()

    @app.get("/api/downloads")
    def downloads() -> dict:
        return service.download_manifest()

    @app.get("/api/distribution")
    def distribution() -> dict:
        return service.distribution_manifest()

    @app.get("/api/updates/{channel}")
    def updates(channel: str) -> dict:
        return service.update_manifest(channel)

    return app


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8893)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
