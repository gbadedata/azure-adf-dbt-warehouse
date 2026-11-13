"""HTTP client for the supporter CRM API: tokens, pagination and retries."""

from __future__ import annotations

import email.utils
import logging
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """The API returned something the client cannot recover from."""


@dataclass
class Page:
    records: list[dict]
    next_cursor: str | None


@dataclass
class ClientStats:
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    token_refreshes: int = 0
    waits_seconds: list[float] = field(default_factory=list)


def _format_since(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _retry_after_seconds(value: str | None) -> float | None:
    """Read ``Retry-After`` as seconds or as an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


class CrmApiClient:
    """Fetches records changed since a point in time, one page at a time.

    * Tokens come from the OAuth 2.0 client-credentials flow and are refreshed
      before they expire, or once on a 401.
    * 429 responses wait for ``Retry-After``; 5xx responses and network errors
      back off exponentially with jitter. Other 4xx responses fail at once,
      because retrying a bad request only hides it.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.Client | None = None,
        page_size: int = 100,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        max_wait: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=timeout)
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_wait = max_wait
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.stats = ClientStats()

    # -- authentication -----------------------------------------------------------
    def _fetch_token(self) -> None:
        response = self._send(
            "POST",
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            authenticated=False,
        )
        body = response.json()
        self._token = body["access_token"]
        # Refresh a minute early so a token never expires mid-request.
        self._token_expires_at = time.monotonic() + max(0, int(body.get("expires_in", 3600)) - 60)
        self.stats.token_refreshes += 1

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self._fetch_token()
        return f"Bearer {self._token}"

    # -- transport ----------------------------------------------------------------
    def _wait(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            delay = min(retry_after, self._max_wait)
        else:
            delay = min(self._backoff_base * (2**attempt), self._max_wait)
            delay *= random.uniform(0.8, 1.2)
        self.stats.retries += 1
        self.stats.waits_seconds.append(round(delay, 3))
        self._sleep(delay)

    def _send(self, method: str, path: str, *, authenticated: bool = True, **kwargs) -> httpx.Response:
        refreshed = False
        attempt = 0
        while True:
            headers = {"Accept": "application/json"}
            if authenticated:
                headers["Authorization"] = self._bearer()
            self.stats.requests += 1
            try:
                response = self._http.request(method, self._base_url + path, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ApiError(f"{method} {path} failed after {attempt + 1} attempts: {exc}") from exc
                log.warning("transport error, retrying", extra={"path": path, "error": str(exc)})
                self._wait(attempt, None)
                attempt += 1
                continue

            if response.status_code == 401 and authenticated and not refreshed:
                # The token may have been revoked or expired early: get a new one, once.
                self._token = None
                refreshed = True
                continue
            if response.status_code in RETRYABLE_STATUS:
                if attempt >= self._max_retries:
                    raise ApiError(
                        f"{method} {path} still returning {response.status_code} after {attempt + 1} attempts"
                    )
                retry_after = None
                if response.status_code == 429:
                    self.stats.rate_limited += 1
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                log.warning(
                    "retryable response",
                    extra={"path": path, "status": response.status_code, "attempt": attempt + 1},
                )
                self._wait(attempt, retry_after)
                attempt += 1
                continue
            if response.status_code >= 400:
                raise ApiError(f"{method} {path} returned {response.status_code}: {response.text[:300]}")
            return response

    # -- public API -----------------------------------------------------------------
    def iter_pages(self, entity: str, updated_since: datetime | None) -> Iterator[Page]:
        """Yield every page of records changed at or after ``updated_since``."""
        params: dict[str, str | int] = {"limit": self._page_size}
        if updated_since is not None:
            params["updated_since"] = _format_since(updated_since)
        cursor: str | None = None
        while True:
            if cursor:
                params["cursor"] = cursor
            body = self._send("GET", f"/v1/{entity}", params=params).json()
            yield Page(records=body.get("data", []), next_cursor=body.get("next_cursor"))
            cursor = body.get("next_cursor")
            if not cursor:
                return

    def close(self) -> None:
        self._http.close()
