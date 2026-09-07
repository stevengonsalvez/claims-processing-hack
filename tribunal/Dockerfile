# Claims Tribunal - REST API + MCP-over-streamable-HTTP in one container (Challenge 4).
# Built in the cloud with `az acr build` (see tribunal/deploy_aca.sh); Docker is not
# required locally. Build it through that script, not by hand: the paths below are
# repo-root-relative, but the repo root itself carries .venv and tribunal/ui/node_modules
# (~1 GB) and there is no .dockerignore, so deploy_aca.sh rsyncs a staged context holding
# only the four directories COPYed here and passes that as the build context.
FROM python:3.12-slim

WORKDIR /app

# ponytail: the workspace requirements.txt also pulls streamlit, azure-ai-evaluation,
# cosmos, document intelligence and the notebook stack - none of which the served API
# imports. This is the subset tribunal/{api,af_workflow,workflow,foundry,search_tools,
# telemetry,mcp_server,mcp_http}.py and challenge-2/agents/ocr_agent.py actually import,
# which keeps the cloud build near a minute. Ranges match requirements.txt.
RUN pip install --no-cache-dir \
    "fastapi>=0.141.1,<1" \
    "uvicorn[standard]>=0.42.0,<1" \
    "python-multipart>=0.0.20" \
    "httpx>=0.28.1,<1" \
    "mcp>=2.0.0,<3" \
    "python-dotenv>=1.1.0,<2" \
    "openai>=2.25.0,<3" \
    "azure-ai-projects>=2.3.0,<2.4.0" \
    "azure-identity>=1.25.3,<2" \
    "azure-search-documents>=12.0.0,<13" \
    "azure-monitor-opentelemetry>=1.8.9,<2" \
    "agent-framework-core>=1.14.0,<2" \
    "agent-framework-foundry>=1.11.0,<2"

# application code + the demo corpus the API serves (/samples, /data/*)
COPY tribunal/ /app/tribunal/
COPY challenge-2/agents/ /app/challenge-2/agents/
COPY challenge-0/data/ /app/challenge-0/data/
COPY challenge-6/coverage_ground_truth.json /app/challenge-6/coverage_ground_truth.json

# tribunal/data/decisions.json is written at runtime by POST /decision
RUN mkdir -p /app/tribunal/data && useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRIBUNAL_API=http://localhost:8000

EXPOSE 8000

# one ASGI app: /mcp (streamable HTTP) + the tribunal REST routes + /healthz
CMD ["uvicorn", "tribunal.mcp_http:app", "--host", "0.0.0.0", "--port", "8000"]
