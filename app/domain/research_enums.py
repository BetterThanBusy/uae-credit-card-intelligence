"""Vocabulary for the research layer. Kept separate from production card enums."""
from __future__ import annotations

from enum import Enum


class DocumentType(str, Enum):
    PRODUCT_PAGE = "PRODUCT_PAGE"
    REWARDS_TERMS = "REWARDS_TERMS"
    SCHEDULE_OF_CHARGES = "SCHEDULE_OF_CHARGES"
    KEY_FACTS = "KEY_FACTS"
    CARD_TERMS = "CARD_TERMS"
    APPLICATION = "APPLICATION"
    FAQ = "FAQ"
    OTHER = "OTHER"


# Authority order. Lower number wins when two official documents disagree
# and effective dates cannot separate them.
DOCUMENT_AUTHORITY: dict[str, int] = {
    DocumentType.PRODUCT_PAGE.value: 1,
    DocumentType.REWARDS_TERMS.value: 2,
    DocumentType.SCHEDULE_OF_CHARGES.value: 3,
    DocumentType.KEY_FACTS.value: 4,
    DocumentType.CARD_TERMS.value: 5,
    DocumentType.APPLICATION.value: 6,
    DocumentType.FAQ.value: 7,
    DocumentType.OTHER.value: 8,
}


class ResearchVerification(str, Enum):
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    UNKNOWN = "UNKNOWN"
    DISCONTINUED = "DISCONTINUED"


class ResearchStage(str, Enum):
    """Strict one-way pipeline. Nothing skips a stage."""

    RESEARCHED = "RESEARCHED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    VERIFIED = "VERIFIED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


STAGE_ORDER = [
    ResearchStage.RESEARCHED,
    ResearchStage.EXTRACTED,
    ResearchStage.VALIDATED,
    ResearchStage.VERIFIED,
    ResearchStage.PROMOTED,
]


class RejectionReason(str, Enum):
    EVIDENCE_NOT_IN_DOCUMENT = "EVIDENCE_NOT_IN_DOCUMENT"
    VALUE_NOT_IN_EVIDENCE = "VALUE_NOT_IN_EVIDENCE"
    UNBOUNDED_QUALIFIER = "UNBOUNDED_QUALIFIER"
    NON_OFFICIAL_SOURCE = "NON_OFFICIAL_SOURCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    IMPLAUSIBLE_VALUE = "IMPLAUSIBLE_VALUE"


# Fields without which a precise financial ranking is dishonest.
RESEARCH_CRITICAL_FIELDS = frozenset(
    {
        "reward_rate",
        "reward_cap",
        "min_monthly_spend",
        "annual_fee",
        "annual_fee_waiver",
        "fx_fee",
        "eligibility_min_salary",
        "international_reward_rate",
    }
)

# Phrases that make a headline number an upper bound rather than a rate.
UNBOUNDED_QUALIFIERS = (
    "up to",
    "as much as",
    "as high as",
    "upto",
    "maximum of up to",
    "earn up to",
)
