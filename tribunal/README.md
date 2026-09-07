# Claims Tribunal

Four agents argue every claim, live. The baseline hackathon pipeline (OCR, structure,
policy match, coverage validation) becomes evidence for a tribunal: an **Adjuster**
(damage and cost from the photo), a **Fraud Investigator** (vector search over prior
claims, statement vs photo contradictions), a **Policy Analyst** (coverage strictly from
the policy text in AI Search) and an **Arbiter** that weighs the three, rules
approve / deny / refer, computes payout, and writes the claimant letter. A human
adjuster approves, denies or overrides at the end.

Models: `gpt-4.1-mini` (all agents, vision for the photo), `mistral-document-ai-2512`
(OCR of the handwritten statement), `text-embedding-3-large` (policy and prior-claim
vectors). Platform: Microsoft Foundry prompt agents, Azure AI Search, Application
Insights via OpenTelemetry.

```
statement pages + damage photo
        │
        ▼
┌──────────────┐    ┌──────────────────┐
│ OCR (Mistral)│──▶│ Structure (4.1m) │──▶ claim record
└──────────────┘    └────────┬─────────┘
                             │ AI Search: prior-claims (vector) + policies (hybrid)
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   ┌────────────┐   ┌────────────────┐   ┌───────────────┐
   │  Adjuster  │   │ Fraud Investig.│   │ Policy Analyst│   (parallel, streamed)
   └─────┬──────┘   └───────┬────────┘   └───────┬───────┘
         └──────────────────┼────────────────────┘
                            ▼
                     ┌────────────┐      ┌──────────────────┐
                     │  Arbiter   │─────▶│ verdict + payout │──▶ human approve / deny / override
                     └────────────┘      │ + fraud + letter │
                                         └──────────────────┘
   every step: OTel span -> Application Insights / Foundry tracing
```

## Challenge coverage

`tribunal/` is the differentiator: one four-agent adjudication pipeline that does Ch1's
retrieval, Ch2/Ch6's agents and Ch3's observability inside a single live-streamed workflow, then
goes further than any one challenge asks for (three-way OCR bench, cloud + continuous eval, red
team, ACA + APIM + MCP deploy). It does not touch `challenge-0..6/` baseline code.

| challenge | basic (upstream) | status | artifact |
|---|---|---|---|
| Ch0 | environment + resource deployment | done | `tribunal/deploy_infra.sh` (Foundry, AI Search, App Insights via `az rest`) |
| Ch1 | document processing + vectorized search | done, exceeds baseline | [`docs/ocr-comparison.md`](docs/ocr-comparison.md), [`docs/search.md`](docs/search.md) |
| Ch2 | OCR + JSON-structuring agents | done, subsumed into the tribunal | `af_workflow.py` (`OcrExecutor`, `StructureExecutor`), `prompts.py` |
| Ch3 | observability, evaluation, red team, alerting | done | [`docs/evaluation.md`](docs/evaluation.md), [`docs/alerting.md`](docs/alerting.md) |
| Ch4 | multi-agent workflow + API deployment | done, adds MCP + APIM | [`docs/deploy.md`](docs/deploy.md) |
| Ch5 | claims processing UI | done, differently (React live agent timeline, not a Streamlit upload form) | `ui/` |
| Ch6 | policy matching + coverage validation | done, subsumed into the tribunal | Policy Analyst + Arbiter in `af_workflow.py`; `eval.py` scores vs `challenge-6/coverage_ground_truth.json` |

### OCR comparison (Ch1)

`tribunal/ocr_bench.py` runs Azure AI Document Intelligence (prebuilt-read), Mistral Document AI
and gpt-4.1-mini vision over all 10 statement pages and scores each against
`challenge-3/ground_truth.json`. Result of the run of 2026-09-07 13:17:04 BST:

| approach | fields found / 99 scorable | median latency / page | cost / 1000 claims |
|---|---|---|---|
| Azure Document Intelligence (prebuilt-read) | 93 (93.9%) | 2.29s | $3.00 |
| Mistral Document AI (mistral-document-ai-2512) | 93 (93.9%) | 1.75s | $4.00 |
| gpt-4.1-mini vision (Responses API) | 92 (92.9%) | 5.31s | $1.59 |

Accuracy doesn't decide it: the three land within one field of each other, and within noise once
the unscorable `incident_description` field (a paraphrase, not page text) is set aside. Ships as:
Mistral for the bulk transcript (flat per-page cost, lowest latency, on the critical path before
any agent starts), gpt-4.1-mini vision reserved for what page-OCR can't do — reading the damage
photo and returning structured claim JSON. Full method, scoring rule, prices and the ground-truth
bug the bench found in `challenge-3/ground_truth.json` (`crash3 police_report_number`):
[`docs/ocr-comparison.md`](docs/ocr-comparison.md). Re-run: `.venv/bin/python -m tribunal.ocr_bench`
(not during a demo — shares the rate-limited Mistral deployment).

