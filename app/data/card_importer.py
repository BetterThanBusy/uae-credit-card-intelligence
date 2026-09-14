"""Research output -> database.

The ingestion path is deliberately a reusable function over a structured
research artifact, not hand-typed rows. A future Card Research Agent writes the
same JSON shape and this importer stays unchanged.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Card,
    CardBenefit,
    CardEligibility,
    CardFee,
    CardRewardRule,
    CardSource,
    CardVersion,
    RewardValueAssumption,
)

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "uae_cards.json"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def load_dataset(path: Path | None = None) -> dict:
    with open(path or DATA_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def content_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def import_cards(session: Session, dataset: dict | None = None) -> tuple[int, str]:
    """Import every card. Returns (count, dataset_version)."""
    dataset = dataset or load_dataset()
    count = 0

    for payload in dataset["cards"]:
        existing = session.scalar(select(Card).where(Card.card_id == payload["card_id"]))
        if existing is not None:
            session.delete(existing)
            session.flush()

        card = Card(
            card_id=payload["card_id"],
            card_name=payload["card_name"],
            issuer=payload["issuer"],
            network=payload.get("network"),
            card_type=payload.get("card_type", "cashback"),
            country=dataset.get("country", "UAE"),
            status=payload.get("status", "UNKNOWN"),
            version=payload.get("version", 1),
            effective_from=_d(payload.get("effective_from")),
            effective_to=_d(payload.get("effective_to")),
            retrieved_at=_dt(payload.get("retrieved_at")),
            last_verified=_dt(payload.get("last_verified")),
            modelling_limitation=payload.get("modelling_limitation"),
            notes=payload.get("notes"),
            extra={
                "card_level_caps": payload.get("card_level_caps"),
                "exclusions": payload.get("exclusions", []),
            },
        )

        for rule in payload.get("reward_rules", []):
            card.reward_rules.append(
                CardRewardRule(
                    category=rule["category"],
                    scope=rule.get("scope", "ANY"),
                    reward_type=rule.get("reward_type", "CASHBACK_PCT"),
                    rate=rule.get("rate"),
                    tier_min_monthly_spend=rule.get("tier_min_monthly_spend"),
                    tier_max_monthly_spend=rule.get("tier_max_monthly_spend"),
                    monthly_cap_amount=rule.get("monthly_cap_amount"),
                    annual_cap_amount=rule.get("annual_cap_amount"),
                    cap_scope=rule.get("cap_scope"),
                    min_monthly_spend_required=rule.get("min_monthly_spend_required")
                    or payload.get("min_monthly_spend_required"),
                    min_transaction_amount=rule.get("min_transaction_amount"),
                    verification_status=rule.get("verification_status", "UNKNOWN"),
                    excluded=rule.get("excluded", False),
                    notes=rule.get("notes"),
                )
            )

        for fee in payload.get("fees", []):
            card.fees.append(
                CardFee(
                    fee_type=fee["fee_type"],
                    amount=fee.get("amount"),
                    unit=fee.get("unit", "AED"),
                    first_year_free=fee.get("first_year_free", False),
                    waiver_min_annual_spend=fee.get("waiver_min_annual_spend"),
                    waiver_condition=fee.get("waiver_condition"),
                    verification_status=fee.get("verification_status", "UNKNOWN"),
                    notes=fee.get("notes"),
                )
            )

        for benefit in payload.get("benefits", []):
            card.benefits.append(
                CardBenefit(
                    benefit_type=benefit["benefit_type"],
                    description=benefit["description"],
                    verification_status=benefit.get("verification_status", "UNKNOWN"),
                )
            )

        elig = payload.get("eligibility")
        if elig:
            card.eligibility = CardEligibility(
                min_monthly_salary=elig.get("min_monthly_salary"),
                min_age=elig.get("min_age"),
                max_age=elig.get("max_age"),
                residency_required=elig.get("residency_required"),
                salary_transfer_required=elig.get("salary_transfer_required"),
                existing_relationship_required=elig.get("existing_relationship_required"),
                nationality_restriction=elig.get("nationality_restriction"),
                verification_status=elig.get("verification_status", "UNKNOWN"),
                notes=elig.get("notes"),
            )

        for source in payload.get("sources", []):
            card.sources.append(
                CardSource(
                    field_name=source["field_name"],
                    value=source.get("value"),
                    unit=source.get("unit"),
                    source_name=source["source_name"],
                    source_url=source["source_url"],
                    source_type=source.get("source_type", "unknown"),
                    source_tier=source.get("source_tier", 6),
                    retrieved_at=_dt(payload.get("retrieved_at")),
                    effective_from=_d(payload.get("effective_from")),
                    effective_to=_d(payload.get("effective_to")),
                    verification_status=source.get("verification_status", "UNKNOWN"),
                    evidence=source.get("evidence"),
                    notes=source.get("notes"),
                )
            )

        card.versions.append(
            CardVersion(
                version=card.version,
                snapshot=payload,
                content_hash=content_hash(payload),
            )
        )

        session.add(card)
        count += 1

    for assumption in dataset.get("reward_value_assumptions", []):
        session.add(
            RewardValueAssumption(
                reward_program=assumption["reward_program"],
                currency=assumption["currency"],
                scenario=assumption.get("scenario", "standard"),
                assumed_value_aed=assumption.get("assumed_value_aed"),
                source=assumption.get("source"),
                last_verified=_dt(assumption.get("last_verified")),
                verification_status=assumption.get("verification_status", "UNKNOWN"),
            )
        )

    session.commit()
    return count, dataset["dataset_version"]
