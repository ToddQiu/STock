const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function setup() {
  class Element {
    constructor(){this.children=[];this.dataset={};this.style={};this.value='';this.classList={toggle(){}};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    setAttribute(key,value){this[key]=value;}
    addEventListener(key,handler){this[key]=handler;}
    showModal(){this.open=true;}
  }
  const nodes=new Map(), buttons=[new Element(),new Element()], storage=new Map();
  const context=vm.createContext({console,Date,URLSearchParams,
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},window:{},
    document:{addEventListener(){},createElement:()=>new Element(),createElementNS:()=>new Element(),
      querySelectorAll:selector=>selector==='[data-movement-sort]'?buttons:[],
      getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);}}});
  const run=code=>vm.runInContext(code,context);
  for(const file of ['app.js','startup_memory.js','workbench.js'])run(fs.readFileSync(path.resolve(__dirname,'../src/quant_news_monitor/ui',file),'utf8'));
  run('renderMovementTable=rows=>globalThis.rendered=rows');
  return {run,context,nodes,buttons,storage};
}

test('movement sorting uses signed values, stable ties and missing-last in both directions',()=>{
  const {run,context}=setup();
  context.sample=[{symbol:'up',market:'US',change_pct:'10.5'},{symbol:'down',market:'US',change_pct:-4},
    {symbol:'zero',market:'US',change_pct:0},{symbol:'tie',market:'HK',change_pct:-4},
    ...[null,undefined,'',NaN,Infinity,true,{},'bad'].map((value,i)=>({symbol:`missing${i}`,market:'US',change_pct:value}))];
  const original=context.sample.map(row=>row.symbol).join();
  run('state.dashboard={movements:sample};applyMovementFilters()');
  assert.deepEqual(Array.from(run('rendered.slice(0,4).map(row=>row.symbol)')),['up','zero','down','tie']);
  run('state.movementSortDirection="asc";applyMovementFilters()');
  assert.deepEqual(Array.from(run('rendered.slice(0,4).map(row=>row.symbol)')),['down','tie','zero','up']);
  assert.ok(Array.from(run('rendered.slice(4)')).every(row=>row.symbol.startsWith('missing')));
  assert.equal(context.sample.map(row=>row.symbol).join(),original);
  run('state.movementMarket="HK";applyMovementFilters()');
  assert.deepEqual(Array.from(run('rendered.map(row=>row.symbol)')),['tie']);
});

test('sort buttons, persisted state and new response use the selected direction',()=>{
  const {run,buttons,storage}=setup();
  run('state.dashboard={movements:[{symbol:"up",change_pct:1},{symbol:"down",change_pct:-1}]};bindMovementSort()');
  buttons[0].click();
  assert.equal(run('state.movementSortDirection'),'asc');
  assert.equal(buttons[1].dataset.direction,'asc');
  assert.match(buttons[1]['aria-label'],/升序/);
  assert.equal(JSON.parse(storage.get('quantify.startup.preferences.v1')).state.movementSortDirection,'asc');
  run('state.dashboard={movements:[{symbol:"up",change_pct:3},{symbol:"down",change_pct:-2}]};applyMovementFilters()');
  assert.equal(run('rendered[0].symbol'),'down');
  run('applyStartupPreferences({version:1,state:{movementSortDirection:"desc"}})');
  assert.equal(run('state.movementSortDirection'),'desc');
  run('applyStartupPreferences({version:1,state:{movementSortDirection:"invalid"}})');
  assert.equal(run('state.movementSortDirection'),'desc');
});

test('event center merges news alerts without losing company-specific attribution',()=>{
  const {run}=setup();
  const output=JSON.parse(run(`JSON.stringify(combinedEvents([
    {market:'US',symbol:'AAPL',url:'https://example.com/a',title:'Article',score:1},
    {market:'US',symbol:'MSFT',url:'https://example.com/a',title:'Article',score:-1}
  ],[{market:'US',symbol:'AAPL',url:'https://example.com/a',alert_created_at:'2026-10-14T09:00:00Z'}],
  [{market:'US',symbol:'AAPL',change_pct:2,detected_at:'2026-10-14T09:01:00Z'}]))`));
  assert.equal(output.length,3);
  assert.equal(output[0].alert,true);
  assert.equal(output[0].item.score,1);
  assert.equal(output[1].item.score,-1);
  assert.equal(output[2].kind,'movement');
});

test('movement rows suppress speculative legacy prose and untranslated headline filler',()=>{
  const {run,context}=setup();
  context.item={reason_category:'earnings',confidence:.9,reason_summary:'最可能由资金技术面共同作用',evidence_url:'https://example.com/news'};
  assert.equal(run('movementExplanation(item).ready'),false);
  assert.equal(run('movementExplanation(item).label'),'原因未核实');
  run('globalThis.container=el("div");appendMovementExplanation(container,item)');
  assert.equal(run('container.children.length'),0);
  context.item.reason_details={version:2,status:'linked',facts:[{text:'Avalo earnings growth'}],summary:'盈利改善可能影响预期。',relation:'aligned',relation_text:'尚未验证因果'};
  assert.equal(run('movementExplanation(item).label'),'中文证据整理中');
  run('item.reason_details.facts[0].text_zh="报道显示盈利改善"');
  assert.equal(run('movementExplanation(item).ready'),true);
  assert.equal(run('movementExplanation(item).translated'),true);
  run('appendMovementExplanation(container,item)');
  assert.equal(run('container.children.length'),3);
  assert.doesNotMatch(run('container.children.map(n=>n.textContent).join()'),/最可能|共同作用|Avalo earnings/);
  run('item.reason_details.relation="opposed"');
  assert.match(run('movementExplanation(item).label'),/反向证据/);
  run('item.evidence_url="javascript:alert(1)"');
  assert.equal(run('movementExplanation(item).ready'),false);
});

