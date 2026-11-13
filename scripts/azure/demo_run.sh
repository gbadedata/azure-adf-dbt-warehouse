#!/usr/bin/env bash
# Run the deployed prod pipeline three times, the way CI does locally:
#   1. day 1: the CRM source as it was on day one, and the day-one export file
#   2. day 2: the next day's changes, and the next export
#   3. replay: day 2 again, which should change nothing
# then recompute every campaign figure from the source and compare it with the
# prod marts. Run after deploy.sh.
set -euo pipefail
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=yes_without_prompt
cd "$(dirname "$0")/../.."
ROOT="$PWD"
source scripts/azure/lib.sh
APP="$(tf -json demo_source | json_field "['app']")"
SOURCE_URL="$(tf -json demo_source | json_field "['url']")"
DATA="data/generated"
python3 scripts/generate_data.py --out "$DATA" >/dev/null

use_day() {  # day1 | day2: point the demo CRM at that day's data and wait until it serves it
  local want
  want="$(python3 -c "import json; print(len(json.load(open('$DATA/$1/api.json'))['supporters']))")"
  az containerapp update --name "$APP" --resource-group "$RG" \
    --set-env-vars "MOCK_DATA_PATH=/app/data/$1/api.json" -o none
  for _ in $(seq 1 40); do
    got="$(curl -fsS "$SOURCE_URL/health" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["records"]["supporters"])' 2>/dev/null || true)"
    [ "$got" = "$want" ] && { echo "source is serving $1 ($want supporters)"; return 0; }
    sleep 6
  done
  echo "the demo source did not switch to $1" >&2
  exit 1
}

step "Starting from empty tables (demo deployment only)"
(
  export SQL_SERVER SQL_DATABASE="$DATABASE" SQL_AUTH=cli PYTHONPATH=src
  .venv-deploy/bin/python -m ingest run-sql scripts/azure/reset_demo.sql
)

step "Run 1: day one"
use_day day1
upload "$DATA"/files/day1/*.csv
run_pipeline

step "Run 2: day two"
use_day day2
upload "$DATA"/files/day2/*.csv
run_pipeline

step "Run 3: day two again (a replay should change nothing)"
upload "$DATA"/files/day2/*.csv
run_pipeline

step "Checking the prod marts against the source"
export SQL_SERVER SQL_DATABASE="$DATABASE" SQL_AUTH=cli PYTHONPATH=src
.venv-deploy/bin/python -m ingest status
.venv-deploy/bin/python scripts/verify_e2e.py --data "$DATA" --marts-schema marts --out results-azure.json
echo
echo "Archived files:"
az storage blob list --auth-mode login --account-name "$STORAGE" --container-name landing \
  --prefix prod/archive/ --query "[].name" -o tsv
