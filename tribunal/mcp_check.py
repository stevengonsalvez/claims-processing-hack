"""Prove the MCP server works from a real client, spawned exactly as VS Code spawns it.

    .venv/bin/python -m tribunal.mcp_check > logs/mcp-check.log      # exits 0 on success

Reads `.vscode/mcp.json` (the file VS Code itself reads), launches `claims-tribunal` over
stdio with the mcp 2.x SDK, initializes, lists tools, calls `list_sample_claims`, then
`adjudicate_claim(crash1)` and asserts the tribunal denies it (Section 4.1 trap). Prints
one compact JSON summary; a failed assertion exits 1.
"""
import asyncio
import json
import pathlib
import sys

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG = REPO / ".vscode" / "mcp.json"
SERVER = "claims-tribunal"
SAMPLE = "crash1"
EXPECTED = "deny"


def server_params() -> StdioServerParameters:
    """The VS Code stdio entry, turned into SDK spawn parameters (no second source of truth)."""
    entry = json.loads(CONFIG.read_text())["servers"][SERVER]
    return StdioServerParameters(
        command=entry["command"], args=entry["args"], cwd=entry.get("cwd"), env=entry.get("env")
    )


def payload(result: types.CallToolResult):
    """Structured output if the tool declared one, else the text block, parsed when it is JSON."""
    if result.is_error:
        raise SystemExit(f"tool call failed: {[getattr(b, 'text', b) for b in result.content]}")
    if result.structured_content is not None:
        body = result.structured_content
        # MCPServer wraps non-object returns (e.g. a list) under "result".
        return body["result"] if isinstance(body, dict) and set(body) == {"result"} else body
    text = "".join(getattr(b, "text", "") for b in result.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def main() -> int:
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            samples = payload(await session.call_tool("list_sample_claims", {}))
            verdict = payload(await session.call_tool("adjudicate_claim", {"sample": SAMPLE}))

    if not isinstance(verdict, dict):
        raise SystemExit(f"adjudicate_claim returned {verdict!r}, not a verdict object")
    out = {
        "server": init.server_info.name,
        "protocol": init.protocol_version,
        "tools": tools,
        "samples": [s["name"] for s in samples] if isinstance(samples, list) else samples,
        "sample": SAMPLE,
        "decision": verdict.get("decision"),
        "confidence": verdict.get("confidence"),
        "net_payout": verdict.get("payout", {}).get("net"),
        "fraud_score": verdict.get("fraud", {}).get("score"),
        "citations": verdict.get("policy", {}).get("citations"),
        "opinions": sorted(verdict.get("opinions", {})),
    }
    print(json.dumps(out, separators=(",", ":"), default=str))

    problems = [
        p
        for p in (
            None if {"list_sample_claims", "adjudicate_claim", "record_decision"} <= set(tools) else f"tools missing: {tools}",
            None if isinstance(samples, list) and samples else f"list_sample_claims returned {samples!r}",
            None if out["decision"] == EXPECTED else f"{SAMPLE} decision {out['decision']!r} != {EXPECTED!r}",
        )
        if p
    ]
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
