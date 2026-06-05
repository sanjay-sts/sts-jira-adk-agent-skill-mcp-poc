"""Phase 2 — stateless, multi-user *forwarding* agent (no credential at rest).

Unlike Phase 1's `agent.py` (single-user; the agent itself runs the OAuth dance and
caches a token on disk), this module assumes a **remote, multi-user** deployment where:

  • the CLIENT does all OAuth (DCR + PKCE + consent) on its own device and
  • forwards a valid Atlassian access token on EVERY request.

The agent stores **no** credential. The forwarded bearer lives only in a request-scoped
`contextvars.ContextVar`, set by the request handler (`server.py`) and read per tool call
by `header_provider`. ADK 2.1's `MCPSessionManager` pools MCP sessions by a hash of the
headers, so each distinct bearer gets its own isolated session — User A's call can never be
routed onto User B's session (Surface 1 isolation is structural; see docs/phase-2-design.md).

What is deliberately NOT here (removed vs. Phase 1): `OAuthClientProvider`, `FileTokenStorage`,
the loopback OAuth callback server, and any module-global token/auth object.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Generator

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

# Reuse the Phase-1 constants/model so the two paths can't drift. Importing agent.py is
# side-effect-free w.r.t. network/disk (OAuth provider construction is lazy; the MCP session
# isn't opened until a tool call). We do NOT use agent.py's single-user toolset/root_agent.
from .agent import (
    AGENT_INSTRUCTION,
    ATLASSIAN_MCP_URL,
    FIRST_REQUEST_TIMEOUT_S,
    PHASE_1_TOOL_FILTER,
    log_token_usage,
    model,
)

logger = logging.getLogger("atlassian_mcp_agent.forwarding")

# ───────────────────────── Request-scoped forwarded bearer ──────────────────────────
# The Atlassian access token for the CURRENT request. Set by the request handler before
# invoking the Runner; read per tool call by `_header_provider`. NEVER persisted: not on
# disk, not in ADK session state, not in logs (Authorization is redacted by
# logging_redaction). Being a ContextVar, it is isolated per asyncio task, so concurrent
# requests on the same process cannot read each other's bearer.
_bearer: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "atlassian_bearer", default=None
)


@contextlib.contextmanager
def bearer_scope(token: str) -> Generator[None, None, None]:
    """Bind the forwarded bearer for the duration of one request, then clear it.

    Set BEFORE iterating the Runner so any child asyncio tasks ADK spawns for tool calls
    inherit this context (tasks copy the current context at creation time).
    """
    reset = _bearer.set(token)
    try:
        yield
    finally:
        _bearer.reset(reset)


def current_bearer() -> str | None:
    """The bearer bound to the current request context (None outside a bearer_scope)."""
    return _bearer.get()


def header_provider(ctx: ReadonlyContext | None = None) -> dict[str, str]:
    """Per-tool-call hook: inject the request's forwarded bearer as the MCP auth header.

    ADK pools MCP sessions keyed by a hash of these headers, so each distinct bearer gets
    its own isolated session (no cross-talk). Fails CLOSED if no bearer is in context — a
    request that reaches a tool call without a forwarded token is refused, never silently
    served with someone else's session.
    """
    del ctx  # identity/routing checks live in the request layer (Surface 2); not needed here
    token = _bearer.get()
    if not token:
        raise RuntimeError(
            "No forwarded Atlassian bearer in request context — refusing the MCP call."
        )
    return {"Authorization": f"Bearer {token}"}


def build_toolset() -> McpToolset:
    """A Streamable-HTTP MCP toolset whose auth is supplied per request via header_provider.

    No `httpx_client_factory` and no OAuth provider — the default MCP http client is used and
    the Authorization header comes from `_header_provider` on every tool call.
    """
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=ATLASSIAN_MCP_URL,
            timeout=FIRST_REQUEST_TIMEOUT_S,
            sse_read_timeout=FIRST_REQUEST_TIMEOUT_S,
        ),
        tool_filter=PHASE_1_TOOL_FILTER,
        header_provider=header_provider,
    )


def build_agent() -> Agent:
    """Construct the multi-user forwarding agent (one per process; safe to share)."""
    return Agent(
        model=model,
        name="atlassian_mcp_agent_mt",
        instruction=AGENT_INSTRUCTION,
        tools=[build_toolset()],
        after_model_callback=log_token_usage,
    )
