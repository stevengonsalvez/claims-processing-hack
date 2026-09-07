#!/usr/bin/env bash
# Challenge 4: build the tribunal container in the cloud (ACR Tasks - no local Docker)
# and run it on Azure Container Apps, serving the REST API and the MCP streamable-HTTP
# endpoint from one ingress. Idempotent: re-run to ship a new image.
#
#     tribunal/deploy_aca.sh            2>&1 | tee logs/deploy-aca.log
#
# Prints the FQDN, then proves the deployment: GET /samples returns the 5 demo claims and a
# real MCP client (tribunal/mcp_http.py --check) lists the three tools against https://<fqdn>/mcp.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
set -a; . ./.env; set +a

RG="${AZURE_RESOURCE_GROUP:-rg-labuser-0009}"
LOCATION="${AZURE_LOCATION:-swedencentral}"
# the lab's resource names all end in the same suffix; .env carries them from Challenge 0, so
# derive it from ACR_NAME rather than hardcoding, and keep this lab's value as the fallback.
SUFFIX="$(sed -E 's/^msagthackcr//' <<<"${ACR_NAME:-msagthackcr6ahymubsyajs6}")"
ACR="${ACR_NAME:-msagthackcr${SUFFIX}}"
ACA_ENV="${CONTAINER_APP_ENVIRONMENT_NAME:-msagthack-acaenv-${SUFFIX}}"
APP="claims-tribunal"
IMAGE="claims-tribunal"
TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M)}"
LAW="${LOG_ANALYTICS_WORKSPACE_NAME:-msagthack-loganalytics-${SUFFIX}}"
FOUNDRY_ACCOUNT="$(sed -E 's#https://([^.]+)\..*#\1#' <<<"${AI_FOUNDRY_PROJECT_ENDPOINT}")"
PY="${PY:-$REPO/.venv/bin/python}"

step() { printf '\n== %s ==\n' "$*"; }
# secrets only ever travel as `az ... -o none` arguments; nothing here echoes a value.

step "container registry $ACR (Basic)"
if az acr show -g "$RG" -n "$ACR" -o none 2>/dev/null; then
  echo "exists"
else
  az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true -l "$LOCATION" -o none
  echo "created"
fi
az acr update -g "$RG" -n "$ACR" --admin-enabled true -o none

step "cloud build $ACR.azurecr.io/$IMAGE:$TAG"
# Staged context: the repo root carries .venv and tribunal/ui/node_modules (~1 GB) and there
# is no .dockerignore to own, so copy in only what the Dockerfile consumes.
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/tribunal" "$CTX/challenge-2/agents" "$CTX/challenge-0/data" "$CTX/challenge-6"
rsync -a --exclude 'ui/' --exclude '__pycache__/' --exclude '.env' tribunal/ "$CTX/tribunal/"
rsync -a --exclude '__pycache__/' challenge-2/agents/ "$CTX/challenge-2/agents/"
rsync -a challenge-0/data/ "$CTX/challenge-0/data/"
cp challenge-6/coverage_ground_truth.json "$CTX/challenge-6/"
az acr build -r "$ACR" -t "$IMAGE:$TAG" -t "$IMAGE:latest" -f tribunal/Dockerfile --platform linux/amd64 "$CTX"

step "container apps environment $ACA_ENV"
if az containerapp env show -g "$RG" -n "$ACA_ENV" -o none 2>/dev/null; then
  echo "exists"
else
  LAW_ID="$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)"
  LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" --query primarySharedKey -o tsv)"
  az containerapp env create -g "$RG" -n "$ACA_ENV" -l "$LOCATION" \
    --logs-destination log-analytics --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" -o none
  echo "created"
fi

step "container app $APP"
ACR_USER="$(az acr credential show -g "$RG" -n "$ACR" --query username -o tsv)"
ACR_PASS="$(az acr credential show -g "$RG" -n "$ACR" --query 'passwords[0].value' -o tsv)"
SECRETS=(
  "azure-openai-key=${AZURE_OPENAI_KEY}"
  "search-admin-key=${SEARCH_ADMIN_KEY}"
  "mistral-key=${MISTRAL_DOCUMENT_AI_KEY}"
  "appinsights-connection-string=${APPLICATIONINSIGHTS_CONNECTION_STRING}"
  "storage-connection-string=${AZURE_STORAGE_CONNECTION_STRING}"
)
ENVVARS=(
  "AI_FOUNDRY_PROJECT_ENDPOINT=${AI_FOUNDRY_PROJECT_ENDPOINT}"
  "MODEL_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT_NAME}"
  "AZURE_OPENAI_BASE_URL=${AZURE_OPENAI_BASE_URL}"
  "AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}"
  "AZURE_OPENAI_EMBEDDING_DEPLOYMENT=${AZURE_OPENAI_EMBEDDING_DEPLOYMENT}"
  "SEARCH_SERVICE_ENDPOINT=${SEARCH_SERVICE_ENDPOINT}"
  "PRIOR_CLAIMS_INDEX=${PRIOR_CLAIMS_INDEX}"
  "MISTRAL_DOCUMENT_AI_ENDPOINT=${MISTRAL_DOCUMENT_AI_ENDPOINT}"
  "MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME=${MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME}"
  "AZURE_STORAGE_ACCOUNT_NAME=${AZURE_STORAGE_ACCOUNT_NAME}"
  "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true"
  "TRIBUNAL_API=http://localhost:8000"
  "AZURE_OPENAI_KEY=secretref:azure-openai-key"
  "SEARCH_ADMIN_KEY=secretref:search-admin-key"
  "MISTRAL_DOCUMENT_AI_KEY=secretref:mistral-key"
  "APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-connection-string"
  "AZURE_STORAGE_CONNECTION_STRING=secretref:storage-connection-string"
)

