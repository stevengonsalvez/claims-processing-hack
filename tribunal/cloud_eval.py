"""Pre-production cloud evaluation: verdicts -> JSONL -> `evaluate()` logged to the Foundry project.

Challenge 3 asks for the GenAIOps loop, not just local scores. `tribunal.quality`
scores verdicts locally; this module runs the same evaluator family through
`azure.ai.evaluation.evaluate(...)` with `azure_ai_project` pointed at the Foundry
project endpoint, so the run lands in the portal's **Evaluation** tab next to the
traces, and prints the `studio_url` the SDK returns.

    .venv/bin/python -m tribunal.cloud_eval             # dataset + run + upload
    .venv/bin/python -m tribunal.cloud_eval --local     # skip the project upload
    .venv/bin/python -m tribunal.cloud_eval --continuous  # + register the continuous-eval rule

Dataset row (one per `tribunal/data/verdict_*.json`):
  query    claim summary the tribunal was asked to adjudicate
  response Arbiter decision + rationale + claimant letter
  context  the policy / fraud / payout JSON the Arbiter was handed
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from azure.ai.evaluation import (
    AzureOpenAIModelConfiguration,
    CoherenceEvaluator,
    ContentSafetyEvaluator,
    FluencyEvaluator,
    GroundednessEvaluator,
    RelevanceEvaluator,
    evaluate,
)
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AzureAIDataSourceConfig,
    ContinuousEvaluationRuleAction,
    EvaluationRule,
    EvaluationRuleFilter,
    TestingCriterionAzureAIEvaluator,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from .workflow import claim_summary

load_dotenv(override=True)

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data")
ROOT = os.path.dirname(HERE)
LOGS = os.path.join(ROOT, "logs", "cloud_eval")
DATASET = os.path.join(LOGS, "verdicts.jsonl")
RESULT = os.path.join(LOGS, "evaluation_results.json")
OUT = os.path.join(DATA, "cloud_eval.md")

PROJECT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
QUALITY_KEYS = ("groundedness", "relevance", "coherence", "fluency")
SAFETY_KEYS = ("violence", "sexual", "self_harm", "hate_unfairness")


def build_dataset(path: str = DATASET) -> int:
    """Write one JSONL row per saved verdict. Returns the row count."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = 0
    with open(path, "w", encoding="utf-8") as f:
        for vpath in sorted(glob.glob(os.path.join(DATA, "verdict_*.json"))):
            v = json.load(open(vpath, encoding="utf-8"))
            f.write(json.dumps({
                "claim": os.path.basename(vpath)[len("verdict_"):-len(".json")],
                "decision": v["decision"],
                "query": f"Adjudicate this insurance claim:\n{claim_summary(v['claim'])}",
                "response": f"Decision: {v['decision']}. {v['rationale']}\n\n{v['letter']}",
                "context": json.dumps({"policy": v["policy"], "fraud": v["fraud"],
                                       "payout": v["payout"]}, indent=1),
            }) + "\n")
            rows += 1
    return rows


def evaluators() -> dict:
    model = AzureOpenAIModelConfiguration(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_deployment=os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        api_version="2024-10-21",
    )
    evs = {
        "groundedness": GroundednessEvaluator(model),
        "relevance": RelevanceEvaluator(model),
        "coherence": CoherenceEvaluator(model),
        "fluency": FluencyEvaluator(model),
    }
    try:  # the safety evaluators are a Foundry service call, not the judge model
        evs["content_safety"] = ContentSafetyEvaluator(
            azure_ai_project=PROJECT, credential=DefaultAzureCredential())
    except Exception as e:
        print(f"content safety evaluator unavailable, continuing without it: {type(e).__name__}: {str(e)[:200]}")
    return evs


CONT_RULE_ID = "tribunal-arbiter-continuous"
CONT_AGENT = "TribunalArbiterAgent"
CONT_EVALUATORS = ("relevance", "coherence", "fluency")


def register_continuous(project_endpoint: str = PROJECT) -> dict:
    """Continuous evaluation: score every completed Arbiter response in the project.

    `evals.create` registers the grader set (`azure_ai_source` / `responses` scenario,
    built-in Foundry evaluators judged by our own gpt-4.1-mini deployment); the
    evaluation rule then fires it on each `responseCompleted` event of the named
    agent, so scores land under Evaluation > Continuous evaluation in the portal.
    """
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    project = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    openai_client = project.get_openai_client()
    criteria = [TestingCriterionAzureAIEvaluator(
        type="azure_ai_evaluator", name=k, evaluator_name=f"builtin.{k}",
        initialization_parameters={"deployment_name": model},
        data_mapping={"query": "{{item.query}}", "response": "{{item.response}}"},
    ) for k in CONT_EVALUATORS]
    existing = next((e for e in openai_client.evals.list().data if e.name == CONT_RULE_ID), None)
    ev = existing or openai_client.evals.create(
        name=CONT_RULE_ID,
        data_source_config=AzureAIDataSourceConfig(type="azure_ai_source", scenario="responses"),
        testing_criteria=criteria,
    )
    rule = project.evaluation_rules.create_or_update(
        id=CONT_RULE_ID,
        evaluation_rule=EvaluationRule(
            display_name="Tribunal Arbiter - continuous quality",
            description="Relevance / coherence / fluency on every Arbiter verdict produced in this project.",
            action=ContinuousEvaluationRuleAction(eval_id=ev.id, max_hourly_runs=20, sampling_rate=100),
            filter=EvaluationRuleFilter(agent_name=CONT_AGENT),
            event_type="responseCompleted",
            enabled=True,
        ),
    )
    return {"eval_id": ev.id, "rule_id": rule.id, "agent": CONT_AGENT,
            "evaluators": list(CONT_EVALUATORS), "enabled": rule.enabled,
            "event_type": getattr(rule.event_type, "value", str(rule.event_type))}