test('mixed evidence cannot hide untranslated counterevidence behind a numeric target revision',()=>{
  const {run,context}=setup();
  context.item={evidence_url:'https://example.com/news',reason_details:{version:2,status:'linked',relation:'mixed',
    fact_summary:'机构上调目标价',summary:'正反证据并存，净影响尚不明确',
    facts:[{text:'Price target raised',text_zh:'目标价上调'}, {text:'Company cuts guidance'}]}};
  assert.equal(run('movementExplanation(item).ready'),false);
  run('item.reason_details.facts[1].text_zh="公司下调业绩指引"');
  assert.equal(run('movementExplanation(item).label'),'正反证据并存');
  assert.match(run('movementExplanation(item).facts'),/目标价上调.*公司下调业绩指引/);
  assert.equal(run('movementExplanation(item).translated'),true);
  run('item.reason_details.version=1');
  assert.equal(run('movementExplanation(item).ready'),false,'cached old reasoning must be rechecked');
});

test('desktop navigation removes requested KPI strip, retains detail views and has unique ids',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../src/quant_news_monitor/ui/index.html'),'utf8');
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(row=>row[1]);
  assert.equal(new Set(ids).size,ids.length);
  assert.doesNotMatch(html,/id="metric-(watch|news|bullish|bearish|alerts)"/);
  for(const view of ['signals','feed','movements','alerts','research','indices','korea-stocks'])assert.ok(html.includes(`data-view="${view}"`),view);
  assert.match(html,/<details class="nav-market" id="nav-research">/);
  assert.match(html,/id="research-tabs" class="nav-sublist"/);
});

test('portfolio risk history keeps separate crossing identities and links to held positions',()=>{
  const {run}=setup();
  const result=JSON.parse(run(`JSON.stringify(combinedEvents([],[],[],[
    {id:'US:TEST:stop_price:first',market:'US',symbol:'TEST',created_at:'2026-10-16T10:00:00Z'},
    {id:'US:TEST:stop_price:second',market:'US',symbol:'TEST',created_at:'2026-10-16T11:00:00Z'}]))`));
  assert.equal(result.length,2);
  assert.ok(result.every(event=>event.alert && event.kind==='portfolio'));
  run('setView=view=>globalThis.openView=view;openPositionRisk=item=>globalThis.openRisk=item.symbol;showToast=text=>globalThis.notice=text;state.dashboard={portfolio:{positions:[{market:"US",symbol:"TEST"}]}}');
  run('openPortfolioAlert({market:"US",symbol:"TEST"})');
  assert.equal(run('openView'),'portfolio');
  assert.equal(run('openRisk'),'TEST');
  run('openPortfolioAlert({market:"US",symbol:"REMOVED"})');
  assert.match(run('notice'),/已删除/);
});

test('source diagnostics summarize repeated failures without exposing raw exception text',()=>{
  const {run,context}=setup();
  context.failures=[
    '数据源 bing-news-rss 失败: RuntimeError: SPCX: OSError: www.bing.com 暂不可用（URLError），30 秒后重试',
    '数据源 bing-news-rss 部分失败: AAPL: OSError: SSL EOF',
    '数据源 google-news-zh-rss 失败: TimeoutError: timed out',
    '数据源 yahoo-finance-rss 失败: HTTPError 429',
    '数据源 custom-source 失败: HTTPError 403',
    '分析 MSFT / test 失败: invalid result'
  ];
  assert.deepEqual(Array.from(run("diagnosticMessages('news',failures)")),[
    'Bing 新闻：连接暂时中断','Google 中文新闻：连接超时','Yahoo 财经：访问频率受限','新闻来源：来源拒绝访问','新闻分析：更新未完成'
  ]);
  assert.equal(run("diagnosticMessages('portfolio',['MU: URLError: SSL EOF'])[0]"),'持仓报价：连接暂时中断');
  assert.equal(run("diagnosticMessages('news',['<img onerror=alert(1)>'])[0]"),'新闻更新：更新未完成');
});

test('sidebar preserves runtime states after removing duplicate compass status',()=>{
  const {run,context,nodes}=setup();
  run(`document.getElementById('sidebar-status-dot').classList.add=()=>{};
    updateScanButton=value=>globalThis.scanRunning=value;
    updateMovementButton=value=>globalThis.movementRunning=value;`);
  for(const [data,title] of [
    [{_startup_cache:true,runtime:{}},'已恢复快照'],
    [{runtime:{scan_in_progress:true}},'检测进行中'],
    [{runtime:{movement_scan_in_progress:true}},'检测进行中'],
    [{runtime:{last_run:{errors:['source unavailable']}}},'数据源警告'],
    [{runtime:{last_movement_run:{errors:['source unavailable']}}},'数据源警告'],
    [{runtime:{last_run:{finished_at:'2026-10-26T10:00:00+08:00'}}},'系统正常'],
    [{runtime:{}},'系统就绪']
  ]){
    context.data=data;
    run('renderRuntime(data)');
    assert.equal(nodes.get('sidebar-status').textContent,title);
    assert.equal(run('scanRunning'),Boolean(data.runtime.scan_in_progress));
    assert.equal(run('movementRunning'),Boolean(data.runtime.movement_scan_in_progress));
  }
  assert.equal(nodes.has('runtime-copy'),false);
});

