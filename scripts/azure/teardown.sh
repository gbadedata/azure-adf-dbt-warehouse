#!/usr/bin/env bash
# Remove everything deploy.sh created. Asks first.
set -euo pipefail
cd "$(dirname "$0")/../../infra/terraform"
RG="$(terraform output -raw resource_group 2>/dev/null || true)"
echo "This deletes resource group ${RG:-<unknown>} and everything in it, including both databases."
read -r -p "Type the resource group name to confirm: " answer
[ -n "$RG" ] && [ "$answer" = "$RG" ] || { echo "stopped"; exit 1; }
if ! terraform destroy -input=false -auto-approve -var "image=unused" -var "demo_source_image=unused"; then
  echo "terraform destroy did not finish; deleting the resource group directly" >&2
  az group delete --name "$RG" --yes
fi
rm -f terraform.tfvars tfplan grants_*.sql
echo "Removed. Check Cost Management in a day or two: it should show nothing new."
