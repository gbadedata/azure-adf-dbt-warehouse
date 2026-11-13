"""Azure SQL / SQL Server access: connections, DDL, idempotent MERGE loads, run log."""

from __future__ import annotations

import json
import logging
import re
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import SqlSettings

log = logging.getLogger(__name__)

SQL_COPT_SS_ACCESS_TOKEN = 1256
TOKEN_SCOPE = "https://database.windows.net/.default"
# Azure SQL serverless returns these while a paused database resumes.
RESUMING_ERRORS = ("40613", "40197", "40501", "49918", "49919", "49920")
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass
class MergeCounts:
    received: int = 0
    inserted: int = 0
    updated: int = 0

    @property
    def unchanged(self) -> int:
        return self.received - self.inserted - self.updated


@dataclass
class RunRecord:
    run_id: str
    source_name: str
    entity: str
    started_at: datetime
    source_file: str | None = None
    requested_since: datetime | None = None
    new_watermark: datetime | None = None
    status: str = "running"
    pages: int = 0
    rows_received: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    api_requests: int = 0
    api_retries: int = 0
    error_message: str | None = None


def iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def split_batches(sql_text: str) -> list[str]:
    """Split a script on lines that contain only ``GO``, as sqlcmd does."""
    batches, current = [], []
    for line in sql_text.splitlines():
        if line.strip().upper() == "GO":
            if "".join(current).strip():
                batches.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if "".join(current).strip():
        batches.append("\n".join(current))
    return batches


def _access_token(settings: SqlSettings) -> bytes:
    from azure.identity import AzureCliCredential, DefaultAzureCredential, ManagedIdentityCredential

    if settings.auth == "msi":
        credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    elif settings.auth == "cli":
        credential = AzureCliCredential()
    else:
        credential = DefaultAzureCredential()
    token = credential.get_token(TOKEN_SCOPE).token.encode("utf-16-le")
    return struct.pack(f"<I{len(token)}s", len(token), token)


def connect(settings: SqlSettings, attempts: int = 6, delay_seconds: float = 10.0):
    """Open a connection, retrying while a paused serverless database wakes up."""
    import pyodbc

    parts = [
        f"Driver={{{settings.driver}}}",
        f"Server=tcp:{settings.server},{settings.port}",
        f"Database={settings.database}",
        "Encrypt=yes",
        f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'}",
        "Connection Timeout=30",
    ]
    attrs = {}
    if settings.auth == "sql":
        parts += [f"UID={settings.user}", f"PWD={settings.password}"]
    elif settings.auth in {"msi", "cli", "auto"}:
        attrs[SQL_COPT_SS_ACCESS_TOKEN] = _access_token(settings)
    else:
        raise ValueError(f"unsupported SQL_AUTH {settings.auth!r}: use sql, msi, cli or auto")

    connection_string = ";".join(parts)
    for attempt in range(1, attempts + 1):
        try:
            return pyodbc.connect(connection_string, attrs_before=attrs or None, autocommit=False)
        except pyodbc.Error as exc:
            message = str(exc)
            if attempt == attempts or not any(code in message for code in RESUMING_ERRORS):
                raise
            log.warning("database not ready, retrying", extra={"attempt": attempt, "error": message[:200]})
            time.sleep(delay_seconds)
    raise RuntimeError("unreachable")


