"""FastAPI application. Backend is independent of Streamlit."""
from __future__ import annotations

import logging
import os

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import routes_cards, routes_health, routes_recommendations, routes_research
from app.core.config import CALCULATION_ENGINE_VERSION
from app.core.logging import configure_logging, log_event, new_request_id, request_id_ctx
from app.db import research_models  # noqa: F401  (registers research tables)
from app.db.database import create_all

configure_logging()
logger = logging.getLogger("app")

app = FastAPI(
    title="UAE Credit Card Intelligence Agent",
    version=CALCULATION_ENGINE_VERSION,
    description=(
        "Estimates which UAE credit card or pair of cards gives the highest net annual "
        "value for a specific spending profile. All financial calculation is deterministic."
    ),
)


# The web client may be deployed separately from the API (e.g. static host +
# container). Origins are configured, never wildcarded in production.
_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or new_request_id()
    request_id_ctx.set(request_id)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    log_event(logger, "request", path=request.url.path, status=response.status_code)
    return response


@app.on_event("startup")
def on_startup() -> None:
    create_all()


app.include_router(routes_health.router, prefix="/api/v1")
app.include_router(routes_cards.router, prefix="/api/v1")
app.include_router(routes_recommendations.router, prefix="/api/v1")
app.include_router(routes_research.router, prefix="/api/v1")

# Serve the web client from the same process in single-container deployments.
# Mounted last so it can never shadow /api/v1.
_web_dir = Path(__file__).resolve().parents[1] / "frontend" / "web"
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
