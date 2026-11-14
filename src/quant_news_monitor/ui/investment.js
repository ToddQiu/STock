(function () {
  'use strict';
  const C = window.InvestmentCore, esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (value, digits = 2) => value == null || !Number.isFinite(value) ? '--' : value.toLocaleString('zh-CN', {maximumFractionDigits: digits, minimumFractionDigits: digits});
  const today = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
  const uid = prefix => prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,8);
  const M = {items: [], id:'', tab:'holdings', mode:'actual', page:0, search:'', exposureKey:'pct', exposureOrder:'desc', planSort:{key:'',order:'desc'}, busy:false, preview:null, includeArchived:false, extraFunds:{}};
  const portfolio = document.querySelector('[data-view="portfolio"]'), original = document.createElement('div');
  original.id = 'investment-original';
  const commandbar = portfolio.querySelector('.portfolio-commandbar');
  commandbar.remove();
  while (portfolio.firstChild) original.append(portfolio.firstChild);
  const tabs = document.createElement('div'); tabs.className='investment-tabs'; tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label','组合栏目');
  const sections = [['holdings','我的资产'],['analysis','组合分析'],['dividends','股息'],['plan','配置计划'],['trades','方案流水'],['exposure','ETF 底层持仓']];
  tabs.innerHTML = sections.map(([id,label])=>`<button type="button" role="tab" id="investment-tab-${id}" data-section="${id}" aria-controls="${id==='holdings'?'investment-original':id==='analysis'?'portfolio-insights':id==='dividends'?'portfolio-dividends':'investment-assets'}" aria-selected="${id==='holdings'}" aria-pressed="${id==='holdings'}" tabindex="${id==='holdings'?0:-1}">${label}</button>`).join('');
  original.setAttribute('role','tabpanel'); original.setAttribute('aria-labelledby','investment-tab-holdings');
  const root = document.createElement('section'); root.className='investment-surface'; root.hidden=true; root.id='investment-assets';
  root.setAttribute('role','tabpanel');
  const navigation = document.createElement('div'); navigation.className='investment-navigation';
  const converters = document.createElement('div'); converters.className='portfolio-fx-converters'; converters.setAttribute('aria-label','人民币汇率换算器');
  converters.innerHTML = [['HKD','港币','人民币'],['USD','美元','人民币'],['CNY_HKD','人民币','港币']].map(([code,label,target])=>`<section class="portfolio-fx-converter" data-fx="${code}"><label for="portfolio-fx-${code}">${label} → ${target} <span data-fx-rate>暂无汇率</span></label><div><input id="portfolio-fx-${code}" type="text" inputmode="decimal" value="1.00" aria-label="${label}换算${target}金额"><span aria-hidden="true">=</span><output for="portfolio-fx-${code}" aria-live="polite">--</output></div></section>`).join('');
  navigation.append(tabs,converters);
  portfolio.append(commandbar,navigation,original,root);
  function renderFx() {
    const snapshot=state.dashboard?.portfolio||{};
    converters.querySelectorAll('[data-fx]').forEach(group=>{
      const code=group.dataset.fx,reverse=code==='CNY_HKD',raw=snapshot.fx_rates?.[reverse?'HKD':code],input=group.querySelector('input');
      const rate=reverse?(Number.isFinite(raw)&&raw>0?1/raw:null):raw;
      const valid=Number.isFinite(rate)&&rate>0,amount=portfolioPrincipalValue(input.value);
      input.setAttribute('aria-invalid',String(amount===null&&input.value.trim()!==''));
      group.querySelector('output').textContent=valid&&amount!==null?(reverse?'HK$':'¥')+num(amount*rate):'--';
      group.querySelector('[data-fx-rate]').textContent=valid?'参考 '+num(rate,4):'暂无汇率';
      const legs=code==='HKD'||reverse?['USD_CNY','USD_HKD']:['USD_CNY'];
      group.title=(valid?`1 ${reverse?'CNY':code} = ${num(rate,4)} ${reverse?'HKD':'CNY'}`:'等待有效汇率')+'\n'+legs.map(key=>{
        const quote=snapshot.fx_details?.[key];
        return quote?`${key} · ${quote.source||''} · ${quote.quote_time?formatDate(quote.quote_time,true):'时间未知'}${quote.error?' · 更新失败，保留参考值':''}`:'';
      }).filter(Boolean).join('\n');
    });
  }
  converters.addEventListener('input',renderFx);
  converters.addEventListener('focusout',event=>{if(event.target.matches('input')){const amount=portfolioPrincipalValue(event.target.value);if(amount!==null)event.target.value=formatPortfolioPrincipal(amount);}});
  const research = document.createElement('section'); research.className='investment-surface investment-section'; research.id='investment-research';
  document.getElementById('research-allocation-panel').append(research);
  const error = document.createElement('p'); error.className='investment-error'; error.setAttribute('role','alert'); portfolio.insertBefore(error,root);
  const dialog = document.createElement('dialog'); dialog.className='investment-dialog investment-surface'; document.body.append(dialog);
  const current = () => M.items.find(row=>row.plan.id===M.id);
  function message(value) {error.textContent=value || ''; const r=research.querySelector('[data-error]'); if(r)r.textContent=value||'';}
  function table(headers, rows) {return `<div class="investment-table-wrap"><table class="investment-table"><thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
  const td = value => `<td>${esc(value)}</td>`;
  const money = value => `<td class="money" data-sort-value="${Number.isFinite(value)?value:''}">${num(value)}</td>`;
  function sortHeader(label,key) {
    return `<button type="button" class="investment-sort" data-sort="${key}" title="按${label}排序">${label}<svg class="ui-icon" aria-hidden="true"><use href="/icons.svg#arrow-down"></use></svg></button>`;
  }
  function compareValues(a,b,order) {
    const missing=v=>v==null||v===''||(typeof v==='number'&&!Number.isFinite(v));
    if(missing(a))return missing(b)?0:1;
    if(missing(b))return -1;
    return (order==='asc'?1:-1)*(typeof a==='number'?a-b:String(a).localeCompare(String(b),'zh-CN',{numeric:true}));
  }
  function markSort(key,order) {
    root.querySelectorAll('th:has([data-sort])').forEach(th=>th.setAttribute('aria-sort',th.querySelector(`[data-sort="${key}"]`)?(order==='asc'?'ascending':'descending'):'none'));
    root.querySelectorAll('[data-sort]').forEach(button=>{
      const active=button.dataset.sort===key;
      button.classList.toggle('is-sorted',active);
      button.querySelector('use').setAttribute('href','/icons.svg#arrow-'+(active&&order==='asc'?'up':'down'));
      const next=active?(order==='asc'?'降序':'升序'):(['name','category','source'].includes(button.dataset.sort)?'升序':'降序');
      button.title=`按${button.textContent}排序：${next}`;
    });
  }
  function sortPlanRows() {
    const body=root.querySelector('[data-form="budget"] tbody'),{key,order}=M.planSort;
    if(!body||!key)return;
    const value=row=>{
      if(key==='name')return row.querySelector('strong').textContent;
      if(key==='category')return row.dataset.category;
      const prefix={amount:'amount_',price:'price_',date:'date_'}[key];
      if(prefix){const input=row.querySelector(`[name="${prefix}${row.dataset.planIndex}"]`);return key==='date'?input.value:input.valueAsNumber;}
      const raw=row.cells[key==='actual'?2:3].dataset.sortValue;
      return raw===''?null:Number(raw);
    };
    // Move existing rows so unsaved input values and their target indexes stay intact.
    [...body.rows].sort((a,b)=>compareValues(value(a),value(b),order)).forEach(row=>body.append(row));
    markSort(key,order);
  }
  function toggleSort(button) {
    const key=button.dataset.sort,initial=['name','category','source'].includes(key)?'asc':'desc';
    if(M.tab==='plan'){
      M.planSort={key,order:M.planSort.key===key?(M.planSort.order==='asc'?'desc':'asc'):initial};
      sortPlanRows();
    }else if(M.tab==='exposure'){
      M.exposureOrder=M.exposureKey===key?(M.exposureOrder==='asc'?'desc':'asc'):initial;
      M.exposureKey=key;M.page=0;renderExposure(current().plan);
      root.querySelector(`[data-sort="${key}"]`).focus();
    }
  }
  function icon(action, name, title) {return `<button type="button" class="icon-button" data-action="${action}" title="${title}" aria-label="${title}"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/icons.svg#${name}"></use></svg></button>`;}
  function field(label,name,value='',type='text',extra='') {return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;}
  function textarea(label,name,value='') {return `<label class="wide">${label}<textarea name="${name}">${esc(value)}</textarea></label>`;}
  function options(p, selected='') {return p.targets.map(t=>`<option value="${esc(t.key)}" ${selected===t.key?'selected':''}>${esc(t.name)} · ${esc(t.symbol)}</option>`).join('');}
  function toolbar() {
    const rows=M.items.filter(r=>M.includeArchived||!r.plan.archived);
    return `<div class="investment-toolbar"><select data-plan aria-label="配置方案">${rows.length?rows.map(r=>`<option value="${esc(r.plan.id)}" ${r.plan.id===M.id?'selected':''}>${esc(r.plan.name)}${r.plan.archived?' · 已归档':''}</option>`).join(''):'<option>尚无方案</option>'}</select>
      <button type="button" class="secondary-button" data-action="new">新建方案</button><button type="button" class="secondary-button" data-action="import">导入 Excel / 方案</button>
      ${icon('refresh','refresh-cw','刷新方案')}${current()?icon('export','download','导出当前私人方案'):''}
      <button type="button" class="text-button" data-action="archived">${M.includeArchived?'隐藏':'显示'}归档</button></div>`;
  }
  function actualRows(p) {
    return ((state.dashboard||{}).portfolio?.positions||[]).map(r=>({...r, amount:p.currency==='CNY'?r.market_value_cny:r.currency===p.currency?r.market_value:null,
      is_etf:Boolean(p.funds[C.key(r.market,r.symbol)]||M.extraFunds[C.key(r.market,r.symbol)]||p.targets.find(t=>t.key===C.key(r.market,r.symbol))?.is_etf||r.asset_type==='etf'||r.market==='FUND')}));
  }
  function reference(p,target) {
    const row=((state.dashboard||{}).portfolio?.positions||[]).find(r=>C.key(r.market,r.symbol)===target.key&&r.currency===target.currency);
    return row && row.price>0 ? {price:row.price,date:row.quote_time||'',source:row.quote_source||'持仓报价'} : target.reference_price>0&&target.reference_date ? {price:target.reference_price,date:target.reference_date,source:'方案手动参考价'} : {price:null,date:'',source:'未取得有效参考价'};
  }
  function ledgerRows(p) {return C.ledger(p).positions.map(r=>({...r, amount:reference(p,r).price==null?null:r.quantity*reference(p,r).price}));}
  function renderPlan(p) {
    const actual=actualRows(p), book=ledgerRows(p), stress=C.stress(p);
    root.innerHTML=toolbar()+`<p class="investment-source">${esc(p.source.filename||'本机方案')} · 方案日期 ${esc(p.as_of||'未填写')} · v${current().version} · ${esc(p.currency)}</p>
      <form data-form="budget"><div class="investment-fields">${field('方案名称','name',p.name,'text','required maxlength="120"')}<label>记账币种<select name="currency" ${p.targets.length||p.trades.length?'disabled':''}>${C.currencies.map(c=>`<option ${c===p.currency?'selected':''}>${c}</option>`).join('')}</select></label>${field('总预算','budget',p.budget,'number','required min="0" step="0.01"')}${field('现金预留','cash_target',p.cash_target,'number','required min="0" step="0.01"')}${field('方案日期','as_of',p.as_of,'date')}${icon('submit','save','保存配置计划')}</div>
      ${table([sortHeader('标的','name')+' / '+sortHeader('分类','category'),sortHeader('目标金额','amount'),sortHeader('当前持仓市值','actual'),sortHeader('方案账本市值','book'),sortHeader('参考价','price')+' / '+sortHeader('日期','date')],p.targets.map((t,i)=>`<tr data-plan-index="${i}" data-category="${esc(t.category)}"><td><strong>${esc(t.name)}</strong><small>${esc(t.key)} · ${esc(t.category)}</small></td><td><input name="amount_${i}" type="number" min="0" step="0.01" value="${t.amount}" aria-label="${esc(t.name)}目标金额" required></td>${money(actual.find(r=>C.key(r.market,r.symbol)===t.key)?.amount??null)}${money(book.filter(r=>r.key===t.key).some(r=>r.amount==null)?null:book.filter(r=>r.key===t.key).reduce((s,r)=>s+r.amount,0))}<td><input name="price_${i}" type="number" min="0.000001" step="any" value="${t.reference_price??''}" aria-label="${esc(t.name)}参考价"><input name="date_${i}" type="date" value="${esc(t.reference_date)}" aria-label="${esc(t.name)}参考价日期"></td></tr>`))}</form>
      <p class="investment-note">配置预算不是实际资产。当前持仓与方案账本单独核对，不相加；未取得市值显示 --。手动参考价只用于带日期的估算。</p>
      <div class="investment-stats"><div><span>目标证券金额</span><strong>${num(p.targets.reduce((s,r)=>s+r.amount,0))}</strong></div><div><span>未分配预算</span><strong>${num(p.budget-p.cash_target-p.targets.reduce((s,r)=>s+r.amount,0))}</strong></div><div><span>情景损益估算</span><strong>${num(stress.pnl)}</strong></div></div>
      <p class="investment-note">${stress.complete?'情景损益按目标金额测算，不是预测。':'情景假设未填全；已填部分损益 '+num(stress.known_pnl)+'，不能代表全组合。'}</p>
      <details class="investment-section"><summary>添加配置标的</summary><form data-form="target"><div class="investment-fields">${field('市场:代码','key','','text','required placeholder="CN:600000"')}${field('名称','name','','text','required')}${field('类别','category')}${field('目标金额','amount',0,'number','min="0" required step="0.01"')}<label>类型<select name="type"><option value="stock">股票</option><option value="etf">ETF / 基金</option></select></label><button class="primary-button" type="submit">添加标的</button></div></form></details>
      <details class="investment-section"><summary>A 股打新条件备忘</summary><p class="investment-note">ETF 市值与普通股票市值分开。申购资格、历史日均市值与权限以券商为准；这里仅保存人工核对记录，不自动申购。</p><button class="secondary-button" type="button" data-action="research">记录核对结论</button></details>
      <button class="text-button" type="button" data-action="archive">${p.archived?'恢复方案':'归档方案'}</button>`;
    root.querySelector('[data-action="submit"]').type='submit';
    sortPlanRows();
  }
  const tradeNames={deposit:'入金',withdraw:'出金',initial:'期初持仓',buy:'买入',sell:'卖出',dividend:'分红'};
  function renderTrades(p) {
    const book=C.ledger(p);
    root.innerHTML=toolbar()+`<h3>方案交易账本</h3><p class="investment-note">独立记录 ${esc(p.name)} 的成交，不自动修改“当前持仓”。首次使用先录入实际现金和期初持仓；期初价格是成本，不是实时价格。</p>
      <form data-form="trade"><div class="investment-fields">${field('交易日','date',today(),'date','required')}${field('账户','account','方案账户','text','required maxlength="80"')}
      <label>类型<select name="kind">${Object.entries(tradeNames).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
      <label>标的<select name="target"><option value="">出入金不选标的</option>${options(p)}</select></label>
      ${field('数量','quantity','','number','min="0.000001" step="any"')}${field('成交价 / 期初成本','price','','number','min="0.000001" step="any"')}${field('费用','fee',0,'number','required min="0" step="0.01"')}${field('出入金 / 税前分红','amount','','number','min="0.01" step="0.01"')}${field('备注','note')}<button class="primary-button" type="submit">记录流水</button></div></form>
      ${table(['账户','币种','现金','净投入（含期初成本）','已实现收益 / 分红'],book.accounts.map(r=>`<tr>${td(r.account)}${td(r.currency)}${money(r.cash)}${money(r.net_funding)}${money(r.realized)}</tr>`))}
      ${table(['标的 / 账户','数量','平均成本','成本金额','参考市值','浮动盈亏'],book.positions.map(r=>{const price=reference(p,r),value=price.price==null?null:r.quantity*price.price;return `<tr><td>${esc(r.name)}<small>${esc(r.account)} · ${esc(price.source)} ${esc(price.date)}</small></td>${money(r.quantity)}${money(r.average_cost)}${money(r.cost_amount)}${money(value)}${money(value==null?null:value-r.cost_amount)}</tr>`;}))}
      ${table(['日期 / 账户','类型 / 标的','数量 × 价格','资金 / 费用','备注','操作'],p.trades.slice().reverse().map(r=>`<tr><td>${esc(r.date)}<small>${esc(r.account)}</small></td><td>${tradeNames[r.kind]}<small>${esc(r.target)}</small></td>${td(r.quantity==null?'--':num(r.quantity)+' × '+num(r.price))}${td(num(r.amount)+' / '+num(r.fee))}${td(r.note)}<td><button type="button" class="text-button" data-delete-trade="${esc(r.id)}">删除记录</button></td></tr>`))}
      ${!p.trades.length?'<p class="investment-empty">尚无实际流水。配置金额不会自动生成成交。</p>':''}`;
  }
  function renderExposure(p) {
    const inputs=M.mode==='target'?p.targets:M.mode==='book'?ledgerRows(p):actualRows(p);
    const result=C.exposure(inputs,{...M.extraFunds,...p.funds},M.mode==='target'?p.budget:null);
    const value=r=>M.exposureKey==='date'?r.sources.map(s=>s.as_of||'').sort().at(-1):M.exposureKey==='source'?r.sources.map(s=>s.fund).join(' / '):r[M.exposureKey];
    const filtered=result.rows.filter(r=>(r.name+' '+r.key).toLowerCase().includes(M.search.toLowerCase()))
      .sort((a,b)=>compareValues(value(a),value(b),M.exposureOrder));
    M.page=Math.min(M.page,Math.max(0,Math.ceil(filtered.length/50)-1)); const page=filtered.slice(M.page*50,M.page*50+50);
    root.innerHTML=toolbar()+`<h3>ETF 底层持仓</h3><div class="investment-toolbar"><select data-mode aria-label="穿透金额来源"><option value="actual" ${M.mode==='actual'?'selected':''}>当前持仓</option><option value="target" ${M.mode==='target'?'selected':''}>配置计划</option><option value="book" ${M.mode==='book'?'selected':''}>方案账本</option></select><input data-search type="search" placeholder="公司名称或代码" value="${esc(M.search)}" aria-label="搜索底层持仓"><span>${filtered.length} 个底层项目</span></div>
      <p class="investment-note">${M.mode==='target'?'按目标金额，比例以总预算为分母。':'按已有市值估算，比例以已选证券市值为分母。'}成分权重来自披露报告，非当日实时持仓；未知代码不跨基金强行合并。</p>
      ${result.missing.map(s=>`<p class="investment-error">${esc(s)}</p>`).join('')}
      ${table([sortHeader('底层标的','name'),sortHeader('ETF 间接暴露','indirect'),sortHeader('合计金额','total'),sortHeader('占比 %','pct'),sortHeader('来源','source')+' / '+sortHeader('报告日','date')],page.map(r=>`<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.key)}</small></td>${money(r.indirect)}${money(r.total)}${money(r.pct)}<td>${r.sources.map(s=>`<small>${esc(s.fund)} · ${esc(s.as_of)} · ${num(s.amount)}</small>`).join('')||'直接持股'}</td></tr>`))}
      <div class="investment-toolbar"><button type="button" class="secondary-button" data-action="prev" ${M.page===0?'disabled':''}>上一页</button><span>${M.page+1} / ${Math.max(1,Math.ceil(filtered.length/50))}</span><button type="button" class="secondary-button" data-action="next" ${(M.page+1)*50>=filtered.length?'disabled':''}>下一页</button></div>
      ${table(['ETF','持仓报告日','对应金额','已映射金额','未映射余额'],result.breakdown.map(r=>`<tr>${td(r.name)}${td(r.as_of)}${money(r.amount)}${money(r.mapped)}${money(r.residual)}</tr>`))}
      <p class="investment-note">未映射余额可能包含现金、其他资产或披露精度差异，不将股票权重强行调整到 100%。负余额也保留供核对。</p>`;
    markSort(M.exposureKey,M.exposureOrder);
  }
  function renderResearch(p) {
    research.innerHTML=`<h3>配置研究 · 估值与复盘</h3><p class="investment-error" data-error role="alert"></p>`+toolbar();
    if (!p) {research.innerHTML+='<p class="investment-empty">导入配置方案后，在这里查看原始研究与估值假设。</p>';return;}
    research.innerHTML+=`<p class="investment-source">${esc(p.source.filename||p.name)} · 文件中的评级是人工历史观点，非 STock 当前结论。</p>`+
      p.research.map((r,i)=>`<details class="investment-section"><summary>${esc(r.name)} · ${esc(r.target)} · 原评级 ${esc(r.original_rating||'未填')}</summary><p class="investment-note">资料日期 ${esc(r.as_of||'未填写')} · ${esc(r.business)}</p><form data-form="research" data-index="${i}"><div class="investment-fields">${textarea('原始投资逻辑 / 后续补充','thesis',r.thesis)}${textarea('风险与反证','risks',r.risks)}${textarea('复盘备注','review',r.review)}${field('来源网址','source_url',r.source_url,'url')}${field('资料日期','as_of',r.as_of,'date')}<button class="secondary-button" type="submit">保存研究版本</button></div></form></details>`).join('');
    research.innerHTML+=`<details class="investment-section" open><summary>条件估值</summary><form data-form="valuation"><div class="investment-fields"><label>标的<select name="target">${options(p)}</select></label><label>情景<select name="scenario"><option value="base">中性</option><option value="bull">看多</option><option value="bear">看空</option></select></label>${field('正常化 EPS','eps','','number','min="0" step="any"')}${field('年增长率 %','growth','','number','min="-99.99" max="1000" step="any"')}${field('目标 PE','pe','','number','min="0.01" step="any"')}${field('折现率 %','discount','','number','min="0" step="any"')}${field('分红现值 / 股','dividends','','number','min="0" step="any"')}${field('年数','years',4,'number','min="1" max="50" required')}${field('资料日期','as_of','','date')}${field('来源网址','source_url','','url')}${textarea('成立条件','conditions')}${textarea('失效条件','invalidation')}<button class="primary-button" type="submit">保存估值假设</button></div><p class="investment-result" data-valuation-result>缺失输入不计算估值；不计分红时明确填 0。</p></form>
      ${table(['标的 / 情景','折现价值','相对参考价 %','参考价来源 / 日期','成立 / 失效条件'],p.valuations.map(r=>{const t=p.targets.find(t=>t.key===r.target),ref=reference(p,t),v=C.valuation(r,ref.price);return `<tr>${td((t?.name||r.target)+' / '+({base:'中性',bull:'看多',bear:'看空'}[r.scenario]))}${money(v.value)}${money(v.upside)}<td>${esc(ref.source)}<small>${esc(ref.date)}</small></td><td>${esc(r.conditions||'待补充')}<small>${esc(r.invalidation||'待补充')}</small></td></tr>`;}))}
      <p class="investment-note">估值由填写的假设计算，不是上涨概率。未填完整输入不产出数值，没有预期基准不称超预期。</p></details>
      <section class="investment-section"><h3>决策与复盘记录</h3><form data-form="review"><div class="investment-fields">${field('日期','date',today(),'date','required')}${field('标题','title','','text','required maxlength="160"')}<label>标的<select name="target"><option value="">整个方案</option>${options(p)}</select></label><label>判断<select name="decision"><option>待核验</option><option>有条件看多</option><option>中性</option><option>有条件看空</option><option>原判断失效</option><option>打新资格核对</option></select></label>${field('来源网址','source_url','','url')}${textarea('证据、反证、条件与后续核对','body')}<button class="primary-button" type="submit">保存复盘</button></div></form>
      ${p.reviews.slice().reverse().map(r=>`<article class="investment-review-item"><strong>${esc(r.title)}</strong><small> · ${esc(r.date)} · ${esc(r.decision)} · ${esc(r.target||'整个方案')}</small><p>${esc(r.body)}</p>${r.source_url?`<a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">查看来源</a>`:''}</article>`).join('')||'<p class="investment-empty">尚无复盘记录。</p>'}</section>`;
    fillValuation();
  }
  function fillValuation() {
    const f=research.querySelector('[data-form="valuation"]'),p=current()?.plan;if(!f||!p)return;
    const row=p.valuations.find(r=>r.target===f.elements.target.value&&r.scenario===f.elements.scenario.value)||{years:4};
    for(const k of ['eps','growth','pe','discount','dividends','years','conditions','invalidation','source_url','as_of'])f.elements[k].value=row[k]??'';
  }
  function render() {
    const p=current()?.plan;original.hidden=M.tab!=='holdings';root.hidden=['holdings','analysis','dividends'].includes(M.tab);
    const insights=document.getElementById('portfolio-insights');if(insights)insights.hidden=M.tab!=='analysis';
    const dividends=document.getElementById('portfolio-dividends');if(dividends)dividends.hidden=M.tab!=='dividends';
    root.setAttribute('aria-labelledby','investment-tab-'+M.tab);
    tabs.querySelectorAll('button').forEach(b=>{const chosen=b.dataset.section===M.tab;b.setAttribute('aria-pressed',String(chosen));b.setAttribute('aria-selected',String(chosen));b.tabIndex=chosen?0:-1;});
    if(p&&M.tab==='plan')renderPlan(p);else if(p&&M.tab==='trades')renderTrades(p);else if(p&&M.tab==='exposure')renderExposure(p);
    else root.innerHTML=toolbar()+'<p class="investment-empty">暂无配置方案。可导入工作簿，或新建一份独立方案。</p>';
    renderResearch(p);
    document.querySelectorAll('#investment-assets button,#investment-research button').forEach(b=>{if(M.busy)b.disabled=true;});
  }
  async function load() {
    if(M.busy)return;M.busy=true;message('');
    const selected=portfolioContext.id;
    try {const result=await api('/api/investment');if(selected!==portfolioContext.id)return;M.items=result.items;
      if(!M.id)try{M.id=localStorage.getItem('stock.investment.selected')||'';}catch(_){}
      if(!current()||current().plan.archived&&!M.includeArchived)M.id=M.items.find(r=>!r.plan.archived)?.plan.id||'';
      const saved=await api('/api/research/inputs?kind=etf_holdings');
      const funds=saved.items.find(r=>r.identity==='IGV-XSW-WCLD-BUG')?.payload?.funds||{};
      M.extraFunds={};for(const f of Object.values(funds)){const market=/^\d{6}/.test(f.symbol)?'CN':'US';M.extraFunds[C.key(market,f.symbol)]={...f,market};}
    } catch(e){if(selected===portfolioContext.id)message(e.message);} finally{M.busy=false;if(selected!==portfolioContext.id)load();else render();}
  }
  async function save(p, version=current()?.version||0) {
    if(M.busy)return;p=C.validatePlan(p);M.busy=true;let saved=false;
    const enabled=[...document.querySelectorAll('#investment-assets button:not(:disabled),#investment-research button:not(:disabled)')];enabled.forEach(b=>b.disabled=true);
    try {const row=await api('/api/investment/save',{method:'POST',body:{plan:p,base_version:version}});
      M.items=M.items.filter(r=>r.plan.id!==p.id);M.items.unshift(row);M.id=p.id;message('');saved=true;
    } finally {M.busy=false;if(saved)render();else enabled.forEach(b=>b.disabled=false);}
  }
  async function action(name) {
    const p=current()?.plan;
    if(name==='refresh')return load();
    if(name==='submit')return;
    if(name==='new'){const next=C.emptyPlan();await save(next,0);M.tab='plan';render();return;}
    if(name==='research'){selectCompanyResearch('allocation');research.scrollIntoView({block:'start'});return;}
    if(name==='export'){window.location.href=portfolioRequestPath('/api/investment/export?id='+encodeURIComponent(p.id));return;}
    if(name==='archive'){if(!confirm(p.archived?'恢复这份方案？':'归档方案？原始版本保留，可通过“显示归档”恢复。'))return;await save({...p,archived:!p.archived});M.includeArchived=true;render();return;}
    if(name==='archived'){M.includeArchived=!M.includeArchived;if(p?.archived&&!M.includeArchived)M.id=M.items.find(r=>!r.plan.archived)?.plan.id||'';render();return;}
    if(name==='prev'||name==='next'){M.page+=name==='next'?1:-1;renderExposure(p);return;}
    if(name==='import'){
      const input=document.createElement('input');input.type='file';input.accept='.xlsx,.xlsm,.json';
      input.onchange=async()=>{try{const file=input.files[0];if(!file)return;if(file.size>1000000)throw new Error('请选择 1 MB 内的文件');
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        M.preview={filename:file.name,data};const result=await api('/api/investment/preview',{method:'POST',body:M.preview});const plan=result.plan;
        dialog.innerHTML=`<h2>确认导入独立配置方案</h2><p>${esc(plan.name)}</p><p>${plan.targets.length} 个标的 · ${Object.keys(plan.funds).length} 只 ETF · ${num(plan.budget)} ${esc(plan.currency)} 配置预算</p>${result.warnings.map(w=>`<p class="investment-note">${esc(w)}</p>`).join('')}<p class="investment-error" data-import-error></p><div class="investment-toolbar"><button type="button" class="primary-button" data-confirm-import>确认导入</button><button type="button" class="secondary-button" data-cancel-import>取消</button></div>`;dialog.showModal();
      }catch(e){message(e.message);}};input.click();
    }
  }
  function formValue(f) {return Object.fromEntries(new FormData(f).entries());}
  async function submit(form) {
    const p=C.clone(current().plan),v=formValue(form),kind=form.dataset.form;
    if(kind==='budget') {p.name=v.name;p.currency=v.currency||p.currency;p.budget=C.number(v.budget,'预算');p.cash_target=C.number(v.cash_target,'现金预留');p.as_of=v.as_of;
      p.targets.forEach((t,i)=>{t.amount=C.number(v['amount_'+i],'金额');t.reference_price=C.number(v['price_'+i],'参考价',true);t.reference_date=v['date_'+i];});}
    if(kind==='target'){const parts=v.key.toUpperCase().trim().split(':');const id=C.key(parts[0],parts[1]);p.targets.push({key:id,market:parts[0],symbol:id.split(':')[1],name:v.name,currency:p.currency,category:v.category,amount:C.number(v.amount,'金额'),is_etf:v.type==='etf',reference_price:null,reference_date:'',shock_pct:null});p.research.push({target:id,name:v.name,business:'',thesis:'',risks:'',original_rating:'',as_of:'',source_url:'',review:''});}
    if(kind==='trade')p.trades.push({...v,id:uid('trade'),currency:p.currency,fee:C.number(v.fee,'费用'),quantity:C.number(v.quantity,'数量',true),price:C.number(v.price,'价格',true),amount:C.number(v.amount,'金额',true)});
    if(kind==='research')Object.assign(p.research[Number(form.dataset.index)],v);
    if(kind==='valuation'){const row={...v,years:C.number(v.years,'年数'),eps:C.number(v.eps,'EPS',true),growth:C.number(v.growth,'增长率',true,-99.99,1000),pe:C.number(v.pe,'PE',true),discount:C.number(v.discount,'折现率',true),dividends:C.number(v.dividends,'分红现值',true)};
      p.valuations=p.valuations.filter(r=>r.target!==row.target||r.scenario!==row.scenario);p.valuations.push(row);}
    if(kind==='review')p.reviews.push({...v,id:uid('review')});
    await save(p);
  }
  for(const surface of [root,research]) {
    surface.addEventListener('click',async event=>{const button=event.target.closest('button');if(!button||M.busy)return;try{
      if(button.dataset.sort)toggleSort(button);
      if(button.dataset.action)await action(button.dataset.action);
      if(button.dataset.deleteTrade&&confirm('删除这条流水并重新核算？若影响后续成交，操作会被拒绝。')){const p=C.clone(current().plan);p.trades=p.trades.filter(r=>r.id!==button.dataset.deleteTrade);await save(p);}
    }catch(e){message(e.message);}});
    surface.addEventListener('submit',event=>{event.preventDefault();if(M.busy)return;submit(event.target).catch(e=>message(e.message));});
    surface.addEventListener('change',event=>{if(event.target.matches('[data-plan]')){M.id=event.target.value;M.page=0;try{localStorage.setItem('stock.investment.selected',M.id);}catch(_){}render();}
      if(event.target.matches('[data-mode]')){M.mode=event.target.value;M.page=0;renderExposure(current().plan);}
      if(event.target.closest('[data-form="valuation"]')&&['target','scenario'].includes(event.target.name))fillValuation();});
  }
  root.addEventListener('input',event=>{if(event.target.matches('[data-search]')){M.search=event.target.value;M.page=0;const focus=event.target.selectionStart;renderExposure(current().plan);const input=root.querySelector('[data-search]');input.focus();try{input.setSelectionRange(focus,focus);}catch(_){}}});
  function selectSection(id) {if(!sections.some(([key])=>key===id))return;M.tab=id;render();}
  window.Investment = {selectSection, renderFx};
  tabs.addEventListener('click',event=>{const button=event.target.closest('[data-section]');if(button)selectSection(button.dataset.section);});
  tabs.addEventListener('keydown',event=>{
    const keys=sections.map(([id])=>id),index=keys.indexOf(M.tab);let next;
    if(event.key==='ArrowRight')next=keys[(index+1)%keys.length];
    if(event.key==='ArrowLeft')next=keys[(index+keys.length-1)%keys.length];
    if(event.key==='Home')next=keys[0];if(event.key==='End')next=keys.at(-1);
    if(next){event.preventDefault();selectSection(next);document.getElementById('investment-tab-'+next).focus();}
  });
  dialog.addEventListener('click',async event=>{
    if(event.target.matches('[data-cancel-import]'))dialog.close();
    if(event.target.matches('[data-confirm-import]')){event.target.disabled=true;try{const row=await api('/api/investment/import',{method:'POST',body:M.preview});M.items=M.items.filter(r=>r.plan.id!==row.plan.id);M.items.unshift(row);M.id=row.plan.id;M.tab='plan';M.includeArchived=row.plan.archived;dialog.close();render();}catch(e){dialog.querySelector('[data-import-error]').textContent=e.message;event.target.disabled=false;}}
  });
  window.addEventListener('portfolio-change',()=>{M.items=[];M.id='';M.tab='holdings';M.preview=null;dialog.close();render();load();});
  render();renderFx();load();
})();
