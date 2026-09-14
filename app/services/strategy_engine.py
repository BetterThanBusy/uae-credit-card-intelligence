"""Two-card optimisation.

Allocation is decided from each card's per-category reward computed in the
context of the user's FULL spend, because tier selection and minimum-spend
gates depend on total throughput. Evaluating a category in isolation would put
every category below every gate and make the comparison meaningless.

The allocation is then re-scored by running the real reward engine on each
split profile, so caps, gates and annual fees are applied honestly to the
smaller spend each card actually sees. A split can therefore come out worse
than a single card — and when it does, we say so instead of hiding it.
"""
from __future__ import annotations

from itertools import combinations

from app.core.config import get_settings
from app.db.models import Card
from app.db.schemas import CardCalculation, TwoCardStrategy, UserProfile
from app.domain.enums import SpendCategory
from app.services.ranking_engine import evaluate_card


def _split_profile(
    user_profile: UserProfile, categories_for_card: set[SpendCategory], intl_here: bool
) -> UserProfile:
    data = user_profile.model_dump()
    data["monthly_category_spend"] = {
        c: amt for c, amt in user_profile.monthly_category_spend.items() if c in categories_for_card
    }
    data["international_monthly_spend"] = (
        user_profile.international_monthly_spend if intl_here else 0.0
    )
    data["current_card_id"] = None
    return UserProfile(**data)


def _category_values(user_profile: UserProfile, card: Card) -> dict[str, float]:
    """Annual reward per category with the user's full spend on this card."""
    evaluation = evaluate_card(user_profile, card)
    return {
        row.category.value: row.monthly_reward_after_category_cap * 12
        for row in evaluation.category_breakdown
    }


def optimize_two_card_strategy(
    user_profile: UserProfile,
    cards: list[Card],
    best_single: CardCalculation | None,
) -> TwoCardStrategy | None:
    settings = get_settings()
    usable = [c for c in cards if evaluate_card(user_profile, c).rankable]
    if len(usable) < 2 or best_single is None:
        return None

    best: TwoCardStrategy | None = None

    for card_a, card_b in combinations(usable, 2):
        values_a = _category_values(user_profile, card_a)
        values_b = _category_values(user_profile, card_b)

        allocation: dict[SpendCategory, Card] = {}
        for category, amount in user_profile.monthly_category_spend.items():
            if amount <= 0:
                continue
            allocation[category] = (
                card_a
                if values_a.get(category.value, 0.0) >= values_b.get(category.value, 0.0)
                else card_b
            )

        greedy_a = {c for c, card in allocation.items() if card is card_a}
        all_cats = set(allocation)

        # Always include the degenerate allocations. Holding a second card is
        # optional, so the best two-card strategy can never be worse than the
        # better of the two cards used alone.
        candidate_splits = [greedy_a, all_cats, set()]

        for cats_a in candidate_splits:
          cats_b = all_cats - cats_a
          for intl_on_a in (True, False):
            eval_a = evaluate_card(_split_profile(user_profile, cats_a, intl_on_a), card_a)
            eval_b = evaluate_card(_split_profile(user_profile, cats_b, not intl_on_a), card_b)

            # A card that ends up holding nothing costs nothing and earns nothing.
            uses_a = bool(cats_a) or intl_on_a
            uses_b = bool(cats_b) or not intl_on_a
            net_a = eval_a.net_annual_value if uses_a else 0.0
            net_b = eval_b.net_annual_value if uses_b else 0.0

            combined = round(net_a + net_b, 2)
            incremental = round(combined - best_single.net_annual_value, 2)

            if best is None or combined > best.combined_net_annual_value:
                intl_card = card_a if intl_on_a else card_b
                effective = {
                    c.value: (card_a if c in cats_a else card_b).card_name for c in all_cats
                }
                best = TwoCardStrategy(
                    primary_card_id=card_a.card_id,
                    secondary_card_id=card_b.card_id,
                    primary_card_name=card_a.card_name,
                    secondary_card_name=card_b.card_name,
                    category_allocation=(
                        dict(sorted(effective.items()))
                        | ({"international": intl_card.card_name} if user_profile.international_monthly_spend > 0 else {})
                    ),
                    combined_net_annual_value=combined,
                    best_single_net_annual_value=best_single.net_annual_value,
                    incremental_value=incremental,
                    recommended=incremental >= settings.minimum_incremental_value,
                    reason=_reason(incremental, settings.minimum_incremental_value),
                )
    return best


def _reason(incremental: float, threshold: float) -> str:
    if incremental >= threshold:
        return f"Splitting spend across two cards adds an estimated AED {incremental:,.0f} per year."
    if incremental < 0:
        return (
            f"A two-card split is estimated to be AED {abs(incremental):,.0f} per year WORSE than "
            "the best single card: dividing spend drops each card below the minimum monthly "
            "spend it needs, and a second annual fee applies. One card is the better answer here."
        )
    return (
        f"A second card adds only an estimated AED {incremental:,.0f} per year, below the "
        f"AED {threshold:,.0f} threshold that justifies managing two cards."
    )
