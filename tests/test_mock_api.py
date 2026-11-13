from pathlib import Path

from fastapi.testclient import TestClient

from mock_api.app import Settings, create_app, decode_cursor
from mock_api.generator import generate


def _client(data):
    return TestClient(create_app(Settings(data_path=Path("unused")), data=data))


def _token(client):
    response = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "local-client", "client_secret": "local-secret"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_generator_is_deterministic():
    assert generate(seed=11) == generate(seed=11)


def test_data_endpoints_need_a_token(source):
    client = _client(source["day1"])
    assert client.get("/v1/donations").status_code == 401
    assert client.get("/v1/donations", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_wrong_client_secret_is_refused(source):
    client = _client(source["day1"])
    response = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "local-client", "client_secret": "wrong"},
    )
    assert response.status_code == 401


def test_pages_return_every_record_once_in_order(source):
    client = _client(source["day1"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    seen, cursor = [], None
    while True:
        params = {"limit": 17, **({"cursor": cursor} if cursor else {})}
        body = client.get("/v1/donations", params=params, headers=headers).json()
        seen.extend(body["data"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    ids = [row["donation_id"] for row in seen]
    assert len(ids) == len(set(ids)) == len(source["day1"]["donations"])
    keys = [(row["updated_at"], row["donation_id"]) for row in seen]
    assert keys == sorted(keys)


def test_updated_since_is_inclusive(source):
    client = _client(source["day1"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    pivot = sorted(r["updated_at"] for r in source["day1"]["supporters"])[50]
    expected = sum(1 for r in source["day1"]["supporters"] if r["updated_at"] >= pivot)
    seen, cursor = 0, None
    while True:
        params = {"limit": 100, "updated_since": pivot, **({"cursor": cursor} if cursor else {})}
        body = client.get("/v1/supporters", params=params, headers=headers).json()
        seen += len(body["data"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == expected


def test_cursor_round_trips():
    from mock_api.app import encode_cursor

    moment, record_id = decode_cursor(encode_cursor("2026-11-01T05:58:00.000Z", "DON-0000001"))
    assert record_id == "DON-0000001"
    assert moment.isoformat() == "2026-11-01T05:58:00+00:00"
