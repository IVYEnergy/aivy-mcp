"""`aivy-mcp setup`: write the Aivy server into every MCP client on this computer.

    uvx --from aivy-mcp aivy-mcp-setup            # every client found
    aivy-mcp setup --client codex --key aivy_sk_...

Clients and their files:
  codex           ~/.codex/config.toml                      ([mcp_servers.aivy])
  claude-code     `claude mcp add` when the CLI is present, else ~/.claude.json
  claude-desktop  ~/Library/Application Support/Claude/claude_desktop_config.json
                  (%APPDATA%\\Claude\\claude_desktop_config.json on Windows)
  cursor          ~/.cursor/mcp.json

Each entry runs `uvx aivy-mcp` by the absolute path of uvx, because GUI
apps start with a minimal PATH. Existing entries are left alone unless
--force; every file is backed up before it is changed. No key is needed:
the person signs up or signs in from the chat afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "aivy"
CLIENTS = ("codex", "claude-code", "claude-desktop", "cursor")


def uvx_path() -> str:
    found = shutil.which("uvx")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/uvx", Path("/opt/homebrew/bin/uvx"), Path("/usr/local/bin/uvx")):
        if candidate.exists():
            return str(candidate)
    return "uvx"


def _backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(path, path.with_name(path.name + f".bak-{stamp}"))


def _server_json(uvx: str, key: str | None) -> dict:
    entry: dict = {"command": uvx, "args": ["aivy-mcp"]}
    if key:
        entry["env"] = {"AIVY_API_KEY": key}
    return entry


def _write_json_client(path: Path, uvx: str, key: str | None, force: bool) -> str:
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return f"skipped: {path} is not valid JSON"
    servers = data.setdefault("mcpServers", {})
    if SERVER_NAME in servers and not force:
        return f"already configured: {path}"
    _backup(path)
    servers[SERVER_NAME] = _server_json(uvx, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"configured: {path}"


def setup_codex(uvx: str, key: str | None, force: bool, home: Path) -> str:
    path = home / ".codex" / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if f"[mcp_servers.{SERVER_NAME}]" in text:
        if not force:
            return f"already configured: {path}"
        lines, out, skip = text.splitlines(), [], False
        for line in lines:
            if line.strip().startswith(f"[mcp_servers.{SERVER_NAME}"):
                skip = True
                continue
            if skip and line.strip().startswith("["):
                skip = False
            if not skip:
                out.append(line)
        text = "\n".join(out).rstrip() + "\n"
    block = (f"\n# Aivy: solar + battery tools (sign up or sign in from the chat; no key needed)\n"
             f"[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(uvx)}\nargs = [\"aivy-mcp\"]\nstartup_timeout_sec = 120\n")
    if key:
        block += f"\n[mcp_servers.{SERVER_NAME}.env]\nAIVY_API_KEY = {json.dumps(key)}\n"
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((text.rstrip("\n") + "\n" if text else "") + block, encoding="utf-8")
    return f"configured: {path}"


def setup_claude_code(uvx: str, key: str | None, force: bool, home: Path) -> str:
    claude = shutil.which("claude")
    if claude:
        if not force:
            try:
                listed = subprocess.run([claude, "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=30)
                if listed.returncode == 0:
                    return "already configured: claude mcp (user scope)"
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            subprocess.run([claude, "mcp", "remove", "-s", "user", SERVER_NAME], capture_output=True, text=True, timeout=30)
        cmd = [claude, "mcp", "add", "-s", "user", SERVER_NAME]
        if key:
            cmd += ["-e", f"AIVY_API_KEY={key}"]
        cmd += ["--", uvx, "aivy-mcp"]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return f"skipped: claude CLI failed ({e})"
        return "configured: claude mcp (user scope)" if done.returncode == 0 else f"skipped: claude mcp add failed: {done.stderr.strip()[:200]}"
    path = home / ".claude.json"
    if not path.exists():
        return "skipped: Claude Code not found (no `claude` CLI, no ~/.claude.json)"
    return _write_json_client(path, uvx, key, force)


def setup_claude_desktop(uvx: str, key: str | None, force: bool, home: Path) -> str:
    system = platform.system()
    if system == "Darwin":
        path = home / "Library/Application Support/Claude/claude_desktop_config.json"
    elif system == "Windows":
        path = Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Claude/claude_desktop_config.json"
    else:
        path = home / ".config/Claude/claude_desktop_config.json"
    if not path.parent.exists():
        return "skipped: Claude Desktop not found"
    return _write_json_client(path, uvx, key, force)


def setup_cursor(uvx: str, key: str | None, force: bool, home: Path) -> str:
    path = home / ".cursor" / "mcp.json"
    if not path.parent.exists():
        return "skipped: Cursor not found"
    return _write_json_client(path, uvx, key, force)


SETUP = {"codex": setup_codex, "claude-code": setup_claude_code, "claude-desktop": setup_claude_desktop,
         "cursor": setup_cursor}


def run(clients: list[str], key: str | None, force: bool, home: Path | None = None) -> dict[str, str]:
    home = home or Path.home()
    uvx = uvx_path()
    return {name: SETUP[name](uvx, key, force, home) for name in clients}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aivy-mcp setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", action="append", choices=CLIENTS,
                        help="only this client (repeatable); default: every client found")
    parser.add_argument("--key", help="an existing API key to store as AIVY_API_KEY (optional)")
    parser.add_argument("--force", action="store_true", help="replace an existing aivy entry")
    args = parser.parse_args(argv)
    results = run(args.client or list(CLIENTS), args.key, args.force)
    width = max(len(k) for k in results)
    for name, outcome in results.items():
        print(f"{name:<{width}}  {outcome}")
    configured = [n for n, o in results.items() if o.startswith("configured")]
    print()
    if configured:
        print("Restart " + ", ".join(configured) + " and say: sign me up to Aivy (or: sign in to my Aivy account).")
    else:
        print("Nothing new to configure. Keys are managed at https://bridge.ivyenergy.com.au/account")
    return 0


if __name__ == "__main__":
    sys.exit(main())
