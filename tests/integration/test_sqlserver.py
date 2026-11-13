"""The same load rules, against a real SQL Server (run in CI with a SQL Server 2022 container).

Set SQLSERVER_TEST_SERVER, SQLSERVER_TEST_USER and SQLSERVER_TEST_PASSWORD to run these.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import make_client
from ingest.config import API_ENTITIES, SqlSettings
from ingest.pipeline import REGISTRATION_COLUMNS, load_api_entity, load_registration_file
from ingest.sql_store import SqlServerStore, connect

pytestmark = pytest.mark.sqlserver
SERVER = os.environ.get("SQLSERVER_TEST_SERVER")
DATABASE = "ingest_it"
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
LOOKBACK = timedelta(minutes=30)

if not SERVER:
    pytest.skip("SQLSERVER_TEST_SERVER not set", allow_module_level=True)


def _settings(database: str) -> SqlSettings:
    return SqlSettings(
        server=SERVER,
        database=database,
        auth="sql",
        user=os.environ.get("SQLSERVER_TEST_USER", "sa"),
        password=os.environ["SQLSERVER_TEST_PASSWORD"],
        driver=os.environ.get("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
        trust_server_certificate=True,
    )


@pytest.fixture
def store():
    master = connect(_settings("master"))
    master.autocommit = True
    master.cursor().execute(
        f"IF DB_ID('{DATABASE}') IS NOT NULL BEGIN "
        f"ALTER DATABASE [{DATABASE}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{DATABASE}]; END; "
        f"CREATE DATABASE [{DATABASE}];"
    )
    master.close()
    sql_store = SqlServerStore(connect(_settings(DATABASE)))
    yield sql_store
    sql_store.close()


def _load_all(store, data):
    client = make_client(data, page_size=40)
    return {name: load_api_entity(store, client, entity, LOOKBACK) for name, entity in API_ENTITIES.items()}


def _raw(store, table):
    rows = store.connection.cursor().execute(f"SELECT record_id, payload FROM raw.{table}").fetchall()
    return {record_id: json.loads(payload) for record_id, payload in rows}


def _expected(snapshot, name):
    key = API_ENTITIES[name].key
    return {row[key]: row for row in snapshot[name]}


def test_scripts_can_be_applied_twice(store):
    assert store.apply_scripts(SQL_DIR) == store.apply_scripts(SQL_DIR)
    tables = {
        row[0]
        for row in store.connection.cursor()
        .execute("SELECT TABLE_SCHEMA + '.' + TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
        .fetchall()
    }
    assert {"ctl.watermarks", "ctl.ingestion_runs", "raw.crm_donations", "raw.event_registrations"} <= tables


def test_incremental_loads_match_the_source_and_replays_change_nothing(store, source):
    store.apply_scripts(SQL_DIR)
    _load_all(store, source["day1"])
    for name, entity in API_ENTITIES.items():
        assert _raw(store, entity.table) == _expected(source["day1"], name)

    day2 = _load_all(store, source["day2"])
    for name, entity in API_ENTITIES.items():
        assert _raw(store, entity.table) == _expected(source["day2"], name)
    assert set(source["meta"]["late_commit_donation_ids"]) <= set(_raw(store, "crm_donations"))
    assert day2["donations"].rows_received < len(source["day2"]["donations"])

    replay = _load_all(store, source["day2"])
    assert all(run.rows_inserted == 0 and run.rows_updated == 0 for run in replay.values())

    statuses = store.connection.cursor().execute("SELECT status, COUNT(*) FROM ctl.ingestion_runs GROUP BY status")
    assert dict(statuses.fetchall()) == {"succeeded": 9}


def test_watermark_only_moves_forward(store, source):
    store.apply_scripts(SQL_DIR)
    _load_all(store, source["day2"])
    after_day2 = store.get_watermark("crm", "donations")
    store.set_watermark("crm", "donations", after_day2 - timedelta(days=3))
    store.commit()
    assert store.get_watermark("crm", "donations") == after_day2


def test_failed_run_is_logged_and_leaves_the_watermark(store, source, monkeypatch):
    store.apply_scripts(SQL_DIR)
    _load_all(store, source["day1"])
    before = store.get_watermark("crm", "donations")
    client = make_client(source["day2"], page_size=5)
    original = client.iter_pages

    def broken(entity, since):
        for number, page in enumerate(original(entity, since)):
            if number == 1:
                raise RuntimeError("connection reset by peer")
            yield page

    monkeypatch.setattr(client, "iter_pages", broken)
    with pytest.raises(RuntimeError):
        load_api_entity(store, client, API_ENTITIES["donations"], LOOKBACK)
    row = (
        store.connection.cursor()
        .execute("SELECT status, error_message FROM ctl.ingestion_runs WHERE status = 'failed'")
        .fetchone()
    )
    assert row[0] == "failed" and "connection reset" in row[1]
    assert store.get_watermark("crm", "donations") == before

    _load_all(store, source["day2"])
    assert _raw(store, "crm_donations") == _expected(source["day2"], "donations")


def _write(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTRATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_registration_files_upsert_on_registration_id(store, source, tmp_path):
    store.apply_scripts(SQL_DIR)
    day1 = _write(tmp_path / "event_registrations_20261101.csv", source["files"]["day1"])
    day2 = _write(tmp_path / "event_registrations_20261102.csv", source["files"]["day2"])

    first = load_registration_file(store, day1)
    assert first.rows_inserted == len(source["files"]["day1"])

    second = load_registration_file(store, day2)
    changed = set(source["meta"]["changed_registration_ids"])
    day1_ids = {row["registration_id"] for row in source["files"]["day1"]}
    new = [row for row in source["files"]["day2"] if row["registration_id"] not in day1_ids]
    assert second.rows_inserted == len(new)
    assert second.rows_updated == len(changed)

    again = load_registration_file(store, day2)
    assert again.rows_inserted == 0 and again.rows_updated == 0

    total = store.connection.cursor().execute("SELECT COUNT(*) FROM raw.event_registrations").fetchone()[0]
    assert total == len(day1_ids) + len(new)
