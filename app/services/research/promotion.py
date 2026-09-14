"""Promotion: research database -> production card database.

The gate is strict and one-way:

    RESEARCHED -> EXTRACTED -> VALIDATED -> VERIFIED -> PROMOTED

Nothing skips a stage, and nothing is promoted while a critical field is
unverified. A card that fails is not deleted: it stays in the research
database with its blocking fields recorded, so the next run knows exactly
what to go and find.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Card, CardSource, CardVersion
from app.db.research_models import ResearchCard
from app.domain.research_enums import (
    STAGE_ORDER,
    ResearchStage,
    ResearchVerification,
)

logger = logging.getLogger(__name__)


class PromotionError(RuntimeError):
    pass


def can_advance(current: str, target: ResearchStage) -> bool:
    """Stages move forward one step at a time. REJECTED is terminal."""
    if current == ResearchStage.REJECTED.value:
        return False
    try:
        current_index = STAGE_ORDER.index(ResearchStage(current))
    except ValueError:
        return False
    return STAGE_ORDER.index(target) == current_index + 1


def advance(card: ResearchCard, target: ResearchStage, note: str | None = None) -> ResearchCard:
    if not can_advance(card.stage, target):
        raise PromotionError(
            f"Cannot move {card.card_slug} from {card.stage} to {target.value}; "
            "stages advance one step at a time."
        )
    card.stage = target.value
    card.stage_history = (card.stage_history or []) + [
        {"stage": target.value, "at": datetime.now(timezone.utc).isoformat(), "note": note}
    ]
    return card


def validate_research_card(session: Session, card: ResearchCard) -> ResearchCard:
    """EXTRACTED -> VALIDATED. Checks structure, not authority."""
    problems: list[str] = []
    if not card.evidence:
        problems.append("no evidence records")
    for row in card.evidence:
        if row.value is not None and not row.evidence_text:
            problems.append(f"{row.field_name}: value stored without evidence text")
        if row.value is not None and not row.source_url:
            problems.append(f"{row.field_name}: value stored without a source URL")
    if problems:
        card.stage = ResearchStage.REJECTED.value
        card.notes = "; ".join(problems)
        card.stage_history = (card.stage_history or []) + [
            {"stage": ResearchStage.REJECTED.value, "at": datetime.now(timezone.utc).isoformat()}
        ]
        session.commit()
        return card
    advance(card, ResearchStage.VALIDATED)
    session.commit()
    return card


def verify_research_card(session: Session, card: ResearchCard) -> ResearchCard:
    """VALIDATED -> VERIFIED. Only when no critical field is blocking."""
    if card.blocking_fields:
        card.notes = (
            "Held at VALIDATED: critical fields not established - "
            + ", ".join(card.blocking_fields)
        )
        session.commit()
        return card
    advance(card, ResearchStage.VERIFIED)
    card.verification_status = ResearchVerification.VERIFIED_OFFICIAL.value
    session.commit()
    return card


def promote_research_card(session: Session, card: ResearchCard) -> Card:
    """VERIFIED -> PROMOTED. Writes into the production card tables.

    Historical card versions are never overwritten: a promotion of an existing
    card increments the version and appends a new snapshot.
    """
    if card.stage != ResearchStage.VERIFIED.value:
        raise PromotionError(
            f"{card.card_slug} is at {card.stage}; only VERIFIED cards can be promoted."
        )
    if card.blocking_fields:
        raise PromotionError(
            f"{card.card_slug} has unverified critical fields: {card.blocking_fields}"
        )

    existing = session.scalar(select(Card).where(Card.card_id == card.card_slug))
    verified = [
        row
        for row in card.evidence
        if row.verification_status == ResearchVerification.VERIFIED_OFFICIAL.value
        and row.value is not None
    ]
    if not verified:
        raise PromotionError(f"{card.card_slug} has no officially verified evidence to promote.")

    if existing is None:
        production = Card(
            card_id=card.card_slug,
            card_name=card.card_name,
            issuer=card.issuer,
            card_type=card.card_type or "cashback",
            country="UAE",
            status=ResearchVerification.VERIFIED_OFFICIAL.value,
            version=1,
            retrieved_at=datetime.now(timezone.utc).replace(tzinfo=None),
            last_verified=datetime.now(timezone.utc).replace(tzinfo=None),
            notes=f"Promoted from research {card.research_id}",
        )
        session.add(production)
    else:
        production = existing
        production.version += 1
        production.last_verified = datetime.now(timezone.utc).replace(tzinfo=None)

    for row in verified:
        production.sources.append(
            CardSource(
                field_name=row.field_name,
                value=row.value,
                unit=row.unit,
                source_name=f"{card.issuer} official document",
                source_url=row.source_url,
                source_type=row.source_type,
                source_tier=row.source_tier,
                retrieved_at=row.retrieved_at,
                effective_from=row.effective_date,
                verification_status=row.verification_status,
                evidence=row.evidence_text,
            )
        )

    production.versions.append(
        CardVersion(
            version=production.version,
            snapshot={
                "research_id": card.research_id,
                "evidence": [
                    {
                        "field_name": row.field_name,
                        "value": row.value,
                        "source_url": row.source_url,
                        "verification_status": row.verification_status,
                    }
                    for row in verified
                ],
            },
            content_hash=card.research_id,
        )
    )

    advance(card, ResearchStage.PROMOTED)
    card.promoted_card_id = production.card_id
    card.promoted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    logger.info("promoted %s to production v%s", card.card_slug, production.version)
    return production


def run_promotion_pipeline(session: Session, card: ResearchCard) -> ResearchCard:
    """Convenience: EXTRACTED all the way to PROMOTED where the data allows."""
    if card.stage == ResearchStage.EXTRACTED.value:
        validate_research_card(session, card)
    if card.stage == ResearchStage.VALIDATED.value:
        verify_research_card(session, card)
    if card.stage == ResearchStage.VERIFIED.value:
        promote_research_card(session, card)
    return card