def write_report(result: dict, name: str, rows: int, upload_error: str | None,
                 continuous: dict | str | None = None) -> str:
    metrics = result.get("metrics", {}) or {}
    studio_url = result.get("studio_url")
    lines = [
        "# Cloud evaluation (azure-ai-evaluation `evaluate`, logged to the Foundry project)", "",
        f"Run `{name}` - {rows} verdicts, judge `{os.environ.get('MODEL_DEPLOYMENT_NAME', 'gpt-4.1-mini')}`, "
        f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.", "",
    ]
    if studio_url:
        lines += [f"Portal: <{studio_url}>", ""]
    elif upload_error:
        lines += ["Upload to the Foundry project FAILED, local results kept in `logs/cloud_eval/`.",
                  "", "```", upload_error.strip()[:900], "```", ""]
    else:
        lines += ["Local run only (`--local`); no portal link.", ""]

    lines += ["## Aggregate metrics", "", "| metric | value |", "|---|---|"]
    for k in sorted(metrics):
        lines.append(f"| `{k}` | {metrics[k]} |")

    per_row = result.get("rows", []) or []
    if per_row:
        cols = [k for k in QUALITY_KEYS if f"outputs.{k}.{k}" in per_row[0]]
        safety = [k for k in SAFETY_KEYS if f"outputs.content_safety.{k}" in per_row[0]]
        lines += ["", "## Per claim", "",
                  "| claim | decision | " + " | ".join(cols + safety) + " |",
                  "|---|---|" + "---|" * (len(cols) + len(safety))]
        for r in per_row:
            vals = [str(r.get(f"outputs.{k}.{k}")) for k in cols]
            vals += [str(r.get(f"outputs.content_safety.{k}")) for k in safety]
            lines.append(f"| {r.get('inputs.claim')} | {r.get('inputs.decision')} | " + " | ".join(vals) + " |")

    if isinstance(continuous, dict):
        lines += ["", "## Continuous evaluation", "",
                  f"Rule `{continuous['rule_id']}` (eval `{continuous['eval_id']}`) is **enabled** on agent "
                  f"`{continuous['agent']}`: every `{continuous['event_type']}` event is scored by "
                  + ", ".join(f"`builtin.{k}`" for k in continuous["evaluators"])
                  + ", sampling 100%, max 20 runs/hour. Portal: project > Evaluation > Continuous evaluation.",
                  "", "Registered with `python -m tribunal.cloud_eval --continuous` "
                  "(`AIProjectClient.evaluation_rules.create_or_update` + `evals.create`)."]
    elif continuous:
        lines += ["", "## Continuous evaluation", "",
                  "Registration failed; see `tribunal/docs/evaluation.md` for the portal steps.",
                  "", "```", str(continuous).strip()[:900], "```"]

    lines += ["", "Dataset: `logs/cloud_eval/verdicts.jsonl` (query = claim summary, response = Arbiter "
              "decision + rationale + claimant letter, context = Policy Analyst / Fraud Investigator / payout "
              "JSON the Arbiter was given). Raw per-row output: `logs/cloud_eval/evaluation_results.json`.", ""]
    text = "\n".join(lines)
    open(OUT, "w", encoding="utf-8").write(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true", help="do not log the run to the Foundry project")
    ap.add_argument("--continuous", action="store_true",
                    help="also register/refresh the continuous-evaluation rule on the Arbiter agent")
    args = ap.parse_args()

    rows = build_dataset()
    if not rows:
        print("no tribunal/data/verdict_*.json to evaluate; run tribunal.smoke first", file=sys.stderr)
        return 1
    print(f"dataset: {DATASET} ({rows} rows)")

    name = f"tribunal-verdicts-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    kwargs = dict(data=DATASET, evaluators=evaluators(), evaluation_name=name,
                  output_path=RESULT, tags={"component": "claims-tribunal", "stage": "pre-production"})
    upload_error = None
    if args.local or not PROJECT:
        result = evaluate(**kwargs)
    else:
        try:
            result = evaluate(azure_ai_project=PROJECT, **kwargs)
        except Exception as e:  # keep the local run when only the project upload is the problem
            upload_error = f"{type(e).__name__}: {e}"
            print(f"project upload failed: {upload_error[:400]}\nre-running locally", file=sys.stderr)
            result = evaluate(**kwargs)

    continuous: dict | str | None = None
    if args.continuous and PROJECT:
        try:
            continuous = register_continuous()
            print("continuous evaluation:", json.dumps(continuous, indent=1))
        except Exception as e:
            continuous = f"{type(e).__name__}: {e}"
            print(f"continuous evaluation registration failed: {continuous[:400]}", file=sys.stderr)

    print("metrics:", json.dumps(result.get("metrics", {}), indent=1))
    print("studio_url:", result.get("studio_url"))
    print(write_report(result, name, rows, upload_error, continuous))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
