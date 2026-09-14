"""Research API. Existing recommendation endpoints are untouched."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.db.research_models import ResearchCard, ResearchRun
from app.services.research.orchestrator import (
    find_target,
    load_targets,
    research_batch,
    research_card,
)
from app.services.research.promotion import (
    PromotionError,
    promote_research_card,
    validate_research_card,
    verify_research_card,
)

router = APIRouter(tags=["research"])


class ResearchCardRequest(BaseModel):
    slug: str = Field(..., description="Target slug from research_targets.json")


class ResearchBatchRequest(BaseModel):
    slugs: list[str] | None = Field(
        default=None, description="Omit to research every configured target."
    )


def _card_payload(card: ResearchCard) -> dict:
    return {
        "research_id": card.research_id,
        "card_slug": card.card_slug,
        "card_name": card.card_name,
        "issuer": card.issuer,
        "stage": card.stage,
        "verification_status": card.verification_status,
        "modelable": card.modelable,
        "blocking_fields": card.blocking_fields or [],
        "promoted_card_id": card.promoted_card_id,
        "evidence_count": len(card.evidence),
        "notes": card.notes,
    }


@router.get("/research/targets")
def list_targets() -> dict:
    return load_targets()


@router.post("/research/card")
def start_card_research(
    request: ResearchCardRequest, session: Session = Depends(get_session)
) -> dict:
    target = find_target(request.slug)
    if target is None:
        raise HTTPException(status_code=404, detail=f"No research target '{request.slug}'")
    return _card_payload(research_card(session, target))


@router.post("/research/batch")
def start_batch_research(
    request: ResearchBatchRequest, session: Session = Depends(get_session)
) -> dict:
    cards = research_batch(session, request.slugs)
    return {
        "researched": len(cards),
        "modelable": sum(1 for c in cards if c.modelable),
        "cards": [_card_payload(c) for c in cards],
    }


@router.get("/research/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.scalar(select(ResearchRun).where(ResearchRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "issuer": run.issuer,
        "target_slug": run.target_slug,
        "retrieval_provider": run.retrieval_provider,
        "queries_issued": run.queries_issued,
        "documents_retrieved": run.documents_retrieved,
        "documents_from_cache": run.documents_from_cache,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "documents": [
            {
                "url": d.url,
                "document_type": d.document_type,
                "is_official": d.is_official,
                "content_hash": d.content_hash,
                "changed_since_last_run": d.changed_since_last_run,
                "needs_reverification": d.needs_reverification,
                "retrieved_at": d.retrieved_at,
            }
            for d in run.documents
        ],
        "cards": [_card_payload(c) for c in run.cards],
    }


@router.get("/research/cards/{card_id}")
def get_research_card(card_id: str, session: Session = Depends(get_session)) -> dict:
    card = _lookup(session, card_id)
    return _card_payload(card)


@router.get("/research/cards/{card_id}/evidence")
def get_research_evidence(card_id: str, session: Session = Depends(get_session)) -> dict:
    card = _lookup(session, card_id)
    return {
        "research_id": card.research_id,
        "card_slug": card.card_slug,
        "evidence": [
            {
                "field_name": row.field_name,
                "category": row.category,
                "scope": row.scope,
                "value": row.value,
                "unit": row.unit,
                "source_url": row.source_url,
                "source_type": row.source_type,
                "source_tier": row.source_tier,
                "is_official": row.is_official,
                "evidence_text": row.evidence_text,
                "retrieved_at": row.retrieved_at,
                "effective_date": row.effective_date,
                "verification_status": row.verification_status,
                "rejection_reason": row.rejection_reason,
                "is_critical": row.is_critical,
                "notes": row.notes,
            }
            for row in card.evidence
        ],
    }


@router.post("/research/verify/{research_id}")
def verify(research_id: str, session: Session = Depends(get_session)) -> dict:
    card = _lookup(session, research_id)
    from app.domain.research_enums import ResearchStage

    if card.stage == ResearchStage.EXTRACTED.value:
        validate_research_card(session, card)
    if card.stage == ResearchStage.VALIDATED.value:
        verify_research_card(session, card)
    return _card_payload(card)


@router.post("/research/promote/{research_id}")
def promote(research_id: str, session: Session = Depends(get_session)) -> dict:
    card = _lookup(session, research_id)
    try:
        production = promote_research_card(session, card)
    except PromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "promoted": True,
        "production_card_id": production.card_id,
        "production_version": production.version,
        "research": _card_payload(card),
    }


def _lookup(session: Session, identifier: str) -> ResearchCard:
    card = session.scalar(
        select(ResearchCard).where(ResearchCard.research_id == identifier)
    ) or session.scalar(
        select(ResearchCard)
        .where(ResearchCard.card_slug == identifier)
        .order_by(ResearchCard.id.desc())
    )
    if card is None:
        raise HTTPException(status_code=404, detail="Research card not found")
    return card
