from app.domain.enums import SpendCategory as C
from app.services.reward_engine import calculate_card_rewards
from tests.conftest import make_card, make_profile


def test_1_flat_ten_percent_no_cap_no_fee():
    """AED 1,000/month at 10% with no cap earns AED 1,200 a year."""
    card = make_card(rules=[{"category": "other", "rate": 0.10}])
    profile = make_profile({C.OTHER: 1000})
    assert calculate_card_rewards(profile, card).gross_annual_rewards == 1200.0


def test_2_monthly_cap_binds():
    """5% on AED 10,000 is AED 500/month, but a AED 100 cap makes it AED 1,200/year."""
    card = make_card(
        rules=[
            {
                "category": "other",
                "rate": 0.05,
                "monthly_cap_amount": 100,
                "cap_scope": "PER_CATEGORY",
            }
        ]
    )
    profile = make_profile({C.OTHER: 10000})
    assert calculate_card_rewards(profile, card).gross_annual_rewards == 1200.0


def test_3_category_specific_rates():
    card = make_card(
        rules=[
            {"category": "groceries", "rate": 0.03},
            {"category": "dining", "rate": 0.06},
            {"category": "other", "rate": 0.01},
        ]
    )
    profile = make_profile({C.GROCERIES: 2000, C.DINING: 1000, C.OTHER: 1000})
    # (2000*.03 + 1000*.06 + 1000*.01) * 12 = (60+60+10)*12 = 1560
    assert calculate_card_rewards(profile, card).gross_annual_rewards == 1560.0


def test_3b_excluded_category_earns_nothing():
    card = make_card(
        rules=[{"category": "utilities", "rate": 0.0, "excluded": True},
               {"category": "other", "rate": 0.01}]
    )
    profile = make_profile({C.UTILITIES: 5000, C.OTHER: 1000})
    assert calculate_card_rewards(profile, card).gross_annual_rewards == 120.0


def test_4_minimum_spend_gate():
    card = make_card(
        rules=[{"category": "other", "rate": 0.05, "min_monthly_spend_required": 5000}]
    )
    below = make_profile({C.OTHER: 4000})
    above = make_profile({C.OTHER: 6000})
    assert calculate_card_rewards(below, card).gross_annual_rewards == 0.0
    assert calculate_card_rewards(below, card).min_spend_met is False
    assert calculate_card_rewards(above, card).gross_annual_rewards == 3600.0


def test_tier_selection_uses_total_monthly_spend():
    card = make_card(
        rules=[
            {"category": "groceries", "rate": 0.03, "tier_min_monthly_spend": 3000,
             "tier_max_monthly_spend": 9999},
            {"category": "groceries", "rate": 0.10, "tier_min_monthly_spend": 10000},
        ]
    )
    low = make_profile({C.GROCERIES: 2000, C.OTHER: 2000})    # total 4000 -> 3%
    high = make_profile({C.GROCERIES: 2000, C.OTHER: 9000})   # total 11000 -> 10%
    assert calculate_card_rewards(low, card).gross_annual_rewards == 720.0
    assert calculate_card_rewards(high, card).gross_annual_rewards == 2400.0


def test_per_merchant_cap_is_not_silently_applied():
    """A per-merchant cap cannot be modelled, so it must not be applied as if
    it were a category cap. The card is flagged elsewhere instead."""
    card = make_card(
        rules=[{"category": "dining", "rate": 0.05, "monthly_cap_amount": 50,
                "cap_scope": "PER_MERCHANT"}]
    )
    profile = make_profile({C.DINING: 4000})
    outcome = calculate_card_rewards(profile, card)
    assert outcome.gross_annual_rewards == 2400.0
    assert any("per-merchant" in (row.cap_applied or "") for row in outcome.category_breakdown)
