# ADK Atlassian MCP Agent — Phase 1

Single-user ADK agent that calls the official Atlassian Rovo Remote MCP Server using OAuth 2.1 + DCR + PKCE. Model: Claude Sonnet 4.6 on AWS Bedrock via LiteLLM.

> ⚠️ **Read the docs in this order before doing anything:**
> 1. [`VISION.md`](VISION.md) — phases, locked decisions, non-negotiables
> 2. [`SKILL.md`](SKILL.md) — operational guide injected into the agent's instruction
> 3. [`docs/phase-0-results.md`](docs/phase-0-results.md) — validated ground truth from MCP Inspector testing
> 4. [`scratchpad/00-context.md`](scratchpad/00-context.md) — session context handoff

## Status

- **Phase 0 (MCP server validation):** ✅ Done — see `docs/phase-0-results.md`
- **Phase 1 (single-user ADK agent):** 🚧 In progress
- **Phase 2+ (per-user, "Continue with Microsoft", enforced SAML):** Deferred

## Prerequisites

- Python 3.11+
- `uv` for package management (or pip)
- AWS account with Bedrock access to Claude Sonnet 4.6 (us-east-1)
- Atlassian Cloud site with Rovo enabled — see `docs/phase-0-results.md` for the validated brokenai setup, or follow Atlassian's signup
- AWS SSO profile configured (`mainadmin` in the example, change as needed)

## Setup

```bash
# 1. Install deps
uv sync   # or: pip install -e .

# 2. Authenticate to AWS Bedrock
aws sso login --profile mainadmin
export AWS_PROFILE=mainadmin
export AWS_REGION=us-east-1

# 3. (Optional) Override the Bedrock model ID
export BEDROCK_MODEL_ID="bedrock/anthropic.claude-sonnet-4-6-20250929-v1:0"

# 4. Run the agent
adk web
```

Open <http://localhost:8000>, pick `atlassian_mcp_test`, ask *"What's on my plate?"*.

## First-run OAuth flow

The first prompt triggers a two-layer consent flow against Atlassian. You will see **two browser screens** — this is expected, not a bug. See `docs/phase-0-results.md` § "OAuth flow — observed behaviour".

1. **Consent #1** at `mcp.atlassian.com/v1/authorize` — MCP client → MCP server. Select products (Jira, Confluence). Click Approve.
2. **Consent #2** at `api.atlassian.com/oauth2/...` — MCP server → Atlassian. Review scopes. Click Accept.
3. Browser redirects to `http://127.0.0.1:3030/callback`, agent continues.

Tokens are cached in `~/.atlassian-mcp/` for subsequent runs. **Click fast** — the second consent has a short JWT TTL (~few minutes).

## Phase 1 Definition of Done

See `VISION.md` §10 for the full checklist. Headline items:

- [ ] `adk web` boots cleanly
- [ ] First prompt opens browser, OAuth completes, agent answers using validated tools
- [ ] `~/.atlassian-mcp/{client.json, token.json}` persist with 0600 perms
- [ ] Second prompt (within token TTL) uses cached token, no browser
- [ ] Granted scopes logged on first persist (verify against `docs/phase-0-results.md`)
- [ ] README + a `phase-1-results.md` with first-run measurements

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on every tool call | Token expired (~1h TTL, no refresh) | Delete `~/.atlassian-mcp/token.json`, re-run |
| "MCP server not found" | DCR client_id mismatch | Delete `~/.atlassian-mcp/client.json`, re-run |
| Browser opens to "Something went wrong" | Second consent's JWT expired | Restart connection, click through fast |
| Agent can't see a page you just created | Rovo indexing lag (~minutes to hours) | Use direct CQL via `searchConfluenceUsingCql` |
| `bedrock:InvokeModel` AccessDenied | Bedrock model not enabled in your AWS account | Enable Claude Sonnet 4.6 in Bedrock console |
| `ExpiredTokenException` from Bedrock | AWS SSO session expired | `aws sso login --profile mainadmin` |

## Project layout

```
adk-atlassian-mcp/
├── README.md                   ← you are here
├── VISION.md                   ← architecture and decisions
├── SKILL.md                    ← agent operational guide
├── pyproject.toml              ← uv-managed dependencies
├── .env.example                ← env var template
├── .gitignore
├── atlassian_mcp_test/         ← the agent package (importable by `adk web`)
│   ├── __init__.py
│   └── agent.py
├── docs/
│   └── phase-0-results.md      ← validation evidence
└── scratchpad/                 ← Claude Code research workspace
    └── 00-context.md
```
