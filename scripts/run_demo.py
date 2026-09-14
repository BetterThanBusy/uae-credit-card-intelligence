"""Run the five golden UAE profiles plus the acceptance profile."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal  # noqa: E402
from app.db.schemas import UserProfile  # noqa: E402
from app.domain.enums import Emirate, RewardPreference, SpendCategory as C  # noqa: E402
from app.services.recommendation_service import recommend  # noqa: E402

GOLDEN_PROFILES: dict[str, UserProfile] = {
    "Acceptance profile (Dubai, balanced + international)": UserProfile(
        emirate=Emirate.DUBAI,
        monthly_category_spend={
            C.GROCERIES: 4000, C.DINING: 2000, C.FUEL: 1000, C.TRAVEL: 2000,
            C.ONLINE: 1500, C.UTILITIES: 1000, C.OTHER: 1000,
        },
        international_monthly_spend=2000, travel_frequency_per_year=3,
        salary_monthly=25000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=500, pays_balance_in_full=True, age=35,
        current_card_id="mashreq-cashback",
    ),
    "High grocery spender": UserProfile(
        emirate=Emirate.SHARJAH,
        monthly_category_spend={C.GROCERIES: 6000, C.DINING: 800, C.FUEL: 600, C.OTHER: 1000},
        salary_monthly=15000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=400, age=41,
    ),
    "High dining spender": UserProfile(
        emirate=Emirate.DUBAI,
        monthly_category_spend={C.DINING: 5000, C.GROCERIES: 1500, C.ENTERTAINMENT: 800, C.OTHER: 1200},
        salary_monthly=22000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=500, age=33,
    ),
    "Frequent international traveller": UserProfile(
        emirate=Emirate.ABU_DHABI,
        monthly_category_spend={C.TRAVEL: 4000, C.DINING: 2000, C.GROCERIES: 1500, C.OTHER: 1500},
        international_monthly_spend=6000, travel_frequency_per_year=12,
        salary_monthly=40000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=1500, age=45,
    ),
    "High fuel spender": UserProfile(
        emirate=Emirate.RAS_AL_KHAIMAH,
        monthly_category_spend={C.FUEL: 2500, C.GROCERIES: 2500, C.DINING: 700, C.OTHER: 800},
        salary_monthly=12000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=300, age=38,
    ),
    "Balanced household spender": UserProfile(
        emirate=Emirate.AJMAN,
        monthly_category_spend={
            C.GROCERIES: 2500, C.DINING: 1200, C.FUEL: 900, C.UTILITIES: 900,
            C.EDUCATION: 3000, C.TELECOM: 400, C.OTHER: 1100,
        },
        salary_monthly=18000, reward_preference=RewardPreference.CASHBACK,
        annual_fee_tolerance=350, age=44,
    ),
}


def show(name: str, response) -> None:
    print("\n" + "=" * 78)
    print(name)
    print("=" * 78)
    print(f"run_id={response.run_id}  card_data_version={response.card_data_version}  "
          f"calc={response.calculation_version}  quality={response.data_quality.value}")
    if not response.top_cards:
        print("  No card could be ranked precisely.")
    for index, card in enumerate(response.top_cards, 1):
        print(f"  {index}. {card.card_name:<46} net AED {card.net_annual_value:>9,.0f}  "
              f"(gross {card.gross_annual_rewards:,.0f} - fee {card.annual_fee:,.0f} "
              f"- fx {"unknown" if "fx_fee" in card.unknown_fields else f"{card.fx_cost:,.0f}"})  [{card.data_quality.value}]")
    for card in response.conditional_cards:
        print(f"  ~  {card.card_name:<46} not ranked: {card.exclusion_reason}")
    if response.best_strategy:
        s = response.best_strategy
        print(f"  Two-card: {s.primary_card_name} + {s.secondary_card_name} -> "
              f"AED {s.combined_net_annual_value:,.0f} (incremental {s.incremental_value:,.0f}; "
              f"recommended={s.recommended})")
    if response.current_card_comparison:
        c = response.current_card_comparison
        print(f"  Current {c.current_card_name}: AED {c.current_estimated_value:,.0f} -> "
              f"potential additional AED {c.potential_additional_value:,.0f}")
    print("  Insights:")
    for insight in response.insights[:6]:
        print(f"    - {insight}")


def main() -> None:
    with SessionLocal() as session:
        for name, profile in GOLDEN_PROFILES.items():
            show(name, recommend(session, profile, with_llm=False))


if __name__ == "__main__":
    main()
