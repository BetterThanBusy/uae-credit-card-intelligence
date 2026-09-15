"""Evidence verification.

This module is the reason the system cannot fabricate financial data. An LLM
(or a human, or a regex) may *propose* an extraction. Nothing is stored as
verified until these deterministic checks pass:

  1. the quoted evidence text actually occurs in the retrieved document
  2. the claimed value actually occurs in the quoted evidence text
  3. the evidence does not hedge the value with an unbounded qualifier
     ("up to 10%") unless the qualifying conditions were themselves verified
  4. the source domain is on the issuer's official allowlist

Fail any check and the field becomes UNKNOWN with a recorded reason. There is
no code path that writes a value without passing through `verify_candidate`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.domain.research_enums import (
    DOCUMENT_AUTHORITY,
    ISSUER_LEVEL_DOCUMENTS,
    RESEARCH_CRITICAL_FIELDS,
    UNBOUNDED_QUALIFIERS,
    DocumentType,
    RejectionReason,
    ResearchVerification,
)

# Official domains, per issuer. A value sourced anywhere else can never be
# VERIFIED_OFFICIAL, regardless of how confident the extractor was.
OFFICIAL_DOMAINS: dict[str, set[str]] = {
    "ADCB": {"adcb.com", "adcbislamic.com"},
    "Emirates NBD": {"emiratesnbd.com"},
    "Emirates Islamic": {"emiratesislamic.ae"},
    "FAB": {"bankfab.com", "bankfab.ae"},
    "Mashreq": {"mashreq.com", "mashreqalislami.com"},
    "HSBC UAE": {"hsbc.ae"},
    "Standard Chartered UAE": {"sc.com"},
    "RAKBANK": {"rakbank.ae"},
    "Dubai Islamic Bank": {"dib.ae"},
    "ADIB": {"adib.ae"},
    "Commercial Bank of Dubai": {"cbd.ae"},
    "Wio Bank": {"wio.io"},
    "Liv": {"liv.me"},
}


@dataclass
class ExtractionCandidate:
    """A proposed field value plus the text that is claimed to support it."""

    field_name: str
    value: str | None
    source_url: str
    evidence_text: str | None
    unit: str | None = None
    category: str | None = None
    scope: str | None = None
    document_type: str = DocumentType.OTHER.value
    effective_date: date | None = None
    conditions_verified: bool = False
    notes: str | None = None

    @property
    def is_critical(self) -> bool:
        return self.field_name in RESEARCH_CRITICAL_FIELDS


@dataclass
class VerificationOutcome:
    candidate: ExtractionCandidate
    status: ResearchVerification
    rejection_reason: RejectionReason | None = None
    is_official: bool = False
    source_tier: int = 6
    detail: str = ""
    normalised_value: float | None = None

    @property
    def usable(self) -> bool:
        return self.status in (
            ResearchVerification.VERIFIED_OFFICIAL,
            ResearchVerification.PARTIALLY_VERIFIED,
        )


def normalise(text: str) -> str:
    """Whitespace- and punctuation-tolerant form for substring matching.

    Scraped markdown collapses layout differently from the rendered page, so a
    strict equality check would reject valid evidence. We normalise both sides
    identically rather than loosening the check itself.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[*_`#>|]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def domain_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower().removeprefix("www.")


def is_official_source(url: str, issuer: str) -> bool:
    allow = OFFICIAL_DOMAINS.get(issuer)
    if not allow:
        return False
    domain = domain_of(url)
    return any(domain == d or domain.endswith(f".{d}") for d in allow)


def extract_numbers(text: str) -> list[float]:
    """Every number in the text, with thousands separators handled."""
    found = []
    for raw in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            found.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return found


def value_supported_by_evidence(value: str, evidence: str) -> bool:
    """Is the claimed value actually present in the quoted evidence?

    Numeric values are compared numerically, so "0.03" is supported by
    evidence reading "3% cashback" and "383.25" by "AED 383.25 (including VAT)".
    """
    evidence_norm = normalise(evidence)
    if normalise(value) in evidence_norm:
        return True

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False

    candidates = extract_numbers(evidence_norm)
    if any(abs(n - numeric) < 1e-9 for n in candidates):
        return True
    # A decimal rate (0.03) is supported by a percentage in the text (3%).
    if 0 < numeric < 1:
        as_percent = numeric * 100
        return any(abs(n - as_percent) < 1e-9 for n in candidates)
    return False


def has_unbounded_qualifier(evidence: str, value: str) -> bool:
    """Does the evidence hedge this number with 'up to' style wording?

    Only counts when the qualifier precedes the number, so "maximum monthly
    cashback of AED 1,000" (a real cap) is not confused with "earn up to 10%"
    (an upper bound on an unknown tiered rate).
    """
    evidence_norm = normalise(evidence)
    try:
        numeric = float(value)
        needles = {value, f"{numeric:g}", f"{numeric * 100:g}"}
    except (TypeError, ValueError):
        needles = {value}

    for qualifier in UNBOUNDED_QUALIFIERS:
        for match in re.finditer(re.escape(qualifier), evidence_norm):
            window = evidence_norm[match.end() : match.end() + 40]
            for needle in needles:
                if needle and normalise(needle) in window:
                    return True
    return False


# Words too generic to identify a card on their own. "Mashreq Cashback Credit
# Card" must not reduce to "cashback", which would match every card page on the
# site and reopen exactly the hole this gate closes.
GENERIC_NAME_TOKENS = frozenset(
    {
        "cashback", "cash", "rewards", "reward", "credit", "card", "cards",
        "platinum", "titanium", "gold", "silver", "classic", "signature",
        "infinite", "world", "elite", "premium", "plus", "visa", "mastercard",
    }
)


def card_name_variants(card_name: str) -> list[str]:
    """Forms of a card name that might appear in a document.

    "HSBC Live+ Credit Card" should match "Live+", "Live Plus" or the full name,
    including on the issuer's own pages, which usually omit their own brand.
    Variants that collapse to generic words are dropped.
    """
    base = card_name.lower()
    raw = {base}

    stripped = base
    for filler in (" credit card", " card", " credit"):
        stripped = stripped.replace(filler, " ")
    stripped = " ".join(stripped.split())
    if stripped:
        raw.add(stripped)
        # The issuer's own site rarely repeats its brand, so try without it.
        tokens = stripped.split()
        if len(tokens) > 1:
            raw.add(" ".join(tokens[1:]))

    variants = set()
    for item in raw:
        for form in (item, item.replace("+", " plus"), item.replace("+", "plus")):
            form = " ".join(form.split())
            if len(form) < 3:
                continue
            words = {w for w in form.replace("+", " ").split() if w}
            if words and words <= GENERIC_NAME_TOKENS:
                continue  # nothing distinctive left
            variants.add(normalise(form))
    return sorted(variants)


GENERIC_CARD_MARKERS = ("credit card", "cashback", "annual membership fee", "cardholder")


# A site-wide navigation menu names every product once, in one place. A real
# product page names its card in several distinct places. Two clustered mention
# sites is the smallest threshold that separates the two.
MIN_BODY_MENTIONS = 2
# How far the supporting sentence may sit from a card mention, in characters.
EVIDENCE_PROXIMITY = 1500


def _url_names_card(source_url: str | None, card_name: str) -> bool:
    if not source_url:
        return False
    from urllib.parse import urlparse

    path = normalise(urlparse(source_url).path.replace("-", " ").replace("_", " "))
    return any(v in path for v in card_name_variants(card_name))


def _mention_positions(text: str, card_name: str) -> list[int]:
    """Distinct places the card is named.

    Variants overlap - "live+", "hsbc live+" and "hsbc live+ credit card" all
    hit the same words - so raw matches are clustered. Without this, one
    navigation link counts as three mentions and a menu looks like a product
    page.
    """
    raw: list[int] = []
    for variant in card_name_variants(card_name):
        start = text.find(variant)
        while start != -1:
            raw.append(start)
            start = text.find(variant, start + 1)

    clustered: list[int] = []
    for position in sorted(raw):
        if not clustered or position - clustered[-1] > 60:
            clustered.append(position)
    return clustered


def document_scope(
    document_text: str,
    card_name: str,
    document_type: str,
    source_url: str | None = None,
    evidence_text: str | None = None,
) -> str:
    """Is this document about the card, about the issuer's cards, or neither?

    Returns CARD, ISSUER or NONE. This is the check that stops a current-account
    page's fees being attributed to a credit card. Document-level matching alone
    is not enough: bank sites put a link to every product in the navigation of
    every page, so the card's name appears on pages that have nothing to do with
    it. Two stronger signals are used instead - the URL naming the card, and the
    supporting sentence sitting close to a card mention in the body.
    """
    text = normalise(document_text)

    if _url_names_card(source_url, card_name):
        return "CARD"

    positions = _mention_positions(text, card_name)

    if positions and evidence_text:
        where = text.find(normalise(evidence_text))
        if where != -1 and min(abs(where - p) for p in positions) <= EVIDENCE_PROXIMITY:
            return "CARD"
    elif len(positions) >= MIN_BODY_MENTIONS:
        # No specific sentence to locate: fall back to how often the card is named.
        return "CARD"

    if document_type in ISSUER_LEVEL_DOCUMENTS and any(m in text for m in GENERIC_CARD_MARKERS):
        return "ISSUER"
    return "NONE"


def verify_candidate(
    candidate: ExtractionCandidate,
    document_text: str,
    issuer: str,
    card_name: str | None = None,
) -> VerificationOutcome:
    """The single gate every financial value must pass."""
    if not candidate.value or str(candidate.value).strip().upper() == "UNKNOWN":
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            detail="No value proposed.",
        )

    if not candidate.evidence_text or not candidate.evidence_text.strip():
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            RejectionReason.MISSING_EVIDENCE,
            detail="A value was proposed with no supporting evidence text.",
        )

    # 1. the evidence must really be in the document
    if normalise(candidate.evidence_text) not in normalise(document_text):
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            RejectionReason.EVIDENCE_NOT_IN_DOCUMENT,
            detail=(
                "The quoted evidence does not appear in the retrieved document. "
                "The extraction was discarded rather than trusted."
            ),
        )

    # 2. the value must really be in the evidence
    if not value_supported_by_evidence(str(candidate.value), candidate.evidence_text):
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            RejectionReason.VALUE_NOT_IN_EVIDENCE,
            detail="The quoted evidence does not contain the claimed value.",
        )

    # 3. 'up to X%' is a ceiling, not a rate
    if has_unbounded_qualifier(candidate.evidence_text, str(candidate.value)) and not (
        candidate.conditions_verified
    ):
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            RejectionReason.UNBOUNDED_QUALIFIER,
            detail=(
                "The source states this as an upper bound ('up to'). The applicable "
                "conditions were not established, so no precise rate is recorded."
            ),
        )

    # 4. plausibility
    plausible, why = _plausible(candidate)
    if not plausible:
        return VerificationOutcome(
            candidate,
            ResearchVerification.UNKNOWN,
            RejectionReason.IMPLAUSIBLE_VALUE,
            detail=why,
        )

    # 5. the document must actually be about this card
    scope = "CARD"
    if card_name:
        scope = document_scope(
            document_text,
            card_name,
            candidate.document_type,
            source_url=candidate.source_url,
            evidence_text=candidate.evidence_text,
        )
        if scope == "NONE":
            return VerificationOutcome(
                candidate,
                ResearchVerification.UNKNOWN,
                RejectionReason.DOCUMENT_NOT_ABOUT_CARD,
                detail=(
                    f"The quote is real and the domain is official, but this document does "
                    f"not mention {card_name} and is not a card-wide terms document. "
                    "Attributing its figures to this card would be a fabrication."
                ),
            )

    official = is_official_source(candidate.source_url, issuer)
    tier = DOCUMENT_AUTHORITY.get(candidate.document_type, 8) if official else 5

    if official and scope == "ISSUER":
        return VerificationOutcome(
            candidate,
            ResearchVerification.PARTIALLY_VERIFIED,
            is_official=True,
            source_tier=tier,
            detail=(
                "Found in an official issuer-wide document that does not name this card. "
                "Applies to the card portfolio; not confirmed for this specific product."
            ),
            normalised_value=_as_float(candidate.value),
        )

    if not official:
        return VerificationOutcome(
            candidate,
            ResearchVerification.PARTIALLY_VERIFIED,
            RejectionReason.NON_OFFICIAL_SOURCE,
            is_official=False,
            source_tier=tier,
            detail=(
                f"Evidence checks passed but {domain_of(candidate.source_url)} is not a "
                f"known official domain for {issuer}."
            ),
            normalised_value=_as_float(candidate.value),
        )

    return VerificationOutcome(
        candidate,
        ResearchVerification.VERIFIED_OFFICIAL,
        is_official=True,
        source_tier=tier,
        detail="Evidence located in an official issuer document and supports the value.",
        normalised_value=_as_float(candidate.value),
    )


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plausible(candidate: ExtractionCandidate) -> tuple[bool, str]:
    numeric = _as_float(candidate.value)
    if numeric is None:
        return True, ""
    if numeric < 0:
        return False, "Negative financial value."
    if candidate.field_name in {"reward_rate", "international_reward_rate"} and numeric > 1:
        return False, f"Reward rate {numeric} exceeds 100%; rates are stored as decimals."
    if candidate.field_name == "fx_fee" and numeric > 20:
        return False, f"FX fee of {numeric}% is outside any plausible range."
    if candidate.field_name == "annual_fee" and numeric > 50000:
        return False, f"Annual fee of {numeric} is implausible."
    return True, ""


# -- conflict resolution --------------------------------------------------
@dataclass
class FieldResolution:
    field_name: str
    outcome: VerificationOutcome | None
    status: ResearchVerification
    conflicts: list[VerificationOutcome] = field(default_factory=list)
    detail: str = ""


def resolve_field(outcomes: list[VerificationOutcome]) -> FieldResolution:
    """Pick one value per field across several documents.

    Newer effective dates beat older ones and the conflict is recorded rather
    than discarded. Two current official documents that disagree produce
    CONFLICTING_SOURCES, never a silent pick.
    """
    usable = [o for o in outcomes if o.usable]
    if not usable:
        field_name = outcomes[0].candidate.field_name if outcomes else "unknown"
        return FieldResolution(field_name, None, ResearchVerification.UNKNOWN)

    field_name = usable[0].candidate.field_name
    official = [o for o in usable if o.is_official]
    pool = official or usable

    distinct = {str(o.candidate.value) for o in pool}
    if len(distinct) == 1:
        best = sorted(pool, key=lambda o: o.source_tier)[0]
        return FieldResolution(field_name, best, best.status)

    dated = [o for o in pool if o.candidate.effective_date is not None]
    if dated:
        newest = max(o.candidate.effective_date for o in dated)  # type: ignore[type-var]
        winners = [o for o in dated if o.candidate.effective_date == newest]
        losers = [o for o in pool if o not in winners]
        best = sorted(winners, key=lambda o: o.source_tier)[0]
        return FieldResolution(
            field_name,
            best,
            best.status,
            conflicts=losers,
            detail=(
                f"Superseded by the document effective {newest.isoformat()}; "
                "the earlier value is retained as a recorded conflict."
            ),
        )

    best = sorted(pool, key=lambda o: o.source_tier)[0]
    return FieldResolution(
        field_name,
        best,
        ResearchVerification.CONFLICTING_SOURCES,
        conflicts=[o for o in pool if o is not best],
        detail=(
            "Two current official sources state different values and no effective "
            "date separates them. Flagged for human review."
        ),
    )
