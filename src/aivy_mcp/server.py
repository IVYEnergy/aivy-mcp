"""MCP server exposing the Aivy Developer API as tools.

Run with `uvx aivy-mcp` (stdio). Configuration by environment:

    AIVY_API_KEY   personal key from the Aivy app (required)
    AIVY_API_URL   API base, default https://bridge.ivyenergy.com.au/v1

Tool descriptions are the contract the model reads, so they say what a
call changes, what it cannot change, and what the server will do on its
own (hand a forced mode back after its duration).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from aivy_mcp.client import NO_KEY_MESSAGE, AivyClient, AivyError, save_key
from aivy_mcp.ui import UI_URIS, UI_URIS_BY_NAME, render_template

# Cards in chat (MCP Apps, io.modelcontextprotocol/ui; ChatGPT reads the
# same resources through its openai/* aliases). The extension needs mcp 2.x;
# on 1.x the tools still work, as text.
try:
    from mcp.server.apps import Apps
    apps: "Apps | None" = Apps()
except Exception:  # noqa: BLE001 — older SDK
    apps = None


# The SDK reads an extension's tools and resources when the server is built,
# so the server is constructed at the bottom of this module, after every
# tool is defined. Plain tools are collected here and registered then.
_PLAIN_TOOLS: list[tuple[Any, dict[str, Any]]] = []


def _tool(**kwargs: Any):
    def register(fn):
        _PLAIN_TOOLS.append((fn, kwargs))
        return fn
    return register


def ui_tool(resource_uri: str, *, invoking: str, invoked: str):
    """Decorator: a tool whose result the host may render as a card."""
    meta = {"openai/outputTemplate": resource_uri, "openai/toolInvocation/invoking": invoking,
            "openai/toolInvocation/invoked": invoked}
    if apps is not None:
        return apps.tool(resource_uri=resource_uri, meta=meta)
    return _tool(meta=meta)


_INSTRUCTIONS = (
        "Tools for the user's own solar + battery system through the Aivy platform (Ivy Energy, Australia). "
        "Call list_sites first; every other tool takes one of its site ids (vendor:station_id). "
        "Power is in watts (battery_w > 0 charging, grid_w > 0 exporting), SOC in percent, prices in "
        "Australian cents per kWh. Readings refresh about once a minute: do not poll faster. "
        "Changing the mode or Ai+ settings needs a control-scope key; a forced (manual) mode set here is "
        "time-boxed and the server returns the site to its previous mode when the time is up. "
        "Sites named sandbox:... are shared demo data: readable, never controllable. "
        "No site yet? list_vendors then connect_site links the user's own system. "
        "No key yet (tools answer 'No Aivy API key')? register_account + verify_registration creates an account "
        "and key from the chat; sign_in does the same for an existing account. "
        "Call get_agent_guide once at the start of a conversation: it is the playbook (which tools answer "
        "which questions, when to confirm, how to report) and takes precedence over guesses."
)

# Site names seen in list_sites, so a status card can show "Home" rather
# than "sungrow:1445844" without a second API call.
_site_names: dict[str, str] = {}

_client: AivyClient | None = None


def _c() -> AivyClient:
    global _client
    if _client is None:
        _client = AivyClient()
    return _client


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def _run(coro) -> str:
    try:
        return _dump(await coro)
    except AivyError as e:
        return _dump({"error": str(e)})


# ── account: register or sign in from the chat (no key needed) ──

@_tool()
async def get_registration_terms(locale: str = "en") -> str:
    """Whether sign-up is open and the terms of use / privacy policy a new account accepts (version, hash and
    a link). Show the link to the person and ask whether they accept BEFORE register_account."""
    return await _run(_c().terms(locale))


@_tool()
async def register_account(email: str, password: str, display_name: str | None = None, locale: str = "en") -> str:
    """Create an Aivy account from the chat, step 1 of 2: a verification code is emailed to `email`. Needs no API
    key. Ask the person to choose the password (8+ characters; it is also their Aivy app password); do not invent
    one. Then ask them for the code and call verify_registration. The reply looks the same whether or not the
    address already has an account: a code that never arrives means check spam or use sign_in instead."""
    body: dict[str, Any] = {"identifier": email, "password": password, "locale": locale}
    if display_name:
        body["display_name"] = display_name
    return await _run(_c().register(body))


@_tool()
async def verify_registration(registration_id: str, registration_session: str, code: str, accept_terms: bool,
                              key_name: str = "chat", scope: str = "read") -> str:
    """Create an Aivy account from the chat, step 2 of 2: confirms the emailed code, accepts the terms (only pass
    accept_terms=true after the person said yes), creates the account and an API key, and stores that key on this
    computer (~/.aivy/credentials.json) so every other tool works from now on, in this and future sessions.
    scope read (observe) or control (may change settings; only if the person asked). Never repeat the key back;
    say it is saved and can be managed at https://bridge.ivyenergy.com.au/account."""
    if not accept_terms:
        return _dump({"error": "the person must accept the terms first (get_registration_terms shows them)"})
    try:
        out = await _c().verify({"registration_id": registration_id, "registration_session": registration_session,
                                 "code": code, "accept_terms": True, "key_name": key_name, "scope": scope})
    except AivyError as e:
        return _dump({"error": str(e)})
    key = out.pop("api_key", None)
    out.pop("note", None)
    if key:
        path = save_key(_c().api_url, key, username=out.get("username"), key_name=key_name)
        _c().use_key(key)
        out["key_saved"] = str(path)
        out["note"] = "the API key is saved on this computer and in use now; it is not shown"
    return _dump(out)


