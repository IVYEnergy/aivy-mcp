"""Card templates for hosts that render MCP Apps (and ChatGPT's Apps SDK).

Each template is one self-contained HTML file: the shared styles and the
host bridge are inlined at load time, so a card needs no network and no
assets, which is what the extension's default CSP allows.
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent

UI_URIS_BY_NAME = {
    "status": "ui://aivy/status.html",
    "plan": "ui://aivy/plan.html",
    "prices": "ui://aivy/prices.html",
    "signup": "ui://aivy/signup.html",
    "control": "ui://aivy/control.html",
    "connect": "ui://aivy/connect.html",
    "curve": "ui://aivy/curve.html",
    "earnings": "ui://aivy/earnings.html",
    "operations": "ui://aivy/operations.html",
    "aiplus": "ui://aivy/aiplus.html",
    "alerts": "ui://aivy/alerts.html",
    "sites": "ui://aivy/sites.html",
}
UI_URIS = {uri: name for name, uri in UI_URIS_BY_NAME.items()}


def logo_data_uri(kind: str = "png") -> str:
    """The Aivy mark as a data: URI (no network inside a card). png is the
    128px raster for hosts and card footers; svg is the vector favicon."""
    import base64
    if kind == "svg":
        return "data:image/svg+xml;base64," + base64.b64encode((_DIR / "aivy-logo.svg").read_bytes()).decode()
    return "data:image/png;base64," + base64.b64encode((_DIR / "aivy-logo-128.png").read_bytes()).decode()


def server_icons() -> list[dict]:
    """Icons for serverInfo (MCP 2025-11-25): what a host shows beside the
    server's name instead of a letter avatar."""
    return [
        {"src": logo_data_uri("png"), "mimeType": "image/png", "sizes": ["128x128"]},
        {"src": logo_data_uri("svg"), "mimeType": "image/svg+xml", "sizes": ["any"]},
    ]


def render_template(name: str) -> str:
    html = (_DIR / f"{name}.html").read_text(encoding="utf-8")
    base_css = (_DIR / "base.css").read_text(encoding="utf-8")
    bridge = (_DIR / "bridge.js").read_text(encoding="utf-8").replace("/*LOGO*/", logo_data_uri("png"), 1)
    return html.replace("/*BASE_CSS*/", base_css, 1).replace("/*BRIDGE_JS*/", bridge, 1)
