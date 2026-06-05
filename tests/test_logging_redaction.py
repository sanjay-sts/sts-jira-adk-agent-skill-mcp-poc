"""Unit tests for the Authorization / bearer-token log redaction filter (VISION §5.7).

These would have caught the original bug where `Authorization: Bearer <token>` only had
the word "Bearer" redacted, leaking the actual token.
"""

from __future__ import annotations

import logging

from atlassian_mcp_agent.logging_redaction import RedactAuthFilter


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redacts_full_bearer_token_in_authorization_header() -> None:
    record = _record("sending Authorization: Bearer abc123.def-456_secret")
    assert RedactAuthFilter().filter(record) is True
    out = record.getMessage()
    assert "abc123" not in out
    assert "secret" not in out
    assert "REDACTED" in out


def test_redacts_basic_auth_value() -> None:
    record = _record("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    RedactAuthFilter().filter(record)
    out = record.getMessage()
    assert "dXNlcjpwYXNzd29yZA" not in out
    assert "REDACTED" in out


def test_redacts_token_rendered_from_args() -> None:
    record = _record("request headers=%s", {"Authorization": "Bearer topsecrettoken"})
    RedactAuthFilter().filter(record)
    assert "topsecrettoken" not in record.getMessage()


def test_redacts_standalone_bearer_token() -> None:
    record = _record("cached token is Bearer zzz111.yyy-222")
    RedactAuthFilter().filter(record)
    assert "zzz111" not in record.getMessage()


def test_passes_through_message_without_credentials() -> None:
    record = _record("tool call succeeded for KAN-1")
    assert RedactAuthFilter().filter(record) is True
    assert record.getMessage() == "tool call succeeded for KAN-1"
