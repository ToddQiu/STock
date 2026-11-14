// Runs the distributable with an empty, separate profile and no developer runtimes on PATH.
const { _electron: electron }=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {once}=require('node:events');
const http=require('node:http');
const folder=path.resolve(process.argv[2]);
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'STock-share-qa-'));
const errors=[],report={profile,folder,checks:[]};
const env={...process.env,STOCK_QA_PROFILE:profile,STOCK_ASSISTANT_HOME:path.join(profile,'not-installed'),PATH:`${process.env.SystemRoot}\\System32;${process.env.SystemRoot}`};
// External feed latency and translation queues must not decide the offline release check.
Object.assign(env,{HTTP_PROXY:'http://127.0.0.1:9',HTTPS_PROXY:'http://127.0.0.1:9',ALL_PROXY:'http://127.0.0.1:9',NO_PROXY:'127.0.0.1,localhost'});
for(const key of Object.keys(env))if(/^(ELECTRON_RUN_AS_NODE|NODE_PATH|PYTHONPATH|PYTHONHOME|VIRTUAL_ENV|OPENAI_API_KEY|X_BEARER_TOKEN|THREADS_ACCESS_TOKEN)$/i.test(key))delete env[key];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function launch(){
  const app=await electron.launch({executablePath:path.join(folder,'STock.exe'),args:['--smoke-test'],cwd:profile,env,timeout:120000});
  try{
    const page=await app.firstWindow({timeout:120000});page.on('pageerror',error=>errors.push(error.message));
    await page.waitForFunction(()=>typeof startupMemory!=='undefined' && startupMemory.ready && Boolean(state.dashboard),undefined,{timeout:120000});
    assert.equal(await app.evaluate(({app})=>app.isPackaged),true);
    assert.equal(await app.evaluate(({app})=>app.getPath('userData')),profile);
    return {app,page};
  }catch(error){
    try{
      const windows=await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().map(w=>({url:w.webContents.getURL(),loading:w.webContents.isLoading(),visible:w.isVisible()})));
      console.error('Window diagnostics:',JSON.stringify(windows));
      const port=JSON.parse(fs.readFileSync(path.join(profile,'service-port.json'),'utf8'));
      const response=await fetch(`http://127.0.0.1:${port}/`,{signal:AbortSignal.timeout(5000)});
      console.error('Local HTML:',response.status,(await response.text()).length);
    }catch(diagnostic){console.error('Diagnostic:',diagnostic.message);}
    await app.close();throw error;
  }
}
async function close(app,url){
  const child=app.process(),exit=once(child,'exit');
  await app.evaluate(({app})=>app.quit());
  let timeout,result;
  try{
    result=await Promise.race([exit,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Desktop did not exit cleanly')),65000);})]);
  }finally{clearTimeout(timeout);}
  const [code]=result;
  assert.equal(code,0);
  let stopped=false;
  for(let i=0;i<30;i++){
    try{await fetch(new URL('/api/health',url),{signal:AbortSignal.timeout(1000)});}
    catch{stopped=true;break;}
    await sleep(200);
  }
  assert.equal(stopped,true,'Owned backend must stop with desktop');
}
(async()=>{
  let current;
  try{
    const title='Microsoft reports higher revenue and stronger cloud demand';
    const config=JSON.parse(fs.readFileSync(path.join(folder,'resources/app/default-config.json'),'utf8'));
    assert.deepEqual(config.portfolio,{positions:[],principal_cny:0});
    assert.deepEqual(config.watchlist,[]);
    fs.mkdirSync(path.join(profile,'data'),{recursive:true});
    fs.writeFileSync(path.join(profile,'data/inbox.jsonl'),JSON.stringify({market:'US',symbol:'MSFT',title,summary:'Microsoft revenue increased.',source:'manual',url:'https://example.com/packaging-test',published_at:new Date().toISOString()})+'\n');
    current=await launch();let {app,page}=current;
    const url=page.url();report.url=url;
    assert.notEqual(new URL(url).port,'8765');
    assert.equal(await page.evaluate(()=>typeof require),'undefined');
    assert.deepEqual(await page.evaluate(()=>state.dashboard.portfolio.positions),[]);
    assert.equal(await page.locator('#overview-compass').isVisible(),true);
    assert.ok(await page.locator('.brand-mark').evaluate(image=>image.complete&&image.naturalWidth>0));
    report.checks.push('fresh packaged app boots without Python/Node on PATH','empty holdings','compass and logo render','loopback port avoids original service');
    const api=async(route,payload)=>page.evaluate(async({route,payload})=>{
      const response=await fetch(route,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!response.ok)throw Error(`${route}: ${response.status} ${await response.text()}`);
      return response.json();
    },{route,payload});
    for(const route of ['/api/health','/api/assets','/api/indices','/api/social','/api/research','/api/research/cards','/api/research/companies','/api/fundamentals','/api/research/earnings','/api/research/valuations','/api/portfolio/backtests'])await api(route);
    const assistant=await api('/api/assistant/status');
    assert.equal(assistant.installed,false,'The package must omit the local assistant model');
    assert.equal(fs.existsSync(path.join(folder,'resources/app/.stock-assistant')),false);
    assert.equal(await page.locator('#assistant-launch, [data-settings-target="assistant"], #backtest-ask-assistant').count(),0);
    report.checks.push('no local assistant entry points or bundled model');
    await page.locator('#manual-news-button').click();
    assert.equal(await page.locator('#manual-dialog').evaluate(node=>node.open),true);await page.keyboard.press('Escape');
    await page.locator('#nav-research summary').click();
    for(const tab of ['cards','etf','landscape','earnings','valuation','forecasts','inputs','stats']){
      const group=['forecasts','inputs','stats'].includes(tab)?'#nav-backtest':'#nav-research';
      if(!await page.locator(group).evaluate(node=>node.open))await page.locator(group+' summary').click();
      if(tab==='landscape')await page.locator('#research-tabs [data-tab="cards"]').click();
      if(tab==='stats')await page.locator('#backtest-tabs [data-tab="inputs"]').click();
      await page.locator(`${tab==='landscape'?'#company-research-views':tab==='stats'?'#research-evidence-views':group==='#nav-backtest'?'#backtest-tabs':'#research-tabs'} [data-tab="${tab}"]`).click();
      await page.waitForFunction(tab=>!document.getElementById(`research-${tab}-panel`).hidden,tab);
    }
    report.checks.push('live backend APIs for all main modules','manual-news dialog','six research sections with merged company and financial views');
    await api('/api/auto',{enabled:false});
    await api('/api/portfolio/principal',{principal_cny:12345});
    await page.evaluate(async()=>{setView('overview',{refresh:false});saveStartupPreferences();await flushStartupMemory();});
    await page.screenshot({path:path.join(profile,'first-launch.png')});
    await page.evaluate(async()=>{setView('assets',{refresh:false});saveStartupPreferences();await flushStartupMemory();});
    await close(app,url);current=null;
    report.checks.push('graceful shutdown stops only its owned backend');
    const savedConfig=JSON.parse(fs.readFileSync(path.join(profile,'config.json'),'utf8'));
    savedConfig.watchlist=[{market:'US',symbol:'MSFT',name:'Microsoft',query:'Microsoft MSFT',aliases:['Microsoft']}];
    fs.writeFileSync(path.join(profile,'config.json'),JSON.stringify(savedConfig));
    current=await launch();app=current.app;page=current.page;
    assert.equal(page.url(),url,'Stable origin must retain browser memory');
    assert.equal(await page.evaluate(()=>state.currentView),'assets');
    assert.equal(JSON.parse(fs.readFileSync(path.join(profile,'config.json'),'utf8')).portfolio.principal_cny,12345);
    report.checks.push('reopen preserves service origin, selected view and principal');
    await api('/api/scan',{offline:true});
    const deadline=Date.now()+180000;let translated;
    while(Date.now()<deadline){
      const result=await api('/api/dashboard');
      const item=result.news.find(row=>row.title===title);
      if(item?.title_translation_status==='translated'){translated=item;break;}
      if(result.translation.state==='unavailable')throw Error(`Bundled translation failed: ${result.translation.error}`);
      await sleep(1000);
    }
    assert.ok(translated,'Bundled translation must translate the synthetic English headline');
    assert.match(translated.title_zh,/[\u3400-\u9fff]/);report.translation=translated.title_zh;
    report.checks.push('frozen rule analysis and research fingerprints','bundled offline translation model and native tokenizer');
    await page.screenshot({path:path.join(profile,'reopened.png')});
    await close(app,url);current=null;
    const foreign=http.createServer((req,res)=>res.end('unrelated local service'));
    await new Promise(resolve=>foreign.listen(Number(new URL(url).port),'127.0.0.1',resolve));
    try{
      current=await launch();app=current.app;page=current.page;
      const fallbackUrl=page.url();assert.notEqual(fallbackUrl,url);
      await close(app,fallbackUrl);current=null;
      assert.equal(await (await fetch(url)).text(),'unrelated local service');
      report.checks.push('occupied saved port falls back without touching unrelated services');
    }finally{await new Promise(resolve=>foreign.close(resolve));}
    assert.deepEqual(errors,[]);report.pageErrors=errors;report.passed=true;
    fs.writeFileSync(path.join(profile,'verification.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  }finally{
    if(current && current.app.process().exitCode===null)await current.app.close();
  }
})().catch(error=>{
  console.error(error);console.error(JSON.stringify(report));
  for(const name of ['desktop-launch.log','desktop-service.log']){
    const file=path.join(profile,name);
    if(fs.existsSync(file))console.error(name+'\n'+fs.readFileSync(file,'utf8').slice(-10000));
  }
  process.exitCode=1;
});
