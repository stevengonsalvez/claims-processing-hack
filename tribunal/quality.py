"""Quality + safety evaluation of saved verdicts with azure-ai-evaluation (Challenge 3).

Scores the Arbiter's rationale + letter against the claim (query) and the three
specialist opinions (context): Groundedness, Relevance, Coherence, Fluency (1-5),
plus content safety via the Foundry project when available.

    python -m tribunal.quality        # reads tribunal/data/verdict_*.json, writes quality.md
"""
import glob
import json
import os

from azure.ai.evaluation import (
    AzureOpenAIModelConfiguration,
    CoherenceEvaluator,
    ContentSafetyEvaluator,
    FluencyEvaluator,
    GroundednessEvaluator,
    RelevanceEvaluator,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from .workflow import claim_summary

load_dotenv(override=True)
DATA = os.environ.get("TRIBUNAL_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
OUT = os.path.join(DATA, "quality.md")

model = AzureOpenAIModelConfiguration(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_KEY"],
    azure_deployment=os.environ.get("TRIBUNAL_JUDGE_MODEL", "gpt-4.1-mini"),  # judge stays fixed across model comparisons
    api_version="2024-10-21",
)
QUALITY = {
    "groundedness": GroundednessEvaluator(model),
    "relevance": RelevanceEvaluator(model),
    "coherence": CoherenceEvaluator(model),
    "fluency": FluencyEvaluator(model),
}


def safety_evaluator():
    try:
        return ContentSafetyEvaluator(azure_ai_project=os.environ["AI_FOUNDRY_PROJECT_ENDPOINT"],
                                      credential=DefaultAzureCredential())
    except Exception as e:  # region / permission dependent; report and continue
        print(f"content safety unavailable: {str(e)[:120]}")
        return None


def main():
    safety = safety_evaluator()
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA, "verdict_*.json"))):
        v = json.load(open(path))
        name = os.path.basename(path)[8:-5]
        query = f"Adjudicate this insurance claim:\n{claim_summary(v['claim'])}"
        response = f"Decision: {v['decision']}. {v['rationale']}\n\n{v['letter']}"
        context = json.dumps({"policy": v["policy"], "fraud": v["fraud"], "payout": v["payout"]}, indent=1)
        row = {"claim": name, "decision": v["decision"]}
        for k, ev in QUALITY.items():
            try:
                r = ev(query=query, response=response, context=context) if k == "groundedness" else ev(query=query, response=response)
                row[k] = r.get(k, r.get(f"{k}_score"))
            except Exception as e:
                row[k] = f"err {str(e)[:40]}"
        if safety:
            try:
                r = safety(query=query, response=response)
                row["safety"] = "/".join(f"{k.split('_')[0][:4]}:{r.get(k)}" for k in ("violence", "sexual", "self_harm", "hate_unfairness"))
            except Exception as e:
                row["safety"] = f"err {str(e)[:40]}"
        rows.append(row)
        print(row)

    keys = ["groundedness", "relevance", "coherence", "fluency"] + (["safety"] if safety else [])
    lines = [f"# Verdict quality (azure-ai-evaluation, judge {os.environ.get('TRIBUNAL_JUDGE_MODEL', 'gpt-4.1-mini')}, agents on {os.environ.get('MODEL_DEPLOYMENT_NAME', 'gpt-4.1-mini')}, 1-5)", "",
             "| claim | decision | " + " | ".join(keys) + " |", "|---|---|" + "---|" * len(keys)]
    for r in rows:
        lines.append(f"| {r['claim']} | {r['decision']} | " + " | ".join(str(r.get(k)) for k in keys) + " |")
    avg = {k: sum(float(r[k]) for r in rows if isinstance(r.get(k), (int, float))) / max(1, len(rows)) for k in keys[:4]}
    lines += ["", "Averages: " + ", ".join(f"{k} {v:.1f}" for k, v in avg.items()),
              "", "Query = claim summary; response = Arbiter decision + rationale + claimant letter; "
              "context (groundedness) = Policy Analyst, Fraud Investigator and payout data the Arbiter was given."]
    open(OUT, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
