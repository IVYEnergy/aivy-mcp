#!/bin/sh
# Aivy MCP installer for macOS and Linux:
#   curl -fsSL https://raw.githubusercontent.com/IVYEnergy/aivy-mcp/main/install.sh | sh
# Installs uv when it is missing, then writes the Aivy server into every MCP
# client found on this computer (Codex, Claude Code, Claude Desktop, Cursor).
# No key is needed: sign up or sign in from the chat afterwards.
set -eu

have() { command -v "$1" >/dev/null 2>&1; }

if ! have uvx; then
  if have brew; then
    echo "Installing uv with Homebrew..."
    brew install uv
  else
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
fi
for dir in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in *":$dir:"*) ;; *) PATH="$dir:$PATH";; esac
done
export PATH

if ! have uvx; then
  echo "uvx is still not on PATH; open a new terminal and run: uvx --from aivy-mcp@latest aivy-mcp-setup" >&2
  exit 1
fi

# @latest: run the newest installer even when uv has an older aivy-mcp cached.
echo "Configuring MCP clients..."
exec uvx --from aivy-mcp@latest aivy-mcp-setup "$@"
