from app.domain.enums import SpendCategory as C
from app.services.ranking_engine import rank_cards
from app.services.strategy_engine import optimize_two_card_strategy
from tests.conftest import make_card, make_profile


def _best(profile, cards):
    ranked, _ = rank_cards(profile, cards)
    return ranked[0] if ranked else None


def test_10_two_card_split_beats_single_when_specialists_differ():
    grocery = make_card("grocery", rules=[{"category": "groceries", "rate": 0.10},
                                          {"category": "dining", "rate": 0.0}])
    dining = make_card("dining", rules=[{"category": "dining", "rate": 0.10},
                                        {"category": "groceries", "rate": 0.0}])
    profile = make_profile({C.GROCERIES: 4000, C.DINING: 4000})
    cards = [grocery, dining]
    strategy = optimize_two_card_strategy(profile, cards, _best(profile, cards))
    assert strategy is not None
    # single best = 4800 ; split = 4800 + 4800 = 9600
    assert strategy.combined_net_annual_value == 9600.0
    assert strategy.incremental_value == 4800.0
    assert strategy.recommended is True


def test_two_card_never_scores_below_the_best_single_card():
    """Holding a second card is optional, so the optimum can never be worse."""
    strong = make_card("strong", rules=[{"category": "groceries", "rate": 0.10},
                                        {"category": "dining", "rate": 0.10}])
    weak = make_card("weak", rules=[{"category": "groceries", "rate": 0.01},
                                    {"category": "dining", "rate": 0.01}], annual_fee=500)
    profile = make_profile({C.GROCERIES: 3000, C.DINING: 3000})
    cards = [strong, weak]
    best = _best(profile, cards)
    strategy = optimize_two_card_strategy(profile, cards, best)
    assert strategy.combined_net_annual_value >= best.net_annual_value
    assert strategy.incremental_value >= 0


def test_marginal_gain_is_not_recommended():
    a = make_card("a", rules=[{"category": "groceries", "rate": 0.05},
                              {"category": "dining", "rate": 0.05}])
    b = make_card("b", rules=[{"category": "groceries", "rate": 0.05},
                              {"category": "dining", "rate": 0.0501}])
    profile = make_profile({C.GROCERIES: 2000, C.DINING: 2000})
    cards = [a, b]
    strategy = optimize_two_card_strategy(profile, cards, _best(profile, cards))
    assert strategy.recommended is False
    assert "threshold" in strategy.reason


def test_single_usable_card_yields_no_strategy():
    only = make_card("only", rules=[{"category": "other", "rate": 0.05}])
    profile = make_profile({C.OTHER: 2000})
    assert optimize_two_card_strategy(profile, [only], _best(profile, [only])) is None
