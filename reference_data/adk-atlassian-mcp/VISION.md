# VISION: Atlassian Rovo MCP × Google ADK — Single-User → Federated Agent

> Hand-off document for Claude Code. Read this first, then `agent.py`. Treat the
> **Locked Decisions** and **Non-Negotiables** sections as constraints; everything
> else is open for design.

---

## 1. Mission

Build an experimental Google ADK agent that calls the **official Atlassian Rovo
Remote MCP Server** to fetch Jira (and later Confluence) data on behalf of an
authenticated user. The agent must use **OAuth 2.1 (Authorization Code + PKCE +
Dynamic Client Registration)** so each end user authenticates with their own
identity and only sees data they have Jira permissions for.

This experiment validates three patterns I want to bring back to enterprise work:

1. **Per-user OAuth delegation inside agents** — no service-account shortcuts.
2. **MCP DCR (RFC 7591)** as the canonical client-onboarding pattern for
   third-party MCP servers.
3. **ADK as a node in a wider agentic system** — proving it can host an
   OAuth-aware remote MCP toolset cleanly, so it's a viable specialist in a
   LangGraph-orchestrated federated research agent.

---

## 2. Phased Plan

Build in four phases. Don't skip ahead — each phase has a hard exit criterion.

### Phase 1 — Single-user smoke test ✅ STARTING HERE
**Goal:** Prove the OAuth + DCR + PKCE handoff works end-to-end with ADK and
return real Jira issues to the chat UI.

- **Identity:** one user, one Atlassian account, registered with an
  `@<tenant>.onmicrosoft.com` M365 email (NOT gmail/outlook — Rovo blocks
  generic email domains).
- **Atlassian:** Free plan, one project, 3–5 sample issues.
- **Agent:** the existing `agent.py` in this folder.
- **Token storage:** file-based, single directory `~/.atlassian-mcp/`.

**Acceptance:**
- `adk web` launches, browser OAuth flow completes on first prompt.
- `getAccessibleAtlassianResources` returns a non-empty cloudId list.
- `searchJiraIssuesUsingJql` with `assignee = currentUser()` returns the
  seeded issues.
- Re-running the agent within the access-token TTL (~1h) uses cached token
  silently (no browser).

**Exit criterion:** A second prompt after restart completes WITHOUT opening
a browser. If a browser opens every time, token persistence is broken — fix
before Phase 2.

### Phase 2 — Per-user token isolation
**Goal:** Support multiple users in the same ADK process without cross-user
token leakage.

- Replace single-directory `FileTokenStorage` with a keyed store: directory
  per `user_id`, or SQLite/Redis-backed.
- Derive `user_id` from `tool_context.state["user_id"]` (set by the calling
  application, not user input — must be trusted).
- Each user gets their own DCR registration (or share one client_info if
  Atlassian permits — verify experimentally and document).

**Acceptance:**
- Two simulated users in the same process each complete their own OAuth
  flow.
- Deleting user A's token directory does not affect user B.
- Token files are 0600 permissions on Unix.

### Phase 3 — "Continue with Microsoft" federation
**Goal:** Let users authenticate to Atlassian using their Entra ID credentials
without any paid SSO infrastructure.

- No code change in the agent — this is purely a user-flow validation.
- During the OAuth dance, user clicks "Continue with Microsoft" on
  `id.atlassian.com` instead of typing an Atlassian password.
- Document the user experience and any consent screens.

**Acceptance:**
- A net-new M365 user (never logged into Atlassian before) can complete the
  flow end-to-end with only their Entra credentials.
- Atlassian auto-provisions the matching Atlassian account.
- Agent returns Jira issues scoped to that user's permissions.

### Phase 4 — (Optional) Enforced SSO via Atlassian Guard
**Goal:** Production-style federation with enforced SAML + SCIM.

- Verify M365 tenant domain in Atlassian.
- 30-day free trial of Atlassian Guard Standard.
- Install Atlassian Cloud gallery app in Entra; wire SAML + SCIM.
- Lock authentication policy to Entra IdP only.

**Acceptance:**
- Users cannot log in to Atlassian with a local password — only via Entra.
- SCIM deprovision in Entra removes user from Atlassian within one
  provisioning cycle.
- ADK agent still works unchanged (the federation is invisible to the agent).

**Note:** Defer this phase until Phase 3 has been demoed. The $4/user/mo cost
isn't worth it for the experiment proper.

---

## 3. Locked Decisions (Do Not Re-Litigate)

