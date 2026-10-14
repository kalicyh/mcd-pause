"""Compare the same meal with and without a quoted card; never purchase a card."""
import copy
import json

from mcp_client import unwrap
from orders import QUOTE_FIELDS

CONTEXT_FIELDS = ("storeCode", "beCode", "beType", "orderType", "reservationDate")
FEE_FIELDS = ("deliveryPrice", "packingPrice", "tablewarePrice")


def with_order_options(menu_response):
    options = {}
    for code, meal in unwrap(menu_response).get("meals", {}).items():
        raw = meal.get("withOrder")
        if not meal.get("canWithOrder") or not isinstance(raw, dict):
            continue
        value = {k: raw[k] for k in ("cardId", "cardType", "membershipCode") if k in raw}
        if raw.get("specId"):
            value["membershipSpecId"] = raw["specId"]
        if not ((value.get("membershipCode") and value.get("membershipSpecId")) or
                (value.get("cardId") and type(value.get("cardType")) is int)):
            continue
        key = json.dumps(value, sort_keys=True)
        option = options.setdefault(key, {"withOrder": value, "meals": []})
        option["meals"].append({"code": str(code), "name": meal.get("name", str(code))})
    return list(options.values())


def money_field(data, field):
    value = data.get(field)
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("核价金额必须是非负整数分：" + field)
    return value


def public_selection(request):
    def modification(raw):
        return {"values": [{k: v[k] for k in ("code", "key", "quantity") if k in v}
                           for v in raw.get("values", [])]}

    def item(raw, combo=False):
        result = {k: raw[k] for k in (("code", "quantity") if combo else ("productCode", "quantity")) if k in raw}
        if "modification" in raw:
            result["modification"] = modification(raw["modification"])
        if "roundList" in raw:
            result["roundList"] = [{"round": r["round"], "comboItemList": [item(c, True) for c in r["comboItemList"]]}
                                   for r in raw["roundList"]]
        return result

    return {"context": {k: request[k] for k in CONTEXT_FIELDS if k in request},
            "items": [item(i) for i in request["items"]]}


def break_even(fee, first_saving, repeat_saving, remaining_uses=None):
    """An explicit repeat-use scenario, not evidence of future eligibility."""
    if any(type(v) is not int for v in (fee, first_saving, repeat_saving)) or fee < 0 or repeat_saving < 0:
        raise ValueError("回本计算需要整数分，卡费和后续每单节省不能为负")
    if remaining_uses is not None and (type(remaining_uses) is not int or remaining_uses < 0):
        raise ValueError("剩余可用次数必须是非负整数")
    if first_saving >= fee:
        return {"orders": 1, "reason": "first_order"}
    if not repeat_saving:
        return {"orders": None, "reason": "no_recurring_saving"}
    future = (fee - first_saving + repeat_saving - 1) // repeat_saving
    if remaining_uses is not None and future > remaining_uses:
        return {"orders": None, "reason": "usage_limit"}
    return {"orders": 1 + future, "reason": "assumption"}


def compare_card(baseline_request, baseline_response, card_request, card_response,
                 card_product_code, quoted_at):
    if any(not isinstance(r, dict) or set(r) - QUOTE_FIELDS for r in (baseline_request, card_request)):
        raise ValueError("对比只接受核价参数，不接受地址或账号字段")
    if baseline_request.get("withOrder") or not card_request.get("withOrder"):
        raise ValueError("需要不加购卡与加购卡两份核价")
    without_card = {k: v for k, v in card_request.items() if k != "withOrder"}
    if baseline_request != without_card or not baseline_request.get("items"):
        raise ValueError("两份核价必须是相同门店、餐单、特调和优惠券；只能改变 withOrder")
    baseline, card = unwrap(baseline_response), unwrap(card_response)
    lines = card.get("productList", [])
    matched = [p for p in lines if p.get("productCode") == card_product_code]
    if len(matched) != 1 or matched[0].get("quantity") != 1 or not matched[0].get("productName"):
        raise ValueError("开卡费用需来自本次核价中明确的一张权益卡明细")
    if any(p.get("productCode") == card_product_code for p in baseline.get("productList", [])):
        raise ValueError("不开卡方案中不能已包含同一张卡")
    fee = money_field(matched[0], "subtotal")
    if fee is None:
        raise ValueError("接口未给出卡费，不能当作免费")

    def amounts(data):
        values = {k: money_field(data, k) for k in ("price", "productPrice", "discount", *FEE_FIELDS)}
        if values["price"] is None or values["productPrice"] is None:
            raise ValueError("接口缺少商品金额或应付总额")
        return values

    before, after = amounts(baseline), amounts(card)
    subtotals = [money_field(p, "subtotal") for p in lines]
    # A card line is not an extra charge to add again when the quoted total already includes it.
    if (None in subtotals or sum(subtotals) != after["productPrice"] or
            after["productPrice"] < fee or
            sum(after[k] or 0 for k in ("productPrice", *FEE_FIELDS)) != after["price"]):
        raise ValueError("无法核实卡费是否已包含在总额中，停止计算最终付款")
    before["cardFee"] = 0
    before["foodPrice"] = before["productPrice"]
    after["cardFee"] = fee
    after["foodPrice"] = after["productPrice"] - fee
    savings = {k: before[k] - after[k] if before[k] is not None and after[k] is not None else None
               for k in ("foodPrice", *FEE_FIELDS)}
    net = before["price"] - after["price"]
    gross = net + fee
    return {"source": "calculate-price", "quoted_at": quoted_at,
            "selection": public_selection(baseline_request),
            "card": {"code": card_product_code, "name": matched[0]["productName"], "fee_cents": fee},
            "without_card": before, "with_card": after, "savings": savings,
            "gross_saving_cents": gross, "net_saving_cents": net,
            "repeat_saving_assumption_cents": max(0, gross),
            "break_even": break_even(fee, gross, max(0, gross))}


def comparison_for_view(bundle):
    """Recompute from source evidence instead of trusting precomputed UI totals."""
    if bundle is None:
        return None
    return compare_card(*(copy.deepcopy(bundle[k]) for k in (
        "baseline_request", "baseline_response", "card_request", "card_response")),
        bundle["card_product_code"], bundle["quoted_at"])
