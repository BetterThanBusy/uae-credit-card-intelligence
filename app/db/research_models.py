"""Research-side tables.

These sit beside the production card tables and never replace them. The
production `cards` table remains the single source of truth for the
calculation engine; research data only enters it through promotion.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base, utcnow


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    target_slug: Mapped[str | None] = mapped_column(String(120), index=True)
    issuer: Mapped[str | None] = mapped_column(String(120))
    retrieval_provider: Mapped[str] = mapped_column(String(40), default="firecrawl")
    queries_issued: Mapped[int] = mapped_column(Integer, default=0)
    documents_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    documents_from_cache: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[dict | None] = mapped_column(JSON)

    documents: Mapped[list["ResearchDocument"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    cards: Mapped[list["ResearchCard"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class ResearchDocument(Base):
    """One retrieved official document, hashed so changes are detectable."""

    __tablename__ = "research_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_db_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(160), index=True)
    document_type: Mapped[str] = mapped_column(String(40), index=True)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    content_type: Mapped[str] = mapped_column(String(16), default="html")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_length: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    effective_date: Mapped[date | None] = mapped_column(Date)
    card_slug: Mapped[str | None] = mapped_column(String(120), index=True)
    previous_content_hash: Mapped[str | None] = mapped_column(String(64))
    changed_since_last_run: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_reverification: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str | None] = mapped_column(Text)

    run: Mapped[ResearchRun | None] = relationship(back_populates="documents")
    evidence: Mapped[list["ResearchEvidence"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )


class ResearchCard(Base):
    """A card under research, at some stage of the promotion pipeline."""

    __tablename__ = "research_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_db_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    research_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    card_slug: Mapped[str] = mapped_column(String(120), index=True)
    card_name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(120))
    card_type: Mapped[str | None] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(24), default="RESEARCHED", index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    modelable: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking_fields: Mapped[list | None] = mapped_column(JSON)
    stage_history: Mapped[list | None] = mapped_column(JSON)
    promoted_card_id: Mapped[str | None] = mapped_column(String(80))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)

    run: Mapped[ResearchRun | None] = relationship(back_populates="cards")
    evidence: Mapped[list["ResearchEvidence"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )


class ResearchEvidence(Base):
    """One extracted field with the text that supports it.

    A row only reaches VERIFIED_OFFICIAL if deterministic checks confirmed the
    evidence text appears in the retrieved document and the value appears in
    the evidence text.
    """

    __tablename__ = "research_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_db_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_cards.id", ondelete="CASCADE"), index=True
    )
    document_db_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), index=True
    )

    field_name: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str | None] = mapped_column(String(40))
    scope: Mapped[str | None] = mapped_column(String(20))
    value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))

    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40))
    source_tier: Mapped[int] = mapped_column(Integer, default=6)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    effective_date: Mapped[date | None] = mapped_column(Date)

    verification_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    rejection_reason: Mapped[str | None] = mapped_column(String(48))
    conflicts_with: Mapped[list | None] = mapped_column(JSON)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    card: Mapped[ResearchCard | None] = relationship(back_populates="evidence")
    document: Mapped[ResearchDocument | None] = relationship(back_populates="evidence")
