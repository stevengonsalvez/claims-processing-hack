# Challenge 1 — Azure AI Search: integrated vectorization, hybrid + semantic, Blob source docs

The tribunal's retrieval layer (`tribunal/search_tools.py`, `tribunal/seed.py`,
`tribunal/blob_upload.py`) covers the Challenge 1 basics and then some.

```
┌───────────────────┐   upload   ┌──────────────────────┐
│ challenge-0/data  │───────────▶│ Blob: claims-data/   │
│ policies/*.md     │            │  policies|statements │
│ statements/*.jpeg │            │  |images             │
│ images/*.jpg      │            └──────────┬───────────┘
└───────────────────┘                       │ source_url
          │ chunk (## Section)              ▼
          ▼                        ┌──────────────────────────────┐
┌───────────────────┐  client-side │ insurance-documents-index    │
│ tribunal.seed     │─────────────▶│  fields + content_vector     │
└───────────────────┘   vectors    │  vectorizer insurance-*      │
                                   │  semantic  insurance-semantic│
   query text ─────────────────────▶│  VectorizableTextQuery      │
                                   └──────────────────────────────┘
```

## What is in place

| Requirement | Where | Notes |
|---|---|---|
| Source documents in Blob Storage | `tribunal/blob_upload.py` | container `claims-data`, prefixes `policies/`, `statements/`, `images/`; 20 blobs, `overwrite=True` so it is idempotent |
| `source_url` on every policy chunk | `tribunal/seed.py` (`blob_url()`) | deterministic URL, no extra network call at seed time; selected back in `search_policies` |
| Integrated vectorization | `ensure_index()` | `AzureOpenAIVectorizer("insurance-vectorizer")` on **both** indexes, wired into the `tribunal-profile` vector profile |
| Server-side embedding at query time | `hybrid(..., server_side_vector=True)` | `VectorizableTextQuery` — no client-side embedding call |
| Keyword + vector hybrid | `hybrid()` | `search_text` + `vector_queries` in one request (RRF fusion) |
| Semantic ranker | `hybrid(..., semantic=True)`, used by `search_policies` | `query_type="semantic"`, `semantic_configuration_name="insurance-semantic"` (title = `title`, content = `content`); returns `@search.reranker_score` as `reranker_score` on ranked hits, and as `doc_reranker_score` on the chunks of the winning document (document-level, not per-chunk) |
| Deterministic document selection | `seed.py` stamps `policy_number` per chunk, `search_policies` filters `policy_number eq '<pn>'` | the claim's policy code is resolved against the index's five codes (OCR spacing/punctuation tolerated: `"comm auto 001"` -> `COMM-AUTO-001`), then chunks are pulled by exact filter, so a claim is never judged against another customer's contract |
| Graceful degradation | `search_policies` | two independent fallbacks: (a) an exception from the semantic call is printed and retried as plain hybrid, so a disabled ranker or an exhausted query budget still returns a document; (b) a missing or unrecognised policy number falls back to ranking the corpus by claim text, which **loses document-selection accuracy** (damage wording alone does not separate the five auto policies) and prints `[search] no usable policy_number ...` so the degradation is visible in the transcript |

`search_policies` only ranks when it has to:

```
policy_number from structuring
        │
   ┌────┴─────────────┐
   │ resolves to one  │ yes ──▶ filter policy_number eq '<pn>'  ──▶ all chunks, deterministic
   │ of the 5 codes?  │
   └────┬─────────────┘
        │ no (null / garbled beyond repair)
        ▼
 keyword + vector + semantic rank ──▶ top doc's chunks  (best-effort, logged)
```

Public function signatures (`search_policies`, `search_prior_claims`, `hybrid`,
`ensure_index`, `upload`, `embed`) are unchanged — `hybrid` only gained two keyword
arguments with defaults, so `tribunal/workflow.py` and `tribunal/af_workflow.py`
are unaffected.

### Vectorizer endpoint

The Foundry account is configured in `.env` as
`https://msagthack-aifoundry-6ahymubsyajs6.cognitiveservices.azure.com/`, but the
search service's `AzureOpenAIVectorizer` only accepts the `*.openai.azure.com` host.
`_vectorizer_resource_url()` maps one to the other (the same mapping
`challenge-1/scripts/policiesprocessing.ipynb` does in `_format_azure_openai_endpoint`);
the account answers on both hosts.

### Semantic ranker on the service

`az search service update --semantic-search` was **not needed**:
`msagthack-search-6ahymubsyajs6` (sku `standard`, Sweden Central) already reports
`semanticSearch: free`. Recorded in `logs/search-semantic-status.log`. The free tier
caps semantic queries at 1,000/month **per search service** (this service is not shared
with the rest of the lab subscription), so a demo's handful of queries is not a budget
risk; the fallback path exists for a ranker that is disabled or erroring, not for cost.

### Why documents still carry client-side vectors

Integrated vectorization is proven on the **query** side (`VectorizableTextQuery`).
Documents keep client-side vectors written by `upload()` because the index is fed by
direct `upload_documents` calls, not by an indexer + skillset pipeline — the tribunal
chunks policies on `## Section` headers so each citation maps to a named contract
section, which a generic split skill would not preserve. Both paths hit the same
`text-embedding-3-large` deployment, so the vectors are compatible.
(ponytail: no indexer/skillset; the query-side vectorizer is what the demo shows.)

