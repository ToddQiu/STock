const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function setup() {
  const local=new Map(), timers=[];
  const context=vm.createContext({console,Date,URLSearchParams,Map,Promise,
    localStorage:{getItem:key=>local.get(key)||null,setItem:(key,value)=>local.set(key,value)},
    window:{setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}},
    document:{getElementById(){return null;},querySelectorAll(){return [];},addEventListener(){}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/quant_news_monitor/ui/startup_memory.js'),'utf8'),context);
  return {run:code=>vm.runInContext(code,context),context,local,timers};
}

function diskFixture({compression=true}={}) {
  const harness=setup(); let saved=null, fail=false;
  Object.assign(harness.context,{Blob,Response,Uint8Array,
    CompressionStream:compression?CompressionStream:undefined,DecompressionStream});
  const database={transaction(){
    const tx={objectStore(){return {put(value){queueMicrotask(()=>{
      if(fail){tx.onerror();return;} saved=structuredClone(value);tx.oncomplete();
    });},get(){const request={};queueMicrotask(()=>{request.result=saved;request.onsuccess();});return request;}};}};
    return tx;
  }};
  harness.context.disk=database;
  harness.run('startupMemory.database=Promise.resolve(disk)');
  return {...harness,saved:()=>saved,fail:()=>{fail=true;}};
}

test('multi-megabyte dashboard survives lossless disk round trip including full evidence',async()=>{
  const h=diskFixture();
  h.context.large={news:Array.from({length:160},(_,id)=>({id,title:'Full article '+id,
    evidence:'Original evidence. '.repeat(2200),token:'must not persist'})),portfolio:{positions:[]}};
  h.run('rememberStartupResponse("/api/dashboard",large)');
  await h.run('flushStartupMemory()');
  const record=h.saved().records.find(([key])=>key==='/api/dashboard')[1];
  assert.equal(record.encoding,'gzip');assert.ok(record.bytes.byteLength<200000);
  h.context.record=record;
  const decoded=await h.run('decodeStartupRecord(record)');
  assert.equal(decoded.payload.news.length,160);
  assert.equal(decoded.payload.news[159].evidence,h.context.large.news[159].evidence);
  assert.equal(decoded.payload.news[0].token,undefined);
});

test('dashboard is pinned and retained even without browser compression support',async()=>{
  const h=diskFixture({compression:false});
  h.run('rememberStartupResponse("/api/dashboard",{news:[{summary:"x".repeat(6000000)}]});for(let i=0;i<30;i++)rememberStartupResponse(`/api/news?offset=${i}`,{news:[]})');
  await h.run('flushStartupMemory()');
  assert.equal(h.run('startupMemory.responses.has("/api/dashboard")'),true);
  assert.equal(h.saved().records.find(([key])=>key==='/api/dashboard')[1].payload.news[0].summary.length,6000000);
});

test('failed cache write keeps the last committed snapshot and invalidation cannot resurrect old data',async()=>{
  const h=diskFixture();h.run('rememberStartupResponse("/api/dashboard",{revision:1})');await h.run('flushStartupMemory()');
  const first=h.saved();h.fail();h.run('rememberStartupResponse("/api/dashboard",{revision:2})');await h.run('flushStartupMemory()');
  assert.deepEqual(h.saved(),first);
  const fresh=diskFixture();fresh.run('rememberStartupResponse("/api/dashboard",{news:[{summary:"x".repeat(2000000)}]});invalidateStartupMemory()');
  await fresh.run('flushStartupMemory()');assert.deepEqual(fresh.saved().records,[]);
});

test('read responses are allowlisted, capped and do not fetch again',()=>{
  const {run,timers}=setup();
  run('for(let i=0;i<40;i++)rememberStartupResponse(`/api/news?language=zh&offset=${i}`, {items:[]});rememberStartupResponse("/api/social/settings", {token:"secret"})');
  assert.equal(run('startupMemory.responses.size'),24);
  assert.equal(run('startupMemory.responses.has("/api/social/settings")'),false);
  assert.ok(timers.length<=1);
});

test('credential fields are removed recursively but configuration flags remain',()=>{
  const {run}=setup();
  const value=JSON.parse(run('JSON.stringify(startupSafePayload({token:"secret",a:{password:"p",api_key:"key",api_key_configured:true},news:[{title:"headline",access_token:"token"}]}))'));
  assert.deepEqual(value,{a:{api_key_configured:true},news:[{title:'headline'}]});
});

test('asset preview preserves prices and original time, removes actionable predictions and alerts',()=>{
  const {run}=setup();
  const result=JSON.parse(run('JSON.stringify(startupPreview("/api/assets",{saved_at:"2026-10-07T10:00:00Z",payload:{assets:[{price:2000,price_time:"2026-10-07T09:59:00Z",quote_status:"live",anomaly:{triggered:true}}],probabilities:[{available:true,up_probability:90,signal:"看多"}],outlooks:[{forecast:{available:true,up_probability:90}}],runtime:{last_updated_at:"2026-10-07T09:59:00Z"}}}))'));
  assert.equal(result.assets[0].price,2000);assert.equal(result.assets[0].price_time,'2026-10-07T09:59:00Z');
  assert.equal(result.assets[0].quote_status,'stale');assert.equal(result.assets[0].anomaly.triggered,false);
  assert.equal(result.probabilities[0].up_probability,null);assert.equal(result.outlooks[0].forecast.available,false);
  assert.equal(result.runtime.last_updated_at,'2026-10-07T09:59:00Z');
});

test('index memory preserves original close but marks only replayed quotes as stale',()=>{
  const {run}=setup();
  run('rememberStartupResponse("/api/indices",{schema_version:1,quotes:[{price:200,status:"closed",quote_time:"2026-10-13T20:00:00Z"}]})');
  assert.equal(run('startupMemory.responses.get("/api/indices").payload.quotes[0].status'),'closed');
  const result=JSON.parse(run('JSON.stringify(startupPreview("/api/indices",startupMemory.responses.get("/api/indices")))'));
  assert.equal(result.quotes[0].price,200);
  assert.equal(result.quotes[0].quote_time,'2026-10-13T20:00:00Z');
  assert.equal(result.quotes[0].status,'stale');
  assert.equal(result.quotes[0].restored_from_cache,true);
});

test('portfolio, Korea and ADR previews are explicitly stale',()=>{
  const {run}=setup();
  const result=JSON.parse(run('JSON.stringify(startupPreview("/api/dashboard",{saved_at:"2026-10-07T10:00:00Z",payload:{portfolio:{runtime:{},summary:{principal_return_pct:40},positions:[{quote_status:"live",daily_pnl_cny:30}]},korea_quotes:{quotes:[{status:"live"}]},watchlist:[{adr_premium:{status:"live"}}]}}))'));
  assert.equal(result.portfolio.positions[0].quote_status,'stale');assert.equal(result.portfolio.positions[0].daily_pnl_cny,null);
  assert.equal(result.portfolio.summary.principal_return_pct,null);assert.equal(result.portfolio.runtime.restored_from_cache,true);
  assert.equal(result.korea_quotes.quotes[0].status,'stale');assert.equal(result.watchlist[0].adr_premium.status,'stale');
});

test('social preview does not advertise previous source connections as currently verified',()=>{
  const {run}=setup();
  const result=JSON.parse(run('JSON.stringify(startupPreview("/api/social?hours=24",{saved_at:"2026-10-07T10:00:00Z",payload:{runtime:{connected_count:3,can_refresh:true},sources:[{status:"connected"}]}}))'));
  assert.equal(result.runtime.connected_count,0);assert.equal(result.runtime.can_refresh,false);assert.equal(result.sources[0].status,'stale');
});

test('mutations clear display data while leaving small UI preferences alone',async()=>{
  const {run,local}=setup();local.set('quantify.startup.preferences.v1','{"view":"research"}');
  run('rememberStartupResponse("/api/news",{items:[]});invalidateStartupMemory()');
  assert.equal(run('startupMemory.responses.size'),0);assert.ok(local.has('quantify.startup.preferences.v1'));
  await run('flushStartupMemory()');
});

test('API mutation is blocked during restored startup, live dashboard confirms configuration',async()=>{
  const {run,context}=setup();
  context.AbortController=AbortController;context.setTimeout=setTimeout;context.clearTimeout=clearTimeout;
  context.window.setTimeout=setTimeout;context.window.clearTimeout=clearTimeout;
  context.fetch=async()=>({ok:true,json:async()=>({stats:{}})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/quant_news_monitor/ui/app.js'),'utf8'),context);
  await assert.rejects(run('api("/api/portfolio/clear",{method:"POST"})'),/正在确认当前配置/);
  await run('api("/api/dashboard?details=0")');assert.equal(run('startupMemory.ready'),true);
  assert.equal(run('startupMemory.responses.has("/api/dashboard")'),true);
  await run('api("/api/portfolio/clear",{method:"POST"})');assert.equal(run('startupMemory.responses.size'),0);
  context.window.clearTimeout(run('startupMemory.timer'));
});

test('offline refresh leaves the last successful startup record intact',async()=>{
  const h=setup();Object.assign(h.context,{AbortController,setTimeout,clearTimeout});
  Object.assign(h.context.window,{setTimeout,clearTimeout});
  h.context.fetch=async()=>{throw new Error('offline');};
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/quant_news_monitor/ui/app.js'),'utf8'),h.context);
  h.run('startupMemory.responses.set("/api/dashboard",{saved_at:new Date().toISOString(),payload:{news:[{title:"Last successful record"}]}})');
  await assert.rejects(h.run('api("/api/dashboard")'),/offline/);
  assert.equal(h.run('startupMemory.responses.get("/api/dashboard").payload.news[0].title'),'Last successful record');
});

test('versioned display cache is loaded before initial network refresh and selected module refreshes',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../src/quant_news_monitor/ui/app.js'),'utf8');
  const init=source.slice(source.indexOf('async function init()'));
  assert.ok(init.indexOf('await restoreStartupMemory()')<init.indexOf('refreshDashboard()'));
  assert.match(init,/state.currentView === "research"/);assert.match(init,/state.currentView === "social"/);
});

test('malformed, incompatible and expired display records fall back without breaking startup',async()=>{
  for(const bundle of [{version:99,records:[]},{version:1,records:[null,['/api/dashboard',null],['/api/dashboard',{saved_at:'bad',payload:{}}]]},
    {version:1,records:[['/api/dashboard',{saved_at:'2000-01-01',payload:{}}]]}]) {
    const {run,context}=setup();context.state={currentView:'overview',newsLanguage:'zh'};
    context.window.addEventListener=()=>{};let rendered=0;
    for(const name of ['renderDashboard','renderAssets','renderOverview','applyNewsFilters','renderConnection'])context[name]=()=>rendered++;
    context.bundle=bundle;run('startupRead=async()=>bundle');
    await run('restoreStartupMemory()');assert.equal(rendered,0);assert.equal(run('startupMemory.responses.size'),0);
  }
});

test('unavailable IndexedDB is nonfatal and restored jobs are not presented as running',async()=>{
  const {run,context}=setup();context.window.indexedDB={open(){throw Error('disabled');}};
  run('rememberStartupResponse("/api/dashboard",{runtime:{}})');await run('flushStartupMemory()');
  const result=JSON.parse(run('JSON.stringify(startupPreview("/api/dashboard",{saved_at:new Date().toISOString(),payload:{runtime:{scan_in_progress:true,movement_scan_in_progress:true},trump:{runtime:{connected:true,refresh_in_progress:true}}}}))'));
  assert.equal(result.runtime.scan_in_progress,false);assert.equal(result.trump.runtime.connected,false);
  assert.equal(result.trump.runtime.refresh_in_progress,false);
});

test('late cache restores full data but never replaces live responses or a new generation',async()=>{
  const h=diskFixture();h.context.state={currentView:'overview',newsLanguage:'zh'};
  h.context.rendered=[];
  for(const name of ['renderAssets','renderOverview','applyNewsFilters','renderConnection'])h.context[name]=()=>{};
  h.context.renderDashboard=value=>h.context.rendered.push(value);
  h.context.record=await h.run('encodeStartupRecord({saved_at:new Date().toISOString(),payload:{news:[{summary:"x".repeat(3000000)}]}})');
  h.run('const bundle={version:1,records:[["/api/dashboard",record]]};let apiReadGeneration=0');
  await h.run('restoreStartupBundle(bundle,null,0)');
  assert.equal(h.context.rendered[0].news[0].summary.length,3000000);
  h.run('rememberStartupResponse("/api/dashboard",{news:[{summary:"live"}]})');
  await h.run('restoreStartupBundle(bundle,null,0)');
  assert.equal(h.context.rendered.length,1);
  assert.equal(h.run('startupMemory.responses.get("/api/dashboard").payload.news[0].summary'),'live');
  h.run('startupMemory.ready=false;startupMemory.responses.clear();apiReadGeneration=1');
  await h.run('restoreStartupBundle(bundle,null,0)');
  assert.equal(h.run('startupMemory.responses.size'),0);
});

test('live section arriving during another section decode is never rendered as stale',async()=>{
  const h=setup();h.context.state={currentView:'overview',newsLanguage:'zh'};
  let renders=0;h.context.renderAssets=()=>renders++;
  for(const name of ['renderDashboard','renderOverview','applyNewsFilters','renderConnection'])h.context[name]=()=>{};
  h.run(`let apiReadGeneration=0;
    decodeStartupRecord=async record=>{
      if(record.payload.news) rememberStartupResponse('/api/assets',{assets:[{price:200,quote_status:'live'}]});
      return record;
    };
    const bundle={version:1,records:[
      ['/api/assets',{saved_at:new Date().toISOString(),payload:{assets:[{price:100}]}}],
      ['/api/news?language=zh',{saved_at:new Date().toISOString(),payload:{news:[]}}]
    ]};`);
  await h.run('restoreStartupBundle(bundle,null,0)');
  assert.equal(renders,0);
  assert.equal(h.run('startupMemory.responses.get("/api/assets").payload.assets[0].price'),200);
});
