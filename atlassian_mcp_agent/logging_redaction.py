"""Redact Authorization headers / bearer tokens from log records (VISION §5.7).

Defense-in-depth: we never log tokens ourselves and pin httpx/httpcore to WARNING,
but this guarantees a stray DEBUG line can't leak a credential. Kept in its own module
so the (security-critical) redaction logic can be unit-tested without importing google-adk.
"""

from __future__ import annotations

import logging
import os
import re

# Match `Authorization: <value>` / `authorization=<value>` (optionally quoted), capturing
# the ENTIRE value — which may be `Bearer <jwt>`, `Basic <b64>`, contain spaces, etc. — up
# to the next quote / comma / brace / newline. Capturing the whole value (not just the first
# word) is what makes "Authorization: Bearer <token>" fully redacted.
_AUTH_KV = re.compile(r'(?i)(authorization["\']?\s*[:=]\s*["\']?)([^"\',}\r\n]+)')
# Backstop for a bare bearer token logged without an "Authorization" key.
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")

_REDACTED = "***REDACTED***"


class RedactAuthFilter(logging.Filter):
    """A logging.Filter that masks credentials in any record's rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - never let logging crash the agent
            return True
        redacted = _AUTH_KV.sub(rf"\1{_REDACTED}", message)
        redacted = _BEARER.sub(f"Bearer {_REDACTED}", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def install_log_redaction(logger_name: str = "atlassian_mcp_agent") -> None:
    """Attach the redaction filter to the app + HTTP loggers and quiet HTTP wire logging."""
    auth_filter = RedactAuthFilter()
    for name in ("httpx", "httpcore", logger_name):
        logging.getLogger(name).addFilter(auth_filter)
    # Keep HTTP client wire logging quiet so headers never reach the logs in the first place.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger(logger_name).setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
