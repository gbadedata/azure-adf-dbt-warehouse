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
tf() { terraform -chdir=infra/terraform output "$@"; }
json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

RG="$(tf -raw resource_group)"
ADF="$(tf -raw data_factory)"
SUB="$(tf -raw subscription_id)"
STORAGE="$(tf -raw landing_storage_account)"
SQL_SERVER="$(tf -raw sql_server)"
DATABASE="$(tf -json databases | json_field "['prod']")"
APP="$(tf -json demo_source | json_field "['app']")"
SOURCE_URL="$(tf -json demo_source | json_field "['url']")"
FACTORY="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DataFactory/factories/$ADF"
DATA="data/generated"
python3 scripts/generate_data.py --out "$DATA" >/dev/null
step() { printf '\n==> %s\n' "$*"; }

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

upload() {
  az storage blob upload --auth-mode login --account-name "$STORAGE" --container-name landing \
    --name "prod/incoming/event_registrations/$(basename "$1")" --file "$1" --overwrite -o none
  echo "uploaded $(basename "$1")"
}

show_failures() {
  az rest --method post --url "$FACTORY/pipelineruns/$1/queryActivityruns?api-version=2018-06-01" \
    --body "{\"lastUpdatedAfter\":\"$(date -u -d '-6 hours' +%FT%TZ)\",\"lastUpdatedBefore\":\"$(date -u -d '+1 hour' +%FT%TZ)\"}" \
    --query "value[?status=='Failed'].{activity:activityName, error:error.message}" -o json
}

run_pipeline() {
  local run status
  run="$(az rest --method post --url "$FACTORY/pipelines/pl_daily_prod/createRun?api-version=2018-06-01" --body '{}' --query runId -o tsv)"
  echo "pl_daily_prod run $run"
  while :; do
    sleep 30
    status="$(az rest --method get --url "$FACTORY/pipelineruns/$run?api-version=2018-06-01" --query status -o tsv)"
    echo "  $(date +%H:%M:%S)  $status"
    case "$status" in
      Succeeded) return 0 ;;
      Failed|Cancelled) show_failures "$run"; exit 1 ;;
    esac
  done
}

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
