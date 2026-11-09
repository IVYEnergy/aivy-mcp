"""MCP server exposing the Aivy Developer API as tools.

Run with `uvx aivy-mcp` (stdio). Configuration by environment:

    AIVY_API_KEY   personal key (optional: without one, register_account /
                   sign_in create and store a key from the chat)
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

from aivy_mcp import __version__
from aivy_mcp.client import NO_KEY_MESSAGE, AivyClient, AivyError, save_key
from aivy_mcp.ui import UI_URIS, UI_URIS_BY_NAME, render_template, server_icons

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
    """Decorator: a tool whose result the host may render as a card. Card
    tools are also callable from other cards (ChatGPT needs the flag)."""
    meta = {"openai/outputTemplate": resource_uri, "openai/toolInvocation/invoking": invoking,
            "openai/toolInvocation/invoked": invoked, "openai/widgetAccessible": True}
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


async def _data(coro, **extra: Any) -> dict[str, Any]:
    """Like _run, for tools with a card: structured content, errors as data,
    plus the keys the card needs (the site id, for its buttons)."""
    try:
        out = await coro
    except AivyError as e:
        return {"error": str(e), **extra}
    if isinstance(out, dict):
        out.update(extra)
        return out
    return {"data": out, **extra}


# ── account: register or sign in from the chat (no key needed) ──

@ui_tool(UI_URIS_BY_NAME["signup"], invoking="Opening Aivy sign-up", invoked="Aivy sign-up")
async def get_registration_terms(locale: str = "en") -> dict[str, Any]:
    """Whether sign-up is open and the terms of use / privacy policy a new account accepts (version, hash and
    a link). Show the link to the person and ask whether they accept BEFORE register_account. Hosts that
    support cards render a sign-up / sign-in form here instead: the person types email and password into the
    card, which calls register_account / verify_registration / sign_in itself, so nothing passes through the
    chat. When the card is shown, wait for the person to say they are done."""
    try:
        return await _c().terms(locale)
    except AivyError as e:
        return {"error": str(e)}


@_tool(meta={"openai/widgetAccessible": True})
async def register_account(email: str, password: str, display_name: str | None = None, locale: str = "en") -> str:
    """Create an Aivy account from the chat, step 1 of 2: a verification code is emailed to `email`. Needs no API
    key. Ask the person to choose the password (8+ characters; it is also their Aivy app password); do not invent
    one. Then ask them for the code and call verify_registration. The reply looks the same whether or not the
    address already has an account: a code that never arrives means check spam or use sign_in instead."""
    body: dict[str, Any] = {"identifier": email, "password": password, "locale": locale}
    if display_name:
        body["display_name"] = display_name
    return await _run(_c().register(body))


@_tool(meta={"openai/widgetAccessible": True})
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


@_tool(meta={"openai/widgetAccessible": True})
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


@ui_tool(UI_URIS_BY_NAME["sites"], invoking="Listing your sites", invoked="Your sites")
async def list_sites() -> dict[str, Any]:
    """List the user's solar + battery sites across vendors. Returns id (vendor:station_id, used by every other
    tool), vendor, name, online and the vendor's headline power in W. Start here."""
    try:
        out = await _c().sites()
    except AivyError as e:
        return {"error": str(e)}
    for site in out.get("sites", []):
        if site.get("id") and site.get("name"):
            _site_names[site["id"]] = site["name"]
    return out


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


@ui_tool(UI_URIS_BY_NAME["curve"], invoking="Loading history", invoked="Power history")
async def get_curve(site_id: str, range: str = "1d", day: str | None = None) -> dict[str, Any]:
    """Power and SOC history. range 1d = 5-minute points for one local day (day = YYYY-MM-DD, default today);
    7d / 30d = daily values. Returns times (UTC), local_times, and series per channel (pv, load, battery, grid,
    soc). Large: summarise before quoting."""
    return await _data(_c().curve(site_id, range, day), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["earnings"], invoking="Adding up the day", invoked="Earnings")
async def get_earnings(site_id: str, date: str | None = None, window_start: str | None = None,
                       window_end: str | None = None) -> dict[str, Any]:
    """Energy bought, sold and self-consumed for a day (YYYY-MM-DD, 'today' or 'yesterday'), priced at the actual
    Amber half-hourly prices when Amber is linked. Optional HH:MM window for part of the day. An estimate from
    vendor power samples, not the bill; read `coverage` and say so when it is partial."""
    return await _data(_c().earnings(site_id, date, window_start, window_end), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["operations"], invoking="Reading the log", invoked="Operations")
