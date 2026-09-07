"""MCP server exposing the tribunal to Claude Desktop / VS Code (Challenge 4, local, no APIM).

    .venv/bin/python -m tribunal.mcp_server            # stdio transport

Client setup (VS Code `.vscode/mcp.json`, Claude Desktop `claude_desktop_config.json`,
and a `try it` script) lives in `tribunal/docs/mcp.md`. `python -m tribunal.mcp_check`
spawns this module exactly as those clients do and asserts a real verdict comes back.

stdio hygiene: the wire is this process's stdout, so nothing may print there outside the
JSON-RPC frames. Imports run under `redirect_stdout(sys.stderr)` (a chatty dependency
banner would corrupt the first frame), logging is pinned to stderr, and while serving the
SDK's `stdio_server` additionally points fd 1 at stderr. The tools themselves only return.
"""
import contextlib
import json
import logging
import os
import sys

with contextlib.redirect_stdout(sys.stderr):
    import httpx
    from mcp.server.mcpserver import MCPServer

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

API = os.environ.get("TRIBUNAL_API", "http://localhost:8000")
# connect fast (the API is local: unreachable should fail in seconds, not minutes);
# read generously, an adjudication streams for ~45 s per claim with quiet gaps.
QUICK = httpx.Timeout(30.0, connect=5.0)
STREAM = httpx.Timeout(300.0, connect=5.0)
mcp = MCPServer("claims-tribunal")


def _unreachable(exc: Exception) -> dict:
    """Actionable error dict for the model instead of a raised exception in the transcript."""
    return {
        "error": f"{type(exc).__name__}: {exc}",
        "api": API,
        "hint": f"the tribunal API at {API} did not answer; start it with tribunal/dev.sh "
        "and set TRIBUNAL_API in the MCP client config to the port it prints",
    }


def _sse(resp: httpx.Response):
    event, data = None, ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].strip()
        elif line == "" and event:
            try:
                payload = json.loads(data or "{}")
            except json.JSONDecodeError:
                payload = {"agent": "tribunal", "message": f"unparseable SSE data: {data[:200]}"}
                event = "error"
            yield event, payload
            event, data = None, ""


@mcp.tool()
def list_sample_claims() -> list[dict] | dict:
    """Demo claims bundled with the tribunal (statement pages + damage photo)."""
    try:
        resp = httpx.get(f"{API}/samples", timeout=QUICK)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:  # noqa: BLE001 - surfaced to the model as data, never as a crash
        return _unreachable(e)


@mcp.tool()
def adjudicate_claim(sample: str) -> dict:
    """Run a sample claim through the four-agent tribunal and return the verdict:
    decision (approve/deny/refer), confidence, payout, fraud score + evidence,
    policy citations, each specialist's opinion and the claimant letter."""
    opinions, verdict = {}, None
    try:
        with httpx.stream("POST", f"{API}/adjudicate", data={"sample": sample}, timeout=STREAM) as resp:
            resp.raise_for_status()
            for event, data in _sse(resp):
                if event == "agent.done" and data.get("prose"):
                    opinions[data["agent"]] = data["prose"]
                elif event == "verdict":
                    verdict = data
                elif event == "error":
                    opinions[f"error:{data.get('agent')}"] = data.get("message")
    except Exception as e:  # noqa: BLE001
        return _unreachable(e) | {"opinions": opinions}
    if not verdict:
        return {"error": "tribunal produced no verdict", "sample": sample, "opinions": opinions}
    verdict.pop("claim", None)
    verdict["opinions"] = opinions
    return verdict


@mcp.tool()
def record_decision(claim_id: str, decision: str, reason: str = "") -> dict:
    """Record the human adjuster's approve / deny / override for a claim."""
    try:
        resp = httpx.post(
            f"{API}/decision",
            json={"claim_id": claim_id, "human_decision": decision, "reason": reason},
            timeout=QUICK,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:  # noqa: BLE001
        return _unreachable(e)


if __name__ == "__main__":
    mcp.run()
