"""Insights derived only from calculated values. No generic advice."""
from __future__ import annotations

from app.db.schemas import (
    CardCalculation,
    CurrentCardComparison,
    TwoCardStrategy,
    UserProfile,
)


def generate_insights(
    user_profile: UserProfile,
    top_cards: list[CardCalculation],
    strategy: TwoCardStrategy | None,
    comparison: CurrentCardComparison | None,
    conditional: list[CardCalculation] | None = None,
) -> list[str]:
    insights: list[str] = []
    if not top_cards:
        insights.append(
            "No card in the database could be ranked precisely for this profile. "
            "See the conditional results and their data gaps below."
        )
        return insights

    best = top_cards[0]

    driving = [b for b in best.category_breakdown if b.monthly_reward_after_category_cap > 0]
    if driving:
        top_cat = max(driving, key=lambda b: b.monthly_reward_after_category_cap)
        annual = top_cat.monthly_reward_after_category_cap * 12
        insights.append(
            f"Your largest reward-driving category on {best.card_name} is "
            f"{top_cat.category.value}, worth an estimated AED {annual:,.0f} of the "
            f"AED {best.gross_annual_rewards:,.0f} annual total."
        )

    zero = [
        b.category.value
        for b in best.category_breakdown
        if b.monthly_spend > 0 and b.monthly_reward_after_category_cap == 0
    ]
    if zero:
        insights.append(
            f"On {best.card_name} these categories earn nothing: {', '.join(sorted(set(zero)))}."
        )

    for cap in best.caps_applied:
        insights.append(f"Cap in effect — {cap}")

    for warning in best.warnings:
        insights.append(warning)

    if user_profile.international_monthly_spend > 0:
        annual_intl = user_profile.international_monthly_spend * 12
        if "fx_fee" in best.unknown_fields:
            insights.append(
                f"You spend an estimated AED {annual_intl:,.0f} abroad each year, but the "
                "foreign transaction fee is not verified for this card, so the net value "
                "shown is optimistic by whatever that fee turns out to be."
            )
        else:
            insights.append(
                f"FX costs an estimated AED {best.fx_cost:,.0f} per year on "
                f"AED {annual_intl:,.0f} of international spend."
            )

    if len(top_cards) > 1:
        second = top_cards[1]
        delta = best.net_annual_value - second.net_annual_value
        insights.append(
            f"{best.card_name} produces an estimated AED {delta:,.0f} more annual net value "
            f"than {second.card_name}."
        )

    if strategy is not None:
        insights.append(strategy.reason)

    if comparison is not None:
        insights.append(
            f"Against your current {comparison.current_card_name}, the estimated potential "
            f"additional value is AED {comparison.potential_additional_value:,.0f} per year."
        )

    if user_profile.annual_fee_tolerance < best.annual_fee:
        insights.append(
            f"{best.card_name} carries an AED {best.annual_fee:,.0f} annual fee, above your "
            f"stated tolerance of AED {user_profile.annual_fee_tolerance:,.0f}. It still ranks "
            "first on net value after the fee is deducted."
        )

    if not user_profile.pays_balance_in_full:
        insights.append(
            "You indicated you do not always pay the balance in full. UAE card interest "
            "typically exceeds any cashback rate, so these reward estimates can be wiped out "
            "by finance charges."
        )

    for card in conditional or []:
        if card.exclusion_reason:
            insights.append(f"{card.card_name} was not ranked: {card.exclusion_reason}")

    return insights
