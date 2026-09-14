"""Eligibility. UNKNOWN never silently becomes eligible."""
from __future__ import annotations

from app.db.models import Card
from app.db.schemas import UserProfile
from app.domain.enums import EligibilityResult, VerificationStatus


def check_eligibility(user_profile: UserProfile, card: Card) -> tuple[EligibilityResult, list[str]]:
    rules = card.eligibility
    if rules is None:
        return EligibilityResult.UNKNOWN, ["No eligibility criteria are recorded for this card."]

    reasons: list[str] = []
    hard_fail = False
    soft_unknown = False

    if rules.min_monthly_salary is not None:
        if user_profile.salary_monthly < rules.min_monthly_salary:
            hard_fail = True
            reasons.append(
                f"Monthly salary of AED {user_profile.salary_monthly:,.0f} is below the "
                f"AED {rules.min_monthly_salary:,.0f} minimum."
            )
        else:
            reasons.append(
                f"Meets the AED {rules.min_monthly_salary:,.0f} minimum salary requirement."
            )
    else:
        soft_unknown = True
        reasons.append("Minimum salary requirement is not established.")

    if rules.min_age is not None:
        if user_profile.age is None:
            soft_unknown = True
            reasons.append(f"Card requires age {rules.min_age}+; age was not supplied.")
        elif user_profile.age < rules.min_age:
            hard_fail = True
            reasons.append(f"Below the minimum age of {rules.min_age}.")

    if rules.residency_required and not user_profile.is_uae_resident:
        hard_fail = True
        reasons.append("UAE residency is required.")

    if rules.salary_transfer_required and not user_profile.salary_transferred:
        hard_fail = True
        reasons.append("Salary transfer to the issuing bank is required.")

    if rules.existing_relationship_required:
        soft_unknown = True
        reasons.append("An existing relationship with the issuer may be required.")

    if hard_fail:
        return EligibilityResult.NOT_ELIGIBLE, reasons
    if rules.verification_status in (
        VerificationStatus.UNKNOWN.value,
        VerificationStatus.PARTIALLY_VERIFIED.value,
    ):
        soft_unknown = True
        reasons.append("Eligibility criteria are not fully verified from an official source.")
    if soft_unknown:
        return EligibilityResult.POTENTIALLY_ELIGIBLE, reasons
    return EligibilityResult.ELIGIBLE, reasons
