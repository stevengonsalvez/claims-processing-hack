#!/usr/bin/env bash
# Challenge 4: publish the Container App through Azure API Management - the REST API from
# its OpenAPI document, and the MCP streamable-HTTP endpoint as an APIM-fronted MCP server.
#
#     tribunal/apim_mcp.sh          2>&1 | tee logs/apim-mcp.log
#
# Everything goes through `az rest`: az CLI 2.76 masks ARM errors as "The content for this
# response was already consumed", and `az apim api create` cannot express the MCP api type.
# Order of attempts for the MCP endpoint:
#   1. native APIM MCP server (properties.type = "mcp") across the preview api-versions
#   2. plain passthrough API at /mcp with a rewrite-uri policy  <- always works
# Both end with a real MCP client (tribunal/mcp_http.py --check) run against the gateway.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
set -a; . ./.env; set +a

RG="${AZURE_RESOURCE_GROUP:-rg-labuser-0009}"
LOCATION="${AZURE_LOCATION:-swedencentral}"
# derived from the Challenge 0 names in .env (this lab's value is only the fallback)
SUFFIX="$(sed -E 's/^msagthack-apim-//' <<<"${API_MANAGEMENT_NAME:-msagthack-apim-6ahymubsyajs6}")"
APIM="${API_MANAGEMENT_NAME:-msagthack-apim-${SUFFIX}}"
APP="claims-tribunal"
PUBLISHER_EMAIL="${APIM_PUBLISHER_EMAIL:-labuser-0009@mngenvmcap414615.onmicrosoft.com}"
PY="${PY:-$REPO/.venv/bin/python}"
SUB="$(az account show --query id -o tsv)"
BASE="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.ApiManagement/service/$APIM"
STABLE_API_VERSION="2024-06-01-preview"
MCP_API_VERSIONS=(2025-09-01-preview 2025-05-01-preview 2024-06-01-preview)

step() { printf '\n== %s ==\n' "$*"; }
rest() { az rest --method "$1" --url "$2" "${@:3}"; }

step "container app $APP ingress"
FQDN="$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
[ -n "$FQDN" ] || { echo "no ingress: run tribunal/deploy_aca.sh first"; exit 1; }
echo "backend https://$FQDN  (REST + /mcp)"

step "api management $APIM (Consumption if it has to be created)"
if az rest --method get --url "$BASE?api-version=$STABLE_API_VERSION" -o none 2>/dev/null; then
  az rest --method get --url "$BASE?api-version=$STABLE_API_VERSION" \
    --query "{sku:sku.name,state:properties.provisioningState,gateway:properties.gatewayUrl}" -o json
  echo "reusing the existing instance (created by the Challenge 0 template)"
else
  # Consumption: serverless, provisions in minutes instead of the ~45 of the classic tiers.
  BODY="$(jq -n --arg loc "$LOCATION" --arg mail "$PUBLISHER_EMAIL" \
    '{location:$loc, sku:{name:"Consumption", capacity:0},
      properties:{publisherEmail:$mail, publisherName:"Claims Tribunal"}}')"
  rest put "$BASE?api-version=$STABLE_API_VERSION" --body "$BODY" -o none
  for i in $(seq 1 60); do
    st="$(az rest --method get --url "$BASE?api-version=$STABLE_API_VERSION" --query properties.provisioningState -o tsv 2>/dev/null || echo Creating)"
    echo "  $st"; [ "$st" = "Succeeded" ] && break; sleep 20
  done
fi
GATEWAY="$(az rest --method get --url "$BASE?api-version=$STABLE_API_VERSION" --query properties.gatewayUrl -o tsv)"
echo "gateway $GATEWAY"

step "import the Container App OpenAPI as api 'claims-tribunal' (path /tribunal)"
# ponytail: subscriptionRequired=false - the tribunal API is unauthenticated by design for the
# demo (tribunal/README.md Limitations); with a key the MCP clients would need the header too.
BODY="$(jq -n --arg fqdn "$FQDN" \
  '{properties:{displayName:"Claims Tribunal", description:"Four agents argue every claim",
                path:"tribunal", protocols:["https"], subscriptionRequired:false,
                serviceUrl:("https://"+$fqdn), format:"openapi-link",
                value:("https://"+$fqdn+"/openapi.json")}}')"
rest put "$BASE/apis/claims-tribunal?api-version=$STABLE_API_VERSION" --body "$BODY" -o none
for i in $(seq 1 30); do
  ops="$(az rest --method get --url "$BASE/apis/claims-tribunal/operations?api-version=$STABLE_API_VERSION" --query "value[].name" -o tsv 2>/dev/null || true)"
  [ -n "$ops" ] && break; sleep 5
done
echo "operations: $(tr '\n' ' ' <<<"$ops")"
echo "REST via gateway: $GATEWAY/tribunal/samples"

