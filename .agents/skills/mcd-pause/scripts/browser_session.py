"""A temporary loopback session for the Skill, not an installed MCP server."""
import copy
import json
import re
import secrets
from datetime import timedelta
from pathlib import Path

from mcp_client import unwrap
from membership import comparison_for_view, with_order_options
from orders import card_terms, make_draft, submit
from shopping import meal_detail, official_image, render_shop
from state import State, aware, now_utc


def private_save(path, value):
    from mcpause import save
    return save(path, value)


def check_modifications(groups, payload):
    values = payload.get('values', []) if isinstance(payload, dict) else []
    expected, used = [], set()
    for group in groups:
        selected = 0
        for option in group['values']:
            allowed = {option.get('selectedKey'), option.get('unselectedKey')} - {None, ''}
            matches = [(i, v) for i, v in enumerate(values)
                       if v.get('code') == option['code'] and v.get('key') in allowed]
            # Ice alternatives can share a code; the actual selectedKey identifies the option.
            matches = [(i, v) for i, v in matches if i not in used]
            if not matches:
                if option.get('unselectedKey'):
                    raise ValueError('配料选项不完整，请重新调整')
                continue
            if len(matches) != 1:
                raise ValueError('重复的配料选项')
            i, value = matches[0]
            q = value.get('quantity')
            maximum = option.get('maxQuantity', 20)
            maximum = 20 if maximum < 0 else maximum
            if type(q) is not int or q < 0 or q > maximum:
                raise ValueError('配料数量不符合详情')
            if q > 0:
                if value['key'] != option.get('selectedKey') or q < option.get('minQuantity', 0):
                    raise ValueError('配料选项不符合详情')
                selected += 1
            elif value['key'] != option.get('unselectedKey'):
                raise ValueError('未选择的配料 key 不正确')
            used.add(i)
            expected.append(value)
        if not group['min'] <= selected <= group['max']:
            raise ValueError('请完成必选配料或冰量')
    if len(used) != len(values):
        raise ValueError('存在未知的配料选项')


def validate_item(item, detail):
    if set(item) - {'productCode', 'quantity', 'roundList', 'modification'}:
        raise ValueError('餐品包含未知字段')
    if type(item.get('quantity')) is not int or not 1 <= item['quantity'] <= 20:
        raise ValueError('每项餐品需要 1–20 份')
    incoming = item.get('roundList', [])
    if not isinstance(incoming, list) or len(incoming) != len(detail['rounds']):
        raise ValueError('套餐轮次不完整')
    if len({r.get('round') for r in incoming}) != len(incoming):
        raise ValueError('套餐轮次重复')
    for group in detail['rounds']:
        value = next((r for r in incoming if r.get('round') == group['id']), None)
        if not value or set(value) != {'round', 'comboItemList'}:
            raise ValueError('套餐轮次不匹配')
        choices, seen, quantity = value['comboItemList'], set(), 0
        for entry in choices:
            if set(entry) - {'code', 'quantity', 'modification'} or entry.get('code') in seen:
                raise ValueError('套餐子项重复或字段无效')
            choice = next((c for c in group['choices'] if c['code'] == entry.get('code')), None)
            q = entry.get('quantity')
            if not choice or type(q) is not int or q < 1 or (choice['max'] >= 0 and q > choice['max']):
                raise ValueError('套餐子项或数量不符合当前详情')
            quantity += q
            seen.add(entry['code'])
            check_modifications(choice['modifications'], entry.get('modification'))
        if not group['min'] <= quantity <= group['max']:
            raise ValueError('套餐份数不符合要求')
    check_modifications(detail['modifications'], item.get('modification'))