@_tool()
async def sign_in(username: str, password: str, key_name: str = "chat", scope: str = "read") -> str:
    """Use an existing Aivy account from the chat: creates an API key with the account's username (or email)
    and password, stores it on this computer (~/.aivy/credentials.json) and uses it from now on. Needs no
    existing key. scope read or control (control only if the person asked). The password is used once and not
    stored; never repeat the key back."""
    try:
        out = await _c().create_key_with_password(username, password, {"name": key_name, "scope": scope})
    except AivyError as e:
        return _dump({"error": str(e)})
    key = out.pop("key", None)
    out.pop("note", None)
    if key:
        path = save_key(_c().api_url, key, username=username, key_name=key_name)
        _c().use_key(key)
        out["key_saved"] = str(path)
        out["note"] = "the API key is saved on this computer and in use now; it is not shown"
    return _dump(out)


# ── playbook ──

PLAYBOOK = (Path(__file__).parent / "playbook.md").read_text(encoding="utf-8")


@_tool()
async def get_agent_guide() -> str:
    """The Aivy agent playbook: which tools answer which questions, the rules for writes (confirm first, forced
    mode is time-boxed, never guess numbers, do not poll), the step-by-step workflows (status, diagnosis, prices,
    earnings, selling tonight, keeping a reserve, charging now, connecting a system, Ai+ tuning) and what each
    error code means. Read it once at the start of a conversation."""
    return PLAYBOOK


# ── account / sites ──

@_tool()
async def whoami() -> str:
    """Check the API key: who it belongs to and whether it may control (scope read or control). Without a key
    it says so: then register_account (new) or sign_in (existing account) sets one up from the chat."""
    if not _c().has_key:
        return _dump({"error": NO_KEY_MESSAGE, "has_key": False})
    return await _run(_c().me())


@_tool()
async def list_sites() -> str:
    """List the user's solar + battery sites across vendors. Returns id (vendor:station_id, used by every other
    tool), vendor, name, online and the vendor's headline power in W. Start here."""
    try:
        out = await _c().sites()
    except AivyError as e:
        return _dump({"error": str(e)})
    for site in out.get("sites", []):
        if site.get("id") and site.get("name"):
            _site_names[site["id"]] = site["name"]
    return _dump(out)


