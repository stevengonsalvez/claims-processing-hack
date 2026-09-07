# Demo script (5 minutes)

Pre-flight (2 min before): `tmux ls | grep dev-tribunal`, open http://localhost:5802, App Insights
transaction search open in another tab. Reset the story: `.venv/bin/python -m tribunal.precedent purge`
(crash4 must open on REFER before the override creates the first precedent). Convene crash2 once so the
Foundry agent versions are warm. Do not save any tribunal/*.py file during the demo (uvicorn --reload).

| t | beat | say | watch for |
|---|---|---|---|
| 0:00 | The problem | Claims adjudication is four jobs: damage, fraud, coverage, decision. Everyone else ships a pipeline. We ship a tribunal: four agents argue every claim, live. | title strip: gpt-4.1-mini, mistral-document-ai, text-embedding-3-large, AI Search, Foundry |
| 0:30 | crash2, approve | Handwritten statement, damage photo. OCR (Mistral) reads it, gpt-4.1-mini structures it, then three specialists run in parallel. | three cards streaming at once, timings on each card |
| 1:15 | verdict | Arbiter weighs them: approve, $500 deductible applied, letter written. Human gate: click Approve. | net payout tile, citations, "Human decision recorded" |
| 1:45 | crash1, deny | Liability-only policy, own-vehicle damage. The Policy Analyst reads the whole contract from AI Search and cites Section 4.1. | Policy Analyst badge DENIED, citation list, deny stamp |
| 2:30 | crash4, refer | Coverage is fine. But the fraud investigator ran a vector search over prior claims: same VIN, same damage, paid two months ago under another name. | Evidence panel: CLM-0412 highlighted; disagreement "adjuster vs fraud"; REFER |
| 3:30 | observability | Every agent is a span. | App Insights: `tribunal.adjudicate` with six `agent *` children, three overlapping |
| 4:00 | evidence of rigour | Scorecard 5/5 vs ground truth; evaluators; recorded browser runs; alert rule on fraud referrals. | `tribunal/data/scorecard.md`, `quality.md`, `explainers/claims-tribunal-validation.html` |
| 4:00 | claimant appeals | Open the claimant page from the bench. The claimant says: bought the Outback from Bennett in June, bill of sale attached. Session 2 answers verdict v1 clause by clause. | `/claim/<id>`: v1 vs v2 side by side, diff highlighted, outcome OVERTURN |
| 4:40 | adjuster signs off | Override with the SIU reason. That ruling is now a precedent: run crash4 again and the tribunal cites it. | Evidence board: Precedents row; verdict clause chip → precedent |
| 5:00 | beyond | Recycled-photo match (crash4.jpg filed under CLM-0412 at 0.94), Agent Framework executors, MCP in VS Code / Claude Desktop, Container Apps + APIM, three-way OCR benchmark, cloud eval + red team. | `tribunal/README.md` Challenge coverage + Beyond the spec |

Fallbacks: if a run stalls past 60 s, reload and re-convene (agents are cached per process). If the
Foundry endpoint 429s, `logs/expect-crash4/session.webm` is a recorded run of the same claim.

Numbers to quote (last recorded runs): crash2 approve 43.5 s, net $10,900; crash1 deny 36.5 s,
confidence 1.00; crash4 refer 44.7 s, fraud 0.85, CLM-0412 similarity 0.783.
