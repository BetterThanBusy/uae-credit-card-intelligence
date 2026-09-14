from app.domain.enums import EligibilityResult, SpendCategory as C
from app.services.eligibility_engine import check_eligibility
from tests.conftest import make_card, make_profile


def test_7_salary_below_minimum_is_not_eligible():
    card = make_card(min_salary=20000)
    result, reasons = check_eligibility(make_profile({C.OTHER: 1000}, salary_monthly=8000), card)
    assert result is EligibilityResult.NOT_ELIGIBLE
    assert any("below" in r for r in reasons)


def test_salary_above_minimum_is_eligible():
    card = make_card(min_salary=5000)
    result, _ = check_eligibility(make_profile({C.OTHER: 1000}, salary_monthly=20000), card)
    assert result is EligibilityResult.ELIGIBLE


def test_unverified_criteria_never_become_eligible():
    card = make_card(min_salary=5000, elig_status="PARTIALLY_VERIFIED")
    result, _ = check_eligibility(make_profile({C.OTHER: 1000}, salary_monthly=20000), card)
    assert result is EligibilityResult.POTENTIALLY_ELIGIBLE


def test_missing_salary_requirement_is_not_assumed_met():
    card = make_card(min_salary=None)
    result, reasons = check_eligibility(make_profile({C.OTHER: 1000}), card)
    assert result is EligibilityResult.POTENTIALLY_ELIGIBLE
    assert any("not established" in r for r in reasons)


def test_age_below_minimum_blocks():
    card = make_card(min_salary=5000, min_age=21)
    result, _ = check_eligibility(make_profile({C.OTHER: 1000}, age=19), card)
    assert result is EligibilityResult.NOT_ELIGIBLE


def test_no_eligibility_record_is_unknown():
    card = make_card()
    card.eligibility = None
    result, _ = check_eligibility(make_profile({C.OTHER: 1000}), card)
    assert result is EligibilityResult.UNKNOWN
