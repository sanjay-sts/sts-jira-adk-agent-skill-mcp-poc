# syntax=docker/dockerfile:1
#
# Image for the Phase 2 multi-user forwarding agent (atlassian_mcp_agent.server).
# Credential-free by design: no token or secret is baked in; the agent only ever handles
# a forwarded bearer per request, in memory. AWS Bedrock creds come from the environment
# / task role at runtime (never from the image).

# ── Builder: install runtime deps into a venv (no project build; pyproject has package=false) ──
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# ── Runtime: slim, non-root; source copied from the build context (run-from-source) ──
FROM python:3.13-slim
WORKDIR /app
RUN useradd --create-home --uid 10001 app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /app/.venv /app/.venv
COPY atlassian_mcp_agent ./atlassian_mcp_agent
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "atlassian_mcp_agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
