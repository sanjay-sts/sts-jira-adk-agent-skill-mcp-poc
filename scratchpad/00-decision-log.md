# 00 — Decision Log (Phase 1)

**Date:** 2026-05-27  ·  **Status:** awaiting architect greenlight on the full log before code.
This is the single source of truth for decisions; it supersedes the "decision needed" flags in
`04-decisions.md` and the architect-tagged questions in `05-open-questions.md`.

Legend: ✅ decided · ⏳ pending architect · 🔒 VISION-locked (no choice)

---

## Decisions requiring architect input — RESOLVED this session

| # | Decision | Status | Choice | Why |
|---|---|---|---|---|
| DEC-1 | Model + Bedrock inference profile | ✅ | `bedrock/us.anthropic.claude-sonnet-4-6` | You chose `us.` Sonnet 4.6. us-east-1 has no in-region inference (profile mandatory); `us.` keeps routing in US/CA. Replaces reference's invalid `…-20250929-v1:0`. |
| DEC-2 | MCP endpoint | ✅ | `https://mcp.atlassian.com/v1/mcp` | Authoritative atlassian-mcp-server repo + OAuth & API-token docs all say `/v1/mcp`. `/authv2` not in current docs → dropped. Also matches Phase-0 validation. (see `06`) |
| DEC-3 | Auth method (Phase 1) | ✅ | OAuth 2.1 (3LO) + DCR + PKCE | Per-user delegation = the experiment's purpose (VISION §1). API-token (headless) is non-delegated; recorded as the future/JSM/service path. (see `06`) |
| DEC-4 | Package name | ✅ | `atlassian_mcp_agent` | Production-oriented; drops the "test" suffix. |
| DEC-5 | ADK project structure | ✅ | Root-level agent package per ADK v2 convention | `__init__.py`→`from . import agent`; `agent.py`→`root_agent`. One convention serves `adk web`/`run`/`api_server`. (see `07`) |

## Corrections / recommendations — applied (flag to change)

| # | Decision | Status | Choice | Why |
|---|---|---|---|---|
| DEC-6 | Dependency pins | ✅ | `google-adk[litellm]>=2.0`, `mcp>=1.24,<2`, `httpx>=0.27`, `boto3>=1.34` | Match ADK 2.0's resolved graph; `<2` cap protects the `httpx_client_factory` seam. (see `01`,`02`) |
| DEC-7 | VISION §3 doc fix | ✅ | Correct model to Bedrock Sonnet 4.6 | Reference VISION still says `gemini-2.5-flash`; everything else uses Bedrock. |

## VISION-locked (no choice — restated for completeness)

| # | Decision | Status | Choice |
|---|---|---|---|
| DEC-8 | OAuth client | 🔒 | MCP SDK `OAuthClientProvider` via `httpx_client_factory` |
| DEC-9 | Phase-1 token storage | 🔒 | File-based `~/.atlassian-mcp/` (`token.json`,`client.json`, 0600/POSIX) |
| DEC-10 | 401 / expiry handling | 🔒 | Full re-auth, no refresh (Atlassian grants no `offline_access`) — **superseded 2026-06-01; see Post-Phase-1 additions** |
| DEC-11 | Tool filter | 🔒 | 8 read-only tools (Phase-0 validated) |
| DEC-12 | SKILL injection | ✅ | Inline into agent instruction (`_load_skill`) |
| DEC-13 | Phase-1 tests | ✅ | `FileTokenStorage` round-trip unit test only |

## Build-level choices — RESOLVED this session

| # | Decision | Status | Choice | Why |
|---|---|---|---|---|
| DEC-14 | Phase 1 tool scope | ✅ | Jira **+ Confluence read** (validated 8 tools) | Confluence read is free (server-controlled scopes → consent shows both regardless) and Phase-0-proven. Supersedes VISION §6's "Confluence in Phase 2+". |
| DEC-15 | Logging | ✅ | stdlib logging + `Authorization` redaction filter; log granted scope; **defer JSON** | Satisfies §5.7 (never log tokens) with least effort; true JSON/structlog deferred to observability work. |
| DEC-16 | Git hygiene | ✅ | Commit `reference_data/` + `scratchpad/`; gitignore `.env`, `.venv/`, `__pycache__/` | Version the reference + decision trail; secrets/venv stay out. `~/.atlassian-mcp/` is outside the repo. |

### Implementation calls (architect did not object)
- Runner: `justfile` + README raw-command fallback (Windows).
- `SKILL.md` inside `atlassian_mcp_agent/`, loaded via `__file__`.
- OAuth callback `127.0.0.1:3030`; port via `ATLASSIAN_OAUTH_CALLBACK_PORT` (default 3030).
- ADK session service: `InMemory` (local single-user).
- Agent `name="atlassian_mcp_agent"`; Python `>=3.11`.

---

## Post-Phase-1 additions (2026-06-01)

