(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.InvestmentCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const round = (value, digits = 6) => Math.round((value + Number.EPSILON) * 10 ** digits) / 10 ** digits;
  function text(value, max = 2000) {
    if (value == null) return '';
    if (typeof value !== 'string' || value.length > max) throw new Error('文字格式或长度无效');
    return value.trim();
  }
  function number(value, label, optional = false, minimum = 0, maximum = 1e12) {
    if (value == null || value === '' || typeof value === 'string' && !value.trim()) {
      if (optional) return null;
      throw new Error('请填写' + label);
    }
    if (typeof value === 'boolean' || !['number', 'string'].includes(typeof value) || !Number.isFinite(Number(value)) || Number(value) < minimum || Number(value) > maximum)
      throw new Error(label + '超出有效范围');
    return Number(value);
  }
  function date(value, optional = false) {
    const s = text(value, 10), d = new Date(s + 'T00:00:00Z');
    if (!s && optional) return '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || !Number.isFinite(d.getTime()) || d.toISOString().slice(0, 10) !== s) throw new Error('日期格式无效');
    return s;
  }
  function key(market, symbol) {
    market = text(market, 10).toUpperCase(); symbol = text(symbol, 32).toUpperCase();
    if (!['CN', 'HK', 'US', 'KR', 'FUND', 'ASSET'].includes(market)) throw new Error('市场无效');
    if (market === 'CN') symbol = symbol.replace(/\.(SH|SS|SZ|BJ)$/, '').replace(/^(SH|SZ|BJ)(?=\d)/, '');
    if (market === 'HK') symbol = symbol.replace(/\.HK$/, '').padStart(5, '0');
    if (market === 'KR') symbol = symbol.replace(/\.(KS|KQ)$/, '').padStart(6, '0');
    if (!/^[A-Z0-9.^=_-]{1,24}$/.test(symbol)) throw new Error('证券代码无效');
    return market + ':' + symbol;
  }
  function url(value) {
    const s = text(value, 2048);
    if (s && !/^https?:\/\/[a-z0-9][a-z0-9.-]+(?::\d+)?(?:[/?#][^\s\\]*)?$/i.test(s)) throw new Error('来源链接无效');
    return s;
  }
  function list(value, max, label) {
    if (!Array.isArray(value) || value.length > max) throw new Error(label + '数量或格式无效');
    return value;
  }
  function identifier(value) {
    const s = text(value, 80);
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(s)) throw new Error('记录标识无效');
    return s;
  }
  const currencies = ['CNY', 'USD', 'HKD', 'KRW'];
  const marketCurrency = {CN:'CNY', FUND:'CNY', US:'USD', HK:'HKD', KR:'KRW', ASSET:'USD'};
  function currency(value) { if (!currencies.includes(value)) throw new Error('币种无效'); return value; }
  function validatePlan(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input) || input.schema !== 1) throw new Error('不是 STock 配置方案');
    const p = {schema: 1, id: identifier(input.id), name: text(input.name, 120), currency: currency(input.currency),
      budget: number(input.budget, '预算'), cash_target: number(input.cash_target, '现金预留'),
      as_of: date(input.as_of, true), archived: Boolean(input.archived), source: {}, targets: [], funds: {}, research: [], valuations: [], reviews: [], trades: []};
    if (!p.name) throw new Error('请填写方案名称');
    const source = input.source || {};
    p.source = {filename: text(source.filename, 200), sha256: text(source.sha256, 64), note: text(source.note), imported_at: text(source.imported_at, 40)};
    p.targets = list(input.targets || [], 500, '标的').map(row => ({key: key(row.market, row.symbol), market: text(row.market,10).toUpperCase(),
      symbol: key(row.market, row.symbol).split(':')[1], name: text(row.name, 120), currency: currency(row.currency),
      category: text(row.category, 80), amount: number(row.amount, '目标金额'), is_etf: Boolean(row.is_etf),
      reference_price: number(row.reference_price, '文件参考价', true), reference_date: date(row.reference_date, true),
      shock_pct: number(row.shock_pct, '情景涨跌幅', true, -100, 1000)}));
    if (new Set(p.targets.map(row => row.key)).size !== p.targets.length || p.targets.some(row => !row.name || row.currency !== p.currency)) throw new Error('目标标的重复、名称缺失或预算币种不一致');
    if (p.targets.some(row => marketCurrency[row.market] !== row.currency)) throw new Error('方案按单一交易币种记账，请为不同币种另建方案');
    if (p.cash_target + p.targets.reduce((s, r) => s + r.amount, 0) > p.budget + 0.01) throw new Error('目标金额与现金预留合计超过预算');
    if (!input.funds || typeof input.funds !== 'object' || Array.isArray(input.funds) || Object.keys(input.funds).length > 100) throw new Error('ETF 快照格式无效');
    for (const [id, fund] of Object.entries(input.funds)) {
      if (id !== key(fund.market, fund.symbol)) throw new Error('ETF 标识不一致');
      p.funds[id] = {market: fund.market, symbol: key(fund.market, fund.symbol).split(':')[1], name: text(fund.name, 120),
        as_of: date(fund.as_of), source_urls: list(fund.source_urls || [], 10, '来源').map(url),
        holdings: list(fund.holdings, 2500, '成分持仓').map(row => ({key: row.key ? key(row.market, row.symbol) : '', market: text(row.market, 10),
          symbol: text(row.symbol, 32), name: text(row.name, 120), type: ['stock', 'cash', 'future', 'other'].includes(row.type) ? row.type : 'other',
          weight: number(row.weight, '持仓权重', false, -1000, 1000), source_row: number(row.source_row, '来源行', true, 0, 10000)}))};
    }
    p.research = list(input.research || [], 500, '研究').map(row => ({target: text(row.target, 40), name: text(row.name, 120),
      business: text(row.business), thesis: text(row.thesis, 10000), risks: text(row.risks, 10000), original_rating: text(row.original_rating, 80),
      as_of: date(row.as_of, true), source_url: url(row.source_url), review: text(row.review, 10000)}));
    p.valuations = list(input.valuations || [], 1500, '估值').map(row => ({target: text(row.target, 40),
      scenario: ['bull', 'base', 'bear'].includes(row.scenario) ? row.scenario : 'base',
      eps: number(row.eps, '正常化 EPS', true, 0), growth: number(row.growth, '增长率', true, -99.99, 1000),
      pe: number(row.pe, '目标 PE', true, 0.01, 10000), discount: number(row.discount, '折现率', true, 0, 1000),
      dividends: number(row.dividends, '分红现值', true), years: number(row.years, '年数', false, 1, 50),
      conditions: text(row.conditions), invalidation: text(row.invalidation), source_url: url(row.source_url), as_of: date(row.as_of, true)}));
    p.reviews = list(input.reviews || [], 1000, '复盘').map(row => ({id: identifier(row.id), date: date(row.date),
      target: text(row.target, 40), title: text(row.title, 160), decision: text(row.decision, 80), body: text(row.body, 10000), source_url: url(row.source_url)}));
    p.trades = list(input.trades || [], 2000, '流水').map(row => ({id: identifier(row.id), date: date(row.date),
      account: text(row.account, 80), kind: text(row.kind, 20), currency: currency(row.currency), target: text(row.target, 40),
      quantity: number(row.quantity, '数量', true), price: number(row.price, '价格', true), fee: number(row.fee, '费用'),
      amount: number(row.amount, '资金金额', true), note: text(row.note, 500)}));
    if (new Set(p.trades.map(row => row.id)).size !== p.trades.length || new Set(p.reviews.map(row => row.id)).size !== p.reviews.length) throw new Error('重复记录标识');
    const targets = new Set(p.targets.map(row => row.key));
    if ([...p.research,...p.valuations].some(row=>!targets.has(row.target)) || p.reviews.some(row=>!row.title || row.target&&!targets.has(row.target))) throw new Error('研究标的或复盘标题无效');
    ledger(p);
    return p;
  }
  function ledger(plan) {
    const accounts = new Map(), lots = new Map();
    const targets = new Map(plan.targets.map(r => [r.key, r]));
    const ordered = plan.trades.map((row, i) => ({...row, order: i})).sort((a, b) => a.date.localeCompare(b.date) || a.order - b.order);
    for (const row of ordered) {
      if (!row.account || !['buy', 'sell', 'initial', 'dividend', 'deposit', 'withdraw'].includes(row.kind)) throw new Error('账户或流水类型无效');
      const group = row.account + '\u001f' + row.currency;
      if (!accounts.has(group)) accounts.set(group, {account: row.account, currency: row.currency, cash: 0, net_funding: 0, realized: 0});
      const account = accounts.get(group), fee = row.fee;
      if (['deposit', 'withdraw'].includes(row.kind)) {
        if (!(row.amount > 0) || fee !== 0) throw new Error('出入金须为正数，费用请计入成交或分红');
        const delta = row.kind === 'deposit' ? row.amount : -row.amount;
        account.cash = round(account.cash + delta); account.net_funding = round(account.net_funding + delta);
      } else {
        const target = targets.get(row.target);
        if (!target || target.currency !== row.currency) throw new Error('流水标的或币种与方案不一致');
        const lotKey = group + '\u001f' + row.target;
        const lot = lots.get(lotKey) || {...target, account: row.account, quantity: 0, cost_amount: 0};
        if (row.kind === 'dividend') {
          if (!(row.amount > 0) || fee > row.amount || lot.quantity <= 0) throw new Error('分红须有持仓，净金额不能为负');
          account.cash = round(account.cash + row.amount - fee); account.realized = round(account.realized + row.amount - fee);
        } else {
          if (!(row.quantity > 0) || !(row.price > 0)) throw new Error('成交数量与价格须大于零');
          const gross = row.quantity * row.price;
          if (row.kind === 'sell') {
            if (row.quantity > lot.quantity + 1e-8) throw new Error(row.account + '：卖出数量超过持仓');
            const removed = lot.cost_amount * row.quantity / lot.quantity;
            account.cash = round(account.cash + gross - fee); account.realized = round(account.realized + gross - fee - removed);
            lot.quantity = round(lot.quantity - row.quantity); lot.cost_amount = lot.quantity ? round(lot.cost_amount - removed) : 0;
          } else {
            lot.quantity = round(lot.quantity + row.quantity); lot.cost_amount = round(lot.cost_amount + gross + fee);
            if (row.kind === 'buy') account.cash = round(account.cash - gross - fee);
            else account.net_funding = round(account.net_funding + gross + fee);
          }
        }
        lots.set(lotKey, lot);
      }
      if (account.cash < -0.000001) throw new Error(row.account + '：现金不足，请先记录实际入金');
    }
    return {accounts: [...accounts.values()], positions: [...lots.values()].filter(r => r.quantity > 0).map(r => ({...r, average_cost: r.cost_amount / r.quantity}))};
  }
  function valuation(row, price) {
    if (['eps', 'growth', 'pe', 'discount', 'dividends', 'years'].some(k => row[k] == null)) return {value: null, upside: null};
    const value = row.eps * (1 + row.growth / 100) ** row.years * row.pe / (1 + row.discount / 100) ** row.years + row.dividends;
    return {value: Number.isFinite(value) ? value : null, upside: value >= 0 && price > 0 ? (value / price - 1) * 100 : null};
  }
  function stress(plan) {
    const rows = plan.targets.map(r => ({...r, pnl: r.shock_pct == null ? null : r.amount * r.shock_pct / 100}));
    const complete = rows.every(r => r.amount === 0 || r.pnl != null);
    return {rows, complete, known_pnl: rows.reduce((s, r) => s + (r.pnl || 0), 0), pnl: complete ? rows.reduce((s, r) => s + (r.pnl || 0), 0) : null};
  }
  function exposure(positions, funds, denominator = null) {
    const combined = new Map(), breakdown = [], missing = [];
    let total = 0, complete = true;
    function add(id, name, amount, direct, source) {
      const row = combined.get(id) || {key: id, name, direct: 0, indirect: 0, sources: []};
      row[direct ? 'direct' : 'indirect'] += amount; if (source) row.sources.push(source); combined.set(id, row);
    }
    for (const position of positions) {
      const id = key(position.market, position.symbol), amount = position.amount, fund = funds[id];
      if (amount == null || !Number.isFinite(amount)) {complete = false; missing.push(position.name + '：缺少有效市值或汇率'); continue;}
      total += amount;
      if (!fund) {
        if (position.is_etf || ['etf','fund'].includes(position.asset_type) || position.market==='FUND' || /\bETF\b|基金/i.test(position.name)) {complete = false; missing.push(position.name + '：未取得持仓快照');}
        else add(id, position.name, amount, true);
        continue;
      }
      let covered = 0;
      for (const [index, holding] of fund.holdings.entries()) {
        const mapped = amount * holding.weight / 100; covered += mapped;
        const holdingId = holding.key || id + ':unmapped:' + index;
        add(holdingId, holding.name, mapped, false, {fund: position.name, weight: holding.weight, amount: mapped, as_of: fund.as_of});
      }
      breakdown.push({key: id, name: position.name, as_of: fund.as_of, amount, mapped: covered, residual: amount - covered});
    }
    const base = denominator > 0 ? denominator : complete && total > 0 ? total : null;
    const rows = [...combined.values()].map(r => ({...r, total: round(r.direct + r.indirect), pct: base ? (r.direct + r.indirect) / base * 100 : null}))
      .sort((a, b) => b.total - a.total);
    return {rows, breakdown, missing, complete, denominator: base, total};
  }
  function emptyPlan(name = '新配置方案') {
    return {schema: 1, id: 'plan-' + Date.now().toString(36), name, currency: 'CNY', budget: 0, cash_target: 0,
      as_of: '', source: {}, targets: [], funds: {}, research: [], valuations: [], reviews: [], trades: [], archived: false};
  }
  return {clone, round, text, number, date, key, url, currencies, validatePlan, ledger, valuation, stress, exposure, emptyPlan};
});
