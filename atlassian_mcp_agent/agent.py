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
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import httpx
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from litellm import CustomStreamWrapper, ModelResponse
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from .logging_redaction import install_log_redaction
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


# Redact Authorization headers / bearer tokens from logs (VISION §5.7). The filter lives in
# logging_redaction.py so it can be unit-tested without importing google-adk.
install_log_redaction()


# Token storage (FileTokenStorage) lives in storage.py so the unit test can import it
# without pulling in google-adk / litellm. See tests/test_token_storage.py.


# ───────────────────────── Browser callback plumbing ───────────────────────────
# Single-user Phase 1: one in-flight OAuth callback at a time, tracked by a module future.
# (Phase 2 / multi-user will need a per-flow store keyed by `state`.)
_CallbackResult = tuple[str, str | None]
_callback_future: asyncio.Future[_CallbackResult] | None = None


def _deliver(
    future: asyncio.Future[_CallbackResult],
    result: _CallbackResult | None,
    error: str | None,
) -> None:
    """Resolve or reject the callback future (called on the loop thread, guarded once)."""
    if future.done():
        return
    if error is not None:
        future.set_exception(RuntimeError(error))
    elif result is not None:
        future.set_result(result)


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # http.server API uses this exact (mixedCase) method name
        params = parse_qs(urlparse(self.path).query)
        future = _callback_future
        if future is None:  # no flow in progress — shouldn't happen
            self.send_response(503)
            self.end_headers()
            return
        loop = future.get_loop()
        if "code" in params:
            result = (params["code"][0], params.get("state", [None])[0])
            loop.call_soon_threadsafe(_deliver, future, result, None)
            body = b"Auth complete - you can close this tab and return to the terminal."
        else:
            # User denied consent, or Atlassian returned ?error=... Fail fast instead of
            # leaving the agent hanging until the request timeout.
            error = params.get("error", ["unknown"])[0]
            detail = params.get("error_description", [""])[0]
            loop.call_soon_threadsafe(
                _deliver,
                future,
                None,
                f"OAuth callback returned no code (error={error}: {detail})",
            )
            body = b"Authorization failed - check the terminal."
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # silence noisy stderr (param names match BaseHTTPRequestHandler's override)


async def redirect_handler(url: str) -> None:
    print(f"\n-> Opening browser for Atlassian login:\n  {url}\n")
    webbrowser.open(url)


async def callback_handler() -> _CallbackResult:
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

Access tokens are short-lived (~1 hour). A refresh token is normally issued, so the
OAuth client refreshes the access token automatically — no browser prompt needed. If a
tool call still fails with a 401 (the refresh token has expired or the grant was
revoked), tell the user they need to re-authenticate rather than retrying in a loop.

═══════════════════════════════════════════════════════════════════════════
OPERATIONAL GUIDE (loaded from SKILL.md)
═══════════════════════════════════════════════════════════════════════════
{_SKILL}
"""


# ───────────────────────── LiteLLM model (Bedrock Claude Sonnet 4.6) ───────────
# AWS creds come from the standard provider chain: env vars, then AWS_PROFILE
# (e.g. "mainadmin" with an active SSO session), then instance metadata.
# Region from AWS_REGION (default us-east-1).
#
# Prompt caching (DEC-17): the static prefix — the 8 tool schemas + the ~12 KB SKILL.md
# system prompt — is otherwise re-billed at full input price on EVERY model call in the
# agentic loop, which is where ADK burns tokens. We hand LiteLLM a cache_control injection
# point on the system message; LiteLLM injects an ephemeral cache_control marker and
# translates it to Bedrock's native cachePoint. Bedrock chains tools -> system, so a single
# checkpoint on `system` covers both. Sonnet 4.6: 1,024-token min (the prefix clears it),
# 5-minute TTL only — so we deliberately set no `ttl` (avoids the open Bedrock ttl bugs).
# The kwarg rides ADK's LiteLlm **kwargs pass-through (_additional_args -> acompletion).
# Cost: written once at ~1.25x, re-read at ~0.1x thereafter. Hit rate is observable via the
# after_model_callback below (cached_content_token_count).


class _CacheControlLiteLLMClient(LiteLLMClient):
    """Fallback path: attach an ephemeral Anthropic ``cache_control`` marker to the system
    message at the acompletion boundary, which LiteLLM translates to Bedrock's cachePoint.

    Used only when ``ATLASSIAN_CACHE_FALLBACK=1`` — the ``cache_control_injection_points``
    kwarg is the primary path. This has to live here (a LiteLLMClient subclass) rather than in
    a ``before_model_callback``: ADK converts the genai system instruction (a plain string)
    into the OpenAI message format *inside* ``LiteLlm``, after callbacks have already run, so
    the only place left to tag it is just before the request reaches litellm.
    """

    async def acompletion(
        self,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        **kwargs: object,
    ) -> ModelResponse | CustomStreamWrapper:
        for msg in messages:
            content = msg.get("content")
            if msg.get("role") == "system" and isinstance(content, str):
                msg["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
                break
        # super().acompletion is an untyped third-party method (ADK leaves its params
        # unannotated); the call is correct, so silence the partial-unknown warning.
        return await super().acompletion(  # pyright: ignore[reportUnknownMemberType]
            model, messages, tools, **kwargs
        )


# Primary path = the kwarg; fallback = the subclass above. Flip ATLASSIAN_CACHE_FALLBACK=1
# (no code change) if the token-usage log shows cached(read) stuck at 0.
if os.environ.get("ATLASSIAN_CACHE_FALLBACK", "0") == "1":
    logger.info("Prompt-cache fallback ON: cache_control injected via LiteLLMClient subclass.")
    model = LiteLlm(model=BEDROCK_MODEL_ID, llm_client=_CacheControlLiteLLMClient())
else:
    model = LiteLlm(
        model=BEDROCK_MODEL_ID,
        cache_control_injection_points=[{"location": "message", "role": "system"}],
    )


def _log_token_usage(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Log per-call token usage so prompt-cache hit rates are observable in the smoke test.

    ``cached_content_token_count`` is ADK's surfaced view of Bedrock's cacheReadInputTokens
    (billed at ~0.1x). Expect ~0 on the first call (cache write) and a jump to roughly the
    static-prefix size on every later call in the loop. If it stays 0, caching isn't engaging
    — re-check the cache_control_injection_points wiring and the litellm version. Returns None
    (we observe usage, never mutate the response).
    """
    del callback_context  # required by the ADK callback signature; unused here
    usage = getattr(llm_response, "usage_metadata", None)
    if usage is not None:
        logger.info(
            "Token usage - prompt: %s, cached(read): %s, output: %s",
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "cached_content_token_count", None),
            getattr(usage, "candidates_token_count", None),
        )
    return None


root_agent = Agent(
    model=model,
    name="atlassian_mcp_agent",
    instruction=_AGENT_INSTRUCTION,
    tools=[toolset],
    after_model_callback=_log_token_usage,
)
