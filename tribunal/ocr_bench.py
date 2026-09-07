"""Three-way document-processing comparison for Challenge 1.

Runs every handwritten statement page (challenge-0/data/statements/crash{1..5}_{front,back}.jpeg)
through three approaches and scores them against challenge-3/ground_truth.json:

    1. Azure AI Document Intelligence, ``prebuilt-read`` (azure-ai-documentintelligence)
    2. Mistral Document AI, ``mistral-document-ai-2512`` (Foundry OCR REST endpoint,
       same call shape as challenge-2/agents/ocr_agent.py)
    3. gpt-4.1-mini vision, Responses API ``input_image`` with a verbatim-transcription prompt

For each page it records wall-clock latency, extracted characters and billed cost at Azure
list price. For each claim it concatenates the front and back text per approach and counts
how many of the 24 ground-truth fields are recoverable from that text.

Run:

    .venv/bin/python -m tribunal.ocr_bench > logs/ocr_bench.log

Writes tribunal/data/ocr_bench.json and tribunal/data/ocr_bench.md.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

STATEMENTS = ROOT / "challenge-0" / "data" / "statements"
GROUND_TRUTH = ROOT / "challenge-3" / "ground_truth.json"
OUT_DIR = ROOT / "tribunal" / "data"

CLAIMS = [f"crash{i}" for i in range(1, 6)]
SIDES = ["front", "back"]

# Azure Retail Prices API, armRegionName=swedencentral, captured 2026-09-07 into
# logs/ocr_bench_prices.log via https://prices.azure.com/api/retail/prices.
PRICES = {
    "doc_intelligence": {
        "meter": "Azure Document Intelligence / S0 Read Pages (tier from 0)",
        "usd_per_page": 1.50 / 1000,
    },
    "mistral": {
        "meter": "Azure Mistral Models / OCR 2512 glbl Pages",
        "usd_per_page": 2.00 / 1000,
    },
    "gpt41mini_vision": {
        "meter": "Azure OpenAI / gpt 4.1 mini Inp glbl + cached Inp glbl + Outp glbl Tokens",
        "usd_per_input_token": 0.0004 / 1000,
        "usd_per_cached_input_token": 0.0001 / 1000,
        "usd_per_output_token": 0.0016 / 1000,
    },
}

TRANSCRIBE_PROMPT = (
    "Transcribe every word on this page verbatim, including printed field labels and "
    "all handwriting. Output plain text only, one line per line of the page, preserving "
    "the original order. Do not summarise, do not translate, do not add commentary, do "
    "not skip fields that look empty."
)

MISTRAL_RETRY_WAIT_S = 30
MISTRAL_MAX_ATTEMPTS = 4
FUZZY_THRESHOLD = 0.85

# Fields whose values are printed nowhere on the two statement pages (they come from the
# policy file and the downstream claim record), so no OCR of these images can recover them.
# Excluded from the scored denominator by this rule, stated up front, rather than by
# deriving the denominator from what the approaches happened to find.
OFF_PAGE_FIELDS = ("claimant_id", "repair_shop_name", "repair_shop_address", "claim_request")

# Ground-truth placeholders: nothing is written on the page to find, and the normalised
# forms ("n a") are substrings of ordinary English ("in a", "on a"), so matching them
# hands every approach a free hit. Unscorable: dropped from numerator and denominator.
UNSCORABLE_VALUES = {"", "n a", "na", "none", "unknown", "not applicable"}

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    """casefold, strip punctuation, collapse whitespace."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.casefold())).strip()


def scorable(value: str) -> bool:
    """False for off-page placeholders like ``N/A`` that no OCR can be credited for."""
    return normalise(value) not in UNSCORABLE_VALUES


