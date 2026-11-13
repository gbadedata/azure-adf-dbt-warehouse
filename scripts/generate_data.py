"""Write the synthetic source data to disk.

    python scripts/generate_data.py --out data/generated

Produces:
    day1/api.json, day2/api.json                       the CRM API's state on each day
    files/day1/event_registrations_20261101.csv        the events platform export, day one
    files/day2/event_registrations_20261102.csv        day two (new rows plus re-sent ones)
    meta.json                                          seed, snapshot times, IDs worth checking
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_api.generator import REGISTRATION_COLUMNS, SMALL, Scale, generate  # noqa: E402


def write(out: Path, seed: int, small: bool) -> dict:
    result = generate(seed=seed, scale=SMALL if small else Scale())
    for day in ("day1", "day2"):
        target = out / day
        target.mkdir(parents=True, exist_ok=True)
        (target / "api.json").write_text(json.dumps(result[day], indent=1), encoding="utf-8")

    stamps = {"day1": "20261101", "day2": "20261102"}
    for day, rows in result["files"].items():
        target = out / "files" / day
        target.mkdir(parents=True, exist_ok=True)
        with (target / f"event_registrations_{stamps[day]}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REGISTRATION_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    (out / "meta.json").write_text(json.dumps(result["meta"], indent=1), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--small", action="store_true", help="a few hundred rows instead of thousands")
    args = parser.parse_args()
    result = write(args.out, args.seed, args.small)
    counts = {day: {k: len(v) for k, v in result[day].items()} for day in ("day1", "day2")}
    counts["files"] = {day: len(rows) for day, rows in result["files"].items()}
    print(json.dumps(counts, indent=1))


if __name__ == "__main__":
    main()
