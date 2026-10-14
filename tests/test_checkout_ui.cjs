// Run the browser checkout against a synthetic MCP API. No real purchases.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../.agents/skills/mcd-pause/assets/browser.js'),'utf8').replace('__MCPAUSE_BROWSER_TOKEN__','"TEST_NONCE"');
const tick=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function element(tag='div',text=''){
  const e={tag,children:[],listeners:{},hidden:false,checked:false,disabled:false,value:'',textContent:text,
    append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;this.textContent='';},
    setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];},addEventListener(k,f){this.listeners[k]=f;},
    showModal(){this.open=true;},close(){this.open=false;}};return e;
}
function harness(){
  const els=new Map(),get=id=>{if(!els.has(id))els.set(id,element());return els.get(id);};
  const cart=[{item:{productCode:'10',quantity:1},name:'合成套餐',summary:['少冰']}];
  get('mcpause-shop-data').textContent=JSON.stringify({context:{storeCode:'1000000'},cards:[{code:'10',name:'合成套餐'}]});
  let timers=new Map(),sequence=0,hold=null,submitResult=null;
  const calls=[];
  const report=items=>{
    const food=items[0].quantity*3690,without={price:food+790,foodPrice:food,cardFee:0,deliveryPrice:600,packingPrice:190,tablewarePrice:null},withCard={...without,price:food+990,cardFee:990,deliveryPrice:0,packingPrice:0};
    return {comparison_id:'COMPARISON-'+items[0].quantity,quote:{price:without.price,items},comparison:{card:{name:'合成体验卡',fee_cents:990},without_card:without,with_card:withCard,net_saving_cents:-200,gross_saving_cents:790,repeat_saving_assumption_cents:790,quoted_at:'2026-10-14T09:00:00Z'},option_count:1};
  };
  const context=vm.createContext({document:{getElementById:get,createElement:element,querySelectorAll:()=>[],addEventListener(){}},
    window:{mcpauseShopping:{start(){},description:()=>['少冰']}},sessionStorage:{getItem:key=>key.startsWith('mcpause-cart:')?JSON.stringify(cart):null,setItem(){}},
    structuredClone,URL,Date,console,setTimeout:f=>{timers.set(++sequence,f);return sequence;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,options)=>{
      const action=url.split('/').at(-1),payload=JSON.parse(options.body);calls.push({action,payload});let result;
      if(action==='info')result={checkout_enabled:true};
      if(action==='compare'){if(hold)await hold;result=report(payload.items);}
      if(action==='draft')result={id:'DRAFT',summary:'合成套餐 · 少冰',total_cents:payload.buy_card?4680:4480,expires_at:'2026-10-14T09:05:00Z'};
      if(action==='submit')result=submitResult||{created:true,order_id:'TEST',payment_url:'https://m.mcd.cn/synthetic-pay'};
      return {ok:true,json:async()=>result};
    }});
  vm.runInContext(source,context);
  return {get,calls,context,async click(id){await get(id).listeners.click();await tick();},
    async timers(){const all=[...timers.values()];timers.clear();for(const fn of all)fn();await tick();},
    hold(){let resolve;hold=new Promise(r=>resolve=r);return ()=>{hold=null;resolve();};},setSubmitResult(r){submitResult=r;}};
}
const text=e=>e.textContent+' '+e.children.map(text).join(' ');
(async()=>{
  const h=harness();await tick();
  assert.equal(h.calls.filter(r=>r.action==='compare').length,1,'Compare automatically at load');
  assert.match(text(h.get('cart-price')),/44\.80/);assert.equal(h.get('buy-card').checked,false);
  assert.match(text(h.get('cart-card')),/本单多付 ¥2\.00/);
  assert.equal(h.get('cart-card').children.find(c=>c.tag==='details').open,undefined,'Breakdown is initially collapsed');
  await h.context.window.mcpauseCompare([{productCode:'10',quantity:1}]);
  assert.equal(h.calls.filter(r=>r.action==='compare').length,1,'Detail and cart share one recent comparison');
  h.get('buy-card').checked=true;h.get('buy-card').listeners.change();assert.match(text(h.get('cart-price')),/46\.80/);
  await h.click('cart-review');assert.equal(h.get('confirm-order').open,true);
  assert.equal(h.calls.filter(r=>r.action==='submit').length,0,'Review and card selection never create orders');
  assert.equal(h.calls.find(r=>r.action==='draft').payload.comparison_id,'COMPARISON-1');
  await h.click('confirm-submit');await h.click('confirm-submit');
  assert.equal(h.calls.filter(r=>r.action==='submit').length,1,'Double submit blocked');
  assert.equal(h.get('checkout-result').children.find(c=>c.tag==='a').href,'https://m.mcd.cn/synthetic-pay');
  assert.equal(h.get('checkout-result').hidden,false,'Payment remains after dialog closes');

  const h2=harness();await tick();
  const quantity=()=>h2.get('cart-items').children[0].children.find(e=>e.className==='item-controls').children[0].children[0];
  let q=quantity();q.value='2';q.listeners.change();
  assert.equal(h2.get('buy-card').checked,false);assert.equal(h2.get('cart-review').disabled,true);
  const release=h2.hold();await h2.timers();
  q=quantity();q.value='3';q.listeners.change();release();await tick();
  assert.match(h2.get('cart-price').textContent,/正在自动/,'Old quote cannot replace the changed cart');
  await h2.timers();assert.match(text(h2.get('cart-price')),/118\.60/);
  assert.equal(h2.calls.filter(r=>r.action==='submit').length,0);

  const h3=harness();await tick();await h3.click('cart-review');
  assert.equal(h3.calls.find(r=>r.action==='draft').payload.buy_card,false,'No card is the default');
  h3.setSubmitResult({created:false,reason:'card_changed'});await h3.click('confirm-submit');
  assert.match(h3.get('confirm-feedback').textContent,/卡种或卡费已变化/);
  assert.equal(h3.get('confirm-submit').disabled,true);
  console.log('Automatic comparisons, collapsed details, stale response protection, explicit card choice and payment handoff passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
