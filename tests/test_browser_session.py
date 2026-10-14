"""Synthetic MCP and loopback requests only; no real ordering or credentials."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

SKILL = Path(__file__).resolve().parents[1] / '.agents/skills/mcd-pause'
sys.path.insert(0, str(SKILL / 'scripts'))
from browser_session import BrowserSession, validate_item
from nutrition import catalog_from_menu
from serve_view import make_server
from shopping import meal_detail


def response(data):
    return {'structuredContent': {'code': 200, 'data': data}}


MENU = response({'categories': [{'name': '汉堡', 'meals': [{'code': '10'}]},
                                {'name': '饮料', 'meals': [{'code': '20'}]}],
                 'meals': {'10': {'name': '合成四件套', 'currentPrice': '36.9'},
                           '20': {'name': '合成可乐', 'currentPrice': '10'},
                           '30': {'name': '合成随单餐', 'currentPrice': '30', 'canWithOrder': True,
                                  'withOrder': {'membershipCode': '106', 'specId': '880'}}}})
DETAIL = response({'code': '10', 'modification': {'items': [{'minValues': 1, 'maxValues': 1, 'values': [
    {'code': 'ice', 'name': '标准', 'selectedKey': 'normal', 'selectedQuantity': 1, 'maxQuantity': 1, 'price': 0},
    {'code': 'ice', 'name': '少冰', 'selectedKey': 'less', 'selectedQuantity': 0, 'maxQuantity': 1, 'price': 0}]}]}})
ITEM = {'productCode': '10', 'quantity': 1, 'modification': {'values': [{'code': 'ice', 'key': 'less', 'quantity': 1}]}}


class FakeClient:
    def __init__(self):
        self.calls, self.created = [], 0
        self.menu, self.card_fee, self.created_requests = MENU, 990, []

    def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == 'query-meals':
            return self.menu
        if name == 'query-meal-detail':
            if arguments['code'] == '10':
                return DETAIL
            return response({'code': arguments['code']})
        if name == 'calculate-price':
            with_card = bool(arguments.get('withOrder'))
            lines = [{'productCode': '10', 'productName': '合成四件套', 'quantity': 1,
                      'subtotal': 4680 - self.card_fee if with_card else 3690}]
            if with_card:
                lines.append({'productCode': 'CARD', 'productName': '合成体验卡', 'quantity': 1, 'subtotal': self.card_fee})
            return response({'price': 4680 if with_card else 4480, 'productPrice': 4680 if with_card else 3690,
                             'deliveryPrice': 0 if with_card else 600, 'packingPrice': 0 if with_card else 190,
                             'productList': lines})
        if name == 'query-order':
            return response({'orderId': arguments['orderId'], 'orderStatus': '4',
                             'deliveryInfo': {'expectDeliveryTime': '合成时间', 'mobilePhone': 'PRIVATE_PHONE'}})
        if name == 'order-list':
            return response({'list': []})
        raise AssertionError(name)

    def create_order(self, request):
        self.created += 1
        self.created_requests.append(copy.deepcopy(request))
        return response({'orderId': 'SYNTHETIC-ORDER', 'payH5Url': 'https://m.mcd.cn/synthetic-payment'})


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeClient()
        context = {'storeCode': '1000000', 'beCode': '100000002', 'beType': 2, 'orderType': 2,
                   'label': '合成门店', 'delivery_label': '合成地址'}
        self.config = {'catalog': catalog_from_menu(MENU, [], context), 'details': {'10': DETAIL},
                       'keyword': '四件套', 'selected': '10', 'celebrate_delivery': True,
                       'checkout': {'addressId': 'PRIVATE_ADDRESS'}}
        self.session = BrowserSession(self.client, self.config, self.tmp.name)
        self.addCleanup(self.session.state.db.close)

    def test_compare_preserves_less_ice_and_does_not_purchase(self):
        result = self.session.handle('compare', {'items': [ITEM]})
        report = result['comparison']
        self.assertEqual(report['net_saving_cents'], -200)
        self.assertEqual(report['break_even']['orders'], 2)
        requests = [r for name, r in self.client.calls if name == 'calculate-price']
        self.assertEqual(requests[0]['items'], [ITEM])
        self.assertEqual(requests[0], {k: v for k, v in requests[1].items() if k != 'withOrder'})
        self.assertEqual(self.client.created, 0)
        self.assertTrue((Path(self.tmp.name) / 'card-evidence.json').exists())

    def test_page_full_menu_and_no_account_data(self):
        page = self.session.page('BROWSER_NONCE')
        for value in ('合成四件套', '合成可乐', '合成随单餐'):
            self.assertIn(value, page)
        self.assertNotIn('PRIVATE_ADDRESS', page)
        self.assertIn('BROWSER_NONCE', page)
        self.assertNotIn('__MCPAUSE_', page)

    def test_selected_meal_handoff_preserves_modifications_and_is_read_only(self):
        self.config['selection'] = {'context': self.config['catalog']['context'], 'items': [ITEM]}
        cart = self.session.selected_cart()
        self.assertEqual(cart[0]['item'], ITEM)
        self.assertIn('少冰', cart[0]['summary'][0])
        self.session.page('NONCE')
        self.session.page('NONCE')
        self.assertEqual([name for name, _ in self.client.calls], ['query-meal-detail'])
        self.assertEqual(self.client.created, 0)

    def test_handoff_rejects_wrong_context_or_unverified_choices(self):
        self.config['selection'] = {'context': {**self.config['catalog']['context'], 'storeCode': '9999999'}, 'items': [ITEM]}
        with self.assertRaises(ValueError):
            self.session.selected_cart()
        self.assertEqual(self.client.calls, [])
        self.config['selection']['context'] = self.config['catalog']['context']
        self.config['selection']['items'] = [{'productCode': '10', 'quantity': 1}]
        with self.assertRaises(ValueError):
            self.session.selected_cart()
        self.assertEqual(self.client.created, 0)

    def test_invalid_modifications_stop_before_quote(self):
        bad = copy.deepcopy(ITEM)
        bad['modification']['values'][0]['key'] = 'unverified'
        with self.assertRaises(ValueError):
            self.session.handle('quote', {'items': [bad]})
        self.assertNotIn('calculate-price', [name for name, _ in self.client.calls])
        self.assertEqual(self.client.created, 0)

    def test_multi_choice_round_checks_actual_quantity(self):
        detail = meal_detail(response({'code': '10', 'rounds': [{'id': 1, 'minQuantity': 2, 'maxQuantity': 3,
                     'choices': [{'code': 'a', 'name': 'A', 'maxQuantity': 3}, {'code': 'b', 'name': 'B', 'maxQuantity': 1}]}]}), '10')
        item = {'productCode': '10', 'quantity': 1, 'roundList': [{'round': '1', 'comboItemList': [{'code': 'a', 'quantity': 2}]}]}
        validate_item(item, detail)
        item['roundList'][0]['comboItemList'][0]['quantity'] = 4
        with self.assertRaises(ValueError):
            validate_item(item, detail)

    def test_read_only_and_explicit_confirmation_gate(self):
        with self.assertRaises(ValueError):
            self.session.handle('draft', {'items': [ITEM]})
        self.session.allow_orders = True
        draft = self.session.handle('draft', {'items': [ITEM]})
        self.assertIn('少冰', draft['summary'])
        with self.assertRaises(ValueError):
            self.session.handle('submit', {'draft_id': draft['id']})
        self.assertEqual(self.client.created, 0)
        result = self.session.handle('submit', {'draft_id': draft['id'], 'confirmed': True})
        self.assertTrue(result['created'])
        with self.assertRaises(ValueError):
            self.session.handle('submit', {'draft_id': draft['id'], 'confirmed': True})
        self.assertEqual(self.client.created, 1)

    def test_order_refresh_is_rate_limited_and_private(self):
        first = self.session.handle('order', {'order_id': 'SYNTHETIC-ORDER'})
        self.assertEqual(first['order']['status'], 'delivering')
        self.assertNotIn('PRIVATE_PHONE', json.dumps(first))
        second = self.session.handle('order', {'order_id': 'SYNTHETIC-ORDER'})
        self.assertTrue(second['cached'])
        self.assertEqual([n for n, _ in self.client.calls].count('query-order'), 1)

    def test_card_requires_the_matching_comparison_and_explicit_choice(self):
        self.session.allow_orders = True
        result = self.session.handle('compare', {'items': [ITEM]})
        comparison_id = result['comparison_id']
        # A detail-page comparison must not overwrite the cart's verified option.
        changed = {**copy.deepcopy(ITEM), 'quantity': 2}
        self.session.handle('compare', {'items': [changed]})
        for payload in ({'items': [ITEM]}, {'items': [changed], 'comparison_id': comparison_id}):
            with self.assertRaises(ValueError):
                self.session.handle('draft', {**payload, 'buy_card': True})
        draft = self.session.handle('draft', {'items': [ITEM], 'buy_card': True, 'comparison_id': comparison_id})
        self.assertIn('本次卡费 ¥9.90', draft['summary'])
        self.assertIn('配送费 ¥0.00', draft['summary'])
        self.assertEqual(draft['checkout']['card']['fee'], 990)
        self.assertEqual(draft['checkout']['lines'][0]['modifications'], ['少冰'])
        self.assertEqual(draft['checkout']['store'], '合成门店')
        self.assertNotIn('PRIVATE_ADDRESS', json.dumps(draft['checkout']))
        self.assertEqual(self.client.created, 0)
        result = self.session.handle('submit', {'draft_id': draft['id'], 'confirmed': True})
        self.assertEqual(self.client.created_requests[0]['withOrder'], {'membershipCode': '106', 'membershipSpecId': '880'})
        self.assertEqual(self.client.created_requests[0]['items'], [ITEM])
        self.assertEqual(self.session.handle('info', {})['last_order']['payment_url'], result['payment_url'])
        self.assertNotIn('PRIVATE_ADDRESS', json.dumps(self.session.handle('info', {})))

    def test_comparison_never_automatically_adds_card_to_draft(self):
        self.session.allow_orders = True
        self.session.handle('compare', {'items': [ITEM]})
        draft = self.session.handle('draft', {'items': [ITEM]})
        self.assertEqual(draft['total_cents'], 4480)
        self.assertIn('本次不开卡', draft['summary'])
        self.assertNotIn('withOrder', self.session.state.get('draft:' + draft['id'])['request'])

    def test_card_fee_change_with_same_total_requires_reconfirmation(self):
        self.session.allow_orders = True
        result = self.session.handle('compare', {'items': [ITEM]})
        payload = {'items': [ITEM], 'buy_card': True, 'comparison_id': result['comparison_id']}
        draft = self.session.handle('draft', payload)
        self.client.card_fee += 100
        with self.assertRaisesRegex(ValueError, '卡种或卡费已变化'):
            self.session.handle('draft', payload)
        result = self.session.handle('submit', {'draft_id': draft['id'], 'confirmed': True})
        self.assertEqual(result['reason'], 'card_changed')
        self.assertEqual(self.client.created, 0)

    def test_no_card_option_still_returns_payable_baseline(self):
        self.client.menu = response({'meals': {}})
        result = self.session.handle('compare', {'items': [ITEM]})
        self.assertIsNone(result['comparison'])
        self.assertEqual(result['quote']['price'], 4480)
        self.assertEqual(result['option_count'], 0)
        self.assertEqual(self.client.created, 0)

    def test_expired_card_comparison_cannot_be_used(self):
        self.session.allow_orders = True
        result = self.session.handle('compare', {'items': [ITEM]})
        self.session.comparisons[result['comparison_id']]['comparison']['quoted_at'] = '2000-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            self.session.handle('draft', {'items': [ITEM], 'buy_card': True, 'comparison_id': result['comparison_id']})
        self.assertEqual(self.client.created, 0)

    def test_loopback_refuses_other_origins_paths_and_missing_nonce(self):
        class Stub:
            def page(self, token):
                return token
            def handle(self, action, payload):
                return {'ok': action == 'info'}
        server = make_server(session=Stub())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        with urllib.request.urlopen(origin) as r:
            token = r.read().decode()
        def post(origin_header=origin, nonce=token):
            return urllib.request.urlopen(urllib.request.Request(origin + '/api/info', data=b'{}',
                headers={'Origin': origin_header, 'X-McPause-Session': nonce, 'Content-Type': 'application/json'}))
        with post() as r:
            self.assertTrue(json.load(r)['ok'])
        for args in ({'origin_header': 'https://unrelated.example'}, {'nonce': ''}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                post(**args)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(origin + '/private.json')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
