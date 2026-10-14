// Run the actual comparison functions and click handler without a browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../.agents/skills/mcd-pause/assets/shopping.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const elements = new Map();
function element(tag, text = '') {
  return {tag, textContent: text, children: [], append(...children) {this.children.push(...children);},
    replaceChildren() {this.children = [];}, addEventListener(type, callback) {this[type] = callback;}};
}
for (const match of html.split('<script>')[0].matchAll(/\b(data-[\w-]+)(?:=|\s|>)/g)) {
  elements.set('[' + match[1] + ']', element('div'));
}
const one = selector => {assert.ok(elements.has(selector), 'Missing element: ' + selector); return elements.get(selector);};
const item = {productCode: 'synthetic-meal', quantity: 1,
  modification: {values: [{code: 'ice', key: 'less', quantity: 1}]}};
const data = {context: {storeCode: '1000000', beType: 1, orderType: 1, label: '合成测试门店'},
  card_comparison: {quoted_at: '2026-10-14T07:16:40+00:00',
    selection: {context: {storeCode: '1000000', beType: 1, orderType: 1}, items: [structuredClone(item)]},
    card: {name: '合成测试卡', fee_cents: 990},
    without_card: {cardFee: 0, foodPrice: 3690, deliveryPrice: 600, packingPrice: 190, tablewarePrice: null, price: 4480},
    with_card: {cardFee: 990, foodPrice: 3690, deliveryPrice: 0, packingPrice: 0, tablewarePrice: null, price: 4680},
    gross_saving_cents: 790, net_saving_cents: -200}};
const messages = [];
let restored;
const context = vm.createContext({data, one, node: element, repeatSaving: 790, sending: false,
  selected: item.productCode, key: 'synthetic-key', restore: snapshot => {restored = snapshot;},
  money: n => Number.isFinite(n) ? '¥' + (n / 100).toFixed(2) : '待核价',
  itemFor: () => item, card: () => ({detail: {}}), validation: () => '',
  remember() {}, description: () => ['合成餐品（少冰）'], send: message => messages.push(message)});
const stable = script.split('\n').find(line => line.includes('const stable ='));
const calculations = script.slice(script.indexOf('    function updatePayback()'), script.indexOf('    function drawMods('));
const handler = script.slice(script.indexOf("    one('[data-compare-card]').addEventListener"),
  script.indexOf("    one('[data-repeat-saving]').addEventListener"));
vm.runInContext(stable + '\n' + calculations + '\n' + handler, context);
vm.runInContext('restoreComparedSelection()', context);
assert.equal(restored.privateContent.view, 'detail');
assert.equal(restored.privateContent.items[item.productCode].modification.values[0].key, 'less');
vm.runInContext("updateCardComparison(card(), '')", context);
assert.equal(one('[data-card-results]').hidden, false);
assert.match(one('[data-card-gross]').textContent, /¥7\.90/);
assert.match(one('[data-card-net]').textContent, /多付 ¥2\.00/);
assert.match(one('[data-break-even]').textContent, /第 2 次/);
assert.equal(one('[data-card-rows]').children.length, 6);
assert.equal(one('[data-card-rows]').children.at(-1).children.at(-1).textContent, '¥46.80');
context.repeatSaving = 100;
vm.runInContext('updatePayback()', context);
assert.match(one('[data-break-even]').textContent, /第 3 次/);
context.repeatSaving = 0;
vm.runInContext('updatePayback()', context);
assert.match(one('[data-break-even]').textContent, /无法回本/);
one('[data-compare-card]').click();
assert.equal(messages.length, 1);
const payload = JSON.parse(messages[0].split('选择数据：')[1]);
assert.deepEqual(payload.items, [item]);
assert.equal(payload.withOrder, undefined);
assert.match(messages[0], /不开卡、不创建订单/);
item.modification.values[0].key = 'standard';
vm.runInContext("updateCardComparison(card(), '')", context);
assert.equal(one('[data-card-results]').hidden, true);
assert.match(one('[data-card-status]').textContent, /重新比较/);
context.sending = true;
vm.runInContext("updateCardComparison(card(), '')", context);
assert.equal(one('[data-compare-card]').disabled, true);
console.log('Card comparison display, assumptions, changed cart and read-only request passed.');
