"""FastAPI application factory: middleware, routers, error handling."""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .routers import (
    audit,
    appointments,
    auth,
    billing,
    branches,
    encounters,
    health,
    lab,
    members,
    opd_queue,
    patients,
    pharmacy,
    prescriptions,
)

logger = logging.getLogger("hms.api")

BASE_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Aditya BioNova Rural Smart HMS — multi-tenant clinic & hospital "
            "management API. Synthetic/development data only; no real patient data."
        ),
        openapi_url=f"{BASE_PREFIX}/openapi.json",
        docs_url=f"{BASE_PREFIX}/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.correlation_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "correlation_id", "")
        logger.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "correlation_id": rid},
        )

    app.include_router(health.router, prefix=BASE_PREFIX)
    app.include_router(auth.router, prefix=BASE_PREFIX)
    app.include_router(branches.router, prefix=BASE_PREFIX)
    app.include_router(members.router, prefix=BASE_PREFIX)
    app.include_router(audit.router, prefix=BASE_PREFIX)
    app.include_router(patients.router, prefix=BASE_PREFIX)
    app.include_router(appointments.router, prefix=BASE_PREFIX)
    app.include_router(opd_queue.router, prefix=BASE_PREFIX)
    app.include_router(encounters.router, prefix=BASE_PREFIX)
    app.include_router(prescriptions.router, prefix=BASE_PREFIX)
    app.include_router(billing.router, prefix=BASE_PREFIX)
    app.include_router(pharmacy.router, prefix=BASE_PREFIX)
    app.include_router(lab.router, prefix=BASE_PREFIX)
    return app


app = create_app()
