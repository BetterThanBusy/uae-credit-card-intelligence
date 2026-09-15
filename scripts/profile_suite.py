"""Run ten realistic UAE spending profiles against a live deployment.

    python scripts/profile_suite.py                                  # localhost:8000
    python scripts/profile_suite.py https://<your-app>.onrender.com  # production

This is an acceptance check against a running API, not a unit test. It answers
four questions you cannot answer by reading code:

  1. does the recommendation actually change with the spending profile, or does
     one card win everything regardless?
  2. is the engine deterministic over HTTP - does the same profile return the
     same numbers twice?
  3. do minimum-spend gates, caps and eligibility rules fire on the profiles
     that should trigger them?
  4. does data quality degrade honestly when a profile involves foreign spend?

Exit code is non-zero if determinism breaks or any profile errors, so this can
gate a deploy.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
API = f"{BASE}/api/v1"

# Ten profiles chosen to exercise different engine paths, not to flatter the
# results. Several are expected to produce warnings or no ranking at all.
PROFILES: list[dict] = [
    {
        "label": "Grocery-heavy family",
        "expect": "grocery rate should dominate the reward mix",
        "body": {
            "emirate": "Sharjah", "salary_monthly": 15000, "age": 41,
            "monthly_category_spend": {"groceries": 6000, "dining": 800, "fuel": 600, "other": 1000},
            "international_monthly_spend": 0,
        },
    },
    {
        "label": "Dining-heavy young professional",
        "expect": "dining rate should dominate; different winner from grocery profile",
        "body": {
            "emirate": "Dubai", "salary_monthly": 22000, "age": 29,
            "monthly_category_spend": {"dining": 5000, "groceries": 1500, "entertainment": 800, "other": 1200},
            "international_monthly_spend": 0,
        },
    },
    {
        "label": "Fuel-heavy commuter",
        "expect": "fuel and Salik should drive the result",
        "body": {
            "emirate": "Ras Al Khaimah", "salary_monthly": 12000, "age": 38,
            "monthly_category_spend": {"fuel": 2500, "groceries": 2500, "dining": 700, "other": 800},
            "international_monthly_spend": 0,
        },
    },
    {
        "label": "Frequent international traveller",
        "expect": "data quality LOW - unverified FX fee must be flagged, not ignored",
        "body": {
            "emirate": "Abu Dhabi", "salary_monthly": 40000, "age": 45,
            "monthly_category_spend": {"travel": 4000, "dining": 2000, "groceries": 1500, "other": 1500},
            "international_monthly_spend": 6000, "travel_frequency_per_year": 12,
            "annual_fee_tolerance": 1500,
        },
    },
    {
        "label": "Mixed high-spend UAE consumer",
        "expect": "card-level monthly cap should bind somewhere",
        "body": {
            "emirate": "Dubai", "salary_monthly": 55000, "age": 44,
            "monthly_category_spend": {
                "groceries": 6000, "dining": 5000, "fuel": 2000, "travel": 4000,
                "online": 3000, "entertainment": 1500, "other": 3000,
            },
            "international_monthly_spend": 4000, "annual_fee_tolerance": 2000,
        },
    },
    {
        "label": "Low-income first card",
        "expect": "premium cards should drop out on eligibility",
        "body": {
            "emirate": "Ajman", "salary_monthly": 6000, "age": 24,
            "monthly_category_spend": {"groceries": 1200, "dining": 400, "fuel": 300, "other": 500},
            "international_monthly_spend": 0, "annual_fee_tolerance": 0,
        },
    },
    {
        "label": "Family with school fees and telecom",
        "expect": "education and telecom categories should be handled, not silently dropped",
        "body": {
            "emirate": "Dubai", "salary_monthly": 28000, "age": 43,
            "monthly_category_spend": {
                "groceries": 3500, "education": 4000, "telecom": 600,
                "dining": 1200, "fuel": 900, "utilities": 900, "other": 1000,
            },
            "international_monthly_spend": 0,
        },
    },
    {
        "label": "Online shopper, no fuel or travel",
        "expect": "online often sits in the catch-all rate; result should be modest",
        "body": {
            "emirate": "Sharjah", "salary_monthly": 18000, "age": 31,
            "monthly_category_spend": {"online": 4500, "groceries": 1500, "dining": 1000, "other": 800},
            "international_monthly_spend": 1500,
        },
    },
    {
        "label": "Light spender below every threshold",
        "expect": "minimum-spend warnings; most cards should earn nothing",
        "body": {
            "emirate": "Fujairah", "salary_monthly": 9000, "age": 27,
            "monthly_category_spend": {"groceries": 700, "dining": 300, "other": 400},
            "international_monthly_spend": 0, "annual_fee_tolerance": 0,
        },
    },
    {
        "label": "Revolver who does not clear the balance",
        "expect": "must warn that interest can wipe out the cashback",
        "body": {
            "emirate": "Dubai", "salary_monthly": 20000, "age": 36,
            "monthly_category_spend": {"groceries": 3000, "dining": 2000, "fuel": 1000, "other": 1500},
            "international_monthly_spend": 500, "pays_balance_in_full": False,
        },
    },
]

DEFAULTS = {
    "reward_preference": "cashback",
    "annual_fee_tolerance": 500,
    "pays_balance_in_full": True,
    "travel_frequency_per_year": 0,
}

failures: list[str] = []


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=90) as response:
        return json.loads(response.read())


def money(value) -> str:
    return "n/a" if value is None else f"{round(value):,}"


def main() -> int:
    print(f"Target: {BASE}\n")
    try:
        health = get("/health")
    except urllib.error.URLError as exc:
        print(f"Cannot reach {API}/health: {exc}")
        print("If this is a free Render instance it may be spinning up. Wait 60s and retry.")
        return 2

    print(f"health: {health['status']}  cards_loaded: {health['cards_loaded']}  "
          f"calculation: {health['calculation_version']}")
    if health["status"] != "ok":
        print("Health is not ok - the database is probably empty. Stopping.")
        return 2

    rows = []
    winners = set()

    for profile in PROFILES:
        body = dict(DEFAULTS)
        body.update(profile["body"])
        try:
            first = post("/recommend", body)
            second = post("/recommend", body)
        except urllib.error.HTTPError as exc:
            failures.append(f"{profile['label']}: HTTP {exc.code} {exc.read()[:200]!r}")
            rows.append((profile["label"], "ERROR", "-", "-", "-", "-"))
            continue

        # determinism: identical profile, identical card data => identical numbers
        a = [(c["card_id"], c["net_annual_value"]) for c in first["top_cards"]]
        b = [(c["card_id"], c["net_annual_value"]) for c in second["top_cards"]]
        if a != b:
            failures.append(f"{profile['label']}: NOT DETERMINISTIC\n  {a}\n  {b}")
        if first["card_data_version"] != second["card_data_version"]:
            failures.append(f"{profile['label']}: card data version changed mid-run")

        top = first["top_cards"][0] if first["top_cards"] else None
        if top:
            winners.add(top["card_id"])
        strategy = first.get("best_strategy")

        rows.append((
            profile["label"],
            top["card_name"] if top else "none rankable",
            money(top["net_annual_value"]) if top else "-",
            first["data_quality"],
            f"{len(first['top_cards'])}/{len(first['top_cards']) + len(first['conditional_cards'])}",
            ("yes +" + money(strategy["incremental_value"])) if strategy and strategy["recommended"] else "no",
        ))

        print(f"\n{'-' * 78}\n{profile['label']}\n  expect: {profile['expect']}")
        if top:
            fx_unknown = "fx_fee" in top["unknown_fields"]
            print(f"  -> {top['card_name']}  net {money(top['net_annual_value'])} AED/yr  "
                  f"[{top['data_quality']}]")
            print(f"     rewards {money(top['gross_annual_rewards'])}  "
                  f"fee {money(top['annual_fee'])}  "
                  f"fx {'not quantified' if fx_unknown else money(top['fx_cost'])}")
            earners = [
                f"{'abroad' if r.get('scope') == 'INTERNATIONAL' else r['category']}"
                f" {round((r['rate_applied'] or 0) * 100, 2)}%"
                for r in top["category_breakdown"]
                if r["monthly_reward_after_category_cap"] > 0
            ]
            print(f"     earning: {', '.join(earners) if earners else 'nothing'}")
            if top["caps_applied"]:
                print(f"     caps: {top['caps_applied'][0]}")
            if top["warnings"]:
                print(f"     warning: {top['warnings'][0]}")
            if top["unknown_fields"]:
                print(f"     unverified: {', '.join(top['unknown_fields'])}")
        else:
            print("  -> no card could be ranked precisely")
        for card in first["conditional_cards"][:2]:
            print(f"     not ranked: {card['card_name']} - {card['exclusion_reason']}")
        for insight in first["insights"][:2]:
            print(f"     insight: {insight}")

    # ---- summary table -------------------------------------------------
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    head = f"{'Profile':<36}{'Top card':<34}{'Net':>9}  {'Qual':<7}{'Rank':<7}{'2-card'}"
    print(head)
    print("-" * len(head))
    for label, card, net, quality, ranked, two in rows:
        print(f"{label[:35]:<36}{card[:33]:<34}{net:>9}  {quality:<7}{ranked:<7}{two}")

    print(f"\nDistinct winning cards across 10 profiles: {len(winners)}")
    if len(winners) < 2:
        failures.append(
            "Every profile produced the same winner. Either the dataset is too "
            "small to differentiate, or category rates are not being applied."
        )

    print(f"\n{'=' * 78}")
    if failures:
        print(f"{len(failures)} PROBLEM(S):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("All 10 profiles returned deterministic results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
