# Beyond the spec: precedent, appeals, recycled photos, explainable verdicts

Four pillars bolted onto the tribunal, all wired through the same SSE contract the
React bench already speaks. Nothing new in the model set (gpt-4.1-mini,
mistral-document-ai-2512, text-embedding-3-large) and no new dependency.

```
 adjuster override ──▶ precedents index ──┐
 photo ──▶ description ──▶ claim-photos ──┤
 prior claims (AI Search) ────────────────┼──▶ evidence event ──▶ Fraud / Policy / Arbiter
 policy sections (AI Search) ─────────────┘                              │
                                                                         ▼
                                          verdict { clauses[evidence_refs], what_if }
                                                                         │
 claimant appeal (text + optional photo) ──▶ session 2 ──▶ appeal.verdict { v1, v2, outcome, diff }
```

## Evidence step (one place, three orchestrators)

`tribunal/workflow.py` owns `gather_evidence(claim, photo_path, claim_id, extra_text)`:
four retrievals in one `asyncio.gather` (prior claims, policy sections, adjuster
precedents, prior-photo match). `tribunal/af_workflow.py` (the default orchestrator),
`tribunal/workflow.py` (the reference implementation) and `tribunal/appeal.py` all call
it, so all three see identical evidence. Precedent search and photo matching are
best-effort: a missing index or a slow describe degrades to an empty list and prints to
stderr rather than sinking an adjudication.

`evidence_event(ev)` builds the SSE payload; `precedent_block(ev)` / `photo_block(ev)`
build the prompt context appended to the Fraud Investigator (both), the Policy Analyst
(precedents) and the Arbiter (both).

`evidence` event, new fields:

| field | shape |
|---|---|
| `precedents` | `[{claim_id, human_decision, reason, tribunal_decision, precedent_id, similarity}]` |
| `photo_matches` | `[{claim_id, file_name, filed_at, similarity, description}]` |
| `photo_description` | forensic description of the incoming photo (gpt-4.1-mini) |

Prompt rules (`tribunal/prompts.py`): a photo match above **0.85** to a *different*
`claim_id` is strong recycled-photo evidence and must be recorded as an `image_anomaly`
with that claim id; an adjuster precedent that overrode a `refer` for the same VIN /
policy / holder with a documented reason counts in the claimant's favour, and the
Arbiter must name it instead of referring the same concern twice. Precedents settle
disputed facts, they never create coverage.

## Explainable verdicts

The Arbiter emits `clauses: [{clause, evidence_refs: [{type, ref, label}]}]` where `type`
is `policy` | `prior_claim` | `photo` | `precedent` and `ref` is the section number, claim
id, file name or precedent id. `build_verdict` passes them through untouched and adds
`what_if: {deductible, limit, covered}` so the UI can recompute
`net = max(0, min(covered, limit || covered) - deductible)` locally for the slider,
without another round trip.

`_split()` in `af_workflow.py` / `appeal.py` recovers the JSON object when the model
emits a bare `===` instead of `===JSON===` (observed once on the appeals arbiter, which
otherwise silently produced a `refer` with `referral_reason: "arbiter output unparseable"`).

## API

| route | body | response |
|---|---|---|
| `POST /decision` | `{claim_id, human_decision, reason, verdict}` | `{ok, count, precedent_id}` |
| `GET /claims/{claim_id}` | - | `{verdict, decisions: [...], appeals: [...]}` |
| `POST /appeal` | multipart `claim_id`, `appeal_text`, `photo?` | `text/event-stream` |

`/decision` appends the ruling to `tribunal/data/decisions.json` (the verdict blob is
stripped from that row: it lives in the case file) **and** calls
`precedent.record_precedent(verdict, human_decision, reason)`, so the next tribunal that
searches `precedents` retrieves it. A search outage records `precedent_error` on the row
and still writes the audit trail.

Case files live in `_claims` in memory and are written through to
`tribunal/data/claims.json`, so `uvicorn --reload` restarting on an edit does not lose the
demo history. `/appeal` streams the standard events plus `appeal.verdict
{v1, v2, outcome, diff}`, appends the appeal to the case file and promotes `v2` to the
claim's current verdict.

## Proof

`tribunal/validate_appeal.cjs` drives the whole chain through the API (node's `fetch`
plus a 30-line SSE parser, no browser) and asserts seven checks:

