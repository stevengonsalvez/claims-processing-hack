"""Claims Tribunal on Microsoft Agent Framework (Challenge 4).

The same tribunal as `tribunal/workflow.py`, expressed as an `agent_framework`
workflow graph instead of hand-rolled `asyncio.gather`: five `Executor` classes
wired by `WorkflowBuilder` with typed dataclass messages on every edge, one
fan-out edge group (Structure -> three specialists) and one fan-in edge group
(three specialists -> Arbiter, which runs once all three have finished).

    ClaimIntake -> OcrText -> Evidence =3=> Opinion =3=> VerdictMsg

Streaming: executors publish `TribunalEvent` values with `ctx.yield_output`;
`adjudicate` consumes `workflow.run(..., stream=True)` and replays them through
the same `emit(event, data)` callback contract the FastAPI SSE route and the
React UI already speak, so nothing downstream changes.

Verdict assembly and the evidence step are imported from `tribunal.workflow`
(`build_verdict`, `gather_evidence`, `LABELS`) so both orchestrators produce
byte-identical verdicts from identical evidence.
"""
import asyncio
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Never

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from . import prompts, telemetry
from .foundry import MODEL, image_part, parse_json, run_agent, split_opinion, text_part
from .telemetry import agent_span, span
from .workflow import (LABELS, REPO, build_verdict, evidence_event, gather_evidence,
                       photo_block, precedent_block)

sys.path.append(os.path.join(REPO, "challenge-2", "agents"))
from ocr_agent import extract_text_with_ocr  # noqa: E402  (Challenge 2, Mistral Document AI)


# --------------------------------------------------------------------------- #
# Typed messages. Every workflow edge carries one of these dataclasses; the
# builder type-checks source output types against target input types at build().
# --------------------------------------------------------------------------- #
@dataclass
class ClaimIntake:
    """Workflow input: the raw artefacts a claimant submitted."""

    claim_id: str
    statement_paths: list[str]
    photo_path: str | None = None


@dataclass
class OcrText:
    """Ocr -> Structure: transcribed statement pages."""

    claim_id: str
    text: str
    pages: int
    errors: list[str] = field(default_factory=list)
    photo_path: str | None = None


@dataclass
class ClaimRecord:
    """The structured claim, carried inside Evidence and Opinion.

    `evidence` is the full retrieval bundle from `workflow.gather_evidence`
    (prior claims, policy chunks, adjuster precedents, prior-photo matches) so the
    fan-in Arbiter can cite precedents and photo matches without a second lookup.
    """

    claim_id: str
    claim: dict
    photo_path: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class Evidence:
    """Structure -> {Adjuster, Fraud, Policy} (fan-out): claim plus retrieved context."""

    claim: ClaimRecord
    prior_claims: list[dict] = field(default_factory=list)
    policy_chunks: list[dict] = field(default_factory=list)


@dataclass
class Opinion:
    """{Adjuster, Fraud, Policy} -> Arbiter (fan-in): one tribunal member's ruling."""

    agent: str
    claim: ClaimRecord
    prose: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class VerdictMsg:
    """Arbiter's workflow output: the assembled verdict."""

    claim_id: str
    verdict: dict


@dataclass
class TribunalEvent:
    """A UI event on its way out: replayed verbatim into `emit(event, data)`."""

    event: str
    data: dict


def _split(raw: str) -> tuple[str, dict]:
    """`split_opinion`, with a fallback for the run where the model emits a bare `===`
    (or no marker at all) instead of `===JSON===`: recover the JSON object from the prose."""
    prose, data = split_opinion(raw)
    if not data and (recovered := parse_json(prose)):
        data = recovered
        prose = prose[:prose.find("{")].rstrip().rstrip("= \n")
    return prose, data


def _data_url(path: str) -> str:
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


async def _start(ctx, agent: str) -> None:
    await ctx.yield_output(TribunalEvent("agent.start", {"agent": agent, "label": LABELS[agent]}))


async def _done(ctx, agent: str, t0: float, **result) -> None:
    await ctx.yield_output(
        TribunalEvent("agent.done", {"agent": agent, "ms": int((time.time() - t0) * 1000), **result}))


def _token(ctx, agent: str):
    async def _t(text: str) -> None:
        await ctx.yield_output(TribunalEvent("agent.token", {"agent": agent, "text": text}))
    return _t