### Search (Ch1)

`search_tools.py`, `seed.py`, `blob_upload.py`:

- Source docs live in Blob Storage (`claims-data/{policies,statements,images}`); every policy
  chunk carries its `source_url`.
- Integrated vectorization: `AzureOpenAIVectorizer` on both indexes, server-side
  `VectorizableTextQuery` at query time — no client-side embedding call on the query path.
- Keyword + vector hybrid search (RRF fusion) and a semantic ranker (`insurance-semantic`,
  reranker score returned per chunk).
- Deterministic policy selection: the claim's OCR'd policy number is resolved against the
  index's 5 codes (OCR spacing/punctuation tolerated) and pulled by exact filter
  (`policy_number eq '<pn>'`) — never left to ranking, so a claim is never judged against another
  customer's contract.
- Graceful degradation, both paths logged: a failing semantic call retries as plain hybrid; an
  unresolved policy number falls back to best-effort text ranking, which cannot reliably separate
  the five auto policies.

Gap: `source_url` and the reranker score reach `search_policies` but not the UI yet —
`workflow.py` emits policy evidence as `{title, score}`. Detail + the one-line fix:
[`docs/search.md`](docs/search.md).

### Evaluation (Ch3)

| stage | command | output |
|---|---|---|
| Decision accuracy | `python -m tribunal.eval` | `tribunal/data/scorecard.md` vs `challenge-6/coverage_ground_truth.json` |
| Local quality | `python -m tribunal.quality` | `tribunal/data/quality.md` (groundedness / relevance / coherence / fluency / content safety) |
| Cloud evaluation | `python -m tribunal.cloud_eval` | `tribunal/data/cloud_eval.md` + Foundry **Evaluation** tab |
| Continuous evaluation | `python -m tribunal.cloud_eval --continuous` | Foundry **Evaluation ▸ Continuous evaluation**, scores every `TribunalArbiterAgent` response |
| Red teaming | `python -m tribunal.redteam --both` | `tribunal/data/redteam.md` + Foundry **AI red teaming** tab (service scan) + local injection probe |
| Alerting | `tribunal/alerts.sh` | fraud-referral alert, `fraud_score > 0.6` → email; detail: [`docs/alerting.md`](docs/alerting.md) |

- Red team service scan: 0/12 attack success (violence, hate-unfairness — the SDK's generic harm
  categories, not domain-specific ones). The local claimant-authored injection probe is
  non-deterministic: 0/8–2/8 attack success across runs of the same eight prompts.
- Judge model is our own `gpt-4.1-mini` deployment — quality scores are self-judged; the hack's
  fixed model set has no independent judge available.
- Needs `azure-ai-evaluation[redteam]` (pyrit + ~40 packages, additive-only); not yet added to
  `requirements.txt`.

Detail: [`docs/evaluation.md`](docs/evaluation.md).

### Deployment (Ch4)

One container serves REST and MCP (streamable HTTP), both published through Azure API Management:

| what | URL |
|---|---|
| Container App | `https://claims-tribunal.niceglacier-506f72ec.swedencentral.azurecontainerapps.io` |
| APIM gateway | `https://msagthack-apim-6ahymubsyajs6.azure-api.net` |
| REST via APIM | `.../tribunal/samples` |
| **MCP via APIM** | `.../tribunal-mcp` |

Deploy / redeploy: `tribunal/deploy_aca.sh` (ACR build + ACA app + RBAC) then `tribunal/apim_mcp.sh`
(APIM apis + MCP server); both idempotent, ~5 min each, both proof the result before exiting 0.

- No auth on either the Container App or APIM (`subscriptionRequired: false` on all three apis) —
  deliberate for the demo's key-less MCP client; patch loop to close it afterwards is in
  `docs/deploy.md`.
- The deployed image is a snapshot (`claims-tribunal:202609071304`); other tracks kept editing
  `tribunal/*.py` after that build — re-run `deploy_aca.sh` if code moved since.

Detail: [`docs/deploy.md`](docs/deploy.md).

## Run

```bash
tribunal/deploy_infra.sh                 # Foundry + AI Search + App Insights, writes .env (~10 min)
.venv/bin/python -m tribunal.seed        # index 5 policies + 43 prior claims (2 planted fraud signals)
tribunal/dev.sh                          # API :8000 + UI :5173 in tmux
.venv/bin/python -m tribunal.eval        # scorecard vs challenge-6/coverage_ground_truth.json
.venv/bin/python -m tribunal.mcp_check   # spawn the MCP server over stdio, check 3 tools + adjudicate crash1
tribunal/alerts.sh                       # upsert the fraud-referral alert rule + action group
```

