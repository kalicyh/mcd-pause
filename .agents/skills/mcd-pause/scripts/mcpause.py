#!/usr/bin/env python3
"""McPause CLI. Run --help; offline commands do not require an MCP credential."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

from mcp_client import Client, READ_TOOLS, unwrap
from nutrition import catalog_from_menu, nutrition_rows, recommend, cents
from orders import make_draft, submit, validate_request, authorize, complete
from state import State, now_utc
from views import ASSETS, render_panel


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    if os.name != "nt":
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return str(target.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦麦暂停键：饭点、选餐、续写卡。默认不会创建定时任务或下单。")
    parser.add_argument("--state-dir", help="本地状态目录；默认为用户 .local/share/mcd-pause")
    parser.add_argument("--token-stdin", action="store_true", help="联网命令使用不回显输入读取凭据，不保存")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="生成完全离线的演示界面")
    demo.add_argument("--out", required=True)
    demo.add_argument("--inline", action="store_true")
    profile = sub.add_parser("profile", help="查看或更新本地偏好；不会创建提醒")
    profile.add_argument("--file")
    sub.add_parser("doctor", help="只读联调：工具清单、服务器时间、营养表")
    read = sub.add_parser("mcp-read", help="只调用只读 MCP 工具")
    read.add_argument("tool", choices=sorted(READ_TOOLS))
    read.add_argument("--arguments", help="工具参数 JSON 文件；省略使用空对象")
    read.add_argument("--out")
    nutrition = sub.add_parser("nutrition", help="解析营养响应，保留未知项")
    nutrition.add_argument("--response", required=True)
    nutrition.add_argument("--out")
    catalog = sub.add_parser("catalog", help="把实时菜单与营养表按确切名称匹配")
    for arg in ("menu", "nutrition", "context", "out"):
        catalog.add_argument("--" + arg, required=True)
    catalog.add_argument("--aliases", help="人工核实的完整名称映射 JSON")
    rec = sub.add_parser("recommend", help="筛选单品组合；菜单参考价仍需官方核价")
    rec.add_argument("--catalog", required=True)
    rec.add_argument("--budget", required=True, help="人民币元")
    rec.add_argument("--max-kcal", type=float)
    rec.add_argument("--min-protein", type=float)
    rec.add_argument("--max-sodium", type=float)
    rec.add_argument("--exclude", action="append", default=[])
    panel = sub.add_parser("render", help="用已取得的数据生成聊天内界面")
    panel.add_argument("--catalog", required=True)
    panel.add_argument("--out", required=True)
    panel.add_argument("--standalone", action="store_true")
    reminder = sub.add_parser("reminder", help="饭点检查与去重；调度由 Codex 执行")
    reminder.add_argument("action", choices=["tick", "snooze", "done"])
    reminder.add_argument("--meal", choices=["breakfast", "lunch", "dinner"], required=True)
    reminder.add_argument("--minutes", type=int, default=20)
    reminder.add_argument("--at", help="仅离线场景测试用，带时区的 ISO 时间")
    checkpoint = sub.add_parser("checkpoint", help="保存或读取三行续写卡")
    checkpoint.add_argument("action", choices=["save", "show"])
    checkpoint.add_argument("--file")
    order = sub.add_parser("order", help="确认单、受控下单与订单查询")
    actions = order.add_subparsers(dest="action", required=True)
    for action in ("quote", "draft"):
        q = actions.add_parser(action, help="quote 联网核价；draft 导入宿主已取得的核价结果")
        q.add_argument("--request", required=True)
        q.add_argument("--amount-field", required=True, help="核价 data 中经核实的应付总额字段路径，单位分")
        q.add_argument("--summary", required=True)
        q.add_argument("--budget", help="人民币元")
        if action == "draft":
            q.add_argument("--response", required=True)
    s = actions.add_parser("submit", help="只能在用户确认当前确认单后执行；会创建真实订单")
    s.add_argument("--draft", required=True)
    s.add_argument("--confirm", required=True)
    s.add_argument("--user-confirmed", action="store_true", required=True)
    auth = actions.add_parser("authorize", help="供原生 MCP 使用：复核新核价并占用一次提交")
    auth.add_argument("--draft", required=True)
    auth.add_argument("--confirm", required=True)
    auth.add_argument("--user-confirmed", action="store_true", required=True)
    auth.add_argument("--response", required=True)
    for action in ("complete", "uncertain"):
        final = actions.add_parser(action)
        final.add_argument("--draft", required=True)
        if action == "complete":
            final.add_argument("--response", required=True)
    for action in ("watch", "observe", "celebrate", "poll-check", "poll-failed"):
        p = actions.add_parser(action)
        p.add_argument("--order-id", required=True)
        if action == "observe":
            p.add_argument("--response", required=True)
    resolve = actions.add_parser("resolve", help="人工核对后的提交恢复；不创建新订单")
    resolve.add_argument("--draft", required=True)
    resolve.add_argument("--order-id", help="从官方订单记录确认的订单号，将联网核验")
    resolve.add_argument("--response", help="原生 MCP 路径：已查询到的订单响应，须含相同订单号")
    resolve.add_argument("--confirmed-no-order", action="store_true", help="用户已在官方渠道核实没有该订单")
    actions.add_parser("local", help="查看本机已跟进的订单")
    args = parser.parse_args(argv)
    client_holder = []

    def client():
        if not client_holder:
            token = getpass.getpass("MCP Token（不保存）: ") if args.token_stdin else None
            client_holder.append(Client(token))
        return client_holder[0]

    # Pure transformations must not create user state directories.
    if args.command == "demo":
        return {"path": save(args.out, render_panel(load(ASSETS / "demo.json"), inline=args.inline)), "mode": "demo"}
    if args.command == "doctor":
        connection = client()
        available = connection.rpc("tools/list", {}).get("tools", [])
        now = unwrap(connection.call("now-time-info", {}))
        rows = nutrition_rows(connection.call("list-nutrition-foods", {}))
        return {"ok": True, "tools": len(available), "nutrition_records": len(rows),
                "server_time": now.get("formatted") if isinstance(now, dict) else None,
                "private_reads": False, "writes": False}
    if args.command == "mcp-read":
        response = client().call(args.tool, load(args.arguments) if args.arguments else {})
        unwrap(response)
        return {"path": save(args.out, response)} if args.out else response
    if args.command == "nutrition":
        rows = nutrition_rows(load(args.response))
        return {"path": save(args.out, rows), "records": len(rows)} if args.out else rows
    if args.command == "catalog":
        result = catalog_from_menu(load(args.menu), nutrition_rows(load(args.nutrition)), load(args.context),
                                   load(args.aliases) if args.aliases else None)
        result["captured_at"] = now_utc().isoformat()
        return {"path": save(args.out, result), "foods": len(result["foods"])}
    if args.command == "recommend":
        constraints = {"budget_cents": cents(args.budget), "max_kcal": args.max_kcal,
                       "min_protein": args.min_protein, "max_sodium": args.max_sodium}
        return recommend(load(args.catalog), constraints, args.exclude)

    state = State(args.state_dir)
    try:
        if args.command == "profile":
            return state.configure(load(args.file)) if args.file else state.profile()
        if args.command == "reminder":
            return state.reminder(args.meal, args.at, args.action, args.minutes)
        if args.command == "checkpoint":
            if args.action == "save":
                if not args.file:
                    raise ValueError("保存续写卡需要 --file")
                return state.save_checkpoint(load(args.file))
            return state.get("checkpoint", {"note": "还没有续写卡；可根据当前聊天生成"})
        if args.command == "render":
            catalog = load(args.catalog)
            context = catalog.get("context", {})
            orders = sorted([state.get(k) for k in state.keys("order:")], key=lambda o: o["updated_at"])[-3:]
            drafts = [state.get(k) for k in state.keys("draft:") if state.get(k).get("status") == "quoted"]
            drafts = [d for d in drafts if all(d["request"].get(k) == context[k]
                      for k in ("storeCode", "beType", "orderType", "beCode", "reservationDate") if k in context)]
            draft = max(drafts, key=lambda d: d["created_at"]) if drafts else None
            return {"path": save(args.out, render_panel(catalog, state.profile(), state.get("checkpoint"), orders, draft, inline=not args.standalone))}
        if args.command == "order":
            if args.action in ("quote", "draft"):
                request = load(args.request)
                quote_request = validate_request(request)
                response = client().call("calculate-price", quote_request) if args.action == "quote" else load(args.response)
                draft = make_draft(state, request, response, args.amount_field, args.summary,
                                   budget_cents=cents(args.budget) if args.budget else None)
                return {k: draft[k] for k in ("id", "summary", "total_cents", "expires_at", "status")}
            if args.action == "submit":
                return submit(state, client(), args.draft, args.confirm)
            if args.action == "authorize":
                return authorize(state, args.draft, args.confirm, load(args.response))
            if args.action == "complete":
                return complete(state, args.draft, load(args.response))
            if args.action == "uncertain":
                draft = state.get("draft:" + args.draft)
                if not draft or draft["status"] != "submitting":
                    raise ValueError("没有等待结果的提交")
                state.set("draft:" + args.draft, {**draft, "status": "uncertain"})
                return {"status": "uncertain", "retry": False}
            if args.action == "celebrate":
                return state.claim_celebration(args.order_id)
            if args.action == "poll-check":
                return state.poll_decision(args.order_id)
            if args.action == "poll-failed":
                return state.poll_failed(args.order_id)
            if args.action == "local":
                return [state.get(k) for k in state.keys("order:")]
            if args.action == "resolve":
                key = "draft:" + args.draft
                draft = state.get(key)
                if not draft or draft["status"] not in ("uncertain", "submitting", "checking"):
                    raise ValueError("只有结果不确定或被中断的提交需要恢复")
                if bool(args.order_id) == bool(args.confirmed_no_order):
                    raise ValueError("请选择已核实的订单号，或明确确认未创建订单")
                if args.response and not args.order_id:
                    raise ValueError("订单响应必须与 --order-id 一起提供")
                if args.order_id:
                    response = unwrap(load(args.response) if args.response else client().call("query-order", {"orderId": args.order_id}))
                    if str(response.get("orderId") or response.get("orderNo") or "") != args.order_id:
                        raise ValueError("查询响应未确认该订单号")
                    state.observe(args.order_id, response)
                    draft.update(status="submitted", order_id=args.order_id)
                else:
                    draft.update(status="resolved_no_order")
                state.set(key, draft)
                return {"status": draft["status"], "created": False}
            if args.action == "watch":
                decision = state.poll_decision(args.order_id)
                if not decision["poll"]:
                    return {**decision, "order": state.get("order:" + args.order_id)}
                try:
                    data = unwrap(client().call("query-order", {"orderId": args.order_id}))
                    return state.observe(args.order_id, data)
                except ValueError:
                    failure = state.poll_failed(args.order_id)
                    return {"error": "订单查询失败", **failure}
            else:
                data = unwrap(load(args.response))
            return state.observe(args.order_id, data)
    finally:
        state.db.close()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
