#!/usr/bin/env bash
# End to end, the way production runs, against a SQL Server you point it at:
#   two days of source data, loaded incrementally, replayed, then dbt build and
#   an independent check of every campaign figure.
#
#   SQL_SERVER=localhost SQL_USER=sa SQL_PASSWORD=... scripts/run_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export SQL_SERVER="${SQL_SERVER:-localhost}"
export SQL_DATABASE="${SQL_DATABASE:-warehouse_ci}"
export SQL_AUTH=sql
export SQL_USER="${SQL_USER:-sa}"
export SQL_TRUST_SERVER_CERTIFICATE=true
export PYTHONPATH="src:."
export CRM_API_BASE_URL="http://127.0.0.1:8765"
export CRM_API_CLIENT_ID=local-client
export CRM_API_CLIENT_SECRET=local-secret
export INGEST_LOOKBACK_MINUTES=30
RUN_ID="${RUN_ID:-local}"
SCHEMA="ci_${RUN_ID}"
DATA="${DATA:-data/generated}"

api_pid=""
start_api() {
  # Rate-limit every 7th request so the retry path runs for real.
  MOCK_DATA_PATH="$DATA/$1/api.json" MOCK_RATE_LIMIT_EVERY=7 MOCK_RETRY_AFTER_SECONDS=1 \
    python -m uvicorn mock_api.app:app --port 8765 --log-level warning &
  api_pid=$!
  for _ in $(seq 1 30); do
    curl -fsS "$CRM_API_BASE_URL/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "mock API did not start" >&2
  exit 1
}
stop_api() { [ -n "$api_pid" ] && kill "$api_pid" && wait "$api_pid" 2>/dev/null || true; api_pid=""; }
trap stop_api EXIT

echo "::group::Generate source data"
python scripts/generate_data.py --out "$DATA"
echo "::endgroup::"

echo "::group::Create database and apply schema (twice, to prove it is re-runnable)"
python scripts/create_database.py "$SQL_DATABASE"
python -m ingest init-db
python -m ingest init-db
echo "::endgroup::"

echo "::group::Day 1: full load"
start_api day1
python -m ingest api
python -m ingest file "$DATA"/files/day1/*.csv
stop_api
echo "::endgroup::"

echo "::group::Day 2: incremental load"
start_api day2
python -m ingest api
python -m ingest file "$DATA"/files/day2/*.csv
echo "::endgroup::"

echo "::group::Replay day 2: nothing should change"
python -m ingest api
python -m ingest file "$DATA"/files/day2/*.csv
stop_api
python -m ingest status
echo "::endgroup::"

echo "::group::dbt build and source freshness"
export DBT_TARGET=ci DBT_SCHEMA="$SCHEMA" DBT_PROFILES_DIR="$PWD/dbt"
dbt source freshness --project-dir dbt
dbt build --project-dir dbt
echo "::endgroup::"

echo "::group::Compare the marts with the source"
python scripts/verify_e2e.py --data "$DATA" --marts-schema "${SCHEMA}_marts" --out results.json
echo "::endgroup::"

echo "::group::Clean up the CI schemas"
dbt run-operation drop_schemas_with_prefix --project-dir dbt --args "{prefix: ${SCHEMA}_}"
left="$(python - <<PY
import sys
sys.path.insert(0, "src")
from ingest.config import SqlSettings
from ingest.sql_store import connect
c = connect(SqlSettings.from_env())
print(c.cursor().execute("SELECT COUNT(*) FROM sys.schemas WHERE name LIKE REPLACE('${SCHEMA}_', '_', '[_]') + '%'").fetchone()[0])
PY
)"
[ "$left" = "0" ] || { echo "schemas left behind: $left" >&2; exit 1; }
echo "::endgroup::"
echo "end-to-end run passed"
