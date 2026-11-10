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
    assert 'command = "/opt/homebrew/bin/uvx"' in codex and 'args = ["aivy-mcp@latest"]' in codex and "AIVY_API_KEY" not in codex
    cursor = json.loads((home / ".cursor/mcp.json").read_text())
    assert cursor["mcpServers"]["other"] == {"command": "y"}
    assert cursor["mcpServers"]["aivy"] == {"command": "/opt/homebrew/bin/uvx", "args": ["aivy-mcp@latest"]}
    desktop = json.loads((home / "Library/Application Support/Claude/claude_desktop_config.json").read_text())
    assert desktop["mcpServers"]["aivy"]["args"] == ["aivy-mcp@latest"]
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
    assert codex.count("# Aivy: solar") == 1
    assert json.loads((home / ".cursor/mcp.json").read_text())["mcpServers"]["aivy"]["env"] == {"AIVY_API_KEY": "aivy_sk_k1"}


def test_an_entry_from_before_at_latest_is_rewritten_and_keeps_its_key(tmp_path, monkeypatch):
    """Installs before 0.11.3 ran `uvx aivy-mcp`, which uv never refreshes.
    Rerunning the installer moves them to @latest without --force; anything
    else under the aivy name is left alone."""
    home = tmp_path
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text(
        '[mcp_servers.other]\ncommand = "x"\n\n# Aivy: solar + battery tools (sign up or sign in from the chat; no key needed)\n'
        '[mcp_servers.aivy]\ncommand = "/usr/local/bin/uvx"\nargs = ["aivy-mcp"]\nstartup_timeout_sec = 120\n\n'
        '[mcp_servers.aivy.env]\nAIVY_API_KEY = "aivy_sk_old"\n', encoding="utf-8")
    (home / ".cursor").mkdir()
    (home / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {
        "aivy": {"command": "/usr/local/bin/uvx", "args": ["aivy-mcp"], "env": {"AIVY_API_KEY": "aivy_sk_old"}}}}),
        encoding="utf-8")
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    (home / "Library/Application Support/Claude").mkdir(parents=True)
    (home / "Library/Application Support/Claude/claude_desktop_config.json").write_text(json.dumps({"mcpServers": {
        "aivy": {"command": "python", "args": ["-m", "aivy_mcp"]}}}), encoding="utf-8")
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/uvx" if name == "uvx" else None)

    results = install.run(["codex", "cursor", "claude-desktop"], key=None, force=False, home=home)
    assert results["codex"].startswith("updated") and results["cursor"].startswith("updated")
    assert results["claude-desktop"].startswith("already configured")  # not ours to rewrite
    codex = (home / ".codex/config.toml").read_text(encoding="utf-8")
    assert codex.count("[mcp_servers.aivy]") == 1 and 'args = ["aivy-mcp@latest"]' in codex
    assert 'AIVY_API_KEY = "aivy_sk_old"' in codex and "[mcp_servers.other]" in codex and codex.count("# Aivy: solar") == 1
    cursor = json.loads((home / ".cursor/mcp.json").read_text())["mcpServers"]["aivy"]
    assert cursor == {"command": "/opt/homebrew/bin/uvx", "args": ["aivy-mcp@latest"], "env": {"AIVY_API_KEY": "aivy_sk_old"}}
    again = install.run(["codex", "cursor"], key=None, force=False, home=home)
    assert all(v.startswith("already configured") for v in again.values())


def test_claude_code_cli_entry_is_moved_to_at_latest_with_its_key(monkeypatch, tmp_path):
    calls = []

    class Done:
        def __init__(self, code, out=""):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:3] == ["mcp", "get"]:
            return Done(0, "aivy:\n  Scope: User\n  Type: stdio\n  Command: /usr/local/bin/uvx\n  Args: aivy-mcp\n"
                           "  Environment:\n    AIVY_API_KEY=aivy_sk_old\n")
        return Done(0)

    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(install.subprocess, "run", fake_run)
    out = install.setup_claude_code("/opt/homebrew/bin/uvx", None, False, tmp_path)
    assert out.startswith("updated")
    assert [c[1:3] for c in calls] == [["mcp", "get"], ["mcp", "remove"], ["mcp", "add"]]
    assert calls[-1][-2:] == ["/opt/homebrew/bin/uvx", "aivy-mcp@latest"] and "AIVY_API_KEY=aivy_sk_old" in calls[-1]

    calls.clear()
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, **kw: (calls.append(cmd) or Done(0, "Args: aivy-mcp@latest")))
    assert install.setup_claude_code("/opt/homebrew/bin/uvx", None, False, tmp_path).startswith("already configured")
    assert len(calls) == 1


def test_clients_that_are_not_installed_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    results = install.run(["cursor", "claude-desktop", "claude-code"], key=None, force=False, home=tmp_path)
    assert all(v.startswith("skipped") for v in results.values())
    # Codex is created when absent: it is the one client whose file may not exist yet.
    assert install.run(["codex"], key=None, force=False, home=tmp_path)["codex"].startswith("configured")
    assert (tmp_path / ".codex/config.toml").exists()