@_tool()
async def get_site(site_id: str) -> str:
    """Facts about one site: timezone, battery capacity (kWh) and where that figure came from, max charge/discharge
    power (W), export limit (W), the SOC floor Ai+ keeps, live SOC and usable kWh, current energy mode and
    forced windows, today's energy and income when the vendor reports them."""
    return await _run(_c().site(site_id))


@ui_tool(UI_URIS_BY_NAME["status"], invoking="Reading the battery", invoked="Live status")
async def get_status(site_id: str) -> dict[str, Any]:
    """Live reading: SOC (%), pv_w, load_w, grid_w (>0 exporting), battery_w (>0 charging). A null channel is one
    the vendor does not report, not zero. `source` is vendor (fresh) or stored (last kept reading).
    Hosts that support cards render this as a status card."""
    try:
        out = await _c().status(site_id)
    except AivyError as e:
        return {"error": str(e), "id": site_id}
    if site_id in _site_names:
        out["name"] = _site_names[site_id]
    return out


@_tool()
async def get_curve(site_id: str, range: str = "1d", day: str | None = None) -> str:
    """Power and SOC history. range 1d = 5-minute points for one local day (day = YYYY-MM-DD, default today);
    7d / 30d = daily values. Returns times (UTC), local_times, and series per channel (pv, load, battery, grid,
    soc). Large: summarise before quoting."""
    return await _run(_c().curve(site_id, range, day))


@_tool()
async def get_earnings(site_id: str, date: str | None = None, window_start: str | None = None,
                       window_end: str | None = None) -> str:
    """Energy bought, sold and self-consumed for a day (YYYY-MM-DD, 'today' or 'yesterday'), priced at the actual
    Amber half-hourly prices when Amber is linked. Optional HH:MM window for part of the day. An estimate from
    vendor power samples, not the bill; read `coverage` and say so when it is partial."""
    return await _run(_c().earnings(site_id, date, window_start, window_end))


@_tool()
async def get_operations(site_id: str, limit: int = 20) -> str:
    """What the platform did to the site and why, newest first: each automatic (Ai+) and manual command with its
    reason and the prices at the time. Use it to explain 'why did it charge at 3am'."""
    return await _run(_c().operations(site_id, limit))


@ui_tool(UI_URIS_BY_NAME["prices"], invoking="Fetching Amber prices", invoked="Prices")
async def get_prices(site_id: str, hours: int = 8, resolution: int = 30) -> dict[str, Any]:
    """Amber Electric prices for the site: the current interval and the forecast as import price and feed-in earn
    (c/kWh incl. GST), with a summary of cheapest/dearest periods. resolution 30 (up to 24 h) or 5 (up to 4 h).
    Fails with 409 when no Amber account is linked to the site. Hosts that support cards render a price chart."""
    try:
        return await _c().prices(site_id, hours, resolution)
    except AivyError as e:
        return {"error": str(e), "id": site_id}


# ── connecting systems ──

@_tool()
async def list_vendors() -> str:
    """Every inverter / battery brand Aivy can connect, whether it is available on this deployment, what
    connecting one needs (`connect`: serial + NMI, a system id, or an OAuth sign-in at the vendor) and how much
    of it can be controlled. Use before connect_site."""
    return await _run(_c().vendors())


@_tool()
async def connect_site(vendor: str, sn: str | None = None, nmi: str | None = None, system_id: str | None = None,
                       region: str = "AU") -> str:
    """CHANGES THE ACCOUNT (control scope). Confirm with the user before calling.

    Link the user's system to their Aivy account. Serial-number vendors (Sungrow and FoxESS need sn + nmi,
    Sigenergy needs system_id, most others sn) return `connected` site ids. OAuth vendors (AlphaESS, SolaX)
    return an `authorize_url`: give it to the user to open in a browser and approve; the site then appears in
    list_sites within a minute. Demo sites need no connecting."""
    body: dict[str, Any] = {"vendor": vendor, "region": region}
    for key, value in (("sn", sn), ("nmi", nmi), ("system_id", system_id)):
        if value:
            body[key] = value
    return await _run(_c().connect_site(body))