test('header connection status retains complete text and retry state',()=>{
  const {run,nodes}=setup();
  run('startupMemory.previewAt=null;startupMemory.storageError="";renderConnection()');
  assert.match(nodes.get('connection-message').textContent,/本地服务已连接/);
  assert.equal(nodes.get('connection-retry').hidden,true);
  run('renderConnection(new Error("connection unavailable"))');
  assert.equal(nodes.get('connection-strip').className,'connection-strip connection-error');
  assert.match(nodes.get('connection-message').textContent,/总览同步失败/);
  assert.equal(nodes.get('connection-message').title,nodes.get('connection-message').textContent);
  assert.equal(nodes.get('connection-retry').hidden,false);
  run('renderConnection()');
  assert.equal(nodes.get('connection-strip').className,'connection-strip');
  assert.equal(nodes.get('connection-retry').hidden,true);
});

test('fresh news status wins over stale errors from an older response or startup cache',()=>{
  const {run,context}=setup();
  context.older={generated_at:'2026-10-23T10:40:00+08:00',runtime:{last_run:{finished_at:'2026-10-23T10:39:00+08:00',errors:['old']}}};
  context.newer={generated_at:'2026-10-23T10:56:00+08:00',runtime:{last_run:{finished_at:'2026-10-23T10:55:00+08:00',errors:[]}}};
  assert.equal(run('latestNewsStatus(newer,older)'),context.newer);
  assert.equal(run('latestNewsStatus(older,newer)'),context.newer);
  assert.equal(run('latestNewsStatus(older,{...newer,_startup_cache:true})'),context.older);
  assert.equal(run('latestNewsStatus(newer,null)'),context.newer);
  assert.equal(run('Object.keys(latestNewsStatus(null,null)).length'),0);
});

test('overview diagnostic links do not repeat provider failures and still open full diagnostics',()=>{
  const {run,nodes}=setup();
  run('uiIcon=()=>el("span");setView=view=>globalThis.openView=view;globalThis.notice=diagnosticNotice("portfolio",["MU: URLError: SSL EOF"])');
  const notice=run('notice');
  assert.equal(notice.children.length,1);
  assert.equal(notice.children[0].textContent,'数据状态');
  assert.doesNotMatch(JSON.stringify(notice),/上轮|SSL|EOF|连接暂时中断/);
  const heading={scrollIntoView(){},focus(){this.focused=true;}};nodes.set('data-status-portfolio',heading);
  notice.children[0].click();assert.equal(run('openView'),'settings');assert.equal(heading.focused,true);
  assert.equal(nodes.get('settings-diagnostics-dialog').open,true);
  assert.equal(run('diagnosticMessages("portfolio",["MU: URLError: SSL EOF"])[0]'),'持仓报价：连接暂时中断');
});

test('cross-asset headers expose intraday charts without repeated descriptions or source blocks',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../src/quant_news_monitor/ui/index.html'),'utf8');
  const assetSection=html.slice(html.indexOf('data-view="assets"'),html.indexOf('data-view="indices"'));
  assert.doesNotMatch(assetSection,/data-instrument-target="ASSET:/);
  const {run,context,nodes}=setup();
  run('uiIcon=()=>el("span");window.requestAnimationFrame=()=>{};openPortfolioChart=(item,period)=>globalThis.openedChart={item,period};instrumentBriefNode=()=>{throw Error("Removed asset description rendered")};');
  context.assets=['GOLD','BTC','OIL','US5Y'].map((key,i)=>({key,name:key,subtitle:'test',symbol:key,price:100,
    high_24h:110,low_24h:90,change_24h:2,change_bps:3.7,price_time:'2026-10-26T09:00:00+08:00',
    quote_status:i===3?'closed':i===2?'stale':'live',source:'Public source',source_url:'https://example.com/'+key,
    contract_name:'December contract',quote_note:'Reference contract details',chart:[]}));
  run('renderAssetPriceCards(assets)');
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  for(const key of ['GOLD','BTC','OIL','US5Y']){
    const card=nodes.get('asset-price-'+key.toLowerCase()).children[0];
    const all=descendants(card),header=card.children[0];
    const button=descendants(header).find(n=>n.className==='asset-chart-button');
    assert.ok(button,key+' chart must be beside the name');
    assert.equal(button.children[1].textContent,'分时图');
    assert.match(button['aria-label'],/分时图$/);
    button.click();
    assert.equal(run('openedChart.period'),'intraday');
    assert.equal(run('openedChart.item.symbol'),key);
    assert.equal(run('openedChart.item.market'),'ASSET');
    assert.equal(all.some(n=>['asset-source-link','asset-quote-note','instrument-brief'].includes(n.className)),false);
    const source=all.find(n=>n.className==='asset-quote-time');
    assert.equal(source.href,'https://example.com/'+key);
    assert.match(source.title,/December contract.*Reference contract details/);
    assert.equal(all.filter(n=>n.className==='asset-sparkline').length,1);
    if(key==='US5Y'){
      assert.match(run('openedChart.item.chartBrief'),/不是债券价格/);
      assert.ok(all.some(n=>n.textContent==='+3.70 bp'));
      assert.ok(all.some(n=>n.textContent==='报价时间 · 休市'));
    }
    if(key==='OIL')assert.ok(all.some(n=>n.textContent==='报价时间 · 上次报价'));
    if(key==='GOLD')assert.match(run('openedChart.item.chartBrief'),/不是上方的 XAU\/USD/);
  }
});

