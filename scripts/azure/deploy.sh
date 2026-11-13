#!/usr/bin/env bash
# Deploy the warehouse to your own Azure subscription, with the synthetic CRM API
# as its source, so the whole thing can be run and checked in Azure.
#
#   ALERT_EMAILS=you@example.org,team@example.org bash scripts/azure/deploy.sh
#
# Needs: Azure CLI (signed in), Terraform >= 1.6, Python 3.11+ with venv, curl.
# Installs Microsoft ODBC Driver 18 if it is missing (asks for sudo).
# Asks before creating anything. Remove it all with scripts/azure/teardown.sh.
set -euo pipefail
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=yes_without_prompt
cd "$(dirname "$0")/../.."
ROOT="$PWD"
: "${ALERT_EMAILS:?set ALERT_EMAILS to one or more addresses, comma-separated, for alerts}"
EMAIL_LIST="$(python3 -c "import json,sys; print(json.dumps([e.strip() for e in sys.argv[1].split(',') if e.strip()]))" "$ALERT_EMAILS")"
LOCATION="${LOCATION:-uksouth}"
step() { printf '\n==> %s\n' "$*"; }

step "Checking tools and sign-in"
for tool in az terraform python3 curl git; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
az account show >/dev/null 2>&1 || az login --use-device-code
SUB="$(az account show --query id -o tsv)"
SUB_NAME="$(az account show --query name -o tsv)"
ME_ID="$(az ad signed-in-user show --query id -o tsv)"
ME_NAME="$(az ad signed-in-user show --query userPrincipalName -o tsv)"
MY_IP="$(curl -fsS https://api.ipify.org)"
echo "subscription : $SUB_NAME ($SUB)"
echo "SQL admin    : $ME_NAME"
echo "your IP      : $MY_IP (allowed through the SQL firewall)"
echo "region       : $LOCATION"
echo "alerts to    : $EMAIL_LIST"
read -r -p "Deploy into this subscription? [y/N] " answer
[ "$answer" = "y" ] || { echo "stopped"; exit 1; }

step "Registering the Azure resource providers this uses (safe to repeat)"
for namespace in Microsoft.App Microsoft.DataFactory Microsoft.Sql Microsoft.Storage \
                 Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.ManagedIdentity \
                 Microsoft.Insights Microsoft.AlertsManagement; do
  state="$(az provider show --namespace "$namespace" --query registrationState -o tsv 2>/dev/null || echo NotRegistered)"
  if [ "$state" != "Registered" ]; then
    echo "registering $namespace (can take a few minutes)"
    az provider register --namespace "$namespace" --wait
  fi
done
echo "all registered"

step "Python environment for the database steps"
if ! odbcinst -q -d -n "ODBC Driver 18 for SQL Server" >/dev/null 2>&1; then
  bash scripts/ci/install_odbc.sh
fi
python3 -m venv .venv-deploy
.venv-deploy/bin/pip install -q --upgrade pip
.venv-deploy/bin/pip install -q httpx==0.28.1 pyodbc==5.5.0 azure-identity==1.27.0

step "Terraform variables (infra/terraform/terraform.tfvars, git-ignored)"
cd infra/terraform
cat > terraform.tfvars <<VARS
subscription_id     = "$SUB"
location            = "$LOCATION"
sql_admin_login     = "$ME_NAME"
sql_admin_object_id = "$ME_ID"
client_ip_addresses = ["$MY_IP"]
alert_emails        = $EMAIL_LIST
deploy_demo_source  = true
# Schedules stay off for the demonstration: demo_run.sh starts each run itself.
daily_schedule = {
  dev  = { hour = 5, minute = 10, enabled = false }
  prod = { hour = 6, minute = 10, enabled = false }
}
VARS
terraform init -input=false

step "Creating the resource group and container registry first"
terraform apply -input=false -auto-approve -target=azurerm_container_registry.this
registry_field() {
  terraform show -json | python3 -c "
import json, sys
state = json.load(sys.stdin)
for r in state['values']['root_module']['resources']:
    if r['address'] == 'azurerm_container_registry.this':
        print(r['values']['$1'])
"
}
ACR="$(registry_field name)"
ACR_SERVER="$(registry_field login_server)"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
echo "registry: $ACR_SERVER, tag: $TAG"

step "Building both images"
build_image() {  # name dockerfile
  if az acr repository show-tags --name "$ACR" --repository "$1" --query "contains(@, '$TAG')" -o tsv 2>/dev/null | grep -q true; then
    echo "$1:$TAG is already in the registry"
    return 0
  fi
  if az acr build --registry "$ACR" --image "$1:$TAG" --file "$ROOT/$2" "$ROOT"; then
    return 0
  fi
  echo "az acr build failed; trying a local Docker build and push" >&2
  command -v docker >/dev/null || { echo "Docker is not installed either; see the message above" >&2; exit 1; }
  az acr login --name "$ACR"
  docker build -t "$ACR_SERVER/$1:$TAG" -f "$ROOT/$2" "$ROOT"
  docker push "$ACR_SERVER/$1:$TAG"
}
build_image warehouse Dockerfile
build_image mock-crm Dockerfile.mock

step "Planning everything else"
terraform plan -input=false -out tfplan \
  -var "image=$ACR_SERVER/warehouse:$TAG" \
  -var "demo_source_image=$ACR_SERVER/mock-crm:$TAG"
read -r -p "Apply this plan? [y/N] " answer
[ "$answer" = "y" ] || { echo "stopped; nothing beyond the registry was created"; exit 1; }
terraform apply -input=false tfplan

step "Tables, then database users for the managed identities"
SQL_SERVER="$(terraform output -raw sql_server)"
for env in prod dev; do
  database="$(terraform output -json databases | python3 -c "import json,sys; print(json.load(sys.stdin)['$env'])")"
  terraform output -json sql_grants | python3 -c "import json,sys; print(json.load(sys.stdin)['$env'])" > "grants_$env.sql"
  echo "-- $database"
  (
    cd "$ROOT"
    export SQL_SERVER SQL_DATABASE="$database" SQL_AUTH=cli PYTHONPATH=src
    .venv-deploy/bin/python -m ingest init-db
    .venv-deploy/bin/python -m ingest run-sql "infra/terraform/grants_$env.sql"
  )
done

step "Deployed"
terraform output
echo
echo "Next: bash scripts/azure/demo_run.sh"
