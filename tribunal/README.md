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
| crash2 | COMM-AUTO-001, clean corpus, photo matches statement: **approve**, net payout after $500 deductible | shows the happy path and the money maths |
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
| `search_tools.py` | AI Search indexes, embeddings, hybrid + vector queries |
| `seed.py` | policy chunks + synthetic prior claims with planted fraud |
| `api.py` | FastAPI: SSE stream, human decision log, sample claims |
| `telemetry.py` | OpenTelemetry to Application Insights, gen_ai.* attributes |
| `eval.py` | scorecard against Challenge 6 ground truth |
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

Local only (stdio, no auth); remote/APIM exposure is a Challenge 4 follow-up.
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
