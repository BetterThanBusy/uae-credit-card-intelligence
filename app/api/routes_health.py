from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import CALCULATION_ENGINE_VERSION, get_settings
from app.db.database import get_session
from app.db.models import Card

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    cards = session.scalar(select(func.count()).select_from(Card)) or 0
    return {
        "status": "ok" if cards else "degraded",
        "cards_loaded": cards,
        "calculation_version": CALCULATION_ENGINE_VERSION,
        "environment": get_settings().environment,
        "llm_explanation_enabled": get_settings().llm_enabled,
    }
