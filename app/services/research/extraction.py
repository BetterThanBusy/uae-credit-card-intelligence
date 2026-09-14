"""Field extraction.

Two proposers exist. Both produce `ExtractionCandidate` objects, and both are
subject to the same deterministic gate in `verification.verify_candidate`:

  - `LLMExtractor` asks Claude to quote the supporting sentence for each field.
    The model proposes; it never decides. An invented quote fails the
    evidence-in-document check and the field becomes UNKNOWN.
  - `PatternExtractor` is a dependency-free fallback that finds candidate
    sentences by keyword and lets the same gate judge them. It keeps the
    pipeline testable and runnable with no API key.

Neither proposer computes anything financial.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from app.core.config import get_settings
from app.domain.research_enums import DocumentType
from app.services.research.firecrawl_client import RetrievedDocument
from app.services.research.verification import ExtractionCandidate

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You extract credit card terms from an official bank document.

Rules you must follow exactly:
- For every field you return, quote the supporting sentence VERBATIM from the document in evidence_text. Copy it character for character.
- If the document does not state a field, return it with value null. Never guess, never infer from context, never carry a value over from general knowledge.
- If the document says "up to X%", the rate is NOT X%. Return the value null unless the document also states the exact conditions under which X% applies, in which case set conditions_verified true.
- Store percentages as decimals: 5% becomes 0.05.
- Do not perform arithmetic. Do not convert currencies. Do not total anything.

Return ONLY a JSON array, no prose and no markdown fences. Each element:
{"field_name": "...", "category": null, "scope": null, "value": null, "unit": "...",
 "evidence_text": "...", "conditions_verified": false, "notes": null}

Field names to look for: annual_fee, annual_fee_waiver, fx_fee, min_monthly_spend,
eligibility_min_salary, eligibility_min_age, reward_rate (one per category, set category),
reward_cap (set category and unit), international_reward_rate.
Categories: groceries, dining, fuel, travel, online, utilities, government, fashion,
entertainment, education, telecom, other."""


# Keyword sets used by the fallback proposer and by document classification.
FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "annual_fee": ("annual fee", "annual membership fee", "membership fee"),
    "annual_fee_waiver": ("fee waiver", "waived", "free for life", "no annual fee"),
    "fx_fee": ("foreign transaction", "foreign currency", "fx fee", "currency conversion"),
    "min_monthly_spend": ("minimum spend", "minimum retail spend", "minimum monthly"),
    "eligibility_min_salary": ("minimum salary", "minimum income", "monthly income"),
    "reward_cap": ("maximum cashback", "cashback cap", "capped at", "maximum monthly"),
}

DOCUMENT_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("schedule of charges", DocumentType.SCHEDULE_OF_CHARGES.value),
    ("schedule of fees", DocumentType.SCHEDULE_OF_CHARGES.value),
    ("service and price guide", DocumentType.SCHEDULE_OF_CHARGES.value),
    ("key facts statement", DocumentType.KEY_FACTS.value),
    ("key fact", DocumentType.KEY_FACTS.value),
    ("terms and conditions", DocumentType.CARD_TERMS.value),
    ("terms & conditions", DocumentType.CARD_TERMS.value),
    ("rewards programme", DocumentType.REWARDS_TERMS.value),
    ("rewards terms", DocumentType.REWARDS_TERMS.value),
    ("cashback terms", DocumentType.REWARDS_TERMS.value),
    ("frequently asked", DocumentType.FAQ.value),
    ("faq", DocumentType.FAQ.value),
    ("apply", DocumentType.APPLICATION.value),
    ("credit-card", DocumentType.PRODUCT_PAGE.value),
    ("credit cards", DocumentType.PRODUCT_PAGE.value),
)