class BrowserSession:
    def __init__(self, client, config, directory, allow_orders=False):
        self.client, self.config = client, config
        self.directory = Path(directory)
        self.catalog = config['catalog']
        self.details = config.get('details', {})
        self.context = {k: v for k, v in self.catalog['context'].items()
                        if k in ('storeCode', 'beCode', 'beType', 'orderType', 'reservationDate')}
        self.foods = {f['id']: f for f in self.catalog['foods']}
        self.images = {code: official_image(f.get('image')) for code, f in self.foods.items()}
        self.state = State(self.directory / 'state')
        if config.get('celebrate_delivery') is True:
            self.state.configure({'celebrate_enabled': True})
        self.allow_orders = allow_orders
        checkout = config.get('checkout', {})
        if allow_orders and not checkout.get('addressId' if self.context['orderType'] == 2 else 'takeWayCode'):
            raise ValueError('请先核实当前门店的配送地址或取餐方式，再开启订单确认')
        self.draft_ids, self.order_types = set(), {}
        self.comparisons = {}

    def page(self, token):
        return render_shop(self.catalog, self.details, standalone=True, browser_token=token,
                           keyword=self.config.get('keyword'), selected=self.config.get('selected'),
                           news=self.config.get('news'), quote=self.config.get('quote'),
                           card_comparison=self.config.get('card_comparison'))

    def detail(self, code):
        if code not in self.foods:
            raise ValueError('餐品不在当前门店菜单中')
        raw = self.client.call('query-meal-detail', {**self.context, 'code': code})
        detail = meal_detail(raw, code, self.images)
        self.details[code] = raw
        return detail

    def request(self, items):
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError('请先选择 1–20 项餐品')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('餐品数据无效')
            detail = self.detail(item.get('productCode'))
            validate_item(item, detail)
        return {**self.context, 'items': copy.deepcopy(items)}

    def summary(self, items):
        parts = []
        for item in items:
            code = item['productCode']
            detail = meal_detail(self.details[code], code)
            choices = []
            for group in item.get('roundList', []):
                r = next(r for r in detail['rounds'] if r['id'] == group['round'])
                for entry in group['comboItemList']:
                    choice = next(c for c in r['choices'] if c['code'] == entry['code'])
                    labels = self.mod_labels(choice['modifications'], entry.get('modification'))
                    choices.append(choice['name'] + '×' + str(entry['quantity']) + ('（' + '、'.join(labels) + '）' if labels else ''))
            choices += self.mod_labels(detail['modifications'], item.get('modification'))
            parts.append(self.foods[code]['name'] + '×' + str(item['quantity']) + ('：' + '、'.join(choices) if choices else ''))
        return parts

    def checkout_view(self, items, quote, card=None):
        lines = []
        for item in items:
            code = item['productCode']
            detail = meal_detail(self.details[code], code)
            choices, modifications = [], []
            for group in item.get('roundList', []):
                r = next(r for r in detail['rounds'] if r['id'] == group['round'])
                for entry in group['comboItemList']:
                    choice = next(c for c in r['choices'] if c['code'] == entry['code'])
                    labels = self.mod_labels(choice['modifications'], entry.get('modification'))
                    choices.append(choice['name'] + ' ×' + str(entry['quantity']))
                    if labels:
                        modifications.append(choice['name'] + '：' + '、'.join(labels))
            modifications += self.mod_labels(detail['modifications'], item.get('modification'))
            lines.append({'name': self.foods[code]['name'], 'quantity': item['quantity'],
                          'image': self.images[code], 'choices': choices, 'modifications': modifications})
        context = self.catalog['context']
        return {'store': context.get('label', '').split(' · ')[0],
                'method': {1: '到店自取', 2: '麦乐送', 5: '得来速', 6: '团餐'}[self.context['beType']],
                'destination': context.get('delivery_label', ''), 'lines': lines,
                'card': {'name': card['productName'], 'fee': card['subtotal']} if card else None,
                'fees': {k: quote.get(k) for k in ('productPrice', 'deliveryPrice', 'packingPrice', 'tablewarePrice')}}

    @staticmethod
    def mod_labels(groups, payload):
        labels = []
        for g in groups:
            for v in g['values']:
                for x in (payload or {}).get('values', []):
                    if x['code'] != v['code']:
                        continue
                    if x['key'] == v.get('selectedKey') and x['quantity'] > 0:
                        labels.append(v['name'])
                    elif x['key'] == v.get('unselectedKey') and v.get('selectedQuantity', 0) > 0:
                        labels.append('不加' + v['name'])
        return labels

    def quote(self, request):
        raw = self.client.call('calculate-price', request)
        data = unwrap(raw)
        if type(data.get('price')) is not int or data['price'] < 0:
            raise ValueError('核价未返回有效的本次实付')
        return raw, {**{k: data.get(k) for k in ('price', 'productPrice', 'deliveryPrice', 'packingPrice', 'tablewarePrice', 'discount')},
                     'items': request['items'], 'quoted_at': now_utc().isoformat()}

    def compare(self, request, option_index=0):
        try:
            options = with_order_options(self.client.call('query-meals', self.context))
            unavailable = '当前门店没有可用的随单购卡选项'
        except ValueError:
            options, unavailable = [], '随单卡选项暂未查到，优惠待核实'
        baseline, quote = self.quote(request)
        if not options:
            return {'comparison': None, 'quote': quote, 'unavailable': unavailable, 'option_count': 0}
        if type(option_index) is not int or not 0 <= option_index < min(3, len(options)):
            raise ValueError('随单卡选项无效')
        with_order = options[option_index]['withOrder']
        card_request = {**copy.deepcopy(request), 'withOrder': with_order}
        try:
            card, _ = self.quote(card_request)
            added = card_terms(card_request, unwrap(card))
            evidence = {'baseline_request': request, 'baseline_response': baseline,
                        'card_request': card_request, 'card_response': card,
                        'card_product_code': added['productCode'], 'quoted_at': now_utc().isoformat()}
            report = comparison_for_view(evidence)
        except ValueError:
            return {'comparison': None, 'quote': quote, 'unavailable': '开卡金额或权益待核实，可先按不开卡方案结算',
                    'option_count': min(3, len(options)), 'option_index': option_index}
        private_save(self.directory / 'card-evidence.json', evidence)
        self.config['card_comparison'] = evidence
        comparison_id = secrets.token_urlsafe(18)
        self.comparisons = {k: v for k, v in self.comparisons.items()
                            if now_utc() - aware(v['comparison']['quoted_at']) < timedelta(minutes=5)}
        self.comparisons[comparison_id] = {'items': copy.deepcopy(request['items']), 'withOrder': with_order,
                                           'comparison': report, 'card_terms': added}
        return {'comparison': report, 'comparison_id': comparison_id, 'quote': quote,
                'option_count': min(3, len(options)), 'option_index': option_index}

    def order(self, order_id):
        if not isinstance(order_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', order_id):
            raise ValueError('请输入有效订单号')
        decision = self.state.poll_decision(order_id)
        if not decision.get('poll'):
            return {'order': self.state.get('order:' + order_id), 'cached': True, **decision}
        try:
            raw = unwrap(self.client.call('query-order', {'orderId': order_id}))
            if str(raw.get('orderId', '')) != order_id:
                raise ValueError('查询返回的订单号不匹配')
            if order_id in self.order_types:
                raw = {**raw, 'orderType': self.order_types[order_id]}
            result = self.state.observe(order_id, raw)
        except Exception:
            self.state.poll_failed(order_id)
            raise
        return {**result, 'stop': result['order']['terminal'], 'cached': False,
                'map_available': False}

    def handle(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError('请求格式无效')
        if action == 'info':
            return {'checkout_enabled': self.allow_orders, 'celebrate_delivery': self.state.profile()['celebrate_enabled'],
                    'map_available': False, 'last_order': self.state.get('browser:last_order')}
        if action == 'detail':
            return {'detail': self.detail(payload.get('code'))}
        if action in ('quote', 'compare', 'selection', 'draft'):
            request = self.request(payload.get('items'))
            if action == 'quote':
                return self.quote(request)[1]
            if action == 'compare':
                return self.compare(request, payload.get('option_index', 0))
            summary = self.summary(request['items'])
            if action == 'selection':
                private_save(self.directory / 'selection.json', {'context': self.context, 'items': request['items'], 'summary': summary})
                return {'saved': True}
            if not self.allow_orders:
                raise ValueError('当前会话只查询与选餐，不创建订单')
            if payload.get('buy_card') is True:
                compared = self.comparisons.get(payload.get('comparison_id'))
                if not compared or compared['items'] != request['items']:
                    raise ValueError('餐单已改变，请先重新比较卡费')
                if now_utc() - aware(compared['comparison']['quoted_at']) >= timedelta(minutes=5):
                    raise ValueError('开卡报价已过期，需要重新核价')
                request['withOrder'] = compared['withOrder']
            response, quote = self.quote(request)
            card = None
            if request.get('withOrder'):
                card = card_terms(request, unwrap(response))
                if card != compared['card_terms']:
                    raise ValueError('卡种或卡费已变化，请重新比较后选择')
                summary.append(f"加购 {card['productName']}，本次卡费 ¥{card['subtotal'] / 100:.2f}（已含在实付中）；后续权益与续费规则待核实")
            else:
                summary.append('本次不开卡')
            summary.append(' · '.join(label + ('待核实' if quote.get(field) is None else f" ¥{quote[field] / 100:.2f}")
                                      for field, label in (('deliveryPrice', '配送费'), ('packingPrice', '打包费'), ('tablewarePrice', '餐具费'))))
            checkout = self.config.get('checkout', {})
            request.update({k: v for k, v in checkout.items() if k in ('addressId', 'takeWayCode', 'remark')})
            label = self.catalog['context'].get('label', '') + ' ' + self.catalog['context'].get('delivery_label', '')
            draft = make_draft(self.state, request, response, 'price', label + '\n' + '\n'.join(summary))
            self.draft_ids.add(draft['id'])
            return {k: draft[k] for k in ('id', 'summary', 'total_cents', 'expires_at')} | {
                'quote': quote, 'checkout': self.checkout_view(request['items'], quote, card)}
        if action == 'submit':
            draft_id = payload.get('draft_id')
            if not self.allow_orders or payload.get('confirmed') is not True or draft_id not in self.draft_ids:
                raise ValueError('需要确认当前会话的具体餐单和总价')
            result = submit(self.state, self.client, draft_id, draft_id)
            if result.get('created'):
                self.order_types[result['order_id']] = self.context['orderType']
                self.state.set('browser:last_order', result)
            return result
        if action == 'orders':
            result = unwrap(self.client.call('order-list', {}))
            rows = result.get('list')
            if not isinstance(rows, list):
                raise ValueError('订单列表结构未知')
            self.order_types.update({str(o['orderId']): o.get('orderType') for o in rows if o.get('orderId')})
            return {'orders': [{k: o.get(k) for k in ('orderId', 'storeName', 'createTime', 'orderStatus', 'realTotalAmount')}
                               for o in rows if o.get('orderId')]}
        if action == 'order':
            return self.order(payload.get('order_id'))
        if action == 'celebrate':
            return self.state.claim_celebration(str(payload.get('order_id', '')))
        raise ValueError('未开放的操作')
