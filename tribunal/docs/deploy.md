# Challenge 4: Container Apps + API Management MCP server

The tribunal runs in the cloud as **one container serving two protocols**: the REST API the
UI already speaks, and the same three tools as an **MCP server over streamable HTTP**, both
published through **Azure API Management**.

```
challenge-0/data ─┐
tribunal/*.py     ├─▶ az acr build ──▶ ACR (Basic)         cloud build, no local Docker
challenge-2/agents┘   msagthackcr6ahymubsyajs6
                                │ image claims-tribunal:<ts>
                                ▼
                      ┌────────────────────────────┐
                      │ Container App              │  external ingress :8000
                      │ claims-tribunal            │  min 1 replica, system-assigned MI
                      │  uvicorn tribunal.mcp_http │
                      │   /mcp   streamable HTTP   │──▶ Foundry agents (MI: Azure AI Developer)
                      │   /samples /adjudicate ... │──▶ AI Search + Mistral OCR (keys)
                      │   /healthz                 │──▶ App Insights (OTel)
                      └─────────────┬──────────────┘
                                    │ https
                      ┌─────────────▼──────────────┐
                      │ API Management (BasicV2)   │
                      │  /tribunal      OpenAPI    │  imported from /openapi.json
                      │  /tribunal-mcp  type=mcp   │  APIM MCP server -> backend /mcp
                      │  /mcp           passthrough│  same endpoint, plain API
                      └─────────────┬──────────────┘
                                    ▼
                   Claude Desktop · VS Code · any MCP client
```

## Live URLs

| what | URL |
|---|---|
| Container App | `https://claims-tribunal.niceglacier-506f72ec.swedencentral.azurecontainerapps.io` |
| REST (direct) | `.../samples`, `.../adjudicate`, `.../decision`, `.../healthz`, `.../openapi.json` |
| MCP (direct) | `.../mcp` |
| APIM gateway | `https://msagthack-apim-6ahymubsyajs6.azure-api.net` |
| REST via APIM | `https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal/samples` |
| **MCP via APIM (MCP server)** | `https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp` |
| MCP via APIM (passthrough) | `https://msagthack-apim-6ahymubsyajs6.azure-api.net/mcp` |

Resource group `rg-labuser-0009`, region `swedencentral`. No subscription key is required:
every API is created with `subscriptionRequired: false` (the tribunal API is unauthenticated
by design for the demo, see the Limitations section of `tribunal/README.md`).

### Where the Challenge 4 baseline endpoints went

The baseline (`challenge-4/api_server.py`) deploys OCR -> JSON-structuring behind three routes.
The tribunal deploys the same shape - one FastAPI app on Container Apps, managed identity, ACR
image - but the workflow behind it is the four-agent tribunal, so the routes are named for what
they do. The equivalents, all live on the FQDN above and through APIM at `/tribunal/...`:

| baseline route | tribunal route | note |
|---|---|---|
| `GET /health` | `GET /healthz` | same liveness role; also the APIM/ACA probe target |
| `POST /process-claim/upload` (multipart) | `POST /adjudicate` with `statements=@front.jpeg&statements=@back.jpeg&photo=@crash.jpg` | same multipart upload; the response is an SSE stream of `agent.start`/`agent.token`/`agent.done`/`verdict` instead of one JSON blob, because four agents argue for ~90 s and the UI renders it live |
| `POST /process-claim/base64` | - | not implemented; the UI and the MCP tools post multipart or a `sample` name |
| - | `POST /adjudicate` with `sample=crash2` | the five bundled demo claims, no upload needed |
| - | `GET /samples`, `POST /decision`, `GET /decisions` | demo corpus and the human adjuster's override log |
| - | `POST /mcp` | the same workflow as an MCP server (this is the part the baseline does not have) |

## Deploy / redeploy

```bash
tribunal/deploy_aca.sh   2>&1 | tee logs/deploy-aca.log   # ACR + image + ACA app + RBAC + proof
tribunal/apim_mcp.sh     2>&1 | tee logs/apim-mcp.log     # APIM apis + MCP server + proof
```

Both read `.env` for the resource names Challenge 0 created (`ACR_NAME`, `API_MANAGEMENT_NAME`,
`CONTAINER_APP_ENVIRONMENT_NAME`, `LOG_ANALYTICS_WORKSPACE_NAME`, `AZURE_RESOURCE_GROUP`) and
derive the lab suffix from them, so they run against another lab's resource group unchanged; the
URLs written out in this document are of course this lab's.

