"""LLM explanation layer.

The LLM receives a finished calculation and writes prose. It never computes.
If no API key is configured the deterministic summary is used, so the product
works with the LLM switched off.
"""
from __future__ import annotations

import json
import logging

from app.core.config import get_settings
from app.db.schemas import RecommendationResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You explain a credit card recommendation that has already been calculated. "
    "Do not change any number. Do not perform any financial calculation. "
    "Do not introduce facts that are absent from the supplied data. "
    "Do not recommend a card marked as excluded or not ranked. "
    "State uncertainty where the data quality says it exists. "
    "Write four to six short sentences in plain English."
)


def build_llm_payload(response: RecommendationResponse) -> dict:
    best = response.top_cards[0] if response.top_cards else None
    return {
        "user": response.user_profile_summary,
        "recommendation": best.model_dump(mode="json") if best else None,
        "alternatives": [c.model_dump(mode="json") for c in response.top_cards[1:3]],
        "calculation_breakdown": best.model_dump(mode="json")["category_breakdown"] if best else [],
        "assumptions": response.assumptions,
        "data_quality": {
            "overall": response.data_quality.value,
            "reasons": best.data_quality_reasons if best else [],
            "unknown_fields": best.unknown_fields if best else [],
        },
        "excluded_cards": [
            {"card": c.card_name, "reason": c.exclusion_reason} for c in response.conditional_cards
        ],
    }


def deterministic_explanation(response: RecommendationResponse) -> str:
    if not response.top_cards:
        return (
            "No card could be ranked precisely for this profile. Every candidate has at least "
            "one unverified field that is critical to the calculation."
        )
    best = response.top_cards[0]
    lines = [
        f"{best.card_name} from {best.issuer} ranks first with an estimated net annual value of "
        f"AED {best.net_annual_value:,.0f}.",
        f"That is AED {best.gross_annual_rewards:,.0f} of estimated rewards, less an annual fee of "
        f"AED {best.annual_fee:,.0f} and estimated FX costs of AED {best.fx_cost:,.0f}.",
        f"Data quality for this result is {response.data_quality.value}.",
    ]
    if best.unknown_fields:
        lines.append("Unverified fields: " + ", ".join(best.unknown_fields) + ".")
    return " ".join(lines)


def explain(response: RecommendationResponse) -> str:
    settings = get_settings()
    if not settings.llm_enabled:
        return deterministic_explanation(response)
    try:
        import httpx

        payload = build_llm_payload(response)
        reply = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.llm_api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "max_tokens": 700,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": json.dumps(payload, default=str)}],
            },
            timeout=30,
        )
        reply.raise_for_status()
        blocks = reply.json().get("content", [])
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return text or deterministic_explanation(response)
    except Exception:
        logger.warning("LLM explanation failed; using deterministic summary", exc_info=True)
        return deterministic_explanation(response)
