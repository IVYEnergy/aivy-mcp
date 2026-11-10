"""Tell the assistant when a newer aivy-mcp is on PyPI, so the person hears
about it in the chat instead of never.

How each client picks up a new version:
  uvx aivy-mcp@latest      checks PyPI at every start: restart the client
  uvx aivy-mcp             uv keeps the version it resolved first; rerun the
                           installer, which rewrites the entry to @latest
  Claude Desktop (.mcpb)   the extension pins its version: open the new .mcpb

The check is one GET to PyPI with a short timeout, at most once per process,
and never raises. AIVY_MCP_NO_UPDATE_CHECK=1 turns it off.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import httpx

from aivy_mcp import __version__

PYPI_URL = "https://pypi.org/pypi/aivy-mcp/json"
RELEASES_URL = "https://github.com/IVYEnergy/aivy-mcp/releases/latest"
INSTALLER = "curl -fsSL https://raw.githubusercontent.com/IVYEnergy/aivy-mcp/main/install.sh | sh"

_checked: dict[str, Any] = {}


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3]) or (0,)


def is_newer(latest: str | None, current: str = __version__) -> bool:
    return bool(latest) and _parts(latest) > _parts(current)


def launch_mode() -> str:
    """How this process was started: 'mcpb' (Claude Desktop extension, set
    by its manifest), 'uvx' (a uv tool environment) or 'other'."""
    mode = os.environ.get("AIVY_MCP_LAUNCH", "").strip().lower()
    if mode:
        return mode
    exe = (sys.argv[0] if sys.argv else "") + " " + sys.prefix
    if "/uv/" in exe or "\\uv\\" in exe or "archive-v" in exe:
        return "uvx"
    return "other"


async def latest_version() -> str | None:
    """The newest release on PyPI, or None when the lookup is off or fails.
    Cached for the life of the process."""
    if os.environ.get("AIVY_MCP_NO_UPDATE_CHECK"):
        return None
    if "latest" in _checked:
        return _checked["latest"]
    latest: str | None = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.get(PYPI_URL, headers={"Accept": "application/json"})
            if r.status_code == 200:
                latest = str(r.json().get("info", {}).get("version") or "") or None
    except Exception:  # noqa: BLE001 — offline, blocked, odd payload: no notice
        latest = None
    _checked["latest"] = latest
    return latest


def notice_for(latest: str, mode: str | None = None) -> str:
    mode = mode or launch_mode()
    if mode == "mcpb":
        how = (f"This is the Claude Desktop extension, which pins its version: download the new .mcpb from "
               f"{RELEASES_URL}, open it, and Claude Desktop replaces the extension.")
    elif mode == "uvx":
        how = ("If the client's entry runs `uvx aivy-mcp@latest`, restarting the client is enough. If it still "
               f"runs `uvx aivy-mcp` (older installs), run the installer again, which rewrites the entry: "
               f"`{INSTALLER}`; then restart the client.")
    else:
        how = f"Update the package where it is installed (pip, pipx or uv), or run the installer again: `{INSTALLER}`."
    return (f"Update available: aivy-mcp {latest} is on PyPI; this server is {__version__}. {how} "
            "Mention this to the user once, briefly, at a natural moment; do not repeat it in the same conversation "
            "and do not stop working to say it.")


async def update_notice() -> str | None:
    """A paragraph for the assistant when a newer release exists, else None."""
    latest = await latest_version()
    return notice_for(latest) if latest and is_newer(latest) else None