# --------------------------------------------------------------------------- #
# Executors
# --------------------------------------------------------------------------- #
class OcrExecutor(Executor):
    """Mistral Document AI over every statement page (Challenge 2 tool), pages in parallel."""

    @handler
    async def run(self, intake: ClaimIntake, ctx: WorkflowContext[OcrText, TribunalEvent]) -> None:
        t0 = time.time()
        await _start(ctx, "ocr")
        with agent_span("ocr", intake.claim_id, "mistral-document-ai-2512"):
            raws = await asyncio.gather(
                *[asyncio.to_thread(extract_text_with_ocr, p) for p in intake.statement_paths])
        pages = [json.loads(r) for r in raws]
        text = "\n\n--- PAGE ---\n\n".join(p.get("text", "") for p in pages)
        errors = [p["error"] for p in pages if p.get("status") != "success"]
        await _done(ctx, "ocr", t0, chars=len(text), pages=len(pages), preview=text[:600], errors=errors)
        await ctx.send_message(
            OcrText(intake.claim_id, text, len(pages), errors, intake.photo_path))


class StructureExecutor(Executor):
    """gpt-4.1-mini turns OCR text into a claim record, then pulls AI Search evidence."""

    @handler
    async def run(self, msg: OcrText, ctx: WorkflowContext[Evidence, TribunalEvent]) -> None:
        t0 = time.time()
        await _start(ctx, "structure")
        with agent_span("structure", msg.claim_id, MODEL):
            raw = await run_agent("TribunalStructureAgent", prompts.STRUCTURE,
                                  [text_part(f"OCR TEXT:\n{msg.text}")], on_token=_token(ctx, "structure"))
        claim = parse_json(raw)
        await _done(ctx, "structure", t0, data=claim)
        await ctx.yield_output(TribunalEvent("claim", claim))

        ev = await gather_evidence(claim, msg.photo_path, msg.claim_id)
        await ctx.yield_output(TribunalEvent("evidence", evidence_event(ev)))
        await ctx.send_message(Evidence(ClaimRecord(msg.claim_id, claim, msg.photo_path, ev),
                                        ev["prior_claims"], ev["policy_chunks"]))


class _MemberExecutor(Executor):
    """Shared body for the three specialists: stream tokens, split prose from JSON, never raise."""

    agent: str = ""

    async def _opinion(self, claim: ClaimRecord, parts: list[dict], instructions: str, ctx) -> None:
        t0 = time.time()
        await _start(ctx, self.agent)
        try:
            with agent_span(self.agent, claim.claim_id, MODEL):
                raw = await run_agent(f"Tribunal{self.agent.title()}Agent", instructions, parts,
                                      on_token=_token(ctx, self.agent))
            prose, data = _split(raw)
            await _done(ctx, self.agent, t0, prose=prose, data=data)
        except Exception as e:  # one bad member must not sink the tribunal
            prose, data = "", {"error": str(e)[:300]}
            await ctx.yield_output(TribunalEvent("error", {"agent": self.agent, "message": str(e)[:300]}))
        await ctx.send_message(Opinion(self.agent, claim, prose, data))


class AdjusterExecutor(_MemberExecutor):
    """Damage and repair cost from the photo."""

    agent = "adjuster"

    @handler
    async def run(self, ev: Evidence, ctx: WorkflowContext[Opinion, TribunalEvent]) -> None:
        photo = [image_part(_data_url(ev.claim.photo_path))] if ev.claim.photo_path else []
        parts = [text_part(f"CLAIM:\n{json.dumps(ev.claim.claim, indent=1)}")] + photo
        await self._opinion(ev.claim, parts, prompts.ADJUSTER, ctx)


class FraudExecutor(_MemberExecutor):
    """Vector search over prior claims plus statement-vs-photo contradictions."""

    agent = "fraud"

    @handler
    async def run(self, ev: Evidence, ctx: WorkflowContext[Opinion, TribunalEvent]) -> None:
        photo = [image_part(_data_url(ev.claim.photo_path))] if ev.claim.photo_path else []
        parts = [text_part(
            f"CLAIM:\n{json.dumps(ev.claim.claim, indent=1)}\n\n"
            f"PRIOR CLAIMS (vector search, top 5):\n{json.dumps(ev.prior_claims, indent=1)}"
            + photo_block(ev.claim.evidence) + precedent_block(ev.claim.evidence))] + photo
        await self._opinion(ev.claim, parts, prompts.FRAUD, ctx)


