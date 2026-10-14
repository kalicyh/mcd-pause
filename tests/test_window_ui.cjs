// Test the actual chat callbacks using a synthetic host; never send a real message.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../.agents/skills/mcd-pause/assets/windows.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
function harness(view='preferences',host=true){
  const els=new Map(),priorities=['new','saving','protein'],actions=['menu','game','query','follow','stop','recent'];
  const make=()=>({value:'',dataset:{},listeners:{},attrs:{},hidden:true,disabled:false,textContent:'',children:[],
    reportValidity(){return true;},setAttribute(k,v){this.attrs[k]=v;},append(...nodes){this.children.push(...nodes);},addEventListener(k,fn){this.listeners[k]=fn;}});
  const one=selector=>{if(!els.has(selector))els.set(selector,make());return els.get(selector);};
  for(const priority of priorities){const el=one('[data-priority="'+priority+'"]');el.dataset.priority=priority;}
  for(const action of actions){const el=one('[data-action="'+action+'"]');el.dataset.action=action;el.textContent=action;}
  const inputs=['intent','budget','kcal','protein','sodium','order-id'].map(k=>one('[data-'+k+']'));
  const buttons=[...priorities.map(k=>one('[data-priority="'+k+'"]')),...actions.map(k=>one('[data-action="'+k+'"]'))];
  const all=selector=>selector==='button'?buttons:selector==='[data-action]'?buttons.filter(b=>b.dataset.action):selector==='[data-priority]'?buttons.filter(b=>b.dataset.priority):selector==='[data-view="preferences"] input'?inputs.slice(0,5):selector==='input'?inputs:[];
  one('#mcpause-window-data').textContent=JSON.stringify({view,state_dir:'/synthetic/state',skill_path:'/synthetic/SKILL.md',intent:'四件套',constraints:{budget_cents:4200,min_protein:25},orders:[]});
  const prompts=[],states=[];let release;
  const openai={setWidgetState:async state=>states.push(state)};
  if(host)openai.sendFollowUpMessage=async message=>{prompts.push(message);await new Promise(resolve=>release=resolve);};
  vm.runInNewContext(script,{document:{getElementById:()=>({querySelector:one,querySelectorAll:all}),createElement:make},window:{openai,addEventListener(){}}});
  return {one,prompts,states,click:action=>one('[data-action="'+action+'"]').listeners.click(),priority:key=>one('[data-priority="'+key+'"]').listeners.click(),release:()=>release()};
}
(async()=>{
  const h=harness();h.priority('protein');h.one('[data-kcal]').value='650';
  const first=h.click('menu');await h.click('menu');assert.equal(h.prompts.length,1,'Duplicate send is blocked while pending');
  const prompt=h.prompts[0].prompt;assert.match(prompt,/\/synthetic\/SKILL.md/);assert.match(prompt,/\/synthetic\/state/);
  const selection=JSON.parse(prompt.split('选择数据：')[1]);assert.deepEqual(selection,{intent:'四件套',priority:'protein',constraints:{budget_cents:4200,max_kcal:650,min_protein:25,max_sodium:null}});
  h.release();await first;
  const game=h.click('game');assert.match(h.prompts[1].prompt,/render-window --view game/);h.release();await game;
  const order=harness('orders');order.one('[data-order-id]').value='SYNTHETIC-ORDER';
  for(const action of ['query','follow','stop']){const pending=order.click(action);assert.match(order.prompts.at(-1).prompt,/SYNTHETIC-ORDER/);order.release();await pending;}
  assert.match(order.prompts[2].prompt,/不取消订单/);
  order.one('[data-order-id]').value='bad id!';await order.click('query');assert.equal(order.prompts.length,3,'Invalid order ID does not send');
  const fallback=harness('preferences',false);await fallback.click('menu');assert.equal(fallback.one('[data-fallback]').hidden,false);assert.match(fallback.one('[data-fallback]').value,/4200/);
  console.log('Chat windows: current choices, transient targets, exact Skill/state routing, pending guard, real order actions and host fallback passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