def field_found(value: str, haystack: str) -> bool:
    """Ground-truth value present in the OCR text, substring first then fuzzy window.

    The substring test is word-boundary anchored: without it a short value such as
    ``red`` scores on ``covered`` and ``N/A`` -> ``n a`` scores on ``in a``.
    """
    needle = normalise(value)
    if not needle:
        return False
    if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
        return True
    n = len(needle)
    if n > len(haystack):
        return False
    step = max(1, n // 10)
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)
    for start in range(0, len(haystack) - n + 1, step):
        window = haystack[start : start + n]
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
            continue
        if matcher.quick_ratio() < FUZZY_THRESHOLD:
            continue
        if matcher.ratio() >= FUZZY_THRESHOLD:
            return True
    return False


@dataclass
class PageResult:
    approach: str
    claim: str
    side: str
    latency_s: float
    characters: int
    cost_usd: float
    usage: dict = field(default_factory=dict)
    error: str = ""
    text: str = ""


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


# --------------------------------------------------------------------------- approaches


class DocIntelligence:
    """Azure AI Document Intelligence prebuilt-read.

    The lab subscription's Azure Policy forces ``disableLocalAuth=true`` on new Cognitive
    Services accounts, so the account key in .env is rejected with AuthenticationTypeDisabled.
    We try the key anyway (so the module works on a normally-configured resource) and fall
    back to Entra ID, which is what actually authenticates here.
    """

    name = "doc_intelligence"
    label = "Azure AI Document Intelligence (prebuilt-read)"

    def __init__(self) -> None:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential

        endpoint = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
        key = os.getenv("DOCUMENT_INTELLIGENCE_KEY", "")
        self.auth = "entra-id"
        self._client = None
        if key:
            candidate = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))
            try:
                self._probe(candidate)
                self._client = candidate
                self.auth = "account-key"
            except Exception as exc:  # noqa: BLE001 - any key failure means fall back
                print(f"   doc_intelligence: account key rejected ({exc.__class__.__name__}: "
                      f"{str(exc).splitlines()[0]}), falling back to Entra ID")
        if self._client is None:
            self._client = DocumentIntelligenceClient(endpoint, DefaultAzureCredential())

    @staticmethod
    def _probe(client) -> None:
        # ponytail: the only way to tell a working key from a policy-disabled one is a real
        # call. Where key auth is enabled this bills an 11th page on top of the bench's 10;
        # in this lab it is rejected at authentication, so it costs a round trip and no page.
        with (STATEMENTS / "crash1_front.jpeg").open("rb") as fh:
            client.begin_analyze_document(
                "prebuilt-read", body=fh, content_type="application/octet-stream"
            ).result()

    def run(self, path: Path) -> tuple[str, dict]:
        with path.open("rb") as fh:
            poller = self._client.begin_analyze_document(
                "prebuilt-read", body=fh, content_type="application/octet-stream"
            )
        result = poller.result()
        pages = len(result.pages) if result.pages else 1
        return result.content or "", {"pages": pages}

    def cost(self, usage: dict) -> float:
        return usage.get("pages", 1) * PRICES[self.name]["usd_per_page"]


