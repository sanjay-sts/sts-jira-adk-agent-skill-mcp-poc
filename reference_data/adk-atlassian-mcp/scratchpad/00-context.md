# Session Context Handoff

This document captures the architect's session that produced this repo. Read it
before doing research or writing code so you don't re-derive what's already
been established.

## Who and what

- **Architect:** Sanjay Sukumaran, Principal AI Architect at Nutrien (Calgary).
  Working style: research-first, prefers actionable/direct guidance, gravitates
  toward architectural rigor with pragmatic incremental delivery.
- **Goal:** Build an ADK agent (Google Agent Development Kit, Python) that
  calls the official Atlassian Rovo Remote MCP Server using OAuth 2.1 + DCR +
  PKCE, where each end user authenticates with their own identity and the
  agent fetches Jira/Confluence content they have permission to see.
- **Why this experiment exists:** validates three patterns for Sanjay's wider
  enterprise work — per-user OAuth delegation inside agents, MCP DCR
  (RFC 7591) as the canonical client-onboarding pattern, and ADK as a node
  in a federated multi-agent system.

## Environment

- **Atlassian site:** `brokenai.atlassian.net` (Jira Free + Confluence 7-day
  Standard trial). User: `stsadmin@2tdgcb.onmicrosoft.com` from his M365
  Developer Program tenant.
- **cloudId:** `bb601b11-3c61-488a-b04a-063489631929`
- **Sample data:**
  - Jira project `KAN` with issue `KAN-1` ("get mcp working via rovo")
  - Confluence space `BROKENAI` (id `425987`) with page "MCP Server testing
    jira item" (id `196745`)
- **Rovo admin config:** Permissions all ALLOWED (Read/Write/Search),
  Authentication = OAuth 2.1 (API tokens off), A2A disabled, domain
  allowlist = "Atlassian supported domains" toggle on (covers loopback).
- **Model:** Claude Sonnet 4.6 on AWS Bedrock via LiteLLM (matches Sanjay's
  existing Bedrock setup: SSO profile `mainadmin`, us-east-1). Originally
  drafted for `gemini-2.5-flash`; switched at architect's request.

## What's been validated (Phase 0)

Tested via MCP Inspector v0.21.2 against `https://mcp.atlassian.com/v1/mcp`.
Full evidence: `docs/phase-0-results.md`. Headline findings:

1. **DCR + PKCE flow works end-to-end.** MCP Inspector successfully registers
   a public client at `cf.mcp.atlassian.com/v1/register`, completes PKCE
   authorization, exchanges for a token, and calls tools.

