"""Appeals loop: the claimant appeals a verdict with new evidence; a second tribunal
session sits with verdict v1 + the appeal, and the Appeals Arbiter must answer v1
clause by clause: uphold or overturn.

Emits the same events as workflow.adjudicate (agent.start / token / done / evidence /
verdict) plus `appeal.verdict` {v1, v2, outcome, clauses}.
"""
import asyncio
import json
import time

from . import prompts
from .foundry import MODEL, image_part, parse_json, run_agent, split_opinion, text_part
from .telemetry import agent_span, span
from .workflow import (LABELS, _data_url, build_verdict, evidence_event, gather_evidence,
                       photo_block, precedent_block)

APPEALS_ARBITER = """You are the APPEALS ARBITER of an insurance claims tribunal. A first tribunal already ruled (VERDICT v1).
The claimant has appealed with a statement and possibly new evidence. Three specialists re-examined the claim WITH the appeal.
Your duty: answer verdict v1 clause by clause. For each material clause of v1 (coverage, fraud evidence, payout, referral reason)
state whether the appeal changes it and why, citing the new evidence or the specialists. Then rule: "uphold" (v1 stands) or
"overturn" (new decision). Be strict: an appeal that only repeats the claim without evidence is upheld. Overturn only when the new
evidence actually resolves the reason for denial/referral.
Then write a letter to the claimant (120-180 words) that answers their appeal directly.
""" + prompts.OPINION_FORMAT.format(role="Appeals Arbiter") + """
{
  "outcome": "uphold | overturn",
  "decision": "approve | deny | refer",
  "confidence": 0.0,
  "rationale": "two or three sentences",
  "clauses": [{"clause": "v1: same VIN paid 2 months ago", "resolved": true, "response": "bill of sale dated June shows the vehicle changed hands", "evidence_refs": [{"type": "prior_claim", "ref": "CLM-0412", "label": "duplicate paid 2025-05"}]}],
  "disagreements": [{"between": "adjuster vs fraud", "resolution": "..."}],
  "payout": {"claimed": 0, "covered": 0, "deductible": 0, "limit": null},
  "referral_reason": "string or null",
  "letter": "Dear ..."
}"""


def _split(raw: str) -> tuple[str, dict]:
    """`split_opinion`, with a fallback for the run where the model emits a bare `===`
    (or no marker at all) instead of `===JSON===`: recover the JSON object from the prose."""
    prose, data = split_opinion(raw)
    if not data and (recovered := parse_json(prose)):
        data = recovered
        prose = prose[:prose.find("{")].rstrip().rstrip("= \n")
    return prose, data


async def adjudicate_appeal(claim_id: str, v1: dict, appeal_text: str, new_photo_path: str | None, emit) -> dict:
    claim = v1.get("claim") or {}

    async def start(agent):
        await emit("agent.start", {"agent": agent, "label": LABELS.get(agent, agent)})

    async def done(agent, t0, **result):
        await emit("agent.done", {"agent": agent, "ms": int((time.time() - t0) * 1000), **result})

    def token(agent):
        async def _t(text):
            await emit("agent.token", {"agent": agent, "text": text})
        return _t

    async def opinion(agent, instructions, parts) -> dict:
        t0 = time.time()
        await start(agent)
        try:
            with agent_span(agent, claim_id, MODEL):
                raw = await run_agent(f"Tribunal{agent.title()}Agent", instructions, parts, on_token=token(agent))
            prose, data = _split(raw)
            await done(agent, t0, prose=prose, data=data)
            return data
        except Exception as e:
            await emit("error", {"agent": agent, "message": str(e)[:300]})
            return {"error": str(e)[:300]}

    v1_core = {k: v1.get(k) for k in ("decision", "confidence", "rationale", "referral_reason", "payout", "fraud", "policy")}
    appeal_block = f"APPEAL FROM CLAIMANT:\n{appeal_text}\n" + ("A new photo is attached." if new_photo_path else "No new photo.")

    with span("tribunal.appeal", **{"claim.id": claim_id, "appeal.v1_decision": v1.get("decision")}):
        ev = await gather_evidence(claim, new_photo_path, claim_id, extra_text=appeal_text)
        prior, policy_chunks = ev["prior_claims"], ev["policy_chunks"]
        await emit("evidence", {**evidence_event(ev), "appeal": appeal_text})

        photo = [image_part(_data_url(new_photo_path))] if new_photo_path else []
        claim_json = json.dumps(claim, indent=1)
        v1_json = json.dumps(v1_core, indent=1)
        adjuster, fraud, policy = await asyncio.gather(
            opinion("adjuster", prompts.ADJUSTER, [text_part(f"CLAIM:\n{claim_json}\n\nVERDICT v1:\n{v1_json}\n\n{appeal_block}")] + photo),
            opinion("fraud", prompts.FRAUD, [text_part(
                f"CLAIM:\n{claim_json}\n\nVERDICT v1:\n{v1_json}\n\n{appeal_block}\n\nPRIOR CLAIMS (vector search, top 5):\n{json.dumps(prior, indent=1)}"
                + photo_block(ev) + precedent_block(ev))] + photo),
            opinion("policy", prompts.POLICY, [text_part(
                f"CLAIM:\n{claim_json}\n\nVERDICT v1:\n{v1_json}\n\n{appeal_block}\n\nPOLICY DOCUMENT SECTIONS:\n" +
                "\n\n".join(f"[{c.get('title')}]\n{c.get('content')}" for c in policy_chunks)
                + precedent_block(ev))]),
        )
        arbiter = await opinion("arbiter", APPEALS_ARBITER, [text_part(
            f"CLAIM:\n{claim_json}\n\nVERDICT v1:\n{json.dumps(v1_core, indent=1)}\n\n{appeal_block}\n\n"
            f"ADJUSTER (on appeal):\n{json.dumps(adjuster, indent=1)}\n\nFRAUD INVESTIGATOR (on appeal):\n{json.dumps(fraud, indent=1)}\n\n"
            f"POLICY ANALYST (on appeal):\n{json.dumps(policy, indent=1)}"
            + precedent_block(ev) + photo_block(ev))])

        v2 = build_verdict(claim_id, claim, adjuster, fraud, policy, arbiter)
        # The model self-reports outcome; the truth is whether the standing decision changed.
        outcome = "overturn" if v2.get("decision") != v1.get("decision") else "uphold"
        v2["appeal"] = {"outcome": outcome, "clauses": arbiter.get("clauses", []),
                        "text": appeal_text, "new_photo": bool(new_photo_path)}
        diff = [{"field": k, "v1": v1.get(k), "v2": v2.get(k)} for k in ("decision", "confidence", "referral_reason")
                if v1.get(k) != v2.get(k)]
        if (v1.get("payout") or {}).get("net") != v2["payout"]["net"]:
            diff.append({"field": "net_payout", "v1": (v1.get("payout") or {}).get("net"), "v2": v2["payout"]["net"]})
        await emit("verdict", v2)
        await emit("appeal.verdict", {"v1": v1_core, "v2": v2, "outcome": v2["appeal"]["outcome"], "diff": diff})
        return v2
