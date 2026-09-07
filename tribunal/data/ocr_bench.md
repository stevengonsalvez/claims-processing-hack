# Challenge 1: three-way OCR comparison on the handwritten statements

10 statement pages (crash1..crash5, front + back), 24 ground-truth fields per claim (120 field instances), scored against `challenge-3/ground_truth.json`.
A field counts as found when its normalised ground-truth value (casefold, punctuation stripped, whitespace collapsed) appears word-boundary anchored in the concatenated front+back OCR text for that claim, or a sliding window of that text matches it with difflib ratio >= 0.85.

Generated: 2026-09-07 13:26:15 BST by `.venv/bin/python -m tribunal.ocr_bench`.

## Head to head

| approach | fields found / 99 scorable | median service latency / page | mean / worst latency | mean chars / page | cost / claim (2 pages) | cost / 1000 claims | pages ok |
|---|---|---|---|---|---|---|---|
| Azure AI Document Intelligence (prebuilt-read) | 93/99 (93.9%) | 2.25s | 2.43s / 4.01s | 866 | $0.003000 | $3.00 | 10/10 |
| Mistral Document AI (mistral-document-ai-2512) | 92/99 (92.9%) | 1.28s | 2.09s / 5.24s | 872 | $0.004000 | $4.00 | 10/10 |
| gpt-4.1-mini vision (Responses API) | 93/99 (93.9%) | 6.49s | 6.37s / 7.50s | 884 | $0.001528 | $1.53 | 10/10 |

**Denominator.** 99 of the 120 (claim, field) pairs are scorable, by a rule stated before the results rather than derived from them. Excluded: the 20 pairs of `claimant_id`, `repair_shop_name`, `repair_shop_address`, `claim_request`, which are printed nowhere on the two statement pages (they come from the policy file and the downstream claim record), so no OCR of these images can recover them; and `crash3` `witness_phone` (1 of the remaining 100), whose ground-truth value is a placeholder such as `N/A`: there is nothing on the page to find, and the normalised form `n a` is a substring of ordinary English (`in a`, `on a`), which would hand every approach a free hit. Matching is word-boundary anchored for the same reason.

Mean service latency excludes time spent sleeping on HTTP 429. The lab's `mistral-document-ai-2512` deployment is provisioned at very low capacity: this run lost 60s to rate-limit backoff, making its wall-clock mean 8.09s/page against a service mean of 2.09s/page. Latency is ranked on the median, not the worst page: the slowest single page in this run was gpt-4.1-mini vision (Responses API) on `crash5_back` at 7.50s, a service-side outlier rather than steady-state speed. The gpt-4.1-mini client carries a 120s request timeout so a stalled connection costs one retry instead of hanging the run.

### Price basis

| approach | meter (Azure Retail Prices API, swedencentral) | unit price |
|---|---|---|
| Azure AI Document Intelligence (prebuilt-read) | Azure Document Intelligence / S0 Read Pages (tier from 0) | $1.50 per 1,000 pages |
| Mistral Document AI (mistral-document-ai-2512) | Azure Mistral Models / OCR 2512 glbl Pages | $2.00 per 1,000 pages |
| gpt-4.1-mini vision (Responses API) | Azure OpenAI / gpt 4.1 mini Inp glbl + cached Inp glbl + Outp glbl Tokens | $0.40 / $0.10 cached / $1.60 per 1M input / cached / output tokens |

Snapshot of the raw price query is in `logs/ocr_bench_prices.log` (`https://prices.azure.com/api/retail/prices`). Document Intelligence is billed at the S0 list rate even though this run used an F0 account, because F0 caps at 500 pages/month and is not a production option.

## Field recall heat table

Fraction of the scorable claims where each field was recoverable from that approach's text. `excluded` marks a field that is off-page for every claim; a denominator below 5 marks a field with a placeholder value on one of the claims. `incident_description` and `damage_description` are the fields to read carefully: their ground-truth values are paraphrased summaries of the handwriting, not what is written on the page, so a miss there measures paraphrase distance, not transcription quality.