test('overview quotes merge percent moves and yield basis points without inventing missing changes',()=>{
  const {run,nodes,context}=setup();
  run('renderDataDiagnostics=()=>{};state.dashboard={};state.assets={assets:[]}');
  const changes=()=>{
    run('renderFreshnessStatus()');
    return nodes.get('overview-freshness').children[1].children[1].children.map(row=>row.children[1]);
  };
  assert.deepEqual(changes().map(n=>n.textContent),['--','--','--','--']);
  run('state.assets.assets=[{key:"GOLD",change_24h:-2.32},{key:"BTC",change_24h:0},{key:"OIL",change_24h:1.85},{key:"US5Y",change_24h:99,change_bps:-20.4}]');
  assert.deepEqual(changes().map(n=>n.textContent),['-2.32%','+0.00%','+1.85%','-20.40 bp']);
  assert.match(changes()[1].className,/neutral/);
  assert.match(changes()[3]['aria-label'],/收益率变化/);
  for(const value of [undefined,null,'',' ',true,NaN,Infinity,'invalid']){
    context.invalid=value;run('state.assets.assets=[{key:"US5Y",change_bps:invalid}]');
    assert.equal(changes()[3].textContent,'--');
  }
  run('state.assets.assets=[{key:"US5Y",change_bps:"4.2"}]');
  assert.equal(changes()[3].textContent,'+4.20 bp');
});

test('compass research shortcuts reuse existing tab actions',()=>{
  const {run}=setup();
  run('uiIcon=()=>({});document.querySelector=selector=>({click:()=>globalThis.clickedTab=selector});setView=view=>globalThis.openView=view');
  run('compassDestination("财报中心","", "research","southeast","earnings").click()');
  assert.equal(run('clickedTab'),'#research-tabs [data-tab="earnings"]');
  run('compassDestination("回测系统","", "backtest","southwest").click()');
  assert.equal(run('openView'),'backtest');
  run('compassDestination("持仓组合","", "portfolio","northwest").click()');
  assert.equal(run('openView'),'portfolio');
});

test('unified overview retains all eight shortcuts and ignores obsolete layout preferences',()=>{
  const {run,nodes}=setup();
  run('uiIcon=()=>({});document.querySelector=selector=>({click:()=>globalThis.clickedTab=selector});setView=view=>globalThis.openView=view');
  const html=fs.readFileSync(path.resolve(__dirname,'../src/quant_news_monitor/ui/index.html'),'utf8');
  assert.doesNotMatch(html,/id="overview-layout"|data-layout=/);
  assert.match(html,/class="overview-workspace">\s*<div class="overview-navigation">/);
  for (const layout of ['list','compass']) {
    run(`applyStartupPreferences({version:1,presentationVersion:2,overviewLayout:'${layout}',state:{movementSortDirection:'asc'}});renderCompass()`);
    assert.equal(run('state.movementSortDirection'),'asc');
    assert.equal(run('"overviewLayout" in captureStartupPreferences()'),false);
    const [center,...shortcuts]=nodes.get('overview-compass').children[0].children;
    assert.equal(center.className,'compass-center');assert.equal(shortcuts.length,8);
    for (const [index,target] of ['portfolio','assets','indices','signals','social','backtest','cards','earnings'].entries()) {
      assert.ok(shortcuts[index]['aria-label']);assert.ok(shortcuts[index].title);
      shortcuts[index].click();
      assert.equal(run(index<6?'openView':'clickedTab'),index<6?target:`#research-tabs [data-tab="${target}"]`);
    }
  }
});

test('holding interpretation renders outside the compass and retains empty-state information',()=>{
  const {run,nodes}=setup();
  run('state.dashboard={portfolio:{positions:[]}};renderHoldingFocus([])');
  assert.match(nodes.get('holdings-events').children[0].children[0].textContent,/尚未录入/);
  run('state.dashboard.portfolio.positions=[{market:"US",symbol:"MU"}];renderHoldingFocus([])');
  assert.match(nodes.get('holdings-events').children[0].children[0].textContent,/暂无匹配/);
});

test('holding explanations retain supported directions but guard missing, conflicting and legacy evidence',()=>{
  const {run,context}=setup();
  const interpret=item=>{context.item=item;return JSON.parse(run('JSON.stringify(holdingEventInterpretation(item))'));};
  const positive=interpret({score:.5,evidence_status:'supported',analysis_summary:'已披露经营改善，后续现金流仍需验证。'});
  assert.equal(positive.tone,'positive');assert.match(positive.analysis,/现金流/);
  assert.equal(interpret({score:-.5,evidence_status:'supported'}).tone,'negative');
  assert.equal(interpret({score:0,evidence_status:'supported'}).label,'中性观察');
  for(const evidence_status of ['mixed','insufficient','legacy',undefined]){
    const note=interpret({score:.99,evidence_status,analysis_summary:'不能直接沿用的强利多结论'});
    assert.equal(note.tone,'neutral');assert.doesNotMatch(note.analysis,/强利多/);
  }
  assert.equal(interpret({score:1,evidence_status:'supported',analysis_metadata:{direction_eligible:false}}).label,'方向待确认');
  for(const score of [null,'',NaN,Infinity])assert.equal(interpret({score,evidence_status:'supported'}).tone,'neutral');
});

