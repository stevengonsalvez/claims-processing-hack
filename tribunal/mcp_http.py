"""Challenge 4: the tribunal MCP server over streamable HTTP, mounted next to the REST API.

One ASGI app, one container, one port:

    uvicorn tribunal.mcp_http:app --host 0.0.0.0 --port 8000

    /mcp        streamable-HTTP MCP transport, the same three tools as `tribunal.mcp_server`
                (list_sample_claims, adjudicate_claim, record_decision) - reused, not re-declared
    /healthz    liveness for Container Apps and the APIM backend probe
    /samples, /adjudicate, /decision, /decisions, /data/*   the `tribunal.api` routes

The tools talk to the tribunal over HTTP at `TRIBUNAL_API` (default `http://localhost:8000`,
which inside the container is this same process), so the stdio server, this HTTP server and
Claude Desktop all exercise exactly one code path. Tool functions are sync and the SDK runs
them on a worker thread, so calling back into our own event loop does not deadlock.

Client check (used by deploy_aca.sh and apim_mcp.sh as their proof):

    python -m tribunal.mcp_http --check https://<host>/mcp [-H "Ocp-Apim-Subscription-Key: <key>"]
    python -m tribunal.mcp_http --check https://<host>/mcp --adjudicate crash2

`--check` alone is the fast path (initialize, list_tools, list_sample_claims). `--adjudicate`
adds the slow one: a real `adjudicate_claim` tool call, ~60-90 s of four agents, which is what
actually exercises gateway timeouts and SSE buffering end to end.
"""
import argparse
import asyncio
import json
import os
import sys

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .api import app as tribunal_api
from .mcp_server import API, mcp

MCP_PATH = os.environ.get("MCP_PATH", "/mcp")


async def healthz(_request):
    return JSONResponse({"status": "ok", "mcp": MCP_PATH, "tribunal_api": API})


# Stateless: every request carries its own MCP session, so an APIM gateway or a second ACA
# replica can serve any request without sticky routing. DNS-rebinding protection is off
# because the Host header is the ACA FQDN / the APIM gateway, not localhost (the API itself
# is unauthenticated by design - see tribunal/README.md Limitations).
app = mcp.streamable_http_app(
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
app.router.routes.insert(0, Route("/healthz", healthz, methods=["GET"]))
# everything that is not /mcp or /healthz is the existing FastAPI app, unchanged
app.router.routes.append(Mount("/", app=tribunal_api))


def _payload(result):
    """Tool results come back structured; fall back to the text content block."""
    body = result.structured_content
    value = body.get("result", body) if isinstance(body, dict) else body
    if value is None:
        value = json.loads("".join(getattr(b, "text", "") for b in result.content) or "null")
    return value


async def check(url: str, headers: dict[str, str], adjudicate: str | None = None) -> int:
    """Initialize a real streamable-HTTP MCP client against `url`, list tools, call one.

    With `adjudicate`, also call `adjudicate_claim(sample)` over the same session and print the
    verdict it returns - the long-running path, the one a gateway can time out or buffer.
    """
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    verdict = None
    async with create_mcp_http_client(headers=headers or None) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write, *_):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = sorted(t.name for t in (await session.list_tools()).tools)
                result = await session.call_tool("list_sample_claims", {})
                if adjudicate:
                    # four agents, ~60-90 s; the SDK's own SSE read timeout is 300 s
                    verdict = _payload(
                        await session.call_tool(
                            "adjudicate_claim", {"sample": adjudicate}, read_timeout_seconds=300.0
                        )
                    )

    samples = _payload(result)
    out = {
        "url": url,
        "server": init.server_info.name,
        "protocol": init.protocol_version,
        "tools": tools,
        "samples": [s["name"] for s in samples] if isinstance(samples, list) else samples,
    }
    print(json.dumps(out, separators=(",", ":"), default=str))

    expected = ["adjudicate_claim", "list_sample_claims", "record_decision"]
    problems = [
        p
        for p in (
            None if tools == expected else f"tools {tools} != {expected}",
            None if isinstance(samples, list) and len(samples) == 5 else f"list_sample_claims returned {samples!r}",
        )
        if p
    ]

    if adjudicate:
        v = verdict if isinstance(verdict, dict) else {}
        # print the payout sub-object verbatim (claimed/covered/deductible/limit/net) rather than
        # one flattened number: net = min(covered, limit) - deductible, and a summary that shows
        # only one of them cannot be checked against the direct /adjudicate run.
        summary = {
            "sample": adjudicate,
            "claim_id": v.get("claim_id"),
            "decision": v.get("decision"),
            "confidence": v.get("confidence"),
            "payout": {k: (v.get("payout") or {}).get(k) for k in ("claimed", "covered", "deductible", "limit", "net")},
            "fraud_score": (v.get("fraud") or {}).get("score"),
            "policy_number": (v.get("policy") or {}).get("policy_number"),
            "opinions": sorted((v.get("opinions") or {})),
            "letter_chars": len(v.get("letter") or ""),
        }
        print(json.dumps(summary, separators=(",", ":"), default=str))
        if v.get("decision") not in ("approve", "deny", "refer"):
            problems.append(f"adjudicate_claim({adjudicate!r}) returned {verdict!r}")
        elif sorted(v.get("opinions") or {}) != ["adjuster", "arbiter", "fraud", "policy"]:
            problems.append(f"adjudicate_claim({adjudicate!r}) opinions {sorted(v.get('opinions') or {})}")

    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", metavar="URL", required=True, help="MCP endpoint, e.g. https://host/mcp")
    ap.add_argument("-H", "--header", action="append", default=[], metavar="NAME: VALUE")
    ap.add_argument(
        "--adjudicate",
        metavar="SAMPLE",
        help="also call the adjudicate_claim tool for this sample (crash1..crash5); ~60-90 s",
    )
    args = ap.parse_args()
    hdrs = dict(h.split(":", 1) for h in args.header)
    sys.exit(
        asyncio.run(
            check(args.check, {k.strip(): v.strip() for k, v in hdrs.items()}, args.adjudicate)
        )
    )
