"""Phase 2 — Surface 2 (conversation-history isolation): identity resolution + binding.

The forwarded bearer is the ONLY trustworthy statement of who is calling — never the client's
claimed user_id. This module resolves a token to its real Atlassian accountId (via
atlassianUserInfo, i.e. Atlassian validates the token and tells us the identity), so the
request layer can:

  • key the ADK session by the cryptographically-established accountId (so one user's
    conversation namespace is structurally separate from another's — a hijacker presenting a
    valid-but-different token lands in THEIR OWN partition, never the victim's), and
  • bind conversation -> accountId and reject a turn whose token resolves to a different
    accountId (defense-in-depth + an explicit audit signal).

Resolution is cached per token fingerprint so repeated turns / load tests don't pay an MCP
round-trip each time. The token itself is never logged or stored — only a short fingerprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, cast

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from .agent import ATLASSIAN_MCP_URL

logger = logging.getLogger("atlassian_mcp_agent.identity")


class IdentityError(Exception):
    """The forwarded token could not be resolved to an Atlassian identity (treat as 401)."""


class BindingMismatch(Exception):
    """The presented token resolves to a different accountId than the conversation is bound to."""


def fingerprint(token: str) -> str:
    """Short, non-reversible token fingerprint for audit logs. NEVER the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def check_binding(bound_account_id: str | None, resolved_account_id: str) -> None:
    """Anti-hijack: a conversation bound to one accountId must not accept another's token.

    No-op when the conversation is new (bound is None) or matches. Raises otherwise.
    """
    if bound_account_id is not None and bound_account_id != resolved_account_id:
        raise BindingMismatch(
            f"token resolves to {resolved_account_id!r}, conversation bound to {bound_account_id!r}"
        )


def _account_id(result: CallToolResult) -> str | None:
    parts = [getattr(b, "text", "") or "" for b in result.content]
    blob = "\n".join(p for p in parts if p)
    try:
        loaded: Any = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    data = cast("dict[str, Any]", loaded)
    account = data.get("account_id") or data.get("accountId")
    return str(account) if account else None


async def _resolve_via_mcp(token: str) -> str:
    # New mcp client API: pass a pre-built httpx client carrying the forwarded bearer.
    http_client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
    )
    async with http_client:
        async with streamable_http_client(ATLASSIAN_MCP_URL, http_client=http_client) as (
            read,
            write,
            _sid,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("atlassianUserInfo", {})
    acct = _account_id(result)
    if not acct:
        raise IdentityError("atlassianUserInfo returned no accountId for the forwarded token")
    return acct


# Resolution cache keyed by token fingerprint (perf only; identity is authoritative from
# Atlassian). A refreshed token (new value) re-resolves and should map to the same accountId.
# TODO(ops): add a TTL / max-size eviction for long-lived processes with many users.
_cache: dict[str, str] = {}
_lock = asyncio.Lock()


async def resolve_account_id(token: str) -> tuple[str, str]:
    """Return (fingerprint, accountId) for a forwarded token, cached by fingerprint.

    Distinct tokens resolve concurrently (the MCP round-trip is outside the lock); the lock
    only guards the tiny cache write. Raises IdentityError if the token can't be resolved.
    """
    fp = fingerprint(token)
    hit = _cache.get(fp)
    if hit is not None:
        return fp, hit
    account_id = await _resolve_via_mcp(token)
    async with _lock:
        _cache.setdefault(fp, account_id)
    return fp, _cache[fp]
