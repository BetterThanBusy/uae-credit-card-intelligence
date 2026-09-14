"""Foreign exchange cost.

An unverified FX fee is returned as None, never as zero. Treating an unknown
cost as zero would overstate net value on exactly the profiles where foreign
spend matters most.
"""
from __future__ import annotations

from app.db.models import Card
from app.db.schemas import UserProfile
from app.domain.enums import FeeType, VerificationStatus


def calculate_fx_cost(
    user_profile: UserProfile, card: Card
) -> tuple[float | None, str, list[str]]:
    """Return (annual_fx_cost, verification_status, notes)."""
    intl_annual = user_profile.international_monthly_spend * 12
    if intl_annual <= 0:
        return 0.0, VerificationStatus.VERIFIED.value, ["No international spend recorded."]

    fee_row = next((f for f in card.fees if f.fee_type == FeeType.FX_FEE.value), None)
    if fee_row is None or fee_row.amount is None:
        return (
            None,
            VerificationStatus.UNKNOWN.value,
            [
                "Foreign transaction fee could not be verified, so the FX cost on "
                f"AED {intl_annual:,.0f} of annual international spend is not quantified. "
                "Net value for this card excludes it and is therefore optimistic."
            ],
        )

    rate = float(fee_row.amount)
    cost = intl_annual * (rate / 100.0 if fee_row.unit == "PERCENT" else 0.0)
    return (
        round(cost, 2),
        fee_row.verification_status,
        [f"FX cost at {rate}% on AED {intl_annual:,.0f} of international spend."],
    )
