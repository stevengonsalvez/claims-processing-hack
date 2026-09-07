"""CLI run of one tribunal session, events printed as they stream.

    python -m tribunal.smoke crash2
"""
import asyncio
import json
import sys

from .api import sample_files
from .workflow import adjudicate


async def main(name: str):
    async def emit(event, data):
        if event == "agent.token":
            print(data["text"], end="", flush=True)
        elif event == "agent.start":
            print(f"\n\n### {data['label']}")
        elif event == "agent.done":
            print(f"\n[{data['agent']} done {data['ms']}ms] " + json.dumps(data.get("data") or {k: v for k, v in data.items() if k in ('chars', 'errors')})[:300])
        elif event == "verdict":
            print("\n\n=== VERDICT ===")
            print(json.dumps({k: v for k, v in data.items() if k not in ("claim", "letter")}, indent=1))
            print("\n--- letter ---\n" + data["letter"])
        elif event == "error":
            print(f"\n!!! {data}")
    statements, photo = sample_files(name)
    await adjudicate(f"SMOKE-{name}", statements, photo, emit)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "crash2"))