test('holding follow-ups distinguish fund flows, event types and missing expectation baselines',()=>{
  const {run}=setup();
  assert.match(run('holdingEventInterpretation({},{name:"恒生科技ETF"}).watch'),/融资买卖额不等于基金申购赎回/);
  assert.match(run('holdingEventInterpretation({event_type:"guidance"}).watch'),/新旧区间/);
  assert.match(run('holdingEventInterpretation({event_type:"earnings"}).watch'),/一次性项目/);
  const risks=Array.from(run('holdingEventInterpretation({risk_flags:["仅有标题","仅有标题",null,""]}).risks'));
  assert.equal(risks.filter(text=>text==='仅有标题').length,1);assert.ok(risks.some(text=>/预期基准/.test(text)));
  assert.equal(run('holdingEventInterpretation({analysis_metadata:{expected_baseline_available:true}}).risks.some(text=>/预期基准/.test(text))'),false);
});

test('expanded holding events keep source, translation, safe text and working detail links',()=>{
  const {run,context}=setup();
  context.item={market:'US',symbol:'AAPL',title:'Apple earnings update',title_zh:'苹果业绩更新',news_summary:'Revenue update',news_summary_zh:'收入更新',
    evidence_status:'supported',score:.5,url:'https://example.com/report',analysis_summary:'<img src=x onerror=alert(1)>',source:'公告',published_at:'2026-10-23T09:00:00+08:00'};
  run('openDetail=item=>globalThis.opened=item;globalThis.article=renderHoldingEvent(item,{market:"US",symbol:"AAPL",name:"苹果",quantity:10},{name:"测试组合"})');
  const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(run('article'));
  assert.ok(nodes.some(node=>node.textContent==='中文译文（机器翻译）：苹果业绩更新'));
  assert.ok(nodes.some(node=>node.textContent==='中文译文（机器翻译）：收入更新'));
  assert.ok(nodes.some(node=>node.textContent===context.item.analysis_summary&&!node.innerHTML));
  assert.ok(nodes.some(node=>/测试组合.*持有 10 股/.test(node.textContent||'')));
  const link=nodes.find(node=>node.className==='holding-event-source');assert.equal(link.href,context.item.url);assert.equal(link.rel,'noopener noreferrer');
  nodes.find(node=>node.className==='event-title').click();assert.equal(context.opened,context.item);
  run('item.url="javascript:alert(1)";delete item.news_summary;globalThis.article=renderHoldingEvent(item,{market:"US",symbol:"AAPL"},{})');
  const missing=flatten(run('article'));
  assert.equal(missing.some(node=>node.className==='holding-event-source'),false);
  assert.ok(missing.some(node=>/当前仅有新闻标题/.test(node.textContent||'')));
});

test('overview reads the whole portfolio directory even when the selected account is empty',()=>{
  const {run,context}=setup();
  context.data={portfolio:{id:'default',positions:[],summary:{position_count:0}},portfolios:[
    {id:'default',name:'默认组合',summary:{position_count:0}},
    {id:'team',name:'示例组合甲',summary:{position_count:8}}
  ]};
  assert.deepEqual(Array.from(run('overviewPortfolioBooks(data).map(b=>b.summary.position_count)')),[0,8]);
  assert.equal(run('overviewPortfolioBooks({}).length'),0);
  assert.equal(run('overviewPortfolioBooks({portfolio:{positions:[],summary:{position_count:8}}})[0].summary.position_count'),8);
});

test('portfolio overview keeps cost returns, current-holding price moves and principal differences separate',()=>{
  const {run,context}=setup();
  context.book={base_currency:'USD',summary:{position_count:2,base_valuation_complete:true,stale_valued_count:0,
    principal_base:1000,net_assets_base:1320,total_market_value_base:1100,cash_base:220,
    total_cost_base:1000,total_pnl_base:100,total_pnl_pct:10,daily_pnl_base:100,daily_pnl_complete:true,
    principal_status:'available',principal_pnl_cny:999999}};
  const before=JSON.stringify(context.book);
  const metrics=JSON.parse(run('JSON.stringify(overviewPortfolioMetrics(book))'));
  assert.equal(metrics.costReturn,10);assert.equal(metrics.dailyReturn,10);
  assert.equal(metrics.capitalChange,320);assert.equal(metrics.capitalReturn,32);
  assert.equal(metrics.principal,1000);assert.equal(metrics.netAssets,1320);
  assert.equal(JSON.stringify(context.book),before);
  run('book.summary.principal_status="unavailable"');
  assert.equal(run('overviewPortfolioMetrics(book).capitalChange'),null,'A complete reference valuation is not a verified principal return');
  assert.match(run('overviewPortfolioMetrics(book).status'),/参考估值/);
  run('book.summary.principal_base=null;book.summary.principal_status="unset"');
  assert.equal(run('overviewPortfolioMetrics(book).capitalChange'),null);
  assert.equal(run('overviewPortfolioMetrics(book).costReturn'),10);
  run('book.summary.principal_base=2000;book.summary.principal_status="available"');
  assert.equal(run('overviewPortfolioMetrics(book).capitalChange'),-680);
  assert.equal(run('overviewPortfolioMetrics(book).capitalReturn'),-34);
});

