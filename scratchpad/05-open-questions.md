# 05 — Open Questions (resolve before / during implementation)

**Date:** 2026-05-27
Genuine unknowns after research. Don't guess these — each has a concrete way to resolve it.
Tagged by who/what unblocks them.

---

## Q1 — Geo `us.` vs Global `global.` inference profile? 🧑 architect
`bedrock/us.anthropic.claude-sonnet-4-6` (US/Canada routing) vs
`bedrock/global.anthropic.claude-sonnet-4-6` (worldwide). Data-residency vs. availability.
**Default if no answer:** `us.` (keeps routing within US/CA — safer for a Nutrien context).

## Q2 — MCP endpoint: `/v1/mcp` or `/v1/mcp/authv2`? 🧑 architect / 🧪 runtime
Phase 0 validated `/v1/mcp`. Atlassian's getting-started docs now recommend `/authv2`.
**Resolve:** run the smoke test on `/v1/mcp` first (known-good), then try `/authv2`.

## Q3 — Does `OAuthClientProvider.server_url` need the base or the full resource path? 🧪 runtime
Reference passes the **base** `https://mcp.atlassian.com` for `.well-known` discovery while
the toolset connects to `…/v1/mcp/authv2`. RFC 8414/9728 discovery is relative to
`server_url`.
**Resolve:** first-run DEBUG logs — confirm discovery hits
`/.well-known/oauth-authorization-server` and resolves the `cf.mcp.atlassian.com` endpoints.
If discovery 404s, try `server_url=<full /v1/mcp URL>`.

## Q4 — On access-token expiry (~1h, no refresh token), does the SDK auto-retrigger the browser? 🧪 runtime
With no `offline_access`, there's no refresh token. Unknown whether `OAuthClientProvider`
transparently re-runs the interactive flow on the next 401, or raises and requires us to
delete `token.json` and reset.
**Resolve:** let a token expire (or hand-expire it), make a tool call, observe behavior.
Implement the catch-and-reset fallback regardless (D6).

## Q5 — Windows `0600` perms on token files. 🧪 runtime / 📄 docs
`os.chmod(.., 0o600)` is effectively a no-op on Windows (our dev box). The Phase-1 DoD
expects `0600`. **Resolve:** either (a) document the check as POSIX-only, or (b) use Windows
ACL hardening (`icacls`) if we care. Recommend (a) for Phase 1; note the file lives under
the user profile already.

## Q6 — Will Rovo Search indexing lag still bite, and what's the actual SLO? 🧪 runtime
Phase 0 saw a <1h-old Confluence page missed by `search` but found via CQL. Phase-0 open
question #1 asked to re-measure after 24h.
**Resolve:** during Phase-1 smoke test, re-run `search` on the same page and record
time-to-index. The SKILL already mitigates (fall back to JQL/CQL for fresh content).

## Q7 — Behavior on revoked grant. 🧪 runtime
What does the MCP server return if the user revokes the OAuth grant in
admin.atlassian.com mid-session? (Phase-0 open question #5, still open.)
**Resolve:** revoke, then make a tool call; record the error and ensure D6's re-auth path
handles it gracefully.

## Q8 — `redirect_uris` as `AnyUrl` vs plain str. 🧪 low-risk
Pydantic coerces today, but confirm no warning/validation issue under `mcp 1.24` strict
mode. **Resolve:** wrap in `AnyUrl(...)` to be safe (cheap; matches SDK example).

## Q9 — VISION.md internal inconsistency. 🧑 architect (doc hygiene)
VISION §3's "Locked Decisions" table still lists **Model = `gemini-2.5-flash`**, while every
other file (README, agent.py, .env, pyproject) uses Bedrock Claude Sonnet 4.6.
`scratchpad/00-context.md` says the switch was intentional.
**Resolve:** in our build's VISION, correct §3 to Bedrock Sonnet 4.6 so the locked-decision
table is the single source of truth.

---

## Resolved during this research (no longer open)
- ✅ ADK class name is `McpToolset` (alias `MCPToolset`) — both exported.
- ✅ `httpx_client_factory` exists on `StreamableHTTPConnectionParams`; shipped ADK v1.22.0.
- ✅ ADK 2.0.0 is GA (2026-05-19) and pins `mcp>=1.24,<2`, `litellm>=1.83.7,<=1.83.14`.
- ✅ Bedrock model ID corrected (see `03`).
- ✅ SKILL.md (~12KB) fits the model's 1M context — no injection-budget concern (was
  Phase-0 open question #3).
