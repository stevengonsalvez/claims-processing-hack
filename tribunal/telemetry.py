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
