"""Phase 2 Surface-2 unit tests — fingerprinting + the anti-hijack binding check.

Pure logic only (no MCP/network): identity *resolution* hits Atlassian and is covered by the
live harness; here we pin the audit fingerprint and the binding rule the request layer enforces.
"""

from __future__ import annotations

import pytest

from atlassian_mcp_agent.identity import BindingMismatch, check_binding, fingerprint


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
