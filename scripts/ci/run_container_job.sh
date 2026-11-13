#!/usr/bin/env bash
# Start a Container Apps job and wait for it, the same way Data Factory does.
#   scripts/ci/run_container_job.sh <job-name> <resource-group> [timeout-minutes]
set -euo pipefail
job="$1"; group="$2"; timeout_minutes="${3:-60}"
execution="$(az containerapp job start --name "$job" --resource-group "$group" --query name -o tsv)"
echo "started $job execution $execution"
deadline=$(( $(date +%s) + timeout_minutes * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 30
  status="$(az containerapp job execution show --name "$job" --resource-group "$group" \
            --job-execution-name "$execution" --query properties.status -o tsv)"
  echo "$(date -u +%H:%M:%S) $execution: $status"
  case "$status" in
    Succeeded) exit 0 ;;
    Failed|Stopped|Degraded) echo "job $job ended with $status" >&2; exit 1 ;;
  esac
done
echo "timed out waiting for $job" >&2
exit 1
