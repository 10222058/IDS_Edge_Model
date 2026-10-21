"""FastAPI application factory — bind 127.0.0.1:9137 in __main__."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from registry.registry import Registry

from .actions import router as actions_router
from .cert_cache import CertBundleCache
from .deps import AppState, OpenSSH, default_open_ssh
from .devices import public_router, router as devices_router
from .ids import router as ids_router
from .lab import router as lab_router
from .query import router as query_router
from .schemas import HealthResponse
from .settings import Settings


def create_app(
    *,
    settings: Optional[Settings] = None,
    registry: Optional[Registry] = None,
    open_ssh: Optional[OpenSSH] = None,
    cert_cache: Optional[CertBundleCache] = None,
    lab_manager: Optional[object] = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    reg = registry or Registry(str(cfg.registry_path))
    state = AppState(
        settings=cfg,
        registry=reg,
        cert_cache=cert_cache or CertBundleCache(),
        open_ssh=open_ssh or default_open_ssh(cfg),
        lab_manager=lab_manager,
    )

    app = FastAPI(title="iot-gateway-control", version="0.1.0")
    app.state.control = state
    # Desktop Actions chrome (:9138) + Tauri WebView call this API cross-origin.
    # Monitoring uses the wizard same-origin /api/v1 proxy; Actions still hits :9137 directly.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?|tauri://localhost",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(devices_router, prefix="/api/v1")
    app.include_router(actions_router, prefix="/api/v1")
    app.include_router(public_router, prefix="/api/v1")
    app.include_router(query_router, prefix="/api/v1")
    app.include_router(ids_router, prefix="/api/v1")
    app.include_router(lab_router, prefix="/api/v1")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app
