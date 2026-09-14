"""Application configuration. No secrets in source control."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel

CALCULATION_ENGINE_VERSION = "1.0.0"


class Settings(BaseModel):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./uae_cards.db")
    environment: str = os.getenv("ENVIRONMENT", "local")
    llm_api_key: str | None = os.getenv("LLM_API_KEY") or None
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    firecrawl_api_key: str | None = os.getenv("FIRECRAWL_API_KEY") or None
    firecrawl_base_url: str = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v2")
    firecrawl_timeout: int = int(os.getenv("FIRECRAWL_TIMEOUT", "120"))
    research_cache_dir: str = os.getenv("RESEARCH_CACHE_DIR", ".research_cache")
    research_cache_ttl_hours: int = int(os.getenv("RESEARCH_CACHE_TTL_HOURS", "168"))
    # Two cards are only worth recommending over one if the gain is material.
    minimum_incremental_value: float = float(os.getenv("MIN_INCREMENTAL_VALUE", "250"))
    # Card data older than this is reported as STALE by the validator.
    staleness_days: int = int(os.getenv("STALENESS_DAYS", "180"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def firecrawl_enabled(self) -> bool:
        return bool(self.firecrawl_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