class PolicyExecutor(_MemberExecutor):
    """Coverage strictly from the policy sections retrieved out of AI Search."""

    agent = "policy"

    @handler
    async def run(self, ev: Evidence, ctx: WorkflowContext[Opinion, TribunalEvent]) -> None:
        parts = [text_part(
            f"CLAIM:\n{json.dumps(ev.claim.claim, indent=1)}\n\nPOLICY DOCUMENT SECTIONS:\n" +
            "\n\n".join(f"[{c.get('title')}]\n{c.get('content')}" for c in ev.policy_chunks)
            + precedent_block(ev.claim.evidence))]
        await self._opinion(ev.claim, parts, prompts.POLICY, ctx)


class ArbiterExecutor(Executor):
    """Fan-in: runs once all three opinions have landed, rules, and assembles the verdict."""

    @handler
    async def run(self, opinions: list[Opinion], ctx: WorkflowContext[Never, TribunalEvent | VerdictMsg]) -> None:
        by_agent = {o.agent: o.data for o in opinions}
        adjuster, fraud, policy = (by_agent.get(k, {}) for k in ("adjuster", "fraud", "policy"))
        claim = opinions[0].claim

        t0 = time.time()
        await _start(ctx, "arbiter")
        brief = text_part(
            f"CLAIM:\n{json.dumps(claim.claim, indent=1)}\n\nADJUSTER:\n{json.dumps(adjuster, indent=1)}\n\n"
            f"FRAUD INVESTIGATOR:\n{json.dumps(fraud, indent=1)}\n\n"
            f"POLICY ANALYST:\n{json.dumps(policy, indent=1)}"
            + precedent_block(claim.evidence) + photo_block(claim.evidence))
        arbiter, last_err = None, None
        # The Arbiter is the hero card: a single upstream blip would collapse the verdict to
        # "arbiter output unparseable" with no clauses, so retry once before giving up.
        for attempt in (1, 2):
            try:
                with agent_span("arbiter", claim.claim_id, MODEL):
                    raw = await run_agent("TribunalArbiterAgent", prompts.ARBITER, [brief],
                                          on_token=_token(ctx, "arbiter") if attempt == 1 else None)
                prose, arbiter = _split(raw)
                await _done(ctx, "arbiter", t0, prose=prose, data=arbiter)
                break
            except Exception as e:
                last_err = e
        if arbiter is None:
            arbiter = {"error": str(last_err)[:300]}
            await ctx.yield_output(TribunalEvent("error", {"agent": "arbiter", "message": str(last_err)[:300]}))

        verdict = build_verdict(claim.claim_id, claim.claim, adjuster, fraud, policy, arbiter)
        await ctx.yield_output(VerdictMsg(claim.claim_id, verdict))


def build_workflow():
    """Wire the tribunal graph. Kept public so `python -m tribunal.af_workflow` can print it."""
    ocr, structure = OcrExecutor(id="ocr"), StructureExecutor(id="structure")
    adjuster, fraud, policy = AdjusterExecutor(id="adjuster"), FraudExecutor(id="fraud"), PolicyExecutor(id="policy")
    arbiter = ArbiterExecutor(id="arbiter")
    return (WorkflowBuilder(start_executor=ocr, name="claims-tribunal",
                            description="OCR -> structure -> [adjuster | fraud | policy] -> arbiter")
            .add_edge(ocr, structure)
            .add_fan_out_edges(structure, [adjuster, fraud, policy])
            .add_fan_in_edges([adjuster, fraud, policy], arbiter)
            .build())


async def adjudicate(claim_id: str, statement_paths: list[str], photo_path: str | None, emit) -> dict:
    """Same signature and same emit contract as `tribunal.workflow.adjudicate`."""
    verdict: dict = {}
    with span("tribunal.adjudicate", **{"claim.id": claim_id, "orchestrator": "agent-framework"}):
        workflow = build_workflow()
        stream = workflow.run(ClaimIntake(claim_id, statement_paths, photo_path), stream=True)
        async for event in stream:
            if event.type != "output":
                continue
            payload = event.data
            if isinstance(payload, TribunalEvent):
                await emit(payload.event, payload.data)
            elif isinstance(payload, VerdictMsg):
                verdict = payload.verdict
                if (rec := getattr(telemetry, "record_verdict", None)) is not None:
                    rec(verdict)
                await emit("verdict", verdict)
    return verdict


if __name__ == "__main__":  # `python -m tribunal.af_workflow` prints the graph as mermaid
    from agent_framework import WorkflowViz

    print(WorkflowViz(build_workflow()).to_mermaid())
