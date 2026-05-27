"""Single-user ADK agent that talks to the Atlassian Rovo Remote MCP Server.

Auth:   OAuth 2.1 (Authorization Code + PKCE/S256) + RFC 7591 Dynamic Client
        Registration, via the MCP Python SDK's ``OAuthClientProvider`` injected
        into ADK's ``StreamableHTTPConnectionParams.httpx_client_factory``.
Model:  Claude Sonnet 4.6 on AWS Bedrock via LiteLLM (cross-region inference
        profile ``bedrock/us.anthropic.claude-sonnet-4-6`` — us-east-1 has no
        in-region inference for this model).
Endpoint: https://mcp.atlassian.com/v1/mcp (Streamable HTTP).

Run (from the repo root):
    uv sync --extra dev
    aws sso login --profile mainadmin
    adk web            # then open http://localhost:8000 and pick atlassian_mcp_agent

First prompt triggers a two-screen browser OAuth dance (products consent, then
Atlassian scopes consent). Tokens cache in ~/.atlassian-mcp/ for later runs.

Decisions behind this file live in scratchpad/00-decision-log.md; ground truth
from MCP Inspector testing is in reference_data/.../docs/phase-0-results.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import httpx
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from .storage import FileTokenStorage

logger = logging.getLogger("atlassian_mcp_agent")

# ───────────────────────── Constants / configuration ───────────────────────────
# Atlassian Rovo MCP Streamable HTTP endpoint (DEC-2). The /v1/sse transport is
# unsupported after 2026-06-30; /v1/mcp is the recommended path.
ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
# Base URL the MCP SDK uses for /.well-known OAuth discovery (RFC 8414 fallback).
ATLASSIAN_MCP_BASE = "https://mcp.atlassian.com"

# Bedrock Claude Sonnet 4.6 via LiteLLM (DEC-1). MUST be an inference-profile id.
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "bedrock/us.anthropic.claude-sonnet-4-6",
)

# Loopback OAuth callback port (configurable; the redirect_uri is derived from it).
OAUTH_CALLBACK_PORT = int(os.environ.get("ATLASSIAN_OAUTH_CALLBACK_PORT", "3030"))
OAUTH_REDIRECT_URI = f"http://127.0.0.1:{OAUTH_CALLBACK_PORT}/callback"

# Generous timeout: the first request includes a human-in-the-loop browser dance.
FIRST_REQUEST_TIMEOUT_S = 300.0

# Phase 1: read-only Jira + Confluence (DEC-14). Exactly the Phase-0-validated set.
PHASE_1_TOOL_FILTER = [
    # Identity & discovery
    "atlassianUserInfo",
    "getAccessibleAtlassianResources",
    # Rovo unified entry points (pass cloudId despite schema; watch indexing lag)
    "search",
    "fetch",
    # JQL/CQL escape hatches
    "searchJiraIssuesUsingJql",
    "searchConfluenceUsingCql",
    # Direct fetch by known id
    "getJiraIssue",
    "getConfluencePage",
]


# ───────────────────────── Logging: redact bearer tokens (§5.7) ─────────────────
class _RedactAuthFilter(logging.Filter):
    """Scrub Authorization headers / bearer tokens from any log record.

    Defense-in-depth: we never log tokens ourselves, and httpx/httpcore are pinned
    to WARNING below, but this guarantees a stray DEBUG line can't leak a token.
    """

    _AUTH_KV = re.compile(r'(?i)(authorization["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)')
    _BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - never let logging crash the agent
            return True
        redacted = self._AUTH_KV.sub(r"\1***REDACTED***", msg)
        redacted = self._BEARER.sub("Bearer ***REDACTED***", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = None
        return True


def _install_log_redaction() -> None:
    _filter = _RedactAuthFilter()
    for name in ("httpx", "httpcore", "atlassian_mcp_agent"):
        lg = logging.getLogger(name)
        lg.addFilter(_filter)
    # Keep HTTP client wire logging quiet so headers never hit the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


_install_log_redaction()


# Token storage (FileTokenStorage) lives in storage.py so the unit test can import it
# without pulling in google-adk / litellm. See tests/test_token_storage.py.


# ───────────────────────── Browser callback plumbing ───────────────────────────
_callback_future: asyncio.Future[tuple[str, str | None]] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # http.server API uses this exact (mixedCase) method name
        q = parse_qs(urlparse(self.path).query)
        assert _callback_future is not None
        loop = _callback_future.get_loop()
        loop.call_soon_threadsafe(
            _callback_future.set_result,
            (q["code"][0], q.get("state", [None])[0]),
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"Auth complete - you can close this tab and return to the terminal."
        )

    def log_message(self, format: str, *args: object) -> None:
        pass  # silence noisy stderr (param names match BaseHTTPRequestHandler's override)


async def redirect_handler(url: str) -> None:
    print(f"\n-> Opening browser for Atlassian login:\n  {url}\n")
    webbrowser.open(url)


async def callback_handler() -> tuple[str, str | None]:
    global _callback_future
    _callback_future = asyncio.get_running_loop().create_future()
    srv = HTTPServer(("127.0.0.1", OAUTH_CALLBACK_PORT), _CallbackHandler)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return await _callback_future
    finally:
        srv.shutdown()


# ───────────────────────── OAuth provider + ADK wiring ─────────────────────────
oauth = OAuthClientProvider(
    server_url=ATLASSIAN_MCP_BASE,  # base URL — SDK reads /.well-known from here
    client_metadata=OAuthClientMetadata(
        redirect_uris=[AnyUrl(OAUTH_REDIRECT_URI)],
        client_name="atlassian-mcp-agent",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # public client + PKCE
        # NOTE: `scope` is intentionally omitted. The Atlassian MCP server has a
        # hardcoded server-side scope set and ignores client-requested scopes (DEC-3).
    ),
    storage=FileTokenStorage(),
    redirect_handler=redirect_handler,
    callback_handler=callback_handler,
)


def _httpx_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build the httpx client MCP uses, injecting the OAuth provider as the auth.

    ADK calls this factory (headers, timeout, auth); we discard the incoming `auth`
    and substitute the MCP SDK's OAuthClientProvider so every request carries a
    DCR/PKCE-managed bearer token (and triggers the browser flow when needed).
    """
    del auth  # intentionally ignored: we inject `oauth` as the client auth below
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(FIRST_REQUEST_TIMEOUT_S),
        auth=oauth,
        follow_redirects=True,
    )


toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=ATLASSIAN_MCP_URL,
        timeout=FIRST_REQUEST_TIMEOUT_S,
        sse_read_timeout=FIRST_REQUEST_TIMEOUT_S,
        httpx_client_factory=_httpx_factory,
    ),
    tool_filter=PHASE_1_TOOL_FILTER,
)


# ───────────────────────── Load skill into instruction ─────────────────────────
def _load_skill() -> str:
    """Load SKILL.md from this package dir and return its body (frontmatter stripped)."""
    skill_path = pathlib.Path(__file__).parent / "SKILL.md"
    if not skill_path.exists():
        logger.warning("SKILL.md not found at %s - agent runs without skill guidance.", skill_path)
        return ""
    text = skill_path.read_text(encoding="utf-8")
    # Strip YAML frontmatter between the first two `---` fences.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].lstrip()
    return text


_SKILL = _load_skill()

_AGENT_INSTRUCTION = f"""\
You are an Atlassian assistant for the user. You have access to the Atlassian
Rovo MCP Server which exposes Jira and Confluence tools. Help the user explore
their issues, sprints, epics, and documentation. You can ONLY see what the
authenticated user has permission to see — never claim something doesn't exist
just because you can't see it; clarify access if a result is empty.

Follow the operational guide below when choosing tools. Prefer the `search`
tool for natural-language questions; fall back to JQL/CQL only when the user
uses precise query language, asks for specific filters, or when the content
in question was created in the last hour (Rovo Search has indexing lag).

When an access token expires (~1 hour, no refresh token is granted), a tool call
will fail with a 401 — tell the user they need to re-authenticate rather than
retrying in a loop.

═══════════════════════════════════════════════════════════════════════════
OPERATIONAL GUIDE (loaded from SKILL.md)
═══════════════════════════════════════════════════════════════════════════
{_SKILL}
"""


# ───────────────────────── LiteLLM model (Bedrock Claude Sonnet 4.6) ───────────
# AWS creds come from the standard provider chain: env vars, then AWS_PROFILE
# (e.g. "mainadmin" with an active SSO session), then instance metadata.
# Region from AWS_REGION (default us-east-1).
model = LiteLlm(model=BEDROCK_MODEL_ID)


root_agent = Agent(
    model=model,
    name="atlassian_mcp_agent",
    instruction=_AGENT_INSTRUCTION,
    tools=[toolset],
)
