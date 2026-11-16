(function () {
  'use strict';
  const host = document.getElementById('options-workspace');
  if (!host) return;
  const C = window.STockOptions, L = window.STockOptionsLearning;
  if (!C || !L) { host.textContent = '期权模块未能加载，请刷新版本后重试。'; return; }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => Number.isFinite(value) ? '$' + value.toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2}) : '无上限';
  const q = id => document.getElementById(id);
  const SAVE_KEY = 'stock.options.exercise.v1', PROGRESS_KEY = 'stock.options.progress.v1';
  const tabs = {learn:'从这里开始',library:'策略库',simulate:'模拟工作台',reference:'术语与规则'};
  let account = C.createAccount(10000), actions = [], completed = new Set(), caseLabel = '通用标的 · 假设行情';
  try { const saved = JSON.parse(localStorage.getItem(PROGRESS_KEY) || '[]'); if (Array.isArray(saved)) completed = new Set(saved.filter(id => L.lessons.some(l => l.id === id))); } catch { /* 学习记录不可用时仍可练习。 */ }
  const numberField = (id,label,value,min,max,step='any',note='') => `<label class="opt-field" for="opt-${id}"><span>${label}</span><input id="opt-${id}" type="number" value="${value}" min="${min}" max="${max}" step="${step}" required>${note ? `<small>${note}</small>` : ''}</label>`;
  const button = (action,text,extra='',primary=false) => `<button type="button" class="${primary?'primary':'secondary'}-button" data-opt-action="${action}" ${extra}>${text}</button>`;

  host.innerHTML = `
    <div class="opt-intro"><div><span class="opt-eyebrow">OPTIONS / 学习实验室</span><h2>先理解一笔交易，再亲手试一遍。</h2><p>从权利与义务开始，在虚拟账户里观察价格、时间和波动率如何改变结果。</p></div><span class="opt-mode">教学情景 · 虚拟资金</span></div>
    <nav class="opt-tabs" aria-label="期权专区栏目">${Object.entries(tabs).map(([id,title]) => `<button id="opt-tab-${id}" type="button" data-opt-action="tab" data-tab="${id}" aria-controls="opt-${id}" aria-pressed="${id==='learn'}">${title}</button>`).join('')}</nav>
    <section id="opt-learn" aria-label="期权入门课程">
      <div class="opt-section-heading"><div><h3>一条从零开始的练习路线</h3><p>每课读懂一个问题，回答后查看解释；随时可以进入模拟。</p></div><span id="opt-progress" class="opt-mode"></span></div>
      <div class="opt-lessons">${L.lessons.map((lesson,i) => `<details class="opt-lesson" ${i===0?'open':''}><summary><span class="opt-step">${String(i+1).padStart(2,'0')}</span><span>${esc(lesson.title)}</span></summary><div class="opt-lesson-body"><p class="opt-lead">${esc(lesson.lead)}</p>${lesson.paragraphs.map(p=>`<p>${esc(p)}</p>`).join('')}<fieldset class="opt-quiz"><legend>${esc(lesson.quiz.question)}</legend>${lesson.quiz.choices.map((text,j)=>button('quiz',esc(text),`data-lesson="${i}" data-choice="${j}"`)).join('')}<p id="opt-quiz-${i}" role="status"></p></fieldset>${lesson.strategy?button('practice','亲手试一遍',`data-strategy="${esc(lesson.strategy)}"`,true):''}</div></details>`).join('')}</div>
      <div class="opt-next"><strong>先试一笔现金担保卖 Put</strong><p>看看收进权利金之后，账户为什么没有立刻赚钱。</p>${button('practice','开始第一笔模拟','data-strategy="cash_put"',true)}</div>
    </section>
    <section id="opt-library" aria-label="期权策略库" hidden><div class="opt-section-heading"><div><h3>选择你想理解的策略</h3><p>先看义务和风险，再看可能的收益。所有案例数字都是教学假设。</p></div><span class="opt-mode">8 个可操作策略</span></div><div class="opt-strategies">${L.strategies.map(s=>`<article class="opt-strategy"><div class="opt-strategy-title"><h4>${esc(s.name)}</h4><span>${esc(s.level)}</span></div><p class="opt-lead">${esc(s.summary)}</p><dl><dt>适用判断</dt><dd>${esc(s.outlook)}</dd><dt>你的义务</dt><dd>${esc(s.obligation)}</dd><dt>收益来源</dt><dd>${esc(s.profit)}</dd><dt>承担风险</dt><dd>${esc(s.risk)}</dd></dl><details><summary>看一个例子</summary><p>${esc(s.example)}</p></details>${button('practice','放进模拟工作台',`data-strategy="${esc(s.id)}"`)}</article>`).join('')}</div></section>
    <section id="opt-simulate" aria-label="期权模拟工作台" hidden>
      <div class="opt-section-heading"><div><h3 id="opt-case">${caseLabel}</h3><p>所有价格均为美元。模型值不是市场报价，模拟不会修改实际持仓。</p></div><div class="opt-actions">${button('save','保存本机练习')}${button('restore','恢复已保存')}${button('reset','重新练习')}</div></div>
      <div id="opt-reset-confirm" class="opt-notice" hidden><p>重新练习会清空当前操作过程，已手动保存的练习仍保留。</p>${button('reset-confirm','确认重新开始')}${button('cancel-reset','继续当前练习')}</div>
      <p id="opt-status" role="status" class="opt-status"></p><p id="opt-error" role="alert" class="opt-error"></p>
      <div class="opt-account" aria-label="模拟账户"><div class="opt-equity"><span>账户总净值</span><strong id="opt-equity"></strong><small id="opt-pnl"></small></div><div><span>现金余额</span><strong id="opt-cash"></strong></div><div><span>期权市值 / 负债</span><strong id="opt-option-value"></strong></div><div><span>持股市值</span><strong id="opt-stock-value"></strong></div></div>
      <div class="opt-account-detail"><span>接货预留 <b id="opt-reserved"></b></span><span>可用现金 <b id="opt-available"></b></span><span>持股 <b id="opt-shares"></b></span><span>已实现盈亏 <b id="opt-realized"></b></span><span>累计手续费 <b id="opt-fees"></b></span></div>
      <p class="opt-equation">总净值 = 现金 + 持股市值 + 期权净市值。卖出的期权是负债；收进权利金不等于当场盈利。</p>
      <div class="opt-simulator-grid"><div class="opt-order">
        <form id="opt-open-form"><h4><span class="opt-step">01</span> 设计一笔交易</h4><label class="opt-field" for="opt-strategy"><span>策略</span><select id="opt-strategy">${L.strategies.map(s=>`<option value="${s.id}" ${s.id==='cash_put'?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label>
          <div class="opt-fields">${numberField('capital','虚拟本金',10000,1,1e10)}${numberField('spot','标的价格',60,0,1e6)}${numberField('strike','行权价 / 买入腿',55,.01,1e6)}<div id="opt-strike2-wrap">${numberField('strike2','卖出腿行权价',65,.01,1e6)}</div>${numberField('days','到期天数',30,1,730,'1')}${numberField('iv','隐含波动率 %',80,0,500)}${numberField('contracts','合约张数',1,1,100,'1','每张标准合约 100 股')}${numberField('fee','每张单边手续费',.65,0,100)}</div>
          <p id="opt-obligation" class="opt-notice"></p><button id="opt-open" type="submit" class="primary-button">在虚拟账户开仓</button><p id="opt-order-note" class="opt-muted"></p>
        </form>
        <details class="opt-case-picker"><summary>换一个教学案例</summary><p>案例只填入假设参数，不读取实时行情。已有操作时需先重新练习。</p>${button('example','普通标的：现金担保 Put','data-case="standard"')}${button('example','SOXL：高波动假设','data-case="soxl"')}</details>
      </div><div class="opt-preview"><h4>这组新仓的到期损益</h4><p class="opt-muted">按开仓参数持有到期；不代表账户全部历史盈亏。区间外上涨风险或收益仍按策略延伸。</p><div id="opt-risk" class="opt-risk"></div><div id="opt-chart" class="opt-chart"></div><div id="opt-legs" class="opt-table-wrap"></div><p id="opt-reading" class="opt-notice"></p></div></div>
      <section class="opt-market"><div class="opt-section-heading"><div><h4><span class="opt-step">02</span> 让行情继续走</h4><p>价格、剩余时间与隐含波动率可以分别调整；观察同一笔仓位如何变化。</p></div><div class="opt-actions">${button('scenario','时间过去 7 天','data-scenario="time"')}${button('scenario','下跌 20%','data-scenario="down"')}${button('scenario','反弹 20%','data-scenario="up"')}${button('scenario','波动率 +20 点','data-scenario="vol"')}</div></div>
        <form id="opt-market-form"><div class="opt-fields">${numberField('next-spot','新的标的价格',60,0,1e6)}${numberField('next-days','剩余天数',0,0,730,'1')}${numberField('next-iv','新的隐含波动率 %',80,0,500)}</div><button id="opt-advance" type="submit" class="secondary-button">更新情景</button></form>
        <div class="opt-actions">${button('close','平掉期权','id="opt-close"')}${button('expiry','推进到到期日','id="opt-expiry"')}${button('settle','执行到期结算','id="opt-settle"')}${button('sell-shares','卖出剩余持股','id="opt-sell-shares"')}${button('undo','撤销上一步','id="opt-undo"')}</div>
        <div id="opt-next-step" class="opt-notice" role="status"></div>
        <details id="opt-roll-section"><summary>调整持仓：展期</summary><p>旧合约按当前理论价平仓，再开新合约。净收入、已实现亏损和新增义务会分别入账；不能用“又收到钱”抹去旧亏损。</p><form id="opt-roll-form"><div class="opt-fields">${numberField('roll-strike','新行权价',50,.01,1e6)}${numberField('roll-days','从现在起的新期限',60,1,730,'1')}</div><button id="opt-roll" type="submit" class="secondary-button">平旧仓并开新仓</button></form></details>
      </section>
      <section class="opt-journal"><h4><span class="opt-step">03</span> 回看每一步的代价</h4><p>查看操作前后净值，而不只累计收到的权利金。保存后可关闭页面，下次恢复继续练习。</p><div id="opt-log" class="opt-table-wrap" tabindex="0" role="region" aria-label="模拟操作记录"></div><p class="opt-muted">手动保存只保留本浏览器最近一份练习；再次保存会覆盖。真实账户与其他组合不受影响。</p></section>
    </section>
    <section id="opt-reference" aria-label="术语及模拟规则" hidden><h3>随用随查</h3><dl class="opt-glossary">${L.glossary.map(g=>`<div><dt>${esc(g.term)}</dt><dd>${esc(g.meaning)}<p>${esc(g.example)}</p></dd></div>`).join('')}</dl><section class="opt-model"><h3>这次模拟怎样算</h3><p>定价采用欧式、无股息 Black–Scholes 理论模型，无风险利率固定为年化 4%，按 365 天计年；所有行情、期限、波动率和手续费都是你设置的教学假设。</p><p>${esc(C.settlementPolicy)}</p><p>不模拟真实美式提前行权、逐腿指派、成交价差、流动性、保证金追缴、股息、利息收入和税费。实盘价差组合也可能遇到提前指派，不能直接套用这里的现金结算。到期价恰等于行权价时，本模型不交割；真实券商处理可能不同。</p><p>账户只模拟一个标的；新增策略会继承现有持股。到期损益图按这组策略开仓时的标的价格计算，账户净值则包含此前操作。SOXL 的案例参数不是实时价格，也不自动将每日涨跌乘三。</p></section><h3>继续阅读官方资料</h3><ul class="opt-sources">${L.sources.map(s=>`<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></li>`).join('')}</ul></section>`;

  function showTab(tab) {
    if (!Object.prototype.hasOwnProperty.call(tabs,tab)) return;
    for (const id of Object.keys(tabs)) { q('opt-'+id).hidden = id !== tab; q('opt-tab-'+id).setAttribute('aria-pressed',String(id===tab)); }
  }
  function progress() { q('opt-progress').textContent = `已答对 ${completed.size} / ${L.lessons.length} 课测验`; }
  function error(message='') { q('opt-error').textContent = message; }
  function num(id) { const raw=q('opt-'+id).value; if (String(raw).trim()==='' || !Number.isFinite(Number(raw))) throw new Error('请填写完整、有效的数字。'); return Number(raw); }
  function params() { return {spot:num('spot'),strike:num('strike'),strike2:num('strike2'),days:num('days'),iv:num('iv')/100,contracts:num('contracts'),fee:num('fee')}; }
  function selectStrategy(id) {
    if (!C.strategyIds.includes(id)) throw new Error('未找到这个策略。');
    if (account.position) throw new Error('当前还有期权持仓，请先平仓或到期结算，再尝试新策略。');
    q('opt-strategy').value = id;
    const spot=account.shares?account.spot:(Number(q('opt-spot').value)||60);
    const p={...C.defaults,spot,strike:id==='bear_put_spread'?spot*1.1:['long_call','long_put','long_straddle'].includes(id)?spot:id==='covered_call'?spot*1.1:spot*.9,strike2:id==='bear_put_spread'?spot*.9:spot*1.1};
    for (const key of ['spot','strike','strike2','days','contracts','fee']) q('opt-'+key).value=String(Math.round(p[key]*100)/100);
    q('opt-iv').value=String(p.iv*100);
    preview();
  }
  function chart(result) {
    const points=result.curve, high=points[points.length-1].spot;
    const lowY=Math.min(0,...points.map(p=>p.pnl)), highY=Math.max(0,...points.map(p=>p.pnl)), span=highY-lowY||1;
    const x=v=>54+v/high*478, y=v=>198-(v-lowY)/span*170;
    const poly=points.map(p=>`${x(p.spot).toFixed(2)},${y(p.pnl).toFixed(2)}`).join(' ');
    return `<svg viewBox="0 0 560 242" role="img" aria-label="到期损益图，横轴是到期标的价格，纵轴是美元损益"><title>到期损益，非价格预测</title><line class="opt-chart-axis" x1="54" y1="${y(0)}" x2="532" y2="${y(0)}"/><line class="opt-chart-axis" x1="54" y1="28" x2="54" y2="198"/><polyline class="opt-chart-line" points="${poly}" fill="none"/><text x="50" y="17">${esc(money(highY))}</text><text x="50" y="214">${esc(money(lowY))}</text><text x="54" y="234">标的 $0</text><text x="532" y="234" text-anchor="end">${esc(money(high))}</text></svg>`;
  }
  function preview() {
    const id=q('opt-strategy').value, item=L.strategies.find(s=>s.id===id);
    q('opt-strike2-wrap').hidden=!['bull_call_spread','bear_put_spread'].includes(id);
    q('opt-obligation').textContent=item?.obligation || '';
    try {
      const p=account.position?account.position.params:params(), r=C.analyze(account.position?.id||id,p);
      q('opt-risk').innerHTML=[['最多盈利',money(r.maxProfit)],['最多亏损',money(r.maxLoss)],['到期盈亏平衡',r.breakevens.length?r.breakevens.map(money).join(' / '):'无盈亏平衡点'],['独立建仓所需资金',money(r.capitalRequired)]].map(([title,value])=>`<div><span>${title}</span><strong>${value}</strong></div>`).join('');
      q('opt-chart').innerHTML=chart(r);
      q('opt-legs').innerHTML=`<table><caption>合约组成 · 每股理论权利金</caption><thead><tr><th>方向</th><th>类型</th><th>张数</th><th>行权价</th><th>权利金</th></tr></thead><tbody>${r.strategy.legs.map(l=>`<tr><td>${l.quantity>0?'买入':'卖出'}</td><td>${l.type==='call'?'Call':'Put'}</td><td>${Math.abs(l.quantity)}</td><td>${money(l.strike)}</td><td>${money(l.premium)}</td></tr>`).join('')}</tbody></table><details><summary>查看到期情景数字</summary><table><thead><tr><th>到期价格</th><th>这组新仓的损益</th></tr></thead><tbody>${[0,p.spot*.7,p.spot,p.spot*1.3].map(spot=>`<tr><td>${money(spot)}</td><td>${money(C.expirationPnl(r.strategy,spot))}</td></tr>`).join('')}</tbody></table></details>`;
      q('opt-reading').textContent=`开仓${r.netPremium>=0?'收取':'支付'}权利金 ${money(Math.abs(r.netPremium))}，另付手续费 ${money(r.fees)}。${item?.risk||''}`;
    } catch (e) { q('opt-risk').innerHTML='';q('opt-chart').innerHTML='';q('opt-legs').innerHTML='';q('opt-reading').textContent=e.message; }
  }
  function renderAccount() {
    const m=C.mark(account), open=Boolean(account.position);
    if(!open&&account.shares) q('opt-spot').value=String(account.spot);
    for (const [id,value] of Object.entries({equity:m.equity,cash:m.cash,'option-value':m.optionValue,'stock-value':m.stockValue,reserved:m.reserved,available:m.available,realized:m.realized,fees:m.fees})) q('opt-'+id).textContent=money(value);
    q('opt-pnl').textContent=`相对初始本金 ${money(m.pnl)} (${(m.pnl/account.initialCapital*100).toFixed(2)}%)`;
    q('opt-pnl').setAttribute('data-tone',m.pnl>0?'positive':m.pnl<0?'negative':'neutral');
    q('opt-shares').textContent=account.shares+' 股';q('opt-case').textContent=caseLabel;
    q('opt-capital').disabled=actions.length>0;
    for (const key of ['strategy','spot','strike','strike2','days','iv','contracts','fee','open']) q('opt-'+key).disabled=open;
    q('opt-order-note').textContent=open?'开仓参数已固定；在下方更新行情，观察持仓变化。':account.shares?'已有股票会保留并参与账户净值；开仓将优先使用可覆盖的持股。':'开仓前可自由修改参数。';
    q('opt-next-spot').value=String(account.spot);q('opt-next-days').value=String(account.days);q('opt-next-days').max=String(account.days);q('opt-next-iv').value=String(Math.round(account.iv*10000)/100);
    q('opt-close').disabled=!open;q('opt-expiry').disabled=!open||account.days===0;q('opt-settle').disabled=!open||account.days!==0;q('opt-sell-shares').disabled=open||!account.shares;q('opt-undo').disabled=!actions.length;
    const canRoll=open&&['cash_put','covered_call'].includes(account.position.id)&&account.days>0;
    q('opt-roll-section').hidden=!canRoll;q('opt-roll').disabled=!canRoll;
    q('opt-roll-days').value=String(Math.min(730,Math.max(account.days+30,30)));
    if(open) q('opt-roll-strike').value=String(Math.max(.01,Math.round(account.position.params.strike*.9*100)/100));
    q('opt-next-step').textContent=open?(account.days===0?'合约已到期：点击“执行到期结算”，查看现金和股票如何变化。':'下一步：试试价格不变只流逝时间，或提高波动率，再比较账户净值。'):account.shares?'你现在持有股票。可以选择备兑 Call 继续轮动，或卖出持股结束这次交易。':'选择策略并开仓，开始观察它的收益与义务。';
    q('opt-log').innerHTML=account.log.length?`<table><thead><tr><th>步骤</th><th>操作与影响</th><th>标的 / 剩余天数</th><th>现金</th><th>账户净值</th><th>总盈亏</th></tr></thead><tbody>${account.log.map((row,i)=>`<tr><td>${i+1}</td><td>${esc(row.message)}</td><td>${money(row.spot)} / ${row.days} 天</td><td>${money(row.cash)}</td><td>${money(row.equity)}</td><td>${money(row.pnl)}</td></tr>`).join('')}</tbody></table>`:'<p class="opt-empty">还没有操作。每一次开仓、行情变化和结算都会记录在这里。</p>';
  }
  function applyAction(book,action) {
    switch(action.type) {
      case 'open': return C.openTrade(book,action.strategy,action.params);
      case 'advance': return C.advance(book,action.params);
      case 'close': return C.closeTrade(book);
      case 'settle': return C.settle(book);
      case 'roll': return C.rollTrade(book,action.params);
      case 'sell-shares': return C.sellShares(book);
      default: throw new Error('保存的练习包含无法识别的操作。');
    }
  }
  function transact(action) {
    if(actions.length>=500) throw new Error('本次练习已达到 500 步，请保存后重新练习。');
    const base=!actions.length?C.createAccount(num('capital')):account;
    const next=applyAction(base,action);
    account=next;actions.push(action);error();q('opt-status').textContent='操作已记录；记得手动保存本机练习。';renderAccount();preview();
  }
  function syncOrder() {
    q('opt-capital').value=String(account.initialCapital);
    const opened=account.position || [...actions].reverse().find(a=>a.type==='open');
    if(opened) {
      q('opt-strategy').value=opened.id||opened.strategy;
      const p=opened.params;
      for(const key of ['spot','strike','strike2','days','contracts','fee']) q('opt-'+key).value=String(p[key]??C.defaults[key]);
      q('opt-iv').value=String(p.iv*100);
    }
    if(!account.position&&account.shares) q('opt-spot').value=String(account.spot);
  }
  function restore() {
    const text=localStorage.getItem(SAVE_KEY);
    if(!text) throw new Error('还没有保存的练习。先开仓，再点击“保存本机练习”。');
    if(text.length>500000) throw new Error('保存的练习过大，未载入。');
    const saved=JSON.parse(text);
    if(saved?.version!==1||!Array.isArray(saved.actions)||saved.actions.length>500) throw new Error('保存的练习格式无效，当前练习保持不变。');
    let next=C.createAccount(saved.capital);
    for(const action of saved.actions) { if(!action||typeof action!=='object') throw new Error('保存的操作格式无效。');next=applyAction(next,action); }
    account=next;actions=saved.actions;caseLabel=saved.case==='soxl'?'SOXL · 高波动假设案例':'通用标的 · 假设行情';
    syncOrder();renderAccount();preview();showTab('simulate');q('opt-status').textContent='已从保存的操作逐步重算并恢复。';
  }
  host.addEventListener('submit',event=>{
    event.preventDefault();error();
    try {
      if(event.target.id==='opt-open-form') transact({type:'open',strategy:q('opt-strategy').value,params:params()});
      if(event.target.id==='opt-market-form') transact({type:'advance',params:{spot:num('next-spot'),days:num('next-days'),iv:num('next-iv')/100}});
      if(event.target.id==='opt-roll-form') { transact({type:'roll',params:{strike:num('roll-strike'),days:num('roll-days'),iv:account.iv}});syncOrder();preview(); }
    } catch(e) { error(e.message); }
  });
  host.addEventListener('input',event=>{ if(['opt-spot','opt-strike','opt-strike2','opt-days','opt-iv','opt-contracts','opt-fee'].includes(event.target.id)) preview(); });
  host.addEventListener('change',event=>{
    try {
      if(event.target.id==='opt-strategy') { selectStrategy(event.target.value);error(); }
      if(event.target.id==='opt-capital'&&!actions.length) { account=C.createAccount(num('capital'));renderAccount();error(); }
    } catch(e) { error(e.message); }
  });
  host.addEventListener('click',event=>{
    const control=event.target.closest('[data-opt-action]');if(!control)return;
    const d=control.dataset;error();
    try {
      switch(d.optAction) {
        case 'tab': showTab(d.tab);break;
        case 'practice': showTab('simulate');selectStrategy(d.strategy);break;
        case 'quiz': {
          const index=Number(d.lesson),lesson=L.lessons[index];if(!lesson)return;
          const correct=Number(d.choice)===lesson.quiz.answer;
          q('opt-quiz-'+index).textContent=(correct?'答对了。':'再想一想。')+lesson.quiz.explanation;
          if(correct) { completed.add(lesson.id);progress();try{localStorage.setItem(PROGRESS_KEY,JSON.stringify([...completed]));}catch{q('opt-quiz-'+index).textContent+=' 学习进度暂时无法保存。';} }
          break;
        }
        case 'scenario': {
          if(!account.position&&!account.shares) throw new Error('请先开仓，再观察行情变化。');
          const p={spot:account.spot,days:account.days,iv:account.iv};
          if(d.scenario==='time')p.days=Math.max(0,p.days-7);
          if(d.scenario==='down')p.spot=Math.round(p.spot*.8*100)/100;
          if(d.scenario==='up')p.spot=Math.round(p.spot*1.2*100)/100;
          if(d.scenario==='vol')p.iv=Math.min(5,p.iv+.2);
          transact({type:'advance',params:p});break;
        }
        case 'close': transact({type:'close'});break;
        case 'settle': transact({type:'settle'});syncOrder();preview();break;
        case 'sell-shares': transact({type:'sell-shares'});break;
        case 'expiry': transact({type:'advance',params:{spot:account.spot,days:0,iv:account.iv}});break;
        case 'undo': {
          if(!actions.length)break;
          const remaining=actions.slice(0,-1);let next=C.createAccount(account.initialCapital);
          for(const action of remaining)next=applyAction(next,action);
          account=next;actions=remaining;syncOrder();renderAccount();preview();q('opt-status').textContent='已撤销上一步，可以试另一种选择。';break;
        }
        case 'save': localStorage.setItem(SAVE_KEY,JSON.stringify({version:1,capital:account.initialCapital,case:caseLabel.startsWith('SOXL')?'soxl':'standard',actions}));q('opt-status').textContent='练习已保存到本浏览器；下次可恢复继续。';break;
        case 'restore': restore();break;
        case 'reset': q('opt-reset-confirm').hidden=false;break;
        case 'cancel-reset': q('opt-reset-confirm').hidden=true;break;
        case 'reset-confirm': account=C.createAccount(num('capital'));actions=[];caseLabel='通用标的 · 假设行情';q('opt-reset-confirm').hidden=true;selectStrategy('cash_put');renderAccount();q('opt-status').textContent='新的练习已开始；之前手动保存的练习仍保留。';break;
        case 'example': {
          if(actions.length) throw new Error('请先点击“重新练习”，避免把当前持股换成另一标的。');
          caseLabel=d.case==='soxl'?'SOXL · 高波动假设案例':'通用标的 · 假设行情';selectStrategy('cash_put');
          q('opt-spot').value='60';q('opt-strike').value=d.case==='soxl'?'40':'55';q('opt-iv').value=d.case==='soxl'?'120':'40';
          q('opt-case').textContent=caseLabel;preview();q('opt-status').textContent='已填入假设参数，请先查看资金需求与最大亏损，再开仓。';break;
        }
      }
    } catch(e) { error(e.message || '操作未完成，当前练习保持不变。'); }
  });
  progress();showTab('learn');renderAccount();preview();
})();
