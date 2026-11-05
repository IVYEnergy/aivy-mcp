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
                     "list_alerts", "create_alert", "delete_alert", "get_alert_events", "send_test_alert_email"}
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


def test_missing_key_is_a_clear_message(monkeypatch):
    monkeypatch.delenv("AIVY_API_KEY", raising=False)
    with pytest.raises(client_mod.AivyError, match="AIVY_API_KEY"):
        client_mod.AivyClient(api_key=None)


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
        assert "http://" not in html.replace("http://www.w3.org/2000/svg", "") and "https://" not in html, "no external assets"


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
