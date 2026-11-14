const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const select = {value: 'CNY', disabled: false,
    get selectedOptions() { return [{textContent: this.value === 'HKD' ? '港币 HKD' : '人民币 CNY'}]; }};
  const account = {dataset: {}}, requests = [], messages = [];
  const context = vm.createContext({console, URLSearchParams, AbortController,
    localStorage: {getItem() { return null; }},
    window: {setTimeout, clearTimeout},
    document: {addEventListener() {}, getElementById(id) { return id === 'pr-account' ? account : select; }},
  });
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync('src/quant_news_monitor/ui/app.js', 'utf8'));
  context.select = select;
  context.notify = (text, type) => messages.push({text, type});
  context.render = data => {
    context.data = data;
    run('state.dashboard = data');
    select.value = data.portfolio.base_currency;
  };
  run(`portfolioContext.id = 'book-hk';
    state.dashboard = {portfolio: {id: 'book-hk', base_currency: 'CNY'}};
    showToast = notify; renderDashboard = render; renderConnection = () => {};`);
  let saved = 'CNY';
  context.fetch = async (url, request) => {
    requests.push({url, ...request});
    if (request.method === 'POST') saved = JSON.parse(request.body).base_currency;
    return {ok: true, json: async () => request.method === 'POST'
      ? {base_currency: saved}
      : {portfolio: {id: 'book-hk', base_currency: saved}}};
  };
  const change = currency => { select.value = currency; return run('savePortfolioCurrency({currentTarget: select})'); };
  return {run, context, select, account, requests, messages, change};
}

test('currency selection saves only the selected book base and refreshes before changing display', async () => {
  const t = setup();
  const operation = t.change('HKD');
  assert.equal(t.select.value, 'CNY');
  assert.equal(t.select.disabled, true);
  await operation;
  assert.equal(t.requests[0].url, '/api/portfolio/account?portfolio_id=book-hk');
  assert.deepEqual(JSON.parse(t.requests[0].body), {base_currency: 'HKD'});
  assert.equal(t.requests[1].url, '/api/dashboard?details=0&portfolio_id=book-hk');
  assert.equal(t.select.value, 'HKD');
  assert.equal(t.select.disabled, false);
  assert.match(t.messages[0].text, /港币 HKD/);
  await t.change('CNY');
  assert.equal(t.select.value, 'CNY');
  assert.deepEqual(JSON.parse(t.requests[2].body), {base_currency: 'CNY'});
});

test('unsaved account changes and a busy book prevent currency writes', async () => {
  const t = setup();
  t.account.dataset.dirty = '1';
  await t.change('HKD');
  assert.equal(t.select.value, 'CNY');
  assert.match(t.messages[0].text, /未保存/);
  delete t.account.dataset.dirty;
  t.run('portfolioContext.switching = true');
  await t.change('HKD');
  assert.equal(t.requests.length, 0);
});

test('failed saves restore the displayed currency and allow retry', async () => {
  const t = setup();
  t.context.fetch = async () => ({ok: false, status: 500, json: async () => ({error: '保存失败'})});
  await t.change('HKD');
  assert.equal(t.select.value, 'CNY');
  assert.equal(t.select.disabled, false);
  assert.deepEqual(t.messages, [{text: '保存失败', type: 'error'}]);
});

test('a saved preference with failed refresh never relabels old CNY amounts as HKD', async () => {
  const t = setup();
  const fetch = t.context.fetch;
  t.context.fetch = async (url, request) => request.method === 'POST'
    ? fetch(url, request) : {ok: false, status: 503, json: async () => ({error: '刷新失败'})};
  await t.change('HKD');
  assert.equal(t.select.value, 'CNY');
  assert.equal(t.select.disabled, false);
  assert.equal(t.messages[0].text, '计价币种已保存，金额等待刷新');
});
