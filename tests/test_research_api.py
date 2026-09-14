import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.card_importer import import_cards, load_dataset
from app.db.database import get_session
from app.db.models import Base
from app.main import app


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


def test_targets_endpoint_lists_configured_cards(client):
    body = client.get("/api/v1/research/targets").json()
    assert len(body["targets"]) >= 10


def test_unknown_target_is_404(client):
    response = client.post("/api/v1/research/card", json={"slug": "not-a-card"})
    assert response.status_code == 404


def test_research_without_firecrawl_key_reports_no_documents_not_fake_ones(client):
    """Replay mode with an empty cache must produce zero evidence, never invented values."""
    response = client.post("/api/v1/research/card", json={"slug": "hsbc-cashback"})
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_count"] == 0
    assert body["modelable"] is False
    assert body["blocking_fields"]


def test_unknown_research_card_is_404(client):
    assert client.get("/api/v1/research/cards/nope").status_code == 404
    assert client.get("/api/v1/research/cards/nope/evidence").status_code == 404


def test_promote_refuses_unverified_card(client):
    created = client.post("/api/v1/research/card", json={"slug": "hsbc-cashback"}).json()
    response = client.post(f"/api/v1/research/promote/{created['research_id']}")
    assert response.status_code == 409


def test_evidence_endpoint_returns_provenance_shape(client):
    created = client.post("/api/v1/research/card", json={"slug": "hsbc-cashback"}).json()
    body = client.get(f"/api/v1/research/cards/{created['research_id']}/evidence").json()
    assert body["card_slug"] == "hsbc-cashback"
    assert isinstance(body["evidence"], list)


def test_existing_recommendation_endpoints_are_unchanged(client):
    assert client.get("/api/v1/health").json()["cards_loaded"] == len(load_dataset()["cards"])
    response = client.post(
        "/api/v1/recommend",
        json={
            "emirate": "Dubai",
            "salary_monthly": 25000,
            "age": 35,
            "monthly_category_spend": {"groceries": 4000, "dining": 2000},
            "reward_preference": "cashback",
            "annual_fee_tolerance": 500,
            "pays_balance_in_full": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["top_cards"]