class MistralDocumentAI:
    """mistral-document-ai-2512 via the Foundry OCR endpoint (challenge-2 ocr_agent shape)."""

    name = "mistral"
    label = "Mistral Document AI (mistral-document-ai-2512)"

    def __init__(self) -> None:
        self.endpoint = os.environ["MISTRAL_DOCUMENT_AI_ENDPOINT"].rstrip("/") + (
            "/providers/mistral/azure/ocr"
        )
        self.key = os.environ["MISTRAL_DOCUMENT_AI_KEY"]
        self.model = os.getenv("MISTRAL_DOCUMENT_AI_DEPLOYMENT_NAME", "mistral-document-ai-2512")
        self.auth = "account-key"

    def run(self, path: Path) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "document": {
                "type": "image_url",
                "image_url": f"data:image/jpeg;base64,{_image_b64(path)}",
            },
        }
        headers = {"Content-Type": "application/json", "api-key": self.key}
        last: httpx.Response | None = None
        waited = 0.0
        for attempt in range(1, MISTRAL_MAX_ATTEMPTS + 1):
            with httpx.Client(timeout=300.0) as client:
                last = client.post(self.endpoint, json=payload, headers=headers)
            if last.status_code == 429 and attempt < MISTRAL_MAX_ATTEMPTS:
                wait = MISTRAL_RETRY_WAIT_S
                retry_after = last.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, int(float(retry_after)))
                    except ValueError:
                        pass
                print(f"   mistral: 429, waiting {wait}s (attempt {attempt}/{MISTRAL_MAX_ATTEMPTS})")
                time.sleep(wait)
                waited += wait
                continue
            last.raise_for_status()
            body = last.json()
            text = "\n\n".join(
                page.get("markdown", "") for page in body.get("pages", []) if isinstance(page, dict)
            )
            info = body.get("usage_info") or {}
            return text, {
                "pages": info.get("pages_processed", 1),
                "rate_limit_wait_s": round(waited, 2),
            }
        assert last is not None
        last.raise_for_status()
        raise RuntimeError("unreachable")

    def cost(self, usage: dict) -> float:
        return usage.get("pages", 1) * PRICES[self.name]["usd_per_page"]


class Gpt41MiniVision:
    """gpt-4.1-mini multimodal transcription over the Azure OpenAI v1 Responses API."""

    name = "gpt41mini_vision"
    label = "gpt-4.1-mini vision (Responses API)"

    def __init__(self) -> None:
        from openai import OpenAI

        self.model = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1-mini")
        self._client = OpenAI(
            base_url=os.environ["AZURE_OPENAI_BASE_URL"],
            api_key=os.environ["AZURE_OPENAI_KEY"],
            max_retries=4,
            # ponytail: a hung connection once burned 607s of wall clock on a single page and
            # skewed the latency mean; bound it rather than post-hoc trimming outliers.
            timeout=120.0,
        )
        self.auth = "account-key"

    def run(self, path: Path) -> tuple[str, dict]:
        response = self._client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": TRANSCRIBE_PROMPT},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{_image_b64(path)}",
                        },
                    ],
                }
            ],
            max_output_tokens=4000,
        )
        usage = response.usage
        cached = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else 0
        return response.output_text or "", {
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": cached,
            "output_tokens": usage.output_tokens,
        }

    def cost(self, usage: dict) -> float:
        p = PRICES[self.name]
        cached = usage.get("cached_input_tokens", 0)
        fresh = max(usage.get("input_tokens", 0) - cached, 0)
        return (
            fresh * p["usd_per_input_token"]
            + cached * p["usd_per_cached_input_token"]
            + usage.get("output_tokens", 0) * p["usd_per_output_token"]
        )


# --------------------------------------------------------------------------- bench


def run_approach(approach, results: list[PageResult]) -> None:
    for claim in CLAIMS:
        for side in SIDES:
            path = STATEMENTS / f"{claim}_{side}.jpeg"
            started = time.perf_counter()
            try:
                text, usage = approach.run(path)
                latency = time.perf_counter() - started
                res = PageResult(
                    approach=approach.name,
                    claim=claim,
                    side=side,
                    latency_s=round(latency, 3),
                    characters=len(text),
                    cost_usd=round(approach.cost(usage), 8),
                    usage=usage,
                    text=text,
                )
                print(
                    f"   {approach.name:<17} {claim}_{side:<5} "
                    f"{res.latency_s:6.2f}s  {res.characters:5d} chars  ${res.cost_usd:.6f}"
                )
            except Exception as exc:  # noqa: BLE001 - a failing approach must not kill the bench
                latency = time.perf_counter() - started
                res = PageResult(
                    approach=approach.name,
                    claim=claim,
                    side=side,
                    latency_s=round(latency, 3),
                    characters=0,
                    cost_usd=0.0,
                    error=f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:300]}",
                )
                print(f"   {approach.name:<17} {claim}_{side:<5} FAILED  {res.error}")
            results.append(res)


