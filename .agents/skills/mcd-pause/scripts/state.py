"""Local state for meal reminders and observed orders."""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc():
    return datetime.now(timezone.utc)


def aware(value=None):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value or now_utc()
    if result.tzinfo is None:
        raise ValueError("时间需要包含时区，例如 2026-10-14T11:40:00+08:00")
    return result


class State:
    def __init__(self, directory=None):
        folder = Path(directory or os.environ.get("MCPAUSE_STATE_DIR", Path.home() / ".local/share/mcd-pause"))
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / "state.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        if os.name != "nt":
            self.path.chmod(0o600)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value, ensure_ascii=False)))

    def keys(self, prefix):
        return [row[0] for row in self.db.execute("SELECT key FROM kv WHERE key LIKE ? ORDER BY key", (prefix + "%",))]

    def once(self, key, value=True):
        with self.db:
            return bool(self.db.execute("INSERT OR IGNORE INTO kv VALUES (?,?)",
                                       (key, json.dumps(value, ensure_ascii=False))).rowcount)

    def replace_if(self, key, expected, replacement):
        with self.db:
            return bool(self.db.execute("UPDATE kv SET value=? WHERE key=? AND value=?",
                        (json.dumps(replacement, ensure_ascii=False), key,
                         json.dumps(expected, ensure_ascii=False))).rowcount)

    def profile(self):
        return self.get("profile", {"timezone": "Asia/Shanghai", "reminders_enabled": False,
                   "celebrate_enabled": False, "weekdays": [0, 1, 2, 3, 4],
                   "meals": {"lunch": "12:00", "dinner": "18:30"}, "lead_minutes": 20,
                   "quiet_start": "22:30", "quiet_end": "08:00", "constraints": {"budget_cents": 3000}})

    def configure(self, changes):
        profile = self.profile()
        allowed = set(profile) | {"excluded_names", "store_context"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("配置包含未知字段")
        profile.update(changes)
        ZoneInfo(profile["timezone"])
        if not isinstance(profile["reminders_enabled"], bool) or not isinstance(profile["celebrate_enabled"], bool):
            raise ValueError("提醒和庆祝开关必须是布尔值")
        if type(profile["lead_minutes"]) is not int or not 0 <= profile["lead_minutes"] <= 120:
            raise ValueError("提前时间应为 0–120 分钟")
        if not isinstance(profile["weekdays"], list) or any(type(day) is not int or not 0 <= day <= 6 for day in profile["weekdays"]):
            raise ValueError("weekdays 使用 0–6（周一至周日）")
        if not isinstance(profile["meals"], dict) or set(profile["meals"]) - {"breakfast", "lunch", "dinner"}:
            raise ValueError("餐次仅支持 breakfast、lunch、dinner")
        constraints = profile["constraints"]
        if not isinstance(constraints, dict) or set(constraints) - {"budget_cents", "max_kcal", "min_protein", "max_sodium"}:
            raise ValueError("营养配置包含未知字段")
        if type(constraints.get("budget_cents")) is not int or constraints["budget_cents"] <= 0:
            raise ValueError("预算必须是正整数分")
        for key in ("max_kcal", "min_protein", "max_sodium"):
            value = constraints.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("营养目标必须是有限非负数")
        excluded = profile.get("excluded_names", [])
        if not isinstance(excluded, list) or any(not isinstance(name, str) or not name.strip() for name in excluded):
            raise ValueError("排除名称必须是非空文本列表")
        for clock in [*profile["meals"].values(), profile["quiet_start"], profile["quiet_end"]]:
            datetime.strptime(clock, "%H:%M")
        profile["meals"] = {meal: datetime.strptime(clock, "%H:%M").strftime("%H:%M") for meal, clock in profile["meals"].items()}
        for field in ("quiet_start", "quiet_end"):
            profile[field] = datetime.strptime(profile[field], "%H:%M").strftime("%H:%M")
        self.set("profile", profile)
        return profile

    def reminder(self, meal, at=None, action="tick", minutes=20):
        profile = self.profile()
        local = aware(at).astimezone(ZoneInfo(profile["timezone"]))
        if meal not in profile["meals"]:
            raise ValueError("餐次尚未配置")
        key = f"meal:{local.date()}:{meal}"
        previous = self.get(key, {})
        if action == "done":
            self.set(key, {"status": "done"})
            return {"action": "none", "reason": "这餐已安排"}
        if action == "snooze":
            if not 1 <= minutes <= 120:
                raise ValueError("延后时间应为 1–120 分钟")
            target = local + timedelta(minutes=minutes)
            self.set(key, {"status": "snoozed", "next_at": target.isoformat()})
            return {"action": "schedule_once", "at": target.isoformat(), "meal": meal,
                    "note": "本命令只保存状态；宿主还需安排一次到时检查"}
        if not profile["reminders_enabled"] or local.weekday() not in profile["weekdays"]:
            return {"action": "none", "reason": "提醒关闭或非提醒日"}
        clock = local.strftime("%H:%M")
        start, end = profile["quiet_start"], profile["quiet_end"]
        quiet = (start <= clock < end) if start < end else (clock >= start or clock < end) if start != end else False
        if quiet or previous.get("status") in ("done", "sent"):
            return {"action": "none", "reason": "免打扰或本餐已处理"}
        hour, minute = map(int, profile["meals"][meal].split(":"))
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        due = target - timedelta(minutes=profile["lead_minutes"])
        until = target + timedelta(minutes=45)
        if previous.get("status") == "snoozed":
            due = aware(previous["next_at"])
            until = due + timedelta(minutes=45)
        if not due <= local <= until:
            return {"action": "none", "reason": "未到提醒窗口或窗口已过"}
        updated = {"status": "sent", "at": local.isoformat()}
        consumed = self.replace_if(key, previous, updated) if previous else self.once(key, updated)
        return {"action": "recommend" if consumed else "none", "meal": meal,
                "at": local.isoformat(), "reason": "首次饭点检查" if consumed else "其他检查已处理"}

    def observe(self, order_id, data, at=None):
        if not isinstance(data, dict):
            raise ValueError("订单响应结构未知")
        returned_id = data.get("orderId") or data.get("orderNo")
        if returned_id is not None and str(returned_id) != order_id:
            raise ValueError("返回订单号与查询订单不一致")
        timestamp = aware(at)
        key = "order:" + order_id
        old = self.get(key, {})
        label = next((str(data[k]) for k in ("orderStatusName", "orderStatusDesc", "statusName", "statusDesc", "orderStatus", "status")
                      if data.get(k) is not None), "未知")
        payment = next((str(data[k]) for k in ("payStatusName", "payStatusDesc", "paymentStatus", "payStatus") if data.get(k) is not None), "未知")
        groups = {"awaiting_payment": {"待支付", "待付款", "等待支付"},
                  "accepted": {"已接单", "门店已接单", "商家已接单", "备餐中", "制作中"},
                  "ready": {"待取餐", "可取餐", "请取餐", "配餐完成"},
                  "delivering": {"配送中", "正在配送"},
                  "completed": {"已完成", "订单完成", "订单已完成", "已送达"},
                  "cancelled": {"已取消", "订单已取消", "已退款"}}
        status = next((k for k, values in groups.items() if label in values), "unknown")
        # query-order's official output schema, checked 2026-10-14. Other numeric fields stay unknown.
        numeric = {"1": ("awaiting_payment", "待支付"), "2": ("accepted", "配餐中（已支付）"),
                   "4": ("delivering", "配送中"), "6": ("completed", "已完成"),
                   "7": ("cancelled", "已取消"), "8": ("completed", "已评价"),
                   "10": ("accepted", "餐厅确认配餐中")}
        raw_status = str(data.get("orderStatus", ""))
        if status == "unknown" and label == raw_status and raw_status in numeric:
            status, label = numeric[raw_status]
        paid = payment in {"已支付", "支付成功", "已付款", "PAID", "paid"}
        if raw_status == "2" and payment == "未知":
            paid, payment = True, "已支付"
        delivery = data.get("deliveryInfo") if isinstance(data.get("deliveryInfo"), dict) else {}
        is_delivery = bool(delivery or str(data.get("orderType")) == "2" or old.get("is_delivery"))
        delivered = label == "已送达" or (raw_status == "6" and status == "completed" and is_delivery)
        seen_active = old.get("seen_active", False) or status in {"awaiting_payment", "accepted", "ready", "delivering"}
        terminal = status in {"completed", "cancelled"}
        order = {"order_id": order_id, "reference": hashlib.sha256(order_id.encode()).hexdigest()[:8],
                 "status": status, "label": label, "payment_label": payment, "paid": paid,
                 "terminal": terminal, "updated_at": timestamp.isoformat(),
                 "is_delivery": is_delivery, "delivered": delivered, "seen_active": seen_active,
                 "started_at": old.get("started_at", timestamp.isoformat()),
                 "poll_count": old.get("poll_count", 0) + 1, "errors": 0,
                 "take_code": str(data.get("takeCode") or data.get("pickupCode") or ""),
                 "eta": str(delivery.get("expectDeliveryTime") or data.get("estimatedArrivalTime") or data.get("estimatedFinishTime") or "")}
        self.set(key, order)
        changed = any(old.get(k) != order.get(k) for k in ("label", "payment_label", "take_code", "eta"))
        eligible = bool(self.profile()["celebrate_enabled"] and delivered and seen_active
                        and not self.get("celebration:" + order_id))
        return {"order": order, "changed": changed, "celebration_eligible": eligible}

    def poll_decision(self, order_id, at=None):
        stamp = aware(at)
        order = self.get("order:" + order_id)
        if not order:
            return {"poll": True}
        if order["terminal"] or order.get("poll_count", 0) >= 30 or order.get("errors", 0) >= 3:
            return {"poll": False, "stop": True, "reason": "订单结束或达到检查上限"}
        if stamp - aware(order["started_at"]) >= timedelta(hours=2):
            return {"poll": False, "stop": True, "reason": "跟进已超过两小时"}
        if stamp - aware(order["updated_at"]) < timedelta(minutes=3):
            return {"poll": False, "stop": False, "reason": "距离上次检查不足三分钟"}
        return {"poll": True}

    def poll_failed(self, order_id, at=None):
        stamp = aware(at).isoformat()
        old = self.get("order:" + order_id, {"order_id": order_id, "terminal": False,
                     "started_at": stamp, "poll_count": 0})
        old.update(errors=old.get("errors", 0) + 1, updated_at=stamp)
        self.set("order:" + order_id, old)
        return {"stop": old["errors"] >= 3, "errors": old["errors"]}

    def claim_celebration(self, order_id, at=None):
        order = self.get("order:" + order_id, {})
        recent = timedelta(0) <= aware(at) - aware(order["updated_at"]) < timedelta(minutes=5) if order.get("updated_at") else False
        if not self.profile()["celebrate_enabled"] or not order.get("delivered") or not order.get("seen_active") or not recent:
            return {"fire": False}
        return {"fire": self.once("celebration:" + order_id, {"attempted_at": aware(at).isoformat()}),
                "note": "仅宿主礼炮工具返回 fired=true 时才能说已播放"}
