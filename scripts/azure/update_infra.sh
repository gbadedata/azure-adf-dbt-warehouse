#!/usr/bin/env bash
# Apply infrastructure changes after `git pull`, keeping the images that are
# already deployed (no rebuild). Shows the plan and asks before applying.
set -euo pipefail
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=yes_without_prompt
cd "$(dirname "$0")/../../infra/terraform"
read_images() {
  terraform show -json | python3 -c '
import json, sys
state = json.load(sys.stdin)["values"]["root_module"]["resources"]
def image(address):
    for r in state:
        if r["address"] == address:
            return r["values"]["template"][0]["container"][0]["image"]
    return ""
print(image("azurerm_container_app_job.this[\"ingest-prod\"]"))
print(image("azurerm_container_app.demo_source[0]"))
'
}
mapfile -t images < <(read_images)
[ -n "${images[0]}" ] || { echo "No deployment found in this Terraform state: run deploy.sh." >&2; exit 1; }
echo "keeping job image : ${images[0]}"
echo "keeping demo image: ${images[1]:-(none)}"
terraform init -input=false >/dev/null
terraform plan -input=false -out tfplan -var "image=${images[0]}" -var "demo_source_image=${images[1]}"
read -r -p "Apply this plan? [y/N] " answer
[ "$answer" = "y" ] || { echo "stopped"; exit 1; }
terraform apply -input=false tfplan
