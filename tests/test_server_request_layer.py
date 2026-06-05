"""Phase 2 request-layer tests — healthz + fail-closed auth, no network/Bedrock needed.

Exercises the FastAPI surface up to (but not into) the agent run: a request without a valid
`Authorization: Bearer` header must be rejected with 401 BEFORE any model or MCP call. The
happy path (which drives the real Runner -> Bedrock -> Atlassian MCP) is covered by the live
isolation harness, not here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlassian_mcp_agent.server import app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_chat_without_authorization_is_rejected() -> None:
    r = client.post("/chat", json={"user_id": "alice", "message": "hi"})
    assert r.status_code == 401


def test_chat_with_malformed_authorization_is_rejected() -> None:
    r = client.post(
        "/chat",
        json={"user_id": "alice", "message": "hi"},
        headers={"Authorization": "Token abc123"},  # not "Bearer …"
    )
    assert r.status_code == 401


def test_chat_with_empty_bearer_is_rejected() -> None:
    r = client.post(
        "/chat",
        json={"user_id": "alice", "message": "hi"},
        headers={"Authorization": "Bearer "},
    )
    assert r.status_code == 401
