# 02 — Research: MCP Python SDK (auth / DCR / PKCE)

**Date:** 2026-05-27
**Method:** Context7 (`/modelcontextprotocol/python-sdk`) + MCP SDK README/migration docs.
**Scope:** Confirm the OAuth client objects the agent imports, the DCR+PKCE flow, and the
streamable-http client API coupling with ADK.

---

## TL;DR

All five auth imports the reference uses **exist and match the official example**. The one
thing to get right is the **version**: the SDK is migrating its streamable-http client API,
and ADK 2.0's factory plumbing depends on the *1.x* shape. Pin `mcp>=1.24,<2`.

---

## Confirmed imports & constructor

The reference's imports mirror the SDK's own OAuth client example exactly:

```python
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
```

`OAuthClientProvider` is an **`httpx.Auth`** — you attach it to an `httpx.AsyncClient`
and it transparently runs the whole OAuth dance on demand. Constructor (verified):

```python
OAuthClientProvider(
    server_url=...,                 # base URL; SDK does /.well-known discovery from here
    client_metadata=OAuthClientMetadata(
        client_name=...,
        redirect_uris=[AnyUrl("http://127.0.0.1:3030/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",   # public client + PKCE
        # scope intentionally omitted for Atlassian (server-controlled)
    ),
    storage=<TokenStorage>,
    redirect_handler=<async (url)->None>,     # open browser
    callback_handler=<async ()->(code, state)>,  # receive the redirect
)
```

### `TokenStorage` protocol (what `FileTokenStorage` must implement)
- `async get_tokens() -> OAuthToken | None`
- `async set_tokens(OAuthToken) -> None`
- `async get_client_info() -> OAuthClientInformationFull | None`
- `async set_client_info(OAuthClientInformationFull) -> None`

The reference's `FileTokenStorage` implements all four and round-trips via
`model_dump_json()` / `OAuthToken(**json.loads(...))`. ✅ This is the right shape and is
exactly what our Phase-1 unit test should cover (serialize → deserialize → equal).

### Two small correctness nits for our build
1. **`redirect_uris` should be `AnyUrl`.** The official example wraps the URL in
   `AnyUrl(...)`; the reference passes a plain string. Pydantic v2 *coerces* str→AnyUrl on
   a `list[AnyUrl]` field, so it works today, but wrapping is safer and matches the SDK.
2. **`server_url`: base vs. resource path.** The reference passes the **base**
   (`https://mcp.atlassian.com`) for discovery while connecting the toolset to
   `…/v1/mcp/authv2`. Discovery (`/.well-known/oauth-authorization-server`) is resolved
   relative to `server_url`. Phase 0 confirmed the auth server resolves to
   `cf.mcp.atlassian.com`. **Verify at runtime** that base-URL discovery succeeds (vs.
   needing the full resource path) — logged as an open question in `05`.

---

## DCR + PKCE flow (what happens on first prompt)

`OAuthClientProvider`, on the first 401 / un-authed request, automatically:
1. **Discovers** auth-server metadata. Tries RFC 9728
   (`/.well-known/oauth-protected-resource`) → Atlassian returns **404**, so it falls back
   to **RFC 8414** (`/.well-known/oauth-authorization-server`). *Do not break this
   fallback* (VISION §5.3).
2. **Dynamic Client Registration (RFC 7591)** — if `get_client_info()` is empty, POSTs
   client metadata to the registration endpoint and persists the returned
   `OAuthClientInformationFull` (Phase 0: `cf.mcp.atlassian.com/v1/register`).
3. **Authorization Code + PKCE (S256)** — builds the authorize URL, calls
   `redirect_handler(url)` (opens browser), waits on `callback_handler()` for `(code, state)`.
4. **Token exchange** → persists `OAuthToken` via `set_tokens()`.
5. Retries the original request with the bearer token attached.

The reference's loopback callback server (`127.0.0.1:3030`) + `webbrowser.open` implements
steps 3–4 correctly for a local single-user run.

### Refresh / 401 reality (Atlassian-specific, from Phase 0)
- Atlassian grants **no `offline_access`** → **no refresh token**. Access-token TTL ~1h.
- On expiry the next call 401s. With no refresh token, recovery = **full interactive
  re-auth** (blow away `token.json`, re-run the dance). **Do not loop-retry refresh.**
- *Verify empirically* whether `OAuthClientProvider` itself transparently re-triggers the
  browser flow on a 401-after-expiry, or whether the agent must catch and reset. (→ `05`.)

---

## ⚠️ Version coupling: `streamablehttp_client` vs `streamable_http_client`

This is the subtle one and the reason `mcp` must be pinned `<2`:

- ADK's factory feature plumbs `httpx_client_factory` into MCP's **`streamablehttp_client`**
  (the 1.x function), whose signature accepts `httpx_client_factory=...`.
- The current MCP SDK `main` README/migration shows a **new** API:
  `from mcp.client.streamable_http import streamable_http_client` taking a pre-built
  `http_client=` (with `auth=` set on it) instead of a factory. This is the forward shape
  (mcp 2.x-era).
- **ADK 2.0.0 pins `mcp>=1.24,<2`**, so the 1.x `streamablehttp_client(..., httpx_client_factory=...)`
  path is guaranteed present. If a stray `mcp>=2` were installed, ADK's factory wiring
  could break.

**Action:** pin `mcp>=1.24,<2` in our `pyproject.toml` (don't rely on the loose `>=1.6`).

---

## Sources
- MCP Python SDK (Context7 `/modelcontextprotocol/python-sdk`) — README OAuth client example & migration guide
- MCP SDK repo — https://github.com/modelcontextprotocol/python-sdk
- Atlassian MCP scope/consent behavior cross-checked in `docs/phase-0-results.md` (reference) and `04`/`05`.
