"""Phase 1 unit test: FileTokenStorage round-trips OAuth model objects through JSON.

This is the only test Phase 1 needs (VISION §8). The live OAuth dance is covered by the
manual smoke-test checklist in the README, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from atlassian_mcp_agent.storage import FileTokenStorage


@pytest.fixture
def storage(tmp_path: Path) -> FileTokenStorage:
    return FileTokenStorage(root=str(tmp_path / "atlassian-mcp"))


async def test_tokens_round_trip(storage: FileTokenStorage) -> None:
    assert await storage.get_tokens() is None  # nothing persisted yet

    token = OAuthToken(
        access_token="dummy-access-token",
        token_type="Bearer",
        expires_in=3600,
        scope="read:jira-work write:jira-work read:page:confluence",
    )
    await storage.set_tokens(token)

    loaded = await storage.get_tokens()
    assert loaded is not None
    assert loaded.access_token == token.access_token
    assert loaded.scope == token.scope  # scope is the DEC-15 ground-truth field


async def test_client_info_round_trip(storage: FileTokenStorage) -> None:
    assert await storage.get_client_info() is None

    info = OAuthClientInformationFull(
        client_id="client-abc-123",
        redirect_uris=[AnyUrl("http://127.0.0.1:3030/callback")],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    await storage.set_client_info(info)

    loaded = await storage.get_client_info()
    assert loaded is not None
    assert loaded.client_id == "client-abc-123"
    assert str(loaded.redirect_uris[0]) == "http://127.0.0.1:3030/callback"


async def test_token_file_written(storage: FileTokenStorage) -> None:
    assert not storage.t_file.exists()
    await storage.set_tokens(OAuthToken(access_token="x", token_type="Bearer"))
    assert storage.t_file.exists()
