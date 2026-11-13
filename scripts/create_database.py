"""Create a database on the server in SQL_SERVER if it does not exist (CI and local use)."""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingest.config import SqlSettings  # noqa: E402
from ingest.sql_store import connect  # noqa: E402


def main() -> None:
    settings = SqlSettings.from_env()
    name = sys.argv[1] if len(sys.argv) > 1 else settings.database
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise SystemExit(f"refusing odd database name {name!r}")
    connection = connect(replace(settings, database="master"))
    connection.autocommit = True
    connection.cursor().execute(f"IF DB_ID(N'{name}') IS NULL CREATE DATABASE [{name}]")
    connection.close()
    print(f"database {name} ready")


if __name__ == "__main__":
    main()
