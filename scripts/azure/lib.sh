# Shared by the demo scripts. Source it from the repository root.
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=yes_without_prompt
tf() { terraform -chdir=infra/terraform output "$@"; }
json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
step() { printf '\n==> %s\n' "$*"; }

RG="$(tf -raw resource_group 2>/dev/null)" || { echo "Nothing is deployed yet: run scripts/azure/deploy.sh first." >&2; exit 1; }
tf -json databases >/dev/null 2>&1 || { echo "The deployment did not finish: run scripts/azure/deploy.sh again." >&2; exit 1; }
ADF="$(tf -raw data_factory)"
SUB="$(tf -raw subscription_id)"
STORAGE="$(tf -raw landing_storage_account)"
SQL_SERVER="$(tf -raw sql_server)"
DATABASE="$(tf -json databases | json_field "['prod']")"
FACTORY="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DataFactory/factories/$ADF"
INCOMING="prod/incoming/event_registrations"

upload() {  # local-file [blob-name]
  az storage blob upload --auth-mode login --account-name "$STORAGE" --container-name landing \
    --name "$INCOMING/${2:-$(basename "$1")}" --file "$1" --overwrite -o none --only-show-errors
  echo "uploaded ${2:-$(basename "$1")}"
}

activity_runs() {  # run-id [status filter]
  local filter="${2:-}"
  az rest --method post --url "$FACTORY/pipelineruns/$1/queryActivityruns?api-version=2018-06-01" \
    --body "{\"lastUpdatedAfter\":\"$(date -u -d '-6 hours' +%FT%TZ)\",\"lastUpdatedBefore\":\"$(date -u -d '+1 hour' +%FT%TZ)\"}" \
    --query "value[${filter}].{activity:activityName, status:status, error:error.message}" -o json
}

start_and_wait() {  # prints the run id, then the final status, one per line
  local run status
  run="$(az rest --method post --url "$FACTORY/pipelines/pl_daily_prod/createRun?api-version=2018-06-01" --body '{}' --query runId -o tsv)"
  echo "pl_daily_prod run $run" >&2
  while :; do
    sleep 30
    status="$(az rest --method get --url "$FACTORY/pipelineruns/$run?api-version=2018-06-01" --query status -o tsv)"
    echo "  $(date +%H:%M:%S)  $status" >&2
    case "$status" in Succeeded|Failed|Cancelled) break ;; esac
  done
  printf '%s\n%s\n' "$run" "$status"
}

run_pipeline() {  # must succeed
  local out run status
  out="$(start_and_wait)"; run="$(sed -n 1p <<<"$out")"; status="$(sed -n 2p <<<"$out")"
  if [ "$status" != "Succeeded" ]; then
    activity_runs "$run" "?status=='Failed'"
    exit 1
  fi
}