test('cached, partial and missing portfolio data never become current returns or fabricated zeroes',()=>{
  const {run,context}=setup();
  context.book={summary:{position_count:8,base_valuation_complete:true,stale_valued_count:0,principal_base:100,
    net_assets_base:110,total_market_value_base:110,total_pnl_pct:10,total_pnl_base:10,
    daily_pnl_base:10,daily_pnl_complete:true,principal_status:'available'}};
  assert.equal(run('overviewPortfolioMetrics(book,true).capitalChange'),null);
  assert.equal(run('overviewPortfolioMetrics(book,true).dailyReturn'),null);
  assert.match(run('overviewPortfolioMetrics(book,true).status'),/上次估值/);
  run('book.summary.daily_pnl_complete=false');
  assert.equal(run('overviewPortfolioMetrics(book).dailyReturn'),null);
  run('book.summary.base_valuation_complete=false');
  assert.equal(run('overviewPortfolioMetrics(book).costReturn'),null);
  assert.equal(run('overviewPortfolioMetrics(book).netAssets'),null);
  run('book.summary.base_valuation_complete=true;book.summary.stale_valued_count=1');
  assert.equal(run('overviewPortfolioMetrics(book).capitalChange'),null);
  assert.equal(run('overviewPortfolioMetrics(book).costReturn'),null);
  assert.equal(run('overviewPortfolioMetrics({summary:{}}).principal'),null);
  assert.equal(run('overviewPortfolioMetrics({summary:{}}).costReturn'),null);
  run('book.summary.stale_valued_count=0;book.summary.daily_pnl_complete=true;book.summary.daily_pnl_base=110');
  assert.equal(run('overviewPortfolioMetrics(book).dailyReturn'),null,'zero previous value has no rate');
  run('book.summary.daily_pnl_base=0;book.summary.total_pnl_pct=0');
  assert.equal(run('overviewPortfolioMetrics(book).dailyReturn'),0);
  assert.equal(run('overviewPortfolioMetrics(book).costReturn'),0);
  run('book.summary.valuation_current=false');
  assert.equal(run('overviewPortfolioMetrics(book).capitalChange'),null);
  assert.equal(run('overviewPortfolioMetrics(book).dailyReturn'),null);
  assert.equal(run('overviewPortfolioMetrics(book).costReturn'),null);
  assert.match(run('overviewPortfolioMetrics(book).status'),/待行情确认/);
});

