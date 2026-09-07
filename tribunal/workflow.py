"""Claims Tribunal workflow: OCR -> structure -> [adjuster || fraud || policy] -> arbiter.

`emit(event, data)` is an async callback; the API turns it into SSE.
"""
import asyncio
import base64
import json
import os
import sys
import time

from . import prompts, search_tools
from .foundry import MODEL, image_part, parse_json, run_agent, split_opinion, text_part
from .telemetry import agent_span, record_verdict, span

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(REPO, "challenge-2", "agents"))
from ocr_agent import extract_text_with_ocr  # noqa: E402  (Challenge 2, Mistral Document AI)

LABELS = {
    "ocr": "OCR · Mistral Document AI",
    "structure": "Structuring · gpt-4.1-mini",
    "adjuster": "Adjuster",
    "fraud": "Fraud Investigator",
    "policy": "Policy Analyst",
    "arbiter": "Arbiter",
}


def _data_url(path: str) -> str:
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def claim_summary(claim: dict) -> str:
    keys = ["policy_number", "policy_holder_name", "vehicle_year_make_model", "vehicle_vin",
            "incident_date", "incident_location", "incident_description", "damage_description", "claim_request"]
    return "\n".join(f"{k}: {claim.get(k)}" for k in keys if claim.get(k))


def _money(v) -> int:
    try:
        return int(float(str(v).replace("$", "").replace(",", "").split()[0]))
    except (ValueError, IndexError, AttributeError):
        return 0


def build_verdict(claim_id, claim, adjuster, fraud, policy, arbiter) -> dict:
    p = arbiter.get("payout") or {}
    decision = arbiter.get("decision", "refer")
    if not arbiter.get("decision"):  # unparseable or missing arbiter output: refer, and say why
        arbiter = {**arbiter, "rationale": "Arbiter output could not be parsed; referred for human review.",
                   "referral_reason": "arbiter output unparseable", "confidence": 0}
    covered = _money(p.get("covered"))
    deductible = _money(p.get("deductible") or policy.get("deductible"))
    limit = _money(p.get("limit") or policy.get("coverage_limit")) or None
    net = 0 if decision == "deny" else max(0, min(covered, limit or covered) - deductible)
    return {
        "claim_id": claim_id,
        "decision": decision,
        "confidence": arbiter.get("confidence", 0),
        "rationale": arbiter.get("rationale", ""),
        "disagreements": arbiter.get("disagreements", []),
        "referral_reason": arbiter.get("referral_reason"),
        "payout": {
            "claimed": _money(p.get("claimed") or adjuster.get("estimated_total")),
            "covered": covered, "deductible": deductible, "limit": limit, "net": net,
            "items": adjuster.get("items", []),
        },
        "fraud": {"score": fraud.get("fraud_score", 0), "evidence": fraud.get("evidence", []),
                  "recommended_action": fraud.get("recommended_action")},
        "policy": {"policy_number": policy.get("policy_number") or claim.get("policy_number"),
                   "policy_name": policy.get("policy_name"),
                   "coverage_decision": policy.get("coverage_decision"),
                   "applicable_coverage": policy.get("applicable_coverage"),
                   "exclusions_triggered": policy.get("exclusions_triggered", []),
                   "citations": policy.get("citations", [])},
        "clauses": arbiter.get("clauses") or [],
        "what_if": {"deductible": deductible, "limit": limit, "covered": covered},
        "letter": arbiter.get("letter", ""),
        "claim": claim,
    }


# --------------------------------------------------------------------------- #
# Evidence step (shared by tribunal.workflow, tribunal.af_workflow, tribunal.appeal)
# --------------------------------------------------------------------------- #
async def gather_evidence(claim: dict, photo_path: str | None, claim_id: str | None = None,
                          extra_text: str = "") -> dict:
    """Prior claims + policy sections + adjuster precedents + prior-photo matches, in parallel.

    Precedent search and photo matching are best-effort: a missing index or a slow
    describe call must never sink an adjudication, so both degrade to empty.
    """
    from . import photo_index, precedent  # local: photo_index imports this module

    summary = claim_summary(claim)
    query = f"{summary}\n{extra_text}".strip()

    async def _precedents() -> list[dict]:
        try:
            return await asyncio.to_thread(precedent.search_precedents, query)
        except Exception as e:  # noqa: BLE001
            print(f"search_precedents failed: {e}", file=sys.stderr)
            return []

    async def _photo() -> tuple[str, list[dict]]:
        if not photo_path:
            return "", []
        try:
            return await photo_index.match_photo(photo_path, top=3, exclude_claim=claim_id)
        except Exception as e:  # noqa: BLE001
            print(f"match_photo failed: {e}", file=sys.stderr)
            return "", []

    prior, policy_chunks, precedents, (photo_description, photo_matches) = await asyncio.gather(
        asyncio.to_thread(search_tools.search_prior_claims, query),
        asyncio.to_thread(search_tools.search_policies, claim.get("policy_number") or "", summary),
        _precedents(),
        _photo(),
    )
    return {"prior_claims": prior, "policy_chunks": policy_chunks, "precedents": precedents,
            "photo_matches": photo_matches, "photo_description": photo_description}


