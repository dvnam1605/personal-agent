"""Run the assistant as an MCP server over stdio (for Claude Desktop and other agents).

Configure in the client, for example Claude Desktop's claude_desktop_config.json:

    "noat": {
      "command": "uv",
      "args": ["--directory", "D:/Code/personal_ai_assistant", "run", "python", "scripts/mcp_server.py"],
      "env": {"NOAT_BASE_URL": "http://127.0.0.1:8000", "NOAT_TOKEN": "<login token>"}
    }

NOAT_TOKEN is a login token (or NOAT_API_KEY plus NOAT_USER_ID). The server only asks
the assistant; it cannot approve writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.mcp.server import AssistantApi, serve  # noqa: E402

if __name__ == "__main__":
    serve(AssistantApi.from_env())
