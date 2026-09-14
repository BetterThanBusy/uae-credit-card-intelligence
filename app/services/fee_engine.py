"""Annual fee, including first-year and spend-based waivers.

Steady-state assumption: we report the year-two fee, because a first-year
waiver flatters a card the user will hold for years. The assumption is
returned so the user sees it.
"""
from __future__ import annotations

from app.db.models import Card
from app.db.schemas import UserProfile
from app.domain.enums import FeeType, VerificationStatus


def calculate_annual_fee(
    user_profile: UserProfile, card: Card
) -> tuple[float | None, str, list[str]]:
    """Return (fee, verification_status, notes)."""
    notes: list[str] = []
    fee_row = next((f for f in card.fees if f.fee_type == FeeType.ANNUAL_FEE.value), None)
    if fee_row is None:
        return None, VerificationStatus.UNKNOWN.value, ["No annual fee record for this card."]
    # A verified waiver the user's spend clears makes the fee zero, even when the
    # amount charged on a miss was never established. Returning UNKNOWN here would
    # block a card from ranking over a number that cannot affect the result.
    if fee_row.waiver_min_annual_spend is not None:
        annual_spend = user_profile.total_monthly_spend * 12
        if annual_spend >= fee_row.waiver_min_annual_spend:
            return (
                0.0,
                fee_row.verification_status
                if fee_row.amount is not None
                else VerificationStatus.VERIFIED.value,
                [
                    f"Annual fee waived: projected annual spend of AED {annual_spend:,.0f} meets "
                    f"the AED {fee_row.waiver_min_annual_spend:,.0f} waiver threshold."
                    + (
                        " The fee amount charged if the waiver were missed is not verified, "
                        "but it does not apply to this profile."
                        if fee_row.amount is None
                        else ""
                    )
                ],
            )

    if fee_row.amount is None:
        return (
            None,
            VerificationStatus.UNKNOWN.value,
            [
                "Annual fee could not be verified and this profile does not meet a "
                "verified waiver condition."
            ],
        )

    fee = float(fee_row.amount)
    if fee == 0:
        return 0.0, fee_row.verification_status, ["No annual fee."]

    notes.append(
        "Year-two fee is used; a first-year waiver is not counted as ongoing value."
        if fee_row.first_year_free
        else "Annual fee applies from the first year."
    )

    if fee_row.waiver_min_annual_spend is not None:
        annual_spend = user_profile.total_monthly_spend * 12
        if annual_spend >= fee_row.waiver_min_annual_spend:
            notes.append(
                f"Annual fee waived: projected annual spend of AED {annual_spend:,.0f} meets "
                f"the AED {fee_row.waiver_min_annual_spend:,.0f} waiver threshold."
            )
            return 0.0, fee_row.verification_status, notes
        notes.append(
            f"Fee waiver needs AED {fee_row.waiver_min_annual_spend:,.0f} annual spend; "
            f"projected spend is AED {annual_spend:,.0f}."
        )
    return fee, fee_row.verification_status, notes
