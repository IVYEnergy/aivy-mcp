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
    assert names == {"list_sites", "get_status", "get_curve", "get_earnings", "get_operations",
                     "get_prices", "get_energy_mode", "set_energy_mode",
                     "plan_discharge",
                     "list_vendors", "connect_site", "disconnect_site", "get_agent_guide",
                     "list_alerts", "create_alert", "delete_alert",
                     "get_registration_terms", "register_account", "verify_registration", "sign_in",
                     "get_strategy", "start_strategy", "end_strategy"}
    for t in tools:
        if t.name in ("set_energy_mode", "connect_site", "disconnect_site"):
            assert "CHANGES THE" in t.description and "Confirm with the user" in t.description
        else:
            assert "CHANGES THE" not in t.description


def test_every_tool_carries_a_title_and_the_four_behaviour_hints():
    """Hosts decide from these whether to ask before a call, so each of the
    four is stated for every tool rather than left to the protocol default
    (which is "destructive, open world"). Written out here independently of
    the server's table."""
    titles = {
        "get_registration_terms": "Sign-up terms", "register_account": "Create account (step 1)",
        "verify_registration": "Create account (step 2)", "sign_in": "Sign in", "get_agent_guide": "Agent playbook",
        "list_sites": "My sites", "get_status": "Site status", "get_curve": "Power history",
        "get_earnings": "Day earnings", "get_operations": "Operations log", "get_prices": "Amber prices",
        "list_vendors": "Supported brands", "connect_site": "Connect a system",
        "disconnect_site": "Disconnect a system", "list_alerts": "Alert rules", "create_alert": "Add alert",
        "delete_alert": "Remove alert", "get_strategy": "Running strategy",
        "start_strategy": "Preview or start strategy", "end_strategy": "Stop strategy",
        "get_energy_mode": "Energy mode", "set_energy_mode": "Set energy mode", "plan_discharge": "Discharge plan"}
    writes = {"register_account", "verify_registration", "sign_in", "connect_site", "disconnect_site",
              "create_alert", "delete_alert", "start_strategy", "end_strategy", "set_energy_mode"}
    destructive = {"disconnect_site", "delete_alert", "start_strategy", "end_strategy", "set_energy_mode"}
    repeatable_writes = {"disconnect_site", "delete_alert", "end_strategy"}
    open_world = {"get_status", "get_curve", "get_earnings", "get_prices", "connect_site", "start_strategy",
                  "end_strategy", "set_energy_mode", "plan_discharge"}
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == set(titles)
    for t in tools:
        sent = t.model_dump(by_alias=True, exclude_none=True)  # what the host receives
        assert sent["title"] == titles[t.name], t.name
        assert sent["annotations"] == {
            "title": titles[t.name], "readOnlyHint": t.name not in writes, "destructiveHint": t.name in destructive,
            "idempotentHint": t.name not in writes or t.name in repeatable_writes,
            "openWorldHint": t.name in open_world}, t.name


def test_every_tool_reaches_the_host_with_a_description():
    """A docstring assembled with + is an expression, not a docstring: the
    tool is then listed with no description and the model has only its name."""
    tools = {t.name: t.description or "" for t in asyncio.run(server.mcp.list_tools())}
    assert [name for name, text in tools.items() if len(text.strip()) < 40] == []


def test_descriptions_reach_the_host_without_source_indentation():
    """A docstring is wrapped and indented for the source file. Sent as it is,
    every line break carries four spaces to the model, and a phrase that wraps
    in the source cannot be matched by the ChatGPT export's rewrites."""
    tools = {t.name: t.description or "" for t in asyncio.run(server.mcp.list_tools())}
    for name, text in tools.items():
        assert "  " not in text and "\n\n\n" not in text, name
        assert all(line == "" or not line.startswith(" ") for line in text.split("\n")), name
    assert "stores that key on this computer (~/.aivy/credentials.json) so every other tool" in tools["verify_registration"]
    assert tools["set_energy_mode"].split("\n")[0] == "CHANGES THE SITE (control scope). Confirm with the user before calling."


