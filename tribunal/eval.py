"""Scorecard: run the tribunal on the five sample claims, compare against
challenge-6/coverage_ground_truth.json, write tribunal/data/scorecard.md.

    python -m tribunal.eval [crash1 crash3 ...]
"""
import asyncio
import json
import os
import sys
import time

from .api import sample_files
from .workflow import REPO, adjudicate

GT = json.load(open(os.path.join(REPO, "challenge-6", "coverage_ground_truth.json")))
DATA_DIR = os.environ.get("TRIBUNAL_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
OUT = os.path.join(DATA_DIR, "scorecard.md")
MODEL = os.environ.get("TRIBUNAL_AGENT_MODEL") or os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
TRIBUNAL_TO_GT = {"approve": "APPROVED", "deny": "DENIED", "refer": "REFER"}


async def run_one(name: str) -> dict:
    async def emit(event, data):
        if event == "error":
            print(f"  [{name}] error {data}")
    statements, photo = sample_files(name)
    t0 = time.time()
    v = await adjudicate(f"EVAL-{name}", statements, photo, emit)
    v["seconds"] = round(time.time() - t0, 1)
    return v


async def main(names):
    rows = []
    for name in names:
        print(f"running {name} ...")
        v = await run_one(name)
        gt = GT[name]
        coverage = v["policy"].get("coverage_decision")
        rows.append({
            "claim": name, "gt": gt["expected_decision"], "policy_agent": coverage,
            "policy_ok": coverage == gt["expected_decision"],
            "tribunal": TRIBUNAL_TO_GT.get(v["decision"], v["decision"]),
            "fraud": v["fraud"]["score"], "net": v["payout"]["net"], "seconds": v["seconds"],
        })
        json.dump(v, open(os.path.join(os.path.dirname(OUT), f"verdict_{name}.json"), "w"), indent=1)

    ok = sum(r["policy_ok"] for r in rows)
    lines = [f"# Tribunal scorecard ({ok}/{len(rows)} policy decisions match ground truth) · agents on {MODEL}", "",
             "| claim | ground truth | policy agent | match | tribunal verdict | fraud | net payout | s |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['claim']} | {r['gt']} | {r['policy_agent']} | {'yes' if r['policy_ok'] else 'NO'} | "
                     f"{r['tribunal']} | {r['fraud']:.2f} | ${r['net']:,} | {r['seconds']} |")
    lines += ["", "Tribunal verdict may legitimately differ from coverage ground truth: REFER is raised when the "
              "Fraud Investigator finds planted prior-claim evidence (crash4 VIN duplicate, crash3 repeat claimant)."]
    open(OUT, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or sorted(GT)))
