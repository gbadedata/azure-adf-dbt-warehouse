#!/usr/bin/env bash
# Show what happens when a bad file arrives. Uploads an export with the wrong
# header and runs the daily pipeline, which should fail: the file load fails,
# dbt is held back, the file stays in incoming, and the failure alert fires.
# Then removes the bad file so later runs are clean. Run after deploy.sh and
# before demo_run.sh (demo_run.sh empties the tables first).
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/azure/lib.sh
BAD="event_registrations_broken_header.csv"

step "Uploading an export whose header does not match the agreed columns"
tmp="$(mktemp)"
printf 'registration_id,event,signed_up\nREG-BAD-1,Walk Together London,2026-11-13\n' > "$tmp"
upload "$tmp" "$BAD"
rm -f "$tmp"

step "Running pl_daily_prod (expected to fail)"
out="$(start_and_wait)"; run="$(sed -n 1p <<<"$out")"; status="$(sed -n 2p <<<"$out")"
echo "final status: $status"
[ "$status" = "Failed" ] || { echo "expected the run to fail, but it ended $status" >&2; exit 1; }
echo "activities:"
activity_runs "$run"

step "Checking the file was kept for the next run"
az storage blob list --auth-mode login --account-name "$STORAGE" --container-name landing \
  --prefix "$INCOMING/" --query "[].name" -o tsv --only-show-errors

step "Waiting for the failure alert (usually 2 to 5 minutes)"
since="$(date -u +%FT%TZ -d '-15 minutes')"
for _ in $(seq 1 20); do
  fired="$(az rest --method get \
    --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.AlertsManagement/alerts?api-version=2019-05-05-preview&timeRange=1h" \
    --query "value[?contains(properties.essentials.alertRule, 'alert-adf-pipeline-failed')].properties.essentials.startDateTime" -o tsv \
    | python3 -c "import sys; print(' '.join(t for t in sys.stdin.read().split() if t >= '$since'))")"
  if [ -n "$fired" ]; then echo "alert fired at $fired"; break; fi
  sleep 30
done
[ -n "${fired:-}" ] || echo "no alert yet; check again with scripts/azure/evidence.sh"

step "Removing the bad file so the next run is clean"
az storage blob delete --auth-mode login --account-name "$STORAGE" --container-name landing \
  --name "$INCOMING/$BAD" -o none --only-show-errors
echo "removed $BAD"
echo
echo "Next: bash scripts/azure/demo_run.sh"
