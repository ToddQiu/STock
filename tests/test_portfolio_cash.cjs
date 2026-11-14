const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const checks = ['CNY','USD','HKD','KRW'].map(currency => ({dataset:{cashCurrency:currency}, checked:false, disabled:false}));
  const inputs = Object.fromEntries(checks.map(c => [c.dataset.cashCurrency,{value:'',disabled:false,focus(){this.focused=true;}}]));
  const button = {disabled:false}, error = {textContent:''}, account = {dataset:{}};
  const dialog = {dataset:{portfolioId:'book-hk'},open:true, close(){this.open=false;},
    querySelectorAll(selector){if(selector.includes('receivable')||selector.includes('payable'))return [];return selector.includes(':checked') ? checks.filter(c=>c.checked) : [...checks,...Object.values(inputs),button];},
    querySelector(selector){return inputs[selector.match(/"(\w+)"/)[1]];}};
  const elements = {'portfolio-cash-dialog':dialog,'portfolio-cash-save':button,'portfolio-cash-error':error,'pr-account':account};
  const context = vm.createContext({console,URLSearchParams,AbortController,
    localStorage:{getItem(){return null;}},window:{setTimeout,clearTimeout},
    document:{addEventListener(){},getElementById(id){return elements[id];}}});
  const run = code => vm.runInContext(code,context);
  run(fs.readFileSync('src/quant_news_monitor/ui/app.js','utf8'));
  const requests=[], messages=[];
  context.notify=(text,type)=>messages.push({text,type});
  context.refresh=async()=>({portfolio:{}});
  run("portfolioContext.id='book-hk';showToast=notify;refreshDashboard=refresh;");
  context.fetch=async(url,request)=>{requests.push({url,...request});return {ok:true,json:async()=>({})};};
  const select=(currency,value)=>{checks.find(c=>c.dataset.cashCurrency===currency).checked=true;inputs[currency].value=value;};
  const save=()=>run('savePortfolioCash({preventDefault(){}})');
  return {context,run,requests,messages,dialog,checks,inputs,button,error,select,save};
}

test('multi-currency save posts only balances for the selected book, never principal',async()=>{
  const t=setup();t.select('CNY','1,234.56');t.select('USD','500');t.select('HKD','0.00');
  await t.save();
  assert.equal(t.requests[0].url,'/api/portfolio/account?portfolio_id=book-hk');
  assert.deepEqual(JSON.parse(t.requests[0].body),{cash_mode:'manual',cash_balances:{CNY:1234.56,USD:500,HKD:0},receivable_balances:{},payable_balances:{}});
  assert.equal(t.dialog.open,false);assert.equal(t.button.disabled,false);assert.match(t.messages[0].text,/本金未改变/);
});

test('blank, negative, over-precision and malformed grouping are not saved',async()=>{
  for(const value of ['','-1','1.234','12,34','NaN','1000000000001']){
    const t=setup();t.select('USD',value);await t.save();assert.equal(t.requests.length,0);assert.match(t.error.textContent,/USD/);
  }
  const t=setup();await t.save();assert.match(t.error.textContent,/至少/);assert.equal(t.requests.length,0);
});

test('failure preserves inputs; double submit and book switching cannot write',async()=>{
  const t=setup();t.select('KRW','123,456.00');
  t.context.fetch=async()=>({ok:false,status:500,json:async()=>({error:'保存失败'})});
  await t.save();assert.equal(t.dialog.open,true);assert.equal(t.inputs.KRW.value,'123,456.00');assert.match(t.error.textContent,/保存失败/);assert.equal(t.button.disabled,false);
  t.button.disabled=true;await t.save();assert.equal(t.requests.length,0);
  t.button.disabled=false;t.run("portfolioContext.id='other'");await t.save();assert.equal(t.dialog.open,false);assert.equal(t.requests.length,0);
});

test('successful write with failed refresh is reported as saved without retrying',async()=>{
  const t=setup();t.select('CNY','0');t.context.refresh=async()=>{throw Error('refresh failed');};t.run('refreshDashboard=refresh');
  await t.save();assert.equal(t.requests.length,1);assert.equal(t.dialog.open,false);assert.match(t.messages[0].text,/已保存.*等待刷新/);
});