def test_every_argument_says_how_to_fill_it_in():
    """The input schema is where a host shows the model what an argument
    means, so each one carries a description, and a closed set of values is
    an enum the host can check rather than a sentence."""
    tools = {t.name: t.model_dump(by_alias=True, exclude_none=True)["inputSchema"]  # what the host receives
             for t in asyncio.run(server.mcp.list_tools())}
    bare =[f"{name}.{arg}" for name, schema in tools.items()
            for arg, spec in schema.get("properties", {}).items() if len(spec.get("description", "")) < 10]
    assert bare == []

    def enum(tool: str, arg: str) -> list:
        spec = tools[tool]["properties"][arg]
        return next(s["enum"] for s in [spec, *spec.get("anyOf", [])] if "enum" in s)

    assert enum("set_energy_mode", "mode") == ["selfConsumption", "amber_smart", "au_forecast_export",
                                               "au_negative_price", "au_smart_energy", "au_weather_season", "forced"]
    assert enum("set_energy_mode", "action") == ["charge", "discharge"]
    assert enum("create_alert", "metric") == enum("list_alerts", "metric") == ["soc", "import_price", "export_price",
                                                                               "temperature"]
    assert enum("create_alert", "operator") == ["above", "below"]
    assert enum("sign_in", "scope") == enum("verify_registration", "scope") == ["read", "control"]
    assert enum("get_curve", "range") == ["1d", "7d", "30d"]
    assert enum("plan_discharge", "goal") == ["best_price", "target_soc"]
    rules = tools["start_strategy"]["properties"]["rules"]["description"]
    assert "first match wins" in rules and "between" in rules, "the rule shape is part of the contract"
    assert tools["start_strategy"]["required"] == ["site_id", "name", "rules"]


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


def test_get_energy_mode_carries_the_ai_plus_configuration(monkeypatch):
    """One read answers both "what mode am I in" and "what can I tune": the
    Ai+ levers come back under `ai_plus`, on Ai+ or not."""
    mode = {"id": "sungrow:1", "energy_mode": "selfConsumption", "time_slots": [], "soc_floor_pct": 10.0,
            "variant": None, "settings": None}
    ai_plus = {"id": "sungrow:1", "active": False, "variant": "smart", "variant_options": ["smart", "band"],
               "settings": {"hard_floor": 10}, "soc_floor_pct": 10.0,
               "tunable": {"hard_floor": {"type": "int", "default": 8, "min": 0, "max": 50, "unit": "%"}}}
    ai_plus_fails = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sites/sungrow:1/energy-mode"):
            return httpx.Response(200, json=mode)
        assert request.url.path.endswith("/sites/sungrow:1/ai-plus") and request.method == "GET"
        return httpx.Response(503, json={"detail": "busy"}) if ai_plus_fails else httpx.Response(200, json=ai_plus)

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = asyncio.run(server.get_energy_mode("sungrow:1"))
    assert out["energy_mode"] == "selfConsumption" and out["variant"] is None and out["site_id"] == "sungrow:1"
    assert out["ai_plus"] == {"active": False, "variant": "smart", "variant_options": ["smart", "band"],
                              "settings": {"hard_floor": 10}, "tunable": ai_plus["tunable"]}

    ai_plus_fails = True  # the mode is still the answer
    out = asyncio.run(server.get_energy_mode("sungrow:1"))
    assert out["energy_mode"] == "selfConsumption" and out["ai_plus"]["error"].startswith("503")

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": {"error": "no such site"}})

    monkeypatch.setattr(server, "_client", _client_with(refused))
    out = asyncio.run(server.get_energy_mode("sungrow:9"))
    assert out["error"].startswith("404") and "ai_plus" not in out


