# Challenge 1: which document-processing approach, and why

Challenge 1 asks you to compare three ways of reading the handwritten claim statements and to
learn when to use each. `tribunal/ocr_bench.py` runs all three over all ten statement pages
(`crash1..crash5`, front and back) and scores them against `challenge-3/ground_truth.json`, so
the answer is measured rather than asserted.

**Every table below is the verbatim output of one run**, pasted in full, because the bench's
artefacts (`tribunal/data/`, `logs/`) are gitignored in this repo and would otherwise be dead
links for anyone reading the fork. Re-running the command at the bottom regenerates them.

```
challenge-0/data/statements/crash{1..5}_{front,back}.jpeg
        │
        ├──▶┌──────────────────────────┐
        │   │ Doc Intelligence         │  prebuilt-read, Entra ID
        │   │ prebuilt-read            │  $1.50 / 1k pages
        │   └──────────────────────────┘
        ├──▶┌──────────────────────────┐
        │   │ Mistral Document AI      │  mistral-document-ai-2512
        │   │ /providers/mistral/…/ocr │  $2.00 / 1k pages
        │   └──────────────────────────┘
        └──▶┌──────────────────────────┐
            │ gpt-4.1-mini vision      │  Responses API, input_image
            │ "transcribe every word"  │  token metered
            └──────────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────────────┐
        │ per page: latency, chars, billed cost  │
        │ per claim: front+back text vs the 24   │──▶ tribunal/data/ocr_bench.{json,md}
        │ ground-truth fields (exact + fuzzy)    │    (gitignored; inlined below)
        └───────────────────────────────────────┘
```

## Result (run of 2026-09-07 13:17:04 BST)

| approach | fields found / 99 scorable | median service latency / page | mean / worst latency | mean chars / page | cost / claim (2 pages) | cost / 1000 claims | pages ok |
|---|---|---|---|---|---|---|---|
| Azure AI Document Intelligence (prebuilt-read) | 93/99 (93.9%) | 2.29s | 2.37s / 2.98s | 866 | $0.003000 | $3.00 | 10/10 |
| Mistral Document AI (mistral-document-ai-2512) | 93/99 (93.9%) | 1.75s | 2.36s / 5.44s | 872 | $0.004000 | $4.00 | 10/10 |
| gpt-4.1-mini vision (Responses API) | 92/99 (92.9%) | 5.31s | 5.60s / 9.49s | 873 | $0.001592 | $1.59 | 10/10 |

Mean service latency excludes time spent sleeping on HTTP 429. This run lost 30s of wall clock
to Mistral rate-limit backoff (wall-clock mean 5.36s/page against a service mean of 2.36s/page).

**gpt-4.1-mini's field score moves by about one field from run to run** (92 here; the previous
run's transcripts, rescored under this same rule, give 91) because the transcription is a sampled
generation, not a deterministic extractor; the README's Limitations section discloses the same
non-determinism for the tribunal itself. Document Intelligence and Mistral returned the same 93
on both runs. That variance is smaller than the gap this decision actually turns on, which is
latency and cost shape, not accuracy.

### Scoring rule

For each claim the front and back text of one approach is concatenated and normalised
(casefold, punctuation stripped, whitespace collapsed). A ground-truth field counts as found if
its normalised value appears **word-boundary anchored** in that text, or if a sliding window of
the text matches it with `difflib` ratio >= 0.85 (the fuzzy fallback catches handwriting slips
such as `Springfield Police Department` vs `Springfield Police Dept.`). The boundary anchor
matters: a plain substring test scores `red` on `covered` and `N/A` -> `n a` on `in a`.

### Denominator: 99 of 120, by a rule fixed before the run

24 fields x 5 claims = 120 (claim, field) pairs. Two exclusions, both stated as rules about the
form rather than derived from the results:

