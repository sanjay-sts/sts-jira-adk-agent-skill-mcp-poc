# 01 — Research: Google ADK × MCP Toolset

**Date:** 2026-05-27
**Method:** Context7 (`/google/adk-python`) + direct reads of ADK 2.0.0 source on GitHub + web.
**Scope:** Confirm the ADK building blocks the Phase 1 agent depends on, and pin versions correctly.

---

## TL;DR

The reference agent's ADK wiring is **sound**. Every API it uses exists in ADK 2.0.0 GA.
The only changes we need for our own build are dependency-pin tightening (`mcp`, and an
honest version floor) and awareness that ADK 2.0 is a *major* GA bump.

| Thing the agent relies on | Status in ADK 2.0.0 | Note |
|---|---|---|
| `from google.adk.tools.mcp_tool import McpToolset` | ✅ Exists | Both `McpToolset` **and** `MCPToolset` are exported as aliases (`__all__`). |
| `StreamableHTTPConnectionParams` | ✅ Exists | Exported from package and `…mcp_session_manager`. |
| `…StreamableHTTPConnectionParams.httpx_client_factory` | ✅ Exists | The seam this whole project hinges on. |
| `McpToolset(tool_filter=[...])` | ✅ Supported | Selects a subset of server tools. |
| `from google.adk.models.lite_llm import LiteLlm` | ✅ Exists | Wrapper for non-Google models via LiteLLM. |
| `Agent(...)` / `LlmAgent(...)` | ✅ Both work | `Agent` is an alias of `LlmAgent`. |
| `adk web` entrypoint discovering `root_agent` | ✅ Standard | Package must expose `root_agent`; `__init__.py` imports `agent`. |

---

## Versioning (the important part)

- **`httpx_client_factory` shipped in ADK `v1.22.0` (2026-01-08)**, commit `bfed19c`
  ("Expose mcps streamable http custom httpx factory parameter"), closing
  [issue #3005](https://github.com/google/adk-python/issues/3005).
  → The VISION doc's claim that it "was added in 2.0" is **inaccurate**; it predates 2.0.
- **ADK `2.0.0` GA was released 2026-05-19** (8 days before the reference repo's date).
  So `google-adk[litellm]>=2.0` is valid and installs a stable release — but 2.0 is a
  **major** bump; read its migration notes before building.
- **ADK 2.0.0's own dependency pins (from its `pyproject.toml`):**
  - `mcp>=1.24,<2`  ← *this is the constraint that actually matters (see below)*
  - `litellm>=1.83.7,<=1.83.14` (tight)
  - `google-genai>=1.72,<2`
  - `requires-python >=3.10`

### Action for our build
- Floor `google-adk[litellm]>=2.0` is fine. (If we wanted *only* the factory feature we
  could go as low as `>=1.22`, but we want 2.0 GA.)
- **Change the reference's `mcp>=1.6` → `mcp>=1.24,<2`** to match what ADK 2.0 resolves.
  `>=1.6` is below ADK's own floor and is misleading; the `<2` cap is load-bearing
  (see `02-research-mcp-sdk.md` — mcp 2.x changes the streamable-http client API that
  ADK's factory plumbing depends on).
- Our `requires-python>=3.11` is stricter than ADK's `>=3.10` → compatible.

---

## How `httpx_client_factory` is wired (verified in source)

`StreamableHTTPConnectionParams` (in `…/mcp_tool/mcp_session_manager.py`, ADK 2.0.0):

```python
class StreamableHTTPConnectionParams(BaseModel):
    url: str
    headers: dict[str, Any] | None = None
    timeout: float = 5.0
    sse_read_timeout: float = 60 * 5.0          # 300s
    terminate_on_close: bool = True
    httpx_client_factory: CheckableMcpHttpClientFactory = create_mcp_http_client
```

- ADK passes the factory straight through to MCP's client:
  `httpx_client_factory=self._connection_params.httpx_client_factory`.
- `CheckableMcpHttpClientFactory` is a `@runtime_checkable` Protocol extending MCP's
  `McpHttpClientFactory`. The factory callable signature is
  **`(headers: dict|None, timeout: httpx.Timeout|None, auth: httpx.Auth|None) -> httpx.AsyncClient`**.
- The reference's factory matches and intentionally **substitutes its own `auth=oauth`**
  (the MCP `OAuthClientProvider`) so every request carries DCR/PKCE-managed bearer tokens:

```python
def _httpx_factory(headers=None, timeout=None, auth=None):
    return httpx.AsyncClient(headers=headers,
                             timeout=timeout or httpx.Timeout(300.0),
                             auth=oauth, follow_redirects=True)
```

This is exactly the supported pattern from #3005. ✅ Keep it.

> Note: the reference passes `timeout=300.0` and `sse_read_timeout=300.0` on
> `StreamableHTTPConnectionParams` (overriding the 5s/300s defaults). Good — the first
> request includes a human-in-the-loop browser dance; generous timeouts are a
> non-negotiable (VISION §5.6).

---

## Multi-user note (Phase 2, not Phase 1)

ADK sessions carry state; per-user identity for Phase 2 comes from
`tool_context.state["user_id"]` (must be set by the trusted calling app, **not** user
input). The current `FileTokenStorage` is single-directory/single-user by design. Keep
Phase 1 single-user; the multi-user keyed store is deferred (VISION §2 Phase 2).

---

## Sources
- ADK issue #3005 — https://github.com/google/adk-python/issues/3005
- ADK CHANGELOG — https://github.com/google/adk-python/blob/main/CHANGELOG.md
- ADK 2.0.0 `pyproject.toml` — https://raw.githubusercontent.com/google/adk-python/v2.0.0/pyproject.toml
- ADK 2.0.0 `mcp_tool/__init__.py` — https://raw.githubusercontent.com/google/adk-python/v2.0.0/src/google/adk/tools/mcp_tool/__init__.py
- ADK 2.0.0 `mcp_session_manager.py` — https://raw.githubusercontent.com/google/adk-python/v2.0.0/src/google/adk/tools/mcp_tool/mcp_session_manager.py