step "attempt 1: native APIM MCP server (properties.type=mcp) at /tribunal-mcp"
# The MCP api type is preview-only, so walk the api-versions newest first. A `serviceUrl` alone
# is NOT enough: the gateway 404s until the api points at a Backend resource via backendId
# (observed on this BasicV2 instance), so create the backend first.
MCP_NATIVE=""
for v in "${MCP_API_VERSIONS[@]}"; do
  echo "-- api-version $v"
  BACKEND="$(jq -n --arg u "https://$FQDN/mcp" \
    '{properties:{protocol:"http", url:$u, description:"Claims Tribunal MCP (streamable HTTP)"}}')"
  az rest --method put --url "$BASE/backends/tribunal-mcp-backend?api-version=$v" --body "$BACKEND" -o none 2>/dev/null || true
  BODY="$(jq -n --arg u "https://$FQDN/mcp" \
    '{properties:{type:"mcp", displayName:"Claims Tribunal MCP", path:"tribunal-mcp",
                  protocols:["https"], subscriptionRequired:false, serviceUrl:$u,
                  backendId:"tribunal-mcp-backend", mcpProperties:{transportType:"streamable"}}}')"
  if out="$(az rest --method put --url "$BASE/apis/tribunal-mcp?api-version=$v" --body "$BODY" \
      --query 'properties.{type:type,path:path,backendId:backendId,mcpProperties:mcpProperties}' -o json 2>&1)"; then
    echo "$out"
    MCP_NATIVE="$v"; break
  fi
  echo "$out" | head -12
done
[ -n "$MCP_NATIVE" ] && echo "native MCP api created with api-version $MCP_NATIVE" || echo "native MCP api type not accepted by this instance/api-versions"

step "attempt 2: passthrough api 'tribunal-mcp-http' (path /mcp -> https://$FQDN/mcp)"
BODY="$(jq -n --arg fqdn "$FQDN" \
  '{properties:{displayName:"Claims Tribunal MCP (streamable HTTP)", path:"mcp",
                protocols:["https"], subscriptionRequired:false,
                serviceUrl:("https://"+$fqdn)}}')"
rest put "$BASE/apis/tribunal-mcp-http?api-version=$STABLE_API_VERSION" --body "$BODY" -o none
for m in post get delete; do
  M="$(tr '[:lower:]' '[:upper:]' <<<"$m")"
  BODY="$(jq -n --arg m "$M" '{properties:{displayName:("MCP "+$m), method:$m, urlTemplate:"/", responses:[]}}')"
  rest put "$BASE/apis/tribunal-mcp-http/operations/mcp-$m?api-version=$STABLE_API_VERSION" --body "$BODY" -o none
done
# rewrite-uri pins the backend path to exactly /mcp (no trailing slash: the transport route is
# an exact match) and forward-request keeps the SSE response streaming rather than buffered.
POLICY='<policies><inbound><base /><rewrite-uri template="/mcp" /></inbound><backend><forward-request buffer-response="false" timeout="300" /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
BODY="$(jq -n --arg p "$POLICY" '{properties:{format:"xml", value:$p}}')"
# the policy PUT answers with the XML document itself, which az prints; nothing to see here
rest put "$BASE/apis/tribunal-mcp-http/policies/policy?api-version=$STABLE_API_VERSION" --body "$BODY" -o none >/dev/null
echo "MCP via gateway: $GATEWAY/mcp"

step "proof: REST through the gateway"
echo "GET $GATEWAY/tribunal/samples -> $(curl -s "$GATEWAY/tribunal/samples" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(len(d), [s["name"] for s in d])' 2>&1 | head -3)"

step "proof: MCP client through the gateway"
rc=0
echo "-- passthrough api: $GATEWAY/mcp"
"$PY" -m tribunal.mcp_http --check "$GATEWAY/mcp" || rc=$?
if [ -n "$MCP_NATIVE" ]; then
  echo "-- native MCP api: $GATEWAY/tribunal-mcp"
  "$PY" -m tribunal.mcp_http --check "$GATEWAY/tribunal-mcp" || rc=$?
fi

# The list_sample_claims call above returns in milliseconds; adjudicate_claim runs the four
# agents for ~90 s, which is the call that actually exercises the gateway's request timeout and
# whether it buffers the SSE stream. MCP_ADJUDICATE= (empty) skips it.
ADJ="${MCP_ADJUDICATE:-crash2}"
if [ -n "$ADJ" ]; then
  ADJ_URL="$GATEWAY/mcp"; [ -n "$MCP_NATIVE" ] && ADJ_URL="$GATEWAY/tribunal-mcp"
  step "proof: adjudicate_claim($ADJ) as an MCP tool call through the gateway ($ADJ_URL)"
  "$PY" -m tribunal.mcp_http --check "$ADJ_URL" --adjudicate "$ADJ" || rc=$?
fi

step "done"
echo "gateway   $GATEWAY"
echo "REST      $GATEWAY/tribunal/samples"
echo "MCP       $GATEWAY/tribunal-mcp   (APIM MCP server, properties.type=mcp)"
echo "MCP       $GATEWAY/mcp            (plain passthrough of the same endpoint)"
exit $rc
