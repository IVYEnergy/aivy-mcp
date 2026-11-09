"""The MCP tool set mirrors the Developer API, and errors come back as data."""
import asyncio
import json

import httpx
import pytest

from aivy_mcp import client as client_mod
from aivy_mcp import server


def _client_with(handler) -> client_mod.AivyClient:
    c = client_mod.AivyClient(api_key="aivy_sk_test", api_url="https://example.test/v1")
    c._http = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(handler),
                                headers={"Authorization": "Bearer aivy_sk_test"})
    return c


def test_every_tool_is_listed_and_writes_say_so():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"whoami", "list_sites", "get_site", "get_status", "get_curve", "get_earnings", "get_operations",
                     "get_prices", "get_energy_mode", "set_energy_mode", "get_ai_plus", "update_ai_plus",
                     "plan_best_price_discharge", "plan_discharge_to_target",
                     "list_vendors", "connect_site", "disconnect_site", "get_agent_guide",
                     "list_alerts", "create_alert", "delete_alert", "get_alert_events", "send_test_alert_email",
                     "get_registration_terms", "register_account", "verify_registration", "sign_in"}
    for t in tools:
        if t.name in ("set_energy_mode", "update_ai_plus", "connect_site", "disconnect_site"):
            assert "CHANGES THE" in t.description and "Confirm with the user" in t.description
        else:
            assert "CHANGES THE" not in t.description


def test_set_energy_mode_sends_only_the_given_fields_and_bearer_auth(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"energy_mode": "forced", "reverts_to": "amber_smart"})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = json.loads(asyncio.run(server.set_energy_mode("sungrow:1", "forced", duration_minutes=60,
                                                        action="charge", power_w=5000, target_soc=90)))
    assert out["reverts_to"] == "amber_smart"
    assert seen["auth"] == "Bearer aivy_sk_test"
    assert seen["url"].endswith("/sites/sungrow:1/energy-mode")
    assert seen["body"] == {"mode": "forced", "duration_minutes": 60, "action": "charge", "power_w": 5000,
                            "target_soc": 90}


def test_api_errors_are_returned_as_data_not_raised(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": {"error": "this API key is read-only"}})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = json.loads(asyncio.run(server.update_ai_plus("sungrow:1", {"hard_floor": 20})))
    assert out["error"].startswith("403") and "read-only" in out["error"]


def test_without_a_key_the_server_starts_and_tools_say_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    c = client_mod.AivyClient(api_key=None)
    assert not c.has_key
    monkeypatch.setattr(server, "_client", c)
    out = json.loads(asyncio.run(server.whoami()))
    assert out["has_key"] is False and "register_account" in out["error"]
    out = asyncio.run(server.get_status("sungrow:1"))  # card tools return structured dicts
    assert "No Aivy API key" in out["error"]


def test_registration_in_the_chat_stores_the_key_locally_and_uses_it(monkeypatch, tmp_path):
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization"),
                     json.loads(request.content) if request.content else None))
        if request.url.path.endswith("/auth/terms"):
            return httpx.Response(200, json={"registration_available": True, "version": "v1", "web": "https://x/terms"})
        if request.url.path.endswith("/auth/register"):
            return httpx.Response(200, json={"registration_id": "r1", "registration_session": "s1", "status": "pending_email"})
        if request.url.path.endswith("/auth/verify"):
            return httpx.Response(200, json={"user_id": 9, "username": "bob@example.com", "display_name": "Bob",
                                             "api_key": "aivy_sk_secret123", "note": "store it"})
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"user": "Bob", "scope": "read"})
        return httpx.Response(404, json={"detail": "nope"})

    c = client_mod.AivyClient(api_key=None, api_url="https://example.test/v1")
    c._http = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_client", c)
    terms = asyncio.run(server.get_registration_terms())  # a card tool: structured dict
    assert terms["registration_available"] is True
    reg = json.loads(asyncio.run(server.register_account("bob@example.com", "a long private phrase", "Bob")))
    assert reg["registration_id"] == "r1"
    refused = json.loads(asyncio.run(server.verify_registration("r1", "s1", "246810", accept_terms=False)))
    assert "accept the terms" in refused["error"]
    done = json.loads(asyncio.run(server.verify_registration("r1", "s1", "246810", accept_terms=True, scope="read")))
    assert "api_key" not in done and done["username"] == "bob@example.com" and "saved" in done["note"]
    saved = json.loads((tmp_path / "credentials.json").read_text())
    assert saved["https://example.test/v1"]["api_key"] == "aivy_sk_secret123"
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    # The key is in use at once, and a fresh client picks it up from the file.
    me = json.loads(asyncio.run(server.whoami()))
    assert me["user"] == "Bob" and seen[-1][2] == "Bearer aivy_sk_secret123"
    assert client_mod.AivyClient(api_key=None, api_url="https://example.test/v1").api_key == "aivy_sk_secret123"
    # Public calls carried no Authorization header; the verify body asked for a key by name.
    assert [s_[2] for s_ in seen[:3]] == [None, None, None]
    assert seen[2][3]["accept_terms"] is True and seen[2][3]["key_name"] == "chat"