async def get_operations(site_id: str, limit: int = 20) -> dict[str, Any]:
    """What the platform did to the site and why, newest first: each automatic (Ai+) and manual command with its
    reason and the prices at the time. Use it to explain 'why did it charge at 3am'."""
    return await _data(_c().operations(site_id, limit), site_id=site_id)


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

@ui_tool(UI_URIS_BY_NAME["connect"], invoking="Listing supported brands", invoked="Connect a system")
async def list_vendors() -> dict[str, Any]:
    """Every inverter / battery brand Aivy can connect, whether it is available on this deployment, what
    connecting one needs (`connect`: serial + NMI, a system id, or an OAuth sign-in at the vendor) and how much
    of it can be controlled. Use before connect_site."""
    return await _data(_c().vendors())


@_tool(meta={"openai/widgetAccessible": True})
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

_METRIC_UNIT = {"soc": "%", "import_price": "c", "export_price": "c", "temperature": "°C"}
_METRIC_WORD = {"soc": "SOC", "import_price": "Import price", "export_price": "Feed-in price",
                "temperature": "Forecast temperature"}


def _alert_name(metric: str, operator: str, threshold: float) -> str:
    """A rule title when the person did not give one: "SOC above 70%"."""
    value = f"{threshold:g}{_METRIC_UNIT.get(metric, '')}"
    return f"{_METRIC_WORD.get(metric, metric)} {operator} {value}"


async def _alerts_after(site_id: str, **result: Any) -> dict[str, Any]:
    """The alerts card after a write: the site's current rules plus what the
    write did, so the card the host renders is already up to date."""
    out = await _data(_c().alerts(site_id), site_id=site_id)
    out.update(result)
    return out


@ui_tool(UI_URIS_BY_NAME["alerts"], invoking="Loading alerts", invoked="Alerts")
async def list_alerts(site_id: str, name: str | None = None, metric: str | None = None, operator: str | None = None,
                      threshold: float | None = None, email: bool | None = None,
                      cooldown_minutes: int | None = None) -> dict[str, Any]:
    """The account's alert rules on a site: what each watches (soc %, import/export price c/kWh, forecast
    temperature °C), above/below which threshold, whether it emails, and when it last fired.

    When the person asks for a NEW alert, pass the rule here as well (metric soc | import_price | export_price |
    temperature, operator above | below, threshold, email, cooldown_minutes; name is optional): hosts that render
    cards show the form filled in with it and the person's click on "Add alert" creates the rule, which is their
    yes. Then wait for them to say it is done. Without a card, restate the rule and call create_alert once they
    agree."""
    draft: dict[str, Any] | None = None
    if metric or operator or threshold is not None:
        draft = {k: v for k, v in {"name": name, "metric": metric, "operator": operator, "threshold": threshold,
                                    "email": email, "cooldown_minutes": cooldown_minutes}.items() if v is not None}
        if not name and metric and operator and threshold is not None:
            draft["name"] = _alert_name(metric, operator, threshold)
    out = await _data(_c().alerts(site_id), site_id=site_id)
    if draft:
        out["draft"] = draft
    return out


@ui_tool(UI_URIS_BY_NAME["alerts"], invoking="Adding the alert", invoked="Alert added")
async def create_alert(site_id: str, name: str, metric: str, operator: str, threshold: float,
                       email: bool = False, cooldown_minutes: int = 360, message: str = "",
                       active_start: str = "00:00", active_end: str = "00:00", locale: str = "en") -> dict[str, Any]:
    """Set up a threshold alert: metric soc | import_price | export_price | temperature, operator above | below.
    Every firing goes to the app inbox (and the phone when the app is installed); email=True also mails the
    account's verified address (whoami.email_alerts says whether that works). Quiet for cooldown_minutes after
    each firing (min 15). An alert only tells the person something; it never changes what the battery does.
    Confirm the rule with the user before creating it (or let them add it from the list_alerts card). Returns
    the site's rules with the new one under `created`; hosts render that as the alerts card."""
    body: dict[str, Any] = {"name": name or _alert_name(metric, operator, threshold), "metric": metric,
                            "operator": operator, "threshold": threshold, "email": email,
                            "cooldown_minutes": cooldown_minutes, "message": message,
                            "active_start": active_start, "active_end": active_end, "locale": locale}
    try:
        created = await _c().create_alert(site_id, body)
    except AivyError as e:
        return {"error": str(e), "site_id": site_id}
    return await _alerts_after(site_id, created=created)


