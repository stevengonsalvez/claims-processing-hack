# MCP: the tribunal inside VS Code and Claude Desktop

`tribunal/mcp_server.py` is an MCP stdio server wrapping the running tribunal API. Any MCP
client can spawn it, and adjudicate a claim through the same four agents the browser demo
drives.

```
┌────────────────┐  stdio JSON-RPC  ┌──────────────────┐  HTTP + SSE  ┌───────────────┐
│ VS Code /      │─────────────────▶│ tribunal.mcp_    │─────────────▶│ FastAPI :8423 │
│ Claude Desktop │◀─────────────────│ server (3 tools) │◀─────────────│ 4-agent flow  │
└────────────────┘                  └──────────────────┘              └───────────────┘
```

| tool | does |
|---|---|
| `list_sample_claims` | the 5 bundled demo claims (statement pages + damage photo) |
| `adjudicate_claim(sample)` | runs the tribunal, returns decision / confidence / payout / fraud / citations / each agent's opinion / claimant letter |
| `record_decision(claim_id, decision, reason)` | logs the human approve / deny / override |

The API must be running first (`tribunal/dev.sh`; the dev session used here serves :8423).
The server never writes anything but JSON-RPC frames to stdout, and returns an error dict
with the API URL and a hint if the API is unreachable, instead of failing the tool call.

## VS Code

Already committed as `.vscode/mcp.json`; open the repo and VS Code offers to start it.

```json
{
  "servers": {
    "claims-tribunal": {
      "type": "stdio",
      "command": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e/.venv/bin/python",
      "args": ["-m", "tribunal.mcp_server"],
      "cwd": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e",
      "env": { "TRIBUNAL_API": "http://localhost:8423" }
    }
  }
}
```

Then in Copilot Chat: Agent mode, tools picker, tick `claims-tribunal`.

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), then quit and
reopen Claude Desktop:

```json
{
  "mcpServers": {
    "claims-tribunal": {
      "command": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e/.venv/bin/python",
      "args": ["-m", "tribunal.mcp_server"],
      "cwd": "/Users/stevengonsalvez/.agents-in-a-box/worktrees/by-name/claims-processing-hack--ms-hack--b61fe60e",
      "env": { "TRIBUNAL_API": "http://localhost:8423" }
    }
  }
}
```

## Try it

1. Start the API: `tribunal/dev.sh` (or confirm `curl -s http://localhost:8423/samples`).
2. Ask Claude Desktop: **"Use claims-tribunal to adjudicate the sample claim crash4 and tell me why it was referred."**
3. Expect `refer`, fraud score 0.75+, and CLM-0412 (same VIN, paid under another name) in the evidence. Observed through MCP (`logs/mcp-crash4.log`): `refer`, fraud `0.8`, evidence `CLM-0412 … VIN 4S4BRBCC2B3378210`, similarity `0.784`, `recommended_action: refer_siu`.

## Proof

```bash
.venv/bin/python -m tribunal.mcp_check > logs/mcp-check.log     # exits 0
```

`tribunal/mcp_check.py` reads `.vscode/mcp.json`, spawns the server over stdio exactly as
VS Code would, initializes, lists tools, calls `list_sample_claims`, then
`adjudicate_claim("crash1")` and fails unless the decision is `deny`. Observed
(2026-09-07, ~42 s end to end):

```json
{"server":"claims-tribunal","protocol":"2025-11-25","tools":["list_sample_claims","adjudicate_claim","record_decision"],
 "samples":["crash1","crash2","crash3","crash4","crash5"],"sample":"crash1","decision":"deny","confidence":1.0,
 "net_payout":0,"fraud_score":0.0,"citations":["Section 4.1: Your Vehicle - Collision damage to your vehicle", "..."],
 "opinions":["adjuster","arbiter","fraud","policy"]}
```

## Limits

- Local only: stdio, no auth, no APIM. Remote exposure is the Challenge 4 follow-up.
- `adjudicate_claim` takes ~45 s per claim (it runs six models); MCP clients with a short
  tool timeout will need it raised.
- Only the bundled samples are adjudicable; there is no upload tool yet.