def test_api_errors_are_returned_as_data_not_raised(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": {"error": "this API key is read-only"}})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = json.loads(asyncio.run(server.set_energy_mode("sungrow:1", "selfConsumption")))
    assert out["error"].startswith("403") and "read-only" in out["error"]


def test_set_energy_mode_without_a_mode_changes_ai_plus_only(monkeypatch):
    """No mode = the mode stays and the Ai+ levers / engine change (PATCH
    ai-plus); arguments that only mean something with a mode are refused
    before anything is sent."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "sungrow:1", "variant": "band", "applied": {"hard_floor": 20}})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = json.loads(asyncio.run(server.set_energy_mode("sungrow:1", settings={"hard_floor": 20}, variant="band")))
    assert out["applied"] == {"hard_floor": 20}
    assert seen == [("PATCH", "/v1/sites/sungrow:1/ai-plus", {"variant": "band", "settings": {"hard_floor": 20}})]

    seen.clear()  # only the engine
    asyncio.run(server.set_energy_mode("sungrow:1", variant="smart"))
    assert seen == [("PATCH", "/v1/sites/sungrow:1/ai-plus", {"variant": "smart", "settings": {}})]

    seen.clear()
    for kwargs in ({}, {"action": "charge", "power_w": 5000}, {"settings": {"hard_floor": 20}, "duration_minutes": 60},
                   {"mode": "amber_smart", "settings": {"hard_floor": 20}}):
        assert "error" in json.loads(asyncio.run(server.set_energy_mode("sungrow:1", **kwargs))), kwargs
    assert seen == [], "nothing is sent for a call that cannot mean one thing"

    asyncio.run(server.set_energy_mode("sungrow:1", "au_negative_price", settings={"reserve_soc": 60}))
    assert seen == [("PUT", "/v1/sites/sungrow:1/energy-mode", {"mode": "au_negative_price", "settings": {"reserve_soc": 60}})]


def test_without_a_key_the_server_starts_and_tools_say_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    c = client_mod.AivyClient(api_key=None)
    assert not c.has_key
    monkeypatch.setattr(server, "_client", c)
    out = asyncio.run(server.list_sites())
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
        if request.url.path.endswith("/sites"):
            return httpx.Response(200, json={"sites": [], "warnings": []})
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
    me = asyncio.run(server.list_sites())["account"]
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


def test_the_playbook_is_one_text_in_every_form(monkeypatch):
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
    monkeypatch.setenv("AIVY_MCP_NO_UPDATE_CHECK", "1")
    assert asyncio.run(server.get_agent_guide()) == playbook


def test_a_newer_release_is_reported_after_the_playbook_per_launch_mode(monkeypatch):
    """The version check is one PyPI GET, cached, silent on failure, and the
    note names the way out for the client that started this process."""
    from aivy_mcp import __version__, update
    monkeypatch.delenv("AIVY_MCP_NO_UPDATE_CHECK", raising=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"info": {"version": "99.0.0"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(update.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(update, "_checked", {})
    monkeypatch.setenv("AIVY_MCP_LAUNCH", "mcpb")
    guide = asyncio.run(server.get_agent_guide())
    assert guide.startswith("# Aivy agent playbook") and "\n## Update\n" in guide
    assert f"aivy-mcp 99.0.0 is on PyPI; this server is {__version__}" in guide
    assert update.RELEASES_URL in guide and ".mcpb" in guide
    asyncio.run(server.get_agent_guide())
    assert calls == ["/pypi/aivy-mcp/json"]  # cached: one lookup per process

    assert "uvx aivy-mcp@latest" in update.notice_for("99.0.0", "uvx")
    assert update.INSTALLER in update.notice_for("99.0.0", "other")
    assert update.is_newer("0.12.0", "0.11.3") and not update.is_newer("0.11.3", "0.11.3")
    assert not update.is_newer(None, "0.11.3") and not update.is_newer("0.9.9", "0.11.3")

    # Same version, or PyPI unreachable: the playbook alone.
    monkeypatch.setattr(update, "_checked", {"latest": __version__})
    assert "## Update" not in asyncio.run(server.get_agent_guide())

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(update.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(down), **kw))
    monkeypatch.setattr(update, "_checked", {})
    assert "## Update" not in asyncio.run(server.get_agent_guide())


def test_eval_checker_judges_order_forbidden_tools_and_confirmation():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("check", Path(__file__).resolve().parents[1] / "evals/check.py")
    check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
    case = next(c for c in check.CASES if c["id"] == "sell-tonight-propose")
    good = [{"name": "list_sites", "args": {}}, {"name": "plan_discharge", "args": {"goal": "best_price", "reserve_soc": 20}}]
    assert check.check(case, good, "Tonight 18:00 to 20:30 would earn about $4.10. Apply it?") == []
    bad = good + [{"name": "set_energy_mode", "args": {"mode": "forced"}}]
    problems = check.check(case, bad, "Done.")
    assert any("must not call set_energy_mode" in p for p in problems)
    assert any("confirmation" in p for p in problems)
    apply = next(c for c in check.CASES if c["id"] == "charge-now-apply")
    assert check.check(apply, [{"name": "set_energy_mode", "args": {"mode": "forced", "action": "charge",
                                "power_w": 5000, "target_soc": 90, "duration_minutes": 120}}], "Charging.") == []
    assert check.check(apply, [{"name": "set_energy_mode", "args": {"mode": "forced", "action": "charge"}}], "x")
    # One tool can be both the preview and the write: forbid_args tells them apart.
    propose = next(c for c in check.CASES if c["id"] == "strategy-propose")
    preview = {"name": "start_strategy", "args": {"site_id": "sungrow:1", "name": "x", "rules": []}}
    assert check.check(propose, [preview], "Start it?") == []
    started = {"name": "start_strategy", "args": {**preview["args"], "accept_risk": True, "proposal_id": "1.a"}}
    assert any("must not call start_strategy with" in p for p in check.check(propose, [preview, started], "Start it?"))
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
    for name, uri in (("get_status", UI_URIS_BY_NAME["status"]), ("plan_discharge", UI_URIS_BY_NAME["plan"]),
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


def test_list_sites_says_whose_key_it_is(monkeypatch):
    """The first call of a conversation answers both "which sites" and "may
    this key control": the account comes back with the sites."""
    account_down = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(503, json={"detail": "busy"}) if account_down else \
                httpx.Response(200, json={"user": "Ann", "scope": "control", "email_alerts": True})
        return httpx.Response(200, json={"sites": [{"id": "sungrow:1", "name": "Home"}], "warnings": []})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = asyncio.run(server.list_sites())
    assert [s["id"] for s in out["sites"]] == ["sungrow:1"]
    assert out["account"] == {"user": "Ann", "scope": "control", "email_alerts": True}
    account_down = True  # the sites are still the answer
    out = asyncio.run(server.list_sites())
    assert [s["id"] for s in out["sites"]] == ["sungrow:1"] and out["account"]["error"].startswith("503")


def test_status_card_data_carries_the_site_name_when_known(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"user": "Ann", "scope": "read"})
        if request.url.path.endswith("/sites"):
            return httpx.Response(200, json={"sites": [{"id": "sungrow:1", "name": "Home", "vendor": "sungrow"}], "warnings": []})
        return httpx.Response(200, json={"id": "sungrow:1", "soc_pct": 55, "pv_w": 100, "source": "vendor", "at": "2026-11-06T00:00:00+00:00"})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    asyncio.run(server.list_sites())
    out = asyncio.run(server.get_status("sungrow:1"))
    assert isinstance(out, dict) and out["name"] == "Home" and out["soc_pct"] == 55
    plan = asyncio.run(server.plan_discharge("sungrow:1", "best_price"))
    assert plan["site_id"] == "sungrow:1"