## Demo script (3 claims, ~45 s each)

| claim | what the tribunal does | why it lands |
|---|---|---|
| crash2 | COMP-AUTO-001, clean corpus, photo matches statement: **approve**, net payout after $500 deductible | shows the happy path and the money maths |
| crash1 | LIAB-AUTO-001, own-vehicle damage: Policy Analyst cites Section 4.1, **deny**, letter explains it | catches the trap in the ground truth |
| crash4 | COMM-AUTO-001, coverage says APPROVED, but CLM-0412 has the same VIN and same damage paid 3 months ago under another name: Fraud Investigator 0.75+, Arbiter **refers**, disagreement panel shows adjuster vs fraud | agents visibly disagree, human gate matters |

Then open App Insights (or the Foundry project Tracing tab): one `tribunal.adjudicate`
trace with six `agent *` spans, parallel fan-out visible in the waterfall.

## Files

| file | role |
|---|---|
| `af_workflow.py` | default orchestrator: the tribunal as a Microsoft Agent Framework workflow graph. See below |
| `workflow.py` | reference orchestrator (`asyncio.gather`); still the source of `build_verdict`, `claim_summary`, `LABELS` |
| `prompts.py` | the four tribunal prompts + structuring prompt |
| `foundry.py` | Foundry agent creation (once per process) + streaming Responses calls |
| `search_tools.py` | AI Search: integrated vectorization (`AzureOpenAIVectorizer`), hybrid keyword+vector, semantic reranker, deterministic `policy_number` document anchor, Blob `source_url` on every chunk. See [docs/search.md](docs/search.md) |
| `seed.py` | policy chunks + synthetic prior claims with planted fraud |
| `blob_upload.py` | uploads challenge-0 policies/statements/images to Blob `claims-data/` |
| `api.py` | FastAPI: SSE stream, human decision log, sample claims |
| `telemetry.py` | OpenTelemetry to Application Insights, gen_ai.* attributes |
| `eval.py` | scorecard against Challenge 6 ground truth |
| `quality.py` | local groundedness/relevance/coherence/fluency/content-safety evaluator loop |
| `cloud_eval.py` | same evaluators uploaded to Foundry, plus the continuous-evaluation rule |
| `redteam.py` | Foundry AI Red Teaming Agent scan + local prompt-injection probe |
| `ocr_bench.py` | three-way OCR comparison (Doc Intelligence / Mistral / gpt-4.1-mini vision). See [docs/ocr-comparison.md](docs/ocr-comparison.md) |
| `deploy_aca.sh`, `apim_mcp.sh` | Container Apps + APIM deploy (REST + MCP). See [docs/deploy.md](docs/deploy.md) |
| `mcp_http.py` | MCP server over streamable HTTP, mounted next to the FastAPI app for the ACA/APIM deploy |
| `ui/` | React + Vite: live agent timeline, verdict card, human gate |

## Microsoft Agent Framework

`af_workflow.py` is the tribunal as an `agent_framework` (1.17.0) workflow graph: five
`Executor` classes, a typed dataclass message on every edge, one `add_fan_out_edges` group
and one `add_fan_in_edges` group, built with `WorkflowBuilder` and driven with
`workflow.run(..., stream=True)`. It is what `api.py` calls; `workflow.py` (`asyncio.gather`)
stays in the tree as the reference implementation both orchestrators build their verdict from.

```
ClaimIntake ─▶ OcrExecutor ─▶ StructureExecutor ─▶ Evidence ──fan-out──▶ Adjuster / Fraud / Policy
                                                                              │  Opinion (fan-in)
                                                                              ▼
                                                                       ArbiterExecutor ─▶ VerdictMsg
```

`WorkflowBuilder.build()` type-checks each edge's declared output against the next handler's
input, so a mis-wired edge fails at build time, not mid-claim; the fan-in is a real barrier
(the arbiter runs once, with all three opinions, no join code in tribunal logic); and
`WorkflowViz(...).to_mermaid()` gives an inspectable graph (`logs/af-graph.mmd`). Executor
output reaches the browser the same way `workflow.py`'s did: `ctx.yield_output(TribunalEvent)`
per token, drained live by the runner, replayed onto the same SSE contract, so the React UI,
`validate.cjs` and `mcp_server.py` needed no change. Details, message table and executor list:
`tribunal/docs/agent-framework.md`.

## Validation

```bash
PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash2   # headless walkthrough
.venv/bin/python -m tribunal.eval        # coverage decisions vs ground truth -> tribunal/data/scorecard.md
.venv/bin/python -m tribunal.quality     # groundedness / relevance / coherence / fluency / content safety -> quality.md
tribunal/alerts.sh                       # fraud-referral alert (fraud_score > 0.6, 15 min window) -> email
```

