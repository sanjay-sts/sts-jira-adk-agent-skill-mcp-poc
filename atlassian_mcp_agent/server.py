"""Phase 2 — FastAPI request layer for the multi-user forwarding agent.

Replaces `adk web` (single-user) as the client-facing surface. Per request it:

  1. extracts the forwarded Atlassian access token from `Authorization: Bearer …`,
  2. resolves the token to its real Atlassian accountId (Surface 2 — identity comes from
     Atlassian, never from the client's claimed user_id) and binds the conversation to it,
  3. binds the token to a request-scoped ContextVar (never to session state / disk / logs),
  4. drives the ADK Runner keyed by (accountId, conversation_id),
  5. clears the bearer.

Isolation:
  • Surface 1 (creds/data): the per-request ContextVar bearer + ADK's per-header MCP session
    pool — A's token can't reach B's session.
  • Surface 2 (conversation history): the ADK session is keyed by the server-resolved
    accountId, so a hijacker presenting a valid-but-different token lands in THEIR OWN
    conversation namespace, never the victim's. A conversation↔accountId binding check is
    kept as defense-in-depth + an explicit audit signal.

The bearer is request metadata only — it never enters the persisted conversation record
(only the non-secret bound accountId + client label do).

Run locally:
    uv run uvicorn atlassian_mcp_agent.server:app --host 127.0.0.1 --port 8080   # or: just serve
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, Header, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.base_session_service import BaseSessionService
from google.genai import types
from pydantic import BaseModel

from .forwarding import bearer_scope, build_agent
from .identity import BindingMismatch, IdentityError, check_binding, resolve_account_id

logger = logging.getLogger("atlassian_mcp_agent.server")
# Dedicated audit channel (resolved accountId + token fingerprint, never the token). Split from
# the operational logger so ops can route/level it independently — e.g. its own CloudWatch
# stream or a stricter level — since it carries identity (PII) the app logs don't otherwise.
audit = logging.getLogger("atlassian_mcp_agent.audit")

APP_NAME = "atlassian_mcp_agent_mt"


def _build_session_service() -> BaseSessionService:
    """Pluggable session store. Default in-memory (single process); sqlite/database for sharing.

    The forwarded bearer is NEVER written here — only the non-secret bound accountId + client
    label — so any backend (incl. a shared DB across autoscaled tasks) holds no credential.

      SESSION_BACKEND=memory                          (default)
      SESSION_BACKEND=sqlite   SESSION_DB_PATH=...     (single-host persistence)
      SESSION_BACKEND=database SESSION_DB_URL=...      (shared across tasks; needs sqlalchemy)
    """
    backend = os.environ.get("SESSION_BACKEND", "memory").lower()
    if backend == "sqlite":
        from google.adk.sessions.sqlite_session_service import SqliteSessionService

        path = os.environ.get("SESSION_DB_PATH", "./sessions.db")
        logger.info("Session backend: sqlite (%s)", path)
        return SqliteSessionService(db_path=path)
    if backend == "database":
        from google.adk.sessions.database_session_service import DatabaseSessionService

        url = os.environ["SESSION_DB_URL"]
        logger.info("Session backend: database (shared)")
        return DatabaseSessionService(db_url=url)
    logger.info("Session backend: in-memory (single process)")
    return InMemorySessionService()


# One agent + runner per process, shared across all users. Isolation is per-request
# (the ContextVar bearer + ADK's header-keyed MCP session pool + accountId-keyed sessions),
# NOT per-agent-instance.
_session_service = _build_session_service()
_runner = Runner(
    app_name=APP_NAME,
    agent=build_agent(),
    session_service=_session_service,
)

app = FastAPI(title="Atlassian MCP forwarding agent (Phase 2)")


class ChatRequest(BaseModel):
    user_id: str  # a friendly client label only; NOT trusted for isolation (accountId is)
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    user_id: str  # the server-resolved Atlassian accountId (the real identity)
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

    # Surface 2: establish identity from the token (Atlassian validates it), never the client.
    try:
        fp, account_id = await resolve_account_id(token)
    except IdentityError as e:
        raise HTTPException(
            401, "Could not resolve Atlassian identity for the forwarded token"
        ) from e

    # Key the ADK session by the resolved accountId so conversation namespaces are isolated
    # by cryptographic identity. The bearer is NEVER written into session state.
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=account_id, session_id=conversation_id
    )
    if session is None:
        await _session_service.create_session(
            app_name=APP_NAME,
            user_id=account_id,
            session_id=conversation_id,
            state={"bound_account_id": account_id, "client_label": req.user_id},
        )
    else:
        # Defense-in-depth anti-hijack (keying already isolates; this makes mismatch explicit).
        try:
            check_binding(session.state.get("bound_account_id"), account_id)
        except BindingMismatch as e:
            audit.warning(
                "hijack-block conv=%s fp=%s presented=%s", conversation_id, fp, account_id
            )
            raise HTTPException(
                403, "Forwarded token does not match this conversation's owner"
            ) from e

    audit.info(
        "chat conv=%s account=%s fp=%s client_label=%s",
        conversation_id, account_id, fp, req.user_id,
    )

    content = types.Content(role="user", parts=[types.Part(text=req.message)])

    final_text = ""
    saw_final = False
    with bearer_scope(token):
        async for event in _runner.run_async(
            user_id=account_id, session_id=conversation_id, new_message=content
        ):
            if event.is_final_response():
                saw_final = True
                if event.content and event.content.parts:
                    final_text = "".join(p.text or "" for p in event.content.parts)

    if not final_text:
        # Don't let an empty/absent final response disappear silently — make it observable.
        logger.warning(
            "empty agent response conv=%s account=%s saw_final=%s",
            conversation_id, account_id, saw_final,
        )

    return ChatResponse(user_id=account_id, conversation_id=conversation_id, response=final_text)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