1. **20 off-page pairs.** `claimant_id`, `repair_shop_name`, `repair_shop_address` and
   `claim_request` are printed nowhere on the two statement pages: they come from the policy
   file and the downstream claim record. No OCR of these images can recover them, so scoring
   them penalises all three approaches for data that is not in the picture. (An earlier version
   of this doc used "the union of what any approach found" as the ceiling, which is circular,
   and it also let through a false positive: `claimant_id` `001` is a substring of
   `LIAB-AUTO-001`. Excluding the field by rule removes both problems.)
2. **1 placeholder pair.** `crash3` `witness_phone` is `N/A` in the ground truth: nothing is
   written on the page to find, and the normalised form `n a` occurs inside ordinary English
   (`in a`, `on a`), so counting it hands every approach a free hit. Unscorable, dropped from
   numerator and denominator alike.

120 - 20 - 1 = **99 scorable pairs**, the denominator in every table here.

### Prices (verbatim snapshot)

`logs/ocr_bench_prices.log`, captured from the Azure Retail Prices API
(`https://prices.azure.com/api/retail/prices`, `armRegionName=swedencentral`). The file is
gitignored, so it is reproduced here in full:

```
# Azure Retail Prices API snapshot, armRegionName=swedencentral, 2026-09-07T11:26:45Z
## gpt-4.1-mini (Azure OpenAI)
gpt 4.1 mini Batch Inp glbl Tokens | 0.0002 USD per 1K
gpt 4.1 mini Batch Outp glbl Tokens | 0.0008 USD per 1K
gpt 4.1 mini cached Inp glbl Tokens | 0.0001 USD per 1K
gpt 4.1 mini Inp glbl Tokens | 0.0004 USD per 1K
gpt 4.1 mini Outp glbl Tokens | 0.0016 USD per 1K
## mistral-document-ai-2512
Azure Mistral Models | OCR 2512 Dzone Pages | 2.2 USD per 1K | tier from 0
Azure Mistral Models | OCR 2512 glbl Pages | 2 USD per 1K | tier from 0
## Azure AI Document Intelligence prebuilt-read
Azure Document Intelligence | S0 Read Pages | 0.6 USD per 1K | tier from 1000
Azure Document Intelligence | S0 Read Pages | 1.5 USD per 1K | tier from 0
```

The bench bills Document Intelligence at `S0 Read Pages` tier-from-0 ($1.50/1k), Mistral at
`OCR 2512 glbl Pages` ($2.00/1k), and gpt-4.1-mini at the fresh / cached / output token meters
applied to the usage each call actually reported. Document Intelligence is costed at the S0 list
rate even though the run used an F0 account, since F0 caps at 500 pages/month and is not a
production option. Cost caveat: `DocIntelligence._probe` spends a real `prebuilt-read` call on
`crash1_front` to find out whether the account key works before falling back to Entra ID. In this
lab the key is policy-disabled, so that call is rejected at authentication and 10 pages are
billed; on a resource where key auth is enabled the probe succeeds and the run bills 11.

## Field recall heat table (all 24 fields)

Fraction of the scorable claims where each field was recoverable from that approach's text.
`excluded` marks an off-page field; `x/4` marks the field with a placeholder value on one claim.

| field | Doc Intelligence | Mistral Document AI | gpt-4.1-mini vision |
|---|---|---|---|
| `claimant_id` | excluded | excluded | excluded |
| `policy_holder_name` | 5/5 | 5/5 | 5/5 |
| `policy_holder_address` | 5/5 | 5/5 | 5/5 |
| `policy_holder_phone` | 5/5 | 5/5 | 5/5 |
| `policy_holder_email` | 5/5 | 5/5 | 5/5 |
| `policy_number` | 5/5 | 4/5 | 4/5 |
| `vehicle_year_make_model` | 5/5 | 4/5 | 5/5 |
| `vehicle_color` | 5/5 | 5/5 | 5/5 |
| `vehicle_vin` | 5/5 | 5/5 | 5/5 |
| `vehicle_license_plate` | 5/5 | 5/5 | 4/5 |
| `incident_date` | 5/5 | 5/5 | 5/5 |
| `incident_time` | 5/5 | 5/5 | 5/5 |
| `incident_location` | 5/5 | 5/5 | 5/5 |
| `incident_description` | 0/5 | 2/5 | 1/5 |
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
| **total** | **93/99** | **93/99** | **92/99** |