@_tool()
async def disconnect_site(site_id: str) -> str:
    """CHANGES THE ACCOUNT (control scope). Confirm with the user before calling.

    Remove a site from the user's account. The vendor-side link and history are left alone; the account just
    stops seeing it. Demo sites cannot be removed."""
    return await _run(_c().disconnect_site(site_id))


# ── alerts ──

@_tool()
async def list_alerts(site_id: str) -> str:
    """The account's alert rules on a site: what each watches (soc %, import/export price c/kWh, forecast
    temperature °C), above/below which threshold, whether it emails, and when it last fired."""
    return await _run(_c().alerts(site_id))


@_tool()
async def create_alert(site_id: str, name: str, metric: str, operator: str, threshold: float,
                       email: bool = False, cooldown_minutes: int = 360, message: str = "",
                       active_start: str = "00:00", active_end: str = "00:00", locale: str = "en") -> str:
    """Set up a threshold alert: metric soc | import_price | export_price | temperature, operator above | below.
    Every firing goes to the app inbox (and the phone when the app is installed); email=True also mails the
    account's verified address (whoami.email_alerts says whether that works). Quiet for cooldown_minutes after
    each firing (min 15). An alert only tells the person something; it never changes what the battery does.
    Confirm the rule with the user before creating it."""
    body: dict[str, Any] = {"name": name, "metric": metric, "operator": operator, "threshold": threshold,
                            "email": email, "cooldown_minutes": cooldown_minutes, "message": message,
                            "active_start": active_start, "active_end": active_end, "locale": locale}
    return await _run(_c().create_alert(site_id, body))


@_tool()
async def delete_alert(site_id: str, rule_id: int) -> str:
    """Retire an alert rule (past firings stay in the inbox). Confirm with the user first."""
    return await _run(_c().delete_alert(site_id, rule_id))


@_tool()
async def get_alert_events(site_id: str, limit: int = 20) -> str:
    """What fired on the site, newest first: the rule, the value that tripped it, when, how it was delivered and
    whether it was emailed. Use for 'did anything happen overnight?'."""
    return await _run(_c().alert_events(site_id, limit))


@_tool()
async def send_test_alert_email() -> str:
    """Send one test alert email to the account's verified address so the person sees what an alert looks like.
    409 when the account has no verified email."""
    return await _run(_c().test_alert_email())


# ── control ──

@_tool()
async def get_energy_mode(site_id: str) -> str:
    """Current energy mode (selfConsumption | amber_smart = Ai+ | forced), its forced windows, the SOC floor, the
    Ai+ variant and settings when on Ai+, and any pending timed hand-back (reverts_at / reverts_to)."""
    return await _run(_c().energy_mode(site_id))


@_tool(meta={"openai/widgetAccessible": True})
async def set_energy_mode(
    site_id: str,
    mode: str,
    duration_minutes: int | None = None,
    action: str | None = None,
    power_w: int | None = None,
    target_soc: int | None = None,
    stop_soc: int | None = None,
    time_slots: list[dict] | None = None,
    variant: str | None = None,
) -> str:
    """CHANGES THE SITE (control scope). Confirm with the user before calling.

    mode: selfConsumption (inverter default), amber_smart (Ai+, price-driven trading) or forced (manual windows).
    forced is always time-boxed: duration_minutes (default 240, max 1440); afterwards the server returns the site
    to the mode it had before. Shorthand for forced: action=charge|discharge with power_w and target_soc (charge)
    or stop_soc (discharge) builds one window starting now. Or pass explicit time_slots:
    [{type, start 'HH:MM', end 'HH:MM', target_soc|stop_soc, power, pace}] in site local time.
    variant picks the Ai+ engine when mode is amber_smart. The SOC floor and export limits cannot be overridden.
    Takes effect on the engine's next tick (about a minute)."""
    body: dict[str, Any] = {"mode": mode}
    for key, value in (("duration_minutes", duration_minutes), ("action", action), ("power_w", power_w),
                       ("target_soc", target_soc), ("stop_soc", stop_soc), ("time_slots", time_slots),
                       ("variant", variant)):
        if value is not None:
            body[key] = value
    return await _run(_c().set_energy_mode(site_id, body))