Both are idempotent. `deploy_aca.sh` tags each image with a timestamp, so re-running it ships a
new revision; it stages a build context (the repo root carries `.venv` and `tribunal/ui/node_modules`,
~1 GB, and there is no `.dockerignore`), calls `az acr build` (ACR Tasks builds in the cloud -
Docker is not installed locally), creates the Container Apps environment against the existing
Log Analytics workspace, sets the secrets and env vars, assigns the managed identity's Foundry
roles, **checks that the newest revision is `Running` on the tag it just built** (single
active-revision mode means a crash-looping image leaves the *previous* revision answering, which
would otherwise let the proof below pass against stale code - it dumps the container log and exits
1 instead), then proves the result. `apim_mcp.sh` reuses the APIM instance the Challenge 0 template
already created; if it were missing it creates a **Consumption** instance (minutes, not the ~45
of the classic tiers) with `az rest`, because az CLI 2.76 masks ARM errors as *"The content for
this response was already consumed"*.

## One app, two protocols

`tribunal/mcp_http.py` builds a single ASGI app:

```python
app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, ...)
app.router.routes.insert(0, Route("/healthz", healthz))
app.router.routes.append(Mount("/", app=tribunal_api))       # the existing FastAPI app
```

- the tools come from `tribunal/mcp_server.py` unchanged - the stdio server, the HTTP server and
  Claude Desktop all run the same three functions (`list_sample_claims`, `adjudicate_claim`,
  `record_decision`), which call the tribunal over `TRIBUNAL_API` (in the container:
  `http://localhost:8000`, i.e. itself). The SDK runs sync tools on a worker thread, so the
  self-call does not block the event loop.
- `stateless_http=True` so APIM or a second replica can serve any request without sticky sessions.
- DNS-rebinding protection is disabled: the `Host` header is the ACA FQDN or the APIM gateway.

## Client config

Claude Desktop (`claude_desktop_config.json`) - remote MCP over the gateway:

```json
{
  "mcpServers": {
    "claims-tribunal": {
      "type": "http",
      "url": "https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp"
    }
  }
}
```

VS Code: the repo's `.vscode/mcp.json` holds the **local stdio** server (`tribunal/docs/mcp.md`).
Add this second entry next to it to also drive the deployed one - same three tools, no local
process:

```json
    "claims-tribunal-remote": {
      "type": "http",
      "url": "https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp"
    }
```

If the APIM api is later switched to `subscriptionRequired: true`, add the key as a header:
`"headers": { "Ocp-Apim-Subscription-Key": "<key from APIM > Subscriptions>" }`.

Verify any of the three endpoints from the CLI (this is what both scripts use as their proof):

```bash
.venv/bin/python -m tribunal.mcp_http --check https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp
# {"url":"...","server":"claims-tribunal","protocol":"2025-11-25",
#  "tools":["adjudicate_claim","list_sample_claims","record_decision"],
#  "samples":["crash1","crash2","crash3","crash4","crash5"]}

# and the slow path - a real adjudicate_claim tool call, ~90 s of four agents, which is what
# exercises the gateway's request timeout and whether it buffers the SSE stream:
.venv/bin/python -m tribunal.mcp_http --check https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp \
    --adjudicate crash2
# ...same first line, then (verbatim from logs/apim-mcp.log, 2026-09-07):
# {"sample":"crash2","claim_id":"CLM-20260907-C2D009","decision":"approve","confidence":0.85,
#  "payout":{"claimed":10300,"covered":10300,"deductible":500,"limit":50000,"net":9800},
#  "fraud_score":0.4,"policy_number":"COMP-AUTO-001",
#  "opinions":["adjuster","arbiter","fraud","policy"],"letter_chars":622}
```

The summary prints the whole `payout` object rather than one number: `net = min(covered, limit) -
deductible`, and the arbiter re-derives those figures per run, so a single "payout" field cannot
be reconciled against a direct `POST /adjudicate` run. `apim_mcp.sh` runs this call at the end of
every deploy (`MCP_ADJUDICATE=` skips it, `MCP_ADJUDICATE=crash4` picks another claim).

