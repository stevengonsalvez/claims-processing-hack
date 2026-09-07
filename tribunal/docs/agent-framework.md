# Challenge 4 — Microsoft Agent Framework orchestration

`tribunal/af_workflow.py` is the tribunal expressed as a **Microsoft Agent Framework
workflow graph** (`agent_framework` 1.17.0): five `Executor` classes, typed dataclass
messages on every edge, one fan-out edge group and one fan-in edge group, built with
`WorkflowBuilder` and driven with `workflow.run(..., stream=True)`.

It is the default orchestrator: `tribunal/api.py` imports `adjudicate` from here.
`tribunal/workflow.py` stays in the tree as the reference implementation and as the
source of `build_verdict`, `claim_summary` and `LABELS`, so both paths produce
byte-identical verdicts.

## The graph

```
                         ClaimIntake
                              │  (workflow input)
                              ▼
                     ┌─────────────────┐
                     │  OcrExecutor    │  mistral-document-ai-2512, pages in parallel
                     └────────┬────────┘
                              │ OcrText
                              ▼
                     ┌─────────────────┐
                     │ StructureExec.  │  gpt-4.1-mini + AI Search (prior claims, policies)
                     └────────┬────────┘
                              │ Evidence          ── add_fan_out_edges ──
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
 ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
 │AdjusterExecutor│  │ FraudExecutor  │  │ PolicyExecutor │   run concurrently
 └───────┬────────┘  └───────┬────────┘  └───────┬────────┘
         │ Opinion           │ Opinion           │ Opinion
         └───────────────────┼───────────────────┘
                             ▼                    ── add_fan_in_edges ──
                    ┌─────────────────┐
                    │ ArbiterExecutor │  fires once, with list[Opinion]
                    └────────┬────────┘
                             │ VerdictMsg  (ctx.yield_output → workflow output)
                             ▼
                     verdict + payout + letter
```

`python -m tribunal.af_workflow` prints the same graph as mermaid (via `WorkflowViz`);
the output is kept at `logs/af-graph.mmd`.

## Typed messages

Every edge carries a dataclass. `WorkflowBuilder.build()` type-checks the source
executor's declared output type against the target's handler parameter type, so a
mis-wired edge fails at build time, not mid-claim.

| message | edge | payload |
|---|---|---|
| `ClaimIntake` | workflow input → `ocr` | `claim_id`, `statement_paths`, `photo_path` |
| `OcrText` | `ocr` → `structure` | transcribed pages, per-page OCR errors, photo path |
| `ClaimRecord` | nested in `Evidence` and `Opinion` | the structured claim dict + claim id + photo path |
| `Evidence` | `structure` → 3 specialists (fan-out) | `ClaimRecord`, prior-claim hits, policy chunks |
| `Opinion` | 3 specialists → `arbiter` (fan-in) | agent name, prose, parsed JSON ruling, `ClaimRecord` |
| `VerdictMsg` | `arbiter` → workflow output | `claim_id` + the assembled verdict dict |
| `TribunalEvent` | workflow output (streamed) | `{event, data}` pair replayed into `emit` |

`ClaimRecord` travels inside `Evidence` and `Opinion` rather than on its own edge: the
graph shape the challenge asks for is `structure → fan-out`, so the claim record is
carried by the fan-out message instead of adding a node between them.

## Executors

| executor | id | handler in → out | what it does |
|---|---|---|---|
| `OcrExecutor` | `ocr` | `ClaimIntake` → `OcrText` | `extract_text_with_ocr` (Challenge 2, Mistral Document AI) on every statement page via `asyncio.to_thread`, joined with `--- PAGE ---` |
| `StructureExecutor` | `structure` | `OcrText` → `Evidence` | streams `prompts.STRUCTURE` through `foundry.run_agent`, parses the claim, then runs `search_prior_claims` + `search_policies` concurrently |
| `AdjusterExecutor` | `adjuster` | `Evidence` → `Opinion` | damage + repair cost, claim JSON plus the photo as an `input_image` part |
| `FraudExecutor` | `fraud` | `Evidence` → `Opinion` | claim JSON + top-5 vector hits over `prior-claims` + the photo |
| `PolicyExecutor` | `policy` | `Evidence` → `Opinion` | claim JSON + the retrieved policy sections, text only |
| `ArbiterExecutor` | `arbiter` | `list[Opinion]` → `VerdictMsg` | weighs the three, then `build_verdict(...)` from `tribunal.workflow` |