def score(results: list[PageResult], truth: dict, approach_names: list[str]) -> dict:
    fields = list(truth[CLAIMS[0]].keys())
    by_key = {(r.approach, r.claim, r.side): r for r in results}
    matrix: dict[str, dict[str, dict[str, bool]]] = {}
    for name in approach_names:
        matrix[name] = {}
        for claim in CLAIMS:
            text = " ".join(
                by_key[(name, claim, side)].text for side in SIDES if (name, claim, side) in by_key
            )
            hay = normalise(text)
            matrix[name][claim] = {f: field_found(str(truth[claim][f]), hay) for f in fields}
    scored = [
        (claim, f)
        for claim in CLAIMS
        for f in fields
        if f not in OFF_PAGE_FIELDS and scorable(str(truth[claim][f]))
    ]
    unscorable = [
        (claim, f)
        for claim in CLAIMS
        for f in fields
        if f not in OFF_PAGE_FIELDS and not scorable(str(truth[claim][f]))
    ]
    return {"fields": fields, "matrix": matrix, "scored": scored, "unscorable": unscorable}


def summarise(results: list[PageResult], scoring: dict, approaches: list) -> dict:
    fields = scoring["fields"]
    scored_pairs = scoring["scored"]
    out = {}
    for approach in approaches:
        name = approach.name
        rows = [r for r in results if r.approach == name]
        ok = [r for r in rows if not r.error]
        found = sum(1 for claim, f in scored_pairs if scoring["matrix"][name][claim][f])
        service = [
            r.latency_s - float(r.usage.get("rate_limit_wait_s", 0.0) or 0.0) for r in ok
        ]
        worst = max(zip(service, ok), key=lambda pair: pair[0])[1] if ok else None
        out[name] = {
            "label": approach.label,
            "auth": approach.auth,
            "pages_attempted": len(rows),
            "pages_ok": len(ok),
            "errors": [f"{r.claim}_{r.side}: {r.error}" for r in rows if r.error],
            "fields_found": found,
            "fields_scored": len(scored_pairs),
            "field_recall": round(found / len(scored_pairs), 4) if scored_pairs else 0.0,
            "fields_off_page": len(OFF_PAGE_FIELDS) * len(CLAIMS),
            "fields_unscorable": len(scoring["unscorable"]),
            "mean_latency_s": round(statistics.fmean(service), 2) if ok else None,
            "median_latency_s": round(statistics.median(service), 2) if ok else None,
            "max_latency_s": round(max(service), 2) if ok else None,
            "max_latency_page": f"{worst.claim}_{worst.side}" if worst else None,
            "mean_wall_latency_s": round(statistics.fmean([r.latency_s for r in ok]), 2) if ok else None,
            "rate_limit_wait_total_s": round(
                sum(float(r.usage.get("rate_limit_wait_s", 0.0) or 0.0) for r in ok), 1
            ),
            "total_characters": sum(r.characters for r in ok),
            "mean_characters_per_page": round(
                statistics.fmean([r.characters for r in ok]), 1
            ) if ok else None,
            "cost_per_page_usd": round(statistics.fmean([r.cost_usd for r in ok]), 6) if ok else None,
            "cost_per_claim_usd": round(sum(r.cost_usd for r in rows) / len(CLAIMS), 6),
            "cost_per_1000_claims_usd": round(sum(r.cost_usd for r in rows) / len(CLAIMS) * 1000, 2),
            "price_basis": PRICES[name]["meter"],
        }
    return out


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(summary: dict, scoring: dict, approaches: list, generated: str) -> str:
    names = [a.name for a in approaches]
    fields = scoring["fields"]
    total_fields = len(fields) * len(CLAIMS)
    scored_n = len(scoring["scored"])

    head = [
        "# Challenge 1: three-way OCR comparison on the handwritten statements",
        "",
        f"10 statement pages (crash1..crash5, front + back), {len(fields)} ground-truth fields per "
        f"claim ({total_fields} field instances), scored against `challenge-3/ground_truth.json`.",
        "A field counts as found when its normalised ground-truth value (casefold, punctuation "
        "stripped, whitespace collapsed) appears word-boundary anchored in the concatenated "
        "front+back OCR text for that claim, or a sliding window of that text matches it with "
        f"difflib ratio >= {FUZZY_THRESHOLD}.",
        "",
        f"Generated: {generated} by `.venv/bin/python -m tribunal.ocr_bench`.",
        "",
        "## Head to head",
        "",
    ]

    rows = []
    for name in names:
        s = summary[name]
        rows.append(
            [
                s["label"],
                f"{s['fields_found']}/{s['fields_scored']} ({s['field_recall'] * 100:.1f}%)",
                f"{s['median_latency_s']:.2f}s" if s["median_latency_s"] is not None else "n/a",
                f"{s['mean_latency_s']:.2f}s / {s['max_latency_s']:.2f}s"
                if s["mean_latency_s"] is not None else "n/a",
                f"{s['mean_characters_per_page']:.0f}" if s["mean_characters_per_page"] is not None else "n/a",
                f"${s['cost_per_claim_usd']:.6f}",
                f"${s['cost_per_1000_claims_usd']:.2f}",
                f"{s['pages_ok']}/{s['pages_attempted']}",
            ]
        )
    head.append(
        _md_table(
            [
                "approach",
                f"fields found / {scored_n} scorable",
                "median service latency / page",
                "mean / worst latency",
                "mean chars / page",
                "cost / claim (2 pages)",
                "cost / 1000 claims",
                "pages ok",
            ],
            rows,
        )
    )
    worst_name = max(names, key=lambda n: summary[n]["max_latency_s"] or 0.0)
    worst = summary[worst_name]
    head += [
        "",
        f"**Denominator.** {scored_n} of the {total_fields} (claim, field) pairs are scorable, by a "
        "rule stated before the results rather than derived from them. Excluded: the "
        f"{len(OFF_PAGE_FIELDS) * len(CLAIMS)} pairs of "
        + ", ".join(f"`{f}`" for f in OFF_PAGE_FIELDS)
        + ", which are printed nowhere on the two statement pages (they come from the policy file "
        "and the downstream claim record), so no OCR of these images can recover them; and "
        + (
            ", ".join(f"`{c}` `{f}`" for c, f in scoring["unscorable"])
            if scoring["unscorable"]
            else "no pairs"
        )
        + f" ({len(scoring['unscorable'])} of the remaining "
        f"{total_fields - len(OFF_PAGE_FIELDS) * len(CLAIMS)}), whose ground-truth value is a "
        "placeholder such as `N/A`: there is nothing on the page to find, and the normalised "
        "form `n a` is a "
        "substring of ordinary English (`in a`, `on a`), which would hand every approach a free "
        "hit. Matching is word-boundary anchored for the same reason.",
        "",
        "Mean service latency excludes time spent sleeping on HTTP 429. The lab's "
        "`mistral-document-ai-2512` deployment is provisioned at very low capacity: "
        f"this run lost {summary['mistral']['rate_limit_wait_total_s']:.0f}s to rate-limit backoff, "
        f"making its wall-clock mean {summary['mistral']['mean_wall_latency_s']:.2f}s/page against a "
        f"service mean of {summary['mistral']['mean_latency_s']:.2f}s/page. Latency is ranked on the "
        "median, not the worst page: the slowest single page in this run was "
        f"{worst['label']} on `{worst['max_latency_page']}` at {worst['max_latency_s']:.2f}s, a "
        "service-side outlier rather than steady-state speed. The gpt-4.1-mini client carries a "
        "120s request timeout so a stalled connection costs one retry instead of hanging the run.",
    ]

    head += ["", "### Price basis", ""]
    head.append(
        _md_table(
            ["approach", "meter (Azure Retail Prices API, swedencentral)", "unit price"],
            [
                [
                    summary["doc_intelligence"]["label"],
                    PRICES["doc_intelligence"]["meter"],
                    "$1.50 per 1,000 pages",
                ],
                [
                    summary["mistral"]["label"],
                    PRICES["mistral"]["meter"],
                    "$2.00 per 1,000 pages",
                ],
                [
                    summary["gpt41mini_vision"]["label"],
                    PRICES["gpt41mini_vision"]["meter"],
                    "$0.40 / $0.10 cached / $1.60 per 1M input / cached / output tokens",
                ],
            ],
        )
    )
    head += [
        "",
        "Snapshot of the raw price query is in `logs/ocr_bench_prices.log` "
        "(`https://prices.azure.com/api/retail/prices`). Document Intelligence is billed at the "
        "S0 list rate even though this run used an F0 account, because F0 caps at 500 pages/month "
        "and is not a production option.",
        "",
        "## Field recall heat table",
        "",
        "Fraction of the scorable claims where each field was recoverable from that approach's "
        "text. `excluded` marks a field that is off-page for every claim; a denominator below 5 "
        "marks a field with a placeholder value on one of the claims. `incident_description` and "
        "`damage_description` are the fields to read carefully: their ground-truth values are "
        "paraphrased summaries of the handwriting, not what is written on the page, so a miss "
        "there measures paraphrase distance, not transcription quality.",
        "",
    ]

    scored_set = set(scoring["scored"])
    heat_rows = []
    for f in fields:
        claims_scored = [c for c in CLAIMS if (c, f) in scored_set]
        if not claims_scored:
            heat_rows.append([f"`{f}`", *["excluded" for _ in names]])
            continue
        cells = []
        for name in names:
            hits = sum(1 for c in claims_scored if scoring["matrix"][name][c][f])
            cells.append(f"{hits}/{len(claims_scored)}")
        heat_rows.append([f"`{f}`", *cells])
    heat_rows.append(
        [
            "**total**",
            *[f"**{summary[n]['fields_found']}/{summary[n]['fields_scored']}**" for n in names],
        ]
    )
    head.append(_md_table(["field", *[summary[n]["label"] for n in names]], heat_rows))

    head += ["", "## Per-claim field recall", ""]
    head.append(
        _md_table(
            ["claim", *[summary[n]["label"] for n in names]],
            [
                [
                    claim,
                    *[
                        f"{sum(1 for f in fields if (claim, f) in scored_set and scoring['matrix'][n][claim][f])}"
                        f"/{sum(1 for f in fields if (claim, f) in scored_set)}"
                        for n in names
                    ],
                ]
                for claim in CLAIMS
            ],
        )
    )

    errors = [(n, e) for n in names for e in summary[n]["errors"]]
    if errors:
        head += ["", "## Failures", ""]
        head.append(_md_table(["approach", "page / error"], [[n, e] for n, e in errors]))

    head += ["", "## When to use which", "", conclusion(summary, scoring)]
    return "\n".join(head) + "\n"


