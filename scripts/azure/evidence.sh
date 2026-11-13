#!/usr/bin/env bash
# Print a record of the deployment's recent activity: pipeline runs (including the
# child pipelines), job executions and any alerts that fired. Read-only.
set -euo pipefail
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=yes_without_prompt
cd "$(dirname "$0")/../.."
tf() { terraform -chdir=infra/terraform output "$@"; }
RG="$(tf -raw resource_group)"; ADF="$(tf -raw data_factory)"; SUB="$(tf -raw subscription_id)"
FACTORY="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DataFactory/factories/$ADF"
SINCE="$(date -u -d '-6 hours' +%FT%TZ)"; UNTIL="$(date -u -d '+1 hour' +%FT%TZ)"

echo "## Pipeline runs (last 6 hours)"
az rest --method post --url "$FACTORY/queryPipelineRuns?api-version=2018-06-01" \
  --body "{\"lastUpdatedAfter\":\"$SINCE\",\"lastUpdatedBefore\":\"$UNTIL\"}" \
  --query "sort_by(value, &runStart)[].{start:runStart, pipeline:pipelineName, status:status, seconds:to_string(durationInMs)}" -o table

for job in $(tf -json jobs | python3 -c 'import json,sys; print(" ".join(v for k, v in json.load(sys.stdin).items() if k.endswith("prod")))'); do
  echo; echo "## Executions of $job"
  az containerapp job execution list --name "$job" --resource-group "$RG" \
    --query "sort_by([], &properties.startTime)[].{start:properties.startTime, end:properties.endTime, status:properties.status}" -o table
done

echo; echo "## Alerts fired (last day)"
az rest --method get \
  --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.AlertsManagement/alerts?api-version=2019-05-05-preview&timeRange=1d" \
  --query "value[].{fired:properties.essentials.startDateTime, rule:properties.essentials.alertRule, severity:properties.essentials.severity, state:properties.essentials.monitorCondition}" -o table