The three specialists share `_MemberExecutor._opinion`: start event, `agent_span`,
streamed `run_agent`, `split_opinion` prose/JSON split, and a `try/except` that turns a
failed member into an `error` event plus an `Opinion` carrying `{"error": ...}` — one bad
member cannot sink the tribunal, exactly as in `workflow.py`.

## Streaming: how framework events become SSE

```
executor ──ctx.yield_output(TribunalEvent)──▶ runner event queue
                                                    │  (drained live, mid-superstep)
adjudicate: async for ev in workflow.run(..., stream=True)
                                                    │
                        ev.type == "output" ─────────┴──▶ emit(ev.data.event, ev.data.data)
                                                          └──▶ FastAPI SSE ──▶ React UI
```

`ctx.yield_output` is the sanctioned executor-side emission path (`WorkflowEvent.emit` /
`type='data'` are deprecated in 1.17.0, and `ctx.add_event` refuses to publish
`output`/`intermediate` events from executor code). The runner polls its event queue
*while* the superstep is still running (`_runner.run_until_convergence`), so tokens reach
the browser as they are generated rather than at superstep boundaries. The only added
latency is at superstep edges, bounded by the runner's 50 ms event-poll timeout.

`adjudicate(claim_id, statement_paths, photo_path, emit)` keeps the exact event contract
of `tribunal/workflow.py` — `agent.start` / `agent.token` / `agent.done` / `claim` /
`evidence` / `verdict` / `error` — so the React UI, `validate.cjs` and `mcp_server.py`
needed no change. Telemetry is unchanged too: one `tribunal.adjudicate` span (tagged
`orchestrator=agent-framework`) with six `agent *` child spans, and `record_verdict` is
called on the verdict before it is emitted.

## Why the graph, not `asyncio.gather`

`workflow.py` gets parallelism from `asyncio.gather`; the framework version gets the same
parallelism from the *topology*, and gets three things `gather` does not give you:

- **Build-time type checking.** `add_fan_in_edges([adjuster, fraud, policy], arbiter)`
  only builds because `ArbiterExecutor.run` accepts `list[Opinion]`.
- **Fan-in as a barrier.** The arbiter is invoked once, with all three opinions, by the
  `FanInEdgeGroup` — there is no explicit join in tribunal code.
- **An inspectable graph.** `WorkflowViz(...).to_mermaid()`, `graph_signature_hash`, and
  per-executor `executor_invoked` / `executor_completed` events come for free.

## Proof

```bash
.venv/bin/python -m tribunal.af_workflow                     # mermaid graph  -> logs/af-graph.mmd
curl -s -N -m 240 -F sample=crash4 localhost:8423/adjudicate > logs/af-sse-crash4.log
PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash2 \
    http://localhost:5802 http://localhost:8423                # -> logs/expect-crash2/
```

| run | log | result |
|---|---|---|
| crash2 (CLI) | `logs/af-smoke-crash2.log` | `approve`, net $9,900, 6 agent sections |
| crash1 (CLI) | `logs/af-smoke-crash1-r2.log` | `deny`, Section 4.1 liability-only exclusion |
| crash4 (SSE) | `logs/af-sse-crash4.log` | 1974 `agent.token` events, 1 `verdict` = `refer`, CLM-0412 cited |
| crash2 (browser) | `logs/af-validate-crash2.out`, `logs/expect-crash2/` | verdict `approve` in 32.5 s, human decision recorded |

The crash4 SSE log shows `adjuster` / `fraud` / `policy` token events interleaving in
short alternating runs, which is the fan-out actually running concurrently.
