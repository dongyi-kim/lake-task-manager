"""Compatibility entry point: proxy stdio to the already running local LTM server.

Copy authenticated stdio configuration from LTM Settings → MCP connection.
This process never starts an app instance, creates a Jira client or falls back to mock data.
"""

from app.local_mcp.stdio import main


if __name__ == "__main__":
    main()
