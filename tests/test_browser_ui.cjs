// Actual polling and delivery handlers; all API responses and timers are synthetic.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../.agents/skills/mcd-pause/assets/browser.js'),'utf8');
const elements=new Map();
const byId=id=>{if(!elements.has(id))elements.set(id,{value:'SYNTHETIC',textContent:'',hidden:true,attributes:{},
  setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
  replaceChildren(){},addEventListener(){}});return elements.get(id);};
const stages=['awaiting_payment','accepted','ready','delivering','completed'].map(stage=>Object.assign(byId(stage),{dataset:{stage}}));
const requests=[],timers=new Map(),listeners={};let sequence=0,fires=0,reduced=false,failure=false,deferred=null;
let result={order:{status:'accepted',label:'备餐中',updated_at:'2026-10-14T08:00:00Z',terminal:false,is_delivery:true}};
const context=vm.createContext({byId,trackedOrder:null,watchTimer:null,orderRequest:null,orderVersion:0,watchFailures:0,nextOrderCheck:0,
  clearTimeout:id=>timers.delete(id),setTimeout:(fn,ms)=>{timers.set(++sequence,{fn,ms});return sequence;},
  info:{celebrate_delivery:true},document:{hidden:false,querySelectorAll:()=>stages,addEventListener:(event,fn)=>listeners[event]=fn},
  sessionStorage:{setItem(){}},matchMedia:()=>({matches:reduced}),confetti:()=>{fires++;},feedback(){},run:fn=>fn(),
  api:async(action,payload)=>{requests.push({action,...payload});if(failure)throw new Error('synthetic failure');if(deferred)return deferred(payload);return action==='celebrate'?{fire:true}:structuredClone(result);}});
vm.runInContext(source.slice(source.indexOf('  const stopWatch='),source.indexOf('  function confetti()')),context);
vm.runInContext(source.slice(source.indexOf('  async function queryOrder('),source.indexOf("  byId('order-list').addEventListener")),context);
const query=id=>vm.runInContext(id?`queryOrder(${JSON.stringify(id)})`:'queryOrder()',context);
(async()=>{
  await query();assert.equal(fires,0);assert.equal(byId('order-status').textContent,'备餐中');
  assert.equal(timers.size,1);assert.equal([...timers.values()][0].ms,180000,'Automatic three-minute polling without a checkbox');
  result.order={...result.order,status:'ready',label:'配餐完成'};await query();
  assert.equal(byId('ready').attributes['aria-current'],'step');assert.equal(byId('accepted').attributes['data-passed'],'');
  context.document.hidden=true;const before=requests.length;await vm.runInContext('poll()',context);assert.equal(requests.length,before,'Hidden page does not query');context.document.hidden=false;
  result={order:{...result.order,status:'completed',label:'已完成',delivered:true,terminal:true},celebration_eligible:true,stop:true};
  await query();assert.equal(fires,1);assert.equal(context.nextOrderCheck,0);assert.equal(byId('order-eta').hidden,true);
  result={...result,cached:true,celebration_eligible:false};await query();assert.equal(fires,1);
  reduced=true;result.celebration_eligible=true;await query();assert.equal(fires,1,'Reduced motion and cached completion do not replay celebration');
  failure=true;
  for(let i=0;i<3;i++)await assert.rejects(query('FAILED'),/synthetic/);
  assert.equal(context.nextOrderCheck,0,'Three failures stop automatic retry');failure=false;
  let release;
  deferred=payload=>payload.order_id==='OLD'?new Promise(resolve=>release=resolve):Promise.resolve({order:{...result.order,label:'配送中',status:'delivering',terminal:false,delivered:false}});
  const old=query('OLD'),duplicate=query('OLD');await query('NEW');release({order:{...result.order,label:'不应覆盖新订单'}});await Promise.all([old,duplicate]);
  assert.equal(requests.filter(x=>x.order_id==='OLD').length,1,'Duplicate in-flight queries are joined');
  assert.equal(byId('order-status').textContent,'配送中');assert.equal(context.trackedOrder,'NEW','Stale response cannot replace a switched order');
  assert.match(byId('order-watch-note').textContent,/自动更新/);
  console.log('Order UI: automatic polling, ready stage, hidden-page pause, terminal stop, bounded retry, stale response protection and one-time celebration passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
