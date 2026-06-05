"""Level-0 affinity spike — does the Atlassian MCP server honor a per-request bearer?

No ADK, no A2A, no agent. Just the raw MCP Streamable-HTTP client and two real
Atlassian access tokens. Answers the one question that decides the Phase 2 wiring
(docs/phase-2-design.md §6):

  Q: On ONE MCP session, does mcp.atlassian.com honor a DIFFERENT Authorization
     bearer per POST, or is identity bound to the session (Mcp-Session-Id) at
     initialize time?

     • honored per-request  -> a single shared McpToolset + a contextvar-reading
                               httpx.Auth is safe (cheap path).
     • bound to the session -> we MUST build a per-request ephemeral MCP session
                               (structural, but still fine).

It runs two checks:

  TEST 1 (isolation baseline): two SEPARATE sessions, one per token, called
          concurrently. Each must report its OWN identity. This is the safe
          per-request-session model; it should always pass.

  TEST 2 (shared-session bearer swap): ONE session. Initialize + call while the
          contextvar holds token A (expect A's identity), then flip the contextvar
          to token B and call again on the SAME session. If the second call reports
          B  -> per-request bearer is honored (cheap path is viable).
          reports A  -> session is identity-bound (per-request session required).

Usage:
    export ATLAS_TOKEN_A="<user A access token>"
    export ATLAS_TOKEN_B="<user B access token>"   # a DIFFERENT user
    uv run python spikes/affinity_spike.py

Tokens expire in ~1h — grab them fresh. Each user does the OAuth dance once (via the
Phase-1 agent or any client) and you read access_token out of ~/.atlassian-mcp/token.json
on that machine. Use two users with DIFFERENT permissions so a leak is detectable.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"

# The bearer the next outbound MCP POST should carry. Set per "request" by the caller;
# read inside the httpx auth hook at send time — exactly the Phase 2 mechanism.
_bearer: contextvars.ContextVar[str | None] = contextvars.ContextVar("bearer", default=None)


class ForwardedBearerAuth(httpx.Auth):
    """Inject Authorization: Bearer <contextvar> on each request, read at send time."""

    def auth_flow(self, request: httpx.Request):
        tok = _bearer.get()
        if not tok:
            raise RuntimeError("no bearer set in context")
        request.headers["Authorization"] = f"Bearer {tok}"
        yield request


def _factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    # Discard any incoming auth; substitute our contextvar-reading hook (mirrors agent wiring).
    del auth
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(60.0),
        auth=ForwardedBearerAuth(),
        follow_redirects=True,
    )


def _identity(call_result) -> str:
    """Best-effort extract a stable identity string from an atlassianUserInfo result."""
    try:
        parts = []
        for block in call_result.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        blob = "\n".join(parts)
        try:
            data = json.loads(blob)
            return (
                data.get("account_id")
                or data.get("accountId")
                or data.get("email")
                or data.get("name")
                or blob[:200]
            )
        except json.JSONDecodeError:
            return blob[:200]
    except Exception as e:
        return f"<unparseable: {e!r}>"


async def whoami(session: ClientSession) -> str:
    res = await session.call_tool("atlassianUserInfo", {})
    return _identity(res)


async def one_session_identity(token: str, label: str) -> str:
    """Open a fresh session bound to `token` and report its identity."""
    _bearer.set(token)
    async with streamablehttp_client(
        ATLASSIAN_MCP_URL, httpx_client_factory=_factory
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            ident = await whoami(session)
            print(f"  [{label}] session identity -> {ident}")
            return ident


async def test1_separate_sessions(tok_a: str, tok_b: str) -> bool:
    print("\nTEST 1 — two SEPARATE sessions, concurrent (per-request-session model):")
    # Each coroutine runs in its own context copy, so the contextvar can't bleed.
    id_a, id_b = await asyncio.gather(
        asyncio.create_task(_in_context(one_session_identity, tok_a, "A")),
        asyncio.create_task(_in_context(one_session_identity, tok_b, "B")),
    )
    ok = id_a != id_b and id_a and id_b
    print(f"  => {'PASS' if ok else 'FAIL'}: identities {'differ' if ok else 'CROSSED'} "
          f"(A={id_a!r}, B={id_b!r})")
    return bool(ok)


async def _in_context(fn, *args):
    # Run fn in a fresh copied context so _bearer.set() is isolated to this task.
    return await fn(*args)


async def test2_shared_session_swap(tok_a: str, tok_b: str) -> str:
    print("\nTEST 2 — ONE shared session, swap bearer A->B between calls:")
    _bearer.set(tok_a)
    async with streamablehttp_client(
        ATLASSIAN_MCP_URL, httpx_client_factory=_factory
    ) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            sid = None
            try:
                sid = get_session_id()
            except Exception:
                pass
            first = await whoami(session)
            print(f"  call#1 (bearer=A, session={sid}) -> {first}")
            _bearer.set(tok_b)  # flip the bearer; SAME session
            second = await whoami(session)
            print(f"  call#2 (bearer=B, same session)   -> {second}")
            if second == first:
                verdict = "SESSION-BOUND: per-request bearer IGNORED -> need per-request sessions"
            else:
                verdict = "PER-REQUEST HONORED: shared toolset + contextvar auth is viable"
            print(f"  => {verdict}")
            return verdict


async def main() -> int:
    tok_a = os.environ.get("ATLAS_TOKEN_A")
    tok_b = os.environ.get("ATLAS_TOKEN_B")
    if not tok_a or not tok_b:
        print("ERROR: set ATLAS_TOKEN_A and ATLAS_TOKEN_B (two DIFFERENT users' "
              "Atlassian access tokens).", file=sys.stderr)
        return 2
    if tok_a == tok_b:
        print("ERROR: the two tokens are identical — use two different users.", file=sys.stderr)
        return 2

    print(f"Affinity spike against {ATLASSIAN_MCP_URL}")
    t1 = await test1_separate_sessions(tok_a, tok_b)
    verdict = await test2_shared_session_swap(tok_a, tok_b)

    print("\n──────── SUMMARY ────────")
    print(f"Separate-session isolation : {'PASS' if t1 else 'FAIL'}")
    print(f"Shared-session swap verdict: {verdict}")
    print("\nWiring implication: if the swap is SESSION-BOUND, Phase 2 builds a per-request "
          "ephemeral MCP session (default). If PER-REQUEST HONORED, a single shared toolset "
          "with the contextvar auth above is enough.")
    return 0 if t1 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