## Proof

```bash
.venv/bin/python -m tribunal.blob_upload         # logs/blob-upload.log
.venv/bin/python -m tribunal.seed policies       # logs/seed-ch1-fix.log
.venv/bin/python -m tribunal.search_tools        # logs/search-ch1-proof-fix.log
.venv/bin/python -m tribunal.smoke crash4        # logs/smoke-crash4-ch1-fix.log (+ -run2)
```

Document selection, all five codes plus the degraded paths
(`logs/search-ch1-policy-pick-fix.log`):

```
'LIAB-AUTO-001'          -> ['liability_only_policy.md'] (12 chunks)
'COMM-AUTO-001'          -> ['commercial_auto_policy.md'] (15 chunks)
'COMP-AUTO-001'          -> ['comprehensive_auto_policy.md'] (11 chunks)
'MOTO-001'               -> ['motorcycle_policy.md'] (16 chunks)
'HV-AUTO-001'            -> ['high_value_vehicle_policy.md'] (14 chunks)
'comm-auto 001'          -> ['commercial_auto_policy.md'] (15 chunks)   # OCR-garbled, still exact
[search] no usable policy_number ('XXX-NOT-A-POLICY-999'); ranking policies by claim text - document selection is best-effort
[search] no usable policy_number (''); ranking policies by claim text - document selection is best-effort
```

Before this change the same three scripted claims all landed on
`comprehensive_auto_policy.md` when the ranker had no policy-number anchor
(`logs/review-ch1-policy-pick.log`).

Observed (`logs/search-ch1-proof-fix.log`):

```
index insurance-documents-index: vectorizers=['insurance-vectorizer']
  resource_url=https://msagthack-aifoundry-6ahymubsyajs6.openai.azure.com
  deployment=text-embedding-3-large semantic=['insurance-semantic']

[1] VectorizableTextQuery (integrated vectorization, no client-side embed)
    score=0.0323   Comprehensive Auto Insurance Policy · Section 4: Deductibles
    score=0.0313   Comprehensive Auto Insurance Policy · Section 6: Additional Benefits
    score=0.031    Commercial Auto Insurance Policy · Deductibles

[2] semantic query_type=insurance-semantic (reranker scores)
    score=0.0323   reranker=2.4445 Comprehensive Auto Insurance Policy · Section 4: Deductibles
    score=0.031    reranker=2.4326 Commercial Auto Insurance Policy · Deductibles
    score=0.0156   reranker=2.3271 High-Value Vehicle Insurance Policy · Coverage Limits and Options

[3] source_url + deterministic policy_number anchor (search_policies)
    policy_number='COMP-AUTO-001'    -> 11 chunks ['comprehensive_auto_policy.md']
      source_url=https://msagthacksa6...blob.core.windows.net/claims-data/policies/comprehensive_auto_policy.md
    policy_number='comm auto 001'    -> 15 chunks ['commercial_auto_policy.md']
    policy_number=''                 -> 12 chunks ['liability_only_policy.md'] doc_reranker_score=2.3475
```

Seed after the index rebuild: `policies: 68 chunks`, `prior claims: 43 docs`
(planted `CLM-0412`, `CLM-0431/0432`).

Tribunal end-to-end, two consecutive fresh runs of `python -m tribunal.smoke crash4`
(`logs/smoke-crash4-ch1-fix.log`, `logs/smoke-crash4-ch1-fix-run2.log`):

| | run 1 | run 2 |
|---|---|---|
| structured `policy_number` | COMM-AUTO-001 | COMM-AUTO-001 |
| policy document retrieved | Commercial Auto Insurance Policy | Commercial Auto Insurance Policy |
| `coverage_decision` / deductible | APPROVED / $1,000 | APPROVED / $1,000 |
| fraud evidence | CLM-0412, similarity 0.749 | CLM-0412, similarity 0.785 |
| arbiter `decision` | refer | refer |

Run 1 hit a Mistral `429 RateLimitReached` on `crash4_back.jpeg` and still produced the
same policy document and verdict, because the anchor comes from the front page's policy
code. Fraud score and repair totals are LLM-sampled and move run to run (0.85 / 0.75);
the retrieved document, coverage decision and deductible do not.

**Demo-day rule:** do not run `tribunal.ocr_bench`, `tribunal.eval`, `tribunal.redteam`
or a second smoke while a claim is on screen. The `mistral-document-ai-2512` deployment
rate-limits aggressively and a 429 costs the back page of the statement.

## Not in these files

`search_tools.py` returns `source_url` and `doc_reranker_score` on every policy chunk,
but `tribunal/workflow.py` emits policy chunks to the UI as `{title, score}` only, so
Blob provenance and reranker scores are not yet demo-visible. That is a one-line change
in the evidence event, in a file this track does not own.

Suggested `tribunal/README.md` row (this track must not edit that file), replacing the
current `search_tools.py` "hybrid + vector queries" text:

```
| `search_tools.py` | Azure AI Search: integrated vectorization (`AzureOpenAIVectorizer`), hybrid keyword+vector, semantic reranker, deterministic `policy_number` document anchor, Blob `source_url` on every chunk. See [docs/search.md](docs/search.md) |
```
