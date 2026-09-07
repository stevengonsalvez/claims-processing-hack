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
```

## Demo script (3 claims, ~45 s each)

| claim | what the tribunal does | why it lands |
|---|---|---|
| crash2 | COMM-AUTO-001, clean corpus, photo matches statement: **approve**, net payout after $500 deductible | shows the happy path and the money maths |
| crash1 | LIAB-AUTO-001, own-vehicle damage: Policy Analyst cites Section 4.1, **deny**, letter explains it | catches the trap in the ground truth |
| crash4 | COMM-AUTO-001, coverage says APPROVED, but CLM-0412 has the same VIN and same damage paid 3 months ago under another name: Fraud Investigator 0.8+, Arbiter **refers**, disagreement panel shows adjuster vs fraud | agents visibly disagree, human gate matters |

Then open App Insights (or the Foundry project Tracing tab): one `tribunal.adjudicate`
trace with six `agent *` spans, parallel fan-out visible in the waterfall.

## Files

| file | role |
|---|---|
| `workflow.py` | orchestration, fan-out / fan-in, verdict assembly |
| `prompts.py` | the four tribunal prompts + structuring prompt |
| `foundry.py` | Foundry agent creation (once per process) + streaming Responses calls |
| `search_tools.py` | AI Search indexes, embeddings, hybrid + vector queries |
| `seed.py` | policy chunks + synthetic prior claims with planted fraud |
| `api.py` | FastAPI: SSE stream, human decision log, sample claims |
| `telemetry.py` | OpenTelemetry to Application Insights, gen_ai.* attributes |
| `eval.py` | scorecard against Challenge 6 ground truth |
| `ui/` | React + Vite: live agent timeline, verdict card, human gate |

## Validation

```bash
API_PORT=8423 tribunal/validate.sh crash2     # expect-cli headless walkthrough, artifacts in logs/expect-crash2/
```

Each run records `run.log`, `verdict.json` (DOM assertions), screenshots (streaming, verdict,
recorded decision) and the Playwright session video.

## Limitations

- No authentication on the API: local demo only, bind to localhost.
- Decisions persist to a JSON file, not Cosmos.
- Prior-claims corpus is synthetic; two fraud signals are planted on purpose (CLM-0412, CLM-0431/0432).
- Similarity scores are Azure AI Search HNSW cosine scores (1.0 = identical); 0.75+ is treated as strong.
