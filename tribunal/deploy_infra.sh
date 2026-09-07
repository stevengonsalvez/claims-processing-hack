#!/bin/bash
# Deploy the trimmed Challenge 0 template (no APIM / ACR / ACA / Cosmos / Key Vault /
# Doc Intelligence) into an existing or new resource group, then populate .env via
# challenge-0/get-keys.sh plus the App Insights connection string it does not write.
# Uses `az rest` for the deployment: az CLI 2.76 masks ARM validation errors with
# "The content for this response was already consumed".
#
#   tribunal/deploy_infra.sh [resource-group] [location]
set -euo pipefail
cd "$(dirname "$0")/.."
RG=${1:-rg-claims-tribunal}; LOC=${2:-swedencentral}; NAME=CustomDeployment-tribunal
SUB=$(az account show --query id -o tsv)
az group show -n "$RG" -o none 2>/dev/null || az group create -n "$RG" -l "$LOC" -o none
BODY=$(mktemp); jq -n --slurpfile t challenge-0/infra/azuredeploy.min.json --arg loc "$LOC" \
  '{properties:{mode:"Incremental",template:$t[0],parameters:{location:{value:$loc}}}}' > "$BODY"
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Resources/deployments/$NAME"
echo "deploying to $RG ($LOC) ..."
az rest --method put --url "$URL?api-version=2022-09-01" --body @"$BODY" -o none
until st=$(az deployment group show -g "$RG" -n "$NAME" --query properties.provisioningState -o tsv); [ "$st" = Succeeded ]; do
  case "$st" in Failed|Canceled) az deployment operation group list -g "$RG" -n "$NAME" \
    --query "[?properties.provisioningState=='Failed'].properties.statusMessage.error.message" -o tsv; exit 1;; esac
  echo "  $st"; sleep 20
done
(cd challenge-0 && bash get-keys.sh --resource-group "$RG")
AI=$(az resource list -g "$RG" --resource-type Microsoft.Insights/components --query '[0].name' -o tsv)
CS=$(az resource show -g "$RG" -n "$AI" --resource-type Microsoft.Insights/components --query properties.ConnectionString -o tsv)
echo "APPLICATIONINSIGHTS_CONNECTION_STRING=\"$CS\"" >> .env
echo "PRIOR_CLAIMS_INDEX=\"prior-claims\"" >> .env
echo ".env written ($(grep -c = .env) vars). Next: .venv/bin/python -m tribunal.seed"
