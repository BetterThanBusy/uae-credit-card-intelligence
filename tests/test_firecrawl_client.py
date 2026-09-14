import json
import os

import httpx
import pytest

from app.services.research.firecrawl_client import (
    DocumentCache,
    FirecrawlClient,
    FirecrawlNotConfigured,
    ReplayRetrievalClient,
    RetrievedDocument,
    _document_from_scrape,
    _search_items,
)

SEARCH_PAYLOAD = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card",
                "title": "ADCB 365 Cashback Credit Card",
                "description": "Cashback on groceries, dining and fuel.",
            },
            {
                "url": "https://kredit.ae/credit-cards/adcb-365",
                "title": "ADCB 365 review",
                "description": "Third party review.",
            },
        ]
    },
}

SCRAPE_PAYLOAD = {
    "success": True,
    "data": {
        "markdown": "3% cashback on Groceries & Supermarkets spends.",
        "metadata": {
            "title": "ADCB 365 Cashback Credit Card",
            "sourceURL": "https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card",
            "statusCode": 200,
        },
    },
}


def make_client(tmp_path, handler, api_key="test-key"):
    transport = httpx.MockTransport(handler)
    cache = DocumentCache(directory=str(tmp_path / "cache"), ttl_hours=1)
    return FirecrawlClient(api_key=api_key, cache=cache, transport=transport, max_retries=1)


def test_search_returns_typed_results(tmp_path):
    def handler(request):
        assert request.url.path.endswith("/search")
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = make_client(tmp_path, handler)
    results = client.search("ADCB 365 site:adcb.com")
    assert len(results) == 2
    assert results[0].domain == "adcb.com"
    assert results[1].domain == "kredit.ae"


def test_scrape_returns_document_with_hash(tmp_path):
    client = make_client(tmp_path, lambda r: httpx.Response(200, json=SCRAPE_PAYLOAD))
    document = client.scrape("https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card")
    assert "3% cashback" in document.content
    assert document.domain == "adcb.com"
    assert len(document.content_hash) == 64


def test_second_identical_request_is_served_from_cache(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=SCRAPE_PAYLOAD)

    client = make_client(tmp_path, handler)
    url = "https://www.adcb.com/en/personal/cards/credit-cards/365-cashback-card"
    client.scrape(url)
    client.scrape(url)
    assert calls["n"] == 1, "a cached page must not be fetched twice"


def test_missing_api_key_raises_rather_than_returning_empty(tmp_path):
    client = FirecrawlClient(
        api_key=None, cache=DocumentCache(directory=str(tmp_path / "c"), ttl_hours=1)
    )
    with pytest.raises(FirecrawlNotConfigured):
        client.scrape("https://www.adcb.com/anything")


def test_http_error_raises_firecrawl_error(tmp_path):
    from app.services.research.firecrawl_client import FirecrawlError

    client = make_client(tmp_path, lambda r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(FirecrawlError):
        client.search("anything")


def test_map_site_returns_urls(tmp_path):
    payload = {"links": [{"url": "https://www.adcb.com/a"}, "https://www.adcb.com/b"]}
    client = make_client(tmp_path, lambda r: httpx.Response(200, json=payload))
    assert client.map_site("https://www.adcb.com") == [
        "https://www.adcb.com/a",
        "https://www.adcb.com/b",
    ]


def test_replay_client_refuses_unknown_urls():
    """Replay mode must never return blank content that looks like 'field absent'."""
    from app.services.research.firecrawl_client import FirecrawlError

    client = ReplayRetrievalClient()
    with pytest.raises(FirecrawlError):
        client.scrape("https://www.adcb.com/not-cached")


def test_replay_client_serves_seeded_documents():
    document = RetrievedDocument(url="https://www.adcb.com/x", content="Annual fee of AED 100.")
    client = ReplayRetrievalClient({document.url: document})
    assert client.scrape("https://www.adcb.com/x").content.startswith("Annual fee")


def test_search_item_shapes_are_tolerated():
    assert _search_items({"data": {"web": [{"url": "a"}]}}) == [{"url": "a"}]
    assert _search_items({"data": [{"url": "b"}]}) == [{"url": "b"}]
    assert _search_items({"data": {}}) == []


def test_pdf_documents_are_marked_as_pdf():
    document = _document_from_scrape(
        "https://www.adcb.com/fees.pdf",
        {"data": {"markdown": "Schedule of fees", "metadata": {}}},
    )
    assert document.content_type == "pdf"


@pytest.mark.skipif(
    not os.getenv("FIRECRAWL_API_KEY"), reason="live Firecrawl integration test; needs a key"
)
def test_integration_live_firecrawl_search():
    """Opt-in integration test. The rest of the suite never touches the network."""
    client = FirecrawlClient()
    results = client.search('"ADCB 365 Cashback Credit Card" site:adcb.com', limit=3)
    assert results
    assert any(r.domain.endswith("adcb.com") for r in results)
