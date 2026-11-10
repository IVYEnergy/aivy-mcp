"""`aivy-mcp setup`: write the Aivy server into every MCP client on this computer.

    uvx --from aivy-mcp aivy-mcp-setup            # every client found
    aivy-mcp setup --client codex --key aivy_sk_...

Clients and their files:
  codex           ~/.codex/config.toml                      ([mcp_servers.aivy])
  claude-code     `claude mcp add` when the CLI is present, else ~/.claude.json
  claude-desktop  ~/Library/Application Support/Claude/claude_desktop_config.json
                  (%APPDATA%\\Claude\\claude_desktop_config.json on Windows)
  cursor          ~/.cursor/mcp.json

Each entry runs `uvx aivy-mcp@latest` by the absolute path of uvx, because
GUI apps start with a minimal PATH; `@latest` makes uv check PyPI at every
start, so restarting the client is how a person updates. An entry that
still runs plain `uvx aivy-mcp` (installs before 0.11.3, which uv keeps at
the version it first resolved) is rewritten to @latest, its key kept.
Other existing entries are left alone unless --force; every file is backed
up before it is changed. No key is needed: the person signs up or signs in
from the chat afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "aivy"
PACKAGE = "aivy-mcp@latest"  # what uvx runs: refreshed from PyPI at every start
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
    entry: dict = {"command": uvx, "args": [PACKAGE]}
    if key:
        entry["env"] = {"AIVY_API_KEY": key}
    return entry


def _is_stale_uvx(entry: dict) -> bool:
    """An entry we wrote before @latest: uvx with the bare package name."""
    args = entry.get("args") or []
    return Path(str(entry.get("command", ""))).name.startswith("uvx") and args == ["aivy-mcp"]


def _write_json_client(path: Path, uvx: str, key: str | None, force: bool) -> str:
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return f"skipped: {path} is not valid JSON"
    servers = data.setdefault("mcpServers", {})
    outcome = "configured"
    if SERVER_NAME in servers and not force:
        existing = servers[SERVER_NAME] if isinstance(servers[SERVER_NAME], dict) else {}
        if not _is_stale_uvx(existing):
            return f"already configured: {path}"
        key = key or (existing.get("env") or {}).get("AIVY_API_KEY")
        outcome = "updated to uvx aivy-mcp@latest"
    _backup(path)
    servers[SERVER_NAME] = _server_json(uvx, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"{outcome}: {path}"


def setup_codex(uvx: str, key: str | None, force: bool, home: Path) -> str:
    path = home / ".codex" / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    outcome = "configured"
    if f"[mcp_servers.{SERVER_NAME}]" in text:
        lines, out, ours, skip = text.splitlines(), [], [], False
        for line in lines:
            if line.strip().startswith(f"[mcp_servers.{SERVER_NAME}"):
                skip = True
                ours.append(line)
                continue
            if skip and line.strip().startswith("["):
                skip = False
            (ours if skip else out).append(line)
        if not force:
            stale = any(re.match(r'\s*args\s*=\s*\[\s*"aivy-mcp"\s*\]', line) for line in ours)
            if not stale:
                return f"already configured: {path}"
            found = next((re.match(r'\s*AIVY_API_KEY\s*=\s*"([^"]*)"', line) for line in ours if "AIVY_API_KEY" in line), None)
            key = key or (found.group(1) if found else None)
            outcome = "updated to uvx aivy-mcp@latest"
        out = [line for line in out if not line.startswith("# Aivy: solar + battery tools")]  # re-added below
        text = "\n".join(out).rstrip() + "\n"
    block = (f"\n# Aivy: solar + battery tools (sign up or sign in from the chat; no key needed)\n"
             f"[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(uvx)}\nargs = [{json.dumps(PACKAGE)}]\n"
             f"startup_timeout_sec = 120\n")
    if key:
        block += f"\n[mcp_servers.{SERVER_NAME}.env]\nAIVY_API_KEY = {json.dumps(key)}\n"
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((text.rstrip("\n") + "\n" if text else "") + block, encoding="utf-8")
    return f"{outcome}: {path}"


def setup_claude_code(uvx: str, key: str | None, force: bool, home: Path) -> str:
    claude = shutil.which("claude")
    if claude:
        outcome = "configured"
        if not force:
            try:
                listed = subprocess.run([claude, "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                listed = None
            if listed is not None and listed.returncode == 0:
                if PACKAGE in listed.stdout or "uvx" not in listed.stdout:
                    return "already configured: claude mcp (user scope)"
                found = re.search(r"AIVY_API_KEY=(\S+)", listed.stdout)  # keep a stored key through the rewrite
                key = key or (found.group(1) if found else None)
                force, outcome = True, "updated to uvx aivy-mcp@latest"
        if force:
            subprocess.run([claude, "mcp", "remove", "-s", "user", SERVER_NAME], capture_output=True, text=True, timeout=30)
        cmd = [claude, "mcp", "add", "-s", "user", SERVER_NAME]
        if key:
            cmd += ["-e", f"AIVY_API_KEY={key}"]
        cmd += ["--", uvx, PACKAGE]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return f"skipped: claude CLI failed ({e})"
        return f"{outcome}: claude mcp (user scope)" if done.returncode == 0 else f"skipped: claude mcp add failed: {done.stderr.strip()[:200]}"
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
    updated = [n for n, o in results.items() if o.startswith("updated")]
    print()
    if configured:
        print("Restart " + ", ".join(configured) + " and say: sign me up to Aivy (or: sign in to my Aivy account).")
    if updated:
        print("Restart " + ", ".join(updated) + ": the entry now runs uvx aivy-mcp@latest, so every restart picks up "
              "the newest release.")
    if not configured and not updated:
        print("Nothing new to configure. Keys are managed at https://bridge.ivyenergy.com.au/account")
    return 0


if __name__ == "__main__":
    sys.exit(main())
