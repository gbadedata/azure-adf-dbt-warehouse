"""Command line entry point. In Azure, Container Apps jobs run these commands.

    python -m ingest init-db                 create control and raw tables (safe to repeat)
    python -m ingest api [--entities ...]    incremental load from the CRM API
    python -m ingest file PATH [PATH ...]    load event-registration CSV exports
    python -m ingest status                  show watermarks and the latest runs
    python -m ingest run-sql FILE [FILE ...] run T-SQL scripts (GO-separated), e.g. the grants

Exit code 0 means every load succeeded; 1 means at least one failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import logs
from .api_client import CrmApiClient
from .config import API_ENTITIES, ApiSettings, SqlSettings
from .pipeline import load_api_entity, load_registration_file
from .sql_store import SqlServerStore, connect

log = logging.getLogger("ingest")
SQL_DIR = Path(os.environ.get("INGEST_SQL_DIR", Path(__file__).resolve().parents[2] / "sql"))


def _store() -> SqlServerStore:
    return SqlServerStore(connect(SqlSettings.from_env()))


def cmd_init_db(_: argparse.Namespace) -> int:
    store = _store()
    applied = store.apply_scripts(SQL_DIR)
    log.info("schema applied", extra={"scripts": applied})
    store.close()
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    names = args.entities.split(",") if args.entities else list(API_ENTITIES)
    unknown = [n for n in names if n not in API_ENTITIES]
    if unknown:
        log.error("unknown entities", extra={"unknown": unknown})
        return 2
    client = CrmApiClient(
        settings.base_url,
        settings.client_id,
        settings.client_secret,
        page_size=settings.page_size,
        max_retries=settings.max_retries,
    )
    store = _store()
    failures = 0
    try:
        for name in names:
            try:
                load_api_entity(store, client, API_ENTITIES[name], settings.lookback)
            except Exception:
                failures += 1
                log.exception("api load failed", extra={"entity": name})
    finally:
        client.close()
        store.close()
    return 1 if failures else 0


def cmd_file(args: argparse.Namespace) -> int:
    store = _store()
    failures = 0
    try:
        for path in args.paths:
            try:
                load_registration_file(store, Path(path))
            except Exception:
                failures += 1
                log.exception("file load failed", extra={"file": str(path)})
    finally:
        store.close()
    return 1 if failures else 0


def cmd_run_sql(args: argparse.Namespace) -> int:
    from .sql_store import split_batches

    store = _store()
    try:
        cursor = store.connection.cursor()
        for path in args.paths:
            batches = split_batches(Path(path).read_text(encoding="utf-8"))
            for batch in batches:
                cursor.execute(batch)
            store.commit()
            log.info("script applied", extra={"file": str(path), "batches": len(batches)})
    finally:
        store.close()
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    store = _store()
    cursor = store.connection.cursor()
    watermarks = cursor.execute(
        "SELECT source_name, entity, CONVERT(NVARCHAR(40), high_watermark, 127) FROM ctl.watermarks ORDER BY 1, 2"
    ).fetchall()
    runs = cursor.execute(
        """
        SELECT TOP 10 entity, COALESCE(source_file, ''), status, rows_received, rows_inserted, rows_updated,
               CONVERT(NVARCHAR(40), started_at, 127)
        FROM ctl.ingestion_runs ORDER BY started_at DESC
        """
    ).fetchall()
    print(json.dumps({"watermarks": [list(r) for r in watermarks], "latest_runs": [list(r) for r in runs]}, indent=1))
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logs.configure(os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(
        prog="python -m ingest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    api = sub.add_parser("api")
    api.add_argument("--entities", help="comma-separated, default: all, in dependency order")
    api.set_defaults(func=cmd_api)
    files = sub.add_parser("file")
    files.add_argument("paths", nargs="+")
    files.set_defaults(func=cmd_file)
    sub.add_parser("status").set_defaults(func=cmd_status)
    run_sql = sub.add_parser("run-sql")
    run_sql.add_argument("paths", nargs="+")
    run_sql.set_defaults(func=cmd_run_sql)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
