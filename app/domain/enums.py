"""Shared domain vocabulary.

Kept free of DB/API imports so every layer can depend on it.
"""
from __future__ import annotations

from enum import Enum


class Country(str, Enum):
    """V1 supports UAE only. The enum exists so the calculation engine never
    hard-codes 'AED' or UAE-specific assumptions inline."""

    UAE = "UAE"


class Emirate(str, Enum):
    DUBAI = "Dubai"
    ABU_DHABI = "Abu Dhabi"
    SHARJAH = "Sharjah"
    AJMAN = "Ajman"
    UMM_AL_QUWAIN = "Umm Al Quwain"
    RAS_AL_KHAIMAH = "Ras Al Khaimah"
    FUJAIRAH = "Fujairah"


class SpendCategory(str, Enum):
    """Categories the user supplies and the reward engine reasons over.

    The first ten are the required V1 set. EDUCATION and TELECOM are additions:
    real UAE cards (e.g. Emirates Islamic Cashback Plus) pay headline rates on
    exactly those categories, and folding them into OTHER would silently
    understate that card. Both are optional on the user profile.
    """

    GROCERIES = "groceries"
    DINING = "dining"
    FUEL = "fuel"
    TRAVEL = "travel"
    ONLINE = "online"
    UTILITIES = "utilities"
    GOVERNMENT = "government"
    FASHION = "fashion"
    ENTERTAINMENT = "entertainment"
    OTHER = "other"
    EDUCATION = "education"
    TELECOM = "telecom"


REQUIRED_CATEGORIES = [
    SpendCategory.GROCERIES,
    SpendCategory.DINING,
    SpendCategory.FUEL,
    SpendCategory.TRAVEL,
    SpendCategory.ONLINE,
    SpendCategory.UTILITIES,
    SpendCategory.GOVERNMENT,
    SpendCategory.FASHION,
    SpendCategory.ENTERTAINMENT,
    SpendCategory.OTHER,
]


class SpendScope(str, Enum):
    """Whether a reward rule applies to local, foreign or any currency spend."""

    DOMESTIC = "DOMESTIC"
    INTERNATIONAL = "INTERNATIONAL"
    ANY = "ANY"


class RewardType(str, Enum):
    CASHBACK_PCT = "CASHBACK_PCT"
    POINTS_PER_AED = "POINTS_PER_AED"
    MILES_PER_AED = "MILES_PER_AED"


class CapScope(str, Enum):
    """What a reward cap is measured against.

    PER_MERCHANT is deliberately represented but NOT modellable: we do not know
    how a user's category spend splits across merchants, so any card carrying a
    per-merchant cap is flagged with a modelling limitation rather than given a
    falsely precise number.
    """

    PER_CATEGORY = "PER_CATEGORY"
    CARD_TOTAL = "CARD_TOTAL"
    PER_MERCHANT = "PER_MERCHANT"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"
    DISCONTINUED = "DISCONTINUED"


class SourceTier(int, Enum):
    OFFICIAL_ISSUER_PAGE = 1
    OFFICIAL_ISSUER_DOCUMENT = 2
    OFFICIAL_PROGRAMME_PARTNER = 3
    REGULATOR = 4
    REPUTABLE_SECONDARY = 5
    DISCOVERY_ONLY = 6


class EligibilityResult(str, Enum):
    ELIGIBLE = "eligible"
    POTENTIALLY_ELIGIBLE = "potentially_eligible"
    NOT_ELIGIBLE = "not_eligible"
    UNKNOWN = "unknown"


class DataQuality(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RewardPreference(str, Enum):
    CASHBACK = "cashback"
    MILES = "miles"
    POINTS = "points"
    NO_PREFERENCE = "no_preference"


class FeeType(str, Enum):
    ANNUAL_FEE = "ANNUAL_FEE"
    FX_FEE = "FX_FEE"


# Fields without which a financial ranking would be falsely precise.
CRITICAL_FIELDS = frozenset(
    {
        "reward_rate",
        "reward_category",
        "reward_cap",
        "min_monthly_spend",
        "annual_fee",
        "fx_fee",
        "eligibility_min_salary",
        "reward_valuation",
    }
)

NON_CRITICAL_FIELDS = frozenset(
    {"lounge_access", "concierge", "insurance", "lifestyle_perks", "welcome_bonus"}
)
