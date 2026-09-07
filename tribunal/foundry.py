"""Thin wrapper over Microsoft Foundry prompt agents with token streaming.

Agents are created once per process (versions accumulate otherwise) and run via
the Responses API with `stream=True` so every token can be pushed to the UI.
"""
import asyncio
import json
import os
import re

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(override=True)

ENDPOINT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

_project: AIProjectClient | None = None
_openai = None
_versions: dict[str, str] = {}
_lock = asyncio.Lock()


async def clients():
    global _project, _openai
    if _project is None:
        _project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())
        _openai = _project.get_openai_client()
    return _project, _openai


async def ensure_agent(name: str, instructions: str) -> str:
    async with _lock:
        if name not in _versions:
            project, _ = await clients()
            agent = await project.agents.create_version(
                agent_name=name,
                definition=PromptAgentDefinition(model=MODEL, instructions=instructions),
            )
            _versions[name] = agent.name
    return _versions[name]


async def run_agent(name: str, instructions: str, content: list[dict], on_token=None) -> str:
    """Run a Foundry agent on a list of Responses input parts, streaming tokens."""
    agent_name = await ensure_agent(name, instructions)
    _, openai = await clients()
    stream = await openai.responses.create(
        input=[{"role": "user", "content": content}],
        stream=True,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    out: list[str] = []
    async for ev in stream:
        if ev.type == "response.output_text.delta":
            out.append(ev.delta)
            if on_token:
                await on_token(ev.delta)
    return "".join(out)


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def image_part(data_url: str) -> dict:
    return {"type": "input_image", "image_url": data_url}


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def split_opinion(text: str) -> tuple[str, dict]:
    """Agents speak first, then emit `===JSON===` and a JSON object."""
    prose, _, tail = text.partition("===JSON===")
    return prose.strip(), parse_json(tail)
