"""Bind confirmation to one priced request and consume it before any order write."""
import hashlib
import json
from datetime import timedelta
import uuid

from mcp_client import unwrap
from nutrition import cents
from state import aware

QUOTE_FIELDS = {"beCode", "beType", "gmServiceCode", "items", "needTableware", "orderType", "reservationDate", "storeCode", "withOrder"}
ORDER_FIELDS = QUOTE_FIELDS | {"addressId", "remark", "takeWayCode"}


def validate_request(request):
    if not isinstance(request, dict) or set(request) - ORDER_FIELDS:
        raise ValueError("订单参数包含未知字段")
    be_type = request.get("beType")
    if type(be_type) is not int or be_type not in (1, 2, 5, 6):
        raise ValueError("必须明确 beType：1 自取、2 外送、5 得来速、6 团餐")
    expected_type = 1 if be_type in (1, 5) else 2
    if type(request.get("orderType")) is not int or request["orderType"] != expected_type:
        raise ValueError("orderType 与取餐方式不一致")
    if not isinstance(request.get("storeCode"), str) or not request["storeCode"].isdigit():
        raise ValueError("必须使用查询返回的数字门店编码")
    if be_type == 1 and "beCode" in request:
        raise ValueError("到店自取不能传 beCode")
    if be_type != 1 and not request.get("beCode"):
        raise ValueError("此取餐方式必须传对应门店的 beCode")
    if expected_type == 2 and (not request.get("addressId") or "takeWayCode" in request):
        raise ValueError("外送需要 addressId，且不传 takeWayCode")
    if expected_type == 1 and not request.get("takeWayCode"):
        raise ValueError("取餐方式必须来自 calculate-price 的 takeWayList")
    if be_type == 6 and not request.get("gmServiceCode"):
        raise ValueError("团餐需要已查询的助餐服务码")
    if request.get("remark") and (expected_type != 2 or len(request["remark"]) > 50):
        raise ValueError("外送备注最多 50 字")
    items = request.get("items")
    if not isinstance(items, list) or not items or len(items) > 20:
        raise ValueError("需要 1–20 项商品")
    for item in items:
        if not isinstance(item, dict) or not item.get("productCode") or str(item["productCode"]).startswith("DEMO"):
            raise ValueError("商品编码缺失或为演示商品")
        if type(item.get("quantity")) is not int or not 1 <= item["quantity"] <= 20:
            raise ValueError("商品数量必须为 1–20 的整数")
    return {k: v for k, v in request.items() if k in QUOTE_FIELDS}


def amount_at(data, field):
    value = data
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("核价响应的金额字段变化；需要人工核对映射")
        value = value[part]
    return cents(value, "fen")


def check_takeway(request, quote):
    if request["orderType"] == 1:
        options = quote.get("takeWayList", [])
        if request["takeWayCode"] not in [item.get("code") for item in options if isinstance(item, dict)]:
            raise ValueError("所选取餐方式不在本次核价返回的 takeWayList 中")


def card_terms(request, quote):
    if not request.get("withOrder"):
        return None
    food_codes = {item["productCode"] for item in request["items"]}
    cards = [p for p in quote.get("productList", [])
             if p.get("productCode") not in food_codes and "卡" in p.get("productName", "")]
    if len(cards) != 1 or cards[0].get("quantity") != 1:
        raise ValueError("无法核实本次加购的权益卡，请重新比较")
    card = cards[0]
    if not card.get("productCode") or type(card.get("subtotal")) is not int or card["subtotal"] < 0:
        raise ValueError("本次卡费待核实，请重新比较")
    return {k: card[k] for k in ("productCode", "productName", "quantity", "subtotal")}


def make_draft(state, request, quote_response, amount_field, summary, at=None, budget_cents=None):
    validate_request(request)
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("需要向用户展示商品、门店、方式的确认摘要")
    quote = unwrap(quote_response)
    amount = amount_at(quote, amount_field)
    check_takeway(request, quote)
    if budget_cents is not None and amount > budget_cents:
        raise ValueError("官方核价超出本次预算，请调整搭配")
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    intent = hashlib.sha256(canonical.encode()).hexdigest()
    for key in state.keys("draft:"):
        previous = state.get(key)
        if previous["intent"] != intent or previous["status"] not in ("submitting", "uncertain", "submitted"):
            continue
        order = state.get("order:" + previous.get("order_id", ""), {})
        if not order.get("terminal"):
            raise ValueError("相同订单已有提交记录；先核对该订单，不能重复创建")
    stamp = aware(at)
    draft = {"id": uuid.uuid4().hex[:12], "status": "quoted", "request": request,
             "intent": intent, "total_cents": amount, "amount_field": amount_field,
             "summary": summary, "created_at": stamp.isoformat(),
             "expires_at": (stamp + timedelta(minutes=5)).isoformat()}
    if request.get("withOrder"):
        draft["card_terms"] = card_terms(request, quote)
    state.set("draft:" + draft["id"], draft)
    return draft


