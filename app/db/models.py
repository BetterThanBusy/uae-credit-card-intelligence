"""Relational model.

Design rule: anything the calculation engine reads is a typed relational column.
JSON is used only for descriptive metadata the engine never computes on.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    card_name: Mapped[str] = mapped_column(String(160))
    issuer: Mapped[str] = mapped_column(String(120), index=True)
    network: Mapped[str | None] = mapped_column(String(40))
    card_type: Mapped[str] = mapped_column(String(40))  # cashback / miles / points
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    country: Mapped[str] = mapped_column(String(8), default="UAE", index=True)

    status: Mapped[str] = mapped_column(String(32), default="VERIFIED")
    version: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime)

    # Set when the card's published terms cannot be modelled without inventing
    # an assumption (e.g. a per-merchant cap). Such cards are never given a
    # precise ranking position.
    modelling_limitation: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSON)

    reward_rules: Mapped[list["CardRewardRule"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )
    fees: Mapped[list["CardFee"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )
    benefits: Mapped[list["CardBenefit"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )
    eligibility: Mapped["CardEligibility | None"] = relationship(
        back_populates="card", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    sources: Mapped[list["CardSource"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )
    versions: Mapped[list["CardVersion"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )


class CardRewardRule(Base):
    """One row per (category, scope, spend tier).

    Tiering is expressed by tier_min_monthly_spend / tier_max_monthly_spend on
    TOTAL monthly card spend; the engine picks exactly one tier per card.
    """

    __tablename__ = "card_reward_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)

    category: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="ANY")
    reward_type: Mapped[str] = mapped_column(String(24), default="CASHBACK_PCT")
    rate: Mapped[float | None] = mapped_column(Float)  # 0.05 == 5% ; or points per AED

    tier_min_monthly_spend: Mapped[float | None] = mapped_column(Float)
    tier_max_monthly_spend: Mapped[float | None] = mapped_column(Float)

    monthly_cap_amount: Mapped[float | None] = mapped_column(Float)
    annual_cap_amount: Mapped[float | None] = mapped_column(Float)
    cap_scope: Mapped[str | None] = mapped_column(String(24))

    min_monthly_spend_required: Mapped[float | None] = mapped_column(Float)
    min_transaction_amount: Mapped[float | None] = mapped_column(Float)

    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    card: Mapped[Card] = relationship(back_populates="reward_rules")


class CardFee(Base):
    __tablename__ = "card_fees"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)

    fee_type: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16), default="AED")  # AED | PERCENT
    first_year_free: Mapped[bool] = mapped_column(Boolean, default=False)
    waiver_min_annual_spend: Mapped[float | None] = mapped_column(Float)
    waiver_condition: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    notes: Mapped[str | None] = mapped_column(Text)

    card: Mapped[Card] = relationship(back_populates="fees")


class CardBenefit(Base):
    """Non-financial perks. Never assigned a monetary value."""

    __tablename__ = "card_benefits"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    benefit_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")

    card: Mapped[Card] = relationship(back_populates="benefits")


class CardEligibility(Base):
    __tablename__ = "card_eligibility"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), unique=True)

    min_monthly_salary: Mapped[float | None] = mapped_column(Float)
    min_age: Mapped[int | None] = mapped_column(Integer)
    max_age: Mapped[int | None] = mapped_column(Integer)
    residency_required: Mapped[bool | None] = mapped_column(Boolean)
    salary_transfer_required: Mapped[bool | None] = mapped_column(Boolean)
    existing_relationship_required: Mapped[bool | None] = mapped_column(Boolean)
    nationality_restriction: Mapped[str | None] = mapped_column(String(120))
    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    notes: Mapped[str | None] = mapped_column(Text)

    card: Mapped[Card] = relationship(back_populates="eligibility")


class CardSource(Base):
    """Field-level provenance. One row per financial field per source."""

    __tablename__ = "card_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)

    field_name: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(24))
    source_name: Mapped[str] = mapped_column(String(160))
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    source_tier: Mapped[int] = mapped_column(Integer, default=6)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    evidence: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    card: Mapped[Card] = relationship(back_populates="sources")


class CardVersion(Base):
    """Immutable snapshot so a past recommendation stays reproducible."""

    __tablename__ = "card_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    card: Mapped[Card] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("card_id", "version", name="uq_card_version"),)


class RewardValueAssumption(Base):
    """How a non-cash reward currency is valued, and on whose authority."""

    __tablename__ = "reward_value_assumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    reward_program: Mapped[str] = mapped_column(String(120), index=True)
    currency: Mapped[str] = mapped_column(String(40))
    scenario: Mapped[str] = mapped_column(String(24), default="standard")
    assumed_value_aed: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(Text)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime)
    verification_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_ref: Mapped[str | None] = mapped_column(String(80), index=True)
    country: Mapped[str] = mapped_column(String(8), default="UAE")
    emirate: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    calculation_version: Mapped[str] = mapped_column(String(24))
    card_data_version: Mapped[str] = mapped_column(String(64))
    user_profile: Mapped[dict] = mapped_column(JSON)
    data_quality: Mapped[str | None] = mapped_column(String(16))

    results: Mapped[list["RecommendationResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class RecommendationResult(Base):
    __tablename__ = "recommendation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_db_id: Mapped[int] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    card_id: Mapped[str] = mapped_column(String(80))
    rank: Mapped[int | None] = mapped_column(Integer)
    eligibility: Mapped[str] = mapped_column(String(32))
    gross_rewards: Mapped[float | None] = mapped_column(Float)
    annual_fee: Mapped[float | None] = mapped_column(Float)
    fx_cost: Mapped[float | None] = mapped_column(Float)
    net_value: Mapped[float | None] = mapped_column(Float)
    data_quality: Mapped[str] = mapped_column(String(16))
    breakdown: Mapped[dict] = mapped_column(JSON)

    run: Mapped[RecommendationRun] = relationship(back_populates="results")
