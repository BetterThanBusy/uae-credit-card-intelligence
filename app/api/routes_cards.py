from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.db.models import Card
from app.db.schemas import CardSummary
from app.domain.enums import FeeType

router = APIRouter(tags=["cards"])


def _summary(card: Card) -> CardSummary:
    fee = next((f for f in card.fees if f.fee_type == FeeType.ANNUAL_FEE.value), None)
    return CardSummary(
        card_id=card.card_id,
        card_name=card.card_name,
        issuer=card.issuer,
        card_type=card.card_type,
        status=card.status,
        version=card.version,
        annual_fee=fee.amount if fee else None,
        annual_fee_status=fee.verification_status if fee else "UNKNOWN",
        modelling_limitation=card.modelling_limitation,
    )


@router.get("/cards", response_model=list[CardSummary])
def list_cards(session: Session = Depends(get_session)) -> list[CardSummary]:
    return [_summary(c) for c in session.scalars(select(Card))]


@router.get("/cards/{card_id}")
def get_card(card_id: str, session: Session = Depends(get_session)) -> dict:
    card = session.scalar(select(Card).where(Card.card_id == card_id))
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return {
        "card": _summary(card).model_dump(),
        "notes": card.notes,
        "exclusions": (card.extra or {}).get("exclusions", []),
        "reward_rules": [
            {
                "category": r.category, "scope": r.scope, "reward_type": r.reward_type,
                "rate": r.rate, "tier_min_monthly_spend": r.tier_min_monthly_spend,
                "tier_max_monthly_spend": r.tier_max_monthly_spend,
                "monthly_cap_amount": r.monthly_cap_amount, "cap_scope": r.cap_scope,
                "min_monthly_spend_required": r.min_monthly_spend_required,
                "excluded": r.excluded, "verification_status": r.verification_status,
                "notes": r.notes,
            }
            for r in card.reward_rules
        ],
        "fees": [
            {"fee_type": f.fee_type, "amount": f.amount, "unit": f.unit,
             "first_year_free": f.first_year_free,
             "waiver_min_annual_spend": f.waiver_min_annual_spend,
             "verification_status": f.verification_status, "notes": f.notes}
            for f in card.fees
        ],
        "eligibility": (
            {
                "min_monthly_salary": card.eligibility.min_monthly_salary,
                "min_age": card.eligibility.min_age,
                "residency_required": card.eligibility.residency_required,
                "verification_status": card.eligibility.verification_status,
            }
            if card.eligibility
            else None
        ),
        "benefits": [{"type": b.benefit_type, "description": b.description} for b in card.benefits],
        "sources": [
            {
                "field_name": s.field_name, "value": s.value, "unit": s.unit,
                "source_name": s.source_name, "source_url": s.source_url,
                "source_type": s.source_type, "source_tier": s.source_tier,
                "verification_status": s.verification_status, "evidence": s.evidence,
                "retrieved_at": s.retrieved_at,
            }
            for s in card.sources
        ],
    }
