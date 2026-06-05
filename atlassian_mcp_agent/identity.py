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
import os
from collections import OrderedDict
from typing import Any, cast

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from .agent import ATLASSIAN_MCP_URL, FIRST_REQUEST_TIMEOUT_S

logger = logging.getLogger("atlassian_mcp_agent.identity")


class IdentityError(Exception):
    """The forwarded token could not be resolved to an Atlassian identity (treat as 401)."""


class BindingMismatch(Exception):
    """The presented token resolves to a different accountId than the conversation is bound to."""


def fingerprint(token: str) -> str:
    """Short, non-reversible token fingerprint for audit logs. NEVER the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _cache_key(token: str) -> str:
    """Full-width sha256 of the token, used only as an internal cache key.

    Distinct from `fingerprint` (16 hex / 64 bits, for logs): the cache key MUST be
    full width so two different tokens can never collide onto one cached accountId — a
    truncated key would (astronomically rarely) route one user onto another's identity.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def check_binding(bound_account_id: str | None, resolved_account_id: str) -> None:
    """Anti-hijack: a conversation bound to one accountId must not accept another's token.

    No-op when the conversation is new (bound is None) or matches. Raises otherwise.
    """
    if bound_account_id is not None and bound_account_id != resolved_account_id:
        raise BindingMismatch(
            f"token resolves to {resolved_account_id!r}, conversation bound to {bound_account_id!r}"
        )


def account_id_from_result(result: CallToolResult) -> str | None:
    """The Atlassian accountId from an atlassianUserInfo CallToolResult, or None if absent.

    The single canonical parser — shared by the request layer (here), the local client, and
    the spikes — so the atlassianUserInfo response shape is understood in exactly one place.
    """
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
        timeout=httpx.Timeout(FIRST_REQUEST_TIMEOUT_S),  # same budget as a real tool call
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
    acct = account_id_from_result(result)
    if not acct:
        raise IdentityError("atlassianUserInfo returned no accountId for the forwarded token")
    return acct


# Resolution cache keyed by the full-width token hash (perf only; identity is authoritative
# from Atlassian). A refreshed token (new value) re-resolves and should map to the same
# accountId. Bounded LRU so a long-lived process serving many users / token-refreshes can't
# grow without bound; override the cap with IDENTITY_CACHE_MAX.
_CACHE_MAX = max(1, int(os.environ.get("IDENTITY_CACHE_MAX", "10000")))
_cache: OrderedDict[str, str] = OrderedDict()
# In-flight resolutions, keyed the same way, so N concurrent first-hits for one token share a
# single MCP round-trip instead of stampeding Atlassian (thundering herd).
_inflight: dict[str, asyncio.Future[str]] = {}
_lock = asyncio.Lock()


async def resolve_account_id(token: str) -> tuple[str, str]:
    """Return (fingerprint, accountId) for a forwarded token, cached by full-hash key.

    Distinct tokens resolve concurrently (the MCP round-trip is awaited outside the lock).
    Concurrent first-hits for the SAME token coalesce onto one in-flight resolution. Raises
    IdentityError if the token can't be resolved.
    """
    fp = fingerprint(token)  # short, logs/audit only
    key = _cache_key(token)  # full-width, never collides distinct tokens

    async with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)  # LRU touch
            return fp, hit
        inflight = _inflight.get(key)
        if inflight is None:
            inflight = asyncio.ensure_future(_resolve_via_mcp(token))
            _inflight[key] = inflight

    try:
        account_id = await inflight
    finally:
        async with _lock:
            if _inflight.get(key) is inflight:  # first awaiter to finish clears it (once)
                del _inflight[key]

    async with _lock:
        existing = _cache.get(key)
        if existing is not None:
            return fp, existing
        _cache[key] = account_id
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)  # evict least-recently-used
    return fp, account_id
