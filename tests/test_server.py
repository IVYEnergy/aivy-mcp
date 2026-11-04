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
                     "plan_best_price_discharge", "plan_discharge_to_target"}
    for t in tools:
        if t.name in ("set_energy_mode", "update_ai_plus"):
            assert "CHANGES THE SITE" in t.description and "Confirm with the user" in t.description
        else:
            assert "CHANGES THE SITE" not in t.description


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
