"""Phase 2 — FastAPI request layer for the multi-user forwarding agent.

Replaces `adk web` (single-user) as the client-facing surface. Per request it:

  1. extracts the forwarded Atlassian access token from `Authorization: Bearer …`,
  2. binds it to a request-scoped ContextVar (never to session state / disk / logs),
  3. drives the ADK Runner keyed by (user_id, conversation_id),
  4. clears the bearer.

This is the same code that deploys to Fargate behind an ALB; it is also the Level-2 HTTP
endpoint the isolation harness drives. The bearer is request metadata only — it never enters
the persisted conversation record.

Run locally:
    uv run uvicorn atlassian_mcp_agent.server:app --host 127.0.0.1 --port 8080

Example:
    curl -s localhost:8080/chat \
      -H "Authorization: Bearer $ATLAS_TOKEN_A" \
      -H 'content-type: application/json' \
      -d '{"user_id":"alice","message":"Who am I in Atlassian?"}'
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from .forwarding import bearer_scope, build_agent

logger = logging.getLogger("atlassian_mcp_agent.server")

APP_NAME = "atlassian_mcp_agent_mt"

# One agent + runner per process, shared across all users. Isolation is per-request
# (the ContextVar bearer + ADK's header-keyed MCP session pool), NOT per-agent-instance.
# NOTE: InMemorySessionService is single-process only; Task 6 swaps it for a shared,
# bearer-scrubbed store so any autoscaled task can serve any conversation.
_session_service = InMemorySessionService()
_runner = Runner(
    app_name=APP_NAME,
    agent=build_agent(),
    session_service=_session_service,
)

app = FastAPI(title="Atlassian MCP forwarding agent (Phase 2)")


class ChatRequest(BaseModel):
    user_id: str
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    user_id: str
    conversation_id: str
    response: str


def _extract_bearer(authorization: str | None) -> str:
    """Pull the forwarded Atlassian access token out of the Authorization header. Fail closed."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing or malformed 'Authorization: Bearer <token>' header")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Empty bearer token")
    return token


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    token = _extract_bearer(authorization)
    conversation_id = req.conversation_id or uuid.uuid4().hex

    # Ensure the ADK session exists, keyed by (app, user_id, conversation_id). The bearer is
    # NEVER written into session state — it lives only in the request-scoped ContextVar below.
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=req.user_id, session_id=conversation_id
    )
    if session is None:
        await _session_service.create_session(
            app_name=APP_NAME, user_id=req.user_id, session_id=conversation_id
        )
        # TODO(Task 5 / Surface 2): on first turn, resolve the token's true accountId
        # (atlassianUserInfo) and bind conversation_id -> accountId; on later turns assert the
        # presented bearer resolves to the same accountId (anti-hijack) before running.

    content = types.Content(role="user", parts=[types.Part(text=req.message)])

    final_text = ""
    with bearer_scope(token):
        async for event in _runner.run_async(
            user_id=req.user_id, session_id=conversation_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(p.text or "" for p in event.content.parts)

    return ChatResponse(
        user_id=req.user_id, conversation_id=conversation_id, response=final_text
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
