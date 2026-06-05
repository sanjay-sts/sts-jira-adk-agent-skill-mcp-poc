# 06 — Research: Atlassian Rovo MCP endpoint & authentication methods

**Date:** 2026-05-27
**Trigger:** Architect asked to reconcile the endpoint/auth question against
`github.com/atlassian/atlassian-mcp-server` and the API-token auth doc.
**Method:** Atlassian MCP server GitHub repo + Atlassian support docs (OAuth 2.1 + API token).

---

## Endpoint — RESOLVED → `https://mcp.atlassian.com/v1/mcp`

| Source | Says |
|---|---|
| `atlassian/atlassian-mcp-server` (GitHub README) | Recommended: **`/v1/mcp`**. "While `/sse` … is supported, we recommend updating … to point to `/mcp`." |
| Atlassian "Configuring OAuth 2.1" doc | Recommended: **`/v1/mcp`** |
| Atlassian "Authentication via API token" doc | Uses **`/v1/mcp`**; `/v1/sse` unsupported after **2026-06-30** |

- **`/v1/mcp/authv2` does NOT appear** in any of the authoritative current sources. It came
  from a secondary blog in an earlier search. **Drop it.** The reference repo's use of
  `/authv2` is treated as unverified/superseded.
- **Decision:** Phase 1 targets `https://mcp.atlassian.com/v1/mcp` (this is also exactly what
  Phase 0 validated). `ATLASSIAN_MCP_BASE = "https://mcp.atlassian.com"` for `.well-known`
  discovery. → Supersedes the reference's `/authv2`.

## Transport
Streamable HTTP via `/v1/mcp`. The old HTTP+SSE (`/v1/sse`) is deprecated and **unsupported
after 2026-06-30** — matches VISION's "SSE deprecated June 2026."

---

## Authentication — two documented methods

### Method A — OAuth 2.1 (3LO) + DCR + PKCE  ← **Phase 1 uses this**
- Interactive browser consent; per-user delegation.
- Token is **bound to a cloudId**; **respects the site's domain allowlist**.
- Full tool surface available (subject to per-product consent: Jira/Confluence/Compass/JSM).
- This is the experiment's whole point (VISION §1: "each end user authenticates with their
  own identity"). **No change to the locked OAuth decision.**

### Method B — API token (headless)  ← future / JSM / service scenarios only
- **An org admin must first enable** API-token auth for the Rovo MCP Server ("Rovo MCP
  scoped API token").
- Header formats:
  - Personal API token → `Authorization: Basic <base64(email:api_token)>`
  - Service-account API key → `Authorization: Bearer <api_key>`
- Trade-offs (why it's NOT Phase 1):
  - **Not bound to a cloudId** → must pass `cloudId` explicitly on every call.
  - **Not restricted by domain allowlists.**
  - **Some MCP tools are unavailable** (scopes not reachable via personal tokens / service keys).
  - **No per-user delegation** — it's a single credential, defeating the experiment's purpose.
- **Notable upside:** API-token auth now covers **additional apps incl. JSM**. This updates
  VISION's rationale: JSM is reachable, but only via this headless path (a *second* MCP
  connection / different auth), so it remains out of Phase 1 scope.

---

## Implications for the build
1. `ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"` (drop `/authv2`).
2. Keep OAuth 2.1 + DCR + PKCE (Method A) for Phase 1 — unchanged.
3. Document Method B (API token) as the **future path for headless/service-account use and
   for JSM**, in `docs/` and the VISION "out of scope" section — with the cloudId / allowlist /
   tool-availability caveats above.

## Sources
- atlassian/atlassian-mcp-server — https://github.com/atlassian/atlassian-mcp-server
- Configuring OAuth 2.1 — https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-oauth-2-1/
- Configuring authentication via API token — https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/
