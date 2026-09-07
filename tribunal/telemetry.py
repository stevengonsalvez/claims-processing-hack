"""OpenTelemetry -> Application Insights. One span per agent, gen_ai.* attributes so
the Foundry tracing tab and App Insights transaction search both render them."""
import logging
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from opentelemetry import trace

load_dotenv(override=True)

_cs = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _cs:
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=_cs, logger_name="claims-tribunal")

tracer = trace.get_tracer("claims-tribunal")

# Challenge 2 modules call logging.basicConfig(INFO); keep SDK request/response chatter out of stdout.
for _n in ("azure", "httpx", "httpcore", "openai", "opentelemetry"):
    logging.getLogger(_n).setLevel(logging.WARNING)


@contextmanager
def span(name: str, **attrs):
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s


def agent_span(agent: str, claim_id: str, model: str):
    return span(
        f"agent {agent}",
        **{
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent,
            "gen_ai.request.model": model,
            "gen_ai.system": "azure.ai.foundry",
            "claim.id": claim_id,
        },
    )


# Challenge 3 proactive alerting: the alert rule queries AppTraces for this event name.
verdict_log = logging.getLogger("claims-tribunal")
verdict_log.setLevel(logging.INFO)


def record_verdict(verdict: dict) -> dict:
    """Stamp the verdict on the current span and emit a `tribunal.verdict` log record.

    The span attributes make the decision visible in transaction search / the Foundry
    tracing tab; the log record is what `tribunal/alerts.sh` alerts on (it lands in
    AppTraces with the fields in customDimensions, via configure_azure_monitor's
    logger_name="claims-tribunal" handler).
    """
    fields = {
        "tribunal.decision": str(verdict.get("decision", "refer")),
        "tribunal.fraud_score": float((verdict.get("fraud") or {}).get("score") or 0),
        "tribunal.net_payout": float((verdict.get("payout") or {}).get("net") or 0),
        "claim.id": str(verdict.get("claim_id", "")),
    }
    s = trace.get_current_span()
    if s is not None:
        for k, v in fields.items():
            s.set_attribute(k, v)
    verdict_log.warning(
        "tribunal.verdict %s %s fraud=%.2f net=%.0f",
        fields["claim.id"], fields["tribunal.decision"],
        fields["tribunal.fraud_score"], fields["tribunal.net_payout"],
        extra={"event_name": "tribunal.verdict", **fields},
    )
    return fields