def evidence_event(ev: dict) -> dict:
    """The `evidence` SSE payload the React bench consumes."""
    return {
        "prior_claims": ev["prior_claims"],
        "policy_chunks": [{"title": c.get("title"), "score": c.get("score")} for c in ev["policy_chunks"]],
        "precedents": [{"claim_id": p.get("claim_id"), "human_decision": p.get("human_decision"),
                        "reason": p.get("reason"), "tribunal_decision": p.get("tribunal_decision"),
                        "precedent_id": p.get("id"), "similarity": p.get("similarity")}
                       for p in ev["precedents"]],
        "photo_matches": ev["photo_matches"],
        "photo_description": ev["photo_description"],
    }


def precedent_block(ev: dict) -> str:
    return ("\n\nADJUSTER PRECEDENTS (past human decisions, vector search):\n" +
            json.dumps([{k: v for k, v in p.items() if k != "content_vector"} for p in ev["precedents"]], indent=1))


def photo_block(ev: dict) -> str:
    if not ev["photo_description"] and not ev["photo_matches"]:
        return ""
    return (f"\n\nTHIS PHOTO (forensic description):\n{ev['photo_description']}"
            f"\n\nPRIOR PHOTOS ON FILE (vector match):\n{json.dumps(ev['photo_matches'], indent=1)}")


async def adjudicate(claim_id: str, statement_paths: list[str], photo_path: str | None, emit) -> dict:
    async def start(agent):
        await emit("agent.start", {"agent": agent, "label": LABELS[agent]})

    async def done(agent, t0, **result):
        await emit("agent.done", {"agent": agent, "ms": int((time.time() - t0) * 1000), **result})

    def token(agent):
        async def _t(text):
            await emit("agent.token", {"agent": agent, "text": text})
        return _t

    async def opinion(agent, instructions, parts) -> dict:
        """Run one tribunal member: stream tokens, split prose from JSON, never raise."""
        t0 = time.time()
        await start(agent)
        try:
            with agent_span(agent, claim_id, MODEL):
                raw = await run_agent(f"Tribunal{agent.title()}Agent", instructions, parts, on_token=token(agent))
            prose, data = split_opinion(raw)
            await done(agent, t0, prose=prose, data=data)
            return data
        except Exception as e:  # one bad member must not sink the tribunal
            await emit("error", {"agent": agent, "message": str(e)[:300]})
            return {"error": str(e)[:300]}

    with span("tribunal.adjudicate", **{"claim.id": claim_id}):
        # 1. OCR every statement page (Mistral Document AI, Challenge 2 tool)
        t0 = time.time()
        await start("ocr")
        with agent_span("ocr", claim_id, "mistral-document-ai-2512"):
            raws = await asyncio.gather(*[asyncio.to_thread(extract_text_with_ocr, p) for p in statement_paths])
        pages = [json.loads(r) for r in raws]
        ocr_text = "\n\n--- PAGE ---\n\n".join(p.get("text", "") for p in pages)
        errors = [p["error"] for p in pages if p.get("status") != "success"]
        await done("ocr", t0, chars=len(ocr_text), pages=len(pages), preview=ocr_text[:600], errors=errors)

        # 2. Structure into a claim record
        t0 = time.time()
        await start("structure")
        with agent_span("structure", claim_id, MODEL):
            raw = await run_agent("TribunalStructureAgent", prompts.STRUCTURE,
                                  [text_part(f"OCR TEXT:\n{ocr_text}")], on_token=token("structure"))
        claim = parse_json(raw)
        await done("structure", t0, data=claim)
        await emit("claim", claim)

        # 3. Evidence gathering for the specialists: prior claims, policy sections,
        #    adjuster precedents, prior-photo matches (AI Search, all in parallel)
        ev = await gather_evidence(claim, photo_path, claim_id)
        prior, policy_chunks = ev["prior_claims"], ev["policy_chunks"]
        await emit("evidence", evidence_event(ev))

        # 4. Three specialists in parallel
        photo = [image_part(_data_url(photo_path))] if photo_path else []
        claim_json = json.dumps(claim, indent=1)
        adjuster, fraud, policy = await asyncio.gather(
            opinion("adjuster", prompts.ADJUSTER, [text_part(f"CLAIM:\n{claim_json}")] + photo),
            opinion("fraud", prompts.FRAUD, [text_part(
                f"CLAIM:\n{claim_json}\n\nPRIOR CLAIMS (vector search, top 5):\n{json.dumps(prior, indent=1)}"
                + photo_block(ev) + precedent_block(ev))] + photo),
            opinion("policy", prompts.POLICY, [text_part(
                f"CLAIM:\n{claim_json}\n\nPOLICY DOCUMENT SECTIONS:\n" +
                "\n\n".join(f"[{c.get('title')}]\n{c.get('content')}" for c in policy_chunks)
                + precedent_block(ev))]),
        )

        # 5. Arbiter
        arbiter = await opinion("arbiter", prompts.ARBITER, [text_part(
            f"CLAIM:\n{claim_json}\n\nADJUSTER:\n{json.dumps(adjuster, indent=1)}\n\n"
            f"FRAUD INVESTIGATOR:\n{json.dumps(fraud, indent=1)}\n\nPOLICY ANALYST:\n{json.dumps(policy, indent=1)}"
            + precedent_block(ev) + photo_block(ev))])

        verdict = build_verdict(claim_id, claim, adjuster, fraud, policy, arbiter)
        try:  # telemetry must never break an adjudication
            record_verdict(verdict)
        except Exception as e:  # noqa: BLE001
            print(f"record_verdict failed: {e}", file=sys.stderr)
        await emit("verdict", verdict)
        return verdict
