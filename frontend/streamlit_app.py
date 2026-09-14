"""Streamlit MVP.

Talks to the FastAPI backend when API_BASE_URL is set, otherwise calls the
service layer directly. The backend never depends on this file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal, create_all  # noqa: E402
from app.db.schemas import UserProfile  # noqa: E402
from app.domain.enums import Emirate, RewardPreference, SpendCategory  # noqa: E402
from app.services.recommendation_service import recommend  # noqa: E402

API_BASE_URL = os.getenv("API_BASE_URL")

st.set_page_config(page_title="UAE Credit Card Intelligence", layout="wide")
st.title("UAE Credit Card Intelligence Agent")
st.caption(
    "Estimates which UAE card, or pair of cards, gives you the highest net annual value "
    "for your actual spending. Every number below is calculated deterministically."
)

CATEGORY_LABELS = {
    SpendCategory.GROCERIES: "Groceries", SpendCategory.DINING: "Dining",
    SpendCategory.FUEL: "Fuel & Salik", SpendCategory.TRAVEL: "Travel",
    SpendCategory.ONLINE: "Online", SpendCategory.UTILITIES: "Utilities",
    SpendCategory.GOVERNMENT: "Government", SpendCategory.FASHION: "Fashion",
    SpendCategory.ENTERTAINMENT: "Entertainment", SpendCategory.OTHER: "Other",
    SpendCategory.EDUCATION: "Education / school fees", SpendCategory.TELECOM: "Telecom",
}


@st.cache_resource
def _ready() -> bool:
    create_all()
    return True


_ready()

with st.sidebar:
    st.header("Location")
    country = st.selectbox("Country", ["UAE"], index=0)
    emirate = st.selectbox("Emirate", [e.value for e in Emirate], index=0)

    st.header("Monthly spending (AED)")
    spend: dict[SpendCategory, float] = {}
    for category, label in CATEGORY_LABELS.items():
        spend[category] = float(st.number_input(label, min_value=0.0, value=0.0, step=100.0))

    st.header("Financial")
    salary = float(st.number_input("Monthly salary (AED)", min_value=0.0, value=20000.0, step=1000.0))
    international = float(
        st.number_input("International monthly spend (AED)", min_value=0.0, value=0.0, step=500.0)
    )
    trips = int(st.number_input("Trips per year", min_value=0, max_value=365, value=0, step=1))
    fee_tolerance = float(
        st.number_input("Annual fee tolerance (AED)", min_value=0.0, value=500.0, step=100.0)
    )
    preference = st.selectbox("Reward preference", [p.value for p in RewardPreference], index=0)
    pays_in_full = st.checkbox("I pay my balance in full every month", value=True)
    age = int(st.number_input("Age", min_value=18, max_value=100, value=35, step=1))

    st.header("Current card (optional)")
    with SessionLocal() as session:
        from sqlalchemy import select

        from app.db.models import Card

        card_options = ["(none)"] + [c.card_id for c in session.scalars(select(Card))]
    current_card = st.selectbox("Card you hold today", card_options, index=0)

    run = st.button("Optimize My Cards", type="primary", use_container_width=True)

if not run:
    st.info("Enter your monthly spending in the sidebar, then select **Optimize My Cards**.")
    st.stop()

try:
    profile = UserProfile(
        country=country,
        emirate=Emirate(emirate),
        monthly_category_spend={c: v for c, v in spend.items() if v > 0},
        international_monthly_spend=international,
        travel_frequency_per_year=trips,
        salary_monthly=salary,
        reward_preference=RewardPreference(preference),
        annual_fee_tolerance=fee_tolerance,
        pays_balance_in_full=pays_in_full,
        age=age,
        current_card_id=None if current_card == "(none)" else current_card,
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"Please check your inputs: {exc}")
    st.stop()

if profile.total_monthly_spend <= 0:
    st.warning("Enter at least one monthly spending amount.")
    st.stop()

with SessionLocal() as session:
    response = recommend(session, profile)


def money(value: float | None) -> str:
    return "unknown" if value is None else f"AED {value:,.0f}"


def fx_display(card) -> str:
    return "not verified" if "fx_fee" in card.unknown_fields else money(card.fx_cost)


quality_colour = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}

if not response.top_cards:
    st.error(
        "No card in the database could be ranked precisely for this profile. "
        "The conditional results below explain what is missing."
    )
else:
    best = response.top_cards[0]
    st.subheader("Recommended card")
    st.markdown(f"### {best.card_name}")
    st.caption(best.issuer)
    columns = st.columns(4)
    columns[0].metric("Estimated annual rewards", money(best.gross_annual_rewards))
    columns[1].metric("Annual fee", money(best.annual_fee))
    columns[2].metric("Estimated FX cost", fx_display(best))
    columns[3].metric("Estimated net annual value", money(best.net_annual_value))

    st.markdown("#### Why this card, for your spending")
    for insight in response.insights[:5]:
        st.markdown(f"- {insight}")

    with st.expander("Calculation breakdown", expanded=True):
        st.table(
            [
                {
                    "Category": row.category.value,
                    "Monthly spend": f"{row.monthly_spend:,.0f}",
                    "Rate": "—" if row.rate_applied is None else f"{row.rate_applied * 100:.2f}%",
                    "Monthly reward": f"{row.monthly_reward_after_category_cap:,.2f}",
                    "Cap / note": row.cap_applied or row.note or "",
                    "Field status": row.rate_source_status,
                }
                for row in best.category_breakdown
            ]
        )

st.divider()
st.subheader("Alternatives")
for card in response.top_cards[1:]:
    st.markdown(
        f"**{card.card_name}** — net {money(card.net_annual_value)} "
        f"(rewards {money(card.gross_annual_rewards)}, fee {money(card.annual_fee)}, "
        f"FX {fx_display(card)}) {quality_colour.get(card.data_quality.value, '')}"
    )

if response.conditional_cards:
    st.subheader("Cards not given a precise ranking")
    for card in response.conditional_cards:
        st.warning(f"**{card.card_name}** — {card.exclusion_reason}")

if response.current_card_comparison:
    comparison = response.current_card_comparison
    st.subheader("Against your current card")
    columns = st.columns(3)
    columns[0].metric(comparison.current_card_name, money(comparison.current_estimated_value))
    columns[1].metric("Recommended", money(comparison.recommended_estimated_value))
    columns[2].metric(
        "Estimated potential additional value", money(comparison.potential_additional_value)
    )

if response.best_strategy:
    strategy = response.best_strategy
    st.subheader("Best two-card strategy")
    st.markdown(
        f"**{strategy.primary_card_name}** + **{strategy.secondary_card_name}** — combined "
        f"{money(strategy.combined_net_annual_value)}, incremental "
        f"{money(strategy.incremental_value)}"
    )
    st.caption(strategy.reason)
    if strategy.recommended:
        st.table(
            [{"Category": k, "Use": v} for k, v in strategy.category_allocation.items()]
        )

st.divider()
st.subheader("Important conditions")
if response.top_cards:
    best = response.top_cards[0]
    for item in best.caps_applied + best.warnings + best.eligibility_reasons:
        st.markdown(f"- {item}")
    if best.benefits:
        st.markdown("**Additional benefits (no monetary value assigned):**")
        for benefit in best.benefits:
            st.markdown(f"- {benefit}")

st.subheader("Assumptions")
for assumption in response.assumptions:
    st.markdown(f"- {assumption}")

st.subheader("Data quality")
st.markdown(
    f"{quality_colour.get(response.data_quality.value, '')} **{response.data_quality.value}**"
)
if response.top_cards:
    for reason in response.top_cards[0].data_quality_reasons:
        st.markdown(f"- {reason}")
    if response.top_cards[0].unknown_fields:
        st.markdown(
            "- Unverified fields: " + ", ".join(response.top_cards[0].unknown_fields)
        )

if response.explanation:
    st.subheader("Explanation")
    st.write(response.explanation)

st.caption(
    f"run_id `{response.run_id}` · calculation `{response.calculation_version}` · "
    f"card data `{response.card_data_version}`"
)
st.info(response.disclaimer)
