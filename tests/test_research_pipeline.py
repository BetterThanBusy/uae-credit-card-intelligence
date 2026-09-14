"""Orchestrator, change detection and promotion. No network access."""
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Card
from app.db.research_models import ResearchCard, ResearchDocument
from app.domain.research_enums import ResearchStage, ResearchVerification
from app.services.research.extraction import classify_document, find_effective_date
from app.services.research.firecrawl_client import ReplayRetrievalClient, RetrievedDocument
from app.services.research.orchestrator import build_queries, load_targets, research_card
from app.services.research.promotion import (
    PromotionError,
    can_advance,
    promote_research_card,
    run_promotion_pipeline,
    validate_research_card,
    verify_research_card,
)
from app.services.research.verification import ExtractionCandidate

PRODUCT_PAGE = (
    "ADCB 365 Cashback Credit Card. "
    "3% cashback on Groceries & Supermarkets spends. "
    "A minimum retail spend of AED 5,000 per calendar month is required to earn cashback. "
    "You can earn a maximum monthly cashback reward of AED 1000. "
    "An annual fee of AED 383.25 including VAT applies from the second year. "
    "You need to have a minimum salary of AED 5000. "
    "A foreign transaction fee of 2.99 percent applies to non-AED spends. "
    "Annual fee waiver of AED 30000 annual spend applies. "
    "International reward rate of 1 percent applies on international retail spends."
)

URL = "https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card"

TARGET = {
    "slug": "adcb-365-cashback",
    "card_name": "ADCB 365 Cashback Credit Card",
    "issuer": "ADCB",
    "domain": "adcb.com",
    "card_type": "cashback",
}


class StubExtractor:
    """Stands in for the LLM. Proposals still pass the real verification gate."""

    def __init__(self, rows):
        self.rows = rows

    def extract(self, document, document_type=None):
        return [
            ExtractionCandidate(
                field_name=row["field_name"],
                value=row["value"],
                unit=row.get("unit"),
                category=row.get("category"),
                scope=row.get("scope"),
                source_url=document.url,
                evidence_text=row["evidence_text"],
                document_type="PRODUCT_PAGE",
                effective_date=row.get("effective_date"),
            )
            for row in self.rows
        ]


GOOD_ROWS = [
    {"field_name": "reward_rate", "category": "groceries", "value": "0.03",
     "evidence_text": "3% cashback on Groceries & Supermarkets spends."},
    {"field_name": "reward_cap", "value": "1000", "unit": "AED per month",
     "evidence_text": "You can earn a maximum monthly cashback reward of AED 1000."},
    {"field_name": "min_monthly_spend", "value": "5000",
     "evidence_text": "A minimum retail spend of AED 5,000 per calendar month is required to earn cashback."},
    {"field_name": "annual_fee", "value": "383.25",
     "evidence_text": "An annual fee of AED 383.25 including VAT applies from the second year."},
    {"field_name": "annual_fee_waiver", "value": "30000",
     "evidence_text": "Annual fee waiver of AED 30000 annual spend applies."},
    {"field_name": "eligibility_min_salary", "value": "5000",
     "evidence_text": "You need to have a minimum salary of AED 5000."},
    {"field_name": "fx_fee", "value": "2.99", "unit": "PERCENT",
     "evidence_text": "A foreign transaction fee of 2.99 percent applies to non-AED spends."},
    {"field_name": "international_reward_rate", "value": "0.01",
     "evidence_text": "International reward rate of 1 percent applies on international retail spends."},
]


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


def make_client(content=PRODUCT_PAGE, url=URL):
    return ReplayRetrievalClient({url: RetrievedDocument(url=url, content=content, title="ADCB 365")})


# --- targets and queries -------------------------------------------------
def test_targets_config_is_not_limited_to_the_original_four():
    slugs = {t["slug"] for t in load_targets()["targets"]}
    assert len(slugs) >= 10
    assert {"adcb-365-cashback", "hsbc-cashback", "fab-cashback"} <= slugs


def test_queries_are_domain_restricted():
    queries = build_queries(TARGET)
    assert all("site:adcb.com" in q for q in queries)
    assert any("Schedule of Charges" in q for q in queries)
    assert any("foreign transaction" in q for q in queries)


# --- document handling ---------------------------------------------------
def test_document_type_classification():
    doc = RetrievedDocument(url="https://www.adcb.com/sof.pdf", content="x",
                            title="Schedule of Charges")
    assert classify_document(doc) == "SCHEDULE_OF_CHARGES"


def test_effective_date_is_extracted_when_stated():
    assert find_effective_date("These charges are effective from 1 August 2026.") == date(2026, 8, 1)
    assert find_effective_date("No date here at all.") is None


def test_documents_are_stored_with_content_hash(session):
    research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    document = session.scalars(select(ResearchDocument)).first()
    assert len(document.content_hash) == 64
    assert document.is_official


