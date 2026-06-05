"""File-based OAuth token + DCR client storage for the Atlassian MCP agent (Phase 1).

Kept in its own module (not agent.py) so the unit test can import it without pulling in
google-adk / litellm / boto3. Single-user by design; Phase 2 replaces this with a keyed
per-user store.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger("atlassian_mcp_agent.storage")


def chmod_600(path: pathlib.Path) -> None:
    """Restrict file perms to owner read/write. No-op on Windows (chmod is POSIX)."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


class FileTokenStorage(TokenStorage):
    """Persist the OAuth token and DCR client registration as JSON (0600 on POSIX)."""

    def __init__(self, root: str = "~/.atlassian-mcp") -> None:
        # No filesystem side effects on construction; the directory is created lazily on
        # the first write (so merely importing the agent doesn't touch disk).
        self.dir = pathlib.Path(os.path.expanduser(root))
        self.t_file = self.dir / "token.json"
        self.c_file = self.dir / "client.json"

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    async def get_tokens(self) -> OAuthToken | None:
        if self.t_file.exists():
            return OAuthToken(**json.loads(self.t_file.read_text()))
        return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        # Log granted scopes (NOT the token) on first persist — ground truth (DEC-15).
        if not self.t_file.exists():
            logger.info(
                "First token persisted. Granted scope: %s. Refresh token present: %s.",
                getattr(tokens, "scope", None),
                bool(getattr(tokens, "refresh_token", None)),
            )
        self._ensure_dir()
        self.t_file.write_text(tokens.model_dump_json())
        chmod_600(self.t_file)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if self.c_file.exists():
            return OAuthClientInformationFull(**json.loads(self.c_file.read_text()))
        return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._ensure_dir()
        self.c_file.write_text(client_info.model_dump_json())
        chmod_600(self.c_file)
