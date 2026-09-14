"""Orchestrates the pipeline and persists the run so results are reproducible."""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import CALCULATION_ENGINE_VERSION, get_settings
from app.core.logging import log_event
from app.db.models import Card, RecommendationResult, RecommendationRun
from app.db.schemas import RecommendationResponse, UserProfile
from app.domain.enums import DataQuality, EligibilityResult
from app.services.comparison_engine import compare_current_card
from app.services.explanation_engine import explain
from app.services.insight_engine import generate_insights
from app.services.ranking_engine import rank_cards
from app.services.strategy_engine import optimize_two_card_strategy

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "This is an estimate based on the card terms and spending assumptions available to the "
    "system. Card terms and eligibility can change. Verify current terms with the issuing "
    "bank before applying."
)


def card_data_version(cards: list[Card]) -> str:
    """Fingerprint of the exact card data used, so a run can be replayed."""
    parts = sorted(f"{c.card_id}:{c.version}" for c in cards)
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"{len(cards)}cards-{digest}"


def _overall_quality(top, conditional) -> DataQuality:
    if not top:
        return DataQuality.LOW
    return top[0].data_quality


def recommend(
    session: Session,
    user_profile: UserProfile,
    request_id: str | None = None,
    with_llm: bool = True,
) -> RecommendationResponse:
    cards = list(session.scalars(select(Card).where(Card.country == user_profile.country.value)))
    if not cards:
        raise ValueError("No cards in the database. Run scripts/seed_database.py first.")

    top_all, conditional = rank_cards(user_profile, cards)
    top_cards = top_all[:5]
    best_single = top_cards[0] if top_cards else None

    strategy = optimize_two_card_strategy(user_profile, cards, best_single)
    comparison = compare_current_card(user_profile, cards, best_single)
    insights = generate_insights(user_profile, top_cards, strategy, comparison, conditional)

    run_id = uuid.uuid4().hex[:16]
    data_version = card_data_version(cards)
    quality = _overall_quality(top_cards, conditional)

    assumptions = sorted({a for c in top_cards for a in c.assumptions})

    response = RecommendationResponse(
        run_id=run_id,
        request_id=request_id,
        calculation_version=CALCULATION_ENGINE_VERSION,
        card_data_version=data_version,
        generated_at=datetime.now(timezone.utc).isoformat(),
        user_profile_summary={
            "emirate": user_profile.emirate.value,
            "total_monthly_spend": user_profile.total_monthly_spend,
            "international_monthly_spend": user_profile.international_monthly_spend,
            "salary_monthly": user_profile.salary_monthly,
            "reward_preference": user_profile.reward_preference.value,
            "annual_fee_tolerance": user_profile.annual_fee_tolerance,
            "pays_balance_in_full": user_profile.pays_balance_in_full,
        },
        eligible_cards=[
            c.card_id
            for c in top_all + conditional
            if c.eligibility is not EligibilityResult.NOT_ELIGIBLE
        ],
        top_cards=top_cards,
        conditional_cards=conditional,
        best_strategy=strategy,
        current_card_comparison=comparison,
        missed_value=comparison.potential_additional_value if comparison else None,
        insights=insights,
        assumptions=assumptions,
        data_quality=quality,
        disclaimer=DISCLAIMER,
    )

    if with_llm and get_settings().llm_enabled:
        response.explanation = explain(response)
    else:
        from app.services.explanation_engine import deterministic_explanation

        response.explanation = deterministic_explanation(response)

    _persist(session, response, user_profile)
    log_event(
        logger,
        "recommendation_complete",
        run_id=run_id,
        card_data_version=data_version,
        calculation_version=CALCULATION_ENGINE_VERSION,
        ranked=len(top_cards),
        conditional=len(conditional),
        data_quality=quality.value,
    )
    return response


def _persist(session: Session, response: RecommendationResponse, profile: UserProfile) -> None:
    run = RecommendationRun(
        run_id=response.run_id,
        request_id=response.request_id,
        calculation_version=response.calculation_version,
        card_data_version=response.card_data_version,
        user_profile=profile.model_dump(mode="json"),
        data_quality=response.data_quality.value,
    )
    for index, card in enumerate(response.top_cards, start=1):
        run.results.append(
            RecommendationResult(
                card_id=card.card_id,
                rank=index,
                eligibility=card.eligibility.value,
                gross_rewards=card.gross_annual_rewards,
                annual_fee=card.annual_fee,
                fx_cost=card.fx_cost,
                net_value=card.net_annual_value,
                data_quality=card.data_quality.value,
                breakdown=card.model_dump(mode="json"),
            )
        )
    for card in response.conditional_cards:
        run.results.append(
            RecommendationResult(
                card_id=card.card_id,
                rank=None,
                eligibility=card.eligibility.value,
                gross_rewards=card.gross_annual_rewards,
                annual_fee=card.annual_fee,
                fx_cost=card.fx_cost,
                net_value=card.net_annual_value,
                data_quality=card.data_quality.value,
                breakdown=card.model_dump(mode="json"),
            )
        )
    session.add(run)
    session.commit()
