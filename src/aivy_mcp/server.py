"""MCP server exposing the Aivy Developer API as tools.

Run with `uvx aivy-mcp@latest` (stdio). Configuration by environment:

    AIVY_API_KEY   personal key (optional: without one, register_account /
                   sign_in create and store a key from the chat)
    AIVY_API_URL   API base, default https://bridge.ivyenergy.com.au/v1
    AIVY_MCP_NO_UPDATE_CHECK  set to skip the once-per-process PyPI version
                   check that get_agent_guide reports (see update.py)

Tool descriptions are the contract the model reads, so they say what a
call changes, what it cannot change, and what the server will do on its
own (hand a forced mode back after its duration). How to fill an argument
in is said on the argument (its `Field` description, and a `Literal` where
the values are a closed set), which reaches the host in the input schema.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field  # the SDK builds each tool's input schema with pydantic

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


# What each tool is called and how it behaves, for hosts that decide from it whether to ask before a call:
# (title, read_only, destructive, idempotent, open_world).
#   read_only   changes nothing
#   destructive the change cannot be undone or has a real consequence (a command to hardware, a deletion)
#   idempotent  calling again with the same arguments changes nothing more
#   open_world  reaches a system outside the platform (a vendor cloud, Amber)
# Hints are per tool, not per call, so a tool carries its heaviest form: start_strategy also previews, and
# set_energy_mode reaches no hardware on a demo site; both are marked for the real write.
# A tool without a row here does not register: add the row with the tool.
_HINTS: dict[str, tuple[str, bool, bool, bool, bool]] = {
    "get_registration_terms": ("Sign-up terms",             True,  False, True,  False),
    "register_account":       ("Create account (step 1)",   False, False, False, False),
    "verify_registration":    ("Create account (step 2)",   False, False, False, False),
    "sign_in":                ("Sign in",                   False, False, False, False),
    "get_agent_guide":        ("Agent playbook",            True,  False, True,  False),
    "list_sites":             ("My sites",                  True,  False, True,  False),
    "get_status":             ("Site status",               True,  False, True,  True),
    "get_curve":              ("Power history",             True,  False, True,  True),
    "get_earnings":           ("Day earnings",              True,  False, True,  True),
    "get_operations":         ("Operations log",            True,  False, True,  False),
    "get_prices":             ("Amber prices",              True,  False, True,  True),
    "list_vendors":           ("Supported brands",          True,  False, True,  False),
    "connect_site":           ("Connect a system",          False, False, False, True),
    "disconnect_site":        ("Disconnect a system",       False, True,  True,  False),
    "list_alerts":            ("Alert rules",               True,  False, True,  False),
    "create_alert":           ("Add alert",                 False, False, False, False),
    "delete_alert":           ("Remove alert",              False, True,  True,  False),
    "get_strategy":           ("Running strategy",          True,  False, True,  False),
    "start_strategy":         ("Preview or start strategy", False, True,  False, True),
    "end_strategy":           ("Stop strategy",             False, True,  True,  True),
    "get_energy_mode":        ("Energy mode",               True,  False, True,  False),
    "set_energy_mode":        ("Set energy mode",           False, True,  False, True),
    "plan_discharge":         ("Discharge plan",            True,  False, True,  True),
}


def _hints(name: str) -> dict[str, Any]:
    """`title` and `annotations` for mcp.tool(). The title is also set inside the annotations, where hosts on
    an older protocol revision look for it."""
    title, read_only, destructive, idempotent, open_world = _HINTS[name]
    try:
        from mcp.types import ToolAnnotations
    except ImportError:  # an SDK from before annotations
        return {"title": title}
    # the wire names: the SDK's own field names differ between 1.x and 2.x, both accept these
    return {"title": title, "annotations": ToolAnnotations.model_validate({
        "title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
        "idempotentHint": idempotent, "openWorldHint": open_world})}


def _describe(fn) -> str:
    """The tool description as the host receives it: the docstring without its source indentation and line
    wrapping. Blank lines still separate paragraphs; the lines inside one are joined."""
    text = inspect.cleandoc(fn.__doc__ or "")
    return "\n\n".join(" ".join(paragraph.split()) for paragraph in re.split(r"\n\s*\n", text))


def _tool(**kwargs: Any):
    def register(fn):
        _PLAIN_TOOLS.append((fn, {**_hints(fn.__name__), "description": _describe(fn), **kwargs}))
        return fn
    return register


def ui_tool(resource_uri: str, *, invoking: str, invoked: str):
    """Decorator: a tool whose result the host may render as a card. Card
    tools are also callable from other cards (ChatGPT needs the flag).

    The description is the function's docstring, which has to be a plain
    string literal: one built with + is an expression, not a docstring, and
    the tool would reach the host with no description."""
    meta = {"openai/outputTemplate": resource_uri, "openai/toolInvocation/invoking": invoking,
            "openai/toolInvocation/invoked": invoked, "openai/widgetAccessible": True}

    def register(fn):
        if apps is None:
            return _tool(meta=meta)(fn)
        return apps.tool(resource_uri=resource_uri, meta=meta, description=_describe(fn), **_hints(fn.__name__))(fn)
    return register


# Arguments several tools share.
SiteId = Annotated[str, Field(description="Site id from list_sites: vendor:station_id, e.g. sungrow:1445844.")]
KeyName = Annotated[str, Field(description="Label for the new API key, so the person can tell it apart later.")]
Scope = Annotated[Literal["read", "control"], Field(description=(
    "What the new key may do: read = observe only; control = may also change settings. Pass control only when "
    "the person asked for it."))]


_INSTRUCTIONS = (
        "Tools for the user's own solar + battery system through the Aivy platform (Ivy Energy, Australia). "
        "Call list_sites first; every other tool takes one of its site ids (vendor:station_id). "
        "Power is in watts (battery_w > 0 charging, grid_w > 0 exporting), SOC in percent, prices in "
        "Australian cents per kWh. Readings refresh about once a minute: do not poll faster. "
        "Changing the mode or Ai+ settings needs a control-scope key; a forced (manual) mode set here is "
        "time-boxed and the server returns the site to its previous mode when the time is up. "
        "Sites named sandbox:... are shared demo data: readable by every key; energy-mode and Ai+ changes on them are "
        "kept for the user's account only (demo: true, nothing reaches hardware), so they are the place to try writes. "
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
async def get_registration_terms(
    locale: Annotated[str, Field(description="Language of the terms as a locale code, e.g. en or zh-Hans.")] = "en",
) -> dict[str, Any]:
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
async def register_account(
    email: Annotated[str, Field(description="The person's email address; the verification code is sent to it.")],
    password: Annotated[str, Field(description="The password the person chose, 8 to 128 characters.")],
    display_name: Annotated[str | None, Field(description="Name to show on the account; optional.")] = None,
    locale: Annotated[str, Field(description="Language of the verification email as a locale code, e.g. en.")] = "en",
) -> str:
    """Create an Aivy account from the chat, step 1 of 2: a verification code is emailed to `email`. Needs no API
    key. Ask the person to choose the password (8+ characters; it is also their Aivy app password); do not invent
    one. Then ask them for the code and call verify_registration. The reply looks the same whether or not the
    address already has an account: a code that never arrives means check spam or use sign_in instead."""
    body: dict[str, Any] = {"identifier": email, "password": password, "locale": locale}
    if display_name:
        body["display_name"] = display_name
    return await _run(_c().register(body))


@_tool(meta={"openai/widgetAccessible": True})
async def verify_registration(
    registration_id: Annotated[str, Field(description="`registration_id` from register_account's reply.")],
    registration_session: Annotated[str, Field(description="`registration_session` from register_account's reply.")],
    code: Annotated[str, Field(description="The code from the verification email, as the person gave it.")],
    accept_terms: Annotated[bool, Field(description=(
        "True only after the person said they accept the terms get_registration_terms showed; false is refused."))],
    key_name: KeyName = "chat",
    scope: Scope = "read",
) -> str:
    """Create an Aivy account from the chat, step 2 of 2: confirms the emailed code, accepts the terms (only pass
    accept_terms=true after the person said yes), creates the account and an API key, and stores that key on this
    computer (~/.aivy/credentials.json) so every other tool works from now on, in this and future sessions.
    Never repeat the key back; say it is saved and can be managed at https://bridge.ivyenergy.com.au/account."""
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
async def sign_in(
    username: Annotated[str, Field(description="The account's username or email address.")],
    password: Annotated[str, Field(description="The account password, as the person gave it.")],
    key_name: KeyName = "chat",
    scope: Scope = "read",
) -> str:
    """Use an existing Aivy account from the chat: creates an API key with the account's username (or email)
    and password, stores it on this computer (~/.aivy/credentials.json) and uses it from now on. Needs no
    existing key. The password is used once and not stored; never repeat the key back."""
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
    error code means. Read it once at the start of a conversation. When a newer aivy-mcp is released, a short
    update note follows the playbook."""
    from aivy_mcp.update import update_notice
    notice = await update_notice()
    return PLAYBOOK if not notice else PLAYBOOK.rstrip("\n") + "\n\n## Update\n\n" + notice + "\n"


# ── account / sites ──

@ui_tool(UI_URIS_BY_NAME["sites"], invoking="Listing your sites", invoked="Your sites")
async def list_sites() -> dict[str, Any]:
    """Start here: the user's solar + battery sites across vendors, and whose API key this is.

    `sites`: for each, id (vendor:station_id, used by every other tool), vendor, name, online and the vendor's
    headline power in W. `account`: who the key belongs to, its `scope` (read, or control = may change settings)
    and `email_alerts` (whether alert emails can reach the account's verified address).

    Without a key it says so (`has_key` false): register_account (new) or sign_in (existing account) then sets
    one up from the chat."""
    if not _c().has_key:
        return {"error": NO_KEY_MESSAGE, "has_key": False}
    out, account = await asyncio.gather(_data(_c().sites()), _data(_c().me()))
    if "error" in out:
        return out
    out["account"] = account  # an account read that failed is reported inside it
    for site in out.get("sites", []):
        if site.get("id") and site.get("name"):
            _site_names[site["id"]] = site["name"]
    return out


@ui_tool(UI_URIS_BY_NAME["status"], invoking="Reading the battery", invoked="Live status")
async def get_status(site_id: SiteId) -> dict[str, Any]:
    """What one site is doing now, and what it is.

    Top level is the live reading: soc_pct, pv_w, load_w, grid_w (>0 exporting), battery_w (>0 charging), `at`,
    and `source` (vendor = fresh, stored = last kept reading). A null channel is one the vendor does not report,
    not zero. Refreshes about once a minute.

    `site` holds the fixed facts: timezone, battery_capacity_kwh and its source, max_power_w, export_limit_w,
    soc_floor_pct (the floor Ai+ keeps), usable_kwh_now, the current energy_mode and time_slots, today's energy
    and income when reported. Take capacity and limits from here, never from an estimate.

    A newly connected site may have no live reading yet: the facts still come back, with `reading_error`."""
    out, site = await asyncio.gather(_data(_c().status(site_id)), _data(_c().site(site_id)))
    if "error" in out:
        if "error" in site:
            return {"error": out["error"], "id": site_id}
        out = {"id": site_id, "soc_pct": site.get("soc_pct"), "source": "stored", "reading_error": out["error"]}
    out["site"] = {k: v for k, v in site.items() if k != "id"}  # a facts read that failed is reported inside it
    name = _site_names.get(site_id) or site.get("name")
    if name:
        out["name"] = name
    return out


@ui_tool(UI_URIS_BY_NAME["curve"], invoking="Loading history", invoked="Power history")
async def get_curve(
    site_id: SiteId,
    range: Annotated[Literal["1d", "7d", "30d"], Field(description=(
        "1d = 5-minute points for one local day; 7d / 30d = one value per day."))] = "1d",
    day: Annotated[str | None, Field(description=(
        "With range 1d: the local calendar day, YYYY-MM-DD. Omitted = today."))] = None,
) -> dict[str, Any]:
    """Power and SOC history of a site: 5-minute points for one local day, or daily values over the last 7 or 30
    days. Returns times (UTC), local_times, and series per channel (pv, load, battery, grid, soc). Large:
    summarise before quoting. For the reading right now use get_status."""
    return await _data(_c().curve(site_id, range, day), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["earnings"], invoking="Adding up the day", invoked="Earnings")
async def get_earnings(
    site_id: SiteId,
    date: Annotated[str | None, Field(description="YYYY-MM-DD, 'today' or 'yesterday'. Omitted = today.")] = None,
    window_start: Annotated[str | None, Field(description=(
        "HH:MM site local. With window_end: account for that part of the day only."))] = None,
    window_end: Annotated[str | None, Field(description="HH:MM site local: where that part of the day ends.")] = None,
) -> dict[str, Any]:
    """Energy bought, sold and self-consumed on one day, or in part of it, priced at the actual Amber half-hourly
    prices when Amber is linked. An estimate from vendor power samples, not the bill; read `coverage` and say so
    when it is partial."""
    return await _data(_c().earnings(site_id, date, window_start, window_end), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["operations"], invoking="Reading the log", invoked="Operations")
async def get_operations(
    site_id: SiteId,
    limit: Annotated[int, Field(description="How many entries to return, newest first: 1 to 100.")] = 20,
) -> dict[str, Any]:
    """What the platform did to the site and why, newest first: each automatic (Ai+) and manual command with its
    reason and the prices at the time. Use it to explain 'why did it charge at 3am'."""
    return await _data(_c().operations(site_id, limit), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["prices"], invoking="Fetching Amber prices", invoked="Prices")
async def get_prices(
    site_id: SiteId,
    hours: Annotated[int, Field(description=(
        "How far ahead the forecast runs, in hours: 1 to 24, at most 4 when resolution is 5."))] = 8,
    resolution: Annotated[int, Field(description="Interval length in minutes: 30 or 5.")] = 30,
) -> dict[str, Any]:
    """Amber Electric prices for the site: the current interval and the forecast as import price and feed-in earn
    (c/kWh incl. GST), with a summary of cheapest/dearest periods. Fails with 409 when no Amber account is linked
    to the site."""
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
async def connect_site(
    vendor: Annotated[str, Field(description="Vendor id from list_vendors, e.g. sungrow.")],
    sn: Annotated[str | None, Field(description="Device / inverter serial number, for serial-number vendors.")] = None,
    nmi: Annotated[str | None, Field(description=(
        "National Metering Identifier, from the electricity bill; Sungrow and FoxESS need it with sn."))] = None,
    system_id: Annotated[str | None, Field(description="Sigenergy system id.")] = None,
    region: Annotated[Literal["AU", "EU", "SG"], Field(description="AlphaESS portal region.")] = "AU",
) -> str:
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
async def disconnect_site(site_id: SiteId) -> str:
    """CHANGES THE ACCOUNT (control scope). Confirm with the user before calling.

    Remove a site from the user's account. The vendor-side link and history are left alone; the account just
    stops seeing it. Demo sites cannot be removed."""
    return await _run(_c().disconnect_site(site_id))


# ── alerts ──

_METRIC_UNIT = {"soc": "%", "import_price": "c", "export_price": "c", "temperature": "°C"}
_METRIC_WORD = {"soc": "SOC", "import_price": "Import price", "export_price": "Feed-in price",
                "temperature": "Forecast temperature"}


_ALERT_EVENTS = 20  # firings list_alerts returns with the rules


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


AlertMetric = Literal["soc", "import_price", "export_price", "temperature"]
AlertOperator = Literal["above", "below"]


@ui_tool(UI_URIS_BY_NAME["alerts"], invoking="Loading alerts", invoked="Alerts")
async def list_alerts(
    site_id: SiteId,
    name: Annotated[str | None, Field(description=(
        "Draft only: a title for the proposed alert. Omitted = one is made from the rule."))] = None,
    metric: Annotated[AlertMetric | None, Field(description=(
        "Draft only: what the proposed alert watches. soc (battery %), import_price / export_price (c/kWh), "
        "temperature (forecast °C). Leave every draft argument out to only read."))] = None,
    operator: Annotated[AlertOperator | None, Field(description=(
        "Draft only: fire when the value is above or below the threshold."))] = None,
    threshold: Annotated[float | None, Field(description=(
        "Draft only: the value to compare with, in the metric's unit."))] = None,
    email: Annotated[bool | None, Field(description=(
        "Draft only: tick the form's email box (also mail the account's address on each firing)."))] = None,
    cooldown_minutes: Annotated[int | None, Field(description=(
        "Draft only: quiet time after a firing, in minutes (15 or more)."))] = None,
) -> dict[str, Any]:
    """Read the alerts on a site: the rules and what fired. Never creates or changes a rule.

    `rules`: what each watches (soc %, import/export price c/kWh, forecast temperature °C), the threshold,
    whether it emails, when it last fired. `events`: the 20 most recent firings, newest first (rule, value, time,
    delivery). Answers "did anything happen overnight?".

    To propose a NEW alert, pass it in the draft arguments (metric, operator and threshold at least). Nothing is
    saved: hosts with cards show the form filled in and the person's click on "Add alert" is their yes, so wait
    for them. Without a card, restate the rule and call create_alert once they agree."""
    draft: dict[str, Any] | None = None
    if metric or operator or threshold is not None:
        draft = {k: v for k, v in {"name": name, "metric": metric, "operator": operator, "threshold": threshold,
                                    "email": email, "cooldown_minutes": cooldown_minutes}.items() if v is not None}
        if not name and metric and operator and threshold is not None:
            draft["name"] = _alert_name(metric, operator, threshold)
    out, fired = await asyncio.gather(_data(_c().alerts(site_id), site_id=site_id),
                                      _data(_c().alert_events(site_id, _ALERT_EVENTS)))
    if "error" not in out:  # the rules are the answer; a history that could not be read is said, not hidden
        out["events"] = fired.get("events", [])
        if "error" in fired:
            out["events_error"] = fired["error"]
    if draft:
        out["draft"] = draft
    return out


@ui_tool(UI_URIS_BY_NAME["alerts"], invoking="Adding the alert", invoked="Alert added")
async def create_alert(
    site_id: SiteId,
    name: Annotated[str, Field(description=(
        "Title shown on the alert, up to 100 characters. An empty string = one is made from the rule "
        "(\"SOC below 20%\")."))],
    metric: Annotated[AlertMetric, Field(description=(
        "What to watch: soc (battery %), import_price / export_price (c/kWh, the current Amber interval), "
        "temperature (forecast °C)."))],
    operator: Annotated[AlertOperator, Field(description="Fire when the value is above or below the threshold.")],
    threshold: Annotated[float, Field(description="The value to compare with, in the metric's unit (%, c/kWh, °C).")],
    email: Annotated[bool, Field(description="Also mail the account's verified address on each firing.")] = False,
    cooldown_minutes: Annotated[int, Field(description=(
        "Quiet time after a firing, in minutes: 15 to 10080."))] = 360,
    message: Annotated[str, Field(description=(
        "The person's own words, put in front of the alert text; up to 400 characters."))] = "",
    active_start: Annotated[str, Field(description=(
        "HH:MM site local: start of the daily window in which the alert may fire. Same as active_end = "
        "all day."))] = "00:00",
    active_end: Annotated[str, Field(description="HH:MM site local: end of that daily window.")] = "00:00",
    locale: Annotated[Literal["en", "ja", "th", "zh-Hans", "zh-Hant"], Field(description=(
        "Language of the alert text and email."))] = "en",
) -> dict[str, Any]:
    """Set up a threshold alert on a site: it fires when the watched metric crosses the threshold. Every firing
    goes to the app inbox (and the phone when the app is installed); email=True also mails the account's verified
    address (list_sites account.email_alerts says whether that works). An alert only tells the person something;
    it never changes what the battery does. Confirm the rule with the user before creating it (or let them add it
    from the list_alerts card). Returns the site's rules with the new one under `created`."""
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
async def delete_alert(
    site_id: SiteId,
    rule_id: Annotated[int, Field(description="`id` of the rule to retire, from list_alerts `rules`.")],
) -> dict[str, Any]:
    """Retire an alert rule (past firings stay in the inbox). Confirm with the user first. Returns the site's
    remaining rules with the removed id under `deleted`."""
    try:
        await _c().delete_alert(site_id, rule_id)
    except AivyError as e:
        return {"error": str(e), "site_id": site_id}
    return await _alerts_after(site_id, deleted=rule_id)


# ── strategy mode: the AI writes the rules, the server runs them ──

# The rule grammar is the description of the `rules` argument, so it reaches the host in the input schema.
_RULE_SHAPE = ("The rules, checked in order; first match wins. At most 20 rules, 4 conditions each (all must hold). "
               "Each rule is {\"when\": [conditions], \"then\": action, \"note\": \"why, in the person's words\"}. "
               "A condition is {\"metric\": soc | import_price | export_price | hour, \"operator\": above | below | "
               "between, \"value\": n}; between takes \"low\" and \"high\" instead of \"value\". soc is %, prices are "
               "c/kWh for the current interval, hour is site local time as a decimal (17.5 = 5:30 pm; low > high "
               "wraps midnight). An action is {\"action\": charge | discharge | hold | auto, \"power_w\"?, "
               "\"target_soc\"? (charge), \"stop_soc\"? (discharge)}.")


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
async def get_strategy(site_id: SiteId) -> dict[str, Any]:
    """Strategy mode on a site: the AI-written rules running now (name, rules, lease, last action, recent
    evaluations), status none when there has never been one, or ended with end_reason. `limits` is what the server
    enforces on every strategy."""
    return await _data(_c().strategy(site_id), site_id=site_id)


# A strategy starts only from a preview the person has seen. The preview
# returns a proposal_id: an HMAC over the site and everything they were
# shown, with the time it was issued. Starting recomputes it, so different
# rules, another site or a stale preview cannot be started. The key lives in
# this process: after a restart the preview is simply done again.
_PROPOSAL_TTL_S = 15 * 60
_PROPOSAL_KEY = secrets.token_bytes(32)


def _canonical(value: Any) -> Any:
    """The same proposal after a trip through a card's JSON: key order does
    not matter and 40.0 comes back as 40."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _proposal_id(site_id: str, proposal: dict[str, Any], issued: int | None = None) -> str:
    issued = int(time.time()) if issued is None else issued
    text = json.dumps([issued, site_id, _canonical(proposal)], separators=(",", ":"), ensure_ascii=False)
    return f"{issued}.{hmac.new(_PROPOSAL_KEY, text.encode(), 'sha256').hexdigest()[:32]}"


def _proposal_problem(site_id: str, proposal: dict[str, Any], proposal_id: str | None) -> str | None:
    """Why this proposal_id does not allow starting these rules, or None."""
    if not proposal_id:
        return ("preview first: call with accept_risk=false, let the person see the rules, then pass the "
                "proposal_id that call returns")
    issued = proposal_id.partition(".")[0]
    if not issued.isdigit() or not hmac.compare_digest(_proposal_id(site_id, proposal, int(issued)), proposal_id):
        return ("this proposal_id does not belong to these rules: name, rules, fallback, summary and "
                "duration_minutes must be exactly what was previewed, and a preview does not survive a restart of "
                "this server. Nothing was changed. Preview again and show the person the result")
    if time.time() - int(issued) > _PROPOSAL_TTL_S:
        return "the preview is more than 15 minutes old: preview again so the person sees it against current readings"
    return None


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Working on the strategy", invoked="Strategy")
async def start_strategy(
    site_id: SiteId,
    name: Annotated[str, Field(description="Short name for the strategy, up to 100 characters.")],
    rules: Annotated[list[dict], Field(description=_RULE_SHAPE)],
    accept_risk: Annotated[bool, Field(description=(
        "false = preview only, nothing starts. true = start what was previewed, only after the person said yes."))] = False,
    proposal_id: Annotated[str | None, Field(description=(
        "`proposal_id` from the preview of these same arguments; needed with accept_risk=true."))] = None,
    summary: Annotated[str | None, Field(description=(
        "What the strategy does, in a paragraph for the person to read; up to 600 characters."))] = None,
    fallback: Annotated[dict | None, Field(description=(
        "The action when no rule matches, shaped like a rule's `then`. Omitted = {\"action\": \"auto\"}."))] = None,
    duration_minutes: Annotated[int | None, Field(description=(
        "The lease: how long the strategy runs, 15 to 10080 minutes. A new strategy runs for a day when omitted; "
        "a running one keeps its lease unless this is given."))] = None,
) -> dict[str, Any]:
    """Preview or start a custom strategy: YOU write the rules from what the person wants. Two calls, the second
    needs the first.

    1) accept_risk=false (default): starts nothing; dry-runs the rules on the current readings and returns a
    proposal_id. A card host shows a Start button that makes call 2 itself, so wait; otherwise read the rules
    back and ask.

    2) accept_risk=true with that proposal_id and every other argument unchanged, only after the person said yes
    (control scope; demo sites dry-run). Refused without a matching proposal_id or after 15 minutes: preview again.

    A strategy already running has its rules replaced by the same two calls."""
    proposal = _strategy_body(name, summary, rules, fallback, duration_minutes)
    if not accept_risk:
        out, running = await asyncio.gather(
            _data(_c().preview_strategy(site_id, _strategy_body(None, None, rules, fallback, None)), site_id=site_id),
            _data(_c().strategy(site_id)))
        if "error" in out:
            return out
        out.update(status="proposed", proposal=proposal, proposal_id=_proposal_id(site_id, proposal))
        if running.get("status") == "active":
            out["replaces"] = {"name": running.get("name"), "remaining_minutes": running.get("remaining_minutes")}
        return out
    problem = _proposal_problem(site_id, proposal, proposal_id)
    if problem:
        return {"error": problem, "site_id": site_id}
    running = await _data(_c().strategy(site_id))
    if running.get("status") == "active":  # the rules of the running strategy are replaced; its name stays
        return await _data(_c().update_strategy(site_id, _strategy_body(None, summary, rules, fallback, duration_minutes)),
                           site_id=site_id)
    return await _data(_c().start_strategy(site_id, {**proposal, "accept_risk": True}), site_id=site_id)


@ui_tool(UI_URIS_BY_NAME["strategy"], invoking="Stopping the strategy", invoked="Strategy stopped")
async def end_strategy(site_id: SiteId) -> dict[str, Any]:
    """Stop the running strategy now; the site goes back to the mode it had before it started. Confirm with the
    person first unless they asked for it."""
    return await _data(_c().end_strategy(site_id), site_id=site_id)


# ── control ──

@ui_tool(UI_URIS_BY_NAME["control"], invoking="Reading the energy mode", invoked="Energy mode")
async def get_energy_mode(site_id: SiteId) -> dict[str, Any]:
    """How the site is run now and how Ai+ is configured.

    Returns `energy_mode` (selfConsumption | amber_smart = Ai+ | au_* = the AU-01..04 student strategies |
    forced), `time_slots` (the forced windows), `soc_floor_pct`, the `variant` and `settings` in force, and
    `reverts_at` / `reverts_to` when a forced mode will hand back by itself.

    `ai_plus` is the Ai+ configuration whether or not Ai+ is the mode: `active`, `variant`, `variant_options`,
    `settings`, and `tunable` (every lever with type, min, max, unit and default). Take ranges from it before
    changing Ai+ with set_energy_mode.

    Hosts that support cards render a control card; a click on its buttons is the person's yes."""
    out, ai_plus = await asyncio.gather(_data(_c().energy_mode(site_id), site_id=site_id),
                                        _data(_c().ai_plus(site_id)))
    if "error" not in out:  # the mode is the answer; an Ai+ read that failed is reported inside it
        out["ai_plus"] = {k: v for k, v in ai_plus.items() if k not in ("id", "soc_floor_pct")}
    return out


