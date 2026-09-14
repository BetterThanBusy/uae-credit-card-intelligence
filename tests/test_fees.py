from app.domain.enums import SpendCategory as C
from app.services.fee_engine import calculate_annual_fee
from app.services.ranking_engine import evaluate_card
from tests.conftest import make_card, make_profile


def test_5_annual_fee_reduces_net_value():
    card = make_card(rules=[{"category": "other", "rate": 0.10}], annual_fee=400)
    profile = make_profile({C.OTHER: 1000})
    result = evaluate_card(profile, card)
    assert result.gross_annual_rewards == 1200.0
    assert result.annual_fee == 400.0
    assert result.net_annual_value == 800.0


def test_first_year_free_does_not_zero_the_ongoing_fee():
    card = make_card(rules=[{"category": "other", "rate": 0.10}], annual_fee=400,
                     first_year_free=True)
    fee, _, notes = calculate_annual_fee(make_profile({C.OTHER: 1000}), card)
    assert fee == 400.0
    assert any("Year-two" in n for n in notes)


def test_spend_based_waiver_applies():
    card = make_card(rules=[{"category": "other", "rate": 0.01}], annual_fee=300,
                     waiver_min_annual_spend=30000)
    profile = make_profile({C.OTHER: 3000})  # 36,000/yr >= 30,000
    fee, _, notes = calculate_annual_fee(profile, card)
    assert fee == 0.0
    assert any("waived" in n for n in notes)


def test_waiver_not_met_keeps_fee():
    card = make_card(rules=[{"category": "other", "rate": 0.01}], annual_fee=300,
                     waiver_min_annual_spend=30000)
    fee, _, _ = calculate_annual_fee(make_profile({C.OTHER: 1000}), card)
    assert fee == 300.0


def test_unknown_annual_fee_makes_card_unrankable():
    card = make_card(rules=[{"category": "other", "rate": 0.10}], annual_fee=None,
                     fee_status="UNKNOWN")
    result = evaluate_card(make_profile({C.OTHER: 1000}), card)
    assert result.rankable is False
    assert "annual_fee" in result.unknown_fields
    assert result.data_quality.value == "LOW"


def test_verified_waiver_beats_unknown_fee_amount():
    """A waiver the user clearly meets makes the fee zero, so an unverified fee
    amount must not block the card from ranking."""
    card = make_card(
        rules=[{"category": "other", "rate": 0.01}],
        annual_fee=None,
        fee_status="UNKNOWN",
        waiver_min_annual_spend=12000,
    )
    profile = make_profile({C.OTHER: 3000})  # 36,000/yr, clears the waiver
    fee, _, notes = calculate_annual_fee(profile, card)
    assert fee == 0.0
    assert any("does not apply to this profile" in n for n in notes)
    result = evaluate_card(profile, card)
    assert result.rankable is True


def test_unknown_fee_still_blocks_when_the_waiver_is_not_met():
    card = make_card(
        rules=[{"category": "other", "rate": 0.01}],
        annual_fee=None,
        fee_status="UNKNOWN",
        waiver_min_annual_spend=12000,
    )
    profile = make_profile({C.OTHER: 500})  # 6,000/yr, misses the waiver
    fee, _, _ = calculate_annual_fee(profile, card)
    assert fee is None
    assert evaluate_card(profile, card).rankable is False
