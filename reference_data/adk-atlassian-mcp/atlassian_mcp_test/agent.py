"""
Single-user ADK agent that talks to the Atlassian Rovo Remote MCP Server.

Model: Claude Sonnet 4.6 on AWS Bedrock via LiteLLM (matches existing
Nutrien/Bedrock SSO setup, profile `mainadmin`, us-east-1).

Run:
    pip install "google-adk[litellm]>=2.0" "mcp>=1.6" httpx
    aws sso login --profile mainadmin
    export AWS_PROFILE=mainadmin
    export AWS_REGION=us-east-1
    adk web .

First prompt triggers a browser OAuth dance (DCR + PKCE) against
mcp.atlassian.com. Tokens cached in ~/.atlassian-mcp/ for subsequent runs.

See VISION.md (locked decisions, non-negotiables) and SKILL.md
(operational guide injected into the agent's instruction).
"""

import asyncio
import json
import logging
import os
import pathlib
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import httpx
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

logger = logging.getLogger(__name__)

# Atlassian's current recommended endpoint. /v1/mcp still works; /authv2 is forward path.
ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp/authv2"
ATLASSIAN_MCP_BASE = "https://mcp.atlassian.com"

# LiteLLM Bedrock model string for Claude Sonnet 4.6.
# Update if the Bedrock model ID changes — check `aws bedrock list-foundation-models`.
# Format reference: https://docs.litellm.ai/docs/providers/bedrock
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "bedrock/anthropic.claude-sonnet-4-6-20250929-v1:0",
)

# Phase 1: read-only tools. Expand per VISION.md §7 phases.
PHASE_1_TOOL_FILTER = [
    # Identity & discovery
    "atlassianUserInfo",
    "getAccessibleAtlassianResources",
    # Rovo unified (preferred entry points, with cloudId + indexing-lag caveats)
    "search",
    "fetch",
    # JQL/CQL escape hatches
    "searchJiraIssuesUsingJql",
    "searchConfluenceUsingCql",
    # Direct fetch by known ID
    "getJiraIssue",
    "getConfluencePage",
]


# ───────────────────────── Token storage (file-based, single user) ─────────────
class FileTokenStorage(TokenStorage):
    def __init__(self, root: str = "~/.atlassian-mcp"):
        self.dir = pathlib.Path(os.path.expanduser(root))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t_file = self.dir / "token.json"
        self.c_file = self.dir / "client.json"

    async def get_tokens(self):
        if self.t_file.exists():
            return OAuthToken(**json.loads(self.t_file.read_text()))
        return None

    async def set_tokens(self, t: OAuthToken):
        # Log granted scopes (NOT the token) on first persist — ground truth.
        if not self.t_file.exists():
            logger.info(
                "First token persisted. Granted scope: %s. Refresh token present: %s.",
                getattr(t, "scope", None),
                bool(getattr(t, "refresh_token", None)),
            )
        self.t_file.write_text(t.model_dump_json())
        try:
            os.chmod(self.t_file, 0o600)
        except (OSError, NotImplementedError):
            pass

    async def get_client_info(self):
        if self.c_file.exists():
            return OAuthClientInformationFull(**json.loads(self.c_file.read_text()))
        return None

    async def set_client_info(self, c: OAuthClientInformationFull):
        self.c_file.write_text(c.model_dump_json())
        try:
            os.chmod(self.c_file, 0o600)
        except (OSError, NotImplementedError):
            pass


# ───────────────────────── Browser callback plumbing ───────────────────────────
_callback_future: asyncio.Future | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        assert _callback_future is not None
        loop = _callback_future.get_loop()
        loop.call_soon_threadsafe(
            _callback_future.set_result,
            (q["code"][0], q.get("state", [None])[0]),
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auth complete - you can close this tab and return to the terminal.")

    def log_message(self, *a, **k):
        pass  # silence noisy stderr


async def redirect_handler(url: str):
    print(f"\n→ Opening browser for Atlassian login:\n  {url}\n")
    webbrowser.open(url)


async def callback_handler():
    global _callback_future
    _callback_future = asyncio.get_running_loop().create_future()
    srv = HTTPServer(("127.0.0.1", 3030), _CallbackHandler)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return await _callback_future
    finally:
        srv.shutdown()


# ───────────────────────── OAuth provider + ADK wiring ─────────────────────────
oauth = OAuthClientProvider(
    server_url=ATLASSIAN_MCP_BASE,  # base URL — SDK reads /.well-known
    client_metadata=OAuthClientMetadata(
        redirect_uris=["http://127.0.0.1:3030/callback"],
        client_name="adk-atlassian-test",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # public client + PKCE
        # NOTE: `scope` is intentionally omitted. The Atlassian MCP server has a
        # hardcoded server-side scope set and ignores client-requested scopes.
        # See VISION.md §3 (Scope strategy) and SKILL.md.
    ),
    storage=FileTokenStorage(),
    redirect_handler=redirect_handler,
    callback_handler=callback_handler,
)


def _httpx_factory(headers=None, timeout=None, auth=None):
    # Inject the MCP SDK's OAuth provider as httpx.Auth on every request.
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(300.0),  # 5 min for first interactive auth
        auth=oauth,
        follow_redirects=True,
    )


toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=ATLASSIAN_MCP_URL,
        timeout=300.0,
        sse_read_timeout=300.0,
        httpx_client_factory=_httpx_factory,
    ),
    tool_filter=PHASE_1_TOOL_FILTER,
)


# ───────────────────────── Load skill into instruction ─────────────────────────
def _load_skill() -> str:
    """Load SKILL.md alongside agent.py and return its body (frontmatter stripped)."""
    skill_path = pathlib.Path(__file__).parent.parent / "SKILL.md"
    if not skill_path.exists():
        # Fallback: try alongside agent.py
        skill_path = pathlib.Path(__file__).parent / "SKILL.md"
    if not skill_path.exists():
        logger.warning("SKILL.md not found — agent runs without skill guidance.")
        return ""
    text = skill_path.read_text(encoding="utf-8")
    # Strip YAML-ish frontmatter between the first two `---` lines.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip()
    return text


_SKILL = _load_skill()

_AGENT_INSTRUCTION = f"""You are an Atlassian assistant for the user. You have access to the Atlassian
Rovo MCP Server which exposes Jira and Confluence tools. Help the user explore
their issues, sprints, epics, and documentation. You can ONLY see what the
authenticated user has permission to see — never claim something doesn't exist
just because you can't see it; clarify access if a result is empty.

Follow the operational guide below when choosing tools. Prefer the `search`
tool for natural-language questions; fall back to JQL/CQL only when the user
uses precise query language, asks for specific filters, or when the content
in question was created in the last hour (Rovo Search has indexing lag).

═══════════════════════════════════════════════════════════════════════════
OPERATIONAL GUIDE (loaded from SKILL.md)
═══════════════════════════════════════════════════════════════════════════
{_SKILL}
"""


# ───────────────────────── LiteLLM model (Bedrock Claude Sonnet 4.6) ───────────
# AWS credentials are picked up from the standard provider chain:
#   1. Environment vars (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN)
#   2. AWS_PROFILE (e.g. "mainadmin" with active SSO session)
#   3. EC2/ECS instance metadata
# Region from AWS_REGION env var (default us-east-1 if unset).
model = LiteLlm(model=BEDROCK_MODEL_ID)


root_agent = Agent(
    model=model,
    name="atlassian_jira_test",
    instruction=_AGENT_INSTRUCTION,
    tools=[toolset],
)
