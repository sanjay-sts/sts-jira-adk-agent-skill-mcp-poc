# VISION: Atlassian Rovo MCP × Google ADK — Single-User → Federated Agent

> Architecture & decisions for the Phase 1 build. The **Locked Decisions** and
> **Non-Negotiables** are constraints; everything else is open for design.
> Resolved build choices are tracked in [`../scratchpad/00-decision-log.md`](../scratchpad/00-decision-log.md).
>
> **This is the corrected Phase-1 vision.** It supersedes the original in
> `reference_data/adk-atlassian-mcp/VISION.md` on two points: the model
> (Bedrock Claude Sonnet 4.6, not gemini-2.5-flash) and the endpoint (`/v1/mcp`).

---

## 1. Mission

Build an experimental Google ADK agent that calls the **official Atlassian Rovo Remote
MCP Server** to fetch Jira (and Confluence) data on behalf of an authenticated user, using
**OAuth 2.1 (Authorization Code + PKCE + Dynamic Client Registration)** so each end user
authenticates with their own identity and only sees data they have permission for.

This validates three patterns for wider enterprise work:
1. **Per-user OAuth delegation inside agents** — no service-account shortcuts.
2. **MCP DCR (RFC 7591)** as the canonical client-onboarding pattern for third-party MCP servers.
3. **ADK as a node in a wider agentic system** — a viable OAuth-aware specialist for a
   LangGraph-orchestrated federated research agent.

---

## 2. Phased plan

### Phase 1 — Single-user smoke test ✅ BUILDING NOW
Prove the OAuth + DCR + PKCE handoff works end-to-end with ADK and returns real Jira (and
Confluence) data to the chat UI.
- One user, one Atlassian account (registered with an `*.onmicrosoft.com` M365 email — Rovo
  blocks generic email domains).
- File-based token storage in `~/.atlassian-mcp/`.
- **Exit criterion:** a second prompt after restart completes WITHOUT opening a browser
  (token persistence works).

### Phase 2 — Per-user token isolation
Keyed token store (dir per `user_id`, derived from `tool_context.state["user_id"]` set by
the trusted calling app). No cross-user token leakage.

### Phase 3 — "Continue with Microsoft" federation
Users authenticate to Atlassian via Entra ID. No agent code change — pure user-flow validation.

### Phase 4 — (Optional) Enforced SSO via Atlassian Guard
Production-style SAML + SCIM. Deferred ($4/user/mo; only matters for enterprise rollout).

---

## 3. Locked Decisions (corrected for Phase 1)