| field | Azure AI Document Intelligence (prebuilt-read) | Mistral Document AI (mistral-document-ai-2512) | gpt-4.1-mini vision (Responses API) |
|---|---|---|---|
| `claimant_id` | excluded | excluded | excluded |
| `policy_holder_name` | 5/5 | 5/5 | 5/5 |
| `policy_holder_address` | 5/5 | 5/5 | 5/5 |
| `policy_holder_phone` | 5/5 | 5/5 | 5/5 |
| `policy_holder_email` | 5/5 | 5/5 | 5/5 |
| `policy_number` | 5/5 | 3/5 | 4/5 |
| `vehicle_year_make_model` | 5/5 | 4/5 | 5/5 |
| `vehicle_color` | 5/5 | 5/5 | 5/5 |
| `vehicle_vin` | 5/5 | 5/5 | 5/5 |
| `vehicle_license_plate` | 5/5 | 5/5 | 4/5 |
| `incident_date` | 5/5 | 5/5 | 5/5 |
| `incident_time` | 5/5 | 5/5 | 5/5 |
| `incident_location` | 5/5 | 5/5 | 5/5 |
| `incident_description` | 0/5 | 2/5 | 2/5 |
| `damage_description` | 5/5 | 5/5 | 5/5 |
| `witness_name` | 5/5 | 5/5 | 5/5 |
| `witness_phone` | 4/4 | 4/4 | 4/4 |
| `police_department` | 5/5 | 5/5 | 5/5 |
| `police_report_number` | 4/5 | 4/5 | 4/5 |
| `repair_shop_name` | excluded | excluded | excluded |
| `repair_shop_address` | excluded | excluded | excluded |
| `claim_request` | excluded | excluded | excluded |
| `signature_name` | 5/5 | 5/5 | 5/5 |
| `signature_date` | 5/5 | 5/5 | 5/5 |
| **total** | **93/99** | **92/99** | **93/99** |

## Per-claim field recall

| claim | Azure AI Document Intelligence (prebuilt-read) | Mistral Document AI (mistral-document-ai-2512) | gpt-4.1-mini vision (Responses API) |
|---|---|---|---|
| crash1 | 19/20 | 19/20 | 19/20 |
| crash2 | 19/20 | 18/20 | 17/20 |
| crash3 | 17/19 | 17/19 | 17/19 |
| crash4 | 19/20 | 19/20 | 20/20 |
| crash5 | 19/20 | 19/20 | 20/20 |

## When to use which

1. **Accuracy does not decide this.** On the 99 scorable field instances the three land within 1 field of each other (93 Document Intelligence, 92 Mistral, 93 gpt-4.1-mini). All three read this handwriting; picking on accuracy alone would be picking noise, so choose on cost, latency and what the next step needs.
2. **The cheapest per page is the surprise: gpt-4.1-mini vision (Responses API)** at $0.001528 per two-page claim ($1.53 per 1,000 claims) versus $0.004000 for Mistral Document AI (mistral-document-ai-2512). The whole spread is under $0.0025 a claim, so OCR unit price only becomes a real line item past roughly 100k claims a year.
3. **Predictability, not price, is the page-meter argument.** Document Intelligence and Mistral bill a flat $0.0015 and $0.0020 per page no matter what is on it; gpt-4.1-mini is token-metered, so a dense page or a chatty model costs more and a prompt-injected page can cost a lot more. Budget with the page meters, alert on the token meter.
4. **Latency: Mistral Document AI (mistral-document-ai-2512) is the floor at a median 1.28s/page, gpt-4.1-mini vision (Responses API) the ceiling at 6.49s/page** (service time, 429 backoff excluded); the worst single page in the run was gpt-4.1-mini vision (Responses API) on `crash5_back` at 7.5s. OCR sits on the tribunal's critical path before four agents start, so the ~5.2s median gap per page is felt live in a way the sub-cent cost gap is not.
5. **What the tribunal ships**: a page-metered OCR service for the bulk transcript of every statement (flat cost, lowest latency, no prompt to regress), and gpt-4.1-mini vision reserved for the jobs the other two cannot do at all - reading the damage photo, and returning structured claim JSON instead of a wall of text. Use the multimodal model where re-prompting is the point, not where transcription is.
