"""Net value and ranking.

A card is only given a precise ranking position when every field that is
critical *for this user's profile* has been established. Criticality is
conditional: an unverified FX fee does not matter to someone with no foreign
spend, and does matter a great deal to a frequent traveller.
"""
from __future__ import annotations

from app.db.models import Card
from app.db.schemas import CardCalculation, UserProfile
from app.domain.enums import DataQuality, EligibilityResult, VerificationStatus
from app.services.eligibility_engine import check_eligibility
from app.services.fee_engine import calculate_annual_fee
from app.services.fx_engine import calculate_fx_cost
from app.services.reward_engine import calculate_card_rewards

_SOFT_STATUSES = {
    VerificationStatus.PARTIALLY_VERIFIED.value,
    VerificationStatus.CONFLICTING.value,
}


def evaluate_card(user_profile: UserProfile, card: Card) -> CardCalculation:
    eligibility, elig_reasons = check_eligibility(user_profile, card)
    rewards = calculate_card_rewards(user_profile, card)
    fee, fee_status, fee_notes = calculate_annual_fee(user_profile, card)
    fx_cost, fx_status, fx_notes = calculate_fx_cost(user_profile, card)

    quality_reasons: list[str] = []
    unknown_fields = list(dict.fromkeys(rewards.unknown_fields))
    rankable = True
    exclusion_reason: str | None = None

    # --- annual fee is always critical ---------------------------------
    if fee is None:
        unknown_fields.append("annual_fee")
        rankable = False
        exclusion_reason = "The annual fee could not be verified."
        quality_reasons.append("Annual fee is UNKNOWN.")
        fee_for_math = 0.0
    else:
        fee_for_math = fee
        if fee_status in _SOFT_STATUSES:
            quality_reasons.append(f"Annual fee is {fee_status}.")

    # --- FX fee is critical only when the user spends abroad -----------
    if user_profile.international_monthly_spend > 0 and fx_cost is None:
        unknown_fields.append("fx_fee")
        quality_reasons.append(
            "Foreign transaction fee is UNKNOWN and this profile has international spend."
        )
        fx_for_math = 0.0
    else:
        fx_for_math = fx_cost or 0.0

    # --- reward caps and rates -----------------------------------------
    for rule in card.reward_rules:
        if rule.verification_status == VerificationStatus.UNKNOWN.value and _category_matters(
            user_profile, rule.category
        ):
            unknown_fields.append(f"reward_rule:{rule.category}")
            rankable = False
            exclusion_reason = (
                f"A critical reward field for {rule.category} could not be verified."
            )

    soft_rules = [
        r
        for r in card.reward_rules
        if r.verification_status in _SOFT_STATUSES and _category_matters(user_profile, r.category)
    ]
    if soft_rules:
        quality_reasons.append(
            f"{len(soft_rules)} reward rule(s) relevant to this profile are not fully verified "
            "from an official issuer source."
        )

    if card.modelling_limitation:
        rankable = False
        exclusion_reason = card.modelling_limitation
        quality_reasons.append("Card terms cannot be modelled precisely from a category profile.")

    if eligibility is EligibilityResult.NOT_ELIGIBLE:
        rankable = False
        exclusion_reason = "Not eligible on the criteria supplied."

    net = round(rewards.gross_annual_rewards - fee_for_math - fx_for_math, 2)

    quality = _grade(unknown_fields, quality_reasons)

    return CardCalculation(
        card_id=card.card_id,
        card_name=card.card_name,
        issuer=card.issuer,
        eligibility=eligibility,
        eligibility_reasons=elig_reasons,
        gross_annual_rewards=rewards.gross_annual_rewards,
        annual_fee=fee_for_math,
        fx_cost=fx_for_math,
        net_annual_value=net,
        data_quality=quality,
        data_quality_reasons=quality_reasons or ["All critical fields verified for this profile."],
        rankable=rankable,
        exclusion_reason=exclusion_reason,
        category_breakdown=rewards.category_breakdown,
        caps_applied=rewards.caps_applied,
        assumptions=rewards.assumptions + fee_notes + fx_notes,
        warnings=rewards.warnings,
        benefits=[b.description for b in card.benefits],
        unknown_fields=sorted(set(unknown_fields)),
        card_version=card.version,
    )


def _category_matters(user_profile: UserProfile, category: str) -> bool:
    """An unverified rule only degrades quality if the user actually spends there."""
    return any(c.value == category and amt > 0 for c, amt in user_profile.monthly_category_spend.items())


def _grade(unknown_fields: list[str], quality_reasons: list[str]) -> DataQuality:
    if unknown_fields:
        return DataQuality.LOW
    if quality_reasons:
        return DataQuality.MEDIUM
    return DataQuality.HIGH


def rank_cards(
    user_profile: UserProfile, cards: list[Card]
) -> tuple[list[CardCalculation], list[CardCalculation]]:
    """Return (rankable_sorted_desc, conditional_cards)."""
    evaluated = [evaluate_card(user_profile, c) for c in cards]
    rankable = [
        c
        for c in evaluated
        if c.rankable and c.eligibility is not EligibilityResult.NOT_ELIGIBLE
    ]
    conditional = [c for c in evaluated if c not in rankable]
    rankable.sort(key=lambda c: c.net_annual_value, reverse=True)
    conditional.sort(key=lambda c: c.net_annual_value, reverse=True)
    return rankable, conditional
