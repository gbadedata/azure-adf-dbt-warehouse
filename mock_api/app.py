"""A stand-in for a supporter CRM's REST API.

It behaves the way real SaaS APIs tend to, because those are the behaviours an
ingestion job has to cope with:

* OAuth 2.0 client-credentials tokens that expire;
* keyset (cursor) pagination ordered by ``updated_at`` then ID;
* an ``updated_since`` filter for incremental loads;
* optional rate limiting (429 with ``Retry-After``) and transient 503s.

Run it with ``uvicorn mock_api.app:app``. Configuration comes from environment
variables, listed in ``Settings.from_env``.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ENTITIES = {
    "campaigns": "campaign_id",
    "supporters": "supporter_id",
    "donations": "donation_id",
}
MAX_PAGE_SIZE = 100


@dataclass
class Settings:
    data_path: Path
    client_id: str = "local-client"
    client_secret: str = "local-secret"
    token_ttl_seconds: int = 3600
    rate_limit_every: int = 0
    fail_every: int = 0
    retry_after_seconds: int = 1

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_path=Path(os.environ.get("MOCK_DATA_PATH", "data/generated/day1/api.json")),
            client_id=os.environ.get("MOCK_CLIENT_ID", "local-client"),
            client_secret=os.environ.get("MOCK_CLIENT_SECRET", "local-secret"),
            token_ttl_seconds=int(os.environ.get("MOCK_TOKEN_TTL_SECONDS", "3600")),
            rate_limit_every=int(os.environ.get("MOCK_RATE_LIMIT_EVERY", "0")),
            fail_every=int(os.environ.get("MOCK_FAIL_EVERY", "0")),
            retry_after_seconds=int(os.environ.get("MOCK_RETRY_AFTER_SECONDS", "1")),
        )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def encode_cursor(updated_at: str, record_id: str) -> str:
    raw = json.dumps([updated_at, record_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    padded = cursor + "=" * (-len(cursor) % 4)
    updated_at, record_id = json.loads(base64.urlsafe_b64decode(padded))
    return _parse_time(updated_at), record_id


def create_app(settings: Settings, data: dict | None = None) -> FastAPI:
    """Build the app. Pass ``data`` directly in tests, or let it read ``settings.data_path``."""
    source = data if data is not None else json.loads(settings.data_path.read_text(encoding="utf-8"))
    ordered = {
        entity: sorted(
            source[entity],
            key=lambda row, key=key: (_parse_time(row["updated_at"]), row[key]),
        )
        for entity, key in ENTITIES.items()
    }

    app = FastAPI(title="Mock supporter CRM API", version="1.0")
    tokens: dict[str, float] = {}
    counter = {"data_requests": 0}
    lock = threading.Lock()
    app.state.counter = counter
    app.state.tokens = tokens

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "records": {k: len(v) for k, v in ordered.items()}}

    @app.post("/oauth/token")
    async def token(request: Request) -> JSONResponse:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        if form.get("client_id") != settings.client_id or form.get("client_secret") != settings.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        access_token = secrets.token_urlsafe(24)
        with lock:
            tokens[access_token] = time.monotonic() + settings.token_ttl_seconds
        return JSONResponse(
            {"access_token": access_token, "token_type": "Bearer", "expires_in": settings.token_ttl_seconds}
        )

    @app.get("/v1/{entity}")
    def list_records(
        entity: str,
        request: Request,
        updated_since: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        header = request.headers.get("authorization", "")
        presented = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        expiry = tokens.get(presented)
        if not presented or expiry is None or expiry < time.monotonic():
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        if entity not in ENTITIES:
            return JSONResponse({"error": "not_found"}, status_code=404)

        with lock:
            counter["data_requests"] += 1
            count = counter["data_requests"]
        if settings.rate_limit_every and count % settings.rate_limit_every == 0:
            return JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": str(settings.retry_after_seconds)},
            )
        if settings.fail_every and count % settings.fail_every == 0:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)

        try:
            since = _parse_time(updated_since) if updated_since else None
            after = decode_cursor(cursor) if cursor else None
        except (ValueError, TypeError, json.JSONDecodeError):
            return JSONResponse({"error": "invalid_parameter"}, status_code=400)

        key = ENTITIES[entity]
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        rows = ordered[entity]
        selected = []
        for row in rows:
            row_time = _parse_time(row["updated_at"])
            if since is not None and row_time < since:
                continue
            if after is not None and (row_time, row[key]) <= after:
                continue
            selected.append(row)
            if len(selected) == limit + 1:
                break

        page = selected[:limit]
        next_cursor = None
        if len(selected) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last["updated_at"], last[key])
        return JSONResponse({"data": page, "next_cursor": next_cursor})

    return app


def _app_from_env() -> FastAPI:
    return create_app(Settings.from_env())


def __getattr__(name: str):  # lazily build ``app`` so importing the module has no side effects
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