if az containerapp show -g "$RG" -n "$APP" -o none 2>/dev/null; then
  az containerapp secret set -g "$RG" -n "$APP" --secrets "${SECRETS[@]}" -o none
  az containerapp registry set -g "$RG" -n "$APP" --server "$ACR.azurecr.io" \
    --username "$ACR_USER" --password "$ACR_PASS" -o none
  az containerapp update -g "$RG" -n "$APP" --image "$ACR.azurecr.io/$IMAGE:$TAG" \
    --set-env-vars "${ENVVARS[@]}" -o none
  echo "updated"
else
  az containerapp create -g "$RG" -n "$APP" --environment "$ACA_ENV" \
    --image "$ACR.azurecr.io/$IMAGE:$TAG" \
    --registry-server "$ACR.azurecr.io" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --target-port 8000 --ingress external --transport auto \
    --cpu 1.0 --memory 2.0Gi --min-replicas 1 --max-replicas 2 \
    --system-assigned \
    --secrets "${SECRETS[@]}" --env-vars "${ENVVARS[@]}" -o none
  echo "created"
fi
unset ACR_PASS

step "managed identity -> data-plane roles on $FOUNDRY_ACCOUNT"
# The tribunal's Foundry client uses DefaultAzureCredential; in the container that resolves to
# this system-assigned identity. Keys in .env cover Azure OpenAI, AI Search and Mistral.
# "Azure AI User" does not exist as a role definition in this tenant (checked: the Foundry
# data-plane roles present are Azure AI Developer / Cognitive Services User), so assign every
# name in the list that does exist, scoped to the Foundry account only.
az containerapp identity assign -g "$RG" -n "$APP" --system-assigned -o none
PRINCIPAL="$(az containerapp identity show -g "$RG" -n "$APP" --query principalId -o tsv)"
FOUNDRY_ID="$(az resource show -g "$RG" -n "$FOUNDRY_ACCOUNT" --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)"
for ROLE in "Azure AI User" "Azure AI Developer" "Cognitive Services User"; do
  if out="$(az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
      --role "$ROLE" --scope "$FOUNDRY_ID" -o none 2>&1)"; then
    echo "  assigned: $ROLE"
  else
    echo "  skipped:  $ROLE ($(head -1 <<<"$out"))"
  fi
done

FQDN="$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
step "deployed: https://$FQDN"
echo "  REST  https://$FQDN/samples"
echo "  MCP   https://$FQDN/mcp   (streamable HTTP)"

step "proof: the newest revision runs the image we just built"
# The app is in Single active-revision mode: if a new image crash-loops (this happened once -
# ModuleNotFoundError: agent_framework), ACA keeps the previous revision serving traffic and
# every check below would pass against the OLD code. So gate on the new revision itself.
WANT="$ACR.azurecr.io/$IMAGE:$TAG"
REV=""; RSTATE=""; RIMAGE=""; RHEALTH=""
for i in $(seq 1 60); do
  REV="$(az containerapp show -g "$RG" -n "$APP" --query properties.latestRevisionName -o tsv)"
  read -r RIMAGE RSTATE RHEALTH <<<"$(az containerapp revision show -g "$RG" -n "$APP" --revision "$REV" \
    --query '[properties.template.containers[0].image, properties.runningState, properties.healthState]' -o tsv | tr '\n' ' ')"
  echo "  $REV  $RSTATE/$RHEALTH  $RIMAGE"
  [ "$RIMAGE" = "$WANT" ] && [ "$RSTATE" = "Running" ] && break
  case "$RSTATE" in Failed|Degraded) break;; esac
  sleep 5
done
if [ "$RIMAGE" != "$WANT" ] || [ "$RSTATE" != "Running" ]; then
  echo "FAIL: latest revision $REV is $RSTATE/$RHEALTH on $RIMAGE, wanted Running on $WANT"
  echo "      the proof below would have run against the previous revision; container logs:"
  az containerapp logs show -g "$RG" -n "$APP" --revision "$REV" --tail 40 --type console || true
  exit 1
fi

step "proof: waiting for ingress"
for i in $(seq 1 60); do
  curl -sf "https://$FQDN/healthz" >/dev/null && break
  sleep 5
done
echo "GET /healthz -> $(curl -s "https://$FQDN/healthz")"
echo "GET /samples -> $(curl -s "https://$FQDN/samples" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(len(d), [s["name"] for s in d])')"

step "proof: MCP client against https://$FQDN/mcp"
"$PY" -m tribunal.mcp_http --check "https://$FQDN/mcp"

step "done"
echo "FQDN=$FQDN"
echo "next: tribunal/apim_mcp.sh   (publish through API Management)"
