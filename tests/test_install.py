"""`aivy-mcp setup` writes each client's config, backs up, and is idempotent."""
import json
from pathlib import Path

from aivy_mcp import install


def test_setup_writes_every_client_found_and_leaves_existing_entries_alone(tmp_path, monkeypatch):
    home = tmp_path
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text('[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
    (home / ".cursor").mkdir()
    (home / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "y"}}}), encoding="utf-8")
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    (home / "Library/Application Support/Claude").mkdir(parents=True)
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/uvx" if name == "uvx" else None)
    monkeypatch.setattr(install, "setup_claude_code", lambda uvx, key, force, home: "skipped: test")

    results = install.run(["codex", "claude-desktop", "cursor", "claude-code"], key=None, force=False, home=home)
    assert results["codex"].startswith("configured") and results["cursor"].startswith("configured")
    assert results["claude-desktop"].startswith("configured") and results["claude-code"].startswith("skipped")

    codex = (home / ".codex/config.toml").read_text(encoding="utf-8")
    assert '[mcp_servers.other]' in codex and '[mcp_servers.aivy]' in codex
    assert 'command = "/opt/homebrew/bin/uvx"' in codex and 'args = ["aivy-mcp"]' in codex and "AIVY_API_KEY" not in codex
    cursor = json.loads((home / ".cursor/mcp.json").read_text())
    assert cursor["mcpServers"]["other"] == {"command": "y"}
    assert cursor["mcpServers"]["aivy"] == {"command": "/opt/homebrew/bin/uvx", "args": ["aivy-mcp"]}
    desktop = json.loads((home / "Library/Application Support/Claude/claude_desktop_config.json").read_text())
    assert desktop["mcpServers"]["aivy"]["args"] == ["aivy-mcp"]
    assert list((home / ".codex").glob("config.toml.bak-*")) and list((home / ".cursor").glob("mcp.json.bak-*"))

    # Second run: nothing changes, no new backups.
    again = install.run(["codex", "cursor"], key=None, force=False, home=home)
    assert all(v.startswith("already configured") for v in again.values())
    assert len(list((home / ".codex").glob("config.toml.bak-*"))) == 1

    # --force with a key replaces the entry and stores the key.
    forced = install.run(["codex", "cursor"], key="aivy_sk_k1", force=True, home=home)
    assert all(v.startswith("configured") for v in forced.values())
    codex = (home / ".codex/config.toml").read_text(encoding="utf-8")
    assert codex.count("[mcp_servers.aivy]") == 1 and 'AIVY_API_KEY = "aivy_sk_k1"' in codex and "[mcp_servers.other]" in codex
    assert json.loads((home / ".cursor/mcp.json").read_text())["mcpServers"]["aivy"]["env"] == {"AIVY_API_KEY": "aivy_sk_k1"}


def test_clients_that_are_not_installed_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    results = install.run(["cursor", "claude-desktop", "claude-code"], key=None, force=False, home=tmp_path)
    assert all(v.startswith("skipped") for v in results.values())
    # Codex is created when absent: it is the one client whose file may not exist yet.
    assert install.run(["codex"], key=None, force=False, home=tmp_path)["codex"].startswith("configured")
    assert (tmp_path / ".codex/config.toml").exists()
