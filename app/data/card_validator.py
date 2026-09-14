"""Card data validation. Detects what would make a ranking untrustworthy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.core.config import get_settings
from app.domain.enums import CRITICAL_FIELDS, FeeType, VerificationStatus


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for item in self.errors:
            lines.append(f"  ERROR   {item}")
        for item in self.warnings:
            lines.append(f"  WARN    {item}")
        for item in self.info:
            lines.append(f"  INFO    {item}")
        return "\n".join(lines)


def _valid_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_dataset(dataset: dict) -> ValidationReport:
    report = ValidationReport()
    settings = get_settings()
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc)

    for card in dataset.get("cards", []):
        cid = card.get("card_id", "<missing id>")
        name = card.get("card_name", cid)

        if not cid or cid == "<missing id>":
            report.errors.append("A card has no card_id.")
        if cid in seen_ids:
            report.errors.append(f"Duplicate card_id: {cid}")
        seen_ids.add(cid)

        if card.get("status") == VerificationStatus.DISCONTINUED.value:
            report.warnings.append(f"{name}: marked DISCONTINUED.")

        # --- staleness / expiry ---------------------------------------
        retrieved = card.get("retrieved_at")
        if not retrieved:
            report.errors.append(f"{name}: no retrieved_at timestamp.")
        else:
            age = (now - datetime.fromisoformat(retrieved.replace("Z", "+00:00"))).days
            if age > settings.staleness_days:
                report.warnings.append(f"{name}: card data is {age} days old (STALE).")
        if card.get("effective_to"):
            report.warnings.append(f"{name}: has an effective_to date; check it has not expired.")

        # --- reward rules ----------------------------------------------
        rules = card.get("reward_rules", [])
        if not rules:
            report.errors.append(f"{name}: no reward rules — cannot be used in any calculation.")
        categories = set()
        for rule in rules:
            categories.add(rule.get("category"))
            rate = rule.get("rate")
            if rate is not None:
                if rate < 0:
                    report.errors.append(f"{name}: negative reward rate for {rule.get('category')}.")
                if rule.get("reward_type", "CASHBACK_PCT") == "CASHBACK_PCT" and rate > 1:
                    report.errors.append(
                        f"{name}: cashback rate {rate} for {rule.get('category')} exceeds 100% — "
                        "rates must be stored as decimals."
                    )
            for cap_field in ("monthly_cap_amount", "annual_cap_amount"):
                cap = rule.get(cap_field)
                if cap is not None and cap < 0:
                    report.errors.append(f"{name}: negative {cap_field}.")
            lo, hi = rule.get("tier_min_monthly_spend"), rule.get("tier_max_monthly_spend")
            if lo is not None and hi is not None and lo > hi:
                report.errors.append(f"{name}: tier range inverted for {rule.get('category')}.")
            if rule.get("monthly_cap_amount") and rule.get("annual_cap_amount"):
                if rule["monthly_cap_amount"] * 12 > rule["annual_cap_amount"]:
                    report.warnings.append(
                        f"{name}: monthly cap x12 exceeds the annual cap for {rule.get('category')}."
                    )
            if rule.get("verification_status") == VerificationStatus.UNKNOWN.value:
                report.warnings.append(
                    f"{name}: reward rule for {rule.get('category')} is UNKNOWN — "
                    "the card cannot be ranked precisely."
                )
        if not categories:
            report.errors.append(f"{name}: no reward categories recorded.")

        # --- fees -------------------------------------------------------
        fee_types = {f.get("fee_type") for f in card.get("fees", [])}
        if FeeType.ANNUAL_FEE.value not in fee_types:
            report.errors.append(f"{name}: no annual fee record.")
        for fee in card.get("fees", []):
            if fee.get("amount") is not None and fee["amount"] < 0:
                report.errors.append(f"{name}: negative {fee.get('fee_type')}.")
            if fee.get("verification_status") == VerificationStatus.CONFLICTING.value:
                report.warnings.append(
                    f"{name}: {fee.get('fee_type')} has CONFLICTING sources — {fee.get('notes', '')}"
                )
        if FeeType.FX_FEE.value not in fee_types:
            report.warnings.append(f"{name}: no FX fee record.")

        # --- eligibility ------------------------------------------------
        if not card.get("eligibility"):
            report.warnings.append(f"{name}: no eligibility information.")

        # --- provenance --------------------------------------------------
        sources = card.get("sources", [])
        if not sources:
            report.errors.append(f"{name}: no provenance records at all.")
        sourced_fields = {s.get("field_name") for s in sources}
        for source in sources:
            if not _valid_url(source.get("source_url")):
                report.errors.append(
                    f"{name}: malformed source_url for {source.get('field_name')}."
                )
            if not source.get("source_name"):
                report.errors.append(f"{name}: source without a source_name.")
        missing_critical = {
            f for f in ("annual_fee", "reward_cap", "eligibility_min_salary") if f not in sourced_fields
        }
        if missing_critical:
            report.warnings.append(
                f"{name}: no provenance record for critical field(s): {', '.join(sorted(missing_critical))}."
            )

        if card.get("modelling_limitation"):
            report.info.append(f"{name}: carries a modelling limitation and will not be ranked.")

    unknown_critical = sorted(
        f for f in CRITICAL_FIELDS if f in {"fx_fee"}
    )
    if unknown_critical:
        report.info.append(
            "Critical-field policy: fx_fee is only treated as critical for profiles with "
            "international spend."
        )
    return report