| Decision | Choice | Rationale |
|---|---|---|
| MCP server | Official Atlassian Rovo, endpoint **`https://mcp.atlassian.com/v1/mcp`** | First-party; `/v1/mcp` is the recommended Streamable HTTP path per the atlassian-mcp-server repo and the OAuth/API-token docs. `/v1/sse` is unsupported after 2026-06-30. (`/authv2` is not in current official docs.) |
| Transport | Streamable HTTP | SSE deprecated June 2026. |
| OAuth flow | Authorization Code + PKCE (S256) + RFC 7591 DCR | Mandatory — Atlassian rejects tokens from static OAuth apps at tool-call time. |
| OAuth client | MCP Python SDK's `OAuthClientProvider` (an `httpx.Auth`) | Implements DCR + PKCE + storage abstraction. |
| ADK integration | Inject `OAuthClientProvider` into `StreamableHTTPConnectionParams.httpx_client_factory` | ADK has no native DCR/PKCE for MCP (google/adk-python#3005; shipped v1.22.0). |
| ADK version | `google-adk[litellm] >= 2.0` (GA 2026-05-19) | Pins `mcp>=1.24,<2`, `litellm>=1.83.7,<=1.83.14`. |
| MCP SDK pin | `mcp>=1.24,<2` | The `<2` cap protects the `httpx_client_factory` plumbing (mcp 2.x changes the streamable-http client API). |
| **Model** | **`bedrock/us.anthropic.claude-sonnet-4-6`** (Claude Sonnet 4.6 on AWS Bedrock via LiteLLM) | Cross-region inference profile is mandatory: us-east-1 has no in-region inference and the bare model id errors on on-demand throughput. `us.` keeps routing within US/Canada. |
| Token storage (Phase 1) | File-based JSON in `~/.atlassian-mcp/` | Simplest thing that persists across restarts. |
| Auth method | **OAuth 2.1 (3LO) + DCR + PKCE** for Phase 1 | Per-user delegation is the experiment's point. API-token (headless) is the future/JSM path (see §6). |
| Scope strategy | **Server-controlled, not client-controlled.** The MCP server requests a hardcoded scope set against Atlassian's identity layer; the DCR `scope` field is ignored. Users consent per-product (Jira/Confluence/Compass/JSM). | We can only consume what the MCP server requests. Adding a scope requires an upstream change (atlassian/atlassian-mcp-server#159). |
| Tool scope (Phase 1) | 8 read-only tools (Jira + Confluence read) | The Phase-0-validated set. |
| Identity provider | M365 Developer Program tenant (Entra ID); `*.onmicrosoft.com` domain | Bypasses Rovo's generic-email block. |

---

## 4. Architecture (Phase 1)

```
Browser ──(1) ADK Web UI localhost:8000 ──(3) Atlassian consent ──(4) callback 127.0.0.1:3030
   │
   ▼ Local Python process
ADK Agent (Bedrock Claude Sonnet 4.6 via LiteLlm)
   │ tool calls
   ▼
McpToolset (Streamable HTTP)  ──httpx_client_factory──►  OAuthClientProvider (DCR+PKCE)
   │                                                          │
   │                                                          ▼ FileTokenStorage ~/.atlassian-mcp/
   ▼ HTTPS                                                      {client.json, token.json}
Atlassian Rovo MCP  (mcp.atlassian.com/v1/mcp)
   ▼
Jira / Confluence  (permissions enforced per user's OAuth token)
```

The MCP server holds the authoritative scope list and requests it during the OAuth dance;
the consent screen is built from that server-side list. Log the granted `OAuthToken.scope`
after first run to see ground truth.

---

## 5. Non-Negotiables (hard-won)

1. **No static OAuth app at developer.atlassian.com.** Those tokens are rejected by the MCP server at tool-call time. DCR is mandatory.
2. **Don't control scopes from the client.** The DCR `scope` field is ignored; the server's hardcoded set is what shows on consent. Users pick **products**, not individual scopes.
3. **Don't depend on `/.well-known/oauth-protected-resource`.** Atlassian returns 404 (RFC 9728 not implemented); the SDK falls back to RFC 8414 (`/.well-known/oauth-authorization-server`). Keep that fallback.
4. **Don't hardcode the cloudId.** Call `getAccessibleAtlassianResources` when needed and pass `cloudId` explicitly.
5. **Assume access-token TTL ~1 hour.** No refresh token is granted; on 401, re-auth — don't loop-retry refresh.
6. **Set generous timeouts (≥300s) on the first request** — it includes a human-in-the-loop browser dance.
7. **Never log tokens.** Redact `Authorization`; the granted `scope` field IS safe to log.
8. **The first user to consent must have access to every product the MCP server requests scopes for.**
9. **Use an `*.onmicrosoft.com` (or verified business) domain** for the Atlassian org email.
10. **Treat MCP tool schemas as aspirational.** `search`/`fetch` claim `cloudId` is optional but the runtime requires it. Always pass `cloudId`.
11. **Assume Rovo Search indexing lag.** Fall back to JQL/CQL for content created in the last hour.
12. **Log the granted `OAuthToken.scope` on first persist.** Ground-truth Phase-0 scopes: Jira `read:jira-work`, `write:jira-work`; Confluence `read:comment:confluence`, `read:confluence-user`, `read:page:confluence`, `read:space:confluence`, `search:confluence`, `write:comment:confluence`, `write:page:confluence`. **No `offline_access`** → no refresh token.

---

## 6. Out of scope (for now)

- **Write operations** (`editJiraIssue`, `addCommentToJiraIssue`, …) — read-only until the auth path is rock-solid.
- **JSM tools** — reachable only via **API-token auth** (a separate, non-delegated connection: personal token via `Authorization: Basic base64(email:token)` or service-account key via `Bearer`). That path is **not** bound to a cloudId, isn't restricted by domain allowlists, and exposes a reduced tool set. It's the documented route for headless/service and JSM scenarios — revisit post-Phase-1.
- **Multi-user** (Phase 2), **Entra federation** (Phase 3), **Guard/SAML/SCIM** (Phase 4).
- **Production deployment**, **LangGraph orchestration**, **per-user rate limiting**.

---

## 7. References

- Atlassian Rovo MCP docs — https://support.atlassian.com/atlassian-rovo-mcp-server/
- Configuring OAuth 2.1 — https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-oauth-2-1/
- Configuring auth via API token — https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/
- Atlassian MCP repo — https://github.com/atlassian/atlassian-mcp-server (scope issue #159)
- Google ADK Python — https://github.com/google/adk-python (`httpx_client_factory` issue #3005)
- MCP Python SDK auth — `mcp.client.auth.OAuthClientProvider`
- Bedrock Claude Sonnet 4.6 model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html

---

## 8. Definition of Done — Phase 1

See `README.md` → "Phase 1 — Definition of Done" and capture the run evidence in
`docs/phase-1-results.md`.
