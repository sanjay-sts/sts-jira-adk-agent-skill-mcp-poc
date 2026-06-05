"""Phase 2 isolation unit test — the forwarded bearer never crosses concurrent tasks.

This is the in-process (Level 1) proof of the core non-crossing guarantee, with no network
and no real tokens: many concurrent asyncio tasks each bind a DISTINCT bearer via
`bearer_scope`, interleave via `await`, and must each read back ONLY their own bearer through
the `_header_provider` hook. A single crossed read fails the test.

It does not exercise the MCP server (that's the affinity spike + the live harness); it pins the
contextvar mechanism that `server.py` relies on.
"""

from __future__ import annotations

import asyncio

import pytest

from atlassian_mcp_agent.forwarding import bearer_scope, current_bearer, header_provider


def _token_in_header() -> str:
    return header_provider()["Authorization"].removeprefix("Bearer ")


@pytest.mark.asyncio
async def test_concurrent_bearers_do_not_cross() -> None:
    async def worker(i: int) -> tuple[int, str, str]:
        token = f"token-user-{i}"
        with bearer_scope(token):
            # Force interleaving so a shared/global would get clobbered by another task.
            await asyncio.sleep(0.01)
            seen_via_header = _token_in_header()
            await asyncio.sleep(0.01)
            seen_via_getter = current_bearer() or ""
        return i, seen_via_header, seen_via_getter

    results = await asyncio.gather(*(worker(i) for i in range(50)))

    for i, via_header, via_getter in results:
        expected = f"token-user-{i}"
        assert via_header == expected, f"CROSS-TALK: task {i} saw {via_header!r} in header"
        assert via_getter == expected, f"CROSS-TALK: task {i} saw {via_getter!r} via getter"


@pytest.mark.asyncio
async def test_bearer_cleared_after_scope() -> None:
    assert current_bearer() is None
    with bearer_scope("temp"):
        assert current_bearer() == "temp"
    assert current_bearer() is None


def test_header_provider_fails_closed_without_bearer() -> None:
    with pytest.raises(RuntimeError, match="No forwarded Atlassian bearer"):
        header_provider()
