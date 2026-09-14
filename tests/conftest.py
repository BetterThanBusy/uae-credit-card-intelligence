"""Synthetic fixtures.

These are test doubles used to prove the arithmetic, and they never enter the
database or any recommendation. The shipped dataset stays research-only.
"""
from __future__ import annotations

import pytest

from app.db.models import Card, CardEligibility, CardFee, CardRewardRule
from app.db.schemas import UserProfile
from app.domain.enums import Emirate, RewardPreference, SpendCategory


def make_card(
    card_id: str = "test-card",
    rules: list[dict] | None = None,
    annual_fee: float | None = 0.0,
    fee_status: str = "VERIFIED",
    first_year_free: bool = False,
    waiver_min_annual_spend: float | None = None,
    fx_fee: float | None = None,
    fx_status: str = "UNKNOWN",
    min_salary: float | None = 0.0,
    min_age: int | None = None,
    elig_status: str = "VERIFIED",
    card_total_monthly_cap: float | None = None,
    modelling_limitation: str | None = None,
) -> Card:
    card = Card(
        card_id=card_id,
        card_name=f"Test {card_id}",
        issuer="Test Issuer",
        card_type="cashback",
        country="UAE",
        version=1,
        modelling_limitation=modelling_limitation,
        extra={
            "card_level_caps": (
                {"monthly_cap_amount": card_total_monthly_cap, "cap_scope": "CARD_TOTAL"}
                if card_total_monthly_cap
                else None
            )
        },
    )
    for rule in rules or []:
        card.reward_rules.append(
            CardRewardRule(
                category=rule["category"],
                scope=rule.get("scope", "ANY"),
                reward_type=rule.get("reward_type", "CASHBACK_PCT"),
                rate=rule.get("rate"),
                tier_min_monthly_spend=rule.get("tier_min_monthly_spend"),
                tier_max_monthly_spend=rule.get("tier_max_monthly_spend"),
                monthly_cap_amount=rule.get("monthly_cap_amount"),
                annual_cap_amount=rule.get("annual_cap_amount"),
                cap_scope=rule.get("cap_scope"),
                min_monthly_spend_required=rule.get("min_monthly_spend_required"),
                verification_status=rule.get("verification_status", "VERIFIED"),
                excluded=rule.get("excluded", False),
            )
        )
    card.fees.append(
        CardFee(
            fee_type="ANNUAL_FEE",
            amount=annual_fee,
            unit="AED",
            first_year_free=first_year_free,
            waiver_min_annual_spend=waiver_min_annual_spend,
            verification_status=fee_status,
        )
    )
    card.fees.append(
        CardFee(fee_type="FX_FEE", amount=fx_fee, unit="PERCENT", verification_status=fx_status)
    )
    card.eligibility = CardEligibility(
        min_monthly_salary=min_salary,
        min_age=min_age,
        residency_required=True,
        verification_status=elig_status,
    )
    return card


def make_profile(spend: dict[SpendCategory, float], **kwargs) -> UserProfile:
    defaults = dict(
        emirate=Emirate.DUBAI,
        salary_monthly=20000,
        reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=500,
        pays_balance_in_full=True,
        age=35,
    )
    defaults.update(kwargs)
    return UserProfile(monthly_category_spend=spend, **defaults)


@pytest.fixture
def card_factory():
    return make_card


@pytest.fixture
def profile_factory():
    return make_profile
