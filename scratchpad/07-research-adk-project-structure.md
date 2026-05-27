# 07 — Research: ADK v2 project structure (adk web / run / api_server)

**Date:** 2026-05-27
**Trigger:** Architect: package = `atlassian_mcp_agent`, and "follow ADK v2 Python correct
folder structure to support adk cli, web and api based calling of the agent."
**Method:** ADK source/docs (AGENTS.md, contributing/adk_project_overview, CLI docs).

---

## The one convention that matters (required by all ADK CLIs)

An **agent package** must be a directory containing:
- `__init__.py` → **must contain `from . import agent`**
- `agent.py` → **must define `root_agent = Agent(...)`** (an `app = App(...)` is also accepted)

This single convention is what lets the ADK CLI auto-discover and load the agent with **no
extra config**, and it is shared by all three entrypoints:

| Command | Purpose | How it finds the agent |
|---|---|---|
| `adk web` | Dev web UI (chat) | Scans the target dir for agent packages; lists them in a dropdown |
| `adk run <agent>` | Interactive CLI/terminal chat | Loads the named agent package |
| `adk api_server` | FastAPI server (REST: `/run`, `/run_sse`, sessions) | Serves the agent packages in the target dir |

All three are pointed at the **parent directory that contains one or more agent packages**
(default: the current directory).

---

## Recommended layout for our Phase 1 (single agent, repo root)

```
sts-jira-adk-agent-skill-mcp-poc/          # repo root (run adk commands here)
├── pyproject.toml
├── .env                                    # AWS_PROFILE, AWS_REGION, BEDROCK_MODEL_ID (gitignored)
├── .env.example
├── justfile
├── atlassian_mcp_agent/                    # ← the agent package (importable)
│   ├── __init__.py                         # from . import agent
│   └── agent.py                            # root_agent = Agent(...)
├── SKILL.md                                # injected into the instruction at construction
├── tests/
│   └── test_token_storage.py
├── docs/
│   ├── VISION.md                           # corrected (Sonnet 4.6)
│   └── phase-1-results.md
├── scratchpad/                             # this research
└── reference_data/                         # untouched reference zip contents
```

Run commands (from repo root):
- `adk web` → opens http://localhost:8000, pick `atlassian_mcp_agent`
- `adk run atlassian_mcp_agent` → terminal chat
- `adk api_server` → REST server (POST `/run` / `/run_sse`)

> The reference repo used exactly this root-level-package shape (`atlassian_mcp_test/`), and
> its `agent.py` docstring shows `adk web .`. We keep the shape, rename to
> `atlassian_mcp_agent`, and it works for web **and** run **and** api_server unchanged.

### `.env` discovery
ADK loads a `.env` from the agent package dir or its parent. Put AWS/Bedrock vars there.
The reference loads them via the standard provider chain — no `python-dotenv` call needed in
`agent.py` when running under `adk` (the CLI loads `.env`); for non-`adk` scripts, load it
explicitly.

### SKILL.md placement
`agent.py::_load_skill()` looks at *parent-of-package* first, then the package dir. With the
layout above, putting `SKILL.md` at repo root works; alternatively place it inside
`atlassian_mcp_agent/` and load relative to `__file__`. Decide in the build (lean: repo root,
matches reference).

---

## Alternative (deferred): `src/` packaged multi-agent layout
ADK's contributing guide also documents a `src/<app>/agents/<agent>/` layout (better for
pip-packaging and multiple agents). Overkill for a single Phase-1 agent; revisit if/when we
add sibling agents or publish a wheel.

## Sources
- ADK AGENTS.md (agent structure convention) — https://github.com/google/adk-python/blob/main/AGENTS.md
- ADK contributing — project overview & architecture — https://github.com/google/adk-python/blob/main/contributing/adk_project_overview_and_architecture.md
- ADK quickstart/streaming folder structure — https://github.com/google/adk-python (llms-full.txt)
