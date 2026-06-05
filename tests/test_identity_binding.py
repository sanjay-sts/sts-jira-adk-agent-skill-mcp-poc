"""Phase 2 Surface-2 unit tests — fingerprinting + the anti-hijack binding check.

Pure logic only (no MCP/network): identity *resolution* hits Atlassian and is covered by the
live harness; here we pin the audit fingerprint and the binding rule the request layer enforces.
"""

from __future__ import annotations

import asyncio

import pytest

from atlassian_mcp_agent import identity
from atlassian_mcp_agent.identity import (
    BindingMismatch,
    IdentityError,
    check_binding,
    fingerprint,
    resolve_account_id,
)


def test_fingerprint_is_stable_and_not_the_token() -> None:
    tok = "super-secret-access-token-value"
    fp = fingerprint(tok)
    assert fp == fingerprint(tok)          # stable
    assert tok not in fp                    # never leaks the token
    assert len(fp) == 16                    # short, for logs


def test_fingerprint_differs_per_token() -> None:
    assert fingerprint("token-a") != fingerprint("token-b")


def test_binding_allows_new_conversation() -> None:
    check_binding(None, "acct-123")  # bound is None → no-op, no raise


def test_binding_allows_same_account() -> None:
    check_binding("acct-123", "acct-123")  # same owner → no raise


def test_binding_rejects_different_account() -> None:
    with pytest.raises(BindingMismatch):
        check_binding("acct-ALICE", "acct-BOB")  # B's token on A's conversation


# ── resolution cache: single-flight, full-width key, LRU bound (network monkeypatched) ──


@pytest.fixture(autouse=True)
def _clear_identity_cache() -> None:
    identity._cache.clear()
    identity._inflight.clear()


@pytest.mark.asyncio
async def test_concurrent_same_token_resolves_once() -> None:
    """N concurrent first-hits for one token share a single MCP round-trip (no stampede)."""
    calls = 0

    async def fake_resolve(token: str) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # widen the race window
        return "acct-alice"

    identity._resolve_via_mcp = fake_resolve  # type: ignore[assignment]
    results = await asyncio.gather(*[resolve_account_id("tok-A") for _ in range(20)])

    assert calls == 1  # coalesced
    assert {acct for _fp, acct in results} == {"acct-alice"}


@pytest.mark.asyncio
async def test_distinct_tokens_get_distinct_identities() -> None:
    async def fake_resolve(token: str) -> str:
        return f"acct-for-{token}"

    identity._resolve_via_mcp = fake_resolve  # type: ignore[assignment]
    (_fa, a), (_fb, b) = await resolve_account_id("tok-A"), await resolve_account_id("tok-B")
    assert a == "acct-for-tok-A"
    assert b == "acct-for-tok-B"


@pytest.mark.asyncio
async def test_failed_resolution_is_not_cached_and_retries() -> None:
    """A resolution failure must clear the in-flight slot and not poison the cache."""
    attempts = 0

    async def flaky_resolve(token: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IdentityError("boom")
        return "acct-eventually"

    identity._resolve_via_mcp = flaky_resolve  # type: ignore[assignment]

    with pytest.raises(IdentityError):
        await resolve_account_id("tok-X")
    assert not identity._cache  # failure not cached
    assert not identity._inflight  # in-flight slot cleared

    _fp, acct = await resolve_account_id("tok-X")  # retry succeeds
    assert acct == "acct-eventually"
    assert attempts == 2


@pytest.mark.asyncio
async def test_cache_is_lru_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_CACHE_MAX", 3)

    async def fake_resolve(token: str) -> str:
        return f"acct-{token}"

    identity._resolve_via_mcp = fake_resolve  # type: ignore[assignment]
    for i in range(10):
        await resolve_account_id(f"tok-{i}")
    assert len(identity._cache) <= 3  # bounded, oldest evicted
