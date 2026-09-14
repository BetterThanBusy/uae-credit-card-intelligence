"""Current-card comparison. Wording is always 'estimated potential'."""
from __future__ import annotations

from app.db.models import Card
from app.db.schemas import CardCalculation, CurrentCardComparison, UserProfile
from app.services.ranking_engine import evaluate_card


def compare_current_card(
    user_profile: UserProfile,
    cards: list[Card],
    recommended: CardCalculation | None,
) -> CurrentCardComparison | None:
    if not user_profile.current_card_id or recommended is None:
        return None
    current = next((c for c in cards if c.card_id == user_profile.current_card_id), None)
    if current is None:
        return None

    current_eval = evaluate_card(user_profile, current)
    return CurrentCardComparison(
        current_card_id=current.card_id,
        current_card_name=current.card_name,
        current_estimated_value=current_eval.net_annual_value,
        recommended_card_id=recommended.card_id,
        recommended_estimated_value=recommended.net_annual_value,
        potential_additional_value=round(
            recommended.net_annual_value - current_eval.net_annual_value, 2
        ),
    )
