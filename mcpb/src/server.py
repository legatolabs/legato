"""Legato's MCP server, as a Claude Desktop extension: the host app runs this with uv."""
import sys

from legato.mcp_server import main

raise SystemExit(main(sys.argv[1:]))
