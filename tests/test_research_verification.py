from datetime import date

import pytest

from app.domain.research_enums import RejectionReason, ResearchVerification
from app.services.research.verification import (
    ExtractionCandidate,
    VerificationOutcome,
    is_official_source,
    resolve_field,
    verify_candidate,
)

DOCUMENT = (
    "Earn cashback every month with the 365 Cashback Credit Card from ADCB. "
    "3% cashback on Groceries & Supermarkets spends. "
    "6% cashback on Dining spends including the online orders. "
    "You can earn a maximum monthly cashback reward of AED1,000. "
    "An annual fee of AED 383.25 (including VAT) will be applicable from the second year. "
    "Up to 10% cashback on travel, dining and groceries."
)


def candidate(**kwargs):
    base = dict(
        field_name="reward_rate",
        value="0.03",
        source_url="https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card",
        evidence_text="3% cashback on Groceries & Supermarkets spends.",
        category="groceries",
        document_type="PRODUCT_PAGE",
    )
    base.update(kwargs)
    return ExtractionCandidate(**base)


def test_official_evidence_that_supports_the_value_verifies():
    outcome = verify_candidate(candidate(), DOCUMENT, "ADCB")
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL
    assert outcome.is_official


def test_percentage_in_text_supports_decimal_value():
    outcome = verify_candidate(candidate(value="0.06", evidence_text="6% cashback on Dining spends including the online orders."), DOCUMENT, "ADCB")
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL


def test_invented_evidence_is_rejected():
    """The single most important test: a quote not in the document is discarded."""
    outcome = verify_candidate(
        candidate(evidence_text="12% cashback on absolutely everything, forever."),
        DOCUMENT,
        "ADCB",
    )
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.EVIDENCE_NOT_IN_DOCUMENT


def test_value_not_present_in_its_own_evidence_is_rejected():
    outcome = verify_candidate(candidate(value="0.09"), DOCUMENT, "ADCB")
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.VALUE_NOT_IN_EVIDENCE


def test_up_to_phrasing_does_not_become_a_rate():
    outcome = verify_candidate(
        candidate(
            value="0.10",
            category="travel",
            evidence_text="Up to 10% cashback on travel, dining and groceries.",
        ),
        DOCUMENT,
        "ADCB",
    )
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.UNBOUNDED_QUALIFIER


def test_up_to_phrasing_is_accepted_once_conditions_are_established():
    outcome = verify_candidate(
        candidate(
            value="0.10",
            category="travel",
            evidence_text="Up to 10% cashback on travel, dining and groceries.",
            conditions_verified=True,
        ),
        DOCUMENT,
        "ADCB",
    )
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL


def test_a_real_cap_is_not_confused_with_an_upper_bound():
    """'maximum monthly cashback of AED 1,000' is a genuine cap, not 'up to'."""
    outcome = verify_candidate(
        candidate(
            field_name="reward_cap",
            value="1000",
            unit="AED per month",
            evidence_text="You can earn a maximum monthly cashback reward of AED1,000.",
        ),
        DOCUMENT,
        "ADCB",
    )
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL


def test_missing_evidence_is_unknown_not_accepted():
    outcome = verify_candidate(candidate(evidence_text=None), DOCUMENT, "ADCB")
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.MISSING_EVIDENCE


def test_secondary_source_can_never_be_verified_official():
    outcome = verify_candidate(
        candidate(source_url="https://kredit.ae/credit-cards/adcb-365"), DOCUMENT, "ADCB"
    )
    assert outcome.status is ResearchVerification.PARTIALLY_VERIFIED
    assert outcome.rejection_reason is RejectionReason.NON_OFFICIAL_SOURCE
    assert not outcome.is_official


def test_official_domain_matching_handles_subdomains():
    assert is_official_source("https://www.adcb.com/x", "ADCB")
    assert is_official_source("https://offers.adcb.com/x", "ADCB")
    assert not is_official_source("https://adcb.com.fake.net/x", "ADCB")
    assert not is_official_source("https://www.adcb.com/x", "Mashreq")


def test_implausible_rate_is_rejected():
    doc = "Cashback rate of 500 applies."
    outcome = verify_candidate(
        candidate(value="500", evidence_text="Cashback rate of 500 applies."), doc, "ADCB"
    )
    assert outcome.rejection_reason is RejectionReason.IMPLAUSIBLE_VALUE


def test_explicit_unknown_value_stays_unknown():
    outcome = verify_candidate(candidate(value="UNKNOWN"), DOCUMENT, "ADCB")
    assert outcome.status is ResearchVerification.UNKNOWN


