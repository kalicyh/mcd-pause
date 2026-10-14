"""Deterministic estimates; unknown nutrition is never treated as zero."""
import csv
import itertools
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from mcp_client import unwrap

FIELDS = ("energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium")


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
        return out if math.isfinite(out) and out >= 0 else None
    except (ValueError, TypeError):
        return None


def cents(value, unit="yuan"):
    if unit not in ("yuan", "fen"):
        raise ValueError("价格单位必须是 yuan 或 fen")
    if isinstance(value, bool) or value is None:
        raise ValueError("价格缺失")
    try:
        amount = Decimal(str(value)) * (100 if unit == "yuan" else 1)
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError("价格不是有效的整数分")
        return int(amount)
    except InvalidOperation:
        raise ValueError("价格格式无法识别") from None


def normalize_name(name):
    # Keep sizes, temperatures, counts and parenthesized qualifiers intact.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(name))).casefold()


def nutrition_rows(raw):
    data = unwrap(raw) if isinstance(raw, dict) and any(k in raw for k in ("result", "structuredContent", "data", "content")) else raw
    if isinstance(data, str):
        if "\n" not in data:
            data = data.replace("\\n", "\n")
        if data.lstrip().startswith("[") and re.match(r"\s*\[\d+\]\{", data):
            header, _, body = data.partition(":")
            columns = header[header.index("{") + 1:header.index("}")].split(",")
            count = int(re.search(r"\[(\d+)\]", header).group(1))
            records = list(csv.reader(body.strip().splitlines(), skipinitialspace=True))
            if len(records) != count or any(len(row) != len(columns) for row in records):
                raise ValueError("营养表行数或列数不匹配，停止解析")
            data = [dict(zip(columns, row)) for row in records]
        else:
            data = json.loads(data)
    if not isinstance(data, list):
        raise ValueError("营养表必须是列表或官方 TOON 表格")
    return [{"productName": row["productName"], **{k: number(row.get(k)) for k in FIELDS}}
            for row in data if isinstance(row, dict) and row.get("productName")]


def match_nutrition(name, rows, aliases=None):
    target = (aliases or {}).get(name, name)
    matches = [r for r in rows if normalize_name(r["productName"]) == normalize_name(target)]
    if len(matches) != 1:
        return {"status": "ambiguous" if matches else "unknown", "values": {k: None for k in FIELDS}}
    return {"status": "alias" if target != name else "exact", "values": {k: matches[0].get(k) for k in FIELDS},
            "matched_name": matches[0]["productName"]}


def classify(name):
    if any(s in name for s in ("套餐", "组合", "随心配", "分享", "全家", "双人", "三人", "四人", "件套", "乐园餐", "亲子餐", "拼盘")):
        return "combo"
    if any(s in name for s in ("会员卡", "麦金卡", "早餐卡", "周边", "玩具", "购物袋", "挠痒", "捶背")):
        return "other"
    if any(s in name for s in ("堡", "不素之霸", "麦香鱼", "麦香鸡", "满分", "贝果", "卷", "粥", "巨无霸")):
        return "main"
    if any(s in name for s in ("可乐", "咖啡", "拿铁", "美式", "牛奶", "果汁", "红茶", "奶茶", "雪碧", "饮料")):
        return "drink"
    if any(s in name for s in ("薯条", "鸡块", "鸡翅", "V翅", "鸡排", "鸡柳", "薯饼", "冰淇淋", "麦旋风", "新地", "蛋挞", "派", "玉米", "沙拉", "苹果片")):
        return "side"
    return "other"


