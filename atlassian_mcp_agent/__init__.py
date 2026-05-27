"""ADK agent package for the Atlassian Rovo Remote MCP Server (Phase 1).

The `from . import agent` line is required by the ADK CLI convention so that
`adk web` / `adk run` / `adk api_server` can auto-discover `agent.root_agent`.
"""

from . import agent

__all__ = ["agent"]
