"""Deterministic reward calculation.

No LLM touches anything in this module. Given the same profile and the same
card-data version, the output is byte-identical.

Cap ordering matters and is fixed: per-category monthly cap first, then the
card-level monthly total cap, then annualise. Caps are monthly, so the engine
models a representative month and multiplies by 12 — an explicit assumption of
evenly distributed spend, reported to the user.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import Card, CardRewardRule
from app.db.schemas import CategoryBreakdown, UserProfile
from app.domain.enums import CapScope, RewardType, SpendCategory, SpendScope, VerificationStatus

MONTHS_PER_YEAR = 12


@dataclass
class RewardOutcome:
    gross_annual_rewards: float = 0.0
    reward_unit: str = "AED"
    category_breakdown: list[CategoryBreakdown] = field(default_factory=list)
    caps_applied: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    min_spend_met: bool = True
    # Per-category annual reward, used by the two-card optimizer.
    annual_by_category: dict[str, float] = field(default_factory=dict)


def _select_rule(
    rules: list[CardRewardRule],
    category: SpendCategory,
    scope: SpendScope,
    total_monthly_spend: float,
) -> CardRewardRule | None:
    """Pick exactly one rule for a (category, scope) pair.

    Scope match: a DOMESTIC lookup accepts DOMESTIC or ANY rules; an
    INTERNATIONAL lookup accepts INTERNATIONAL or ANY. Tier match is on TOTAL
    monthly card spend. Where several rules still qualify, the highest rate
    wins, which is how an issuer's own calculator behaves.
    """
    acceptable = {scope.value, SpendScope.ANY.value}
    candidates: list[CardRewardRule] = []
    for rule in rules:
        if rule.category != category.value:
            continue
        if rule.scope not in acceptable:
            continue
        lo = rule.tier_min_monthly_spend
        hi = rule.tier_max_monthly_spend
        if lo is not None and total_monthly_spend < lo:
            continue
        if hi is not None and total_monthly_spend > hi:
            continue
        candidates.append(rule)

    if not candidates:
        return None
    # Prefer an exactly-scoped rule over a catch-all ANY rule, then highest rate.
    return sorted(
        candidates,
        key=lambda r: (r.scope == scope.value, r.rate or 0.0),
        reverse=True,
    )[0]


def calculate_card_rewards(user_profile: UserProfile, card: Card) -> RewardOutcome:
    """Annual gross reward for one card, with the full working shown."""
    outcome = RewardOutcome()
    rules = list(card.reward_rules)

    total_monthly = user_profile.total_monthly_spend
    card_min_spend = _card_min_monthly_spend(rules)

    if card_min_spend and total_monthly < card_min_spend:
        outcome.min_spend_met = False
        outcome.warnings.append(
            f"Total monthly spend of AED {total_monthly:,.0f} is below the "
            f"AED {card_min_spend:,.0f} minimum this card requires before any reward is earned."
        )

    outcome.assumptions.append(
        "Monthly spend is assumed to be evenly distributed across the year; "
        "monthly caps are applied to a representative month and annualised."
    )

    monthly_total = 0.0

    # --- domestic categories -------------------------------------------
    for category in SpendCategory:
        spend = user_profile.spend(category)
        if spend <= 0:
            continue
        rule = _select_rule(rules, category, SpendScope.DOMESTIC, total_monthly)
        monthly_total += _apply_category(
            outcome, category, spend, rule, card_min_spend, total_monthly
        )

    # --- international spend -------------------------------------------
    intl = user_profile.international_monthly_spend
    if intl > 0:
        rule = _select_rule(rules, SpendCategory.OTHER, SpendScope.INTERNATIONAL, total_monthly)
        if rule is None:
            outcome.warnings.append(
                "No international reward rate is recorded for this card; "
                "international spend is modelled as earning nothing."
            )
            outcome.category_breakdown.append(
                CategoryBreakdown(
                    category=SpendCategory.OTHER,
                    monthly_spend=intl,
                    rate_applied=None,
                    rate_source_status=VerificationStatus.UNKNOWN.value,
                    monthly_reward_uncapped=0.0,
                    monthly_reward_after_category_cap=0.0,
                    note="International spend — no recorded rate.",
                )
            )
        else:
            monthly_total += _apply_category(
                outcome,
                SpendCategory.OTHER,
                intl,
                rule,
                card_min_spend,
                total_monthly,
                label="international",
            )

    # --- card-level monthly cap ----------------------------------------
    card_cap = _card_total_monthly_cap(card)
    if card_cap is not None and monthly_total > card_cap:
        outcome.caps_applied.append(
            f"Card-level monthly cashback cap of AED {card_cap:,.0f} applied "
            f"(uncapped monthly reward would be AED {monthly_total:,.2f})."
        )
        _scale_breakdown(outcome, card_cap / monthly_total if monthly_total else 0.0)
        monthly_total = card_cap

    outcome.gross_annual_rewards = round(monthly_total * MONTHS_PER_YEAR, 2)
    outcome.annual_by_category = {
        f"{b.category.value}": round(b.monthly_reward_after_category_cap * MONTHS_PER_YEAR, 2)
        for b in outcome.category_breakdown
    }
    return outcome


def _apply_category(
    outcome: RewardOutcome,
    category: SpendCategory,
    spend: float,
    rule: CardRewardRule | None,
    card_min_spend: float | None,
    total_monthly: float,
    label: str | None = None,
) -> float:
    """Reward for one category in a representative month, after its own cap."""
    if rule is None:
        outcome.category_breakdown.append(
            CategoryBreakdown(
                category=category,
                monthly_spend=spend,
                rate_applied=None,
                rate_source_status=VerificationStatus.UNKNOWN.value,
                monthly_reward_uncapped=0.0,
                monthly_reward_after_category_cap=0.0,
                note="No reward rule recorded for this category.",
            )
        )
        return 0.0

    if rule.excluded or not rule.rate:
        outcome.category_breakdown.append(
            CategoryBreakdown(
                category=category,
                monthly_spend=spend,
                rate_applied=0.0,
                rate_source_status=rule.verification_status,
                monthly_reward_uncapped=0.0,
                monthly_reward_after_category_cap=0.0,
                note=rule.notes or "This category is excluded from the reward programme.",
            )
        )
        return 0.0

    if rule.reward_type != RewardType.CASHBACK_PCT.value:
        # Points/miles need a defensible valuation before any AED figure exists.
        outcome.unknown_fields.append("reward_valuation")
        outcome.warnings.append(
            f"{category.value} earns {rule.reward_type}, which has no verified AED valuation."
        )
        return 0.0

    gate = rule.min_monthly_spend_required or card_min_spend
    if gate and total_monthly < gate:
        outcome.category_breakdown.append(
            CategoryBreakdown(
                category=category,
                monthly_spend=spend,
                rate_applied=rule.rate,
                rate_source_status=rule.verification_status,
                monthly_reward_uncapped=round(spend * rule.rate, 2),
                monthly_reward_after_category_cap=0.0,
                cap_applied="minimum spend not met",
                note=f"Requires AED {gate:,.0f} total monthly spend.",
            )
        )
        return 0.0

    uncapped = spend * rule.rate
    capped = uncapped
    cap_note: str | None = None

    if rule.cap_scope == CapScope.PER_MERCHANT.value:
        # Deliberately not applied: the profile has no merchant split.
        cap_note = "per-merchant cap not modellable"
    elif rule.monthly_cap_amount is not None and uncapped > rule.monthly_cap_amount:
        capped = rule.monthly_cap_amount
        cap_note = f"category monthly cap AED {rule.monthly_cap_amount:,.0f}"
        outcome.caps_applied.append(
            f"{category.value}: capped at AED {rule.monthly_cap_amount:,.0f}/month "
            f"(would otherwise earn AED {uncapped:,.2f})."
        )
    elif rule.annual_cap_amount is not None and uncapped * MONTHS_PER_YEAR > rule.annual_cap_amount:
        capped = rule.annual_cap_amount / MONTHS_PER_YEAR
        cap_note = f"annual cap AED {rule.annual_cap_amount:,.0f}"
        outcome.caps_applied.append(
            f"{category.value}: annual cap of AED {rule.annual_cap_amount:,.0f} applied."
        )

    outcome.category_breakdown.append(
        CategoryBreakdown(
            category=category,
            monthly_spend=spend,
            rate_applied=rule.rate,
            rate_source_status=rule.verification_status,
            monthly_reward_uncapped=round(uncapped, 2),
            monthly_reward_after_category_cap=round(capped, 2),
            cap_applied=cap_note,
            note=(f"{label} spend. " if label else "") + (rule.notes or "") or None,
        )
    )
    return capped


def _scale_breakdown(outcome: RewardOutcome, factor: float) -> None:
    """Pro-rate category rewards when the card-level cap binds, so the
    breakdown still sums to the reported total."""
    for row in outcome.category_breakdown:
        row.monthly_reward_after_category_cap = round(
            row.monthly_reward_after_category_cap * factor, 2
        )
        row.cap_applied = (row.cap_applied or "") + " | card-level cap pro-rated"


def _card_min_monthly_spend(rules: list[CardRewardRule]) -> float | None:
    gates = [r.min_monthly_spend_required for r in rules if r.min_monthly_spend_required]
    return min(gates) if gates else None


def _card_total_monthly_cap(card: Card) -> float | None:
    for rule in card.reward_rules:
        if rule.cap_scope == CapScope.CARD_TOTAL.value and rule.monthly_cap_amount is not None:
            return rule.monthly_cap_amount
    extra = card.extra or {}
    caps = extra.get("card_level_caps") or {}
    if caps.get("cap_scope") == CapScope.CARD_TOTAL.value:
        return caps.get("monthly_cap_amount")
    return None
