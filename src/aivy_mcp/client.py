"""Thin HTTP client for the Aivy Developer API.

Everything the MCP tools do is one HTTP call here. Kept separate so the same
client can back a CLI or a notebook without the MCP machinery.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "https://bridge.ivyenergy.com.au/v1"


class AivyError(RuntimeError):
    """The API refused or could not complete a request. The message carries
    the server's `detail`, which is written to be read by a person or model."""


NO_KEY_MESSAGE = ("No Aivy API key yet. Register in this chat with register_account (then verify_registration), "
                  "sign in with sign_in if you already have an Aivy account, or set AIVY_API_KEY. "
                  "Keys can also be created at https://bridge.ivyenergy.com.au/developer")


def credentials_path() -> Path:
    """Where a key obtained in the chat is kept: ~/.aivy/credentials.json
    (AIVY_HOME overrides), readable by the user only."""
    return Path(os.environ.get("AIVY_HOME") or Path.home() / ".aivy") / "credentials.json"


def load_saved_key(api_url: str) -> str | None:
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(api_url) if isinstance(data, dict) else None
    key = entry.get("api_key") if isinstance(entry, dict) else None
    return key if isinstance(key, str) and key.startswith("aivy_sk_") else None


def save_key(api_url: str, api_key: str, *, username: str | None = None, key_name: str | None = None) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[api_url] = {"api_key": api_key, "username": username, "key_name": key_name,
                     "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


class AivyClient:
    """HTTP client for the Developer API.

    The key is optional at construction: the public endpoints (terms,
    register, verify) and HTTP Basic key creation work without one, and
    `use_key` switches the client to a key obtained that way. Keys come,
    in order, from the argument, AIVY_API_KEY, or the local credential file.
    """

    def __init__(self, api_key: str | None = None, api_url: str | None = None, timeout: float = 30.0):
        self.api_url = (api_url or os.environ.get("AIVY_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("AIVY_API_KEY") or load_saved_key(self.api_url) or ""
        self._http = httpx.AsyncClient(
            base_url=self.api_url, timeout=timeout,
            headers={"User-Agent": f"aivy-mcp/{_version()}"},
        )

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def use_key(self, api_key: str) -> None:
        self.api_key = api_key

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, *, auth: str = "key", basic: tuple[str, str] | None = None,
                    **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth == "key":
            if not self.api_key:
                raise AivyError(NO_KEY_MESSAGE)
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif auth == "basic" and basic:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise AivyError(f"could not reach the Aivy API at {self.api_url}: {e}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise AivyError(f"{response.status_code}: {detail}")
        return response.json()

    # ── account creation (no key needed) ──
    async def terms(self, locale: str = "en") -> dict:
        return await self._call("GET", "/auth/terms", auth="none", params={"locale": locale})

    async def register(self, body: dict) -> dict:
        return await self._call("POST", "/auth/register", auth="none", json=body)

    async def verify(self, body: dict) -> dict:
        return await self._call("POST", "/auth/verify", auth="none", json=body)

    async def create_key_with_password(self, username: str, password: str, body: dict) -> dict:
        return await self._call("POST", "/keys", auth="basic", basic=(username, password), json=body)

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

    # ── alerts ──
    async def alerts(self, site_id: str) -> dict:
        return await self._call("GET", f"/sites/{site_id}/alerts")

    async def create_alert(self, site_id: str, body: dict) -> dict:
        return await self._call("POST", f"/sites/{site_id}/alerts", json=body)

    async def delete_alert(self, site_id: str, rule_id: int) -> dict:
        return await self._call("DELETE", f"/sites/{site_id}/alerts/{rule_id}")

    async def alert_events(self, site_id: str, limit: int = 50) -> dict:
        return await self._call("GET", f"/sites/{site_id}/alerts/events", params={"limit": limit})

    async def test_alert_email(self) -> dict:
        return await self._call("POST", "/alerts/test-email")

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
