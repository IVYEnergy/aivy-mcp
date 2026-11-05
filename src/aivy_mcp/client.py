"""Thin HTTP client for the Aivy Developer API.

Everything the MCP tools do is one HTTP call here. Kept separate so the same
client can back a CLI or a notebook without the MCP machinery.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_API_URL = "https://bridge.ivyenergy.com.au/v1"


class AivyError(RuntimeError):
    """The API refused or could not complete a request. The message carries
    the server's `detail`, which is written to be read by a person or model."""


class AivyClient:
    def __init__(self, api_key: str | None = None, api_url: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("AIVY_API_KEY", "")
        self.api_url = (api_url or os.environ.get("AIVY_API_URL") or DEFAULT_API_URL).rstrip("/")
        if not self.api_key:
            raise AivyError("AIVY_API_KEY is not set. Create a key in the Aivy app (menu, API keys) and "
                            "export AIVY_API_KEY=aivy_sk_...")
        self._http = httpx.AsyncClient(
            base_url=self.api_url, timeout=timeout,
            headers={"Authorization": f"Bearer {self.api_key}", "User-Agent": f"aivy-mcp/{_version()}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise AivyError(f"could not reach the Aivy API at {self.api_url}: {e}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise AivyError(f"{response.status_code}: {detail}")
        return response.json()

    # ── account / sites ──
    async def me(self) -> dict:
        return await self._call("GET", "/me")

    async def sites(self) -> dict:
        return await self._call("GET", "/sites")

    async def site(self, site_id: str) -> dict:
        return await self._call("GET", f"/sites/{site_id}")

    async def status(self, site_id: str) -> dict:
        return await self._call("GET", f"/sites/{site_id}/status")

    async def curve(self, site_id: str, range_: str = "1d", day: str | None = None) -> dict:
        params = {"range": range_}
        if day:
            params["day"] = day
        return await self._call("GET", f"/sites/{site_id}/curve", params=params)

    async def earnings(self, site_id: str, date: str | None = None, window_start: str | None = None,
                       window_end: str | None = None) -> dict:
        params = {k: v for k, v in {"date": date, "window_start": window_start, "window_end": window_end}.items() if v}
        return await self._call("GET", f"/sites/{site_id}/earnings", params=params)

    async def operations(self, site_id: str, limit: int = 20) -> dict:
        return await self._call("GET", f"/sites/{site_id}/operations", params={"limit": limit})

    async def prices(self, site_id: str, hours: int = 8, resolution: int = 30) -> dict:
        return await self._call("GET", f"/sites/{site_id}/prices", params={"hours": hours, "resolution": resolution})

    async def vendors(self) -> dict:
        return await self._call("GET", "/vendors")

    async def connect_site(self, body: dict) -> dict:
        return await self._call("POST", "/sites", json=body)

    async def disconnect_site(self, site_id: str) -> dict:
        return await self._call("DELETE", f"/sites/{site_id}")

    # ── control ──
    async def energy_mode(self, site_id: str) -> dict:
        return await self._call("GET", f"/sites/{site_id}/energy-mode")

    async def set_energy_mode(self, site_id: str, body: dict) -> dict:
        return await self._call("PUT", f"/sites/{site_id}/energy-mode", json=body)

    async def ai_plus(self, site_id: str) -> dict:
        return await self._call("GET", f"/sites/{site_id}/ai-plus")

    async def patch_ai_plus(self, site_id: str, variant: str | None, settings: dict) -> dict:
        return await self._call("PATCH", f"/sites/{site_id}/ai-plus", json={"variant": variant, "settings": settings})

    # ── planning ──
    async def plan_best_price_discharge(self, site_id: str, body: dict) -> dict:
        return await self._call("POST", f"/sites/{site_id}/plans/best-price-discharge", json=body)

    async def plan_discharge_to_target(self, site_id: str, body: dict) -> dict:
        return await self._call("POST", f"/sites/{site_id}/plans/discharge-to-target", json=body)


def _version() -> str:
    from aivy_mcp import __version__
    return __version__
