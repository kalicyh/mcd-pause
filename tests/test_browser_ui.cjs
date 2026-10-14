// Run the actual delivery UI handler against synthetic API results.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../.agents/skills/mcd-pause/assets/browser.js'),'utf8');
const elements=new Map();
const byId=id=>{if(!elements.has(id))elements.set(id,{value:'SYNTHETIC',checked:false,textContent:'',hidden:true});return elements.get(id);};
const requests=[];
let fires=0,stops=0,reduced=false;
let result={order:{status:'accepted',label:'备餐中',updated_at:'2026-10-14T08:00:00Z',terminal:false},celebration_eligible:false};
const context=vm.createContext({byId,trackedOrder:null,watchTimer:null,clearTimeout(){},setTimeout(){return 1;},poll(){},
  info:{celebrate_delivery:true},document:{querySelectorAll(){return [];}},
  matchMedia:()=>({matches:reduced}),stopWatch:()=>{stops++;},confetti:()=>{fires++;},
  api:async(action,payload)=>{requests.push(action);return action==='celebrate'?{fire:true}:structuredClone(result);}});
vm.runInContext(source.slice(source.indexOf('  async function queryOrder(){'),source.indexOf('  async function poll()')),context);
(async()=>{
  await vm.runInContext('queryOrder()',context);
  assert.equal(fires,0);assert.equal(byId('order-status').textContent,'备餐中');
  result={order:{status:'completed',label:'已完成',delivered:true,updated_at:'2026-10-14T08:04:00Z',eta:'合成时间'},celebration_eligible:true,stop:true};
  await vm.runInContext('queryOrder()',context);
  assert.equal(fires,1);assert.equal(stops,1);assert.match(byId('order-status').textContent,/已送达/);
  result={...result,cached:true,celebration_eligible:false};
  await vm.runInContext('queryOrder()',context);
  assert.equal(fires,1);assert.equal(requests.filter(x=>x==='celebrate').length,1);
  reduced=true;result.celebration_eligible=true;
  await vm.runInContext('queryOrder()',context);
  assert.equal(fires,1);assert.equal(requests.filter(x=>x==='celebrate').length,1);
  console.log('Delivery UI: no celebration while preparing, once on arrival, no replay or reduced-motion effect.');
})().catch(error=>{console.error(error);process.exitCode=1;});
