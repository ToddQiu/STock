const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('../src/quant_news_monitor/ui/portfolio_research_core');
const source=fs.readFileSync(require.resolve('../src/quant_news_monitor/ui/portfolio_research.js'),'utf8');
const loadCode=source.slice(source.indexOf('  async function load()'),source.indexOf('  function renderExposure()'));

test('opening analysis requests the selected year automatically and retries failed polling',async()=>{
  const calls=[],timers=[];let fail=true;
  const ctx=vm.createContext({portfolioContext:{id:'Todd'},performanceYear:2026,data:{},poll:null,loadSequence:0,
    document:{hidden:false},render(){},error(){},clearTimeout(){},setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},
    api:async url=>{calls.push(url);if(fail)throw Error('offline');return {portfolio_id:'Todd',performance:{year:2026,busy:false}};}});
  vm.runInContext(loadCode,ctx);
  await ctx.load();
  assert.match(calls[0],/year=2026/);
  assert.equal(timers.length,1,'failed polling must schedule recovery');
  fail=false;await timers[0].fn();
  assert.equal(ctx.data.portfolio_id,'Todd');
  assert.equal(timers.length,2);
});

test('late response from another year cannot replace the current analysis',async()=>{
  let resolve;
  const ctx=vm.createContext({portfolioContext:{id:'Todd'},performanceYear:2026,data:{marker:'current'},poll:null,loadSequence:0,
    document:{hidden:false},render(){},error(){},clearTimeout(){},setTimeout(){},
    api:()=>new Promise(done=>{resolve=done;})});
  vm.runInContext(loadCode,ctx);
  const pending=ctx.load();ctx.performanceYear=2025;resolve({marker:'old year'});await pending;
  assert.equal(ctx.data.marker,'current');
});

test('an older overlapping poll cannot overwrite fresher results for the same portfolio and year',async()=>{
  const pending=[];
  const ctx=vm.createContext({portfolioContext:{id:'Todd'},performanceYear:2026,data:{},poll:null,loadSequence:0,
    document:{hidden:false},render(){},error(){},clearTimeout(){},setTimeout(){},
    api:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext(loadCode,ctx);
  const first=ctx.load(),second=ctx.load();
  pending[1]({marker:'latest'});await second;
  pending[0]({marker:'older'});await first;
  assert.equal(ctx.data.marker,'latest');
});

test('local formulas respond to live valuations, cash and changed quantities without an AI client',()=>{
  const dates=['2025-12-31','2026-01-02','2026-01-05'];
  const history=prices=>({currency:'USD',source:'fixture adjusted',points:prices.map((v,i)=>({time:dates[i],close:v,adjusted_close:v}))});
  const histories={'US:A':history([100,110,120]),'US:B':history([100,90,80])};
  const positions=[{market:'US',symbol:'A',currency:'USD',market_value_base:50},{market:'US',symbol:'B',currency:'USD',market_value_base:50}];
  const options={year:2026,asOf:Date.parse('2026-01-06'),allowPartial:true};
  const first=C.portfolioPerformance(positions,{cash_base:0},histories,options);
  const changed=[{...positions[0],quantity:10,market_value_base:150},positions[1]];
  const next=C.portfolioPerformance(changed,{cash_base:0},histories,options);
  assert.ok(Math.abs(first.annualReturn)<1e-9);
  assert.ok(Math.abs(next.annualReturn-10)<1e-9);
  assert.equal(C.portfolio(changed,{cash_base:200}).max,37.5);
  assert.ok(Math.abs(C.portfolioPerformance(changed,{cash_base:200},histories,options).annualReturn-5)<1e-9);
});

test('analysis view computes new-listing intervals automatically and labels them separately from annual returns',()=>{
  const elements=new Map();
  function node(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',parentElement:{},
    querySelector:node,insertAdjacentHTML(){},replaceChildren(){}});return elements.get(id);}
  const dates=['2025-12-31','2026-01-02','2026-01-05'];
  const history=prices=>({currency:'USD',source:'fixture',points:prices.map((v,i)=>({time:dates[i],close:v,adjusted_close:v}))});
  const now=Date.parse('2026-01-06');
  class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
  const ctx=vm.createContext({C:{...C,portfolioPerformance:(p,a,h,o)=>C.portfolioPerformance(p,a,h,{...o,asOf:now})},Date:Clock,performanceYear:2026,
    data:{performance:{year:2026,histories:{'US:A':history([100,110,121]),'US:B':{...history([100,110,121]),points:history([100,110,121]).points.slice(1)}}}},
    portfolio:{positions:[{market:'US',symbol:'A',currency:'USD',market_value_base:50},{market:'US',symbol:'B',currency:'USD',market_value_base:50}],summary:{cash_base:0}},
    assets:{querySelectorAll:()=>[]},document:{getElementById:node},n:v=>v==null?'--':v.toFixed(2),when:v=>v||'未取得',esc:String,
    holdingLabel:v=>v,table:()=>'',source:()=>''});
  vm.runInContext(source.slice(source.indexOf('  function renderPerformance()'),source.indexOf('  function allocations()')),ctx);
  ctx.renderPerformance();
  const html=node('pr-performance-result').innerHTML;
  assert.match(html,/共同区间模拟收益/);assert.match(html,/10.00%/);assert.match(html,/2026-01-02 → 2026-01-05/);
  assert.doesNotMatch(html,/2026年截至模拟收益/);
  ctx.portfolio.summary.cash_base=100;ctx.renderPerformance();
  assert.match(node('pr-performance-result').innerHTML,/5.00%/);
});