@_tool(meta={"openai/widgetAccessible": True})
async def set_energy_mode(
    site_id: SiteId,
    mode: Annotated[Literal["selfConsumption", "amber_smart", "au_forecast_export", "au_negative_price",
                            "au_smart_energy", "au_weather_season", "forced"] | None, Field(description=(
        "How to run the site: selfConsumption (inverter default); amber_smart (Ai+, price-driven trading); "
        "au_forecast_export, au_negative_price, au_smart_energy, au_weather_season (the AU-01..04 student "
        "strategies, described in get_agent_guide); forced (manual charge / discharge windows). Omitted = keep "
        "the mode and change Ai+ only."))] = None,
    duration_minutes: Annotated[int | None, Field(description=(
        "With mode: hand the site back to its previous mode after this many minutes, at most 1440. forced is "
        "always time-boxed (240 when omitted); other modes stay until changed unless this is given."))] = None,
    action: Annotated[Literal["charge", "discharge"] | None, Field(description=(
        "forced: one window starting now and lasting duration_minutes. Use this or time_slots."))] = None,
    power_w: Annotated[int | None, Field(description="With action: the charge / discharge power in W.")] = None,
    target_soc: Annotated[int | None, Field(description=(
        "With action=charge: stop charging at this SOC, 0 to 100."))] = None,
    stop_soc: Annotated[int | None, Field(description=(
        "With action=discharge: stop discharging at this SOC, 0 to 100."))] = None,
    time_slots: Annotated[list[dict] | None, Field(description=(
        "forced: the windows, in site local time. Each is {\"type\": charge | discharge, \"start\": \"HH:MM\", "
        "\"end\": \"HH:MM\" (earlier than start = crosses midnight), \"target_soc\" (charge) or \"stop_soc\" "
        "(discharge), \"power\": W, \"pace\": true to spread a discharge so stop_soc is reached exactly at end}. "
        "plan_discharge returns them ready to pass."))] = None,
    variant: Annotated[str | None, Field(description=(
        "Ai+ engine variant, one of get_energy_mode `ai_plus.variant_options`: with mode=amber_smart the engine "
        "to switch to, without mode a change of engine in place."))] = None,
    settings: Annotated[dict | None, Field(description=(
        "Without mode: the Ai+ levers to change, names and ranges from get_energy_mode `ai_plus.tunable`. With an "
        "au_* mode: that strategy's own numbers, e.g. {\"reserve_soc\": 60}. Refused with mode=amber_smart."))] = None,
) -> str:
    """CHANGES THE SITE (control scope). Confirm with the user before calling.

    Switch how the site is run (pass `mode`), or tune Ai+ without switching (omit `mode`, pass `settings` and/or
    `variant`). A rejected value changes nothing; `rejected` says why.

    mode=forced is manual and time-boxed: the site returns to its previous mode when duration_minutes is up. Give
    `action` for one window starting now, or `time_slots`. Refused (409) while a strategy is running, or when an
    au_* mode cannot run on the site. Takes effect within about a minute; the SOC floor and export limits hold.
    On a demo site (sandbox:...) nothing reaches hardware."""
    forced_args = {"duration_minutes": duration_minutes, "action": action, "power_w": power_w,
                   "target_soc": target_soc, "stop_soc": stop_soc, "time_slots": time_slots}
    if mode is None:
        given = [name for name, value in forced_args.items() if value is not None]
        if given:
            return _dump({"error": f"{', '.join(given)} need a mode (forced); without mode only the Ai+ "
                                   "settings and variant can change"})
        if not settings and variant is None:
            return _dump({"error": "nothing to change: pass a mode, or Ai+ settings / variant without one"})
        return await _run(_c().patch_ai_plus(site_id, variant, settings or {}))
    if mode == "amber_smart" and settings:
        return _dump({"error": "Ai+ levers are set in a call without mode; switching to amber_smart is its own "
                               "call. Nothing was changed."})
    body: dict[str, Any] = {"mode": mode}
    for key, value in ({**forced_args, "variant": variant, "settings": settings}).items():
        if value is not None:
            body[key] = value
    return await _run(_c().set_energy_mode(site_id, body))


