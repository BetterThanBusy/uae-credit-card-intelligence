from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.db.schemas import RecommendationResponse, UserProfile
from app.services.recommendation_service import recommend

router = APIRouter(tags=["recommendations"])


@router.post("/recommend", response_model=RecommendationResponse)
def create_recommendation(
    user_profile: UserProfile,
    request: Request,
    session: Session = Depends(get_session),
) -> RecommendationResponse:
    try:
        return recommend(
            session, user_profile, request_id=getattr(request.state, "request_id", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