@ui_tool(UI_URIS_BY_NAME["alerts"], invoking="Removing the alert", invoked="Alert removed")
async def delete_alert(site_id: str, rule_id: int) -> dict[str, Any]:
    """Retire an alert rule (past firings stay in the inbox). Confirm with the user first. Returns the site's
    remaining rules (the alerts card) with the removed id under `deleted`."""
    try:
        await _c().delete_alert(site_id, rule_id)
    except AivyError as e:
        return {"error": str(e), "site_id": site_id}
    return await _alerts_after(site_id, deleted=rule_id)


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


# ── strategy mode: the AI writes the rules, the server runs them ──

_RULE_SHAPE = ("Each rule is {\"when\": [conditions], \"then\": action, \"note\": \"why\"}. A condition is {\"metric\": soc | "
               "import_price | export_price | hour, \"operator\": above | below | between, \"value\": n} (between uses "
               "\"low\" and \"high\"; hour is local time as a decimal, 17.5 = 5:30 pm, low > high wraps midnight). An action is "
               "{\"action\": charge | discharge | hold | auto, \"power_w\"?, \"target_soc\"? (charge), \"stop_soc\"? (discharge)}. "
               "Rules are checked in order, first match wins, `fallback` (default auto) applies when none matches. At most 20 "
               "rules, 4 conditions each.")