# ── planning (nothing is applied) ──

@ui_tool(UI_URIS_BY_NAME["plan"], invoking="Planning the discharge", invoked="Discharge plan")
async def plan_discharge(
    site_id: SiteId,
    goal: Annotated[Literal["best_price", "target_soc"], Field(description=(
        "best_price = sell in the best-paid Amber intervals; target_soc = one steady discharge that ends at a "
        "chosen SOC."))],
    window_start: Annotated[str | None, Field(description=(
        "HH:MM site local. best_price: where the selling window starts. target_soc: when to start discharging "
        "(omitted = now)."))] = None,
    window_end: Annotated[str | None, Field(description=(
        "HH:MM site local. best_price: where the selling window ends. target_soc: the time to be at target_soc "
        "(required)."))] = None,
    reserve_soc: Annotated[int | None, Field(description=(
        "best_price only: the SOC to keep, 0 to 100; only energy above it is sold."))] = None,
    min_sell_c: Annotated[float | None, Field(description=(
        "best_price only: sell only in intervals paying more than this, c/kWh."))] = None,
    target_soc: Annotated[int | None, Field(description=(
        "target_soc only, and required there: the SOC to be at by window_end, 0 to 100."))] = None,
    battery_capacity_kwh: Annotated[float | None, Field(description=(
        "Battery capacity to calculate with, kWh. Normally omitted: the site's own figure is used."))] = None,
) -> dict[str, Any]:
    """Work out when and how hard to discharge. Calculates only: nothing is applied, a read key is enough.

    goal='best_price' ("sell at the best prices tonight"): the best-paid Amber intervals between window_start
    and window_end for the energy above reserve_soc, optionally only above min_sell_c. Returns the intervals,
    expected revenue and the average it beats.

    goal='target_soc' ("keep 30% for 9 pm"): one steady discharge from window_start (default now) that reaches
    target_soc at window_end; needs both of those; prices play no part. Returns power_w.

    Both return proposed time_slots: apply with set_energy_mode(mode='forced', time_slots=...) after the user
    agrees. On hosts with cards the plan's Apply button is the user's yes."""
    if goal == "best_price":
        if target_soc is not None:
            return {"error": "target_soc belongs to goal='target_soc'; with best_price the floor is reserve_soc",
                    "site_id": site_id}
        body = {k: v for k, v in {"window_start": window_start, "window_end": window_end, "reserve_soc": reserve_soc,
                                  "min_sell_c": min_sell_c, "battery_capacity_kwh": battery_capacity_kwh}.items()
                if v is not None}
        return await _data(_c().plan_best_price_discharge(site_id, body), site_id=site_id)
    if goal == "target_soc":
        if target_soc is None or not window_end:
            return {"error": "goal='target_soc' needs target_soc and window_end (the time to be there)",
                    "site_id": site_id}
        if reserve_soc is not None or min_sell_c is not None:
            return {"error": "reserve_soc and min_sell_c belong to goal='best_price'; here the floor is target_soc",
                    "site_id": site_id}
        body = {"end": window_end, "target_soc": target_soc}
        if window_start:
            body["start"] = window_start
        if battery_capacity_kwh:
            body["battery_capacity_kwh"] = battery_capacity_kwh
        return await _data(_c().plan_discharge_to_target(site_id, body), site_id=site_id)
    return {"error": "goal is best_price or target_soc", "site_id": site_id}


# ── server ──

if apps is not None:
    for _uri, _name in UI_URIS.items():
        apps.add_html_resource(_uri, render_template(_name), name=f"aivy-{_name}-card",
                               title=f"Aivy {_name} card", prefers_border=True)

def _server_kwargs() -> dict[str, Any]:
    """Constructor options the installed SDK understands (2.x reports version
    and website in serverInfo; 1.x has neither)."""
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
_accepted = inspect.signature(mcp.tool).parameters  # early 1.x takes neither meta, annotations nor title
for _fn, _kwargs in _PLAIN_TOOLS:
    mcp.tool(**{k: v for k, v in _kwargs.items() if k in _accepted})(_fn)


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
