"""Pydantic v2 contracts for the API and the calculation engines."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    Country,
    DataQuality,
    EligibilityResult,
    Emirate,
    RewardPreference,
    SpendCategory,
)


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: Country = Country.UAE
    emirate: Emirate

    monthly_category_spend: dict[SpendCategory, float] = Field(default_factory=dict)
    international_monthly_spend: float = 0.0
    travel_frequency_per_year: int = 0

    salary_monthly: float
    reward_preference: RewardPreference = RewardPreference.NO_PREFERENCE
    annual_fee_tolerance: float = 0.0
    pays_balance_in_full: bool = True
    age: int | None = None
    is_uae_resident: bool = True
    salary_transferred: bool = False

    current_card_id: str | None = None
    lifestyle_preferences: list[str] = Field(default_factory=list)

    @field_validator("monthly_category_spend")
    @classmethod
    def _no_negative_spend(cls, v: dict[SpendCategory, float]) -> dict[SpendCategory, float]:
        for category, amount in v.items():
            if amount < 0:
                raise ValueError(f"negative spend for {category}")
            if amount > 1_000_000:
                raise ValueError(f"implausible monthly spend for {category}")
        return v

    @field_validator("international_monthly_spend", "salary_monthly", "annual_fee_tolerance")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("value must not be negative")
        return v

    @field_validator("travel_frequency_per_year")
    @classmethod
    def _sane_travel(cls, v: int) -> int:
        if v < 0 or v > 365:
            raise ValueError("travel_frequency_per_year must be between 0 and 365")
        return v

    @model_validator(mode="after")
    def _uae_only_v1(self) -> "UserProfile":
        if self.country is not Country.UAE:
            raise ValueError("V1 supports UAE only")
        return self

    # -- helpers used by the engines -------------------------------------
    def spend(self, category: SpendCategory) -> float:
        return float(self.monthly_category_spend.get(category, 0.0))

    @property
    def total_monthly_domestic_spend(self) -> float:
        return float(sum(self.monthly_category_spend.values()))

    @property
    def total_monthly_spend(self) -> float:
        """Total card throughput: this is what min-spend thresholds measure."""
        return self.total_monthly_domestic_spend + self.international_monthly_spend


class CategoryBreakdown(BaseModel):
    category: SpendCategory
    scope: str | None = None  # INTERNATIONAL rows share the OTHER category
    monthly_spend: float
    rate_applied: float | None
    rate_source_status: str
    monthly_reward_uncapped: float
    monthly_reward_after_category_cap: float
    cap_applied: str | None = None
    note: str | None = None


class CardCalculation(BaseModel):
    card_id: str
    card_name: str
    issuer: str
    eligibility: EligibilityResult
    eligibility_reasons: list[str] = Field(default_factory=list)

    gross_annual_rewards: float
    annual_fee: float
    fx_cost: float
    net_annual_value: float

    reward_unit: str = "AED"
    data_quality: DataQuality
    data_quality_reasons: list[str] = Field(default_factory=list)
    rankable: bool = True
    exclusion_reason: str | None = None

    category_breakdown: list[CategoryBreakdown] = Field(default_factory=list)
    caps_applied: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    card_version: int = 1


class TwoCardStrategy(BaseModel):
    primary_card_id: str
    secondary_card_id: str
    primary_card_name: str
    secondary_card_name: str
    category_allocation: dict[str, str]
    combined_net_annual_value: float
    best_single_net_annual_value: float
    incremental_value: float
    recommended: bool
    reason: str


class CurrentCardComparison(BaseModel):
    current_card_id: str
    current_card_name: str
    current_estimated_value: float
    recommended_card_id: str
    recommended_estimated_value: float
    potential_additional_value: float


class RecommendationResponse(BaseModel):
    run_id: str
    request_id: str | None = None
    calculation_version: str
    card_data_version: str
    generated_at: str

    user_profile_summary: dict[str, Any]
    eligible_cards: list[str]
    top_cards: list[CardCalculation]
    conditional_cards: list[CardCalculation] = Field(default_factory=list)
    best_strategy: TwoCardStrategy | None = None
    current_card_comparison: CurrentCardComparison | None = None
    missed_value: float | None = None
    insights: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    explanation: str | None = None
    disclaimer: str


class CardSummary(BaseModel):
    card_id: str
    card_name: str
    issuer: str
    card_type: str
    status: str
    version: int
    annual_fee: float | None
    annual_fee_status: str
    modelling_limitation: str | None = None