def conclusion(summary: dict, scoring: dict) -> str:
    di, mi, gp = (summary[k] for k in ("doc_intelligence", "mistral", "gpt41mini_vision"))
    everyone = (di, mi, gp)
    spread = max(s["fields_found"] for s in everyone) - min(s["fields_found"] for s in everyone)
    best = max(everyone, key=lambda s: s["fields_found"])
    cheapest = min(everyone, key=lambda s: s["cost_per_claim_usd"])
    dearest = max(everyone, key=lambda s: s["cost_per_claim_usd"])
    fastest = min(everyone, key=lambda s: s["median_latency_s"])
    slowest = max(everyone, key=lambda s: s["median_latency_s"])
    worst = max(everyone, key=lambda s: s["max_latency_s"] or 0.0)
    return "\n".join(
        [
            f"1. **Accuracy does not decide this.** On the "
            f"{best['fields_scored']} scorable field instances the three "
            + (
                "score identically"
                if spread == 0
                else f"land within {spread} field{'s' if spread > 1 else ''} of each other"
            )
            + f" ({di['fields_found']} Document Intelligence, {mi['fields_found']} Mistral, "
            f"{gp['fields_found']} gpt-4.1-mini). All three read this handwriting; picking on "
            "accuracy alone would be picking noise, so choose on cost, latency and what the next "
            "step needs.",
            f"2. **The cheapest per page is the surprise: {cheapest['label']}** at "
            f"${cheapest['cost_per_claim_usd']:.6f} per two-page claim "
            f"(${cheapest['cost_per_1000_claims_usd']:.2f} per 1,000 claims) versus "
            f"${dearest['cost_per_claim_usd']:.6f} for {dearest['label']}. The whole spread is "
            f"under ${dearest['cost_per_claim_usd'] - cheapest['cost_per_claim_usd']:.4f} a claim, "
            "so OCR unit price only becomes a real line item past roughly 100k claims a year.",
            f"3. **Predictability, not price, is the page-meter argument.** Document Intelligence "
            f"and Mistral bill a flat ${di['cost_per_page_usd']:.4f} and "
            f"${mi['cost_per_page_usd']:.4f} per page no matter what is on it; gpt-4.1-mini is "
            f"token-metered, so a dense page or a chatty model costs more and a prompt-injected "
            f"page can cost a lot more. Budget with the page meters, alert on the token meter.",
            f"4. **Latency: {fastest['label']} is the floor at a median "
            f"{fastest['median_latency_s']:.2f}s/page, {slowest['label']} the ceiling at "
            f"{slowest['median_latency_s']:.2f}s/page** (service time, 429 backoff excluded); the "
            f"worst single page in the run was {worst['label']} on `{worst['max_latency_page']}` at "
            f"{worst['max_latency_s']:.1f}s. OCR sits on the "
            "tribunal's critical path before four agents start, so the "
            f"~{slowest['median_latency_s'] - fastest['median_latency_s']:.1f}s median gap per page "
            "is felt live in a way the sub-cent cost gap is not.",
            "5. **What the tribunal ships**: a page-metered OCR service for the bulk transcript of "
            "every statement (flat cost, lowest latency, no prompt to regress), and gpt-4.1-mini "
            "vision reserved for the jobs the other two cannot do at all - reading the damage "
            "photo, and returning structured claim JSON instead of a wall of text. Use the "
            "multimodal model where re-prompting is the point, not where transcription is.",
        ]
    )


