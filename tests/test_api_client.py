import pytest

from conftest import make_client
from ingest.api_client import ApiError, _retry_after_seconds


def _all(client, entity, since=None):
    rows = []
    for page in client.iter_pages(entity, since):
        rows.extend(page.records)
    return rows


def test_reads_every_record(source):
    client = make_client(source["day1"])
    rows = _all(client, "donations")
    assert {r["donation_id"] for r in rows} == {r["donation_id"] for r in source["day1"]["donations"]}
    assert client.stats.retries == 0


def test_waits_for_retry_after_on_429(source):
    sleeps: list[float] = []
    client = make_client(source["day1"], sleeps=sleeps, rate_limit_every=3, retry_after_seconds=2)
    rows = _all(client, "supporters")
    assert len(rows) == len(source["day1"]["supporters"])
    assert client.stats.rate_limited > 0
    assert sleeps and all(wait == 2.0 for wait in sleeps)


def test_backs_off_and_recovers_from_503(source):
    sleeps: list[float] = []
    client = make_client(source["day1"], sleeps=sleeps, fail_every=4)
    rows = _all(client, "donations")
    assert len(rows) == len(source["day1"]["donations"])
    assert client.stats.retries > 0
    assert all(0 < wait <= 60 for wait in sleeps)


def test_gives_up_when_the_api_keeps_failing(source):
    client = make_client(source["day1"], fail_every=1, max_retries=3)
    with pytest.raises(ApiError, match="503"):
        _all(client, "donations")
    assert client.stats.requests == 1 + 4  # token, then the first try and three retries


def test_refreshes_a_rejected_token_once(source):
    client = make_client(source["day1"])
    _all(client, "campaigns")
    client._token = "revoked"
    rows = _all(client, "campaigns")
    assert len(rows) == len(source["day1"]["campaigns"])
    assert client.stats.token_refreshes == 2


def test_client_errors_are_not_retried(source):
    client = make_client(source["day1"])
    with pytest.raises(ApiError, match="404"):
        _all(client, "volunteers")
    assert client.stats.retries == 0


def test_retry_after_parsing():
    assert _retry_after_seconds("3") == 3.0
    assert _retry_after_seconds(None) is None
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert _retry_after_seconds("soon") is None
