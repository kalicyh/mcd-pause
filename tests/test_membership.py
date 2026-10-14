"""Synthetic card quotes; no paid cards, real accounts or network calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / '.agents/skills/mcd-pause'
sys.path.insert(0, str(SKILL / 'scripts'))
from mcpause import main
from membership import break_even, comparison_for_view, with_order_options
from shopping import render_shop


def response(data):
    return {'structuredContent': {'code': 200, 'data': data}, 'private': 'ACCOUNT_SECRET'}


def evidence():
    base = {'storeCode': '1000000', 'beType': 1, 'orderType': 1,
            'items': [{'productCode': 'meal', 'quantity': 1, 'couponId': 'PRIVATE_COUPON'}]}
    return {'baseline_request': base,
            'card_request': {**copy.deepcopy(base), 'withOrder': {'membershipCode': 'member', 'membershipSpecId': 'spec'}},
            'baseline_response': response({'productPrice': 3690, 'deliveryPrice': 600, 'packingPrice': 190,
                                           'price': 4480, 'discount': 0,
                                           'productList': [{'productCode': 'meal', 'subtotal': 3690}]}),
            'card_response': response({'productPrice': 4680, 'deliveryPrice': 0, 'packingPrice': 0,
                                      'price': 4680, 'discount': 790,
                                      'productList': [{'productCode': 'meal', 'subtotal': 3690},
                                                      {'productCode': 'card', 'productName': '合成测试卡（15天）',
                                                       'quantity': 1, 'subtotal': 990}]}),
            'card_product_code': 'card', 'quoted_at': '2026-10-14T07:16:40+00:00'}


class MembershipTests(unittest.TestCase):
    def test_menu_maps_spec_id_without_guessing_card_fee(self):
        menu = response({'meals': {
            'a': {'name': '合成随单购套餐', 'canWithOrder': True, 'currentPrice': '29.9',
                  'withOrder': {'membershipCode': 'member', 'specId': 'spec', 'token': 'PRIVATE'}},
            'b': {'name': '同卡另一餐', 'canWithOrder': True,
                  'withOrder': {'membershipCode': 'member', 'specId': 'spec'}},
            'c': {'name': '普通餐', 'canWithOrder': False},
            'd': {'name': '缺少规格', 'canWithOrder': True, 'withOrder': {'membershipCode': 'member'}}}})
        options = with_order_options(menu)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['withOrder'], {'membershipCode': 'member', 'membershipSpecId': 'spec'})
        self.assertEqual([m['code'] for m in options[0]['meals']], ['a', 'b'])
        self.assertNotIn('29.9', json.dumps(options))
        self.assertNotIn('PRIVATE', json.dumps(options))

    def test_card_fee_is_included_once_and_savings_use_actual_baseline(self):
        result = comparison_for_view(evidence())
        self.assertEqual(result['with_card']['price'], 4680)
        self.assertEqual(result['with_card']['foodPrice'], 3690)
        self.assertEqual(result['card']['fee_cents'], 990)
        self.assertEqual(result['savings']['deliveryPrice'], 600)
        self.assertEqual(result['savings']['packingPrice'], 190)
        self.assertEqual(result['gross_saving_cents'], 790)
        self.assertEqual(result['net_saving_cents'], -200)
        self.assertEqual(result['break_even'], {'orders': 2, 'reason': 'assumption'})
        self.assertIsNone(result['savings']['tablewarePrice'])
        self.assertNotIn('PRIVATE_COUPON', json.dumps(result))
        self.assertNotIn('ACCOUNT_SECRET', json.dumps(result))

    def test_only_with_order_may_change_between_quotes(self):
        for change in ({'storeCode': '2000000'}, {'items': [{'productCode': 'other', 'quantity': 1}]},
                       {'needTableware': True}, {'withOrder': None}):
            data = evidence()
            data['card_request'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                comparison_for_view(data)
        data = evidence()
        data['baseline_request']['addressId'] = 'PRIVATE_ADDRESS'
        data['card_request']['addressId'] = 'PRIVATE_ADDRESS'
        with self.assertRaises(ValueError):
            comparison_for_view(data)

    def test_missing_or_unreconciled_card_fee_is_not_treated_as_free(self):
        for field, value in (('subtotal', None), ('subtotal', True), ('subtotal', '990'),
                             ('subtotal', -1), ('quantity', 2)):
            data = evidence()
            data['card_response']['structuredContent']['data']['productList'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                comparison_for_view(data)
        for field, value in (('price', 3690), ('productPrice', 3690)):
            data = evidence()
            data['card_response']['structuredContent']['data'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                comparison_for_view(data)

    def test_one_time_saving_is_not_repeated_automatically(self):
        self.assertEqual(break_even(990, 790, 100)['orders'], 3)
        self.assertEqual(break_even(990, 790, 0)['reason'], 'no_recurring_saving')
        self.assertEqual(break_even(990, 790, 100, remaining_uses=1)['reason'], 'usage_limit')
        self.assertEqual(break_even(990, 1000, 0)['orders'], 1)
        self.assertEqual(break_even(990, -100, 500)['orders'], 4)
        for args in ((990, 790, True), (990, 790, -1), (990, 790, 100, -1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                break_even(*args)

    def test_cli_and_render_recompute_evidence_without_credentials_or_user_state(self):
        data = evidence()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'evidence.json'
            source.write_text(json.dumps(data))
            with patch('mcpause.Client', side_effect=AssertionError('must not connect')), \
                 patch('mcpause.State', side_effect=AssertionError('must not create account state')):
                result = main(['compare-card', '--evidence', str(source)])
            self.assertEqual(result['net_saving_cents'], -200)
        catalog = {'mode': 'live', 'context': {'storeCode': '1000000', 'beType': 1, 'orderType': 1},
                   'foods': [{'id': 'meal', 'name': '合成测试餐', 'kind': 'combo', 'reference_cents': 3690}]}
        data['card_response']['structuredContent']['data']['productList'][1]['productName'] = '</script><script>bad()</script>'
        html = render_shop(catalog, {}, card_comparison=data)
        self.assertNotIn('</script><script>bad()', html)
        self.assertNotIn('PRIVATE_COUPON', html)
        self.assertNotIn('ACCOUNT_SECRET', html)
        self.assertIn('"net_saving_cents": -200', html)


if __name__ == '__main__':
    unittest.main()
