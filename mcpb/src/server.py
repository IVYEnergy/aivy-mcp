"""Entry point for the Claude Desktop extension: runs the released aivy-mcp
(installed from pyproject.toml by the host's uv) over stdio."""

from aivy_mcp.server import main

if __name__ == "__main__":
    main()
