"""Offline workflow tests. All products, responses and orders are synthetic."""
import copy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/mcd-pause"
sys.path.insert(0, str(SKILL / "scripts"))

from mcp_client import Client, McpError, NoRedirect, unwrap
from mcpause import main, save
from nutrition import catalog_from_menu, cents, match_nutrition, nutrition_rows, recommend, totals
from orders import authorize, complete, make_draft, submit, validate_request
from state import State
from views import render_panel


NOW = datetime.fromisoformat("2026-10-14T11:40:00+08:00")


def result(data):
    return {"structuredContent": {"code": 200, "data": data}}


def quote(price=2250):
    return result({"price": price, "takeWayList": [{"code": "eat-in"}, {"code": "take-in-store"}]})


def request():
    return {"storeCode": "1000000", "beType": 1, "orderType": 1,
            "takeWayCode": "take-in-store", "items": [{"productCode": "1440", "quantity": 1}]}


class FakeClient:
    def __init__(self, price=2250, fail=False):
        self.price, self.fail, self.writes, self.reads = price, fail, [], []

    def call(self, name, arguments):
        self.reads.append((name, copy.deepcopy(arguments)))
        return quote(self.price)

    def create_order(self, arguments):
        self.writes.append(copy.deepcopy(arguments))
        if self.fail:
            raise TimeoutError("synthetic timeout after send")
        return result({"orderId": "TEST-ORDER", "payH5Url": "https://example.invalid/pay"})


class TemporaryState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name)
        self.addCleanup(self.state.db.close)

    def draft(self, body=None, at=NOW):
        return make_draft(self.state, body or request(), quote(), "price", "合成餐单 · 外带", at)


class NutritionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((SKILL / "assets/demo.json").read_text())

    def test_toon_and_numeric_unknowns(self):
        rows = nutrition_rows(result('[2]{productName,energyKcal,protein}:\n"鸡肉,小份",380,24\n新品,?,null'))
        self.assertEqual(rows[0]["productName"], "鸡肉,小份")
        self.assertEqual(rows[0]["protein"], 24)
        self.assertIsNone(rows[1]["energyKcal"])
        self.assertIsNone(rows[0]["sodium"])

    def test_malformed_toon_does_not_silently_drop_rows(self):
        for text in ("[2]{productName,protein}:\n鸡肉,24", "[1]{productName,protein}:\n鸡肉,24,3"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                nutrition_rows(result(text))

    def test_nutrition_accepts_native_text_only_response(self):
        response = {"content": [{"type": "text", "text": json.dumps({"code": 200,
                    "data": "[1]{productName,protein}:\n合成餐品,24"})}]}
        self.assertEqual(nutrition_rows(response)[0]["protein"], 24)

    def test_specifications_and_duplicate_names(self):
        rows = nutrition_rows([{"productName": "薯条（中份）", "energyKcal": 300},
                               {"productName": "红茶", "energyKcal": 1},
                               {"productName": "红茶", "energyKcal": 2}])
        self.assertEqual(match_nutrition("薯条 (中份)", rows)["status"], "exact")
        self.assertEqual(match_nutrition("薯条", rows)["status"], "unknown")
        self.assertEqual(match_nutrition("红茶", rows)["status"], "ambiguous")
        self.assertEqual(match_nutrition("中薯", rows, {"中薯": "薯条（中份）"})["status"], "alias")

    def test_money_units_and_invalid_amounts(self):
        self.assertEqual(cents("22.50"), 2250)
        self.assertEqual(cents(2250, "fen"), 2250)
        for value in (None, True, -1, "NaN", "1.001", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)
        with self.assertRaises(ValueError):
            cents(1, "unknown-unit")

    def test_quantities_and_unknown_propagation(self):
        food = copy.deepcopy(self.catalog["foods"][0])
        food["quantity"] = 2
        combined = totals([food, self.catalog["foods"][-1]])
        self.assertEqual(combined["reference_cents"], 5700)
        self.assertIsNone(combined["energyKcal"])
        self.assertEqual(totals([food])["protein"], 48)
        food["quantity"] = True
        with self.assertRaises(ValueError):
            totals([food])

    def test_recommendation_respects_every_bound(self):
        answer = recommend(self.catalog, {"budget_cents": 3000, "max_kcal": 650,
                                         "min_protein": "30", "max_sodium": 1200})
        self.assertTrue(answer["options"])
        identities = set()
        for option in answer["options"]:
            amount = option["totals"]
            self.assertLessEqual(amount["reference_cents"], 3000)
            self.assertLessEqual(amount["energyKcal"], 650)
            self.assertGreaterEqual(amount["protein"], 30)
            self.assertLessEqual(amount["sodium"], 1200)
            self.assertTrue(option["needs_quote"])
            identities.add(tuple(f["id"] for f in option["foods"]))
        self.assertEqual(len(identities), len(answer["options"]))
        self.assertFalse(recommend(self.catalog, {"budget_cents": 100})["options"])
        self.assertFalse(recommend(self.catalog, {"budget_cents": 3000, "min_protein": 30},
                                   ["鸡肉", "牛肉", "新品"])["options"])

    def test_invalid_targets_fail(self):
        for settings in ({"budget_cents": True}, {"budget_cents": -1},
                         {"budget_cents": 3000, "min_protein": float("nan")}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                recommend(self.catalog, settings)

    def test_unknown_nutrition_is_not_zero_for_upper_bound(self):
        self.catalog["foods"] = [self.catalog["foods"][-1]]
        self.assertFalse(recommend(self.catalog, {"budget_cents": 3000, "max_kcal": 900})["options"])

    def test_catalog_excludes_addons_and_unclassified_merchandise(self):
        menu = {"categories": [{"meals": [{"code": "2", "tags": ["套餐"]}]}], "meals": {
            "1": {"name": "鸡腿堡", "currentPrice": "22.50"},
            "2": {"name": "超值双堡", "currentPrice": "12.00"},
            "3": {"name": "会员卡", "currentPrice": "9.99", "canWithOrder": True},
            "4": {"name": "限量徽章", "currentPrice": "1"},
            "5": {"name": "零价薯条", "currentPrice": "0"}}}
        catalog = catalog_from_menu(result(menu), [], {"storeCode": "1000000"})
        kinds = {f["id"]: f["kind"] for f in catalog["foods"]}
        self.assertEqual(kinds, {"1": "main", "2": "combo", "4": "other", "5": "side"})
        options = recommend(catalog, {"budget_cents": 3000})["options"]
        self.assertEqual([[f["id"] for f in o["foods"]] for o in options], [["1"]])


class StateTests(TemporaryState):
    def enable_reminders(self):
        self.state.configure({"timezone": "Asia/Singapore", "reminders_enabled": True})

    def test_disabled_then_timezone_window_and_once_across_connections(self):
        self.assertEqual(self.state.reminder("lunch", NOW)["action"], "none")
        self.enable_reminders()
        self.assertEqual(self.state.reminder("lunch", "2026-10-14T03:39:00+00:00")["action"], "none")
        self.assertEqual(self.state.reminder("lunch", "2026-10-14T03:40:00+00:00")["action"], "recommend")
        other = State(self.tmp.name)
        self.addCleanup(other.db.close)
        self.assertEqual(other.reminder("lunch", NOW)["action"], "none")

    def test_snooze_and_done(self):
        self.enable_reminders()
        self.state.reminder("lunch", NOW)
        snooze = self.state.reminder("lunch", NOW, "snooze", 20)
        self.assertEqual(snooze["action"], "schedule_once")
        self.assertEqual(self.state.reminder("lunch", NOW + timedelta(minutes=19))["action"], "none")
        self.assertEqual(self.state.reminder("lunch", NOW + timedelta(minutes=20))["action"], "recommend")
        self.state.reminder("lunch", NOW, "done")
        self.assertEqual(self.state.reminder("lunch", NOW + timedelta(minutes=21))["action"], "none")

    def test_invalid_profile_does_not_replace_saved_preferences(self):
        before = self.state.profile()
        for changes in ({"constraints": {"budget_cents": True}},
                        {"constraints": {"budget_cents": 3000, "max_sodium": float("nan")}},
                        {"excluded_names": [""]}, {"lead_minutes": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.state.configure(changes)
            self.assertEqual(self.state.profile(), before)

    def test_weekends_quiet_hours_and_late_windows(self):
        self.enable_reminders()
        for at in ("2026-10-17T11:40:00+08:00", "2026-10-14T13:00:00+08:00"):
            self.assertEqual(self.state.reminder("lunch", at)["action"], "none")
        self.state.configure({"meals": {"lunch": "23:00"}, "quiet_end": "8:00"})
        self.assertEqual(self.state.profile()["quiet_end"], "08:00")
        self.assertEqual(self.state.reminder("lunch", "2026-10-14T22:40:00+08:00")["action"], "none")
        with self.assertRaises(ValueError):
            self.state.reminder("lunch", "2026-10-14T11:40:00")

    def test_resume_card_retains_workspace_and_thread_only(self):
        card = self.state.save_checkpoint({"task": "修复登录", "verified": "已重现", "next_step": "核对回调",
                                           "workspace": self.tmp.name, "thread_id": "test-thread", "irrelevant": "ignored"})
        self.assertEqual(card["workspace"], str(Path(self.tmp.name).resolve()))
        self.assertEqual(self.state.get("checkpoint")["thread_id"], "test-thread")
        self.assertNotIn("irrelevant", card)
        with self.assertRaises(ValueError):
            self.state.save_checkpoint({"task": "x", "verified": "x", "next_step": "x", "workspace": "relative"})

    def test_unknown_status_and_acceptance_do_not_imply_paid(self):
        self.state.configure({"celebrate_enabled": True})
        response = self.state.observe("A", {"orderStatus": 40, "payStatus": 1}, NOW)
        self.assertEqual(response["order"]["status"], "unknown")
        self.assertFalse(response["order"]["terminal"])
        self.assertFalse(self.state.claim_celebration("A", NOW)["fire"])
        self.state.observe("A", {"orderStatusName": "已接单"}, NOW)
        self.assertFalse(self.state.claim_celebration("A", NOW)["fire"])

    def test_celebration_requires_enabled_recent_payment_and_once(self):
        data = {"orderStatusName": "备餐中", "payStatusName": "已支付"}
        self.state.observe("A", data, NOW)
        self.assertFalse(self.state.claim_celebration("A", NOW)["fire"])
        self.state.configure({"celebrate_enabled": True})
        self.assertFalse(self.state.claim_celebration("A", NOW + timedelta(minutes=6))["fire"])
        self.assertFalse(self.state.claim_celebration("A", NOW - timedelta(seconds=1))["fire"])
        self.assertTrue(self.state.claim_celebration("A", NOW)["fire"])
        other = State(self.tmp.name)
        self.addCleanup(other.db.close)
        self.assertFalse(other.claim_celebration("A", NOW)["fire"])

    def test_observation_change_identity_and_no_fake_eta(self):
        data = {"orderId": "A", "orderStatusName": "已接单", "payStatusName": "已支付"}
        self.assertTrue(self.state.observe("A", data, NOW)["changed"])
        second = self.state.observe("A", data, NOW + timedelta(minutes=3))
        self.assertFalse(second["changed"])
        self.assertEqual(second["order"]["eta"], "")
        with self.assertRaises(ValueError):
            self.state.observe("B", data, NOW)

    def test_poll_interval_terminal_and_elapsed_limit(self):
        self.assertTrue(self.state.poll_decision("A", NOW)["poll"])
        self.state.observe("A", {"orderStatusName": "配送中"}, NOW)
        self.assertFalse(self.state.poll_decision("A", NOW + timedelta(seconds=179))["poll"])
        self.assertTrue(self.state.poll_decision("A", NOW + timedelta(minutes=3))["poll"])
        self.assertTrue(self.state.poll_decision("A", NOW + timedelta(hours=2))["stop"])
        self.state.observe("B", {"orderStatusName": "已完成"}, NOW)
        self.assertTrue(self.state.poll_decision("B", NOW)["stop"])

    def test_poll_attempt_and_error_limits(self):
        for i in range(30):
            self.state.observe("A", {"orderStatus": "unknown"}, NOW + timedelta(minutes=i * 3))
        self.assertTrue(self.state.poll_decision("A", NOW + timedelta(minutes=90))["stop"])
        for i in range(3):
            self.state.poll_failed("B", NOW + timedelta(minutes=i * 3))
        self.assertTrue(self.state.poll_decision("B", NOW + timedelta(minutes=9))["stop"])


class OrderTests(TemporaryState):
    def test_confirmation_expiry_and_demo_never_write(self):
        draft, client = self.draft(), FakeClient()
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], "different-id", NOW)
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], draft["id"], NOW + timedelta(minutes=6))
        body = request()
        body["items"][0]["productCode"] = "DEMO-01"
        with self.assertRaises(ValueError):
            self.draft(body)
        self.assertFalse(client.reads)
        self.assertFalse(client.writes)

    def test_submit_reprices_and_creates_once(self):
        draft, client = self.draft(), FakeClient()
        submitted = submit(self.state, client, draft["id"], draft["id"], NOW)
        self.assertTrue(submitted["created"])
        self.assertEqual(submitted["payment_status"], "unverified")
        self.assertNotIn("takeWayCode", client.reads[0][1])
        self.assertEqual(client.writes, [request()])
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], draft["id"], NOW)
        with self.assertRaises(ValueError):
            self.draft()
        self.assertEqual(len(client.writes), 1)

    def test_price_change_invalidates_confirmation(self):
        draft, client = self.draft(), FakeClient(price=2300)
        changed = submit(self.state, client, draft["id"], draft["id"], NOW)
        self.assertEqual(changed["reason"], "price_changed")
        self.assertFalse(client.writes)
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], draft["id"], NOW)

    def test_uncertain_write_blocks_retry_and_new_draft(self):
        draft, client = self.draft(), FakeClient(fail=True)
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], draft["id"], NOW)
        self.assertEqual(self.state.get("draft:" + draft["id"])["status"], "uncertain")
        with self.assertRaises(ValueError):
            self.draft()
        with self.assertRaises(ValueError):
            submit(self.state, client, draft["id"], draft["id"], NOW)
        self.assertEqual(len(client.writes), 1)

    def test_two_preexisting_drafts_share_one_submission_lease(self):
        first, second = self.draft(), self.draft()
        self.assertTrue(authorize(self.state, first["id"], first["id"], quote(), NOW)["authorized"])
        with self.assertRaises(ValueError):
            authorize(self.state, second["id"], second["id"], quote(), NOW)
        self.assertEqual(self.state.get("draft:" + second["id"])["status"], "duplicate_blocked")

    def test_native_mcp_complete_and_unknown_response(self):
        draft = self.draft()
        authorize(self.state, draft["id"], draft["id"], quote(), NOW)
        with self.assertRaises(ValueError):
            complete(self.state, draft["id"], result({"message": "unknown"}))
        self.assertEqual(self.state.get("draft:" + draft["id"])["status"], "uncertain")
        body = request()
        body["items"][0]["quantity"] = 2
        second = self.draft(body)
        authorize(self.state, second["id"], second["id"], quote(), NOW)
        self.assertTrue(complete(self.state, second["id"], result({"orderId": "TEST-SECOND"}))["created"])

    def test_takeway_removed_or_budget_exceeded(self):
        body = request()
        body["takeWayCode"] = "imagined-pickup"
        with self.assertRaises(ValueError):
            self.draft(body)
        with self.assertRaises(ValueError):
            make_draft(self.state, request(), quote(), "price", "合成", NOW, budget_cents=2000)
        draft = self.draft()
        with self.assertRaises(ValueError):
            authorize(self.state, draft["id"], draft["id"], result({"price": 2250, "takeWayList": []}), NOW)

    def test_delivery_parameters_and_quote_fields(self):
        body = request()
        body.update(beType=2, orderType=2, beCode="delivery-test", addressId="address-test", remark="合成备注")
        del body["takeWayCode"]
        self.assertNotIn("addressId", validate_request(body))
        self.assertNotIn("remark", validate_request(body))
        del body["addressId"]
        with self.assertRaises(ValueError):
            validate_request(body)

    def test_terminal_order_allows_fresh_explicit_purchase(self):
        first, client = self.draft(), FakeClient()
        submit(self.state, client, first["id"], first["id"], NOW)
        self.state.observe("TEST-ORDER", {"orderStatusName": "已取消"}, NOW)
        second = self.draft()
        self.assertTrue(authorize(self.state, second["id"], second["id"], quote(), NOW)["authorized"])

    def test_native_recovery_requires_matching_order_id(self):
        draft = self.draft()
        authorize(self.state, draft["id"], draft["id"], quote(), NOW)
        response = Path(self.tmp.name) / "order.json"
        response.write_text(json.dumps(result({"orderId": "TEST-FOUND", "orderStatusName": "已接单"})))
        args = ["--state-dir", self.tmp.name, "order", "resolve", "--draft", draft["id"], "--response", str(response)]
        with patch("mcpause.Client", side_effect=AssertionError("must not connect")):
            with self.assertRaises(ValueError):
                main(args + ["--order-id", "TEST-WRONG"])
            recovered = main(args + ["--order-id", "TEST-FOUND"])
        self.assertFalse(recovered["created"])
        self.assertEqual(self.state.get("draft:" + draft["id"])["order_id"], "TEST-FOUND")


