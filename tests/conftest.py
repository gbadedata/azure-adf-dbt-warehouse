from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from ingest.api_client import CrmApiClient  # noqa: E402
from ingest.sql_store import MergeCounts, RunRecord, parse_iso  # noqa: E402
from mock_api.app import Settings, create_app  # noqa: E402
from mock_api.generator import SMALL, generate  # noqa: E402


@pytest.fixture(scope="session")
def source() -> dict:
    return generate(seed=7, scale=SMALL)


def make_client(
    data: dict,
    sleeps: list[float] | None = None,
    page_size: int = 25,
    max_retries: int = 3,
    **settings,
) -> CrmApiClient:
    """A real client talking to the mock API in-process, with sleeps recorded instead of taken."""
    app = create_app(Settings(data_path=Path("unused"), **settings), data=data)
    http = TestClient(app, base_url="http://crm.test")
    record = sleeps if sleeps is not None else []
    return CrmApiClient(
        "http://crm.test",
        "local-client",
        "local-secret",
        http=http,
        page_size=page_size,
        max_retries=max_retries,
        sleep=record.append,
    )


class FakeStore:
    """In-memory stand-in with the same merge rules as the SQL Server store."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = defaultdict(dict)
        self.registrations: dict[str, dict] = {}
        self.watermarks: dict[tuple[str, str], datetime] = {}
        self.runs: dict[str, RunRecord] = {}
        self.commits = 0

    def get_watermark(self, source_name, entity):
        return self.watermarks.get((source_name, entity))

    def set_watermark(self, source_name, entity, value):
        current = self.watermarks.get((source_name, entity))
        if current is None or value > current:
            self.watermarks[(source_name, entity)] = value

    def start_run(self, run):
        self.runs[run.run_id] = run

    def finish_run(self, run):
        self.runs[run.run_id] = run

    def merge_api_records(self, table, records, run_id):
        counts = MergeCounts(received=len(records))
        target = self.tables[table]
        for record in records:
            existing = target.get(record["id"])
            if existing is None:
                target[record["id"]] = record
                counts.inserted += 1
            elif parse_iso(record["updated_at"]) > parse_iso(existing["updated_at"]):
                target[record["id"]] = record
                counts.updated += 1
        return counts

    def merge_registrations(self, rows, source_file):
        counts = MergeCounts(received=len(rows))
        for row in rows:
            existing = self.registrations.get(row["registration_id"])
            if existing is None:
                counts.inserted += 1
            elif existing != row:
                counts.updated += 1
            else:
                continue
            self.registrations[row["registration_id"]] = dict(row)
        return counts

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()