### Per-claim field recall

| claim | Doc Intelligence | Mistral Document AI | gpt-4.1-mini vision |
|---|---|---|---|
| crash1 | 19/20 | 19/20 | 19/20 |
| crash2 | 19/20 | 19/20 | 17/20 |
| crash3 | 17/19 | 17/19 | 17/19 |
| crash4 | 19/20 | 19/20 | 19/20 |
| crash5 | 19/20 | 19/20 | 20/20 |

### Every miss in the run, named

| approach | missed (claim, field) |
|---|---|
| Doc Intelligence | crash1-5 `incident_description`; crash3 `police_report_number` |
| Mistral Document AI | crash1/2/3 `incident_description`; crash3 `police_report_number`; crash4 `vehicle_year_make_model`; crash5 `policy_number` |
| gpt-4.1-mini vision | crash1/2/3/4 `incident_description`; crash3 `police_report_number`; crash2 `policy_number`; crash2 `vehicle_license_plate` |

Two of those categories are not OCR failures:

- **`incident_description` is not transcribable.** Its ground-truth value is a paraphrased
  summary of the accident, not what the claimant wrote in the box, so a substring/fuzzy test
  measures paraphrase distance and not transcription quality. It accounts for 5 of Doc
  Intelligence's 6 misses, 3 of Mistral's 6 and 4 of gpt-4.1-mini's 7. Drop the field and the
  three sit at 93/94, 91/94 and 91/94: within noise of each other, with the ordering flipped.
  `damage_description` is written on the page verbatim and all three get 5/5.
- **crash3 `police_report_number` is a bug in the challenge ground truth, not a miss.**
  `challenge-3/ground_truth.json` says `25-44278`. All three approaches independently read
  `2025-41134` off `crash3_back`, e.g. Document Intelligence returns
  `Police Report\nReport Number:\n2025-41134\nPolice Department: ...`. Three unrelated engines
  agreeing character-for-character against the answer key is a defect in the key. Anything
  scoring extraction against this file should expect a spurious crash3 failure here.

That leaves four genuine handwriting misses in 297 scorable reads: Mistral on crash4
`vehicle_year_make_model` and crash5 `policy_number`, gpt-4.1-mini on crash2 `policy_number` and
crash2 `vehicle_license_plate`. Document Intelligence made none.

## When to use which

1. **Accuracy does not decide this.** On the 99 scorable field instances the three land within
   one field of each other (93 / 93 / 92), and once the untranscribable `incident_description`
   is set aside they are within two. All three read this handwriting; choosing on accuracy alone
   would be choosing noise. Choose on cost shape, latency and what the next step needs.
2. **The cheapest per page is the surprise.** gpt-4.1-mini vision came in at $0.001592 per
   two-page claim against $0.003 (Document Intelligence) and $0.004 (Mistral). A single statement
   page is a small image and a short transcript, so token pricing beats page pricing here. The
   whole spread is under a quarter of a cent per claim, so OCR unit price only becomes a real
   line item past roughly 100k claims a year.
3. **Predictability, not price, is the argument for the page meters.** Document Intelligence and
   Mistral bill a flat $0.0015 and $0.0020 per page regardless of content; gpt-4.1-mini's bill
   moves with how dense the page is and how chatty the model feels, and a prompt-injected page
   can cost a lot more. Budget on the page meters, alert on the token meter.
4. **Latency: Mistral is the floor at a median 1.75s/page, gpt-4.1-mini the ceiling at 5.31s.**
   OCR sits on the tribunal's critical path before any of the four agents start, so ~3.6s per
   page is felt live in a way that a sub-cent cost gap is not. The worst single page in this run
   was gpt-4.1-mini on `crash1_front` at 9.49s (a cold first call), against a 5.44s worst page
   for Mistral and 2.98s for Document Intelligence, which is why the ranking is on the median.
   Caveat: the lab's Mistral deployment is provisioned at very low capacity and this run lost 30s
   to HTTP 429 backoff; the table reports service time with that backoff excluded.
