# 04 — Decisions (for architect review before code)

> **Resolutions live in [`00-decision-log.md`](00-decision-log.md)** (2026-05-27). DEC-1..DEC-5
> were answered by the architect; D3 endpoint → `/v1/mcp`, D1 model → `bedrock/us.anthropic.claude-sonnet-4-6`,
> D11 package → `atlassian_mcp_agent`. The "decision needed from you" notes below are kept for
> context but are now superseded by the log.

**Date:** 2026-05-27
Each decision = chosen approach + alternatives + why. Anything that touches a VISION §3
locked decision or §5 non-negotiable is flagged. **Items marked 🔴 are corrections to the
reference repo** that I recommend carrying into our fresh Phase 1 build.

---

## D1 🔴 Bedrock model ID → use an inference profile
- **Chosen:** `BEDROCK_MODEL_ID = "bedrock/us.anthropic.claude-sonnet-4-6"`.
- **Alternatives:** `bedrock/global.anthropic.claude-sonnet-4-6` (max availability, routes
  worldwide — may cross geographies); `bedrock/converse/us.anthropic.claude-sonnet-4-6`
  (force Converse route explicitly).
- **Why:** us-east-1 offers no In-Region inference for Sonnet 4.6; the base ID errors on
  on-demand. The reference's `…-20250929-v1:0` ID does not exist. See `03`.
- **Decision needed from you:** Geo `us.` vs Global `global.`? (Data-residency vs.
  availability. For a Calgary/Nutrien context, `us.` keeps routing within US/Canada regions.)

## D2 🔴 Tighten dependency pins
- **Chosen:** `google-adk[litellm]>=2.0`, `mcp>=1.24,<2`, `httpx>=0.27`, `boto3>=1.34`.
- **Alternative:** leave `mcp>=1.6` (reference) — rejected: below ADK's own `>=1.24` floor
  and the missing `<2` cap risks breaking ADK's `httpx_client_factory` plumbing (see `02`).
- **Why:** match ADK 2.0's resolved graph; make the load-bearing `<2` cap explicit.

## D3 MCP endpoint URL
- **Chosen (proposed):** `https://mcp.atlassian.com/v1/mcp` for Phase 1.
- **Alternative:** `/v1/mcp/authv2` (Atlassian's current "new integrations" recommendation;
  the reference uses this).
- **Why:** `/v1/mcp` is what Phase 0 actually validated end-to-end. Both work. Recommend
  matching the validated path for the smoke test, then A/B `/authv2` once green.
- **Decision needed from you:** match Phase-0 (`/v1/mcp`) or adopt latest (`/authv2`)?

## D4 OAuth client = MCP SDK `OAuthClientProvider` (LOCKED, VISION §3)
- **Chosen:** keep it. Inject as `httpx.Auth` via `httpx_client_factory`.
- **Alternatives considered & rejected by VISION:** static OAuth app at
  developer.atlassian.com (tokens rejected by MCP server at tool-call time — §5.1);
  hand-rolled DCR/PKCE (reinventing the SDK).
- **Why:** it's the only path the MCP server accepts; SDK already does DCR+PKCE+storage.

## D5 Token storage (Phase 1) = file-based JSON in `~/.atlassian-mcp/`
- **Chosen:** keep `FileTokenStorage` (`token.json`, `client.json`, `0600`).
- **Alternatives (deferred to Phase 2):** keyed per-`user_id` dir; SQLite; Redis.
- **Why:** simplest thing that persists across restarts; Phase-1 is single-user (VISION §2).
- **Nit:** `os.chmod(.., 0o600)` is a no-op on Windows — the reference already swallows
  `NotImplementedError`. On our Windows dev box, document that 0600 is POSIX-only and the
  Phase-1 DoD's "0600 perms" check is effectively Unix-only. (→ `05`.)

## D6 401 / token-expiry handling = full re-auth, no refresh
- **Chosen:** on 401 after ~1h, delete `token.json` and re-run interactive auth. Never
  loop-retry. Surface a clear message to the user.
- **Why:** Atlassian grants no `offline_access` → no refresh token (Phase 0, VISION §5.12).
- **Open:** does `OAuthClientProvider` auto-retrigger the browser on expiry, or must we
  catch it? (→ `05`.)

## D7 SKILL.md injection = inline into agent `instruction` at construction
- **Chosen:** keep `_load_skill()` (strip frontmatter, embed body).
- **Alternatives:** remote FastMCP `SkillsDirectoryProvider` (Phase 2.5, deferred);
  per-call tool description augmentation (heavier).
- **Why:** simplest; SKILL.md already structured to move out unchanged later.
- **Watch:** SKILL.md is ~12KB → fits easily in a 1M-context model. No budget concern with
  Sonnet 4.6 (cf. Phase-0 open question #3, now effectively answered).

## D8 Tool filter (Phase 1) = the validated 8 read-only tools
- **Chosen:** keep `PHASE_1_TOOL_FILTER` exactly as Phase-0 validated (atlassianUserInfo,
  getAccessibleAtlassianResources, search, fetch, searchJiraIssuesUsingJql,
  searchConfluenceUsingCql, getJiraIssue, getConfluencePage).
- **Why:** read-only until the auth path is rock-solid (VISION §6); matches Phase-0 evidence.

## D9 Logging = structured, redact `Authorization`, log granted `scope` on first persist
- **Chosen:** keep the reference's approach; ensure httpx logging never emits the bearer.
- **Why:** VISION §5.7 (never log tokens) + §5.12 / DoD (log granted scope = ground truth).

## D10 Tests (Phase 1) = unit test `FileTokenStorage` round-trip only
- **Chosen:** `tests/test_token_storage.py` — serialize/deserialize `OAuthToken` &
  `OAuthClientInformationFull` through JSON; assert equality + `0600` on Unix.
- **Alternatives:** live integration test against the MCP server — deferred to a manual
  smoke-test checklist in the README (VISION §8).
- **Why:** Phase 1 doesn't need live integration tests; the OAuth dance is human-in-loop.

## D11 Project layout (fresh build at repo root)
- **Chosen (proposed):** mirror the reference target (VISION §7) at repo root —
  `pyproject.toml`, `<pkg>/__init__.py` + `agent.py`, `tests/`, `docs/`, `scratchpad/`.
  Keep `reference_data/` untouched as read-only reference.
- **Decision needed from you:** package name. Reference uses `atlassian_mcp_test`; repo is
  `sts-jira-adk-agent-skill-mcp-poc`. Propose `atlassian_mcp_agent` (drop "test").
- **Tooling:** `uv`, `ruff`, `basedpyright` strict, `pytest` + `pytest-asyncio` (per VISION §8).
  Add a `justfile` for `run/test/lint/typecheck` (architect prefers `just`).

---

## Things the reference got right (no change)
- The whole `httpx_client_factory` ↔ `OAuthClientProvider` seam (D4).
- `McpToolset` import name and `tool_filter` usage.
- Generous (≥300s) first-request timeouts.
- `token_endpoint_auth_method="none"` + omitting `scope` (server-controlled).
- The 8-tool read-only filter and the SKILL decision rules.
