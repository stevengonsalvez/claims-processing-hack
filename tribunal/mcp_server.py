"""MCP server exposing the tribunal to Claude Desktop / VS Code (Challenge 4, local, no APIM).

    .venv/bin/python -m tribunal.mcp_server            # stdio transport

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"claims-tribunal": {"command": "<repo>/.venv/bin/python",
        "args": ["-m", "tribunal.mcp_server"], "cwd": "<repo>", "env": {"TRIBUNAL_API": "http://localhost:8423"}}}}
"""
import json
import os

import httpx
from mcp.server.mcpserver import MCPServer

API = os.environ.get("TRIBUNAL_API", "http://localhost:8000")
mcp = MCPServer("claims-tribunal")


def _sse(resp: httpx.Response):
    event, data = None, ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].strip()
        elif line == "" and event:
            yield event, json.loads(data or "{}")
            event, data = None, ""


@mcp.tool()
def list_sample_claims() -> list[dict]:
    """Demo claims bundled with the tribunal (statement pages + damage photo)."""
    return httpx.get(f"{API}/samples", timeout=30).json()


@mcp.tool()
def adjudicate_claim(sample: str) -> dict:
    """Run a sample claim through the four-agent tribunal and return the verdict:
    decision (approve/deny/refer), confidence, payout, fraud score + evidence,
    policy citations, each specialist's opinion and the claimant letter."""
    opinions, verdict = {}, None
    with httpx.stream("POST", f"{API}/adjudicate", data={"sample": sample}, timeout=300) as resp:
        for event, data in _sse(resp):
            if event == "agent.done" and data.get("prose"):
                opinions[data["agent"]] = data["prose"]
            elif event == "verdict":
                verdict = data
            elif event == "error":
                opinions[f"error:{data.get('agent')}"] = data.get("message")
    if not verdict:
        return {"error": "tribunal produced no verdict", "opinions": opinions}
    verdict.pop("claim", None)
    verdict["opinions"] = opinions
    return verdict


@mcp.tool()
def record_decision(claim_id: str, decision: str, reason: str = "") -> dict:
    """Record the human adjuster's approve / deny / override for a claim."""
    return httpx.post(f"{API}/decision", json={"claim_id": claim_id, "human_decision": decision, "reason": reason}, timeout=30).json()


if __name__ == "__main__":
    mcp.run()
