# Phase 2 — Multi-user, no cross-talk (stateless bearer forwarding)

> Supersedes VISION §2's "keyed token store, dir per `user_id`" plan. That plan
> assumed **server-side token storage**, which a hard security constraint now forbids.
> This document is the Phase 2 architecture of record. Decision: `00-decision-log.md` DEC-19.

## 1. The requirement (exactly)

The agent runs as a **remote service** (ECS Fargate behind an ALB, autoscaled for 100s of
users). N users on N devices connect concurrently; **4+ users may share one task**, each with
one or more conversations. The single hard requirement:

> **User A's credentials are never used in user B's conversation, and user A's
> conversation history is never shown to user B — single task or autoscaled.**

Hard constraint: **no credential at rest in the agent.** The agent stores no token on disk,
in a database, or in session state. A token exists only in **request-scoped process memory**
for the duration of the call, then is discarded. (Note: the agent *does* see the plaintext
bearer in memory in-flight — it must, to make the MCP call. "No cred in the agent" means
**no persistence**, not "never touched.")

## 2. Two isolation surfaces

| | **Surface 1 — Atlassian creds/data** | **Surface 2 — conversation history** |
|---|---|---|
| What leaks | A's token used in B's request → B does what A can do | A's stored chat history shown to B |
| Prevented by | contextvar per-request bearer **+ Atlassian enforces per-token** | ADK session keying **+ token↔user binding check** |
| IDs' role | audit only (which token served which call) | **essential — this is what the IDs protect** |

**Prevention is structural; IDs are validation + audit, not prevention.** Surface 1's wall is
the request-scoped bearer with no shared mutable user-bound state. Surface 2's wall is ADK's
`(user_id, session_id)` session keying *plus* verifying the presented token actually belongs
to the user who owns the conversation.

## 3. Trust model

- **The client does OAuth.** Each device runs the full Atlassian MCP OAuth (DCR + PKCE +
  consent), holds its own tokens, refreshes them, and sends a currently-valid access token on
  **every** request. The agent runs **no** OAuth — `OAuthClientProvider`, `FileTokenStorage`,
  DCR, and the browser flow are **removed** from the agent.
- **Atlassian is the authority for Surface 1.** A forwarded token can only ever do what that
  token permits; a buggy/malicious client cannot impersonate another user by *claiming* an
  identity. So the agent need not trust the client's `user_id` claim for *data access* — only
  for *conversation routing* (Surface 2), which is why Surface 2 needs the binding check.

## 4. Architecture

```
Device A ─┐                                  ┌─ Fargate task ──────────────┐
Device B ─┼─► ALB (TLS term) ────────────────┤  FastAPI request handler    │
Device N ─┘   each request carries:          │   • bearer → ContextVar     │
              Authorization: Bearer <tok>    │   • (user_id, conv_id)      │──► Atlassian MCP
              + user_id + conversation_id    │   • Runner.run_async(...)   │    (per-request
                                             │   • clear ContextVar        │     Bearer)
                       │                     └─────────────┬───────────────┘
                       │                                   │
                       └─ shared SessionService ◄──────────┘  (history only, NEVER the bearer)
                          keyed by (app, user_id, conversation_id)
```

### Per-request bearer injection (Surface 1) — ADK-native `header_provider`

ADK 2.1's `McpToolset` accepts a `header_provider` callable invoked **per tool call**
(`mcp_tool.py:_run_async_impl:386-396`), and `MCPSessionManager` **pools sessions keyed by a
hash of the (merged) headers** (`mcp_session_manager.py:_generate_session_key:289-291`,
`create_session:480-505`). So a per-request bearer in the header → a **distinct, isolated MCP
session per bearer**, automatically. We don't hand-roll httpx auth; we feed the contextvar
bearer through the header provider:

```python
_bearer: ContextVar[str | None] = ContextVar("atlassian_bearer", default=None)

def _header_provider(ctx: ReadonlyContext) -> dict[str, str]:
    tok = _bearer.get()
    if not tok:
        raise RuntimeError("no forwarded bearer in request context")
    return {"Authorization": f"Bearer {tok}"}

toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=ATLASSIAN_MCP_URL, ...),
    tool_filter=PHASE_1_TOOL_FILTER,
    header_provider=_header_provider,   # called per tool call; bearer from request context
)
```

- **Cross-talk is structural in ADK:** A's header hash ≠ B's, so `create_session` can never
  route A's call onto B's pooled session. No shared-bearer swap ever happens.
