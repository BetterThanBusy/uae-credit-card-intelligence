"""The web client is a rendering layer only.

These tests defend two promises: the client is actually served by the API, and
it never performs financial arithmetic. If someone later "optimises" by
computing a total in JavaScript, the second test fails.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.card_importer import import_cards, load_dataset
from app.db.database import get_session
from app.db.models import Base
from app.main import app

INDEX = Path(__file__).resolve().parents[1] / "frontend" / "web" / "index.html"

MONEY_FIELDS = [
    "net_annual_value", "gross_annual_rewards", "annual_fee", "fx_cost",
    "monthly_reward_after_category_cap", "monthly_reward_uncapped",
    "combined_net_annual_value", "best_single_net_annual_value", "incremental_value",
    "potential_additional_value", "current_estimated_value", "recommended_estimated_value",
]


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed:
        import_cards(seed, load_dataset())

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def script() -> str:
    return INDEX.read_text(encoding="utf-8").split("<script>")[1]


def test_client_file_exists():
    assert INDEX.is_file()


def test_index_is_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "UAE Credit Card Intelligence" in response.text


def test_static_mount_does_not_shadow_the_api(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/cards").status_code == 200


def test_client_performs_no_arithmetic_on_money_fields():
    js = script()
    offenders = []
    for field in MONEY_FIELDS:
        for operator in ("*", "+", "-", "/"):
            for pattern in (f"{field}{operator}", f"{field} {operator}"):
                if pattern in js:
                    offenders.append(pattern)
    # The only permitted exception is a ratio used for a progress-bar width,
    # which produces a percentage, never a displayed currency figure.
    offenders = [o for o in offenders if o != "net_annual_value/"]
    assert not offenders, f"financial arithmetic found in the web client: {offenders}"


def test_client_has_no_hardcoded_card_names_or_amounts():
    """No fake data for visual purposes: the client must render only API output."""
    js = script()
    for token in ("ADCB", "Mashreq", "RAKBANK", "Emirates Islamic", "HSBC"):
        assert token not in js, f"card name '{token}' hardcoded in the client"
    assert "AED 3" not in js and "AED 1," not in js


def test_client_calls_the_real_endpoints():
    js = script()
    for endpoint in ('"/health"', '"/cards"', '"/recommend"', '"/cards/"'):
        assert endpoint in js, f"client does not call {endpoint}"


def test_client_declares_no_browser_storage():
    js = script()
    assert "localStorage" not in js
    assert "sessionStorage" not in js
