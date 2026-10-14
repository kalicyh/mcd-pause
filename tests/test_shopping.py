"""Synthetic menu data only; no MCP requests or account state."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / '.agents/skills/mcd-pause'
sys.path.insert(0, str(SKILL / 'scripts'))
from mcpause import main
from nutrition import catalog_from_menu
from shopping import difference, official_image, render_shop, shopping_data


def response(data):
    return {'structuredContent': {'code': 200, 'data': data}}


def catalog():
    return {'mode': 'live', 'context': {'storeCode': '1000000', 'orderType': 2,
            'phone': 'PRIVATE_PHONE', 'token': 'PRIVATE_TOKEN', 'addressId': 'PRIVATE_ADDRESS'},
            'foods': [
                {'id': '1', 'name': '合成经典四件套', 'kind': 'combo', 'reference_cents': 1900, 'tags': ['热卖']},
                {'id': '2', 'name': '合成新品四件套', 'kind': 'combo', 'reference_cents': 3900, 'tags': ['上新']},
                {'id': '3', 'name': '合成活动四件套', 'kind': 'combo', 'reference_cents': 2900, 'tags': []},
                {'id': '4', 'name': '合成薯条', 'kind': 'side', 'reference_cents': 1000, 'tags': ['上新']}]}


class ShoppingTests(unittest.TestCase):
    def test_wanted_food_is_prioritized_without_hiding_full_menu(self):
        news = {'3': {'source': 'campaign-calendar', 'evidence': '合成测试活动明确介绍新品'}}
        data = shopping_data(catalog(), {}, news=news, keyword='四件套', selected='1')
        self.assertEqual([c['code'] for c in data['cards']], ['4', '3', '2', '1'])
        self.assertEqual(data['priority_codes'], ['3', '2', '1'])
        self.assertIn('4', data['categories'][0]['codes'])
        self.assertEqual(data['selected'], '1')
        self.assertFalse(data['cards'][-1]['new'])
        unsupported = shopping_data(catalog(), {}, news={'1': {'source': 'guess', 'evidence': '编号更大'}})
        self.assertFalse(next(c for c in unsupported['cards'] if c['code'] == '1')['new'])

    def test_null_tags_do_not_break_real_catalogs(self):
        data = response({'categories': [{'meals': [{'code': '1', 'tags': None}]},
                                       {'meals': [{'code': '1', 'tags': ['上新']}]}],
                         'meals': {'1': {'name': '合成汉堡', 'currentPrice': '20'}}})
        self.assertEqual(catalog_from_menu(data, [], {})['foods'][0]['tags'], ['上新'])

    def test_official_images_survive_catalog_and_match_exact_choice_codes(self):
        meal_image='https://menu-img.mcd.cn/pcm/test/combo.png'
        hero_image='https://menu-img.mcd.cn/pcm/test/combo-600.png'
        drink_image='https://menu-img.mcd.cn/pcm/test/drink.png'
        raw=response({'meals': {'1': {'name': '合成四件套', 'currentPrice': '20', 'image': meal_image},
                               'drink': {'name': '合成可乐', 'currentPrice': '5', 'image': drink_image}}})
        source=catalog_from_menu(raw, [], {})
        detail=response({'code': '1', 'image': hero_image, 'rounds': [{'id': 4, 'minQuantity': 1, 'maxQuantity': 1,
                         'choices': [{'code': 'drink', 'name': '合成可乐中杯', 'isDefault': 1},
                                     {'code': 'large', 'name': '合成可乐大杯'}]}]})
        card=next(c for c in shopping_data(source, {'1': detail}, keyword='四件套')['cards'] if c['code']=='1')
        self.assertEqual(card['image'], meal_image)
        self.assertEqual(card['detail']['image'], hero_image)
        self.assertEqual(card['detail']['rounds'][0]['choices'][0]['image'], drink_image)
        self.assertIsNone(card['detail']['rounds'][0]['choices'][1]['image'])
        for url in ('https://menu-img.mcd.cn.evil.test/pcm/a.png', 'http://menu-img.mcd.cn/pcm/a.png',
                    'https://user:secret@menu-img.mcd.cn/pcm/a.png', 'https://menu-img.mcd.cn/pcm/a.png?token=secret',
                    'file:///private/image.png', 'data:image/png;base64,AA=='):
            self.assertIsNone(official_image(url))

    def test_image_loading_is_explicitly_limited_to_browser_page(self):
        inline=render_shop(catalog(), {})
        page=render_shop(catalog(), {}, standalone=True)
        self.assertIn('"images_enabled": false', inline)
        self.assertNotIn('<!doctype html>', inline)
        self.assertIn('"images_enabled": true', page)
        self.assertIn("img-src https://menu-img.mcd.cn", page)
        self.assertIn('<!doctype html>', page)
        self.assertNotIn('__MCPAUSE_SHOP_FRAGMENT__', page)

    def test_detail_identity_modification_keys_and_unknown_prices(self):
        detail = response({'code': '1', 'rounds': [{'id': 4, 'minQuantity': 1, 'maxQuantity': 1,
          'choices': [{'code': 'drink', 'name': '合成可乐', 'isDefault': 1, 'quantity': 1,
            'diffPrice': '+ ¥0', 'modification': {'items': [{'minValues': 1, 'maxValues': 1,
              'values': [{'code': 'ice', 'name': '少冰', 'selectedKey': '1-1', 'selectedQuantity': 1,
                          'maxQuantity': 1, 'price': 0}]}]}}]}]})
        card = next(c for c in shopping_data(catalog(), {'1': detail})['cards'] if c['code'] == '1')
        self.assertEqual(card['detail']['rounds'][0]['choices'][0]['modifications'][0]['values'][0]['selectedKey'], '1-1')
        with self.assertRaises(ValueError):
            shopping_data(catalog(), {'2': detail})
        self.assertIsNone(difference('待定'))
        self.assertEqual(difference('+ ¥1.5'), 150)
        self.assertEqual(difference('- ¥2'), -200)

    def test_html_escape_and_private_account_fields_are_not_embedded(self):
        source = catalog()
        source['foods'][0]['name'] = '</script><script>bad()</script>'
        html = render_shop(source, {})
        self.assertNotIn('</script><script>bad()', html)
        for private in ('PRIVATE_PHONE', 'PRIVATE_TOKEN', 'PRIVATE_ADDRESS'):
            self.assertNotIn(private, html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_render_cli_needs_no_credentials_or_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'catalog.json').write_text(json.dumps(catalog()))
            (root/'details.json').write_text('{}')
            with patch('mcpause.State', side_effect=AssertionError('must not create account state')):
                result = main(['render-shop', '--catalog', str(root/'catalog.json'), '--details', str(root/'details.json'), '--out', str(root/'view.html')])
            self.assertTrue(Path(result['path']).exists())


if __name__ == '__main__':
    unittest.main()