def get_confirmable(state, draft_id, confirmation, at=None):
    key = "draft:" + draft_id
    draft = state.get(key)
    if not draft or confirmation != draft_id:
        raise ValueError("需要对本次确认单的明确确认")
    if draft["status"] != "quoted" or aware(at) > aware(draft["expires_at"]):
        raise ValueError("确认单已使用、过期或需要重新核价")
    return draft


def authorize(state, draft_id, confirmation, fresh_response, at=None):
    key = "draft:" + draft_id
    draft = get_confirmable(state, draft_id, confirmation, at)
    locked = {**draft, "status": "checking"}
    if not state.replace_if(key, draft, locked):
        raise ValueError("该确认单正在被其他进程处理")
    try:
        fresh = unwrap(fresh_response)
        check_takeway(draft["request"], fresh)
        new_amount = amount_at(fresh, draft["amount_field"])
        fresh_card = card_terms(draft["request"], fresh)
    except Exception:
        state.set(key, {**locked, "status": "quote_failed"})
        raise
    if new_amount != draft["total_cents"]:
        state.set(key, {**locked, "status": "price_changed", "latest_cents": new_amount})
        return {"created": False, "reason": "price_changed", "total_cents": new_amount,
                "next": "展示新价格，重新生成确认单并等待用户确认"}
    if fresh_card != draft.get("card_terms"):
        state.set(key, {**locked, "status": "card_changed"})
        return {"created": False, "reason": "card_changed", "total_cents": new_amount,
                "next": "卡种或卡费已变化，请重新比较并确认"}
    intent_key = "submission:" + draft["intent"]
    lease = {"draft_id": draft_id}
    old_lease = state.get(intent_key)
    if old_lease:
        prior = state.get("draft:" + old_lease["draft_id"], {})
        order = state.get("order:" + prior.get("order_id", ""), {})
        reusable = prior.get("status") == "resolved_no_order" or order.get("terminal")
        acquired = bool(reusable and state.replace_if(intent_key, old_lease, lease))
    else:
        acquired = state.once(intent_key, lease)
    if not acquired:
        state.set(key, {**locked, "status": "duplicate_blocked"})
        raise ValueError("相同餐单已有提交记录，请先核对订单")
    # Reserve before either the native host or our client makes the write.
    state.set(key, {**locked, "status": "submitting"})
    return {"authorized": True, "draft_id": draft_id, "request": draft["request"]}


def complete(state, draft_id, response):
    key = "draft:" + draft_id
    draft = state.get(key)
    if not draft or draft["status"] != "submitting":
        raise ValueError("没有等待记录结果的提交")
    try:
        response = unwrap(response)
        order_id = response.get("orderId") if isinstance(response, dict) else None
        if not order_id:
            raise ValueError("下单响应没有可识别的 orderId")
    except Exception:
        state.set(key, {**draft, "status": "uncertain"})
        raise ValueError("下单结果不确定；禁止重试。请先通过订单列表或官方 App 核对") from None
    state.set(key, {**draft, "status": "submitted", "order_id": str(order_id)})
    return {"created": True, "order_id": str(order_id), "payment_url": response.get("payH5Url"),
            "payment_status": "unverified", "next": "由用户支付，再调用 query-order 核实状态"}


def submit(state, client, draft_id, confirmation, at=None):
    draft = get_confirmable(state, draft_id, confirmation, at)
    fresh = client.call("calculate-price", validate_request(draft["request"]))
    decision = authorize(state, draft_id, confirmation, fresh, at)
    if not decision.get("authorized"):
        return decision
    try:
        response = client.create_order(decision["request"])
    except Exception:
        state.set("draft:" + draft_id, {**state.get("draft:" + draft_id), "status": "uncertain"})
        raise ValueError("下单结果不确定；禁止重试。请先通过订单列表或官方 App 核对") from None
    return complete(state, draft_id, response)