test('overview removes footer notes but retains valuation context and metric definitions on hover',()=>{
  const {run,context}=setup();
  context.data={_startup_cache:true,portfolios:[{id:'team',name:'示例组合甲',updated_at:'2026-10-26T00:06:00+08:00',
    summary:{position_count:8,base_valuation_complete:true,net_assets_base:825304.5,principal_base:800000}}]};
  const flatten=node=>[node,...node.children.flatMap(flatten)];
  const nodes=flatten(run('renderPortfolioOverview(data)'));
  assert.equal(nodes.some(n=>['overview-portfolio-time','overview-portfolio-basis'].includes(n.className)),false);
  const link=nodes.find(n=>n.dataset.overviewPortfolio==='team');
  assert.match(link.title,/上次估值 · 待行情确认/);
  assert.match(link.title,/10\/26 00:06/);
  assert.equal(link['aria-description'],link.title);
  assert.equal(nodes.filter(n=>n.dataset.metric).length,5);
  const row=key=>nodes.find(n=>n.dataset.metric===key);
  assert.match(row('cost-return').title,/相对成本/);
  assert.match(row('net-assets').title,/净资产含现金/);
  assert.match(row('capital-change').title,/未分离出入金，不等于投资收益/);
  assert.equal(row('cost-return').children[1].textContent,'--');
  assert.equal(row('capital-change').children[1].textContent,'待估值确认');
  const source=fs.readFileSync(path.resolve(__dirname,'../src/quant_news_monitor/ui/workbench.js'),'utf8');
  assert.doesNotMatch(source,/freshness-quote-note/);
  assert.match(source,/changeNode.title=key === "US5Y" \? "较前收的收益率变化/);
});

test('portfolio diagnostics move into the title row without losing their action',()=>{
  const {run,context}=setup();
  context.data={portfolio:{runtime:{errors:['connection failed']}},portfolios:[{id:'default',name:'默认组合',summary:{position_count:0}}]};
  run('uiIcon=name=>el("span",name);setView=view=>globalThis.openView=view;openSettingsDiagnostics=key=>globalThis.openDiagnostic=key');
  const cell=run('renderPortfolioOverview(data)');
  assert.equal(cell.children.length,2,'no separate footer row');
  assert.equal(cell.children[0].className,'overview-summary-heading');
  const button=cell.children[0].children[1].children[0];
  assert.match(button.className,/overview-diagnostic/);
  assert.equal(button.children[0].className,'activity');
  assert.match(button['aria-label'],/持仓行情/);
  button.click();
  assert.equal(run('openView'),'settings');
  assert.equal(run('openDiagnostic'),'portfolio');
});


test('live delayed valuations remain visible as references and partial daily totals show coverage',()=>{
  const {run,context}=setup();
  context.book={id:'team',name:'测试组合',runtime:{status:'live'},summary:{
    position_count:15,base_valuation_complete:true,valuation_current:false,stale_valued_count:15,
    principal_base:1000,net_assets_base:1100,total_market_value_base:1100,total_pnl_base:100,total_pnl_pct:10,
    principal_status:'unavailable',daily_pnl_complete:false,daily_pnl_count:11,daily_pnl_base:-25,
    daily_pnl_quote_time:'2026-11-12T16:00:00+08:00',daily_pnl_delayed_count:11}};
  const metrics=run('overviewPortfolioMetrics(book)');
  assert.equal(metrics.costReturn,null);
  assert.equal(metrics.referenceReturn,10);
  assert.equal(metrics.referenceCapital,100);
  assert.equal(metrics.partialDaily,-25);
  const flatten=node=>[node,...node.children.flatMap(flatten)];
  const nodes=flatten(run('renderPortfolioOverview({portfolios:[book]})'));
  const row=key=>nodes.find(n=>n.dataset.metric===key);
  assert.match(row('cost-return').children[0].textContent,/参考/);
  assert.match(row('cost-return').children[1].textContent,/10.00%/);
  assert.equal(row('daily-return').children[0].textContent,'前收11/15');
  assert.doesNotMatch(row('daily-return').children[1].textContent,/%/);
  assert.match(row('capital-change').children[0].textContent,/参考/);
  assert.equal(run('overviewPortfolioMetrics(book,true).referenceReturn'),null);
  run('book.summary.principal_base=null');
  assert.equal(run('overviewPortfolioMetrics(book).referenceCapital'),null);
  run('book.summary.base_valuation_complete=false');
  assert.equal(run('overviewPortfolioMetrics(book).referenceReturn'),null);
});

test('principal return shows delayed complete values as reference and keeps missing data unavailable',()=>{
  const {run,context}=setup();
  context.book={summary:{principal_base:1000000,net_assets_base:720033.3,principal_return_pct:null,principal_status:'unavailable',base_valuation_complete:true,valuation_current:false,stale_valued_count:7},runtime:{status:'partial',restored_from_cache:false}};
  const value=run('portfolioCapitalMetrics(book)');
  assert.ok(Math.abs(value.rate-(-27.99667))<1e-8);assert.equal(value.reference,true);
  assert.match(value.note,/参考/);
  for(const change of [{principal_base:null},{principal_base:0},{net_assets_base:null},{net_assets_base:Infinity},{base_valuation_complete:false}]){
    context.change=change;assert.equal(run('portfolioCapitalMetrics({...book,summary:{...book.summary,...change}}).rate'),null);
  }
  for(const status of ['stale','error','waiting']){
    context.status=status;assert.equal(run('portfolioCapitalMetrics({...book,runtime:{status}}).rate'),null);
  }
  assert.equal(run('portfolioCapitalMetrics(book,true).rate'),null);
  assert.equal(run('portfolioCapitalMetrics({...book,runtime:{status:"partial",restored_from_cache:true}}).rate'),null);
  assert.equal(run('portfolioCapitalMetrics({...book,summary:{...book.summary,net_assets_base:0}}).rate'),-100);
  assert.equal(run('portfolioCapitalMetrics({...book,summary:{...book.summary,principal_return_pct:5,principal_status:"available",valuation_current:true,stale_valued_count:0}}).reference'),false);
  assert.ok(Math.abs(run('overviewPortfolioMetrics(book).referenceCapital')-(-279966.7))<1e-7);
});

test('portfolio refresh uses a narrow snapshot and keeps unrelated dashboard data',async()=>{
  const {run,context}=setup();const paths=[];
  context.paths=paths;
  run(`state.currentView='portfolio';portfolioContext.id='book';state.dashboard={portfolio:{id:'book',positions:[{symbol:'old'}]},news:[{title:'kept'}],stats:{window_hours:72},runtime:{}};
    api=async path=>{paths.push(path);return {portfolio:{id:'book',positions:[]},portfolios:[]}};
    renderDashboard=data=>{state.dashboard=data};renderConnection=()=>{};`);
  await run('refreshDashboard()');
  assert.deepEqual(paths,['/api/portfolio/snapshot']);
  assert.equal(run('state.dashboard.portfolio.positions.length'),0);
  assert.equal(run('state.dashboard.news[0].title'),'kept');
  run(`state.currentView='overview';paths.length=0;api=async path=>{paths.push(path);return {portfolio:{id:'book'},news:[]}}`);
  await run('refreshDashboard()');assert.deepEqual(paths,['/api/dashboard?details=0']);
});

test('holding sorts distinguish amount and percent, keep unavailable estimates last and persist percent mode',()=>{
  const {run,context}=setup();
  context.rows=[{symbol:'large',daily_pnl_base:100,change_pct:1,pnl_base:500,pnl_pct:5},{symbol:'small',daily_pnl_base:20,change_pct:10,pnl_base:50,pnl_pct:20},{symbol:'loss',daily_pnl_base:-200,change_pct:-8,pnl_base:-900,pnl_pct:-30},{symbol:'missing',daily_pnl_base:null,change_pct:99,pnl_base:null,pnl_pct:null}];
  const order=key=>run(`state.portfolioSortKey='${key}';sortedPortfolioPositions(rows).map(r=>r.symbol).join(',')`);
  assert.equal(order('daily_pnl'),'large,small,loss,missing');
  assert.equal(order('daily_pct'),'small,large,loss,missing');
  assert.equal(order('total_pct'),'small,large,loss,missing');
  run(`state.portfolioSortDirection='asc'`);
  assert.equal(order('daily_pct'),'loss,large,small,missing');
  run(`window.localStorage=localStorage;savePortfolioSortPreferences();state.portfolioSortKey='default';loadPortfolioSortPreferences()`);
  assert.equal(run('state.portfolioSortKey'),'daily_pct');
});


test('holding news loads its own window despite empty global feed and preserves loading, errors and retries', async()=>{
  const {run,context,nodes}=setup();let resolve,reject,calls=[];
  context.api=url=>{calls.push(url);return new Promise((yes,no)=>{resolve=yes;reject=no;});};
  run(`state.currentView='signals';state.dashboard={news:[],alerts:[],movements:[],portfolio:{positions:[]}};state.newsLanguage='zh';document.getElementById('event-search').value='CN:601169.SS';document.getElementById('event-scope').value='all';renderEventCenter()`);
  assert.match(nodes.get('event-count').textContent,/正在读取/);
  assert.equal(calls.length,1);assert.match(calls[0],/market=CN&symbol=601169.SS/);
  resolve({market:'CN',symbol:'601169',news:[{market:'CN',symbol:'601169.SS',title:'Bank of Beijing result',source:'manual'}],lookback_hours:72});
  await new Promise(r=>setImmediate(r));
  assert.equal(nodes.get('event-count').textContent,'1 条相关新闻');
  assert.equal(nodes.get('event-timeline').children.length,1);
  assert.equal(nodes.get('event-timeline').children[0].children[1].children[0].textContent,'Bank of Beijing result');
  run(`workbenchState.instrumentNews=null;renderEventCenter()`);reject(Error('offline'));
  await new Promise(r=>setImmediate(r));
  assert.equal(nodes.get('event-count').textContent,'新闻读取失败');
  assert.equal(nodes.get('event-timeline').children[0].textContent,'offline');
  nodes.get('event-timeline').children[1].click();assert.equal(calls.length,3);
  resolve({market:'CN',symbol:'601169',news:[],lookback_hours:72});await new Promise(r=>setImmediate(r));
  assert.equal(nodes.get('event-count').textContent,'0 条相关新闻');
  assert.equal(nodes.get('event-timeline').children[0].children[0].children[0].textContent,'暂无相关新闻');
  run(`document.getElementById('event-search').value='';renderEventCenter()`);
  assert.equal(nodes.get('event-count').textContent,'0 条动态');
  assert.equal(nodes.get('event-timeline').children[0].children[0].children[0].textContent,'暂无匹配动态');
});

test('instrument news accepts canonical CN and HK response codes without changing global symbol rules', async()=>{
  const {run,context,nodes}=setup();const requests=[];
  context.api=url=>new Promise(resolve=>requests.push({url,resolve}));
  run(`state.currentView='signals';state.dashboard={news:[],alerts:[],movements:[],portfolio:{positions:[]}}`);
  for (const [market,symbol] of [['CN','000543.SZ'],['HK','00700.HK']]) {
    run(`document.getElementById('event-search').value='${market}:${symbol}';renderEventCenter();renderEventCenter()`);
    const canonical=symbol.split('.')[0], request=requests.at(-1);
    request.resolve({market,symbol:canonical,news:[{market,symbol:canonical,title:symbol}]});
    await new Promise(r=>setImmediate(r));
    assert.equal(nodes.get('event-count').textContent,'1 条相关新闻');
    assert.equal(nodes.get('event-timeline').children[0].children[1].children[0].textContent,symbol);
  }
  assert.equal(requests.length,2,'canonical normalization must not trigger repeated requests');
  assert.equal(run(`normalizeStockSymbol('601169.SS')`),'601169.SS','global symbol rules stay unchanged');
  run(`document.getElementById('event-search').value='CN:601169.SS';renderEventCenter()`);
  requests.at(-1).resolve({market:'US',symbol:'MU',news:[]});
  await new Promise(r=>setImmediate(r));
  assert.equal(nodes.get('event-count').textContent,'新闻读取失败');
  assert.match(nodes.get('event-timeline').children[0].textContent,/标的不匹配/);
});

test('late responses cannot replace another instrument and clearing search restores general events', async()=>{
  const {run,context,nodes}=setup();const requests=[];
  context.api=url=>new Promise(resolve=>requests.push({url,resolve}));
  run(`state.currentView='signals';state.dashboard={news:[],alerts:[],movements:[],portfolio:{positions:[]}};document.getElementById('event-search').value='KR:005930.KS';renderEventCenter();renderEventCenter()`);
  assert.equal(requests.length,1,'normalized code must not loop requests');
  run(`document.getElementById('event-search').value='US:MU';renderEventCenter()`);
  requests[1].resolve({market:'US',symbol:'MU',news:[{market:'US',symbol:'MU',title:'MU result'}]});await new Promise(r=>setImmediate(r));
  requests[0].resolve({market:'KR',symbol:'005930',news:[{market:'KR',symbol:'005930',title:'Old result'}]});await new Promise(r=>setImmediate(r));
  assert.equal(run('workbenchState.instrumentNews.key'),'us:mu');
  assert.equal(nodes.get('event-timeline').children[0].children[1].children[0].textContent,'MU result');
  run(`document.getElementById('event-search').value='';renderEventCenter()`);
  assert.equal(nodes.get('event-count').textContent,'0 条动态');
});

test('principal grouping preserves numeric amounts and rejects malformed or out-of-range input',()=>{
  const {run}=setup();
  for(const [input,expected] of [['1,000,000.00',1000000],['1000000.25',1000000.25],['0',0],['.50',0.5],['1,000,000,000,000.00',1e12]]) assert.equal(run(`portfolioPrincipalValue(${JSON.stringify(input)})`),expected);
  for(const input of ['', '1,00', '-1', '1.001', '1000000000001', 'abc', 'Infinity']) assert.equal(run(`portfolioPrincipalValue(${JSON.stringify(input)})`),null);
  assert.equal(run('formatPortfolioPrincipal(1000000)'), '1,000,000.00');
  assert.equal(run('formatPortfolioPrincipal(1234.5)'), '1,234.50');
});


test('holding event facts and unknown direction remain distinct from neutral judgments',()=>{
  const {run}=setup();
  for(const [status,label] of [['insufficient','待核实'],['price_only','行情事实'],['neutral','中性观察'],['mixed','多空并存']]){
    const note=JSON.parse(run(`JSON.stringify(holdingEventInterpretation({score:-.9,evidence_status:${JSON.stringify(status)}}))`));
    assert.equal(note.label,label);assert.equal(note.tone,'neutral');
  }
});
