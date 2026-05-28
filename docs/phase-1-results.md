# Phase 1 Results — ADK Agent against Atlassian Rovo MCP

**Date:** 2026-05-27
**Operator:** sanjay.s.t.s@gmail.com
**Atlassian site:** brokenai.atlassian.net
**Model:** `bedrock/us.anthropic.claude-sonnet-4-6` (AWS Bedrock cross-region inference, via LiteLLM)
**Endpoint:** `https://mcp.atlassian.com/v1/mcp`
**ADK / mcp versions:** google-adk 2.1.0 · mcp 1.27.1 · litellm 1.86.2 · httpx 0.28.1

## TL;DR

Phase 1 passed its primary exit criterion. OAuth DCR + PKCE completed successfully through two
Atlassian consent screens, tokens cached to `~/.atlassian-mcp/`, and the agent returned live Jira
data (`KAN-1`) on the first prompt. The MCP session negotiated protocol `2025-11-25` against
`r13-*` session IDs. One cosmetic ADK 2.1.0 serialization bug affects the web-UI graph panel only
and has no impact on agent functionality. The second-prompt (no-browser) scenario is pending a
separate restart within the ~1 h token TTL.

## Definition of Done — checklist

- [x] `uv sync` + `adk web` boots with no errors
- [x] First prompt → browser OAuth → agent answers using the 8 validated tools
- [x] `~/.atlassian-mcp/{client.json, token.json}` persisted (Windows: chmod no-op, as designed)
- [ ] Second prompt after process restart (within TTL) → Jira issues, NO browser *(pending)*
- [x] Granted scopes logged on first persist: `scope=""` (server controls scopes — empty string
      returned, consistent with DEC-3)
- [x] This doc completed

## Measurements

| Metric | Value |
|---|---|
| MCP protocol version negotiated | `2025-11-25` |
| Time-to-first-token (incl. both consent screens) | ~26 s (19:24:48 session → 19:25:03 LiteLLM call) |
| Observed access-token TTL | ~1 h (Atlassian standard; not yet expiry-tested) |
| Refresh token issued? | **Yes** — `Refresh token present: True` (see Deviations) |
| Granted scopes (`OAuthToken.scope`) | `""` (empty — server-controlled, client field ignored) |

## Consent screens (exact text)

1. **Consent #1** (`mcp.atlassian.com`, DCR products):
   > "Atlassian Rovo MCP server — atlassian-mcp-agent is requesting access. Apps: Jira,
   > Confluence, Compass. Redirect URI: http://127.0.0.1:3030/callback."

2. **Consent #2** (`api.atlassian.com`, scopes — brokenai.atlassian.net):
   > Jira: View / Update `jira-work`.
   > Confluence: View `Comments, Contents, Page, Space details, confluence-user` / Update
   > `Comments, Page` / Search `confluence`.
   > User: View `me`.

## Deviations from VISION / Phase-0

1. **Refresh token issued (unexpected).** VISION §4 stated "no `offline_access`, no refresh
   token". In the live run `Refresh token present: True` was logged. The Atlassian MCP server
   appears to now issue a refresh token by default even without `offline_access` in the scope.
   This is a positive surprise — token lifetimes are no longer strictly ~1 h with forced re-auth.

2. **`build_graph` returns 500 in ADK 2.1.0.** The `/dev/apps/.../build_graph` endpoint used
   by the web-UI "State" tab fails with
   `PydanticSerializationError: Unable to serialize unknown type: LiteLLMClient`.
   This is a cosmetic ADK bug (graph visualisation only); all agent/tool/model calls work
   correctly. No workaround needed.

3. **`AWS_PROFILE` must be set before `uv run adk web` on Windows.** Botocore's credential
   chain does not pick up an SSO session from `aws sso login` when the profile isn't exported
   into the process environment. Workaround: `set AWS_PROFILE=mainadmin` before starting ADK.

## Runtime open questions resolved

- **Q3 `server_url` base-vs-resource discovery:** `ATLASSIAN_MCP_BASE = https://mcp.atlassian.com`
  confirmed correct. SDK falls back to RFC 8414 (`/.well-known/oauth-authorization-server`) as
  expected; `/.well-known/oauth-protected-resource` returns 404 (DEC-10 validated).
- **Q5 Windows `0600` perms:** `chmod_600` is a documented no-op on Windows; token files written
  successfully without error.
- **Q3/Q4/Q6/Q7:** Not yet exercised (expiry, revocation, indexing-lag SLO — require longer
  running session).

## Next steps (Phase 2 prerequisites)

- Confirm second-prompt no-browser flow (restart within token TTL).
- Investigate whether the issued refresh token is honoured on expiry (would remove forced
  re-auth entirely).
- Phase 2: per-user keyed token storage (`FileTokenStorage` keyed by user ID).
- Phase 2: support `ATLASSIAN_API_TOKEN` env-var path for headless / JSM use cases (DEC-12).
- Fix or work around ADK 2.1.0 `build_graph` serialization bug (track upstream).