def _strategy_body(name: str | None, summary: str | None, rules: list[dict], fallback: dict | None,
                   duration_minutes: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {"rules": rules}
    if name is not None:
        body["name"] = name
    if summary is not None:
        body["summary"] = summary
    if fallback is not None:
        body["fallback"] = fallback
    if duration_minutes is not None:
        body["duration_minutes"] = duration_minutes
    return body


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Reading the strategy", invoked="Strategy")
async def get_strategy(site_id: str) -> dict[str, Any]:
    """Strategy mode on a site: the AI-written rules running now (name, rules, lease, last action, recent
    evaluations), status none when there has never been one, or ended with end_reason. `limits` is what the server
    enforces on every strategy. Hosts render it as a card with a Stop button."""
    return await _data(_c().strategy(site_id), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Checking the rules", invoked="Proposed strategy")
async def preview_strategy(site_id: str, name: str, rules: list[dict], summary: str = "",
                           fallback: dict | None = None, duration_minutes: int = 1440) -> dict[str, Any]:
    """Step 1 of strategy mode ("I want to write my own strategy"): YOU write the rules from what the person
    wants, then call this. It dry-runs them against the site's current readings (nothing starts) and returns which
    rule would fire. Hosts render the proposal as a card: the rules in plain words, the lease, the limits and a
    Start button; the person's click is their acceptance and calls start_strategy. Without a card, read the rules
    back and ask before start_strategy. """ + _RULE_SHAPE + """ duration_minutes is the lease (15 to 10080,
    default a day): the site returns to its previous mode when it ends."""
    body = _strategy_body(None, None, rules, fallback, None)
    out = await _data(_c().preview_strategy(site_id, body), site_id=site_id)
    out["status"] = "proposed"
    out["proposal"] = _strategy_body(name, summary, rules, fallback, duration_minutes)
    return out


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Starting the strategy", invoked="Strategy started")
async def start_strategy(site_id: str, name: str, rules: list[dict], accept_risk: bool, summary: str = "",
                         fallback: dict | None = None, duration_minutes: int = 1440) -> dict[str, Any]:
    """Step 2 of strategy mode: run the rules on the site (control scope; demo sites run a dry run). Only after the
    person has seen the rules and said yes: accept_risk=true is that yes, never assume it. The server evaluates the
    rules every five minutes for the lease, applies each decision through the normal engine (SOC floor and export
    limits hold), allows at most 3 action changes an hour and none within 15 minutes of the last, treats stale
    readings as auto, and returns the site to its previous mode when the lease ends, the strategy is stopped or
    fails, or the mode is changed elsewhere. While it runs, set_energy_mode and update_ai_plus are refused. """ + \
        _RULE_SHAPE
    if not accept_risk:
        return {"error": "the person must see the rules and accept first (preview_strategy shows them)", "site_id": site_id}
    body = _strategy_body(name, summary, rules, fallback, duration_minutes)
    body["accept_risk"] = True
    return await _data(_c().start_strategy(site_id, body), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Changing the rules", invoked="Strategy updated")
async def update_strategy_rules(site_id: str, rules: list[dict], summary: str | None = None,
                                fallback: dict | None = None, duration_minutes: int | None = None) -> dict[str, Any]:
    """Replace the running strategy's rules (and optionally its fallback, summary or lease from now); evaluated at
    once. Confirm the new rules with the person first. 404 when none is running. """ + _RULE_SHAPE
    return await _data(_c().update_strategy(site_id, _strategy_body(None, summary, rules, fallback, duration_minutes)),
                       site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Stopping the strategy", invoked="Strategy stopped")
async def end_strategy(site_id: str) -> dict[str, Any]:
    """Stop the running strategy now; the site goes back to the mode it had before it started. Confirm with the
    person first unless they asked for it."""
    return await _data(_c().end_strategy(site_id), site_id=site_id)


# ── control ──

@ui_tool(UI_URIS_BY_NAME["control"], invoking="Reading the energy mode", invoked="Energy mode")
async def get_energy_mode(site_id: str) -> dict[str, Any]:
    """Current energy mode (selfConsumption | amber_smart = Ai+ | au_forecast_export, au_negative_price, au_smart_energy,
    au_weather_season = the AU-01..04 student strategies | forced), its forced windows, the SOC floor, the
    Ai+ variant and settings when on Ai+, and any pending timed hand-back (reverts_at / reverts_to)."""
    return await _data(_c().energy_mode(site_id), site_id=site_id)


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

    mode: selfConsumption (inverter default), amber_smart (Ai+, price-driven trading), one of the AU Ingenuity
    student strategies: au_forecast_export (AU-01 Smart export: sells evening surplus above a load-forecast reserve),
    au_negative_price (AU-02: blocks export while feed-in is negative, sells above 50% on high prices),
    au_smart_energy (AU-03: battery serves the home only when import is dear, never exports), au_weather_season
    (AU-04: sells above a weather- and season-sized reserve), or forced (manual windows). The au_* modes need Amber
    linked, AU-01 / AU-04 also a Sungrow or Sigenergy site; the server answers 409 with the reason otherwise.
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


@ui_tool(UI_URIS_BY_NAME["aiplus"], invoking="Loading Ai+ settings", invoked="Ai+ settings")
async def get_ai_plus(site_id: str) -> dict[str, Any]:
    """Ai+ (amber_smart) configuration: active or not, engine variant and options, effective settings, and
    `tunable`: every lever with type, min, max, unit and default. Read before update_ai_plus."""
    return await _data(_c().ai_plus(site_id), site_id=site_id)


@_tool(meta={"openai/widgetAccessible": True})
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


@ui_tool(UI_URIS_BY_NAME["plan"], invoking="Planning the discharge", invoked="Discharge plan")
async def plan_discharge_to_target(site_id: str, end: str, target_soc: int, start: str | None = None,
                                   battery_capacity_kwh: float | None = None) -> dict[str, Any]:
    """Propose a steady discharge that lands on target_soc at end (HH:MM, site local), from the real capacity, SOC
    and power limits. Returns power_w and proposed time_slots. Nothing is applied."""
    body: dict[str, Any] = {"end": end, "target_soc": target_soc}
    if start:
        body["start"] = start
    if battery_capacity_kwh:
        body["battery_capacity_kwh"] = battery_capacity_kwh
    return await _data(_c().plan_discharge_to_target(site_id, body), site_id=site_id)


# ── server ──

if apps is not None:
    for _uri, _name in UI_URIS.items():
        apps.add_html_resource(_uri, render_template(_name), name=f"aivy-{_name}-card",
                               title=f"Aivy {_name} card", prefers_border=True)

def _server_kwargs() -> dict[str, Any]:
    """Constructor options the installed SDK understands (2.x reports version
    and website in serverInfo; 1.x has neither)."""
    import inspect
    accepted = inspect.signature(_Server.__init__).parameters
    wanted: dict[str, Any] = {"instructions": _INSTRUCTIONS, "version": __version__, "title": "Aivy Energy",
                              "website_url": "https://github.com/IVYEnergy/aivy-mcp"}
    if apps is not None:
        wanted["extensions"] = [apps]
    if "icons" in accepted:  # the Aivy mark beside the server name, instead of a letter avatar
        try:
            from mcp.types import Icon
            wanted["icons"] = [Icon(src=i["src"], mime_type=i["mimeType"], sizes=i["sizes"]) for i in server_icons()]
        except Exception:  # noqa: BLE001 — older types module
            pass
    return {k: v for k, v in wanted.items() if k in accepted}


mcp = _Server("aivy", **_server_kwargs())
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