def classify_document(document: RetrievedDocument) -> str:
    """Best-effort document typing from URL and title. Never affects a value."""
    haystack = f"{document.url} {document.title}".lower()
    for needle, doc_type in DOCUMENT_TYPE_HINTS:
        if needle in haystack:
            return doc_type
    head = document.content[:1500].lower()
    for needle, doc_type in DOCUMENT_TYPE_HINTS:
        if needle in head:
            return doc_type
    return DocumentType.OTHER.value


EFFECTIVE_DATE_PATTERNS = (
    r"effective (?:from |as of |with effect from )?(\d{1,2}\s+\w+\s+\d{4})",
    r"with effect from (\d{1,2}\s+\w+\s+\d{4})",
    r"effective (?:from )?(\d{4}-\d{2}-\d{2})",
)


def find_effective_date(text: str) -> date | None:
    """Pull a stated effective date so newer documents can supersede older ones."""
    from datetime import datetime

    lowered = text[:6000].lower()
    for pattern in EFFECTIVE_DATE_PATTERNS:
        match = re.search(pattern, lowered)
        if not match:
            continue
        raw = match.group(1)
        for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
    return None


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


class PatternExtractor:
    """Keyword proposer. Finds sentences that may state a field and proposes
    the numbers in them. The verification gate does the deciding."""

    def extract(
        self, document: RetrievedDocument, document_type: str | None = None
    ) -> list[ExtractionCandidate]:
        document_type = document_type or classify_document(document)
        effective = find_effective_date(document.content)
        candidates: list[ExtractionCandidate] = []

        for sentence in split_sentences(document.content):
            lowered = sentence.lower()
            if len(sentence) > 400:
                continue
            for field_name, keywords in FIELD_KEYWORDS.items():
                if not any(k in lowered for k in keywords):
                    continue
                numbers = re.findall(r"\d[\d,]*\.?\d*", sentence)
                if not numbers:
                    continue
                raw = numbers[0].replace(",", "")
                value = raw
                unit = "AED"
                if "%" in sentence and field_name in {"fx_fee"}:
                    unit = "PERCENT"
                candidates.append(
                    ExtractionCandidate(
                        field_name=field_name,
                        value=value,
                        unit=unit,
                        source_url=document.url,
                        evidence_text=sentence,
                        document_type=document_type,
                        effective_date=effective,
                    )
                )
        return candidates


class LLMExtractor:
    """Claude proposes structured fields with verbatim evidence quotes."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def extract(
        self, document: RetrievedDocument, document_type: str | None = None
    ) -> list[ExtractionCandidate]:
        if not self.enabled:
            raise RuntimeError("LLM extraction requires LLM_API_KEY")
        document_type = document_type or classify_document(document)
        effective = find_effective_date(document.content)

        import httpx

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "system": EXTRACTION_SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Document URL: {document.url}\n"
                            f"Document type: {document_type}\n\n"
                            f"{document.content[:120000]}"
                        ),
                    }
                ],
            },
            timeout=180,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return self._parse(text, document, document_type, effective)

    @staticmethod
    def _parse(
        text: str,
        document: RetrievedDocument,
        document_type: str,
        effective: date | None,
    ) -> list[ExtractionCandidate]:
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            rows = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("LLM extraction returned unparseable JSON; no fields proposed")
            return []
        if not isinstance(rows, list):
            return []
        candidates = []
        for row in rows:
            if not isinstance(row, dict) or row.get("value") in (None, "", "null"):
                continue
            candidates.append(
                ExtractionCandidate(
                    field_name=str(row.get("field_name", "")),
                    value=str(row.get("value")),
                    unit=row.get("unit"),
                    category=row.get("category"),
                    scope=row.get("scope"),
                    source_url=document.url,
                    evidence_text=row.get("evidence_text"),
                    document_type=document_type,
                    effective_date=effective,
                    conditions_verified=bool(row.get("conditions_verified", False)),
                    notes=row.get("notes"),
                )
            )
        return candidates


def get_extractor():
    """Claude when a key is configured, pattern matching otherwise."""
    extractor = LLMExtractor()
    return extractor if extractor.enabled else PatternExtractor()
