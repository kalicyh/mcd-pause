"""Render a self-contained host panel or a standalone, offline demo."""
from datetime import datetime, timezone
import json
from pathlib import Path
from nutrition import normalize_name

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render_reminder(meal, intent=None):
    names = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}
    config = {'meal': names[meal], 'intent': intent}
    encoded = json.dumps(config, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return (ASSETS / 'reminder.html').read_text().replace('__MCPAUSE_REMINDER_DATA__', encoded)


def render_panel(catalog, profile=None, orders=None, draft=None, inline=True):
    demo = catalog.get("mode") == "demo"
    excluded = (profile or {}).get("excluded_names", [])
    if excluded and not demo:
        catalog = {**catalog, "foods": [f for f in catalog["foods"] if not any(
            normalize_name(name) in normalize_name(f["name"]) for name in excluded)]}
    config = {
        "catalog": catalog, "mode": "demo" if demo else "live",
        "constraints": (profile or {}).get("constraints", {"budget_cents": 3000}),
        "orders": orders or [],
        "draft": ({k: draft[k] for k in ("id", "summary", "total_cents", "expires_at", "status")} if draft else None),
        "rendered_at": datetime.now(timezone.utc).isoformat(),
    }
    if demo:
        config["constraints"] = {"budget_cents": 3000, "max_kcal": 650, "min_protein": 30}
        config["draft"] = None
        config["orders"] = []
    # Script text is inert JSON; escape HTML delimiters to prevent early </script> termination.
    encoded = json.dumps(config, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    fragment = (ASSETS / "panel.html").read_text().replace("__MCPAUSE_DATA__", encoded)
    if inline:
        return fragment
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>麦麦暂停键 · McPause</title><style>html{color-scheme:light dark}body{margin:0;padding:20px 8px;font-family:system-ui;background:light-dark(#f6f3ed,#161616)}</style></head><body>' + fragment + '</body></html>'
