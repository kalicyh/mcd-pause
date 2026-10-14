(() => {
  const token = __MCPAUSE_BROWSER_TOKEN__;
  if (!token) return;
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId('mcpause-shop-data').textContent);
  const money = n => Number.isInteger(n) ? '¥' + (n / 100).toFixed(2) : '待核实';
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  const stable = value => JSON.stringify(value, (_, v) => v && typeof v==='object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])) : v);
  const feedback = text => { byId('browser-feedback').textContent = text; };
  function payable(host,price,label) {
    const caption=node('span',label);caption.className='price-label';host.replaceChildren(caption,node('strong',money(price)));
  }
  function thumbnail(url,name) {
    if(!url)return node('span');
    const img=node('img');img.className='food-thumb';img.src=url;img.alt=name;img.referrerPolicy='no-referrer';img.loading='lazy';
    img.addEventListener('error',()=>{img.hidden=true;},{once:true});return img;
  }
  let info = {}, cart = [], compared = null, cartQuote = null, draft = null, watchTimer = null, trackedOrder = null;
  let requestQueue = Promise.resolve(), priceTimer = null, priceVersion = 0, pricing = false, reviewing = false;
  const comparisonCache = new Map();
  const storageKey = 'mcpause-cart:' + JSON.stringify([data.context.storeCode, data.context.beCode, data.context.reservationDate]);

  function api(action, payload = {}) {
    // One MCP client/session is shared by detail queries, pricing and order tracking.
    const task = requestQueue.then(async () => {
      try {
        const response = await fetch('/api/' + action, {method:'POST',headers:{'Content-Type':'application/json','X-McPause-Session':token},body:JSON.stringify(payload)});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '查询未完成');
        return result;
      } catch (error) {
        throw new Error(error instanceof TypeError ? '本次点餐连接已断开，请回到 Codex 重新打开。' : error.message);
      }
    });
    requestQueue = task.catch(()=>{});
    return task;
  }
  window.mcpauseRequest = api;
  function compareMeal(selection, option=0, force=false) {
    const payload={items:structuredClone(selection),option_index:option}, key=stable(payload), cached=comparisonCache.get(key);
    if(!force&&cached&&cached.until>Date.now())return cached.promise;
    const entry={until:Date.now()+60000};
    entry.promise=api('compare',payload).then(result=>{entry.until=Date.now()+60000;return result;},error=>{comparisonCache.delete(key);throw error;});
    comparisonCache.set(key,entry);
    if(comparisonCache.size>30)comparisonCache.delete(comparisonCache.keys().next().value);
    return entry.promise;
  }
  window.mcpauseCompare = compareMeal;
  function saveCart() { try { sessionStorage.setItem(storageKey, JSON.stringify(cart)); } catch {} }
  function items() { return cart.map(row => row.item); }
  window.mcpauseCartItems = items;
  function updateCheckout() {
    const buying=byId('buy-card').checked&&compared?.comparison_id;
    const price=buying?compared.comparison.with_card.price:cartQuote?.price;
    if(pricing)byId('cart-price').textContent='正在自动核价…';
    else if(cartQuote)payable(byId('cart-price'),price,buying?'实付 · 含开卡费':'实付');
    else byId('cart-price').textContent='';
    byId('cart-refresh').hidden=pricing||Boolean(cartQuote)||!cart.length;
    byId('cart-review').disabled=reviewing||!cart.length||Boolean(info.checkout_enabled&&(pricing||!cartQuote));
    byId('buy-card').disabled=pricing||!compared?.comparison_id||!info.checkout_enabled;
  }
  function invalidate() {
    clearTimeout(priceTimer);priceVersion++;compared=null;cartQuote=null;draft=null;pricing=Boolean(cart.length);
    byId('cart-card').hidden=true;byId('card-choice').hidden=true;byId('buy-card').checked=false;
    saveCart();updateCheckout();
    if(cart.length)priceTimer=setTimeout(()=>refreshCartPricing(),450);
  }
  function drawCart() {
    byId('cart').hidden = !cart.length;
    byId('cart-count').textContent = '· ' + cart.reduce((n,row) => n+row.item.quantity,0) + ' 份';
    const host = byId('cart-items'); host.replaceChildren();
    cart.forEach((row,index) => {
      const line = node('div'); line.className = 'row cart-item';
      const copy = node('div');copy.className='item-copy';copy.append(node('strong',row.name));
      const sub=node('details');sub.append(node('summary','查看搭配'),node('p',(row.summary||[]).join(' · ')));copy.append(sub);
      const label = node('label','份数 '), quantity = node('input'); quantity.type='number';quantity.min='1';quantity.max='20';quantity.step='1';quantity.value=row.item.quantity;quantity.setAttribute('aria-label',row.name+'份数');
      quantity.addEventListener('change',()=>{const q=Number(quantity.value);if(!Number.isInteger(q)||q<1||q>20){quantity.value=row.item.quantity;return;}row.item.quantity=q;invalidate();drawCart();});label.append(quantity);
      const remove = node('button','移除');remove.type='button';remove.addEventListener('click',()=>{cart.splice(index,1);invalidate();drawCart();});
      const controls=node('div');controls.className='item-controls';controls.append(label,remove);
      line.append(thumbnail(data.cards.find(c=>c.code===row.item.productCode)?.image,row.name),copy,controls);host.append(line);
    });
    window.mcpauseShopping.cartChanged?.();
  }
  window.mcpauseAddToCart = row => {
    const signature = item => stable({...item,quantity:1});
    const match = cart.find(c=>signature(c.item)===signature(row.item));
    if(match){if(match.item.quantity>=20){feedback('同一搭配最多 20 份。');return;}match.item.quantity++;}
    else {if(cart.length>=20){feedback('餐单最多 20 项。');return;}cart.push(structuredClone(row));}
    invalidate();drawCart();feedback('已加入餐单，正在计算整单价格与开卡优惠。');
  };
  function showComparison(result) {
    compared=result.comparison?result:null;cartQuote=result.quote;
    const report=result.comparison, host=byId('cart-card');host.replaceChildren();host.hidden=false;
    byId('card-choice').hidden=!report;
    if(!report){host.append(node('p',result.unavailable||'开卡优惠待核实'));byId('buy-card').checked=false;return;}
    const headRow=node('div');headRow.className='offer-head';
    const outcome=node('span',report.net_saving_cents<0?'本单多付 '+money(-report.net_saving_cents):report.net_saving_cents>0?'本单净省 '+money(report.net_saving_cents):'本单实付相同');outcome.className='offer-outcome';
    headRow.append(node('h3',report.card.name),outcome);host.append(headRow);
    const prices=node('div');prices.className='price-comparison';
    for(const [label,value,note] of [['不开卡',report.without_card.price,''],['随单开卡',report.with_card.price,'含卡费 '+money(report.card.fee_cents)]]){
      const cell=node('div'),title=node('span',label);title.className='price-label';cell.append(title,node('strong',money(value)),node('small',note));prices.append(cell);
    }
    host.append(prices);
    const details=node('details'), summary=node('summary','费用与回本明细');details.append(summary);
    const table=node('table'),head=node('tr');for(const title of ['费用','不开卡','随单开卡'])head.append(node('th',title));table.append(head);
    for(const [field,label] of [['foodPrice','餐品'],['cardFee','开卡费'],['deliveryPrice','配送费'],['packingPrice','打包费'],['tablewarePrice','餐具费'],['price','本次实付']]){
      const before=report.without_card[field],after=report.with_card[field],waived=before>0&&after===0&&['deliveryPrice','packingPrice','tablewarePrice'].includes(field);
      const row=node('tr');row.append(node('th',label),node('td',money(before)),node('td',money(after)+(waived?' · 已免':'')));table.append(row);
    }
    details.append(table,node('p','本单优惠 '+money(report.gross_saving_cents)+'（未扣卡费）'));
    const label=node('label','假设后续每次节省（元） '),input=node('input');input.type='number';input.min='0';input.step='.01';input.value=(report.repeat_saving_assumption_cents/100).toFixed(2);label.append(input);
    const payback=node('p');payback.setAttribute('aria-live','polite');
    const update=()=>{const n=input.value,remaining=report.card.fee_cents-report.gross_saving_cents;if(!/^\d+(?:\.\d{1,2})?$/.test(n)){payback.textContent='请输入非负金额，最多两位小数。';return;}const repeat=Math.round(Number(n)*100);payback.textContent=remaining<=0?'本次优惠已覆盖卡费。':repeat>0?'按此假设，第 '+(1+Math.ceil(remaining/repeat))+' 次用餐回本（含本次）。':'后续没有节省，按此假设无法回本。';};
    input.addEventListener('input',update);update();details.append(label,payback,node('p','默认按每单同额优惠估算。有效期内的减免次数、门槛及续费规则待核实。'));
    const stamp=node('p','核价 '+new Date(report.quoted_at).toLocaleTimeString('zh-CN',{hour12:false}));stamp.className='muted';details.append(stamp);
    if(result.option_count>1){const label=node('label','其他随单卡方案 '),select=node('select');for(let i=0;i<result.option_count;i++){const option=node('option','方案 '+(i+1));option.value=i;select.append(option);}select.value=result.option_index;select.addEventListener('change',()=>{byId('buy-card').checked=false;refreshCartPricing(true,Number(select.value));});label.append(select);details.append(label);}
    host.append(details);
    byId('card-choice-text').textContent='随单加购这张卡 · '+money(report.card.fee_cents);
    byId('buy-card').setAttribute('aria-label','随单开通 '+report.card.name+'，卡费 '+money(report.card.fee_cents));
    byId('card-choice-note').hidden=Boolean(info.checkout_enabled);
    byId('card-choice-note').textContent='当前仅查询，尚未开放下单。';
  }
  async function refreshCartPricing(force=false,option=0) {
    clearTimeout(priceTimer);if(!cart.length)return;
    const version=++priceVersion, selection=structuredClone(items());
    pricing=true;compared=null;cartQuote=null;byId('buy-card').checked=false;byId('cart-card').hidden=true;byId('card-choice').hidden=true;updateCheckout();
    try {
      const result=await compareMeal(selection,option,force);
      if(version!==priceVersion||stable(selection)!==stable(items()))return;
      showComparison(result);feedback('');
    } catch(error) {if(version===priceVersion){byId('cart-card').hidden=false;byId('cart-card').replaceChildren(node('p','核价暂未完成：'+error.message));}}
    finally {if(version===priceVersion){pricing=false;updateCheckout();}}
  }
  async function run(action) { try { feedback('正在查询…'); await action(); feedback(''); } catch(error) { feedback(error.message); } }
  byId('cart-refresh').addEventListener('click',()=>refreshCartPricing(true));
  byId('buy-card').addEventListener('change',()=>{draft=null;updateCheckout();});
  function renderConfirmation(next) {
    const host=byId('confirm-summary'),view=next.checkout;host.replaceChildren();
    if(!view){host.textContent=next.summary;return;}
    const delivery=node('div');delivery.className='delivery-summary';delivery.append(node('strong',view.destination||view.method),node('span',view.method+' · '+view.store));host.append(delivery);
    for(const line of view.lines){
      const meal=node('div');meal.className='confirm-meal';
      const quantity=node('span','×'+line.quantity);quantity.className='amount';
      meal.append(thumbnail(line.image,line.name),node('h3',line.name),quantity);
      if(line.choices.length){const choices=node('p',line.choices.join(' · '));choices.className='meal-choices';meal.append(choices);}
      if(line.modifications.length){const mods=node('details');mods.className='meal-mods';mods.append(node('summary','配料与冰量'));for(const value of line.modifications)mods.append(node('p',value));meal.append(mods);}
      host.append(meal);
    }
    if(view.card){const row=node('div');row.className='confirm-card';row.append(node('span',view.card.name),node('strong',money(view.card.fee)));host.append(row);}
    const fees=node('details');fees.className='confirm-fees';fees.append(node('summary',view.card?'费用明细 · 含开卡费':'费用明细 · 不开卡'));
    for(const [key,label] of [['productPrice',view.card?'餐品及卡费':'餐品'],['deliveryPrice','配送费'],['packingPrice','打包费'],['tablewarePrice','餐具费']]){const row=node('p');row.append(node('span',label),node('span',money(view.fees[key])));fees.append(row);}
    if(view.card)fees.append(node('p','后续权益、减免次数与续费规则待核实。'));
    host.append(fees);
  }
  byId('cart-review').addEventListener('click',()=>run(async()=>{
    if(reviewing)return;
    if(!info.checkout_enabled){await api('selection',{items:items()});byId('cart-price').textContent='餐单已保存。回到 Codex 说“核对刚才的餐单”即可继续。';return;}
    reviewing=true;updateCheckout();
    const selection=structuredClone(items()),buying=byId('buy-card').checked;
    try {
      const next=await api('draft',{items:selection,buy_card:buying,comparison_id:compared?.comparison_id});
      if(stable(selection)!==stable(items())||buying!==byId('buy-card').checked)throw new Error('餐单已改变，请重新确认。');
      draft=next;renderConfirmation(draft);payable(byId('confirm-price'),draft.total_cents,'实付');
      byId('confirm-expiry').textContent=new Date(draft.expires_at).toLocaleTimeString()+' 前有效 · 下单后前往官方支付';
      byId('confirm-feedback').textContent='';byId('confirm-submit').disabled=false;byId('confirm-submit').textContent='确认下单';byId('confirm-cancel').textContent='返回修改';byId('confirm-order').showModal();
    } catch(error) {
      if(buying){comparisonCache.clear();refreshCartPricing(true);}
      throw error;
    } finally {reviewing=false;updateCheckout();}
  }));
  function paymentLink(result) {
    if(!result?.payment_url)return null;
    try {const url=new URL(result.payment_url);if(url.protocol==='https:'&&!url.username&&!url.password&&(url.hostname==='mcd.cn'||url.hostname.endsWith('.mcd.cn'))){const link=node('a','前往官方支付');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';return link;}}catch{}
    return null;
  }
  function showCreatedOrder(result) {
    if(!result?.created)return;
    const host=byId('checkout-result');host.hidden=false;host.replaceChildren(node('h3','订单已创建'),node('p','订单号：'+result.order_id));
    const link=paymentLink(result);host.append(link||node('p','支付链接暂未返回，请在官方 App 核对并付款。'));
    host.append(node('p','完成支付后，可在“订单进度”核实支付与配送状态。'));
    byId('order-id').value=result.order_id;
  }
  byId('confirm-cancel').addEventListener('click',()=>{byId('confirm-order').close();draft=null;});
  byId('confirm-submit').addEventListener('click',async()=>{
    if(!draft||byId('confirm-submit').disabled)return;byId('confirm-submit').disabled=true;
    const submitting=draft;draft=null;
    try {
      const result=await api('submit',{draft_id:submitting.id,confirmed:true});
      if(!result.created){byId('confirm-feedback').textContent=result.reason==='card_changed'?'卡种或卡费已变化，请关闭并重新确认。':'价格已变化，本次未创建订单。请关闭并重新确认。';comparisonCache.clear();refreshCartPricing(true);return;}
      showCreatedOrder(result);byId('confirm-feedback').replaceChildren(node('p','订单已创建，等待你完成支付。'));
      const link=paymentLink(result);if(link)byId('confirm-feedback').append(link);else byId('confirm-feedback').append(node('p','支付链接暂未返回，请在官方 App 核对并付款。'));
      byId('confirm-cancel').textContent='关闭';feedback('订单已创建，支付入口已保留在餐单下方。');
    } catch(error) {byId('confirm-feedback').textContent=error.message+'。请先查询订单，不要重复提交。';}
  });
  function page(orders){byId('menu-page').hidden=orders;byId('orders-page').hidden=!orders;byId('menu-tab').className=orders?'':'primary';byId('orders-tab').className=orders?'primary':'';}
  byId('menu-tab').addEventListener('click',()=>page(false));byId('orders-tab').addEventListener('click',()=>page(true));
  const stopWatch=note=>{clearTimeout(watchTimer);watchTimer=null;byId('order-follow').checked=false;byId('order-watch-note').textContent=note||'';};
  function confetti(){
    const canvas=byId('delivery-confetti'),ctx=canvas.getContext('2d');if(!ctx)return;
    canvas.hidden=false;canvas.width=innerWidth;canvas.height=innerHeight;
    const colors=['#ffbc0d','#db3030','#f6b9d5','#39a875'];
    const bits=Array.from({length:90},()=>({x:Math.random()*canvas.width,y:-20-Math.random()*canvas.height/2,vx:(Math.random()-.5)*4,vy:2+Math.random()*3,r:Math.random()*6,color:colors[Math.floor(Math.random()*colors.length)]}));
    const start=performance.now();function frame(t){ctx.clearRect(0,0,canvas.width,canvas.height);for(const p of bits){p.x+=p.vx;p.y+=p.vy;p.vy+=.035;ctx.save();ctx.translate(p.x,p.y);ctx.rotate(p.r+=.05);ctx.fillStyle=p.color;ctx.fillRect(-3,-5,6,10);ctx.restore();}if(t-start<2600)requestAnimationFrame(frame);else{canvas.hidden=true;ctx.clearRect(0,0,canvas.width,canvas.height);}}requestAnimationFrame(frame);
  }
  async function queryOrder(){
    const id=byId('order-id').value.trim();if(!id)throw new Error('请先选择订单或输入订单号。');
    if(trackedOrder&&trackedOrder!==id)stopWatch('已切换订单。');trackedOrder=id;
    const result=await api('order',{order_id:id}),order=result.order;
    if(!order?.label){if(result.stop)stopWatch(result.reason);throw new Error(result.reason||'订单状态待核实。');}
    byId('order-detail').hidden=false;byId('order-status').textContent=order.delivered?'已送达，吃饭啦！':order.label;
    byId('order-checked').textContent=(result.cached?'上次查询：':'查询于：')+new Date(order.updated_at).toLocaleString();
    document.querySelectorAll('[data-stage]').forEach(el=>{if(el.dataset.stage===order.status)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
    byId('order-eta').textContent=order.eta?'预计时间：'+order.eta:'预计送达时间待核实';byId('order-pickup').textContent=order.take_code?'取餐码：'+order.take_code:'';
    if(result.celebration_eligible&&info.celebrate_delivery&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const claim=await api('celebrate',{order_id:id});if(claim.fire)confetti();}
    clearTimeout(watchTimer);
    if(result.stop)stopWatch(result.reason||'订单已结束，已停止检查。');
    else if(byId('order-follow').checked){byId('order-watch-note').textContent='跟进中；关闭页面或停止点餐会话后结束。';watchTimer=setTimeout(poll,180000);}
  }
  async function poll(){if(document.hidden){watchTimer=setTimeout(poll,180000);return;}try{await queryOrder();}catch(error){feedback(error.message);stopWatch('查询失败，已停止自动检查。');}}
  byId('order-query').addEventListener('click',()=>run(queryOrder));
  byId('order-follow').addEventListener('change',()=>{if(byId('order-follow').checked)run(queryOrder);else stopWatch('已停止自动检查。');});
  byId('order-list').addEventListener('click',()=>run(async()=>{
    const result=await api('orders'),host=byId('recent-orders');host.replaceChildren();
    for(const order of result.orders){const row=node('div');row.className='row';row.append(node('span',(order.storeName||'麦当劳')+' · '+(order.createTime||'时间待核实')));const button=node('button','查看 '+order.orderId);button.type='button';button.addEventListener('click',()=>{stopWatch();byId('order-id').value=order.orderId;run(queryOrder);});row.append(button);host.append(row);}if(!result.orders.length)host.textContent='暂无可查询的订单。';
  }));
  try{const saved=JSON.parse(sessionStorage.getItem(storageKey));if(Array.isArray(saved))cart=saved.filter(row=>row?.item&&data.cards.some(c=>c.code===row.item.productCode));}catch{}
  if(!cart.length&&data.card_comparison?.selection.items)cart=data.card_comparison.selection.items.map(item=>({item:structuredClone(item),name:data.cards.find(c=>c.code===item.productCode)?.name||'已选餐品',summary:window.mcpauseShopping.description()}));
  drawCart();byId('browser-nav').hidden=false;byId('cart-review').disabled=true;
  api('info').then(result=>{
    info=result;byId('cart-review').textContent=info.checkout_enabled?'去结算':'保存餐单';byId('session-label').textContent=info.checkout_enabled?'':'选餐与查询';
    showCreatedOrder(info.last_order);updateCheckout();refreshCartPricing();window.mcpauseShopping.start();
  }).catch(error=>feedback(error.message));
})();