def test_plan_discharge_asks_the_endpoint_its_goal_names(monkeypatch):
    """One planning tool: goal picks the calculation, and an argument that
    belongs to the other goal is refused rather than silently dropped."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"proposed_time_slots": []})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    asyncio.run(server.plan_discharge("sungrow:1", "best_price", window_start="17:00", window_end="21:00",
                                      reserve_soc=20, min_sell_c=30))
    asyncio.run(server.plan_discharge("sungrow:1", "target_soc", window_end="21:00", target_soc=30))
    assert seen == [("/v1/sites/sungrow:1/plans/best-price-discharge",
                     {"window_start": "17:00", "window_end": "21:00", "reserve_soc": 20, "min_sell_c": 30}),
                    ("/v1/sites/sungrow:1/plans/discharge-to-target", {"end": "21:00", "target_soc": 30})]
    seen.clear()
    for goal, kwargs in (("best_price", {"target_soc": 30}), ("target_soc", {"target_soc": 30}),
                         ("target_soc", {"window_end": "21:00"}),
                         ("target_soc", {"window_end": "21:00", "target_soc": 30, "reserve_soc": 20}), ("cheapest", {})):
        assert "error" in asyncio.run(server.plan_discharge("sungrow:1", goal, **kwargs)), (goal, kwargs)
    assert seen == []


def test_get_status_carries_the_site_s_facts_with_the_live_reading(monkeypatch):
    """One read answers "how is it doing" and "how big is it": the reading
    on top, capacity and limits under `site`. Either half alone is still an
    answer; both failing is an error."""
    reading = {"id": "sungrow:7", "at": "2026-11-06T00:00:00+00:00", "soc_pct": 55, "pv_w": 100, "source": "vendor"}
    facts = {"id": "sungrow:7", "vendor": "sungrow", "name": "Shed", "timezone": "Australia/Adelaide",
             "battery_capacity_kwh": 13.5, "max_power_w": 5000, "soc_floor_pct": 10.0, "soc_pct": 54.0}
    down: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        kind = "reading" if request.url.path.endswith("/status") else "facts"
        assert request.url.path in ("/v1/sites/sungrow:7/status", "/v1/sites/sungrow:7")
        if kind in down:
            return httpx.Response(502, json={"detail": "vendor cloud did not answer"})
        return httpx.Response(200, json=reading if kind == "reading" else facts)

    monkeypatch.setattr(server, "_client", _client_with(handler))
    out = asyncio.run(server.get_status("sungrow:7"))
    assert out["soc_pct"] == 55 and out["source"] == "vendor" and out["name"] == "Shed"
    assert out["site"] == {k: v for k, v in facts.items() if k != "id"} and "reading_error" not in out

    down.add("reading")  # a site connected a minute ago: no live reading yet, the facts are still shown
    out = asyncio.run(server.get_status("sungrow:7"))
    assert "error" not in out and out["reading_error"].startswith("502")
    assert out["soc_pct"] == 54.0 and out["source"] == "stored" and out["site"]["battery_capacity_kwh"] == 13.5

    down.symmetric_difference_update({"reading", "facts"})  # only the facts are missing
    out = asyncio.run(server.get_status("sungrow:7"))
    assert out["soc_pct"] == 55 and out["site"]["error"].startswith("502") and "name" not in out

    down.add("reading")
    out = asyncio.run(server.get_status("sungrow:7"))
    assert out["error"].startswith("502") and out["id"] == "sungrow:7" and "site" not in out


def test_strategy_mode_previews_then_starts_only_what_was_previewed(monkeypatch):
    """One tool, two calls: the dry run returns a proposal_id and nothing
    starts without it, with other rules, on another site, or too late."""
    rules = [{"when": [{"metric": "export_price", "operator": "above", "value": 40}],
              "then": {"action": "discharge", "stop_soc": 30}, "note": "sell spikes"}]
    calls: list[tuple[str, str, dict | None]] = []
    running = {"status": "none"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/strategy/preview"):
            return httpx.Response(200, json={"site_id": "sungrow:1", "readings": {"soc": 60, "export_price": 55},
                                             "would": {"action": "discharge", "rule": 0, "why": "x"},
                                             "rules": [{**rules[0], "text": "when feed-in above 40c -> discharge down to 30%"}],
                                             "fallback": {"action": "auto", "text": "auto"}, "limits": {}})
        if request.method == "POST":
            return httpx.Response(200, json={"site_id": "sungrow:1", "status": "active", "name": body["name"],
                                             "rules": body["rules"], "remaining_minutes": 1440})
        if request.method == "PUT":
            return httpx.Response(200, json={"site_id": "sungrow:1", "status": "active", "name": "Sell spikes",
                                             "rules": body["rules"], "remaining_minutes": 600})
        if request.method == "DELETE":
            return httpx.Response(200, json={"site_id": "sungrow:1", "status": "ended", "end_reason": "stopped"})
        return httpx.Response(200, json={"site_id": "sungrow:1", "limits": {}, **running})

    def writes() -> list[tuple[str, str, dict | None]]:
        return [c for c in calls if c[0] != "GET" and not c[1].endswith("/preview")]

    monkeypatch.setattr(server, "_client", _client_with(handler))
    proposed = asyncio.run(server.start_strategy("sungrow:1", "Sell spikes", rules, summary="Sell above 40c.",
                                                 duration_minutes=720))
    assert proposed["status"] == "proposed" and proposed["would"]["action"] == "discharge" and "replaces" not in proposed
    assert proposed["proposal"] == {"name": "Sell spikes", "summary": "Sell above 40c.", "rules": rules, "duration_minutes": 720}
    assert ("POST", "/v1/sites/sungrow:1/strategy/preview", {"rules": rules}) in calls, "the preview sends only what the API evaluates"
    assert writes() == [], "a preview starts nothing"
    proposal, proposal_id = proposed["proposal"], proposed["proposal_id"]

    def start(site_id="sungrow:1", **changes):
        return asyncio.run(server.start_strategy(site_id, **{**proposal, "accept_risk": True, "proposal_id": proposal_id,
                                                              **changes}))

    other_rules = [{**rules[0], "then": {"action": "discharge", "stop_soc": 5}}]
    for refused in (start(proposal_id=None), start(proposal_id="1.abc"), start(rules=other_rules),
                    start(duration_minutes=10080), start(site_id="sungrow:2")):
        assert "error" in refused
    monkeypatch.setattr(server.time, "time", lambda now=server.time.time(): now + 16 * 60)
    assert "15 minutes" in start()["error"]
    monkeypatch.undo()
    monkeypatch.setattr(server, "_client", _client_with(handler))
    assert writes() == [], "nothing was sent for a start that was not previewed"

    # what comes back from a card: other key order, 40 as 40.0
    from_card = json.loads(json.dumps(proposal, sort_keys=True).replace('"value": 40', '"value": 40.0'))
    started = start(**from_card)
    assert started["status"] == "active"
    assert writes() == [("POST", "/v1/sites/sungrow:1/strategy", {**from_card, "accept_risk": True})]

    # while one runs, the same two calls replace its rules; the lease moves only when asked
    running.update(status="active", name="Sell spikes", remaining_minutes=600)
    calls.clear()
    again = asyncio.run(server.start_strategy("sungrow:1", "Sell spikes", other_rules))
    assert again["replaces"] == {"name": "Sell spikes", "remaining_minutes": 600}
    replaced = asyncio.run(server.start_strategy("sungrow:1", "Sell spikes", other_rules, accept_risk=True,
                                                 proposal_id=again["proposal_id"]))
    assert replaced["status"] == "active" and writes() == [("PUT", "/v1/sites/sungrow:1/strategy", {"rules": other_rules})]

    assert asyncio.run(server.get_strategy("sungrow:1"))["status"] == "active"
    assert asyncio.run(server.end_strategy("sungrow:1"))["status"] == "ended"
    assert calls[-1][:2] == ("DELETE", "/v1/sites/sungrow:1/strategy")


def test_alert_writes_return_the_card_already_up_to_date(monkeypatch):
    """A host renders the alerts card for create/delete too, so their result is
    the current rule list plus what changed; list_alerts carries the rule the
    person asked for as a draft the card fills in, and what fired lately."""
    rules: list[dict] = [{"id": 1, "name": "lower soc", "metric": "soc", "operator": "below", "threshold": 20}]
    fired = [{"id": 9, "rule_id": 1, "value": 18.0, "at": "2026-11-17T20:10:00+00:00", "emailed": True}]
    history_down = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/alerts/events"):
            assert request.url.params["limit"] == "20"
            return httpx.Response(503, json={"detail": "busy"}) if history_down else \
                httpx.Response(200, json={"site_id": "sungrow:1", "events": fired})
        if request.method == "POST":
            body = json.loads(request.content)
            rules.append({"id": 2, **body})
            return httpx.Response(200, json={"id": 2, **body})
        if request.method == "DELETE":
            rules[:] = [r for r in rules if r["id"] != int(request.url.path.rsplit("/", 1)[1])]
            return httpx.Response(200, json={"deleted": 1})
        return httpx.Response(200, json={"site_id": "sungrow:1", "rules": list(rules)})

    monkeypatch.setattr(server, "_client", _client_with(handler))
    listed = asyncio.run(server.list_alerts("sungrow:1", metric="soc", operator="above", threshold=70))
    assert listed["draft"] == {"metric": "soc", "operator": "above", "threshold": 70, "name": "SOC above 70%"}
    assert listed["events"] == fired and "events_error" not in listed, "one call answers 'did anything fire?'"
    assert "draft" not in asyncio.run(server.list_alerts("sungrow:1"))
    history_down = True  # the rules still come back, and the gap is said
    partial = asyncio.run(server.list_alerts("sungrow:1"))
    assert [r["id"] for r in partial["rules"]] == [1] and partial["events"] == [] and partial["events_error"].startswith("503")
    history_down = False
    created = asyncio.run(server.create_alert("sungrow:1", "", "soc", "above", 70))
    assert created["created"]["id"] == 2 and created["created"]["name"] == "SOC above 70%"
    assert [r["id"] for r in created["rules"]] == [1, 2] and created["site_id"] == "sungrow:1"
    deleted = asyncio.run(server.delete_alert("sungrow:1", 1))
    assert deleted["deleted"] == 1 and [r["id"] for r in deleted["rules"]] == [2]


def test_server_reports_the_package_version_and_the_readme_counts_the_cards():
    from aivy_mcp import __version__
    from aivy_mcp.ui import UI_URIS_BY_NAME
    from pathlib import Path
    if hasattr(server.mcp, "version"):  # mcp 2.x puts it in initialize's serverInfo
        assert server.mcp.version == __version__
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    tools = asyncio.run(server.mcp.list_tools())
    with_card = sum(1 for t in tools if (t.meta or {}).get("openai/outputTemplate"))
    assert with_card == 16 and len(UI_URIS_BY_NAME) == 12
    assert "sixteen of the tools as cards" in readme
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


def test_the_server_and_every_card_carry_the_aivy_mark():
    from aivy_mcp.ui import UI_URIS_BY_NAME, render_template, server_icons
    icons = server_icons()
    assert [i["mimeType"] for i in icons] == ["image/png", "image/svg+xml"]
    assert all(i["src"].startswith("data:image/") for i in icons)
    if hasattr(server.mcp, "icons") and server.mcp.icons is not None:  # mcp 2.x: shown beside the server name
        assert [str(i.src)[:15] for i in server.mcp.icons] == ["data:image/png;", "data:image/svg+"]
    for name in UI_URIS_BY_NAME:
        html = render_template(name)
        assert "B.logo = 'data:image/svg+xml;base64," in html and "/*LOGO*/" not in html, name


def test_an_environment_value_that_is_not_a_key_counts_as_no_key(monkeypatch, tmp_path):
    """Claude Desktop extensions template the environment; an empty optional
    field arrives as the literal placeholder. It must not be sent as a key."""
    monkeypatch.setenv("AIVY_HOME", str(tmp_path))
    monkeypatch.setenv("AIVY_API_KEY", "${user_config.api_key}")
    assert client_mod.env_key() is None
    assert not client_mod.AivyClient(api_url="https://x.test/v1").has_key
    client_mod.save_key("https://x.test/v1", "aivy_sk_saved")
    assert client_mod.AivyClient(api_url="https://x.test/v1").api_key == "aivy_sk_saved", "the saved key wins over junk"
    monkeypatch.setenv("AIVY_API_KEY", " aivy_sk_env ")
    assert client_mod.AivyClient(api_url="https://x.test/v1").api_key == "aivy_sk_env", "a real key in the environment wins"
