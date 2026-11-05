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
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from aivy_mcp.client import AivyClient, AivyError

mcp = _Server(
    "aivy",
    instructions=(
        "Tools for the user's own solar + battery system through the Aivy platform (Ivy Energy, Australia). "
        "Call list_sites first; every other tool takes one of its site ids (vendor:station_id). "
        "Power is in watts (battery_w > 0 charging, grid_w > 0 exporting), SOC in percent, prices in "
        "Australian cents per kWh. Readings refresh about once a minute: do not poll faster. "
        "Changing the mode or Ai+ settings needs a control-scope key; a forced (manual) mode set here is "
        "time-boxed and the server returns the site to its previous mode when the time is up. "
        "Sites named sandbox:... are shared demo data: readable, never controllable. "
        "No site yet? list_vendors then connect_site links the user's own system."
    ),
)

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


# ── account / sites ──

@mcp.tool()
async def whoami() -> str:
    """Check the API key: who it belongs to and whether it may control (scope read or control)."""
    return await _run(_c().me())


@mcp.tool()
async def list_sites() -> str:
    """List the user's solar + battery sites across vendors. Returns id (vendor:station_id, used by every other
    tool), vendor, name, online and the vendor's headline power in W. Start here."""
    return await _run(_c().sites())


@mcp.tool()
async def get_site(site_id: str) -> str:
    """Facts about one site: timezone, battery capacity (kWh) and where that figure came from, max charge/discharge
    power (W), export limit (W), the SOC floor Ai+ keeps, live SOC and usable kWh, current energy mode and
    forced windows, today's energy and income when the vendor reports them."""
    return await _run(_c().site(site_id))


@mcp.tool()
async def get_status(site_id: str) -> str:
    """Live reading: SOC (%), pv_w, load_w, grid_w (>0 exporting), battery_w (>0 charging). A null channel is one
    the vendor does not report, not zero. `source` is vendor (fresh) or stored (last kept reading)."""
    return await _run(_c().status(site_id))


@mcp.tool()
async def get_curve(site_id: str, range: str = "1d", day: str | None = None) -> str:
    """Power and SOC history. range 1d = 5-minute points for one local day (day = YYYY-MM-DD, default today);
    7d / 30d = daily values. Returns times (UTC), local_times, and series per channel (pv, load, battery, grid,
    soc). Large: summarise before quoting."""
    return await _run(_c().curve(site_id, range, day))


@mcp.tool()
async def get_earnings(site_id: str, date: str | None = None, window_start: str | None = None,
                       window_end: str | None = None) -> str:
    """Energy bought, sold and self-consumed for a day (YYYY-MM-DD, 'today' or 'yesterday'), priced at the actual
    Amber half-hourly prices when Amber is linked. Optional HH:MM window for part of the day. An estimate from
    vendor power samples, not the bill; read `coverage` and say so when it is partial."""
    return await _run(_c().earnings(site_id, date, window_start, window_end))


@mcp.tool()
async def get_operations(site_id: str, limit: int = 20) -> str:
    """What the platform did to the site and why, newest first: each automatic (Ai+) and manual command with its
    reason and the prices at the time. Use it to explain 'why did it charge at 3am'."""
    return await _run(_c().operations(site_id, limit))


@mcp.tool()
async def get_prices(site_id: str, hours: int = 8, resolution: int = 30) -> str:
    """Amber Electric prices for the site: the current interval and the forecast as import price and feed-in earn
    (c/kWh incl. GST), with a summary of cheapest/dearest periods. resolution 30 (up to 24 h) or 5 (up to 4 h).
    Fails with 409 when no Amber account is linked to the site."""
    return await _run(_c().prices(site_id, hours, resolution))


# ── connecting systems ──

@mcp.tool()
async def list_vendors() -> str:
    """Every inverter / battery brand Aivy can connect, whether it is available on this deployment, what
    connecting one needs (`connect`: serial + NMI, a system id, or an OAuth sign-in at the vendor) and how much
    of it can be controlled. Use before connect_site."""
    return await _run(_c().vendors())


@mcp.tool()
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


@mcp.tool()
async def disconnect_site(site_id: str) -> str:
    """CHANGES THE ACCOUNT (control scope). Confirm with the user before calling.

    Remove a site from the user's account. The vendor-side link and history are left alone; the account just
    stops seeing it. Demo sites cannot be removed."""
    return await _run(_c().disconnect_site(site_id))


# ── control ──

@mcp.tool()
async def get_energy_mode(site_id: str) -> str:
    """Current energy mode (selfConsumption | amber_smart = Ai+ | forced), its forced windows, the SOC floor, the
    Ai+ variant and settings when on Ai+, and any pending timed hand-back (reverts_at / reverts_to)."""
    return await _run(_c().energy_mode(site_id))


@mcp.tool()
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


@mcp.tool()
async def get_ai_plus(site_id: str) -> str:
    """Ai+ (amber_smart) configuration: active or not, engine variant and options, effective settings, and
    `tunable`: every lever with type, min, max, unit and default. Read before update_ai_plus."""
    return await _run(_c().ai_plus(site_id))


@mcp.tool()
async def update_ai_plus(site_id: str, settings: dict[str, Any] | None = None, variant: str | None = None) -> str:
    """CHANGES THE SITE (control scope). Confirm with the user before calling.

    Set Ai+ levers (names and ranges from get_ai_plus `tunable`) and/or switch engine variant. Validated against
    the variant that will run; if any value is rejected nothing changes and `rejected` says why. Does not switch
    the mode: use set_energy_mode(mode='amber_smart') for that."""
    return await _run(_c().patch_ai_plus(site_id, variant, settings or {}))


# ── planning (nothing is applied) ──

@mcp.tool()
async def plan_best_price_discharge(site_id: str, window_start: str | None = None, window_end: str | None = None,
                                    reserve_soc: int | None = None, min_sell_c: float | None = None,
                                    battery_capacity_kwh: float | None = None) -> str:
    """Propose when to sell: picks the best-paid Amber intervals in the window (HH:MM) for the battery's usable
    energy above reserve_soc, optionally only above min_sell_c c/kWh. Returns intervals, expected revenue, the
    average it beats and proposed time_slots. Nothing is applied; apply with set_energy_mode(mode='forced',
    time_slots=...) after the user agrees."""
    body = {k: v for k, v in {"window_start": window_start, "window_end": window_end, "reserve_soc": reserve_soc,
                              "min_sell_c": min_sell_c, "battery_capacity_kwh": battery_capacity_kwh}.items()
            if v is not None}
    return await _run(_c().plan_best_price_discharge(site_id, body))


@mcp.tool()
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
