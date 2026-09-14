import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.card_importer import import_cards, load_dataset
from app.db.models import Base
from app.domain.enums import Emirate, RewardPreference, SpendCategory as C
from app.db.schemas import UserProfile
from app.services.recommendation_service import recommend


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        import_cards(s, load_dataset())
        yield s


def acceptance_profile(**overrides) -> UserProfile:
    data = dict(
        emirate=Emirate.DUBAI,
        monthly_category_spend={
            C.GROCERIES: 4000, C.DINING: 2000, C.FUEL: 1000, C.TRAVEL: 2000,
            C.ONLINE: 1500, C.UTILITIES: 1000, C.OTHER: 1000,
        },
        international_monthly_spend=2000,
        travel_frequency_per_year=3,
        salary_monthly=25000,
        reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=500,
        pays_balance_in_full=True,
        age=35,
    )
    data.update(overrides)
    return UserProfile(**data)


def test_11_end_to_end_recommendation_is_complete(session):
    response = recommend(session, acceptance_profile(), with_llm=False)
    assert response.run_id
    assert response.card_data_version.startswith(f"{len(load_dataset()['cards'])}cards-")
    assert response.calculation_version == "1.0.0"
    assert response.top_cards
    assert response.insights
    assert response.assumptions
    assert response.explanation
    assert response.disclaimer.startswith("This is an estimate")
    best = response.top_cards[0]
    assert best.category_breakdown
    assert best.net_annual_value == round(
        best.gross_annual_rewards - best.annual_fee - best.fx_cost, 2
    )


def test_9_current_card_comparison(session):
    response = recommend(
        session, acceptance_profile(current_card_id="mashreq-cashback"), with_llm=False
    )
    comparison = response.current_card_comparison
    assert comparison is not None
    assert comparison.current_card_id == "mashreq-cashback"
    assert comparison.potential_additional_value == round(
        comparison.recommended_estimated_value - comparison.current_estimated_value, 2
    )
    assert response.missed_value == comparison.potential_additional_value


def test_recommendation_is_reproducible(session):
    """Same profile + same card data version => identical numbers."""
    first = recommend(session, acceptance_profile(), with_llm=False)
    second = recommend(session, acceptance_profile(), with_llm=False)
    assert first.run_id != second.run_id
    assert first.card_data_version == second.card_data_version
    assert [c.net_annual_value for c in first.top_cards] == [
        c.net_annual_value for c in second.top_cards
    ]


def test_different_profiles_produce_different_reward_totals(session):
    """The whole premise: the answer must move with the spending pattern."""
    grocery = recommend(
        session,
        acceptance_profile(
            monthly_category_spend={C.GROCERIES: 8000}, international_monthly_spend=0
        ),
        with_llm=False,
    )
    dining = recommend(
        session,
        acceptance_profile(
            monthly_category_spend={C.DINING: 8000}, international_monthly_spend=0
        ),
        with_llm=False,
    )
    assert grocery.top_cards[0].gross_annual_rewards != dining.top_cards[0].gross_annual_rewards


def test_low_salary_profile_loses_high_salary_cards(session):
    rich = recommend(session, acceptance_profile(salary_monthly=40000), with_llm=False)
    poor = recommend(session, acceptance_profile(salary_monthly=6000), with_llm=False)
    assert len(poor.eligible_cards) < len(rich.eligible_cards)


def test_unranked_cards_are_surfaced_with_reasons(session):
    response = recommend(session, acceptance_profile(), with_llm=False)
    assert response.conditional_cards
    for card in response.conditional_cards:
        assert card.exclusion_reason


def test_profile_rejects_negative_spend():
    with pytest.raises(ValueError):
        acceptance_profile(monthly_category_spend={C.GROCERIES: -100})


def test_profile_rejects_non_uae_country():
    with pytest.raises(ValueError):
        UserProfile(emirate=Emirate.DUBAI, salary_monthly=10000, country="USA")