## Proof (2026-09-07, logs kept under `logs/`)

Every row below was re-captured after the final image build, `claims-tribunal:202609071304`
(revision `claims-tribunal--0000002`, `Running/Healthy`), so the cloud runs the same code as the
repo. Both scripts exited 0 (`DEPLOY_RC=0`, `APIM_RC=0` at the end of their logs).

| check | result | log |
|---|---|---|
| MCP over HTTP locally, spare port | tools `adjudicate_claim, list_sample_claims, record_decision`, 5 samples, protocol `2025-11-25` | `logs/mcp-http-local.log` |
| newest revision runs the tag just built | `claims-tribunal--0000002  Running/Healthy  ...claims-tribunal:202609071304` | `logs/deploy-aca.log` |
| managed-identity roles | `skipped: Azure AI User (Role ... doesn't exist)`, `assigned: Azure AI Developer`, `assigned: Cognitive Services User` | `logs/deploy-aca.log` |
| `GET https://<aca-fqdn>/healthz`, `/samples` | `{"status":"ok",...}`, `5 ['crash1'..'crash5']` | `logs/deploy-aca.log` |
| MCP client vs `https://<aca-fqdn>/mcp` | 3 tools, `list_sample_claims` -> 5 samples | `logs/deploy-aca.log` |
| `GET <gateway>/tribunal/samples` | `5 ['crash1'..'crash5']` | `logs/apim-mcp.log` |
| MCP client vs `<gateway>/tribunal-mcp` and `<gateway>/mcp` | 3 tools, 5 samples on both | `logs/apim-mcp.log` |
| full tribunal in the cloud, `POST /adjudicate sample=crash2` | 6 `agent.start`/`agent.done`, 1954 token events, 0 errors, verdict **approve**, net **$9,400** | `logs/aca-adjudicate-crash2.log` |
| `adjudicate_claim("crash2")` as an **MCP tool call through APIM** | four opinions returned, `deny`, confidence 1.0, claimed $11,650, policy `C044-AUTO-001` | `logs/apim-adjudicate-crash2.log` |
| `adjudicate_claim("crash1")` through APIM | four opinions, `deny`, policy **`LIAB-AUTO-001`** = ground truth: the deployed image carries the `search_tools.py` policy-pick fix, and a liability-only policy correctly denies own-vehicle damage | `logs/apim-adjudicate-crash1.log` |

The last three are the ones that matter: the deployed container runs the whole four-agent tribunal
(Foundry agents via managed identity, AI Search, Mistral OCR, App Insights) and an MCP client
drives it end to end through the API Management gateway, on the slow path (~90 s) that would
expose a gateway request timeout or a buffered SSE stream. Both proofs are reproducible from the
repo - `python -m tribunal.mcp_http --check <url> --adjudicate <sample>`, the exact command line
is the first line of each log.

**Read the verdicts as samples, not as fixtures.** The tribunal is four LLM agents over
handwritten OCR, and the same claim moves between runs: crash2 came back `approve`/net $9,400
(direct REST), `approve`/net $9,800 and $11,900 (through APIM), and `deny` when the OCR read the
policy number as `C044-AUTO-001` instead of `COMP-AUTO-001` and the policy analyst found no
matching policy. The harness therefore asserts the *contract* - a verdict with a decision in
`approve|deny|refer` and all four opinions present - not a specific number. Verdict quality is
`tribunal/docs/evaluation.md`'s subject, not this track's.

## How the pieces are configured

| piece | choice | why |
|---|---|---|
| ACR | `msagthackcr6ahymubsyajs6`, Basic, admin user | ACR Tasks (`az acr build`) builds in the cloud; admin creds are the ACA registry credential |
| image | `python:3.12-slim`, trimmed pip set | the full `requirements.txt` (streamlit, evaluation, cosmos, notebooks) is not imported by the served app; the cloud build takes ~1 min |
| ACA | 1 vCPU / 2 GiB, min 1 replica, external ingress 8000 | min 1 keeps the Foundry agent cache warm, so the first claim is not a cold start |
| secrets | `azure-openai-key`, `search-admin-key`, `mistral-key`, `appinsights-connection-string`, `storage-connection-string` | ACA secrets, referenced as `secretref:` env vars; never printed by the scripts |
| identity | system-assigned + `Azure AI Developer` and `Cognitive Services User` on the Foundry account | `tribunal/foundry.py` uses `DefaultAzureCredential`; everything else is key-based |
| tracing | `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` + the App Insights connection string | the deployed app writes the same `tribunal.adjudicate` traces as local runs |
| APIM MCP | api `tribunal-mcp` with `properties.type: "mcp"` + `backendId` | the api-version `2025-09-01-preview` MCP api type; the backend is an APIM `Backend` resource pointing at `https://<fqdn>/mcp` |
| APIM passthrough | api `tribunal-mcp-http`, path `/mcp`, `rewrite-uri` to `/mcp`, `buffer-response="false"` | belt and braces: works on any APIM sku/api-version, and keeps the SSE stream unbuffered |

