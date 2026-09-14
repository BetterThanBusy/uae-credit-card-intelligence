from app.domain.enums import SpendCategory as C
from app.services.reward_engine import calculate_card_rewards
from tests.conftest import make_card, make_profile


def test_card_level_cap_binds_after_category_caps():
    card = make_card(
        rules=[{"category": "dining", "rate": 0.06}, {"category": "groceries", "rate": 0.03}],
        card_total_monthly_cap=100,
    )
    profile = make_profile({C.DINING: 5000, C.GROCERIES: 5000})
    # uncapped monthly = 300 + 150 = 450 -> card cap 100 -> 1200/yr
    outcome = calculate_card_rewards(profile, card)
    assert outcome.gross_annual_rewards == 1200.0
    assert any("Card-level monthly" in c for c in outcome.caps_applied)


def test_breakdown_sums_to_total_after_card_cap():
    card = make_card(
        rules=[{"category": "dining", "rate": 0.06}, {"category": "groceries", "rate": 0.03}],
        card_total_monthly_cap=100,
    )
    profile = make_profile({C.DINING: 5000, C.GROCERIES: 5000})
    outcome = calculate_card_rewards(profile, card)
    total = sum(r.monthly_reward_after_category_cap for r in outcome.category_breakdown)
    assert abs(total * 12 - outcome.gross_annual_rewards) < 1.0


def test_annual_cap_converted_to_monthly():
    card = make_card(
        rules=[{"category": "other", "rate": 0.10, "annual_cap_amount": 600,
                "cap_scope": "PER_CATEGORY"}]
    )
    profile = make_profile({C.OTHER: 1000})  # would earn 1200/yr
    assert calculate_card_rewards(profile, card).gross_annual_rewards == 600.0
