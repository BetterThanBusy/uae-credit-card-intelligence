from app.domain.enums import SpendCategory as C
from app.services.ranking_engine import evaluate_card, rank_cards
from tests.conftest import make_card, make_profile


def test_8_unknown_critical_reward_field_blocks_precise_ranking():
    card = make_card(
        rules=[{"category": "groceries", "rate": 0.10, "verification_status": "UNKNOWN"}]
    )
    result = evaluate_card(make_profile({C.GROCERIES: 3000}), card)
    assert result.rankable is False
    assert result.data_quality.value == "LOW"
    assert result.exclusion_reason is not None


def test_unknown_field_in_an_unused_category_does_not_block():
    card = make_card(
        rules=[{"category": "groceries", "rate": 0.03},
               {"category": "travel", "rate": 0.10, "verification_status": "UNKNOWN"}]
    )
    result = evaluate_card(make_profile({C.GROCERIES: 3000}), card)
    assert result.rankable is True


def test_modelling_limitation_excludes_card_from_ranking():
    card = make_card(rules=[{"category": "dining", "rate": 0.05}],
                     modelling_limitation="per-merchant cap")
    ranked, conditional = rank_cards(make_profile({C.DINING: 2000}), [card])
    assert ranked == []
    assert conditional[0].exclusion_reason == "per-merchant cap"


def test_ranking_is_ordered_by_net_value():
    rich = make_card("rich", rules=[{"category": "other", "rate": 0.05}], annual_fee=0)
    poor = make_card("poor", rules=[{"category": "other", "rate": 0.01}], annual_fee=0)
    ranked, _ = rank_cards(make_profile({C.OTHER: 2000}), [poor, rich])
    assert [c.card_id for c in ranked] == ["rich", "poor"]
    assert ranked[0].net_annual_value > ranked[1].net_annual_value


def test_ineligible_card_is_not_ranked():
    card = make_card(min_salary=50000, rules=[{"category": "other", "rate": 0.10}])
    ranked, conditional = rank_cards(make_profile({C.OTHER: 2000}, salary_monthly=10000), [card])
    assert ranked == []
    assert conditional[0].eligibility.value == "not_eligible"


def test_every_result_carries_its_calculation():
    card = make_card(rules=[{"category": "groceries", "rate": 0.03}])
    result = evaluate_card(make_profile({C.GROCERIES: 2000}), card)
    assert result.category_breakdown
    assert result.category_breakdown[0].rate_applied == 0.03
    assert result.assumptions