# --- conflict resolution -------------------------------------------------
def _outcome(value, official=True, tier=1, effective=None, status=None):
    cand = candidate(field_name="annual_fee", value=value, effective_date=effective)
    return VerificationOutcome(
        cand,
        status or (ResearchVerification.VERIFIED_OFFICIAL if official else ResearchVerification.PARTIALLY_VERIFIED),
        is_official=official,
        source_tier=tier,
    )


def test_agreeing_sources_resolve_cleanly():
    resolution = resolve_field([_outcome("383.25"), _outcome("383.25", tier=3)])
    assert resolution.status is ResearchVerification.VERIFIED_OFFICIAL
    assert resolution.outcome.candidate.value == "383.25"


def test_newer_effective_date_supersedes_older_and_records_the_conflict():
    older = _outcome("300", effective=date(2024, 1, 1))
    newer = _outcome("383.25", effective=date(2026, 1, 1))
    resolution = resolve_field([older, newer])
    assert resolution.outcome.candidate.value == "383.25"
    assert resolution.conflicts == [older]
    assert "Superseded" in resolution.detail


def test_undated_disagreement_is_flagged_conflicting_not_silently_picked():
    resolution = resolve_field([_outcome("299"), _outcome("313.95", tier=2)])
    assert resolution.status is ResearchVerification.CONFLICTING_SOURCES
    assert resolution.conflicts


def test_official_source_beats_secondary_when_both_exist():
    resolution = resolve_field([_outcome("950", official=False, tier=5), _outcome("997.50")])
    assert resolution.outcome.candidate.value == "997.50"


def test_no_usable_outcomes_resolves_to_unknown():
    rejected = VerificationOutcome(
        candidate(field_name="fx_fee"), ResearchVerification.UNKNOWN
    )
    assert resolve_field([rejected]).status is ResearchVerification.UNKNOWN


# --- card-relevance gate -------------------------------------------------
# Regression for a live failure: a basic current-account page on an official
# bank domain supplied fx_fee=3 AND fx_fee=20, both stamped VERIFIED_OFFICIAL,
# and both attributed to a credit card.
CURRENT_ACCOUNT_PAGE = (
    "HSBC Basic Current Account. A foreign transaction fee of 3 percent applies. "
    "Withdrawals abroad are charged 20 AED per transaction. "
    "A minimum monthly spend of 1 transaction keeps the account active."
)

CARD_PAGE = (
    "HSBC Live+ Credit Card. Earn 6% cashback on dining. "
    "A foreign transaction fee of 3 percent applies to non-AED spends."
)

SCHEDULE_OF_CHARGES = (
    "HSBC UAE Schedule of Services and Tariffs. Credit card cardholder charges. "
    "A foreign transaction fee of 3 percent applies to non-AED spends."
)


def fx_candidate(url, evidence, doc_type="PRODUCT_PAGE"):
    return ExtractionCandidate(
        field_name="fx_fee",
        value="3",
        unit="PERCENT",
        source_url=url,
        evidence_text=evidence,
        document_type=doc_type,
    )


def test_unrelated_product_page_cannot_supply_card_evidence():
    outcome = verify_candidate(
        fx_candidate(
            "https://www.hsbc.ae/current-accounts/products/basic/",
            "A foreign transaction fee of 3 percent applies.",
        ),
        CURRENT_ACCOUNT_PAGE,
        "HSBC UAE",
        "HSBC Live+ Credit Card",
    )
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.DOCUMENT_NOT_ABOUT_CARD


def test_page_naming_the_card_is_verified_official():
    outcome = verify_candidate(
        fx_candidate(
            "https://www.hsbc.ae/credit-cards/products/live-plus/",
            "A foreign transaction fee of 3 percent applies to non-AED spends.",
        ),
        CARD_PAGE,
        "HSBC UAE",
        "HSBC Live+ Credit Card",
    )
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL


def test_issuer_wide_schedule_is_accepted_but_only_partially():
    """A schedule of charges covers the portfolio, so it cannot be claimed as
    confirmed for one specific card."""
    outcome = verify_candidate(
        fx_candidate(
            "https://www.hsbc.ae/docs/tariffs.pdf",
            "A foreign transaction fee of 3 percent applies to non-AED spends.",
            doc_type="SCHEDULE_OF_CHARGES",
        ),
        SCHEDULE_OF_CHARGES,
        "HSBC UAE",
        "HSBC Live+ Credit Card",
    )
    assert outcome.status is ResearchVerification.PARTIALLY_VERIFIED
    assert outcome.is_official
    assert "not confirmed for this specific product" in outcome.detail