2. **Two-layer consent confirmed.** First consent at `mcp.atlassian.com`
   (user picks products: Jira/Confluence/Compass). Second consent at
   `api.atlassian.com` (user sees the actual OAuth scopes the MCP server
   requests against Atlassian's identity layer). The SECOND consent has a
   short JWT TTL — read fast.

3. **Scope set is server-controlled.** Captured ground-truth scopes:
   - Jira: `read:jira-work`, `write:jira-work`
   - Confluence: `read:comment:confluence`, `read:confluence-user`,
     `read:page:confluence`, `read:space:confluence`, `search:confluence`,
     `write:comment:confluence`, `write:page:confluence`
   - **No `offline_access`** → no refresh token. On 401, fully re-auth.
   - **No `write:sprint:jira-software`** → transitionJiraIssue fails on
     Scrum projects with Sprint on the transition screen
     (atlassian/atlassian-mcp-server#159).

4. **Tool count is 30, not 40+.** Full inventory in SKILL.md. The two
   most important new entries are `search` and `fetch` — Rovo's unified
   Jira+Confluence search returning ARIs. These are now the **default**
   tools per SKILL.md decision rules.

5. **Tool schemas occasionally lie.** `search` and `fetch` schemas claim
   `cloudId` is "not needed — derived from token", but runtime requires it.
   Always pass cloudId.

6. **Rovo Search has indexing lag.** A Confluence page <1 hour old was
   missed by `search` but found instantly via direct CQL. For freshly-created
   content, fall back to JQL/CQL.

## Architectural decisions made (locked in VISION.md §3)

- **Endpoint:** `https://mcp.atlassian.com/v1/mcp/authv2` (Atlassian's current
  recommended path; `/v1/mcp` still works).
- **Transport:** Streamable HTTP (SSE deprecated June 2026).
- **OAuth:** Authorization Code + PKCE (S256) + RFC 7591 DCR. **Do not**
  register a static OAuth app at developer.atlassian.com — those tokens get
  rejected by the MCP server at tool-call time.
- **OAuth client:** MCP Python SDK's `OAuthClientProvider` (an `httpx.Auth`
  subclass). It implements DCR + PKCE + refresh + storage abstraction.
- **ADK integration:** inject `OAuthClientProvider` into
  `StreamableHTTPConnectionParams.httpx_client_factory` (added via
  google/adk-python#3005). ADK has no native DCR/PKCE for MCP yet.
- **ADK version:** `google-adk[litellm] >= 2.0` (the `httpx_client_factory`
  field requires 2.0+).
- **Model:** Claude Sonnet 4.6 on Bedrock via LiteLLM (`bedrock/...`).
- **Tool filter (Phase 1):** 8 read-only tools — see `agent.py` constant
  `PHASE_1_TOOL_FILTER`.
- **JSM:** out of scope (currently requires API-token auth, not OAuth).

## Non-negotiables (VISION.md §5)

12 hard rules. The ones most likely to bite:

- **#1:** No static OAuth app — DCR is mandatory.
- **#2:** Don't try to control scopes from the client; they're server-fixed.
- **#10:** Tool schemas are aspirational — validate empirically. Always pass
  `cloudId` even when schema says optional.
- **#11:** Rovo Search has indexing lag. Fall back to JQL/CQL for fresh
  content.
- **#12:** No `offline_access`, no refresh token. On 401, re-auth.

## Skill architecture (SKILL.md)

The SKILL.md is loaded into the agent's `instruction` at construction time
via `_load_skill()` in agent.py. It includes:

- 8 decision rules in priority order (apply first match)
- Read/write workflow patterns
- JQL + CQL cheatsheets
- 11 pitfalls including the validated ones above
- Full 30-tool inventory

**Future direction (Phase 2.5):** move SKILL.md to a remote FastMCP server
using `SkillsDirectoryProvider` (FastMCP 3.0+). The current inlined approach
is interim. The SKILL.md is already structured to move out unchanged
(frontmatter intact, no relative dependencies).

## What's deliberately NOT done yet

- **Multi-user.** Phase 2. Current `FileTokenStorage` is single-user.
- **Write operations.** Read-only Phase 1. `editJiraIssue`,
  `addCommentToJiraIssue`, etc. excluded from tool filter.
- **JSM tools.** Need API-token auth, not OAuth.
- **Confluence beyond read.** Adds complexity around ADF/HTML round-trip.
- **Entra ID federation.** Phase 3 ("Continue with Microsoft"). Just-works
  on first run with the right Atlassian email; doesn't change the agent.
- **Atlassian Guard / SAML / SCIM.** Phase 4. $4/user/mo, only matters for
  enterprise rollout.
- **LangGraph orchestration.** This agent is a leaf node; supervisor
  integration is separate work.
- **Remote skill server (FastMCP `SkillsDirectoryProvider`).** Phase 2.5.

## How to use this repo with Claude Code

Suggested kickoff prompt:

> Read in order: scratchpad/00-context.md → VISION.md → SKILL.md →
> docs/phase-0-results.md → agent.py.
>
> Then do research before code. For each library, drop findings in
> scratchpad/:
>
> - 01-research-adk-mcp.md: Context7 lookup for `google-adk` (Python, latest).
>   Confirm `LiteLlm` model wrapper, `McpToolset`,
>   `StreamableHTTPConnectionParams.httpx_client_factory` (issue #3005),
>   `tool_context.state`, multi-user session management.
> - 02-research-mcp-sdk.md: Context7 lookup for `mcp` (Python SDK, latest).
>   `OAuthClientProvider`, `TokenStorage`, `OAuthClientMetadata`,
>   `OAuthToken`, DCR + PKCE flow internals, error handling on 401.
> - 03-research-litellm-bedrock.md: Verify the Bedrock model ID format for
>   Claude Sonnet 4.6 (`bedrock/anthropic.claude-sonnet-4-6-...`) via
>   LiteLLM docs and `aws bedrock list-foundation-models`. Confirm AWS
>   credential precedence with ADK + LiteLLM.
> - 04-decisions.md: For each non-trivial choice (token storage backend,
>   error handling on 401, how SKILL.md is injected, logging,
>   tests), document chosen approach + 2-3 alternatives + why.
> - 05-open-questions.md: anything ambiguous after research. Don't guess.
>
> Stop after scratchpad/. Tag the architect for review of 04 and 05 before
> implementation. All VISION.md §3 locked decisions and §5 non-negotiables
> are binding — if research suggests deviation, raise in 05, don't silently
> change.
