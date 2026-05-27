# ADK × Atlassian Rovo MCP Agent — Phase 1

A single-user Google **ADK** agent that calls the official **Atlassian Rovo Remote MCP
Server** using **OAuth 2.1 + Dynamic Client Registration + PKCE**. Each end user
authenticates with their own identity and sees only the Jira/Confluence data they have
permission for. Model: **Claude Sonnet 4.6 on AWS Bedrock** via LiteLLM.

> The integration seam: ADK has no native DCR/PKCE for MCP, so we inject the MCP Python
> SDK's `OAuthClientProvider` (an `httpx.Auth`) into
> `StreamableHTTPConnectionParams.httpx_client_factory` (ADK ≥ 1.22, GA in 2.0).

## Status

- **Phase 0** (MCP server validation via MCP Inspector): ✅ done — see `reference_data/adk-atlassian-mcp/docs/phase-0-results.md`
- **Phase 1** (this single-user ADK agent): 🚧 in progress
- **Phase 2+** (per-user token isolation, "Continue with Microsoft", enforced SAML): deferred

Design decisions and the supporting research are in [`scratchpad/`](scratchpad/) —
start with [`scratchpad/00-decision-log.md`](scratchpad/00-decision-log.md).

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- AWS account with **Bedrock access to Claude Sonnet 4.6** (the `us.` cross-region
  inference profile; us-east-1 source region)
- An AWS SSO profile (the examples use `mainadmin`)
- An Atlassian Cloud site with Rovo enabled (see the Phase-0 results for the validated setup)

## Setup

```bash
# 1. Install deps (incl. dev tools)
uv sync --extra dev          #  or:  just sync

# 2. Configure env
cp .env.example .env         # then edit if needed

# 3. Authenticate to AWS Bedrock
aws sso login --profile mainadmin

# 4. Run the dev web UI
just web                     #  or:  uv run adk web
```

Open <http://localhost:8000>, pick **`atlassian_mcp_agent`**, and ask *"What's on my plate?"*.

### Three ways to run the agent

| Command | What you get |
|---|---|
| `just web`  / `uv run adk web` | Dev chat UI at http://localhost:8000 |
| `just run`  / `uv run adk run atlassian_mcp_agent` | Interactive terminal chat |
| `just api`  / `uv run adk api_server` | REST API server (`POST /run`, `/run_sse`) |

### Without `just` (Windows)

`just` is optional. The raw equivalents:

```powershell
uv sync --extra dev
uv run adk web                       # or: adk run atlassian_mcp_agent | adk api_server
uv run pytest
uv run ruff check .
uv run basedpyright
```

## First-run OAuth flow

The first prompt triggers a **two-screen** consent flow against Atlassian — this is
expected, not a bug:

1. **Consent #1** at `mcp.atlassian.com` (MCP client → MCP server): pick the **products**
   to grant (Jira, Confluence). Approve.
2. **Consent #2** at `api.atlassian.com` (MCP server → Atlassian): review the **scopes** the
   MCP server requests. Accept.
3. The browser redirects to `http://127.0.0.1:3030/callback` and the agent continues.

Tokens cache in `~/.atlassian-mcp/` for later runs. **Click fast** — the second consent has
a short JWT TTL (a few minutes). The callback port is configurable via
`ATLASSIAN_OAUTH_CALLBACK_PORT`.

## Phase 1 — Definition of Done

- [ ] `uv sync` + `just web` boots `adk web` with no errors
- [ ] First prompt opens the browser → OAuth completes → agent answers using the validated tools
- [ ] `~/.atlassian-mcp/{client.json, token.json}` persist (0600 on POSIX) after first run
- [ ] A second prompt after a process restart (within the ~1h token TTL) returns Jira issues
      with **no** browser popup
- [ ] Granted scopes are logged on first persist (compare against the Phase-0 ground truth)
- [ ] `docs/phase-1-results.md` captures time-to-first-token, observed TTL, granted scopes,
      the exact consent-screen text, and any deviations

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on every tool call | Access token expired (~1h, no refresh token) | Delete `~/.atlassian-mcp/token.json`, re-run |
| "MCP server not found" / auth loops | DCR client_id mismatch | Delete `~/.atlassian-mcp/client.json`, re-run |
| Browser opens to "Something went wrong" | Second consent's JWT expired | Restart the connection, click through faster |
| Agent can't see a page you just created | Rovo indexing lag (minutes–hours) | Ask via CQL (`searchConfluenceUsingCql`) |
| `... on-demand throughput isn't supported` | Used a bare model id instead of an inference profile | Keep `BEDROCK_MODEL_ID=bedrock/us.anthropic.claude-sonnet-4-6` |
| `bedrock:InvokeModel` AccessDenied | Sonnet 4.6 not enabled in your AWS account | Enable it in the Bedrock console |
| `ExpiredTokenException` from Bedrock | AWS SSO session expired | `aws sso login --profile mainadmin` |

## Project layout

```
.
├── pyproject.toml                  # uv-managed deps (google-adk[litellm]>=2, mcp>=1.24,<2, …)
├── .env.example                    # env template
├── justfile                        # run/web/api/test/lint/typecheck
├── atlassian_mcp_agent/            # the ADK agent package (adk auto-discovers root_agent)
│   ├── __init__.py                 # from . import agent
│   ├── agent.py                    # OAuth + MCP toolset + LiteLlm + root_agent
│   └── SKILL.md                    # operational guide injected into the instruction
├── tests/
│   └── test_token_storage.py       # FileTokenStorage JSON round-trip
├── docs/
│   ├── VISION.md                   # architecture & decisions (corrected for Phase 1)
│   └── phase-1-results.md          # first-run measurements
├── scratchpad/                     # research + decision log (00–07)
└── reference_data/                 # original reference zip (read-only)
```
