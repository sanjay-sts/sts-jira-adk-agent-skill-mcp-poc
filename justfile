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