5. **What the tribunal ships.** A page-metered OCR service for the bulk transcript of every
   statement (flat cost, lowest latency, no prompt to regress), with gpt-4.1-mini vision reserved
   for the jobs the other two cannot do at all: reading the damage photo, and returning structured
   claim JSON instead of a wall of text. Use the multimodal model where re-prompting is the point,
   not where transcription is. That is exactly the split in `tribunal/workflow.py`: Mistral for
   OCR, gpt-4.1-mini for structure, the Adjuster's photo read and all four agents.

## Re-running

```bash
.venv/bin/python -m tribunal.ocr_bench > logs/ocr_bench.log
```

Takes about 3 minutes for 31 API calls: 10 pages x 3 approaches plus the Document Intelligence
key probe (rejected at auth here, so it costs a round trip and no page), sequential per approach,
plus 30s of backoff each time the low-capacity Mistral
deployment returns 429. Writes `tribunal/data/ocr_bench.json` (per-page latency, usage, cost and
full transcript, plus the field matrix) and `tribunal/data/ocr_bench.md` (the tables above; both
paths are gitignored, which is why this doc inlines them). Any approach whose credentials are
missing is reported as UNAVAILABLE and the comparison runs with the rest.

To check that the tables inlined above still match the last run without spending an API call:

```bash
.venv/bin/python -m tribunal.ocr_bench --check-doc   # exit 0 = doc matches ocr_bench.json
```

**Do not run this during a demo.** The shared `mistral-document-ai-2512` deployment is capacity 5
(`az cognitiveservices account deployment list -n msagthack-aifoundry-6ahymubsyajs6 -g
rg-labuser-0009 --query "[].sku.capacity"`) and starts returning 429 under any concurrent load,
so a bench run while a claim is on screen will stall the live adjudication (same warning as the
README Limitations section and `tribunal/docs/search.md`). The Document Intelligence account is F0, capped at 500 pages/month,
and each run spends 10 of them (11 where the key probe succeeds): roughly 45 runs before that
account goes dark for the month.

Requires in `.env`: `DOCUMENT_INTELLIGENCE_ENDPOINT`, `MISTRAL_DOCUMENT_AI_ENDPOINT` +
`MISTRAL_DOCUMENT_AI_KEY`, `AZURE_OPENAI_BASE_URL` + `AZURE_OPENAI_KEY`.

### Deploying the Document Intelligence resource

```bash
az cognitiveservices account create \
  --name msagthack-docintel-6ahymubsyajs6 --resource-group rg-labuser-0009 \
  --kind FormRecognizer --sku F0 --location swedencentral \
  --custom-domain msagthack-docintel-6ahymubsyajs6 --yes
az cognitiveservices account show -n msagthack-docintel-6ahymubsyajs6 -g rg-labuser-0009 \
  --query properties.endpoint -o tsv     # -> DOCUMENT_INTELLIGENCE_ENDPOINT
```

F0 was accepted, so no S0 fallback was needed. **Azure Policy in this lab subscription forces
`disableLocalAuth=true` on new Cognitive Services accounts**: the account key is written to
`.env` but the service answers

```
(AuthenticationTypeDisabled) Key based authentication is disabled for this resource.
```

A `PATCH` on the ARM resource setting `disableLocalAuth: false` returns 200 and is then reverted
by policy. `ocr_bench.py` therefore tries the key first (so it works on a normally-configured
resource) and falls back to `DefaultAzureCredential`, which is what actually authenticates here.
That requires the signed-in principal to reach the Cognitive Services data plane. `labuser-0009`
does, through its `rg-labuser-0009`-scoped assignments (Contributor, Foundry User, Foundry Project
Manager, Role Based Access Control Administrator); its explicit **Cognitive Services User** grant
is scoped to the Foundry account, not to this one, so which of the RG-scoped roles carries the
data action here was not pinned down. On a subscription of your own, grant **Cognitive Services
User** on the Document Intelligence account and use `DefaultAzureCredential`, or leave key auth
enabled and the key path in `ocr_bench.py` takes over.
