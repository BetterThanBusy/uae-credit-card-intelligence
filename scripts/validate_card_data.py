"""Validate the researched dataset and print a report."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.card_importer import load_dataset  # noqa: E402
from app.data.card_validator import validate_dataset  # noqa: E402


def main() -> int:
    report = validate_dataset(load_dataset())
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
