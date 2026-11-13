"""Check the warehouse against the source, computed independently of dbt.

Run after the end-to-end load and `dbt build`. Recomputes every campaign figure
from the generated source files in plain Python, then compares it with
mart_campaign_performance row by row, and checks the run log and raw counts.

    python scripts/verify_e2e.py --data data/generated --marts-schema ci_123_marts --out results.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from ingest.config import SqlSettings  # noqa: E402
from ingest.sql_store import connect  # noqa: E402

CENT = Decimal("0.01")


def expected_campaigns(data: Path) -> dict[str, dict]:
    day2 = json.loads((data / "day2" / "api.json").read_text(encoding="utf-8"))
    registrations: dict[str, dict] = {}
    for day in ("day1", "day2"):
        for path in sorted((data / "files" / day).glob("*.csv")):
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    registrations[row["registration_id"]] = row

    out: dict[str, dict] = {}
    for campaign in day2["campaigns"]:
        out[campaign["campaign_id"]] = {
            "supporters_acquired": 0,
            "donors": set(),
            "paid_donations": 0,
            "gross_gbp": Decimal(0),
            "refunded_gbp": Decimal(0),
            "net_gbp": Decimal(0),
            "gift_aid_gbp": Decimal(0),
            "pending_gbp": Decimal(0),
            "event_registrations": 0,
            "event_fundraising_target_gbp": Decimal(0),
        }
    for supporter in day2["supporters"]:
        out[supporter["acquisition_campaign_id"]]["supporters_acquired"] += 1
    for donation in day2["donations"]:
        row = out[donation["campaign_id"]]
        amount = Decimal(donation["amount"])
        if donation["status"] == "paid":
            row["donors"].add(donation["supporter_id"])
            row["paid_donations"] += 1
            row["gross_gbp"] += amount
            row["net_gbp"] += amount
            if donation["gift_aid"]:
                row["gift_aid_gbp"] += (amount * Decimal("0.25")).quantize(CENT, rounding=ROUND_HALF_UP)
        elif donation["status"] == "refunded":
            row["gross_gbp"] += amount
            row["refunded_gbp"] += amount
        else:
            row["pending_gbp"] += amount
    for registration in registrations.values():
        row = out[registration["campaign_id"]]
        row["event_registrations"] += 1
        row["event_fundraising_target_gbp"] += Decimal(registration["fundraising_target_gbp"])
    for row in out.values():
        row["donors"] = len(row["donors"])

    emails = {s["email"].strip().lower() for s in day2["supporters"]}
    matched = sum(1 for r in registrations.values() if r["supporter_email"].strip().lower() in emails)
    return {
        "campaigns": out,
        "totals": {
            "supporters": len(day2["supporters"]),
            "donations": len(day2["donations"]),
            "registrations": len(registrations),
            "registrations_matched": matched,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "generated")
    parser.add_argument("--marts-schema", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "results.json")
    args = parser.parse_args()

    expected = expected_campaigns(args.data)
    connection = connect(SqlSettings.from_env())
    cursor = connection.cursor()
    schema = args.marts_schema
    failures: list[str] = []

    columns = list(next(iter(expected["campaigns"].values())).keys())
    rows = cursor.execute(
        f"SELECT campaign_id, {', '.join(columns)} FROM [{schema}].mart_campaign_performance"
    ).fetchall()
    actual = {row[0]: dict(zip(columns, row[1:], strict=True)) for row in rows}
    if set(actual) != set(expected["campaigns"]):
        failures.append(f"campaigns differ: {sorted(set(actual) ^ set(expected['campaigns']))}")
    compared = 0
    for campaign_id, want in expected["campaigns"].items():
        got = actual.get(campaign_id, {})
        for column, value in want.items():
            compared += 1
            if Decimal(str(got.get(column))) != Decimal(str(value)):
                failures.append(f"{campaign_id}.{column}: mart {got.get(column)} != source {value}")

    def scalar(sql: str) -> int:
        return cursor.execute(sql).fetchone()[0]

    counts = {
        "raw_supporters": scalar("SELECT COUNT(*) FROM raw.crm_supporters"),
        "raw_donations": scalar("SELECT COUNT(*) FROM raw.crm_donations"),
        "raw_registrations": scalar("SELECT COUNT(*) FROM raw.event_registrations"),
        "fct_donations": scalar(f"SELECT COUNT(*) FROM [{schema}].fct_donations"),
        "registrations_matched": scalar(
            f"SELECT COUNT(*) FROM [{schema}].fct_event_registrations WHERE supporter_id IS NOT NULL"
        ),
        "mart_email_columns": scalar(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{schema}' AND LOWER(COLUMN_NAME) LIKE '%email%'"
        ),
    }
    want_counts = {
        "raw_supporters": expected["totals"]["supporters"],
        "raw_donations": expected["totals"]["donations"],
        "raw_registrations": expected["totals"]["registrations"],
        "fct_donations": expected["totals"]["donations"],
        "registrations_matched": expected["totals"]["registrations_matched"],
        "mart_email_columns": 0,
    }
    for key, value in want_counts.items():
        if counts[key] != value:
            failures.append(f"{key}: warehouse {counts[key]} != expected {value}")

    run_log = [
        {
            "entity": r[0],
            "file": r[1],
            "status": r[2],
            "incremental": r[3] is not None,
            "received": r[4],
            "inserted": r[5],
            "updated": r[6],
            "unchanged": r[7],
            "api_requests": r[8],
            "api_retries": r[9],
        }
        for r in cursor.execute(
            """
            SELECT entity, COALESCE(source_file, ''), status,
                   CONVERT(NVARCHAR(40), requested_since, 127), rows_received, rows_inserted,
                   rows_updated, rows_unchanged, api_requests, api_retries
            FROM ctl.ingestion_runs ORDER BY started_at
            """
        ).fetchall()
    ]
    if any(run["status"] != "succeeded" for run in run_log):
        failures.append("a load in the run log did not succeed")
    replays = [run for run in run_log if run["entity"] in {"campaigns", "supporters", "donations"}][-3:]
    if any(run["inserted"] or run["updated"] for run in replays):
        failures.append("the replayed API run changed rows; loads are not idempotent")

    late_ids = json.loads((args.data / "meta.json").read_text(encoding="utf-8"))["late_commit_donation_ids"]
    placeholders = ", ".join("?" for _ in late_ids)
    late_found = cursor.execute(
        f"SELECT COUNT(*) FROM [{schema}].fct_donations WHERE donation_id IN ({placeholders})", *late_ids
    ).fetchone()[0]
    if late_found != len(late_ids):
        failures.append(f"late commits in marts: {late_found} of {len(late_ids)}")

    result = {
        "passed": not failures,
        "campaign_values_compared": compared,
        "counts": counts,
        "late_commits_found": f"{late_found} of {len(late_ids)}",
        "run_log": run_log,
        "failures": failures,
    }
    args.out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [
            "## End-to-end check",
            "",
            f"**{'Passed' if not failures else 'FAILED'}**: {compared} campaign figures compared with the source; "
            f"late commits found {late_found} of {len(late_ids)}.",
            "",
            "| Load | Incremental | Received | Inserted | Updated | Unchanged | API retries |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for run in run_log:
            name = run["entity"] + (f" ({run['file']})" if run["file"] else "")
            lines.append(
                f"| {name} | {'yes' if run['incremental'] else 'no'} | {run['received']} | {run['inserted']} | "
                f"{run['updated']} | {run['unchanged']} | {run['api_retries'] or 0} |"
            )
        lines += ["", "```json", json.dumps(counts, indent=1), "```"]
        if failures:
            lines += ["", "### Failures", *[f"- {f}" for f in failures]]
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    print(json.dumps({k: v for k, v in result.items() if k != "run_log"}, indent=1, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