def test_unchanged_document_is_flagged_and_not_reprocessed(session):
    client, extractor = make_client(), StubExtractor(GOOD_ROWS)
    research_card(session, TARGET, client, extractor)
    research_card(session, TARGET, client, extractor)
    documents = list(session.scalars(select(ResearchDocument).order_by(ResearchDocument.id)))
    assert documents[-1].changed_since_last_run is False
    assert documents[-1].previous_content_hash == documents[0].content_hash


def test_changed_document_is_flagged_for_reverification(session):
    research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    changed = PRODUCT_PAGE.replace("383.25", "420.00")
    research_card(session, TARGET, make_client(changed), StubExtractor(GOOD_ROWS))
    latest = list(session.scalars(select(ResearchDocument).order_by(ResearchDocument.id)))[-1]
    assert latest.changed_since_last_run is True
    assert latest.needs_reverification is True


def test_secondary_domains_are_never_scraped_as_authority(session):
    secondary = "https://kredit.ae/credit-cards/adcb-365"
    client = ReplayRetrievalClient(
        {secondary: RetrievedDocument(url=secondary, content=PRODUCT_PAGE)}
    )
    card = research_card(session, TARGET, client, StubExtractor(GOOD_ROWS))
    assert session.scalars(select(ResearchDocument)).all() == []
    assert card.modelable is False


# --- extraction to research database -------------------------------------
def test_research_card_reaches_extracted_with_evidence(session):
    card = research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    assert card.stage == ResearchStage.EXTRACTED.value
    assert card.evidence
    assert card.modelable is True
    assert card.blocking_fields == []


def test_fabricated_evidence_never_reaches_the_research_database_as_a_value(session):
    rows = GOOD_ROWS + [
        {"field_name": "reward_rate", "category": "dining", "value": "0.25",
         "evidence_text": "25% cashback on dining, guaranteed for life."}
    ]
    card = research_card(session, TARGET, make_client(), StubExtractor(rows))
    dining = [e for e in card.evidence if e.category == "dining"]
    assert dining and dining[0].value is None
    assert dining[0].rejection_reason == "EVIDENCE_NOT_IN_DOCUMENT"


def test_missing_critical_field_blocks_modelability(session):
    rows = [r for r in GOOD_ROWS if r["field_name"] != "fx_fee"]
    card = research_card(session, TARGET, make_client(), StubExtractor(rows))
    assert card.modelable is False
    assert "fx_fee" in card.blocking_fields


# --- promotion -----------------------------------------------------------
def test_stage_machine_allows_only_single_forward_steps():
    assert can_advance("RESEARCHED", ResearchStage.EXTRACTED)
    assert not can_advance("RESEARCHED", ResearchStage.PROMOTED)
    assert not can_advance("REJECTED", ResearchStage.VALIDATED)


def test_promotion_refuses_a_card_that_is_not_verified(session):
    card = research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    with pytest.raises(PromotionError):
        promote_research_card(session, card)


def test_card_with_blocking_fields_is_held_at_validated(session):
    rows = [r for r in GOOD_ROWS if r["field_name"] != "reward_cap"]
    card = research_card(session, TARGET, make_client(), StubExtractor(rows))
    validate_research_card(session, card)
    verify_research_card(session, card)
    assert card.stage == ResearchStage.VALIDATED.value
    assert "reward_cap" in card.blocking_fields
    with pytest.raises(PromotionError):
        promote_research_card(session, card)


def test_full_research_to_production_flow(session):
    card = research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    run_promotion_pipeline(session, card)
    assert card.stage == ResearchStage.PROMOTED.value
    production = session.scalar(select(Card).where(Card.card_id == "adcb-365-cashback"))
    assert production is not None
    assert production.sources
    assert all(s.source_url.startswith("https://www.adcb.com") for s in production.sources)
    assert all(s.evidence for s in production.sources)


def test_promotion_increments_version_without_destroying_history(session):
    card = research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    run_promotion_pipeline(session, card)
    second = research_card(session, TARGET, make_client(), StubExtractor(GOOD_ROWS))
    run_promotion_pipeline(session, second)
    production = session.scalar(select(Card).where(Card.card_id == "adcb-365-cashback"))
    assert production.version == 2
    assert len(production.versions) == 2


def test_batch_survives_one_bank_blocking_access(session):
    from app.services.research.orchestrator import research_batch

    cards = research_batch(
        session, ["adcb-365-cashback", "hsbc-cashback"], make_client(), StubExtractor(GOOD_ROWS)
    )
    assert len(cards) == 2
    by_slug = {c.card_slug: c for c in cards}
    assert by_slug["adcb-365-cashback"].modelable is True
    assert by_slug["hsbc-cashback"].modelable is False
