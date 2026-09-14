"""Live Firecrawl check. ONE card, before any batch run.

This is the script that must be run before trusting the live path. It exercises
each stage separately and prints the raw response shape at each one, so a
mismatch between the real API and the client's parsing assumptions shows up as a
diagnostic rather than as silently empty data.

    export FIRECRAWL_API_KEY=fc-...
    python scripts/live_firecrawl_check.py

Exit code is non-zero if any stage fails, so this can gate a batch run.

It deliberately does NOT fall back to anything on error. An API failure must
surface as a failure; converting it to empty content would look identical to
"the official page does not mention this field", which is the one confusion
this whole project exists to prevent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.research.extraction import classify_document, get_extractor  # noqa: E402
from app.services.research.firecrawl_client import (  # noqa: E402
    DocumentCache,
    FirecrawlClient,
    FirecrawlError,
)
from app.services.research.orchestrator import build_queries, find_target  # noqa: E402
from app.services.research.verification import (  # noqa: E402
    is_official_source,
    verify_candidate,
)

TARGET_SLUG = "hsbc-live-plus-research"  # falls back to any configured HSBC target
FALLBACK_TARGET = {
    "slug": "hsbc-live-plus",
    "card_name": "HSBC Live+ Credit Card",
    "issuer": "HSBC UAE",
    "domain": "hsbc.ae",
    "card_type": "cashback",
}

failures: list[str] = []


def stage(name: str) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")


def fail(name: str, detail: str) -> None:
    failures.append(f"{name}: {detail}")
    print(f"  FAIL  {detail}")


def main() -> int:
    settings = get_settings()
    if not settings.firecrawl_enabled:
        print("FIRECRAWL_API_KEY is not set. Nothing was tested.")
        print("This script makes real API calls and must not be faked.")
        return 2

    target = find_target(TARGET_SLUG) or FALLBACK_TARGET
    client = FirecrawlClient(cache=DocumentCache())
    print(f"Target: {target['card_name']} ({target['issuer']}, {target['domain']})")
    print(f"Base URL: {client.base_url}")

    # -- 1. search ------------------------------------------------------
    stage("1. SEARCH")
    queries = build_queries(target)[:3]
    results = []
    for query in queries:
        print(f"  query: {query}")
        try:
            found = client.search(query, limit=5)
        except FirecrawlError as exc:
            fail("search", str(exc))
            continue
        print(f"    -> {len(found)} result(s)")
        for item in found[:5]:
            print(f"       {item.domain:<24} {item.url}")
        results.extend(found)
    if not results:
        fail(
            "search",
            "No results parsed. If the API returned 200, _search_items() does not "
            "match the live response shape - print the raw payload and fix the client.",
        )

    # -- 2. official URL filtering --------------------------------------
    stage("2. OFFICIAL URL FILTERING")
    official = [r for r in results if is_official_source(r.url, target["issuer"])]
    rejected = [r for r in results if r not in official]
    print(f"  official: {len(official)}   rejected (discovery only): {len(rejected)}")
    for item in rejected[:5]:
        print(f"    rejected {item.domain}")
    if results and not official:
        fail(
            "filter",
            f"No result survived the official-domain filter for {target['issuer']}. "
            "Check OFFICIAL_DOMAINS in verification.py against the live domains above.",
        )

    # -- 3. targeted scrape ---------------------------------------------
    stage("3. TARGETED SCRAPE")
    documents = []
    for item in official[:3]:
        try:
            document = client.scrape(item.url)
        except FirecrawlError as exc:
            fail("scrape", f"{item.url}: {exc}")
            continue
        print(f"  {item.url}")
        print(f"    chars={len(document.content):<7} type={document.content_type} "
              f"classified={classify_document(document)}")
        print(f"    hash={document.content_hash[:16]}...")
        if not document.content.strip():
            fail(
                "scrape",
                f"{item.url} returned empty content. Either the page is JS-rendered and "
                "needs a wait/action, or _document_from_scrape() is reading the wrong key.",
            )
        else:
            documents.append(document)

    # -- 4. PDF retrieval ------------------------------------------------
    stage("4. PDF RETRIEVAL")
    pdf_urls = [r.url for r in official if r.url.lower().endswith(".pdf")]
    if not pdf_urls:
        print("  No PDF surfaced by these queries. Trying the PDF-specific query.")
        try:
            pdf_urls = [
                r.url
                for r in client.search(
                    f'"{target["card_name"]}" PDF site:{target["domain"]}', limit=5
                )
                if r.url.lower().endswith(".pdf")
                and is_official_source(r.url, target["issuer"])
            ]
        except FirecrawlError as exc:
            fail("pdf-search", str(exc))
    if pdf_urls:
        try:
            pdf = client.scrape(pdf_urls[0])
            print(f"  {pdf_urls[0]}")
            print(f"    chars={len(pdf.content)} type={pdf.content_type}")
            if not pdf.content.strip():
                fail("pdf", "PDF returned no text; check parsePDF handling.")
            else:
                documents.append(pdf)
        except FirecrawlError as exc:
            fail("pdf", str(exc))
    else:
        print("  No official PDF found for this card. Not a failure.")

    # -- 5/6/7. extraction, evidence, verification ------------------------
    stage("5-7. EXTRACTION -> EVIDENCE -> VERIFICATION")
    extractor = get_extractor()
    print(f"  extractor: {type(extractor).__name__}")
    verified = rejected_count = 0
    for document in documents:
        candidates = extractor.extract(document)
        print(f"\n  {document.url}\n    {len(candidates)} candidate(s)")
        for candidate in candidates[:12]:
            outcome = verify_candidate(candidate, document.content, target["issuer"])
            mark = "OK  " if outcome.usable else "DROP"
            reason = outcome.rejection_reason.value if outcome.rejection_reason else ""
            print(
                f"      {mark} {candidate.field_name:<26} "
                f"{str(candidate.value):<12} {outcome.status.value:<20} {reason}"
            )
            if outcome.usable:
                verified += 1
            else:
                rejected_count += 1
    print(f"\n  usable={verified}  dropped={rejected_count}")
    if documents and verified == 0:
        fail(
            "verification",
            "Every candidate was dropped. Inspect the reasons above: repeated "
            "EVIDENCE_NOT_IN_DOCUMENT usually means the scraped markdown differs "
            "from what the extractor quoted.",
        )

    # -- 8. cache behaviour ----------------------------------------------
    stage("8. CACHE BEHAVIOUR")
    if documents:
        url = documents[0].url
        before = client.call_count
        client.scrape(url)
        after = client.call_count
        print(f"  API calls for an already-cached page: {after - before}")
        if after != before:
            fail("cache", "A cached page triggered another API call; caching is not working.")
        else:
            print("  Cache hit confirmed: no additional API call.")

    stage("RESULT")
    print(f"  Total Firecrawl API calls this run: {client.call_count}")
    if failures:
        print(f"  {len(failures)} FAILURE(S) - do NOT run the batch until these are fixed:")
        for item in failures:
            print(f"    - {item}")
        return 1
    print("  All stages passed. The live path matches the client's assumptions.")
    print("  Safe to run: POST /api/v1/research/batch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