def catalog_from_menu(raw, rows, context, aliases=None):
    data = unwrap(raw)
    if not isinstance(data, dict) or not isinstance(data.get("meals"), (dict, list)):
        raise ValueError("菜单结构未知；核对响应后提供标准化 catalog，不猜测字段")
    meals = data["meals"]
    tags = {}
    for category in data.get("categories", []):
        for entry in category.get("meals", []):
            tags.setdefault(str(entry.get("code")), set()).update(entry.get("tags") or [])
    entries = meals.items() if isinstance(meals, dict) else ((m.get("code"), m) for m in meals)
    foods = []
    for key, entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("productName")
        code = entry.get("code") or key
        if not name or not code:
            continue
        price = entry.get("currentPrice")
        try:
            reference = cents(price) if price is not None else None
        except ValueError:
            reference = None
        kind = "combo" if "套餐" in tags.get(str(code), set()) or "麦金卡" in tags.get(str(code), set()) else classify(name)
        foods.append({"id": str(code), "name": name, "kind": kind,
                      "image": entry.get("image"),
                      "can_with_order": bool(entry.get("canWithOrder")),
                      "tags": sorted(tags.get(str(code), set())),
                      "reference_cents": reference,
                      "nutrition": match_nutrition(name, rows, aliases)})
    if not foods:
        raise ValueError("菜单未包含可识别餐品")
    codes = {f["id"] for f in foods}
    categories = [{"id": "menu-" + str(i), "name": str(c.get("name") or "其他餐品").replace("\n", " "),
                   "codes": list(dict.fromkeys(str(m.get("code")) for m in c.get("meals", [])
                                               if str(m.get("code")) in codes))}
                  for i, c in enumerate(data.get("categories", []))]
    return {"mode": "live", "source": "query-meals", "context": context, "foods": foods,
            "categories": [c for c in categories if c["codes"]]}


def totals(foods):
    result = {k: 0 for k in FIELDS}
    price = 0
    for item in foods:
        quantity = item.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 20:
            raise ValueError("份数必须是 1–20 的整数")
        cost = item.get("reference_cents")
        price = None if price is None or cost is None else price + cost * quantity
        values = item.get("nutrition", {}).get("values", {})
        for k in FIELDS:
            value = number(values.get(k))
            result[k] = None if result[k] is None or value is None else result[k] + value * quantity
    return {"reference_cents": price, **{k: round(v, 2) if v is not None else None for k, v in result.items()}}


def violations(total, constraints):
    problems = []
    for field, limit, direction in (("reference_cents", constraints.get("budget_cents"), "max"),
                                     ("energyKcal", constraints.get("max_kcal"), "max"),
                                     ("protein", constraints.get("min_protein"), "min"),
                                     ("sodium", constraints.get("max_sodium"), "max")):
        if limit is None:
            continue
        if total.get(field) is None:
            problems.append({"field": field, "reason": "unknown"})
        elif (direction == "max" and total[field] > limit) or (direction == "min" and total[field] < limit):
            problems.append({"field": field, "reason": direction, "difference": round(abs(total[field] - limit), 2)})
    return problems


def recommend(catalog, constraints, excluded=None):
    constraints = dict(constraints)
    if type(constraints.get("budget_cents")) is not int or constraints["budget_cents"] <= 0:
        raise ValueError("需要正整数分的预算")
    for key in ("max_kcal", "min_protein", "max_sodium"):
        if key in constraints and constraints[key] is not None:
            parsed = number(constraints[key])
            if parsed is None:
                raise ValueError("营养目标必须是非负数")
            constraints[key] = parsed
    foods = [f for f in catalog["foods"] if type(f.get("reference_cents")) is int and f["reference_cents"] > 0
             and not f.get("can_with_order")
             and f.get("kind") in ("main", "side", "drink")
             and not any(normalize_name(s) in normalize_name(f["name"]) for s in (excluded or []))]
    groups = {kind: sorted([f for f in foods if f.get("kind") == kind], key=lambda f: (f["reference_cents"], f["id"]))[:40]
              for kind in ("main", "side", "drink")}
    candidates = []
    for main, side, drink in itertools.product(groups["main"], [None] + groups["side"], [None] + groups["drink"]):
        chosen = [f for f in (main, side, drink) if f]
        total = totals(chosen)
        if not violations(total, constraints):
            candidates.append({"foods": chosen, "totals": total, "needs_quote": True})
    if not candidates:
        return {"options": [], "reason": "没有满足全部约束且数据充分的单品组合。可以调整目标或查询套餐详情。"}
    rankings = {
        "saving": lambda x: (x["totals"]["reference_cents"], -(x["totals"]["protein"] or 0)),
        "protein": lambda x: (-(x["totals"]["protein"] if x["totals"]["protein"] is not None else -1), x["totals"]["reference_cents"]),
        "variety": lambda x: (-len(x["foods"]), x["totals"]["reference_cents"]),
    }
    selected = []
    seen = set()
    for goal, key in rankings.items():
        for candidate in sorted(candidates, key=key):
            identity = tuple(f["id"] for f in candidate["foods"])
            if identity not in seen:
                selected.append({"goal": goal, **candidate})
                seen.add(identity)
                break
    return {"options": selected, "candidate_count": len(candidates), "price_basis": "menu_reference"}