## What failed, and why

- **`--role "Azure AI User"` does not exist in this tenant.** `az role assignment create` fails
  with `Role 'Azure AI User' doesn't exist.` The Foundry data-plane roles that do exist here are
  `Azure AI Developer` and `Cognitive Services User`; `deploy_aca.sh` now tries all three names and
  assigns the ones that resolve.
- **First image crashed on boot**: `ModuleNotFoundError: No module named 'agent_framework'` -
  `tribunal/api.py` had just moved to the Agent Framework workflow graph (`af_workflow.py`), so the
  trimmed pip set was missing `agent-framework-core` / `agent-framework-foundry`. Added, rebuilt,
  revision healthy. If you trim further, boot the image before shipping it.
- **The native MCP api 404s if you only set `serviceUrl`.** ARM accepts
  `{"type":"mcp","serviceUrl":"https://<fqdn>/mcp"}` and returns 201, but the gateway answers
  `404 Resource Not Found` on `POST /tribunal-mcp`. It starts working once the api references an
  APIM `Backend` resource (`backendId: tribunal-mcp-backend`). `mcpProperties.transportType` is
  silently dropped - the server echoes `{"endpoints": null, "federation": null,
  "isFederationRouter": false}` - so treat that field as not-yet-contracted.
- **APIM sku is BasicV2, not Consumption**, because the Challenge 0 template had already created
  `msagthack-apim-6ahymubsyajs6` in the resource group and re-creating it as Consumption would have
  meant deleting a working instance mid-hack. The script creates Consumption when the instance is
  absent.
- **Not done**: no authentication in front of either the Container App or the gateway (demo scope),
  no custom domain, no APIM `mcpTools` mapping of the REST operations into individual MCP tools
  (the tools are served by our own MCP implementation instead, which is what the tribunal needs).

**The deployed tribunal is world-callable** while it is up: ACA ingress is external and all three
APIM apis are `subscriptionRequired: false`, so anyone who finds the FQDN can spend this
subscription's gpt-4.1-mini and Mistral quota. That is deliberate for the demo (an MCP client with
no key is the whole point of the walkthrough) and it is the first thing to close afterwards - one
call per api, then hand clients the key header shown above:

```bash
SUB=$(az account show --query id -o tsv)
B="https://management.azure.com/subscriptions/$SUB/resourceGroups/rg-labuser-0009/providers/Microsoft.ApiManagement/service/msagthack-apim-6ahymubsyajs6"
for a in claims-tribunal tribunal-mcp tribunal-mcp-http; do
  az rest --method patch --url "$B/apis/$a?api-version=2024-06-01-preview" \
    --body '{"properties":{"subscriptionRequired":true}}' -o none
done
```

ACA ingress has to stay external regardless: a BasicV2 APIM instance cannot VNet-integrate, so
there is no private path from the gateway to the app. Locking it down properly means an
`ip-filter` policy or Entra auth in front of the app, which is out of scope for the hack.

## Teardown

```bash
az containerapp delete -g rg-labuser-0009 -n claims-tribunal -y
az containerapp env delete -g rg-labuser-0009 -n msagthack-acaenv-6ahymubsyajs6 -y
az acr delete -g rg-labuser-0009 -n msagthackcr6ahymubsyajs6 -y
# APIM: remove only what this track added
for a in claims-tribunal tribunal-mcp tribunal-mcp-http; do
  az rest --method delete --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-labuser-0009/providers/Microsoft.ApiManagement/service/msagthack-apim-6ahymubsyajs6/apis/$a?api-version=2024-06-01-preview" -o none
done
```
