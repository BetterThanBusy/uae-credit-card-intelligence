import copy

from app.data.card_importer import load_dataset
from app.data.card_validator import validate_dataset


def test_shipped_dataset_has_no_errors():
    assert validate_dataset(load_dataset()).ok


def test_shipped_dataset_contains_no_placeholder_cards():
    banned = ("PLACEHOLDER", "TEST_", "SYNTHETIC", "SAMPLE", "DUMMY")
    for card in load_dataset()["cards"]:
        blob = f"{card['card_id']}{card['card_name']}{card['issuer']}".upper()
        assert not any(token in blob for token in banned)


def test_every_card_has_provenance_with_real_urls():
    for card in load_dataset()["cards"]:
        assert card["sources"], f"{card['card_id']} has no sources"
        for source in card["sources"]:
            assert source["source_url"].startswith("https://")
            assert source["source_name"]


def test_detects_impossible_cashback_rate():
    dataset = copy.deepcopy(load_dataset())
    dataset["cards"][0]["reward_rules"][0]["rate"] = 5.0
    report = validate_dataset(dataset)
    assert not report.ok
    assert any("exceeds 100%" in e for e in report.errors)


def test_detects_negative_fee():
    dataset = copy.deepcopy(load_dataset())
    dataset["cards"][0]["fees"][0]["amount"] = -100
    assert any("negative" in e for e in validate_dataset(dataset).errors)


def test_detects_duplicate_card_ids():
    dataset = copy.deepcopy(load_dataset())
    dataset["cards"].append(copy.deepcopy(dataset["cards"][0]))
    assert any("Duplicate" in e for e in validate_dataset(dataset).errors)


def test_detects_malformed_source_url():
    dataset = copy.deepcopy(load_dataset())
    dataset["cards"][0]["sources"][0]["source_url"] = "not-a-url"
    assert any("malformed" in e for e in validate_dataset(dataset).errors)


def test_detects_missing_annual_fee_record():
    dataset = copy.deepcopy(load_dataset())
    dataset["cards"][0]["fees"] = [
        f for f in dataset["cards"][0]["fees"] if f["fee_type"] != "ANNUAL_FEE"
    ]
    assert any("no annual fee record" in e for e in validate_dataset(dataset).errors)


def test_detects_inverted_tier_range():
    dataset = copy.deepcopy(load_dataset())
    rule = dataset["cards"][1]["reward_rules"][0]
    rule["tier_min_monthly_spend"], rule["tier_max_monthly_spend"] = 9999, 3000
    assert any("tier range inverted" in e for e in validate_dataset(dataset).errors)
