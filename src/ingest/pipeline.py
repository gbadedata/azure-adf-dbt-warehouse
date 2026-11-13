"""The load logic, kept separate from HTTP and SQL so it can be tested on its own."""

from __future__ import annotations

import csv
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .api_client import CrmApiClient
from .config import API_SOURCE, Entity
from .sql_store import MergeCounts, RunRecord, parse_iso

log = logging.getLogger(__name__)

REGISTRATION_COLUMNS = [
    "registration_id",
    "event_name",
    "event_date",
    "campaign_id",
    "supporter_email",
    "registered_at",
    "ticket_type",
    "fundraising_target_gbp",
]


class Store(Protocol):
    def get_watermark(self, source_name: str, entity: str) -> datetime | None: ...
    def set_watermark(self, source_name: str, entity: str, value: datetime) -> None: ...
    def start_run(self, run: RunRecord) -> None: ...
    def finish_run(self, run: RunRecord) -> None: ...
    def merge_api_records(self, table: str, records: list[dict], run_id: str) -> MergeCounts: ...
    def merge_registrations(self, rows: list[dict], source_file: str) -> MergeCounts: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class FileFormatError(ValueError):
    """A source file does not have the agreed columns."""


def latest_versions(records: list[dict], entity: Entity) -> list[dict]:
    """Collapse a page to one row per key, keeping the newest version."""
    newest: dict[str, dict] = {}
    for record in records:
        key = str(record[entity.key])
        current = newest.get(key)
        if current is None or parse_iso(record[entity.updated_field]) > parse_iso(current[entity.updated_field]):
            newest[key] = record
    return list(newest.values())


def load_api_entity(
    store: Store,
    client: CrmApiClient,
    entity: Entity,
    lookback: timedelta,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    """Load everything changed since the last high watermark, minus an overlap window.

    The overlap catches rows committed late with a timestamp just behind the
    watermark. Re-reading a few rows is harmless because the merge only applies
    strictly newer versions. The watermark moves only after every page has been
    merged, so a failed run is simply repeated next time.
    """
    watermark = store.get_watermark(API_SOURCE, entity.name)
    since = watermark - lookback if watermark else None
    run = RunRecord(
        run_id=str(uuid.uuid4()),
        source_name=API_SOURCE,
        entity=entity.name,
        started_at=now(),
        requested_since=since,
    )
    store.start_run(run)
    requests_before, retries_before = client.stats.requests, client.stats.retries
    highest = watermark
    try:
        for page in client.iter_pages(entity.name, since):
            records = latest_versions(page.records, entity)
            payload = [{"id": str(r[entity.key]), "updated_at": r[entity.updated_field], "payload": r} for r in records]
            counts = store.merge_api_records(entity.table, payload, run.run_id)
            store.commit()
            run.pages += 1
            run.rows_received += counts.received
            run.rows_inserted += counts.inserted
            run.rows_updated += counts.updated
            run.rows_unchanged += counts.unchanged
            for record in records:
                seen = parse_iso(record[entity.updated_field])
                if highest is None or seen > highest:
                    highest = seen

        if highest is not None and (watermark is None or highest > watermark):
            store.set_watermark(API_SOURCE, entity.name, highest)
        run.new_watermark = highest
        run.status = "succeeded"
    except Exception as exc:
        store.rollback()
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run.api_requests = client.stats.requests - requests_before
        run.api_retries = client.stats.retries - retries_before
        store.finish_run(run)
        log.info(
            "api load finished",
            extra={
                "entity": entity.name,
                "status": run.status,
                "requested_since": since.isoformat() if since else None,
                "received": run.rows_received,
                "inserted": run.rows_inserted,
                "updated": run.rows_updated,
                "unchanged": run.rows_unchanged,
                "pages": run.pages,
                "retries": run.api_retries,
            },
        )
    return run


def read_registrations(path: Path) -> list[dict]:
    """Read one export, refusing files whose header is not the agreed one."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != REGISTRATION_COLUMNS:
            raise FileFormatError(f"{path.name}: expected columns {REGISTRATION_COLUMNS}, got {reader.fieldnames}")
        rows = list(reader)
    # A file can repeat a registration; the last occurrence wins, as it would in the source.
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["registration_id"]] = row
    return list(latest.values())


def load_registration_file(
    store: Store,
    path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    run = RunRecord(
        run_id=str(uuid.uuid4()),
        source_name="events",
        entity="event_registrations",
        started_at=now(),
        source_file=path.name,
    )
    store.start_run(run)
    try:
        rows = read_registrations(path)
        counts = store.merge_registrations(rows, path.name)
        store.commit()
        run.pages = 1
        run.rows_received = counts.received
        run.rows_inserted = counts.inserted
        run.rows_updated = counts.updated
        run.rows_unchanged = counts.unchanged
        run.status = "succeeded"
    except Exception as exc:
        store.rollback()
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        store.finish_run(run)
        log.info(
            "file load finished",
            extra={
                "file": path.name,
                "status": run.status,
                "received": run.rows_received,
                "inserted": run.rows_inserted,
                "updated": run.rows_updated,
            },
        )
    return run
