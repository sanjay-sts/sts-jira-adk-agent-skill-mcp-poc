# Common dev commands. On Windows without `just`, see README → "Without just".
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List recipes
default:
    @just --list

# Install deps (incl. dev tools)
sync:
    uv sync --extra dev

# ADK dev web UI → http://localhost:8000 (pick `atlassian_mcp_agent`)
web:
    uv run adk web

# Run the agent in the terminal
run:
    uv run adk run atlassian_mcp_agent

# ADK REST API server (POST /run, /run_sse)
api:
    uv run adk api_server

# Phase 2: multi-user forwarding server (POST /chat with Authorization: Bearer <atlassian token>)
serve port="8080":
    uv run uvicorn atlassian_mcp_agent.server:app --host 127.0.0.1 --port {{port}}

# Phase 2: Level-0 affinity spike (needs ATLAS_TOKEN_A / ATLAS_TOKEN_B for two different users)
spike:
    uv run python spikes/affinity_spike.py

# Phase 2: isolation harness — concurrent/sequential/hijack/history (needs spikes/harness_config.json + a running agent)
harness:
    uv run python spikes/isolation_harness.py

# Unit tests
test:
    uv run pytest

# Lint
lint:
    uv run ruff check .

# Type-check (strict)
typecheck:
    uv run basedpyright

# Lint + typecheck + tests
check: lint typecheck test