Each browser run records `logs/expect-<claim>/run.log`, `verdict.json` (DOM assertions),
screenshots (streaming, verdict, recorded decision) and `session.webm`. `validate.sh` drives
the same flow through the expect-cli daemon; it wedges on pages holding an SSE stream open,
so the Node script that uses expect-cli's bundled playwright-core is the reliable path.

The alert fired once, end to end: a crash4 run logged `refer fraud=0.80` to `AppTraces`, and
`tribunal-fraud-referrals` moved to Fired (Sev2) one 5-minute evaluation cycle later
(`logs/alert-fired.json`). Email delivery to the action group is not proven from here: Azure
requires the recipient to confirm a one-time verification mail first. Detail: `tribunal/docs/alerting.md`.

## MCP

`mcp_server.py` is an MCP stdio server wrapping the running API: `list_sample_claims`,
`adjudicate_claim(sample)`, `record_decision(claim_id, decision, reason)`. The API must be
running first (`tribunal/dev.sh`).

```bash
TRIBUNAL_API=http://localhost:8423 .venv/bin/python -m tribunal.mcp_server   # stdio
.venv/bin/python -m tribunal.mcp_check                                       # spawn it, check 3 tools, adjudicate crash1, assert deny
```

**VS Code**: `.vscode/mcp.json` is committed. Open the repo and Copilot Chat agent mode offers to
start `claims-tribunal`.

**Claude Desktop** (macOS): add to `~/Library/Application Support/Claude/claude_desktop_config.json`
and restart the app:

```json
{
  "mcpServers": {
    "claims-tribunal": {
      "command": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e/.venv/bin/python",
      "args": ["-m", "tribunal.mcp_server"],
      "cwd": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e",
      "env": { "TRIBUNAL_API": "http://localhost:8423" }
    }
  }
}
```

Also deployed: Container Apps + APIM MCP server at
`https://msagthack-apim-6ahymubsyajs6.azure-api.net/tribunal-mcp` — stdio above is the local path;
neither has auth. Detail: `tribunal/docs/deploy.md`.
`adjudicate_claim` takes ~40-45 s (six model calls): raise a short client tool timeout.
Only the 5 bundled samples are adjudicable; no upload tool. Detail: `tribunal/docs/mcp.md`.

## Limitations

- No authentication on the API: local demo only, bind to localhost.
- Decisions persist to a JSON file, not Cosmos.
- Prior-claims corpus is synthetic; two fraud signals are planted on purpose (CLM-0412, CLM-0431/0432).
- Similarity scores are Azure AI Search HNSW cosine scores (1.0 = identical); 0.75+ is treated as strong.
- gpt-4.1-mini is not fully deterministic: the Policy Analyst has occasionally cited the wrong
  policy on crash1 (approve instead of deny) and once on crash2 during a concurrent code change.
  The current scorecard run is 5/5; rerun `tribunal.eval` before a demo rather than trust one run.
- `tribunal/smoke.py` still drives the reference `workflow.py` orchestrator, not `af_workflow.py`;
  `-m tribunal.smoke` does not exercise the Agent Framework graph.
- Mistral OCR rate-limits (`429`) under repeated calls in a short window; don't run `eval`,
  `redteam` or a second `smoke` while a claim is on screen.
- MCP was not launched inside a real VS Code Copilot Chat window or the real Claude Desktop app,
  only proven via stdio + `mcp_check.py`.
- Only the fraud-referral alert firing was observed; the negative case (`fraud_score <= 0.6`
  staying quiet) was not separately tested.
- `ensure_index` still embeds `AZURE_OPENAI_KEY` in the index vectorizer definition (same as the
  baseline notebook); managed identity is the production shape.
- crash5 scored low in the cloud-eval run (groundedness/coherence/fluency 2.0) against an earlier
  local run (groundedness 4.0) — a real verdict-quality signal on crash5, unresolved.
- Continuous evaluation is registered and enabled; a scored row landing under Foundry
  **Evaluation ▸ Continuous evaluation** from a live UI-driven run has not been directly observed.
- Verdict nondeterminism reproduces through the deployed MCP path too: crash2 has returned
  approve (net $9,400 / $9,800 / $11,900) and once deny when OCR misread the policy number as
  `C044-AUTO-001`; crash1 matched ground truth (deny) in the one run captured.
- Deploy/APIM rough edges: no repo-root `.dockerignore`, `.vscode/mcp.json`'s remote entry not
  committed, no APIM `mcpTools` REST→MCP mapping (our own MCP server serves the tools instead).
  Detail: `tribunal/docs/deploy.md` § What failed, and why.