class SqlServerStore:
    """Everything the loaders need from the database, in one place."""

    def __init__(self, connection) -> None:
        self.connection = connection

    # -- schema -------------------------------------------------------------------
    def apply_scripts(self, directory: Path) -> list[str]:
        applied = []
        cursor = self.connection.cursor()
        for path in sorted(directory.glob("*.sql")):
            for batch in split_batches(path.read_text(encoding="utf-8")):
                cursor.execute(batch)
            applied.append(path.name)
        self.connection.commit()
        return applied

    # -- watermarks ---------------------------------------------------------------
    def get_watermark(self, source_name: str, entity: str) -> datetime | None:
        row = (
            self.connection.cursor()
            .execute(
                "SELECT CONVERT(NVARCHAR(40), high_watermark, 127) FROM ctl.watermarks "
                "WHERE source_name = ? AND entity = ?",
                source_name,
                entity,
            )
            .fetchone()
        )
        return parse_iso(row[0]) if row else None

    def set_watermark(self, source_name: str, entity: str, value: datetime) -> None:
        """Move the watermark forward. It never moves back."""
        self.connection.cursor().execute(
            """
            MERGE ctl.watermarks AS t
            USING (SELECT ? AS source_name, ? AS entity,
                          CONVERT(DATETIMEOFFSET(3), ?, 127) AS high_watermark) AS s
            ON t.source_name = s.source_name AND t.entity = s.entity
            WHEN MATCHED AND s.high_watermark > t.high_watermark THEN
                UPDATE SET high_watermark = s.high_watermark, updated_at = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (source_name, entity, high_watermark, updated_at)
                VALUES (s.source_name, s.entity, s.high_watermark, SYSUTCDATETIME());
            """,
            source_name,
            entity,
            iso(value),
        )

    # -- run log --------------------------------------------------------------------
    def start_run(self, run: RunRecord) -> None:
        self.connection.cursor().execute(
            """
            INSERT INTO ctl.ingestion_runs
                (run_id, source_name, entity, source_file, started_at, status, requested_since)
            VALUES (?, ?, ?, ?, CONVERT(DATETIMEOFFSET(3), ?, 127), 'running',
                    CONVERT(DATETIMEOFFSET(3), ?, 127))
            """,
            run.run_id,
            run.source_name,
            run.entity,
            run.source_file,
            iso(run.started_at),
            iso(run.requested_since),
        )
        self.connection.commit()

    def finish_run(self, run: RunRecord) -> None:
        self.connection.cursor().execute(
            """
            UPDATE ctl.ingestion_runs
            SET finished_at = SYSDATETIMEOFFSET(),
                status = ?,
                new_watermark = CONVERT(DATETIMEOFFSET(3), ?, 127),
                pages = ?, rows_received = ?, rows_inserted = ?, rows_updated = ?, rows_unchanged = ?,
                api_requests = ?, api_retries = ?, error_message = ?
            WHERE run_id = ?
            """,
            run.status,
            iso(run.new_watermark),
            run.pages,
            run.rows_received,
            run.rows_inserted,
            run.rows_updated,
            run.rows_unchanged,
            run.api_requests,
            run.api_retries,
            (run.error_message or None) and run.error_message[:4000],
            run.run_id,
        )
        self.connection.commit()

    # -- loads ----------------------------------------------------------------------
    def merge_api_records(self, table: str, records: list[dict], run_id: str) -> MergeCounts:
        """Upsert ``[{"id", "updated_at", "payload"}]`` into ``raw.<table>``.

        A row is only overwritten by a strictly newer version, so replaying a
        page, or a whole run, changes nothing.
        """
        if not IDENTIFIER.match(table):
            raise ValueError(f"bad table name {table!r}")
        if not records:
            return MergeCounts()
        row = (
            self.connection.cursor()
            .execute(
                f"""
                SET NOCOUNT ON;
                DECLARE @actions TABLE (merge_action NVARCHAR(10));
                MERGE raw.{table} WITH (HOLDLOCK) AS t
                USING (
                    SELECT j.record_id,
                           CONVERT(DATETIMEOFFSET(3), j.updated_at, 127) AS source_updated_at,
                           j.payload
                    FROM OPENJSON(?) WITH (
                        record_id  NVARCHAR(100) '$.id',
                        updated_at NVARCHAR(40)  '$.updated_at',
                        payload    NVARCHAR(MAX) '$.payload' AS JSON
                    ) AS j
                ) AS s
                ON t.record_id = s.record_id
                WHEN MATCHED AND s.source_updated_at > t.source_updated_at THEN
                    UPDATE SET source_updated_at = s.source_updated_at,
                               payload = s.payload,
                               _last_loaded_at = SYSUTCDATETIME(),
                               _run_id = ?
                WHEN NOT MATCHED BY TARGET THEN
                    INSERT (record_id, source_updated_at, payload, _first_loaded_at, _last_loaded_at, _run_id)
                    VALUES (s.record_id, s.source_updated_at, s.payload, SYSUTCDATETIME(), SYSUTCDATETIME(), ?)
                OUTPUT $action INTO @actions;
                SELECT COALESCE(SUM(CASE WHEN merge_action = 'INSERT' THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN merge_action = 'UPDATE' THEN 1 ELSE 0 END), 0)
                FROM @actions;
                """,
                json.dumps(records, separators=(",", ":")),
                run_id,
                run_id,
            )
            .fetchone()
        )
        return MergeCounts(received=len(records), inserted=int(row[0]), updated=int(row[1]))

    def merge_registrations(self, rows: list[dict], source_file: str) -> MergeCounts:
        """Upsert event-registration rows on ``registration_id``.

        Matches what Data Factory's upsert does in Azure; additionally a row is
        only rewritten if a value actually changed.
        """
        if not rows:
            return MergeCounts()
        columns = [
            ("registration_id", "NVARCHAR(50)"),
            ("event_name", "NVARCHAR(200)"),
            ("event_date", "NVARCHAR(30)"),
            ("campaign_id", "NVARCHAR(50)"),
            ("supporter_email", "NVARCHAR(320)"),
            ("registered_at", "NVARCHAR(40)"),
            ("ticket_type", "NVARCHAR(50)"),
            ("fundraising_target_gbp", "NVARCHAR(30)"),
        ]
        names = [name for name, _ in columns]
        compared = [name for name in names if name != "registration_id"]
        with_clause = ",\n".join(f"{name} {kind} '$.{name}'" for name, kind in columns)
        row = (
            self.connection.cursor()
            .execute(
                f"""
                SET NOCOUNT ON;
                DECLARE @actions TABLE (merge_action NVARCHAR(10));
                MERGE raw.event_registrations WITH (HOLDLOCK) AS t
                USING (SELECT * FROM OPENJSON(?) WITH ({with_clause})) AS s
                ON t.registration_id = s.registration_id
                WHEN MATCHED AND EXISTS (
                    SELECT {", ".join(f"s.{c}" for c in compared)}
                    EXCEPT
                    SELECT {", ".join(f"t.{c}" for c in compared)}
                ) THEN
                    UPDATE SET {", ".join(f"{c} = s.{c}" for c in compared)},
                               _source_file = ?, _last_loaded_at = SYSUTCDATETIME()
                WHEN NOT MATCHED BY TARGET THEN
                    INSERT ({", ".join(names)}, _source_file, _last_loaded_at)
                    VALUES ({", ".join(f"s.{c}" for c in names)}, ?, SYSUTCDATETIME())
                OUTPUT $action INTO @actions;
                SELECT COALESCE(SUM(CASE WHEN merge_action = 'INSERT' THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN merge_action = 'UPDATE' THEN 1 ELSE 0 END), 0)
                FROM @actions;
                """,
                json.dumps(rows, separators=(",", ":")),
                source_file,
                source_file,
            )
            .fetchone()
        )
        return MergeCounts(received=len(rows), inserted=int(row[0]), updated=int(row[1]))

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()