class IntegrationTests(unittest.TestCase):
    def test_mcp_wrapper_and_text_fallback_reject_errors(self):
        self.assertEqual(unwrap({"result": result({"ok": 1})}), {"ok": 1})
        self.assertEqual(unwrap({"content": [{"type": "text", "text": '## Original Response\n```json\n{"code":200,"data":[1]}\n```'}]}), [1])
        for response in ({"isError": True}, {"code": 500, "data": {}}, {"result": None}, {"structuredContent": None}):
            with self.subTest(response=response), self.assertRaises(McpError):
                unwrap(response)

    def test_generic_client_refuses_writes_and_redirects(self):
        client = Client.__new__(Client)
        with patch.object(client, "rpc") as rpc:
            for name in ("create-order", "cancel-order", "coupon-list", "receive-coupon"):
                with self.assertRaises(McpError):
                    client.call(name, {})
            rpc.assert_not_called()
        with self.assertRaises(McpError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")

    def test_demo_cli_does_not_connect_or_create_state(self):
        with tempfile.TemporaryDirectory() as folder, patch("mcpause.Client", side_effect=AssertionError("network")):
            state_dir, out = Path(folder) / "state", Path(folder) / "demo.html"
            answer = main(["--state-dir", str(state_dir), "demo", "--out", str(out)])
            self.assertEqual(answer["mode"], "demo")
            self.assertTrue(out.read_text().startswith("<!doctype html>"))
            self.assertFalse(state_dir.exists())

    def test_panel_does_not_execute_product_names_or_leak_draft_request(self):
        catalog = {"mode": "live", "context": {}, "foods": [{"id": "1", "name": "</script><script>attack()</script>"}]}
        draft = {"id": "d", "summary": "合成", "total_cents": 100, "expires_at": NOW.isoformat(),
                 "status": "quoted", "request": {"addressId": "private-address-test"}}
        panel = render_panel(catalog, draft=draft)
        self.assertNotIn("</script><script>attack", panel)
        self.assertNotIn("private-address-test", panel)
        self.assertIn("\\u003c/script\\u003e", panel)

    def test_panel_respects_saved_food_exclusions(self):
        catalog = {"mode": "live", "context": {}, "foods": [
            {"id": "fish", "name": "合成鱼堡"}, {"id": "chicken", "name": "合成鸡肉堡"}]}
        panel = render_panel(catalog, {"excluded_names": ["鱼"]})
        self.assertNotIn("合成鱼堡", panel)
        self.assertIn("合成鸡肉堡", panel)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_saved_results_are_private_even_if_file_existed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.touch(mode=0o644)
            save(path, {"synthetic": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_installer_preserves_differing_copy_then_backs_up(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "skills"
            cmd = [sys.executable, str(ROOT / "scripts/install_skill.py"), "--dest", str(dest)]
            subprocess.run(cmd, check=True, capture_output=True)
            original = (dest / "mcd-pause/SKILL.md").read_text()
            second = subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.assertIn("相同版本", second.stdout)
            (dest / "mcd-pause/SKILL.md").write_text("local edits")
            blocked = subprocess.run(cmd, capture_output=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual((dest / "mcd-pause/SKILL.md").read_text(), "local edits")
            subprocess.run(cmd + ["--backup-and-replace"], check=True, capture_output=True)
            self.assertEqual((dest / "mcd-pause/SKILL.md").read_text(), original)
            backups = list((Path(folder) / "mcpause-skill-backups").glob("*/SKILL.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "local edits")


if __name__ == "__main__":
    unittest.main()