@_tool()
async def get_ai_plus(site_id: str) -> str:
    """Ai+ (amber_smart) configuration: active or not, engine variant and options, effective settings, and
    `tunable`: every lever with type, min, max, unit and default. Read before update_ai_plus."""
    return await _run(_c().ai_plus(site_id))


@_tool()
async def update_ai_plus(site_id: str, settings: dict[str, Any] | None = None, variant: str | None = None) -> str:
    """CHANGES THE SITE (control scope). Confirm with the user before calling.

    Set Ai+ levers (names and ranges from get_ai_plus `tunable`) and/or switch engine variant. Validated against
    the variant that will run; if any value is rejected nothing changes and `rejected` says why. Does not switch
    the mode: use set_energy_mode(mode='amber_smart') for that."""
    return await _run(_c().patch_ai_plus(site_id, variant, settings or {}))


# ── planning (nothing is applied) ──

@ui_tool(UI_URIS_BY_NAME["plan"], invoking="Planning the sale", invoked="Sell plan")
async def plan_best_price_discharge(site_id: str, window_start: str | None = None, window_end: str | None = None,
                                    reserve_soc: int | None = None, min_sell_c: float | None = None,
                                    battery_capacity_kwh: float | None = None) -> dict[str, Any]:
    """Propose when to sell: picks the best-paid Amber intervals in the window (HH:MM) for the battery's usable
    energy above reserve_soc, optionally only above min_sell_c c/kWh. Returns intervals, expected revenue, the
    average it beats and proposed time_slots. Nothing is applied; apply with set_energy_mode(mode='forced',
    time_slots=...) after the user agrees. Hosts that support cards show the plan with an Apply button; a click
    on it is the user's yes."""
    body = {k: v for k, v in {"window_start": window_start, "window_end": window_end, "reserve_soc": reserve_soc,
                              "min_sell_c": min_sell_c, "battery_capacity_kwh": battery_capacity_kwh}.items()
            if v is not None}
    try:
        out = await _c().plan_best_price_discharge(site_id, body)
    except AivyError as e:
        return {"error": str(e), "site_id": site_id}
    out["site_id"] = site_id  # the card's Apply button needs it
    return out


@_tool()
async def plan_discharge_to_target(site_id: str, end: str, target_soc: int, start: str | None = None,
                                   battery_capacity_kwh: float | None = None) -> str:
    """Propose a steady discharge that lands on target_soc at end (HH:MM, site local), from the real capacity, SOC
    and power limits. Returns power_w and proposed time_slots. Nothing is applied."""
    body: dict[str, Any] = {"end": end, "target_soc": target_soc}
    if start:
        body["start"] = start
    if battery_capacity_kwh:
        body["battery_capacity_kwh"] = battery_capacity_kwh
    return await _run(_c().plan_discharge_to_target(site_id, body))


# ── server ──

if apps is not None:
    for _uri, _name in UI_URIS.items():
        apps.add_html_resource(_uri, render_template(_name), name=f"aivy-{_name}-card",
                               title=f"Aivy {_name} card", prefers_border=True)

mcp = _Server("aivy", instructions=_INSTRUCTIONS, **({"extensions": [apps]} if apps is not None else {}))
for _fn, _kwargs in _PLAIN_TOOLS:
    mcp.tool(**_kwargs)(_fn)


def main() -> None:
    # `aivy-mcp setup [...]` configures the MCP clients on this computer;
    # anything else runs the server on stdio.
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from aivy_mcp.install import main as setup_main
        raise SystemExit(setup_main(sys.argv[2:]))
    mcp.run()


if __name__ == "__main__":
    main()
