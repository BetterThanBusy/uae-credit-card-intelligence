"""Research orchestrator.

Runs the flow: search → official URL discovery → targeted scrape → extraction →
field-level verification → research database. It stops at the research
database; nothing here writes to the production card tables. Promotion is a
separate, explicit step.

Cost discipline: search first, filter to official domains, scrape only what
survives the filter, and skip re-processing any document whose content hash is
unchanged since the last run.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.db.research_models import (
    ResearchCard,
    ResearchDocument,
    ResearchEvidence,
    ResearchRun,
)
from app.domain.research_enums import (
    RESEARCH_CRITICAL_FIELDS,
    ResearchStage,
    ResearchVerification,
)
from app.services.research.extraction import classify_document, get_extractor
from app.services.research.firecrawl_client import (
    FirecrawlError,
    RetrievalClient,
    get_retrieval_client,
)
from app.services.research.verification import (
    ExtractionCandidate,
    VerificationOutcome,
    is_official_source,
    resolve_field,
    verify_candidate,
)

logger = logging.getLogger(__name__)

TARGETS_FILE = Path(__file__).resolve().parents[3] / "app" / "data" / "research_targets.json"
MAX_DOCUMENTS_PER_CARD = 8


def load_targets(path: Path | None = None) -> dict:
    with open(path or TARGETS_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def find_target(slug: str, config: dict | None = None) -> dict | None:
    config = config or load_targets()
    return next((t for t in config["targets"] if t["slug"] == slug), None)


def build_queries(target: dict, config: dict | None = None) -> list[str]:
    config = config or load_targets()
    return [
        template.format(card_name=target["card_name"], domain=target["domain"])
        for template in config["query_templates"]
    ]


def research_card(
    session: Session,
    target: dict,
    client: RetrievalClient | None = None,
    extractor=None,
    config: dict | None = None,
    max_documents: int = MAX_DOCUMENTS_PER_CARD,
) -> ResearchCard:
    """Research one card end to end, up to (not including) promotion."""
    client = client or get_retrieval_client()
    extractor = extractor or get_extractor()
    config = config or load_targets()

    run = ResearchRun(
        run_id=uuid.uuid4().hex[:16],
        target_slug=target["slug"],
        issuer=target["issuer"],
        retrieval_provider=type(client).__name__,
    )
    session.add(run)
    session.flush()

    card = ResearchCard(
        run_db_id=run.id,
        research_id=uuid.uuid4().hex[:16],
        card_slug=target["slug"],
        card_name=target["card_name"],
        issuer=target["issuer"],
        card_type=target.get("card_type"),
        stage=ResearchStage.RESEARCHED.value,
        stage_history=[_stamp(ResearchStage.RESEARCHED)],
    )
    session.add(card)
    session.flush()

    # --- discovery -----------------------------------------------------
    discovered: dict[str, str] = {}
    for query in build_queries(target, config):
        run.queries_issued += 1
        try:
            results = client.search(query, limit=5)
        except FirecrawlError as exc:
            logger.warning("search failed for %s: %s", query, exc)
            continue
        for result in results:
            if result.url in discovered:
                continue
            if not is_official_source(result.url, target["issuer"]):
                continue  # secondary sites are discovery only, never scraped as authority
            discovered[result.url] = result.title
        if len(discovered) >= max_documents:
            break

    # --- retrieval + extraction ----------------------------------------
    outcomes_by_field: dict[str, list] = {}

    for url, title in list(discovered.items())[:max_documents]:
        try:
            document = client.scrape(url)
        except FirecrawlError as exc:
            logger.warning("scrape failed for %s: %s", url, exc)
            continue
        if not document.content.strip():
            continue

        previous = session.scalar(
            select(ResearchDocument)
            .where(ResearchDocument.url == url)
            .order_by(ResearchDocument.id.desc())
        )
        content_hash = document.content_hash
        unchanged = previous is not None and previous.content_hash == content_hash

        record = ResearchDocument(
            run_db_id=run.id,
            url=url,
            title=document.title or title,
            domain=document.domain,
            document_type=classify_document(document),
            is_official=True,
            content_type=document.content_type,
            content_hash=content_hash,
            content_length=len(document.content),
            retrieved_at=document.retrieved_at.replace(tzinfo=None),
            card_slug=target["slug"],
            previous_content_hash=previous.content_hash if previous else None,
            changed_since_last_run=bool(previous and not unchanged),
            needs_reverification=bool(previous and not unchanged),
            content=document.content,
        )
        session.add(record)
        session.flush()
        run.documents_retrieved += 1

        if unchanged and previous is not None:
            # Cost control: an unchanged document is not re-extracted. Its prior
            # evidence is copied onto this run's card so the result is identical
            # to a full re-extraction, minus the cost.
            run.documents_from_cache += 1
            for prior in previous.evidence:
                outcome = _outcome_from_stored(prior)
                outcomes_by_field.setdefault(_stored_field_key(prior), []).append(outcome)
                session.add(
                    ResearchEvidence(
                        card_db_id=card.id,
                        document_db_id=record.id,
                        field_name=prior.field_name,
                        category=prior.category,
                        scope=prior.scope,
                        value=prior.value,
                        unit=prior.unit,
                        source_url=prior.source_url,
                        source_type=prior.source_type,
                        source_tier=prior.source_tier,
                        is_official=prior.is_official,
                        evidence_text=prior.evidence_text,
                        effective_date=prior.effective_date,
                        verification_status=prior.verification_status,
                        rejection_reason=prior.rejection_reason,
                        is_critical=prior.is_critical,
                        notes=(prior.notes or "") + " [carried forward: document unchanged]",
                    )
                )
            log_event(logger, "document_unchanged", url=url, card=target["slug"])
            continue

        for candidate in extractor.extract(document):
            outcome = verify_candidate(candidate, document.content, target["issuer"])
            key = _field_key(candidate)
            outcomes_by_field.setdefault(key, []).append(outcome)

            session.add(
                ResearchEvidence(
                    card_db_id=card.id,
                    document_db_id=record.id,
                    field_name=candidate.field_name,
                    category=candidate.category,
                    scope=candidate.scope,
                    value=str(candidate.value) if outcome.usable else None,
                    unit=candidate.unit,
                    source_url=candidate.source_url,
                    source_type=candidate.document_type,
                    source_tier=outcome.source_tier,
                    is_official=outcome.is_official,
                    evidence_text=candidate.evidence_text,
                    effective_date=candidate.effective_date,
                    verification_status=outcome.status.value,
                    rejection_reason=(
                        outcome.rejection_reason.value if outcome.rejection_reason else None
                    ),
                    is_critical=candidate.is_critical,
                    notes=outcome.detail,
                )
            )

    # --- field resolution ----------------------------------------------
    blocking: list[str] = []
    resolved_statuses: list[str] = []
    for key, outcomes in outcomes_by_field.items():
        if not outcomes:
            continue
        resolution = resolve_field(outcomes)
        resolved_statuses.append(resolution.status.value)
        base_field = key.split("::")[0]
        if resolution.status in (
            ResearchVerification.UNKNOWN,
            ResearchVerification.CONFLICTING_SOURCES,
        ) and base_field in RESEARCH_CRITICAL_FIELDS:
            blocking.append(key)

    missing_critical = [
        f
        for f in RESEARCH_CRITICAL_FIELDS
        if not any(k.split("::")[0] == f for k in outcomes_by_field)
    ]
    blocking.extend(sorted(missing_critical))

    card.blocking_fields = sorted(set(blocking))
    card.modelable = not card.blocking_fields
    card.verification_status = _card_status(resolved_statuses, bool(card.blocking_fields))
    card.stage = ResearchStage.EXTRACTED.value
    card.stage_history = (card.stage_history or []) + [_stamp(ResearchStage.EXTRACTED)]

    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run.status = "COMPLETE"
    session.commit()

    log_event(
        logger,
        "research_card_complete",
        run_id=run.run_id,
        card=target["slug"],
        documents=run.documents_retrieved,
        blocking_fields=len(card.blocking_fields or []),
        modelable=card.modelable,
    )
    return card


def research_batch(
    session: Session,
    slugs: list[str] | None = None,
    client: RetrievalClient | None = None,
    extractor=None,
) -> list[ResearchCard]:
    """Research many cards. One bank blocking access does not stop the batch."""
    config = load_targets()
    targets = [t for t in config["targets"] if not slugs or t["slug"] in slugs]
    client = client or get_retrieval_client()
    extractor = extractor or get_extractor()

    results: list[ResearchCard] = []
    for target in targets:
        try:
            results.append(research_card(session, target, client, extractor, config))
        except Exception as exc:  # noqa: BLE001
            logger.warning("research failed for %s: %s", target["slug"], exc)
            session.rollback()
            failed = ResearchCard(
                research_id=uuid.uuid4().hex[:16],
                card_slug=target["slug"],
                card_name=target["card_name"],
                issuer=target["issuer"],
                stage=ResearchStage.REJECTED.value,
                verification_status=ResearchVerification.UNKNOWN.value,
                modelable=False,
                blocking_fields=["retrieval_failed"],
                notes=f"Retrieval failed: {exc}",
                stage_history=[_stamp(ResearchStage.REJECTED)],
            )
            session.add(failed)
            session.commit()
            results.append(failed)
    return results


def _stored_field_key(row: ResearchEvidence) -> str:
    parts = [row.field_name]
    if row.category:
        parts.append(row.category)
    if row.scope:
        parts.append(row.scope)
    return "::".join(parts)


def _outcome_from_stored(row: ResearchEvidence) -> VerificationOutcome:
    """Rebuild a verification outcome from a previously verified evidence row.

    This is not a re-verification: it replays a decision that was already made
    against the same document content hash.
    """
    candidate = ExtractionCandidate(
        field_name=row.field_name,
        value=row.value,
        source_url=row.source_url,
        evidence_text=row.evidence_text,
        unit=row.unit,
        category=row.category,
        scope=row.scope,
        document_type=row.source_type,
        effective_date=row.effective_date,
    )
    return VerificationOutcome(
        candidate,
        ResearchVerification(row.verification_status),
        is_official=row.is_official,
        source_tier=row.source_tier,
        detail="Carried forward; source document unchanged since last run.",
    )


def _field_key(candidate) -> str:
    parts = [candidate.field_name]
    if candidate.category:
        parts.append(candidate.category)
    if candidate.scope:
        parts.append(candidate.scope)
    return "::".join(parts)


def _card_status(statuses: list[str], has_blocking: bool) -> str:
    if not statuses:
        return ResearchVerification.UNKNOWN.value
    if ResearchVerification.CONFLICTING_SOURCES.value in statuses:
        return ResearchVerification.CONFLICTING_SOURCES.value
    if has_blocking:
        return ResearchVerification.PARTIALLY_VERIFIED.value
    if all(s == ResearchVerification.VERIFIED_OFFICIAL.value for s in statuses):
        return ResearchVerification.VERIFIED_OFFICIAL.value
    return ResearchVerification.PARTIALLY_VERIFIED.value


def _stamp(stage: ResearchStage) -> dict:
    return {"stage": stage.value, "at": datetime.now(timezone.utc).isoformat()}