```
node tribunal/validate_appeal.cjs crash4 http://localhost:8423
# logs/appeal-crash4/{run.log,adjudicate.sse,appeal.sse,result.json}
```

Raw curl transcripts of the same flow are in `logs/beyond-crash4.log` (adjudication) and
`logs/beyond-appeal.log` (appeal). From the crash4 run on 2026-09-07:

- `evidence.precedents[0]` = `PREC-EVAL-crash4-123534`, `human_decision: override`,
  reason "SIU cleared the VIN reuse: vehicle sold by Bennett in June, bill of sale on
  file", similarity 0.768.
- `evidence.photo_matches[0]` = `CLM-0412 / crash4.jpg`, filed 2025-05-14,
  **similarity 0.942** - the planted recycled photo, well above the 0.85 threshold.
- verdict: `approve`, `what_if {deductible: 1000, limit: null, covered: 4800}`, three
  clauses, one of which cites `{"type": "precedent", "ref": "PREC-EVAL-crash4-123534"}` -
  the precedent is what turned the seeded `refer` into an `approve`.
- `POST /decision` -> `{"ok": true, "count": 14, "precedent_id": "PREC-CLM-20260907-0178AA-130946"}`;
  `GET /claims/CLM-20260907-0178AA` then shows that decision and its precedent id.
- `POST /appeal` ("I bought the Outback from Andrew Bennett in June 2025, bill of sale
  attached, this is my first claim", no photo) -> `appeal.verdict` with
  `outcome: overturn` and `diff` on decision (`refer` -> `approve`), confidence
  (0 -> 0.95), referral_reason and net payout (0 -> 3800); the appeal clauses answer v1
  clause by clause with `evidence_refs` to the bill of sale and the precedent.

### Run-to-run flakiness (honest note)

Three `validate_appeal.cjs` runs on 2026-09-07 (`logs/beyond-validate-appeal.log`,
`logs/appeal-crash4/`):

| run | precedents | photo matches > 0.85 | v1 clauses | appeal |
|---|---|---|---|---|
| 12:12 | 2 | 0 (describe hit `Connection reset by peer`) | 3 | uphold, approve -> approve |
| 12:14 | 3 | 2 (`CLM-0412` 0.936) | 0 (arbiter call errored upstream) | overturn, refer -> approve, 4 fields |
| curl (`logs/beyond-crash4.log`) | 1 | 1 (`CLM-0412` 0.942) | 3 (one citing the precedent) | overturn, refer -> approve, 4 fields |

Two transient upstream failures, in different agents, on different runs:

- the photo describe call lost its connection - now retried once inside
  `photo_index.describe` (the 12:14 run, after the retry landed, matched again);
- the Arbiter call itself raised, so there was no output to parse and `build_verdict`
  fell back to `refer` / `referral_reason: "arbiter output unparseable"`. That fallback is
  the designed behaviour, not a clause bug: every run where the Arbiter actually answered
  produced three clauses with `evidence_refs`. The Arbiter is not retried yet - that is
  the obvious next hardening step.

## Rehearsal reset (run before every demo run)

Every rehearsal writes a new precedent, and a polluted `precedents` index makes crash4
open on APPROVE with nothing left to override. Reset with:

```bash
.venv/bin/python -m tribunal.precedent purge          # deletes every precedent doc
printf '[]' > tribunal/data/decisions.json
printf '{}' > tribunal/data/claims.json               # API re-reads on next reload
```

`PRECEDENTS_INDEX=<name>` points `precedent.py` at a different (empty) index if you would
rather keep the polluted one for later inspection.

Post-purge crash4 baseline (logs/crash4-postpurge.sse, 2026-09-07):
`precedents: 0`, `photo_matches: CLM-0412 @ 0.935`, decision **deny**, 2 clauses, what-if present.
The override narrative runs off the deny (adjuster overrides to approve, "SIU cleared VIN reuse"),
not off a refer.

## Arbiter retry

`ArbiterExecutor.run` now retries the Arbiter call once on any exception before falling back to
`build_verdict`'s `arbiter output unparseable` path (tokens stream on the first attempt only, so a
retry does not double up the live prose). Same one-shot shape as `photo_index.describe`.

## Durable case file

`data/claims.json` and `data/decisions.json` are written via `.tmp` + `os.replace` and loaded
through a tolerant `_load`, so a `--reload` landing mid-write can no longer truncate the file and
crash the API on boot.