| # | Decision | Status | Choice | Why |
|---|---|---|---|---|
| DEC-17 | Prompt caching (Bedrock/LiteLLM path) | ✅ | Cache the static prefix (tool schemas + the SKILL.md system prompt) via LiteLLM `cache_control_injection_points=[{"location":"message","role":"system"}]`. Bedrock chains tools→system, so one `system` checkpoint covers both. Fallback (`ATLASSIAN_CACHE_FALLBACK=1`): a `LiteLLMClient` subclass that injects `cache_control` directly. | ADK has no native cache_control for LiteLLM (adk-python#994 open) and ADK's built-in Context Caching is Gemini-only — neither helps the Bedrock/Claude path. The static prefix is re-billed at full price on every model call in the agentic loop (the dominant repeated cost). Sonnet 4.6: 1,024-token min (prefix clears it), 5-minute TTL only (so no `ttl` is set). Verified the kwarg flows through ADK 2.1.0's `LiteLlm._additional_args` → `acompletion`; hit rate confirmed in a live run (`cachedContentTokenCount` > 0). |
| DEC-18 | Enable write tools (Jira + Confluence) | ✅ | Add the create/update tools to `PHASE_1_TOOL_FILTER`: Jira `createJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue`, `transitionJiraIssue`, `addWorklogToJiraIssue`, `createIssueLink`; Confluence `createConfluencePage`, `updateConfluencePage`, `createConfluenceFooterComment`, `createConfluenceInlineComment`; plus read-helpers `getTransitionsForJiraIssue`, `getIssueLinkTypes`, `getConfluenceSpaces`. | The Phase 1 auth path proved solid (live run), so VISION §6's "read-only until auth is rock-solid" condition is met. Server-controlled scopes already grant `write:jira-work` / `write:page:confluence` / `write:comment:confluence`, so no new consent is needed. Agent instruction + SKILL.md enforce read-before-write and confirm-before-mutate. Known gap: `write:sprint:jira-software` isn't granted → `transitionJiraIssue` can fail on Scrum boards with a Sprint field on the transition screen (pitfall 2). |

**Refresh-token correction (supersedes DEC-10).** The Phase 1 live run returned a refresh token despite no `offline_access` in the granted scope set (server default; see `docs/phase-1-results.md`). The MCP SDK now auto-refreshes access tokens; full interactive re-auth is needed only on a *persistent* 401 (refresh expired or grant revoked). `agent.py`, `SKILL.md`, `README.md`, and `VISION.md` §5 updated to match.

## Net changes vs. the reference repo
1. Model ID → `bedrock/us.anthropic.claude-sonnet-4-6` (was an invalid ID).
2. Endpoint → `/v1/mcp` (was `/authv2`).
3. `mcp` pin → `>=1.24,<2` (was `>=1.6`).
4. Package → `atlassian_mcp_agent` (was `atlassian_mcp_test`).
5. VISION §3 model row → Sonnet 4.6 (was `gemini-2.5-flash`).
6. Document API-token auth as the future/JSM/headless path.

## Gate
⏳ **Architect to confirm this log**, then implementation proceeds per the task list.
Runtime-only open items (not blockers) remain in `05`: server_url discovery shape, auto-reauth
on expiry, Windows 0600 no-op, Rovo indexing-lag SLO, revoked-grant behavior.

---

## Phase 2 — Multi-user, no cross-talk (2026-06-04)

Full design: `docs/phase-2-design.md`.

| # | Decision | Status | Choice | Why |
|---|---|---|---|---|
| DEC-19 | Phase 2 multi-user model | ✅ | **Stateless bearer forwarding**, not a server-side keyed token store. The agent runs as a remote service (ECS Fargate + ALB, autoscaled); the **client does all OAuth** (DCR+PKCE+consent) and forwards a valid Atlassian access token on **every** request. The agent stores **no** credential — bearer lives only in a **request-scoped `contextvars.ContextVar`**. Injection uses ADK 2.1's native **`McpToolset(header_provider=…)`** (called per tool call; verified `mcp_tool.py:386-396`), with `MCPSessionManager` **pooling sessions by a hash of the headers** (`_generate_session_key:289`) → a **distinct MCP session per bearer**, so A's call can never reach B's session. `OAuthClientProvider` / `FileTokenStorage` / DCR / loopback callback are **removed from the agent**. | Hard security constraint: no cred at rest in the agent. **Supersedes VISION §2 / DEC-10's keyed-store plan** (which assumed server-side storage). Surface-1 isolation is structural in ADK (header-keyed sessions) + enforced downstream by Atlassian per-token; the agent's only job is **non-crossing + non-persistence**. Source-verified against google-adk 2.1.0 / mcp 1.27.1 (Task 2). |
| DEC-20 | Two isolation surfaces | ✅ | **S1 (creds/data):** prevented structurally by the per-request contextvar bearer + Atlassian per-token enforcement. **S2 (conversation history):** prevented by ADK `(user_id, session_id)` session keying **+ a token↔user binding check** (bind `conversation_id→accountId` on creation; reject a turn whose bearer resolves to a different accountId = anti-hijack). IDs (`user_id`, `conversation_id`, token-fingerprint-hash) are **validation + audit, not prevention**. | Turning on multi-conversation routing creates S2, which Atlassian does **not** protect — a client presenting a valid token but claiming another user's `conversation_id` could pull that history unless the binding is checked. The IDs exist to protect S2 and to audit S1. |
| DEC-21 | Test strategy | ✅ | A2A is the **last** thing added (L4); cross-talk lives below it. Prove the property with a **scripted concurrency harness** (ground-truth `bearer→accountId→resource`, probes `atlassianUserInfo` + a permission-scoped read) covering concurrent-multi-user, multi-conversation-per-user, sequential-residual, and hijack-rejection. 3 machines / 3 **unequal-permission** users are the realism/demo layer, not the verdict. Model: **Haiku** via `BEDROCK_MODEL_ID`; assert on tool output, not prose. | Cross-talk is a race; humans can't generate the overlap/volume. The current code fails the *sequential-residual* case by construction (one global `token.json`). |

**Gating unknown (spike first):** does the Atlassian MCP server honor a per-request bearer on a
shared session, or bind the session to the first user? Decides shared-toolset-with-per-request-auth
vs per-request ephemeral MCP session. Until answered, default to per-request sessions.

**Removed from the agent in Phase 2:** `OAuthClientProvider`, `FileTokenStorage`, the loopback
OAuth callback server, and the module-global `oauth`/`toolset` singletons — all moved client-side
or replaced by the per-request contextvar path.
