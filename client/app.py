"""Per-machine local frontend client — owns OAuth locally, forwards the bearer.

This is the human-facing client for the multi-user test (one instance per machine / per user).
It deliberately keeps the agent credential-free by doing all OAuth HERE:

  1. Mints/refreshes the Atlassian access token on THIS machine, reusing Phase 1's
     OAuthClientProvider (DCR + PKCE + consent). The token rests only on this machine.
  2. Serves a single chat page at localhost.
  3. /send forwards {message, conversation_id} to the REMOTE agent's /chat with
     Authorization: Bearer <local token> + user_id (the Atlassian accountId by default).
  4. On a remote 401 (the forwarded token was rejected/expired), refreshes locally and
     retries once.

The remote agent (atlassian_mcp_agent.server) never sees a token at rest — only the
forwarded bearer, per request.

Run (on each user's machine):
    export REMOTE_AGENT_URL=http://<agent-host>:8080     # the FastAPI forwarding agent
    export CLIENT_USER_ID=alice                          # optional; default = Atlassian accountId
    aws sso login --profile <profile>                    # only the AGENT needs Bedrock, not this
    uv run uvicorn client.app:app --host 127.0.0.1 --port 9090
    # open http://127.0.0.1:9090, first message triggers the Atlassian consent browser dance
"""

from __future__ import annotations

import logging
import os
import pathlib
import uuid

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from pydantic import BaseModel

# Reuse Phase 1's configured OAuth provider + endpoint as the LOCAL token-minter.
from atlassian_mcp_agent.agent import ATLASSIAN_MCP_URL, oauth
from atlassian_mcp_agent.identity import account_id_from_result
from atlassian_mcp_agent.storage import FileTokenStorage

logger = logging.getLogger("atlassian_mcp_agent.client")

REMOTE_AGENT_URL = os.environ.get("REMOTE_AGENT_URL", "http://127.0.0.1:8080").rstrip("/")
_CONFIGURED_USER_ID = os.environ.get("CLIENT_USER_ID")  # optional override

_storage = FileTokenStorage()
_STATIC = pathlib.Path(__file__).parent / "static"

# In-memory cache of this machine's token + identity. The token is the user's own, on the
# user's own machine — allowed. Cleared and re-minted on a remote 401.
_cache: dict[str, str | None] = {"access_token": None, "user_id": None}

app = FastAPI(title="Atlassian agent — local client")


class SendRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class SendResponse(BaseModel):
    user_id: str
    conversation_id: str
    response: str


def _identity(call_result: CallToolResult) -> str:
    """This user's Atlassian accountId from an atlassianUserInfo result ('unknown' if absent)."""
    return account_id_from_result(call_result) or "unknown"


async def _mint_token_and_identity() -> tuple[str, str]:
    """Run/refresh the Atlassian OAuth via the Phase-1 provider and resolve this user's identity.

    Opening an MCP session through `oauth` triggers the DCR/PKCE/consent dance on first use
    (and a silent refresh thereafter), populating ~/.atlassian-mcp/token.json. We then read the
    access token back and resolve the accountId from atlassianUserInfo.
    """
    # New mcp client API: pass a pre-built httpx client; `oauth` (Phase-1 provider) is its auth.
    oauth_client = httpx.AsyncClient(
        auth=oauth, timeout=httpx.Timeout(300.0), follow_redirects=True
    )
    async with oauth_client:
        async with streamable_http_client(ATLASSIAN_MCP_URL, http_client=oauth_client) as (
            read,
            write,
            _sid,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                info = await session.call_tool("atlassianUserInfo", {})
    tokens = await _storage.get_tokens()
    if tokens is None or not tokens.access_token:
        raise RuntimeError("OAuth completed but no access token was stored.")
    return tokens.access_token, _identity(info)


async def _get_token_and_user(force: bool = False) -> tuple[str, str]:
    if force or not _cache["access_token"]:
        token, account_id = await _mint_token_and_identity()
        _cache["access_token"] = token
        _cache["user_id"] = _CONFIGURED_USER_ID or account_id
    return _cache["access_token"], _cache["user_id"]  # type: ignore[return-value]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.post("/send", response_model=SendResponse)
async def send(req: SendRequest) -> SendResponse:
    conversation_id = req.conversation_id or uuid.uuid4().hex
    token, user_id = await _get_token_and_user()

    payload = {"user_id": user_id, "conversation_id": conversation_id, "message": req.message}
    async with httpx.AsyncClient(timeout=300.0) as http:
        resp = await http.post(
            f"{REMOTE_AGENT_URL}/chat",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            # Forwarded token rejected/expired — refresh locally and retry ONCE.
            logger.info("Remote returned 401; refreshing local token and retrying.")
            token, user_id = await _get_token_and_user(force=True)
            payload["user_id"] = user_id
            resp = await http.post(
                f"{REMOTE_AGENT_URL}/chat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        data = resp.json()

    return SendResponse(
        user_id=data.get("user_id", user_id),
        conversation_id=data.get("conversation_id", conversation_id),
        response=data.get("response", ""),
    )