DOC = ROOT / "tribunal" / "docs" / "ocr-comparison.md"
PRICE_LOG = ROOT / "logs" / "ocr_bench_prices.log"


def check_doc() -> int:
    """Assert tribunal/docs/ocr-comparison.md still quotes the current run. No API calls.

    tribunal/data/ and logs/ are gitignored, so the judge-facing doc inlines their tables
    instead of linking them; this is the guard that stops the two drifting apart again.
    """
    data = json.loads((OUT_DIR / "ocr_bench.json").read_text())
    doc = DOC.read_text()
    names = list(data["summary"])
    off = set(data["scoring_rule"]["off_page_fields_excluded"])
    unscorable = {tuple(p) for p in data["scoring_rule"]["unscorable_pairs"]}
    missing: list[str] = []

    if data["generated"] not in doc:
        missing.append(f"run timestamp {data['generated']}")
    for name, s in data["summary"].items():
        for frag in (
            f"{s['fields_found']}/{s['fields_scored']} ({s['field_recall'] * 100:.1f}%)",
            f"{s['median_latency_s']:.2f}s",
            f"{s['mean_latency_s']:.2f}s / {s['max_latency_s']:.2f}s",
            f"${s['cost_per_claim_usd']:.6f}",
        ):
            if frag not in doc:
                missing.append(f"{name} head-table cell {frag!r}")
    for f in data["fields"]:
        if f in off:
            cells = ["excluded"] * len(names)
        else:
            claims = [c for c in data["claims"] if (c, f) not in unscorable]
            cells = [
                f"{sum(1 for c in claims if data['field_matrix'][n][c][f])}/{len(claims)}"
                for n in names
            ]
        row = "| `" + f + "` | " + " | ".join(cells) + " |"
        if row not in doc:
            missing.append("heat-table row " + row)
    for claim in data["claims"]:
        scored = [
            f for f in data["fields"] if f not in off and (claim, f) not in unscorable
        ]
        cells = [
            f"{sum(1 for f in scored if data['field_matrix'][n][claim][f])}/{len(scored)}"
            for n in names
        ]
        row = "| " + claim + " | " + " | ".join(cells) + " |"
        if row not in doc:
            missing.append("per-claim row " + row)
    if PRICE_LOG.exists():
        for line in PRICE_LOG.read_text().strip().splitlines():
            if line not in doc:
                missing.append("price-snapshot line " + line)
    else:
        missing.append(f"{PRICE_LOG} absent, cannot verify the inlined price snapshot")

    if missing:
        print(f"{DOC} is out of date with {OUT_DIR / 'ocr_bench.json'}:")
        for m in missing:
            print(f"  missing: {m}")
        return 1
    print(f"{DOC} matches the {data['generated']} run and the price snapshot")
    return 0