| Decision | Choice | Rationale |
|---|---|---|
| MCP server | Official Atlassian Rovo. New clients should use `https://mcp.atlassian.com/v1/mcp/authv2` (Atlassian's current recommendation); `https://mcp.atlassian.com/v1/mcp` still works. | First-party, no proxy hops, includes 46+ Jira / Confluence / Compass / JSM tools. |
| Transport | Streamable HTTP | SSE deprecated June 2026. |
| OAuth flow | Authorization Code + PKCE (S256) + RFC 7591 DCR | Mandatory — Atlassian rejects tokens from static OAuth apps at tool-call time. |
| OAuth client | MCP Python SDK's `OAuthClientProvider` (an `httpx.Auth`) | Already implements DCR + PKCE + refresh + storage abstraction. |
| ADK integration | Inject `OAuthClientProvider` into `StreamableHTTPConnectionParams.httpx_client_factory` | ADK has no native DCR/PKCE for MCP yet. This is the supported bridge (google/adk-python issue #3005). |
| ADK version | `google-adk >= 2.0` | `httpx_client_factory` field was added in 2.0. |
| Model | `gemini-2.5-flash` | Cheap, fast, sufficient for tool-calling agent. |
| Token storage Phase 1 | File-based JSON in `~/.atlassian-mcp/` | Simplest thing that persists across restarts. |
| Scope strategy | **Server-controlled, not client-controlled.** The MCP server has a hardcoded scope set it requests against Atlassian's identity layer (currently `read:jira-work` + `write:jira-work` + Confluence scopes, per atlassian/atlassian-mcp-server#159). The `scope` field in client DCR registration is functionally ignored. Users consent per-product on Atlassian's screen (Jira / Confluence / Compass / JSM) — not per individual scope. | The MCP server is itself an OAuth client downstream; we can only consume what it requests. To add a scope (e.g. `write:sprint:jira-software`) requires an upstream change in the Atlassian-hosted MCP server. |
| JSM | **Out of scope** for now | Atlassian's JSM MCP tools currently require Basic auth (API token), not OAuth. Adding them means two MCP connections. Defer. |
| Identity provider | M365 Developer Program tenant (Entra ID) | Already provisioned; gives 25 E5 seats; `*.onmicrosoft.com` domain bypasses Rovo's generic-email block. |

---

## 4. Architecture (Phase 1)

```
┌────────────────────────────────────────────────────────────────────┐
│  Browser  ──────────────────────────────────────────────────────►  │
│   ▲          (1) ADK Web UI (http://localhost:8000)                │
│   │          (3) Atlassian OAuth consent screen                    │
│   │          (4) HTTP callback to 127.0.0.1:3030                   │
└───┼────────────────────────────────────────────────────────────────┘
    │
┌───┼────────────────────────────────────────────────────────────────┐
│   │                       Local Python process                     │
│   │                                                                │
│   │   ┌──────────────┐    tool calls    ┌─────────────────────┐    │
│   └──►│  ADK Agent   │ ───────────────► │   McpToolset        │    │
│       │ (Gemini 2.5) │ ◄─────────────── │  Streamable HTTP    │    │
│       └──────────────┘    JSON results  └────────┬────────────┘    │
│                                                  │ httpx_client    │
│                                                  │ _factory        │
│                                                  ▼                 │
│                                         ┌─────────────────────┐    │
│                                         │ OAuthClientProvider │    │
│                                         │ (DCR + PKCE +       │    │
│                                         │  refresh, from MCP  │    │
│                                         │  Python SDK)        │    │
│                                         └────────┬────────────┘    │
│                                                  │                 │
│                                                  ▼                 │
│                                         ┌─────────────────────┐    │
│                                         │ FileTokenStorage    │    │
│                                         │ ~/.atlassian-mcp/   │    │
│                                         │   client.json       │    │
│                                         │   token.json        │    │
│                                         └─────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼ HTTPS
                                  ┌─────────────────────────────────┐
                                  │ Atlassian Rovo MCP              │
                                  │   mcp.atlassian.com/v1/mcp      │
                                  │   cf.mcp.atlassian.com/v1/      │
                                  │     {register,token}            │
                                  └────────────────┬────────────────┘
                                                   │
                                                   ▼
                                  ┌─────────────────────────────────┐
                                  │ Atlassian Jira / Confluence     │
                                  │  (permissions enforced per      │
                                  │   user's OAuth token)           │
                                  └─────────────────────────────────┘
```

**Where scope assignment actually happens:** the Atlassian Rovo MCP box (bottom
right of the diagram) holds the authoritative scope list. The MCP server requests
its own hardcoded set of scopes against Atlassian's identity layer during the
OAuth dance. The consent screen the user sees is built from that server-side list.
The `scope` field in your DCR client metadata is silently ignored — passing
`read:jira-work read:jira-user offline_access` from the client does not narrow,
widen, or otherwise change what the MCP server asks for. Log the `scope` field
of the returned `OAuthToken` after first run to see ground truth.

---

## 5. Non-Negotiables (Hard-Won)

1. **DO NOT register an OAuth app at `developer.atlassian.com`.** Tokens minted
   from such an app pass Jira REST calls but get rejected by the MCP server at
   tool-call time. The MCP server has its own auth pipeline. DCR is mandatory.

2. **DO NOT try to control scopes from the client.** The `scope` field in your
   DCR `OAuthClientMetadata` is functionally ignored. The MCP server has its
   own hardcoded scope set (currently `read:jira-work`, `write:jira-work`, plus
   Confluence scopes per atlassian/atlassian-mcp-server#159) and that's what
   shows on the consent screen. What the **user** picks at consent is which
   **Atlassian products** to grant (Jira / Confluence / Compass / JSM) — not
   individual scopes. The MCP server then dynamically loads the tools
   compatible with what was granted. To add a scope the MCP server doesn't
   request, you file an upstream issue.

3. **DO NOT depend on `/.well-known/oauth-protected-resource`.** Atlassian
   returns 404 (RFC 9728 not implemented). The MCP SDK falls back to RFC 8414
   (`/.well-known/oauth-authorization-server`) automatically — keep it that way.

4. **DO NOT hardcode the cloudId.** Always call
   `getAccessibleAtlassianResources` first, and pass `cloudId` explicitly to
   subsequent tool calls. A user may have access to multiple Atlassian sites.

5. **DO assume access-token TTL is ~1 hour.** Refresh works most of the time,
   but Atlassian's refresh endpoint is known to flake (intermittent 5xx). On
   refresh failure, fall back to re-running the interactive auth flow — don't
   crash the agent.

6. **DO set generous timeouts** (≥ 300 seconds) on the first MCP request.
   First-run includes a human-in-the-loop browser auth dance.

7. **DO NOT log tokens.** `OAuthToken` instances must never appear in logs or
   error messages. Configure httpx logging to redact `Authorization` headers.
   The granted `scope` field on the token IS safe to log and useful for debug.

8. **DO assume the first user to consent must have access to every product the
   MCP server requests scopes for.** If the MCP server requests Jira *and*
   Confluence scopes server-side, a Jira-only user may fail the consent. Verify
   by inspecting the consent screen on first run.

9. **DO use an `*.onmicrosoft.com` (or otherwise verified business) domain for
   the Atlassian org email.** Gmail/Outlook/Yahoo email domains may block Rovo
   from enabling on the site.

10. **DO treat MCP tool schemas as aspirational, not authoritative.** Verified
    in Phase 0: the `search` and `fetch` tools' schemas claim `cloudId` is
    "not needed — derived from token", but the runtime rejects calls without
    it. Always pass `cloudId`. Validate every claimed-optional parameter
    empirically before trusting it.

11. **DO assume Rovo Search has indexing lag.** Verified in Phase 0: a
    Confluence page <1 hour old was missed by `search` but found instantly
    via `searchConfluenceUsingCql`. The agent must fall back to JQL/CQL when
    the user references recent content. This is the single most likely
    source of "I just created X and the agent can't find it" complaints.

12. **DO log the granted `OAuthToken.scope` on first persist.** Verified in
    Phase 0 via `getAccessibleAtlassianResources`: the actual granted scopes
    were `read:jira-work`, `write:jira-work` (Jira) and
    `read:comment:confluence`, `read:confluence-user`, `read:page:confluence`,
    `read:space:confluence`, `search:confluence`, `write:comment:confluence`,
    `write:page:confluence` (Confluence). **No `offline_access` scope was
    granted, meaning no refresh token.** On 401 from token expiry, fully
    re-auth; do not attempt refresh.

---

## 6. Out of Scope (For Now)

- Confluence tools (add in Phase 2+ by expanding `tool_filter`. You cannot
  request Confluence scopes from the client — the MCP server already requests
  them server-side, so granting Confluence at consent time is all that's needed).
- JSM tools (require API-token auth; revisit after Phase 4).
- Write operations (`editJiraIssue`, `addCommentToJiraIssue`) — read-only until
  the auth path is rock-solid.
- Production deployment (containerization, secrets management, observability).
  Run locally on dev machine only.
- LangGraph orchestration. This agent is a leaf node; integration into a
  LangGraph supervisor is a separate piece of work.
- Cost-center attribution / per-user rate limiting.

---

## 7. Repo Structure (Target)

```
adk-atlassian/
├── VISION.md                 ← this file
├── README.md                 ← run instructions (Claude Code to write)
├── pyproject.toml            ← uv-managed; google-adk, mcp, httpx
├── .env.example              ← GOOGLE_API_KEY, optional ATLASSIAN_SITE_HINT
├── .gitignore                ← .venv/, ~/.atlassian-mcp/ guidance, .env
├── atlassian_mcp_test/
│   ├── __init__.py
│   ├── agent.py              ← current single-user agent (Phase 1)
│   ├── auth.py               ← OAuth provider + token storage (extract from agent.py in Phase 2)
│   └── tools.py              ← McpToolset construction + tool_filter (Phase 2)
├── tests/
│   ├── test_token_storage.py ← serialize/deserialize round-trip
│   └── test_oauth_flow.py    ← (Phase 2) mock the DCR + token exchange
└── docs/
    ├── phase-1-results.md    ← what worked, what didn't, screenshots of the OAuth flow
    └── gotchas.md            ← living doc of edge cases encountered
```

Use `uv` for package management (matches my Zed setup). Lock to Python 3.11+.

---

## 8. Implementation Notes for Claude Code

- **Don't refactor `agent.py` aggressively in Phase 1.** It works as a single
  file; splitting into modules belongs in Phase 2 when we add multi-user.
- **Use `basedpyright` strict mode** for type checking (matches my dev env).
- **Add a `make` or `just` runner** for the common commands (`adk web`, `pytest`,
  `ruff check`, `pyright`). I prefer `just`.
- **README must include:** Atlassian site setup (one user, one project, sample
  issues), env var setup, run command, expected first-run output, troubleshoot
  section linking to the gotchas in §5.
- **Tests:** Phase 1 doesn't need integration tests against the live MCP server.
  Unit tests for `FileTokenStorage` (round-trip OAuth model objects through JSON)
  are enough. Save end-to-end against live MCP for a manual smoke-test
  checklist in the README.
- **Logging:** structured (JSON) logs at DEBUG level for the OAuth dance,
  INFO for tool calls, with the `Authorization` header explicitly redacted.

---

## 9. References

- Atlassian Rovo MCP docs: https://support.atlassian.com/atlassian-rovo-mcp-server/
- Configuring OAuth 2.1 (canonical scope/consent reference): https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-oauth-2-1/
- Atlassian MCP repo: https://github.com/atlassian/atlassian-mcp-server
- MCP server controls scope set, not the client (open issue): https://github.com/atlassian/atlassian-mcp-server/issues/159
- Google ADK Python: https://github.com/google/adk-python
- ADK `httpx_client_factory` enabling PR / issue: google/adk-python#3005
- MCP Python SDK auth module: `mcp.client.auth.OAuthClientProvider`
- Why static OAuth apps fail (Ben Fuqua, Medium): https://benjamin-fuqua.medium.com/why-your-custom-oauth-tokens-fail-with-the-atlassian-mcp-server-and-how-dcr-fixes-it-2566b779f795

---

## 10. Definition of Done — Phase 1

- [ ] `uv sync && just run` boots `adk web` with no errors.
- [ ] First chat prompt opens browser → user logs in → consent → callback → agent answers.
- [ ] `~/.atlassian-mcp/{client.json, token.json}` exist with 0600 perms after first run.
- [ ] Second prompt after Python process restart (within token TTL) returns Jira issues with NO browser popup.
- [ ] README documents the full happy path + 3 most common failure modes.
- [ ] On first run, log and record the **granted scopes from the token
      response** (`OAuthToken.scope`). This is the ground truth for what the MCP
      server actually requested and what the user consented to. Compare against
      what §3 / §5 documents — surface any drift in `phase-1-results.md`.
- [ ] `docs/phase-1-results.md` captures: time-to-first-token, observed token
      TTL, observed scope set from the token response, the exact consent-screen
      text shown to the user, and any deviations from this vision doc.
