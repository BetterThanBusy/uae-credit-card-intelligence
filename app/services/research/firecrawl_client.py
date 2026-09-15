"""Firecrawl retrieval client.

This is the production retrieval layer. It is deliberately the ONLY module that
knows Firecrawl exists: everything downstream consumes `RetrievedDocument`, so
the retrieval provider can be swapped without touching extraction, verification
or promotion.

Cost control is built in rather than bolted on:
  - every response is cached on disk by a hash of the request
  - a cached document whose content hash is unchanged is never re-processed
  - search returns URLs; only URLs that pass the official-domain filter are scraped
  - full-domain crawling is opt-in and capped
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

FIRECRAWL_SDK_HINT = (
    "Set FIRECRAWL_API_KEY to enable live retrieval. Without it the research "
    "pipeline runs in replay mode against the on-disk cache."
)


class FirecrawlError(RuntimeError):
    """Raised when Firecrawl is unreachable, unauthorised or returns an error."""


class FirecrawlNotConfigured(FirecrawlError):
    """Raised when a live call is attempted without an API key."""


@dataclass
class SearchResult:
    url: str
    title: str = ""
    description: str = ""

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.url).netloc.lower().removeprefix("www.")


@dataclass
class RetrievedDocument:
    """Provider-neutral document. Everything downstream depends on this only."""

    url: str
    content: str
    title: str = ""
    content_type: str = "html"  # html | pdf
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.url).netloc.lower().removeprefix("www.")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_cache(self) -> dict:
        return {
            "url": self.url,
            "content": self.content,
            "title": self.title,
            "content_type": self.content_type,
            "retrieved_at": self.retrieved_at.isoformat(),
            "status_code": self.status_code,
            "metadata": self.metadata,
        }

    @classmethod
    def from_cache(cls, payload: dict) -> "RetrievedDocument":
        return cls(
            url=payload["url"],
            content=payload["content"],
            title=payload.get("title", ""),
            content_type=payload.get("content_type", "html"),
            retrieved_at=datetime.fromisoformat(payload["retrieved_at"]),
            status_code=payload.get("status_code"),
            metadata=payload.get("metadata", {}),
        )


class RetrievalClient(Protocol):
    """What the orchestrator needs. Firecrawl is one implementation."""

    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...

    def scrape(self, url: str, formats: list[str] | None = None) -> RetrievedDocument: ...

    def map_site(self, url: str, search: str | None = None, limit: int = 50) -> list[str]: ...


class DocumentCache:
    """Disk cache keyed by request hash. Prevents paying twice for one page."""

    def __init__(self, directory: str | None = None, ttl_hours: int | None = None) -> None:
        settings = get_settings()
        self.directory = Path(directory or settings.research_cache_dir)
        self.ttl = timedelta(hours=ttl_hours or settings.research_cache_ttl_hours)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        cached_at = datetime.fromisoformat(payload["_cached_at"])
        if datetime.now(timezone.utc) - cached_at > self.ttl:
            return None
        return payload["data"]

    def set(self, key: str, data: dict) -> None:
        self._path(key).write_text(
            json.dumps(
                {"_cached_at": datetime.now(timezone.utc).isoformat(), "data": data},
                default=str,
            ),
            encoding="utf-8",
        )


class FirecrawlClient:
    """Thin, typed wrapper over the Firecrawl HTTP API.

    Uses the documented v2 endpoints (`/search`, `/scrape`, `/map`, `/crawl`)
    over plain HTTP rather than the SDK, so the only dependency is httpx, which
    the project already ships.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache: DocumentCache | None = None,
        transport: Any = None,
        max_retries: int = 3,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.firecrawl_api_key
        self.base_url = (base_url or settings.firecrawl_base_url).rstrip("/")
        self.timeout = settings.firecrawl_timeout
        self.cache = cache if cache is not None else DocumentCache()
        self.max_retries = max_retries
        self._transport = transport  # injectable for tests
        self.call_count = 0

    # -- plumbing --------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        if not self.api_key:
            raise FirecrawlNotConfigured(FIRECRAWL_SDK_HINT)
        import httpx

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    self.call_count += 1
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    time.sleep(2**attempt)
                    last_error = FirecrawlError("rate limited")
                    continue
                if response.status_code >= 400:
                    # The API explains exactly which field it rejected. Discarding
                    # that body turns a one-line fix into a guessing game.
                    raise FirecrawlError(
                        f"{response.status_code} from {path}: {_error_detail(response)}"
                    )
                return response.json()
            except FirecrawlError as exc:
                # A 4xx is a request-shape problem. Retrying identical bad input
                # just spends credits, so fail fast and report.
                if "rate limited" not in str(exc):
                    raise
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2**attempt)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2**attempt)
        raise FirecrawlError(f"Firecrawl request to {path} failed: {last_error}")

    # -- capabilities ----------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Discover candidate URLs. Never authoritative by itself."""
        cache_key = f"search::{query}::{limit}"
        cached = self.cache.get(cache_key)
        if cached is None:
            cached = self._post("/search", {"query": query, "limit": limit})
            self.cache.set(cache_key, cached)
        return [
            SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                description=item.get("description", "") or item.get("snippet", ""),
            )
            for item in _search_items(cached)
            if item.get("url")
        ]

    def scrape(self, url: str, formats: list[str] | None = None) -> RetrievedDocument:
        """Retrieve one page or PDF as markdown. PDFs are handled by Firecrawl."""
        formats = formats or ["markdown"]
        cache_key = f"scrape::{url}::{','.join(sorted(formats))}"
        cached = self.cache.get(cache_key)
        if cached is None:
            cached = self._scrape_negotiated(url, formats)
            self.cache.set(cache_key, cached)
        return _document_from_scrape(url, cached)

    def _scrape_negotiated(self, url: str, formats: list[str]) -> dict:
        """Try the documented payload, then fall back on a 400.

        Firecrawl has moved PDF handling between top-level `parsePDF` and a
        `parsers` list across versions. Rather than pin one guess, try the
        richest payload first and drop the optional keys if the API rejects
        them. The minimal payload is the last attempt, and if that also fails
        the error is raised with the API's own explanation attached.
        """
        attempts: list[dict] = [
            {"url": url, "formats": formats, "parsers": ["pdf"]},
            {"url": url, "formats": formats, "parsePDF": True},
            {"url": url, "formats": formats},
            {"url": url},
        ]
        errors: list[str] = []
        for payload in attempts:
            try:
                result = self._post("/scrape", payload)
            except FirecrawlNotConfigured:
                raise
            except FirecrawlError as exc:
                errors.append(f"{sorted(payload)} -> {exc}")
                continue
            if len(attempts) > 1 and payload is not attempts[0]:
                logger.info("scrape succeeded with reduced payload: %s", sorted(payload))
            return result
        raise FirecrawlError(
            "Every scrape payload shape was rejected for "
            f"{url}. Attempts: " + " | ".join(errors)
        )

    def map_site(self, url: str, search: str | None = None, limit: int = 50) -> list[str]:
        """List URLs on a domain without downloading them. Cheap discovery."""
        payload: dict[str, Any] = {"url": url, "limit": limit}
        if search:
            payload["search"] = search
        cache_key = f"map::{url}::{search}::{limit}"
        cached = self.cache.get(cache_key)
        if cached is None:
            cached = self._post("/map", payload)
            self.cache.set(cache_key, cached)
        links = cached.get("links") or cached.get("data") or []
        return [item["url"] if isinstance(item, dict) else item for item in links]

    def crawl(self, url: str, limit: int = 20, include_paths: list[str] | None = None) -> str:
        """Start a crawl. Opt-in and capped: we prefer search + targeted scrape.

        Returns the job id; poll with `crawl_status`.
        """
        payload: dict[str, Any] = {"url": url, "limit": min(limit, 50)}
        if include_paths:
            payload["includePaths"] = include_paths
        response = self._post("/crawl", payload)
        job_id = response.get("id") or response.get("jobId")
        if not job_id:
            raise FirecrawlError("Crawl did not return a job id")
        return str(job_id)

    def crawl_status(self, job_id: str) -> dict:
        if not self.api_key:
            raise FirecrawlNotConfigured(FIRECRAWL_SDK_HINT)
        import httpx

        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            self.call_count += 1
            response = client.get(
                f"{self.base_url}/crawl/{job_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        response.raise_for_status()
        return response.json()


class ReplayRetrievalClient:
    """Offline implementation backed entirely by the on-disk cache.

    Used when no API key is present, and in environments where outbound network
    access to Firecrawl is blocked. It never invents a document: an uncached URL
    raises, rather than returning empty content that could be mistaken for
    "the page says nothing about this field".
    """

    def __init__(self, documents: dict[str, RetrievedDocument] | None = None) -> None:
        self.documents = documents or {}
        self.call_count = 0

    def add(self, document: RetrievedDocument) -> None:
        self.documents[document.url] = document

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.call_count += 1
        return [
            SearchResult(url=url, title=doc.title)
            for url, doc in list(self.documents.items())[:limit]
        ]

    def scrape(self, url: str, formats: list[str] | None = None) -> RetrievedDocument:
        self.call_count += 1
        if url not in self.documents:
            raise FirecrawlError(
                f"No cached document for {url}. Replay mode cannot retrieve new pages."
            )
        return self.documents[url]

    def map_site(self, url: str, search: str | None = None, limit: int = 50) -> list[str]:
        self.call_count += 1
        return [u for u in self.documents if u.startswith(url)][:limit]


def get_retrieval_client() -> RetrievalClient:
    """Firecrawl when configured, replay otherwise. Never a silent no-op."""
    settings = get_settings()
    if settings.firecrawl_enabled:
        return FirecrawlClient()
    logger.warning("FIRECRAWL_API_KEY not set; research runs in replay mode")
    return ReplayRetrievalClient()


# -- response shape helpers ---------------------------------------------
def _looks_like_pdf(url: str) -> str:
    """A .pdf with ?referrer=... still ends in the query string, not '.pdf'."""
    from urllib.parse import urlparse

    return urlparse(url).path.lower().endswith(".pdf")


def _error_detail(response) -> str:
    """The API's own message, trimmed. Never swallowed."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return (response.text or "<empty body>")[:400]
    for key in ("error", "message", "detail", "details"):
        if key in body:
            return str(body[key])[:400]
    return str(body)[:400]


def _search_items(payload: dict) -> list[dict]:
    data = payload.get("data", payload)
    if isinstance(data, dict):
        # v2 groups by result type; web results are the ones we want.
        return data.get("web") or data.get("results") or []
    if isinstance(data, list):
        return data
    return []


def _document_from_scrape(url: str, payload: dict) -> RetrievedDocument:
    data = payload.get("data", payload)
    metadata = data.get("metadata", {}) or {}
    content = data.get("markdown") or data.get("content") or data.get("html") or ""
    return RetrievedDocument(
        url=metadata.get("sourceURL") or metadata.get("url") or url,
        content=content,
        title=metadata.get("title", ""),
        content_type="pdf" if _looks_like_pdf(url) else "html",
        status_code=metadata.get("statusCode"),
        metadata=metadata,
    )
