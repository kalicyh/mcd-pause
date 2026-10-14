"""Prepare real menu cards and configurable meal details without creating orders."""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from mcp_client import unwrap
from nutrition import cents
from views import ASSETS
from membership import comparison_for_view


def difference(value, default=False):
    if value is None:
        return 0 if default else None
    match = re.fullmatch(r"\s*([+-]?)\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)\s*", str(value))
    if not match:
        return None
    return (-1 if match[1] == "-" else 1) * cents(match[2])


def modifications(raw):
    groups = []
    for group in (raw or {}).get("items", []):
        values = [{k: v[k] for k in ("code", "name", "price", "minQuantity", "maxQuantity",
                                     "selectedQuantity", "selectedKey", "unselectedKey") if k in v}
                  for v in group.get("values", [])]
        if values:
            groups.append({"min": group.get("minValues", 0), "max": group.get("maxValues", len(values)),
                           "values": values})
    return groups


def official_image(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.netloc == "menu-img.mcd.cn"
                and url.path.startswith("/pcm/") and not url.query and not url.fragment):
            return value
    except ValueError:
        pass
    return None


def meal_detail(raw, code, images=None):
    images = images or {}
    detail = unwrap(raw)
    if str(detail.get("code")) != code:
        raise ValueError("套餐详情编码与推荐卡片不一致")
    rounds = []
    for group in detail.get("rounds", []):
        choices = [{"code": c["code"], "name": c["name"], "quantity": c.get("quantity", 0),
                    "image": official_image(c.get("image")) or images.get(c["code"]),
                    "default": c.get("isDefault") == 1, "max": c.get("maxQuantity", -1),
                    "difference": difference(c.get("diffPrice"), c.get("isDefault") == 1),
                    "modifications": modifications(c.get("modification"))}
                   for c in group.get("choices", [])]
        rounds.append({"id": str(group["id"]), "name": group.get("name", "套餐选项"),
                       "min": group.get("minQuantity", 0), "max": group.get("maxQuantity", 0),
                       "choices": choices})
    return {"image": official_image(detail.get("image")), "rounds": rounds,
            "modifications": modifications(detail.get("modification"))}


def shopping_data(catalog, details, news=None, keyword=None, selected=None, quote=None, card_comparison=None):
    if catalog.get("mode") == "demo":
        raise ValueError("推荐选餐界面需要真实菜单；离线演示请使用 demo 命令")
    news = news or {}
    images = {f["id"]: official_image(f.get("image")) for f in catalog.get("foods", [])}
    cards = []
    for food in catalog.get("foods", []):
        price = food.get("reference_cents")
        if type(price) is not int or price < 0:
            price = None
        evidence = next((tag for tag in food.get("tags", [])
                         if re.search(r"新品|上新|全新登场", tag)), None)
        supplied = news.get(food["id"], {})
        if supplied.get("source") == "campaign-calendar" and supplied.get("evidence"):
            evidence = supplied["evidence"]
        card = {"code": food["id"], "name": food["name"], "price": price,
                "image": images[food["id"]],
                "new": bool(evidence), "new_evidence": evidence,
                "tags": food.get("tags", []), "can_with_order": food.get("can_with_order", False), "detail": None}
        if food["id"] in details:
            card["detail"] = meal_detail(details[food["id"]], food["id"], images)
        cards.append(card)
    cards.sort(key=lambda card: (not card["new"], card["price"] if card["price"] is not None else float("inf"), card["code"]))
    if not cards:
        raise ValueError("没有符合本次选餐范围的可售餐品")
    context = {k: v for k, v in catalog.get("context", {}).items()
               if k in ("storeCode", "beCode", "beType", "orderType", "reservationDate", "label", "delivery_label")}
    fees = {}
    if quote:
        data = unwrap(quote)
        fees = {k: data[k] for k in ("deliveryPrice", "packingPrice")
                if type(data.get(k)) is int and data[k] >= 0}
    categories = catalog.get("categories", [])
    included = {code for group in categories for code in group["codes"]}
    remaining = [c["code"] for c in cards if c["code"] not in included]
    if remaining:
        categories = [*categories, {"id": "uncategorized", "name": "其他餐品" if included else "全部餐品", "codes": remaining}]
    return {"cards": cards, "categories": categories, "intent": keyword,
            "priority_codes": [c["code"] for c in cards if keyword and keyword in c["name"]],
            "context": context, "fees": fees,
            "card_comparison": comparison_for_view(card_comparison),
            "selected": selected if any(c["code"] == selected for c in cards) else None,
            "captured_at": catalog.get("captured_at"),
            "rendered_at": datetime.now(timezone.utc).isoformat()}


def render_shop(catalog, details, standalone=False, browser_token=None, **options):
    config = shopping_data(catalog, details, **options)
    # The lightweight visualization host has a fixed image allowlist.
    config["images_enabled"] = standalone
    config["standalone"] = standalone
    config["browser_session"] = bool(standalone and browser_token)
    encoded = json.dumps(config, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    fragment = (ASSETS / "shopping.html").read_text().replace("__MCPAUSE_SHOP_DATA__", encoded)
    if standalone:
        page = (ASSETS / "shopping-page.html").read_text().replace("__MCPAUSE_SHOP_FRAGMENT__", fragment)
        bridge = (ASSETS / "browser.js").read_text() if browser_token else ""
        return page.replace("__MCPAUSE_BROWSER_SCRIPT__", bridge).replace("__MCPAUSE_BROWSER_TOKEN__", json.dumps(browser_token))
    return fragment
