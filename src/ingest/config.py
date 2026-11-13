"""Settings, read from environment variables so the same image runs in CI, dev and prod."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class Entity:
    """One API resource and where it lands in the warehouse."""

    name: str
    table: str
    key: str
    updated_field: str = "updated_at"


# Loaded in this order, so reference data arrives before the facts that use it.
API_ENTITIES: dict[str, Entity] = {
    "campaigns": Entity("campaigns", "crm_campaigns", "campaign_id"),
    "supporters": Entity("supporters", "crm_supporters", "supporter_id"),
    "donations": Entity("donations", "crm_donations", "donation_id"),
}
API_SOURCE = "crm"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class SqlSettings:
    server: str
    database: str
    auth: str = "msi"  # sql | msi | cli | auto
    port: int = 1433
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    driver: str = "ODBC Driver 18 for SQL Server"
    trust_server_certificate: bool = False
    managed_identity_client_id: str | None = None

    @classmethod
    def from_env(cls) -> SqlSettings:
        return cls(
            server=os.environ["SQL_SERVER"],
            database=os.environ["SQL_DATABASE"],
            auth=os.environ.get("SQL_AUTH", "msi").lower(),
            port=int(os.environ.get("SQL_PORT", "1433")),
            user=os.environ.get("SQL_USER"),
            password=os.environ.get("SQL_PASSWORD"),
            driver=os.environ.get("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            trust_server_certificate=_bool(os.environ.get("SQL_TRUST_SERVER_CERTIFICATE")),
            managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
        )


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    client_id: str
    client_secret: str = field(repr=False)
    page_size: int = 100
    max_retries: int = 5
    lookback: timedelta = timedelta(minutes=30)

    @classmethod
    def from_env(cls) -> ApiSettings:
        return cls(
            base_url=os.environ["CRM_API_BASE_URL"].rstrip("/"),
            client_id=os.environ["CRM_API_CLIENT_ID"],
            client_secret=os.environ["CRM_API_CLIENT_SECRET"],
            page_size=int(os.environ.get("CRM_PAGE_SIZE", "100")),
            max_retries=int(os.environ.get("CRM_MAX_RETRIES", "5")),
            lookback=timedelta(minutes=int(os.environ.get("INGEST_LOOKBACK_MINUTES", "30"))),
        )
