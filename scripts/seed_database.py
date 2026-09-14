"""Create tables, import the researched dataset, emit data/card_sources.csv."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configure_logging  # noqa: E402
from app.data.card_importer import import_cards, load_dataset  # noqa: E402
from app.db.database import SessionLocal, create_all  # noqa: E402

SOURCES_CSV = Path(__file__).resolve().parents[1] / "data" / "card_sources.csv"

FIELDS = [
    "card_id", "card_name", "issuer", "field_name", "value", "unit",
    "source_url", "source_name", "source_type", "source_tier",
    "retrieved_at", "effective_from", "effective_to", "verification_status",
    "evidence", "notes",
]


def emit_sources_csv(dataset: dict) -> int:
    rows = []
    for card in dataset["cards"]:
        for source in card.get("sources", []):
            rows.append(
                {
                    "card_id": card["card_id"],
                    "card_name": card["card_name"],
                    "issuer": card["issuer"],
                    "field_name": source["field_name"],
                    "value": source.get("value", ""),
                    "unit": source.get("unit", ""),
                    "source_url": source["source_url"],
                    "source_name": source["source_name"],
                    "source_type": source.get("source_type", ""),
                    "source_tier": source.get("source_tier", ""),
                    "retrieved_at": card.get("retrieved_at", ""),
                    "effective_from": card.get("effective_from", ""),
                    "effective_to": card.get("effective_to", ""),
                    "verification_status": source.get("verification_status", ""),
                    "evidence": source.get("evidence", ""),
                    "notes": source.get("notes", ""),
                }
            )
    with open(SOURCES_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    configure_logging()
    create_all()
    dataset = load_dataset()
    with SessionLocal() as session:
        count, version = import_cards(session, dataset)
    source_rows = emit_sources_csv(dataset)
    print(f"Seeded {count} cards (dataset {version}); wrote {source_rows} provenance rows to {SOURCES_CSV.name}")


if __name__ == "__main__":
    main()
