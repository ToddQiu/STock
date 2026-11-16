const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const file = '../src/quant_news_monitor/ui/options_core.js';
const O = fs.existsSync(require('node:path').resolve(__dirname, file)) ? require(file) : {};
const near = (a, b, tolerance = 1e-6) => assert.ok(Math.abs(a - b) < tolerance, `${a} != ${b}`);
const p = {spot:100, strike:100, strike2:110, days:365, iv:.2, rate:.05, contracts:1, fee:0};

test('options engine exists for the browser and Node consumers', () => assert.equal(typeof O.price, 'function'));
test('Black-Scholes reference, put-call parity and boundary prices', () => {
  near(O.price({...p, type:'call'}), 10.45058357, .00002);
  near(O.price({...p, type:'put'}), 5.57352602, .00002);
  near(O.price({...p, type:'call', days:0, spot:120}), 20);
  near(O.price({...p, type:'put', days:0, spot:0}), 100);
  near(O.price({...p, type:'put', spot:0}), 95.12294245, .00001);
  near(O.price({...p, type:'call', iv:0, rate:0}), 0);
});
test('collecting a put premium adds an equal liability, not opening profit', () => {
  const original = O.createAccount(20000), a = O.openTrade(original, 'cash_put', p), m = O.mark(a);
  near(m.equity, 20000); near(m.pnl, 0); near(m.reserved, 10000);
  assert.ok(m.cash > 20000); assert.ok(m.optionValue < 0);
  near(m.cash + m.optionValue, 20000);
  assert.equal(original.position, null); assert.equal(original.log.length, 0);
});
test('deep put assignment spends strike cash, retains loss and can become a covered call', () => {
  const a = O.openTrade(O.createAccount(20000), 'cash_put', {...p, iv:0, rate:0, strike:120});
  const settled = O.settle(O.advance(a, {spot:40, days:0, iv:.8}));
  near(settled.cash, 10000); assert.equal(settled.shares, 100); near(settled.stockCost, 12000);
  near(O.mark(settled).equity, 14000); near(O.mark(settled).pnl, -6000);
  assert.equal(settled.position, null); assert.throws(() => O.settle(settled));
  const call = O.openTrade(settled, 'covered_call', {...p, spot:40, strike:60, iv:0, rate:0});
  assert.equal(call.shares, 100); near(call.cash, 10000);
  const delivered = O.settle(O.advance(call, {spot:90, days:0, iv:0}));
  assert.equal(delivered.shares, 0); near(delivered.cash, 16000); near(O.mark(delivered).pnl, -4000);
});
test('covered call limits gains to strike plus premium and charges opening fees once', () => {
  const a = O.openTrade(O.createAccount(20000), 'covered_call', {...p, strike:90, iv:0, rate:0, fee:1});
  near(O.mark(a).equity, 19999); near(a.fees, 1);
  const b = O.settle(O.advance(a, {spot:150, days:0, iv:0}));
  near(b.cash, 19999); assert.equal(b.shares, 0); near(b.fees, 1);
});
test('debit spreads settle net intrinsic value with finite upside and downside', () => {
  const bull = O.analyze('bull_call_spread', {...p, iv:0, rate:0, strike:90, strike2:110});
  near(bull.maxProfit, 1000); near(bull.maxLoss, 1000); near(bull.breakevens[0], 100);
  near(O.expirationPnl(bull.strategy, 150), 1000); near(O.expirationPnl(bull.strategy, 0), -1000);
  let a = O.openTrade(O.createAccount(5000), 'bull_call_spread', {...p, iv:0, rate:0, strike:90, strike2:110});
  a = O.settle(O.advance(a, {spot:150, days:0, iv:0}));
  near(a.cash, 6000); assert.equal(a.shares, 0);
  const bear = O.analyze('bear_put_spread', {...p, iv:0, rate:0, strike:110, strike2:90});
  near(O.expirationPnl(bear.strategy, 0), 1000); near(O.expirationPnl(bear.strategy, 150), -1000);
});
test('long options, protective put and straddle retain their distinct expiration risks', () => {
  const call = O.analyze('long_call', {...p, iv:0, rate:0, strike:90});
  near(call.maxLoss, 1000); assert.equal(call.maxProfit, Infinity); near(call.breakevens[0], 100);
  const put = O.analyze('long_put', {...p, iv:0, rate:0, strike:110});
  near(put.maxLoss, 1000); near(put.maxProfit, 10000);
  const protect = O.analyze('protective_put', {...p, iv:0, rate:0, strike:110});
  near(protect.maxLoss, 0); assert.equal(protect.maxProfit, Infinity);
  const straddle = O.analyze('long_straddle', p);
  assert.equal(straddle.breakevens.length, 2); assert.ok(straddle.breakevens[0] < 100 && straddle.breakevens[1] > 100);
  near(O.expirationPnl(straddle.strategy, 100), -straddle.maxLoss);
});
test('rolling recognizes the old loss without changing equity except fees', () => {
  let a = O.openTrade(O.createAccount(20000), 'cash_put', {...p, iv:0, rate:0, strike:100, fee:1});
  a = O.advance(a, {spot:60, days:10, iv:0});
  const equity = O.mark(a).equity, before = JSON.stringify(a);
  const b = O.rollTrade(a, {strike:90, days:30, iv:0});
  near(b.realized, -4002); near(O.mark(b).equity, equity - 2); near(b.fees, 3);
  assert.equal(b.position.params.strike, 90); assert.equal(b.days, 30); assert.equal(JSON.stringify(a), before);
});
test('closing marks option profit separately and leaves stock ownership intact', () => {
  let a = O.openTrade(O.createAccount(20000), 'covered_call', {...p, iv:0, rate:0, strike:110, fee:1});
  a = O.advance(a, {spot:120, days:20, iv:0});
  const b = O.closeTrade(a);
  near(b.cash, 8998); assert.equal(b.shares, 100); near(b.realized, -1002); near(O.mark(b).pnl, 998);
  const sold = O.sellShares(b);
  near(sold.cash, 20998); assert.equal(sold.shares, 0); near(sold.realized, 998);
  assert.throws(() => O.sellShares(a), /平仓|合约/);
});
test('insufficient funds and invalid steps cannot partially mutate an account', () => {
  const small = O.createAccount(5000), before = JSON.stringify(small);
  assert.throws(() => O.openTrade(small, 'cash_put', p), /资金|现金/);
  assert.equal(JSON.stringify(small), before);
  const a = O.openTrade(O.createAccount(10000), 'cash_put', {...p, iv:0, rate:0, days:30});
  const saved = JSON.stringify(a);
  assert.throws(() => O.advance(a, {spot:80, days:31, iv:.2}), /天数|增加/);
  assert.throws(() => O.rollTrade(a, {strike:300, days:60, iv:.2}), /资金|现金/);
  assert.throws(() => O.settle(a), /到期/);
  assert.throws(() => O.openTrade(a, 'long_call', p), /持仓|合约/);
  assert.equal(JSON.stringify(a), saved);
});
test('reject malformed prices, strikes, sizes and spread directions', () => {
  for (const bad of [NaN, Infinity, -1, '', null, true]) assert.throws(() => O.price({...p, type:'call', spot:bad}));
  for (const bad of [0, 1.5, 101]) assert.throws(() => O.strategy('cash_put', {...p, contracts:bad}));
  assert.throws(() => O.strategy('bull_call_spread', {...p, strike2:90}));
  assert.throws(() => O.strategy('bear_put_spread', p));
  assert.throws(() => O.strategy('naked_call', p));
  assert.throws(() => O.strategy('cash_put', {...p, days:0}));
  assert.throws(() => O.createAccount(-1));
});