- **No module-global user state, no OAuthClientProvider/FileTokenStorage** — removed entirely.
- **Bearer never persisted:** not in `session.state`, the session store, logs (reuse
  `logging_redaction`), traces, APM local-variable capture, or core dumps. It lives only in the
  request-scoped contextvar and in the pooled session's in-memory httpx client.
- **Operational caveat (not isolation):** the session pool is keyed by header and held on the
  long-lived toolset, so it grows with distinct live bearers (and each token refresh mints a new
  key). At 100s of users this needs a TTL / eviction or periodic toolset/session cleanup —
  tracked as an ops item, not a correctness one. The pooled session also keeps a bearer in
  memory longer than one request (until eviction); still no cred at rest on disk/db.
- **Affinity spike (§6)** is now a *confirmation*, not a gate: ADK already gives each bearer its
  own session, so we mainly verify the Atlassian server is happy with concurrent per-user
  sessions and that identity resolves correctly per token.

### Conversation isolation + binding (Surface 2)

- ADK `Runner.run_async(user_id=user_id, session_id=conversation_id, …)` keys history by
  `(app, user_id, conversation_id)` → isolates conversations, including two for the same user.
- **Binding gate:** on conversation creation, resolve the token's true identity
  (`atlassianUserInfo.accountId`) and bind `conversation_id → accountId`. On each later turn,
  assert the presented bearer resolves to the same `accountId`; **reject mismatches**
  (anti-hijack). Audit-log `(conversation_id, user_id, token-fingerprint-hash)` — hash only.

### Autoscale shape

Stateless tasks + a **shared** SessionService → any task serves any turn; no sticky routing.
Each request is self-contained `(bearer, user_id, conversation_id)`. Conversation history
persists (bearer-scrubbed); the bearer is re-supplied by the client every request.

## 5. The trap (do not do this)

Never use `user_id`/`conversation_id` as a **key to store or retrieve a token** server-side —
that re-introduces creds-at-rest. The IDs **correlate and validate**; the client always
supplies the token fresh.

## 6. Open gating unknown — the affinity spike

Does `https://mcp.atlassian.com/v1/mcp` honor a **different Authorization bearer per POST** on
one MCP session, or **bind the session to the first user**?
- Honors per-request → shared toolset + `ForwardedBearerAuth`. Cheap.
- Binds to user → per-request ephemeral MCP session. Structural but still fine.

Run this **first** (Level 0, no ADK/A2A, two real tokens). Until it's answered, default the
implementation to per-request sessions.

## 7. Test plan (A2A not required — add A2A last)

Cross-talk lives **below** A2A (bearer scoping + MCP session), so test at the cheapest layer.

- **L0 — affinity spike** (no agent): the §6 question.
- **L1 — in-process concurrency** (no HTTP): `asyncio.gather` two+ bearers, assert no crossing.
- **L2 — local HTTP** (no A2A): FastAPI + concurrent two-token load script.
- **L3 — Fargate behind ALB**: same endpoint, scripted harness; optional autoscale slice.
- **L4 — A2A**: swap transport only, once isolation is already proven.

**Probes:** `atlassianUserInfo` (identity) + one **permission-scoped read** (capability — a
resource only that user can see). **Ground-truth** map `bearer → accountId → resource`.

**Cases the harness must prove:**
1. Multiple users per task, concurrent → no credential (S1) or history (S2) crossing.
2. Multiple conversations per user (A1, A2) → no history bleed; both use A's token.
3. Sequential residual → A finishes, B connects to the same warm task → B must be B.
4. Hijack-rejection → B's token + `conversation_id=A1` → **rejected**, not served A's history.

**Pass criterion:** across many concurrent *and* sequential trials, every response carries only
the calling bearer's identity/capability — zero A-in-B, zero B-in-A. Test users must have
**unequal permissions** or a leak is undetectable.

Humans (3 machines / 3 users) are the realism + demo layer and catch coarse bugs; the
**scripted harness with ground-truth assertions is the rigorous proof**.

## 8. Model

Haiku via `BEDROCK_MODEL_ID` (cheaper/faster → more overlap per test run). Confirm the exact
Bedrock cross-region inference-profile id for Haiku 4.5 before trusting it (Phase 1 was bitten
by a bad model id — DEC-1). Assert on **tool output**, not model prose (Haiku may flub
narration).

## 9. Out of scope (Phase 2)

- A2A protocol surface (AgentCard, `auth-required` task lifecycle) — designed for, added last (L4).
- A Cred-1 (caller-identity) check on the agent endpoint itself — for production this stops the
  endpoint being an open relay to Atlassian/Bedrock for any valid-token holder; **not POC-blocking**.
- Per-turn identity re-verification cost tuning (opaque tokens need a userinfo round-trip).
