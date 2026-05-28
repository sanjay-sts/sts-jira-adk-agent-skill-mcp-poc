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

---

## Understanding the auth: OAuth 2.1, PKCE, and DCR

This section explains *why* the agent is wired the way it is — useful background if you want
to extend it or apply the same pattern to other MCP servers.

### OAuth 2.1 — the foundation

OAuth 2.1 is not a new protocol. It is OAuth 2.0 with the unsafe parts removed and the best
practices made mandatory:

- **Implicit flow** — removed (tokens in redirect URLs are dangerous)
- **Password grant** — removed (agents must never hold user passwords)
- **PKCE** — mandatory for all authorization code flows (see below)
- **Redirect URIs** — must match exactly, no wildcards

The core question OAuth answers: *"How does an app act on behalf of a user without ever knowing
their password?"* The answer is a time-limited access token issued only after the user explicitly
consents.

---

### PKCE — Proof Key for Code Exchange (RFC 7636)

**The problem.** On desktop/mobile apps, multiple processes can register the same local callback
URL. A malicious process could intercept the authorization code that the auth server sends back
after the user logs in and redeem it for a token before the legitimate agent does.

**The fix.** Bind the authorization code to the agent instance that requested it, using a
one-time secret that is never transmitted directly.

```
Step 1 — Agent generates a random secret (never sent over the wire):
         code_verifier  = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

Step 2 — Agent hashes it (SHA-256) and sends ONLY the hash to the auth server:
         code_challenge = BASE64URL(SHA256(code_verifier))
                        = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

Step 3 — User logs in and consents. Auth server stores the challenge, returns a code.

Step 4 — Agent sends the VERIFIER (original secret) + code to the token endpoint.
         Auth server recomputes SHA256(verifier) and checks it matches.
         A thief who intercepted the code has no verifier → cannot redeem the code.
```

In our run you can see the challenge in the authorization URL:
```
...&code_challenge=i-tfkHjhahkkabfewRor6WxU5fCvz7qoKpb1vQoiNC4&code_challenge_method=S256
```

**PKCE = the authorization code is cryptographically bound to the agent instance that generated
it.** No client secret required — safe for apps that cannot keep secrets (mobile, desktop, CLI).

---

### DCR — Dynamic Client Registration (RFC 7591)

**The problem.** Traditional OAuth requires you to pre-register your app at a developer portal
(e.g. Atlassian Developer Console), receive a static `client_id` + `client_secret`, and embed
them in your code. That works for a single-deployment web app. It breaks for AI agents:

- Each user runs their own agent instance — you cannot pre-register thousands of them.
- For Atlassian MCP specifically: statically registered clients are **rejected at tool-call
  time**. DCR is the only supported path.

**How it works.** The agent registers itself programmatically at first boot:

```
POST https://mcp.atlassian.com/oauth/register
{
  "client_name": "atlassian-mcp-agent",
  "redirect_uris": ["http://127.0.0.1:3030/callback"],
  "grant_types": ["authorization_code"],
  "token_endpoint_auth_method": "none"    ← public client, no secret needed
}

← Response:
{
  "client_id": "AMi-ldd7VfjPuu8i"         ← unique per deployment
}
```

The `client_id` is saved to `~/.atlassian-mcp/client.json` and reused on every subsequent run.
The registration step is skipped from then on.

You can see the DCR-issued `client_id` in the live authorization URL:
```
https://mcp.atlassian.com/v1/authorize?...&client_id=AMi-ldd7VfjPuu8i&...
```

**DCR = the agent registers itself at runtime. No developer portal, no hardcoded secrets.**

---

### Is this the future for AI agents?

**Yes — for MCP-connected agents it is already the standard today.**

The [MCP specification (2025-11-25)](https://spec.modelcontextprotocol.io/specification/2025-11-25/basic/authentication/)
mandates OAuth 2.1 + PKCE + DCR as the required auth layer for remote MCP servers. The reasons
align exactly with what makes agents different from traditional web apps:

| Problem | Solution |
|---|---|
| User must grant permission, not the developer | OAuth user consent flow |
| Agent is a public client (no safe secret storage) | PKCE replaces `client_secret` |
| Agent cannot be pre-registered at every MCP server | DCR — self-registers at runtime |
| Tokens must be short-lived and scoped | OAuth access tokens (~1 h, minimal scopes) |

---

### M2M — Client Credentials for autonomous multi-agent orchestration

The user-delegated flow above requires a human to open a browser and consent. For fully
autonomous agents — an orchestrator calling sub-agents with no human in the loop — the pattern
shifts to **Client Credentials**:

```
User-delegated (what we built):
  User → [browser consent] → Agent gets token scoped to that user

Client Credentials (M2M / service account):
  Orchestrator → [no browser, direct token request] → Sub-agent gets service token
```

For multi-agent *chains* (orchestrator → sub-agent → tool), two additional RFCs are relevant:

- **Token Exchange (RFC 8693):** an orchestrator exchanges its user-delegated token for a
  narrower-scoped token for a sub-agent. The full delegation chain is auditable.
- **JWT Bearer Grant (RFC 7523):** an agent signs a JWT with its own private key and presents
  it directly to the token endpoint — no browser redirect, no user interaction.

```
                    USER-ENABLED                    AUTONOMOUS M2M
                   (human consents)              (no human in loop)

Single agent:    OAuth 2.1 + PKCE + DCR      Client Credentials + DCR
                 ← what Phase 1 builds →

Multi-agent      Token Exchange               JWT Bearer / mTLS
  chain:         (RFC 8693)                   + Token Exchange for delegation
```

**What makes this a sound foundation for AI agents:**

1. **Tokens are short-lived** — a compromised token has a small blast radius (~1 h)
2. **Scopes are minimal** — agents can only do what the user explicitly granted
3. **No secrets in code** — DCR + PKCE means there is no `client_secret` to leak or rotate
4. **Audit trail** — every token issuance is logged at the auth server with the agent's identity
5. **Revocable** — the user can revoke the agent's access at any time from their Atlassian
   account settings

The fact that Atlassian issued a **refresh token** in the Phase 1 live run (contrary to earlier
documentation) signals that the ecosystem is already moving toward long-lived agent sessions —
acknowledging that forcing re-authentication every hour is impractical for production agents.
