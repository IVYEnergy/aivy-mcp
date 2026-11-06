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


def render_template(name: str) -> str:
    html = (_DIR / f"{name}.html").read_text(encoding="utf-8")
    base_css = (_DIR / "base.css").read_text(encoding="utf-8")
    bridge = (_DIR / "bridge.js").read_text(encoding="utf-8")
    return html.replace("/*BASE_CSS*/", base_css, 1).replace("/*BRIDGE_JS*/", bridge, 1)