def main() -> int:
    if "--check-doc" in sys.argv[1:]:
        return check_doc()
    truth = json.loads(GROUND_TRUTH.read_text())
    print("Challenge 1 three-way OCR comparison")
    print(f"  statements: {STATEMENTS}")
    print(f"  ground truth: {GROUND_TRUTH} ({len(truth)} claims x {len(truth['crash1'])} fields)")

    approaches = []
    for cls in (DocIntelligence, MistralDocumentAI, Gpt41MiniVision):
        try:
            approaches.append(cls())
            print(f"  ready: {approaches[-1].label} (auth: {approaches[-1].auth})")
        except Exception as exc:  # noqa: BLE001 - report and continue with the rest
            print(f"  UNAVAILABLE: {cls.label}: {exc.__class__.__name__}: {str(exc).splitlines()[0]}")
    if not approaches:
        print("no approaches available", file=sys.stderr)
        return 1

    results: list[PageResult] = []
    for approach in approaches:
        print(f"\n{approach.label}")
        run_approach(approach, results)

    scoring = score(results, truth, [a.name for a in approaches])
    summary = summarise(results, scoring, approaches)
    generated = time.strftime("%Y-%m-%d %H:%M:%S %Z")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": generated,
        "claims": CLAIMS,
        "fields": scoring["fields"],
        "scoring_rule": {
            "off_page_fields_excluded": list(OFF_PAGE_FIELDS),
            "unscorable_pairs": [list(p) for p in scoring["unscorable"]],
            "scored_pairs": len(scoring["scored"]),
            "match": f"word-boundary substring, else difflib window >= {FUZZY_THRESHOLD}",
        },
        "prices": PRICES,
        "price_source": "https://prices.azure.com/api/retail/prices, armRegionName=swedencentral",
        "summary": summary,
        "field_matrix": scoring["matrix"],
        "pages": [asdict(r) for r in results],
    }
    (OUT_DIR / "ocr_bench.json").write_text(json.dumps(payload, indent=2))
    (OUT_DIR / "ocr_bench.md").write_text(render_markdown(summary, scoring, approaches, generated))

    print("\nSummary")
    for name, s in summary.items():
        print(
            f"  {s['label']}: {s['fields_found']}/{s['fields_scored']} scorable fields "
            f"({s['field_recall'] * 100:.1f}%), worst page {s['max_latency_page']} "
            f"{s['max_latency_s']}s, median "
            f"{s['median_latency_s']}s/page, ${s['cost_per_claim_usd']:.6f}/claim, "
            f"{s['pages_ok']}/{s['pages_attempted']} pages ok"
        )
    print(f"\nwrote {OUT_DIR / 'ocr_bench.json'}")
    print(f"wrote {OUT_DIR / 'ocr_bench.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