def test_sign_in_uses_the_password_once_with_basic_auth(monkeypatch, tmp_path):
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization"); seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 3, "name": "chat", "scope": "control", "key": "aivy_sk_fromlogin"})

    c = client_mod.AivyClient(api_key=None, api_url="https://example.test/v1")
    c._http = httpx.AsyncClient(base_url="https://example.test/v1", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_client", c)
    out = json.loads(asyncio.run(server.sign_in("alice", "pw-2026", scope="control")))
    assert "key" not in out and out["scope"] == "control" and c.api_key == "aivy_sk_fromlogin"
    assert seen["auth"] == "Basic YWxpY2U6cHctMjAyNg==" and seen["body"] == {"name": "chat", "scope": "control"}
    assert "pw-2026" not in (tmp_path / "credentials.json").read_text()


def test_connect_site_sends_only_what_the_vendor_needs(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"vendor": "sungrow", "connected": ["sungrow:S4"], "contested": []})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = json.loads(asyncio.run(server.connect_site("sungrow", sn="A234", nmi="2001234567")))
    assert out["connected"] == ["sungrow:S4"]
    assert seen["method"] == "POST" and seen["body"] == {"vendor": "sungrow", "region": "AU", "sn": "A234", "nmi": "2001234567"}


def test_the_playbook_is_one_text_in_every_form():
    """The MCP tool, the Claude skill and the hosted /v1/agent-guide.md must
    never drift: SKILL.md is the playbook with a front-matter block on top."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "src/aivy_mcp/playbook.md").read_text(encoding="utf-8")
    assert playbook.startswith("# Aivy agent playbook") and "## 4. Workflows" in playbook
    skill = (root / "skills/aivy/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: aivy\n")
    assert skill.split("---\n", 2)[2].lstrip("\n") == playbook
    hosted = root.parents[1] / "backend/app/static/agent-guide.md"
    if hosted.exists():  # monorepo only; the public repo has no backend
        assert hosted.read_text(encoding="utf-8") == playbook
    assert asyncio.run(server.get_agent_guide()) == playbook


def test_eval_checker_judges_order_forbidden_tools_and_confirmation():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("check", Path(__file__).resolve().parents[1] / "evals/check.py")
    check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
    case = next(c for c in check.CASES if c["id"] == "sell-tonight-propose")
    good = [{"name": "list_sites", "args": {}}, {"name": "plan_best_price_discharge", "args": {"reserve_soc": 20}}]
    assert check.check(case, good, "Tonight 18:00 to 20:30 would earn about $4.10. Apply it?") == []
    bad = good + [{"name": "set_energy_mode", "args": {"mode": "forced"}}]
    problems = check.check(case, bad, "Done.")
    assert any("must not call set_energy_mode" in p for p in problems)
    assert any("confirmation" in p for p in problems)
    apply = next(c for c in check.CASES if c["id"] == "charge-now-apply")
    assert check.check(apply, [{"name": "set_energy_mode", "args": {"mode": "forced", "action": "charge",
                                "power_w": 5000, "target_soc": 90, "duration_minutes": 120}}], "Charging.") == []
    assert check.check(apply, [{"name": "set_energy_mode", "args": {"mode": "forced", "action": "charge"}}], "x")
    # Claude Code stream-json transcripts are understood too.
    import tempfile, os
    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__aivy__get_status", "input": {"site_id": "sungrow:1"}},
        {"type": "text", "text": "At 55%."}]}})]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("\n".join(lines)); path = f.name
    calls, text = check.load_transcript(path); os.unlink(path)
    assert calls == [{"name": "get_status", "args": {"site_id": "sungrow:1"}}] and text == "At 55%."


def test_cards_are_registered_as_mcp_apps_resources_and_linked_to_their_tools():
    """status / plan / prices tools point at ui:// resources; each resource
    is a self-contained HTML document with the bridge and styles inlined."""
    from aivy_mcp.ui import UI_URIS, UI_URIS_BY_NAME, render_template
    if server.apps is None:
        pytest.skip("mcp 1.x: no MCP Apps extension")
    from mcp.server.apps import APP_MIME_TYPE
    resources = {str(r.uri): r for r in asyncio.run(server.mcp.list_resources())}
    assert set(UI_URIS) <= set(resources)
    for uri in UI_URIS:
        assert (resources[uri].mime_type or getattr(resources[uri], "mimeType", None)) == APP_MIME_TYPE
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name, uri in (("get_status", UI_URIS_BY_NAME["status"]), ("plan_best_price_discharge", UI_URIS_BY_NAME["plan"]),
                      ("get_prices", UI_URIS_BY_NAME["prices"])):
        meta = tools[name].meta or {}
        assert meta["ui"]["resourceUri"] == uri and meta["openai/outputTemplate"] == uri
        assert tools[name].output_schema, "structured output feeds the card"
    assert (tools["set_energy_mode"].meta or {}).get("openai/widgetAccessible") is True
    for name in UI_URIS_BY_NAME:
        html = render_template(name)
        assert html.startswith("<!doctype html>") and "window.AivyBridge" in html and "--brand:" in html
        assert "/*BRIDGE_JS*/" not in html and "/*BASE_CSS*/" not in html
        # Links to our site are fine; loading anything from the network is not (default CSP blocks it).
        assert "<script src=" not in html and "<link " not in html and 'src="http' not in html and "url(http" not in html, \
            "no external assets"


def test_status_card_data_carries_the_site_name_when_known(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sites"):
            return httpx.Response(200, json={"sites": [{"id": "sungrow:1", "name": "Home", "vendor": "sungrow"}], "warnings": []})
        return httpx.Response(200, json={"id": "sungrow:1", "soc_pct": 55, "pv_w": 100, "source": "vendor", "at": "2026-11-06T00:00:00+00:00"})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    asyncio.run(server.list_sites())
    out = asyncio.run(server.get_status("sungrow:1"))
    assert isinstance(out, dict) and out["name"] == "Home" and out["soc_pct"] == 55
    plan = asyncio.run(server.plan_best_price_discharge("sungrow:1"))
    assert plan["site_id"] == "sungrow:1"


def test_server_reports_the_package_version_and_the_readme_counts_the_cards():
    from aivy_mcp import __version__
    from aivy_mcp.ui import UI_URIS_BY_NAME
    from pathlib import Path
    if hasattr(server.mcp, "version"):  # mcp 2.x puts it in initialize's serverInfo
        assert server.mcp.version == __version__
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    tools = asyncio.run(server.mcp.list_tools())
    with_card = sum(1 for t in tools if (t.meta or {}).get("openai/outputTemplate"))
    assert with_card == 13 and len(UI_URIS_BY_NAME) == 12
    assert "thirteen of the tools as cards" in readme
    for t in tools:
        if (t.meta or {}).get("openai/outputTemplate"):
            assert f"`{t.name}`" in readme, f"README card table lacks {t.name}"


def test_registry_manifest_matches_the_package():
    """server.json is what the MCP Registry publishes; the README carries the
    ownership token the registry checks on PyPI."""
    from aivy_mcp import __version__
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "server.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "io.github.IVYEnergy/aivy-mcp"
    assert manifest["version"] == __version__
    assert len(manifest["description"]) <= 100
    pkg = manifest["packages"][0]
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == "aivy-mcp" and pkg["version"] == __version__
    assert pkg["transport"] == {"type": "stdio"}
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {manifest['name']} " in readme or f"mcp-name: {manifest['name']} -->" in readme
    assert (root / "pyproject.toml").read_text(encoding="utf-8").count(f'version = "{__version__}"') == 1


def test_a_key_saved_by_another_process_is_picked_up_without_a_restart(monkeypatch, tmp_path):
    """Claude Desktop runs one server process for the chat and another for a
    card's own tool calls. The card signs in and saves the key; the chat's
    process, built without a key, must start using it on its next call."""
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"username": "u"})

    c = client_mod.AivyClient(api_url="https://x.test/v1")
    c._http = httpx.AsyncClient(base_url="https://x.test/v1", transport=httpx.MockTransport(handler))
    assert not c.has_key
    with pytest.raises(client_mod.AivyError):
        asyncio.run(c.me())
    assert "auth" not in seen, "nothing goes upstream without a key"
    client_mod.save_key("https://x.test/v1", "aivy_sk_fromcard", username="u")  # the other process
    assert c.has_key
    assert asyncio.run(c.me()) == {"username": "u"}
    assert seen["auth"] == "Bearer aivy_sk_fromcard"