def test_card_name_variants_match_plus_and_spacing():
    from app.services.research.verification import document_scope

    assert document_scope(
        "The Live Plus card offers cashback.", "HSBC Live+ Credit Card", "PRODUCT_PAGE",
        evidence_text="offers cashback.",
    ) == "CARD"
    assert document_scope(
        "Our Live+ card offers cashback.", "HSBC Live+ Credit Card", "PRODUCT_PAGE",
        evidence_text="offers cashback.",
    ) == "CARD"
    assert document_scope("A savings account page.", "HSBC Live+ Credit Card", "PRODUCT_PAGE") == "NONE"


def test_generic_words_alone_do_not_count_as_a_card_match():
    from app.services.research.verification import document_scope

    scope = document_scope(
        "This page mentions credit and card and fees.", "ADCB 365 Cashback Credit Card", "PRODUCT_PAGE"
    )
    assert scope == "NONE"


def test_gate_is_optional_so_existing_callers_are_unaffected():
    outcome = verify_candidate(
        fx_candidate("https://www.hsbc.ae/anything", "A foreign transaction fee of 3 percent applies."),
        CURRENT_ACCOUNT_PAGE,
        "HSBC UAE",
    )
    assert outcome.status is ResearchVerification.VERIFIED_OFFICIAL


# --- navigation menus must not qualify a page ----------------------------
# Regression for a live failure: HSBC's global nav links to Live+ on every
# page, so a basic current-account page "mentioned" the card and its fees were
# accepted as the card's own.
NAV = "Home. Credit cards: HSBC Live+ Credit Card. Loans. Savings. "
ACCOUNT_BODY = (
    "HSBC Basic Current Account. " + ("Account information. " * 200)
    + "A foreign transaction fee of 3 percent applies to this account."
)


def test_navigation_mention_does_not_make_a_page_about_the_card():
    from app.services.research.verification import document_scope

    scope = document_scope(
        NAV + ACCOUNT_BODY,
        "HSBC Live+ Credit Card",
        "PRODUCT_PAGE",
        source_url="https://www.hsbc.ae/current-accounts/products/basic/",
        evidence_text="A foreign transaction fee of 3 percent applies to this account.",
    )
    assert scope == "NONE"


def test_current_account_fee_is_refused_end_to_end():
    outcome = verify_candidate(
        ExtractionCandidate(
            field_name="fx_fee",
            value="3",
            unit="PERCENT",
            source_url="https://www.hsbc.ae/current-accounts/products/basic/",
            evidence_text="A foreign transaction fee of 3 percent applies to this account.",
            document_type="PRODUCT_PAGE",
        ),
        NAV + ACCOUNT_BODY,
        "HSBC UAE",
        "HSBC Live+ Credit Card",
    )
    assert outcome.status is ResearchVerification.UNKNOWN
    assert outcome.rejection_reason is RejectionReason.DOCUMENT_NOT_ABOUT_CARD


def test_url_naming_the_card_is_sufficient():
    from app.services.research.verification import document_scope

    scope = document_scope(
        "Fees and charges apply. A foreign transaction fee of 3 percent applies.",
        "HSBC Live+ Credit Card",
        "PRODUCT_PAGE",
        source_url="https://www.hsbc.ae/credit-cards/products/live-plus/",
        evidence_text="A foreign transaction fee of 3 percent applies.",
    )
    assert scope == "CARD"


def test_evidence_next_to_a_card_mention_qualifies():
    from app.services.research.verification import document_scope

    body = "HSBC Live+ Credit Card. Earn 6% on dining. A foreign transaction fee of 3 percent applies."
    scope = document_scope(
        body,
        "HSBC Live+ Credit Card",
        "PRODUCT_PAGE",
        source_url="https://www.hsbc.ae/some/other/path/",
        evidence_text="A foreign transaction fee of 3 percent applies.",
    )
    assert scope == "CARD"


def test_repeated_mentions_qualify_when_no_evidence_is_supplied():
    from app.services.research.verification import document_scope

    body = "HSBC Live+ Credit Card. " * 4 + "Cashback details."
    assert document_scope(body, "HSBC Live+ Credit Card", "PRODUCT_PAGE") == "CARD"
    assert document_scope(NAV + "Savings only.", "HSBC Live+ Credit Card", "PRODUCT_PAGE") == "NONE"
