from app.domain.enums import SpendCategory as C
from app.services.fx_engine import calculate_fx_cost
from app.services.ranking_engine import evaluate_card
from tests.conftest import make_card, make_profile


def test_6_fx_cost_deducted_when_fee_known():
    card = make_card(
        rules=[{"category": "other", "rate": 0.01, "scope": "INTERNATIONAL"},
               {"category": "other", "rate": 0.01, "scope": "DOMESTIC"}],
        fx_fee=3.0, fx_status="VERIFIED",
    )
    profile = make_profile({C.OTHER: 1000}, international_monthly_spend=2000)
    cost, status, _ = calculate_fx_cost(profile, card)
    assert cost == 720.0          # 24,000 * 3%
    assert status == "VERIFIED"
    result = evaluate_card(profile, card)
    # rewards: (1000 + 2000) * 1% * 12 = 360 ; net = 360 - 0 fee - 720 fx
    assert result.net_annual_value == -360.0


def test_unknown_fx_fee_is_not_treated_as_zero_cost():
    card = make_card(rules=[{"category": "other", "rate": 0.01}], fx_fee=None,
                     fx_status="UNKNOWN")
    profile = make_profile({C.OTHER: 1000}, international_monthly_spend=2000)
    cost, status, notes = calculate_fx_cost(profile, card)
    assert cost is None
    assert status == "UNKNOWN"
    assert any("optimistic" in n for n in notes)
    result = evaluate_card(profile, card)
    assert "fx_fee" in result.unknown_fields
    assert result.data_quality.value == "LOW"


def test_unknown_fx_fee_is_harmless_without_international_spend():
    card = make_card(rules=[{"category": "other", "rate": 0.01}], fx_fee=None,
                     fx_status="UNKNOWN")
    result = evaluate_card(make_profile({C.OTHER: 1000}), card)
    assert "fx_fee" not in result.unknown_fields
    assert result.data_quality.value == "HIGH"


def test_international_spend_counts_toward_minimum_spend_gate():
    card = make_card(
        rules=[{"category": "other", "rate": 0.05, "min_monthly_spend_required": 5000}]
    )
    profile = make_profile({C.OTHER: 3000}, international_monthly_spend=3000)
    result = evaluate_card(profile, card)
    assert result.gross_annual_rewards > 0
