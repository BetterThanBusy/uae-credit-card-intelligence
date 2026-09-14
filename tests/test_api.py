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
    # StaticPool keeps every session on the same in-memory database.
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


ACCEPTANCE = {
    "emirate": "Dubai",
    "salary_monthly": 25000,
    "age": 35,
    "monthly_category_spend": {
        "groceries": 4000, "dining": 2000, "fuel": 1000, "travel": 2000,
        "online": 1500, "utilities": 1000, "other": 1000,
    },
    "international_monthly_spend": 2000,
    "travel_frequency_per_year": 3,
    "reward_preference": "cashback",
    "annual_fee_tolerance": 500,
    "pays_balance_in_full": True,
    "current_card_id": "mashreq-cashback",
}


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["cards_loaded"] == len(load_dataset()["cards"])


def test_list_cards(client):
    body = client.get("/api/v1/cards").json()
    returned = {c["card_id"] for c in body}
    assert returned == {c["card_id"] for c in load_dataset()["cards"]}
    assert {"adcb-365-cashback", "mashreq-cashback"} <= returned


def test_card_detail_includes_provenance(client):
    body = client.get("/api/v1/cards/adcb-365-cashback").json()
    assert body["reward_rules"]
    assert body["sources"]
    assert all(s["source_url"].startswith("https://") for s in body["sources"])


def test_unknown_card_returns_404(client):
    assert client.get("/api/v1/cards/does-not-exist").status_code == 404


def test_recommend_returns_full_contract(client):
    response = client.post("/api/v1/recommend", json=ACCEPTANCE)
    assert response.status_code == 200
    body = response.json()
    for key in (
        "run_id", "card_data_version", "calculation_version", "user_profile_summary",
        "eligible_cards", "top_cards", "conditional_cards", "best_strategy",
        "current_card_comparison", "missed_value", "insights", "assumptions",
        "data_quality", "disclaimer",
    ):
        assert key in body, f"missing {key}"
    assert response.headers["x-request-id"]


def test_recommend_rejects_invalid_profile(client):
    bad = dict(ACCEPTANCE, monthly_category_spend={"groceries": -50})
    assert client.post("/api/v1/recommend", json=bad).status_code == 422


def test_recommend_rejects_unknown_emirate(client):
    bad = dict(ACCEPTANCE, emirate="Doha")
    assert client.post("/api/v1/recommend", json=bad).status_code == 422
