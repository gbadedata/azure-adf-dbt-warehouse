from datetime import timedelta

import pytest

from conftest import make_client
from ingest.config import API_ENTITIES
from ingest.pipeline import FileFormatError, load_api_entity, read_registrations
from ingest.sql_store import parse_iso

LOOKBACK = timedelta(minutes=30)


def load_all(store, data, lookback=LOOKBACK, **settings):
    client = make_client(data, **settings)
    return {name: load_api_entity(store, client, entity, lookback) for name, entity in API_ENTITIES.items()}


def table_state(store, entity_name):
    entity = API_ENTITIES[entity_name]
    return {key: row["payload"] for key, row in store.tables[entity.table].items()}


def source_state(snapshot, entity_name):
    entity = API_ENTITIES[entity_name]
    return {row[entity.key]: row for row in snapshot[entity_name]}


def test_first_run_loads_everything_and_sets_the_watermark(store, source):
    runs = load_all(store, source["day1"])
    for name in API_ENTITIES:
        assert table_state(store, name) == source_state(source["day1"], name)
        assert runs[name].rows_inserted == len(source["day1"][name])
        assert runs[name].requested_since is None
    highest = max(parse_iso(r["updated_at"]) for r in source["day1"]["donations"])
    assert store.get_watermark("crm", "donations") == highest


def test_second_run_matches_the_source_exactly_including_late_commits(store, source):
    load_all(store, source["day1"])
    day1_watermark = store.get_watermark("crm", "donations")
    runs = load_all(store, source["day2"])
    assert runs["donations"].requested_since == day1_watermark - LOOKBACK
    for name in API_ENTITIES:
        assert table_state(store, name) == source_state(source["day2"], name)
    late = set(source["meta"]["late_commit_donation_ids"])
    assert late <= set(table_state(store, "donations"))
    # Only the changed rows, plus the overlap window, were read again.
    assert runs["donations"].rows_received < len(source["day2"]["donations"]) / 2


def test_without_an_overlap_window_late_commits_are_lost(store, source):
    load_all(store, source["day1"], lookback=timedelta(0))
    load_all(store, source["day2"], lookback=timedelta(0))
    missing = set(source_state(source["day2"], "donations")) - set(table_state(store, "donations"))
    assert missing == set(source["meta"]["late_commit_donation_ids"])


def test_replaying_a_run_changes_nothing(store, source):
    load_all(store, source["day1"])
    load_all(store, source["day2"])
    replay = load_all(store, source["day2"])
    for run in replay.values():
        assert run.rows_inserted == 0
        assert run.rows_updated == 0
        assert run.status == "succeeded"


def test_a_failed_run_keeps_the_watermark_and_the_next_run_recovers(store, source, monkeypatch):
    load_all(store, source["day1"])
    before = store.get_watermark("crm", "donations")
    client = make_client(source["day2"], page_size=10)
    original = client.iter_pages

    def fail_after_first_page(entity, since):
        for number, page in enumerate(original(entity, since)):
            if number == 1:
                raise RuntimeError("connection reset")
            yield page

    monkeypatch.setattr(client, "iter_pages", fail_after_first_page)
    with pytest.raises(RuntimeError):
        load_api_entity(store, client, API_ENTITIES["donations"], LOOKBACK)

    failed = [run for run in store.runs.values() if run.status == "failed"]
    assert len(failed) == 1 and "connection reset" in failed[0].error_message
    assert store.get_watermark("crm", "donations") == before

    load_all(store, source["day2"])
    assert table_state(store, "donations") == source_state(source["day2"], "donations")


def test_registration_files_must_have_the_agreed_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("registration_id,event\nREG-1,Walk\n", encoding="utf-8")
    with pytest.raises(FileFormatError):
        read_registrations(bad)


def test_repeated_registrations_in_one_file_keep_the_last(tmp_path):
    from ingest.pipeline import REGISTRATION_COLUMNS

    path = tmp_path / "events.csv"
    header = ",".join(REGISTRATION_COLUMNS)
    first = "REG-1,Walk,2026-09-20,CMP-004,a@example.org,2026-08-01T10:00:00.000Z,standard,100.00"
    second = "REG-1,Walk,2026-09-20,CMP-004,a@example.org,2026-08-01T10:00:00.000Z,family,150.00"
    path.write_text("\n".join([header, first, second]) + "\n", encoding="utf-8")
    rows = read_registrations(path)
    assert len(rows) == 1 and rows[0]["ticket_type"] == "family"
