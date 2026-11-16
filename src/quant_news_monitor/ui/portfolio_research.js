(function() {
  'use strict';
  const C=window.STockPortfolioResearch;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n=(v,d=2)=>C.finite(v)?v.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'--';
  const pct=v=>C.finite(v)?n(v)+'%':'--';
  const when=v=>v&&Number.isFinite(Date.parse(v))?new Date(v).toLocaleString('zh-CN',{hour12:false}):'未取得';
  const currencies=['CNY','USD','HKD','KRW'];
  const options=(values,chosen)=>values.map(v=>`<option ${v===chosen?'selected':''}>${esc(v)}</option>`).join('');
  const field=(label,name,value,type='number')=>`<label>${label}<input name="${name}" value="${esc(value??'')}" type="${type}" ${type==='number'?'min="0" step="any"':''}></label>`;
  const select=(label,name,value)=>`<label>${label}<select name="${name}">${options(currencies,value)}</select></label>`;
  const table=(heads,rows)=>`<div class="pr-scroll"><table><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map(v=>`<td>${v}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${heads.length}">缺少可用样本</td></tr>`}</tbody></table></div>`;
  const source=row=>row?.source_url&&/^https:\/\//.test(row.source_url)?`<a href="${esc(row.source_url)}" target="_blank" rel="noopener noreferrer">${esc(row.source||'原始资料')}</a>`:'来源待补充';
  let data={histories:{},events:[],peers:[],imports:[],records:[]}, portfolio={},target='US:AAPL',poll=null,loadSequence=0;
  let holdingNames=new Map();
  const holdingLabel=id=>`<span title="${esc(id)}">${esc(holdingNames.get(id)||id)}</span>`;
  let riskBasis='total',riskWindow=60,researchTab='events',valuationTarget='',valuationResult=null;
  let performanceYear=new Date().getUTCFullYear();
  let analysisTab='overview';
  let consensusKey='',consensusPeriod='',consensusConfirmed=false,eventModel=null,draftTarget='',heatReport=null;
  function eventSection(model,benchmark,name) {
    return `<section id="pr-event-linkage" class="pr-report" aria-label="过去7日与未来14日事件联动"><h4>${esc(name)} · 过去7日与未来14日事件联动</h4><p>${esc(model.window)}</p><div class="pr-summary"><span>7个交易日变化<strong>${pct(model.past.return7)}</strong></span><span>基准 · ${esc(benchmark)}<strong>${pct(model.benchmark?.return7)}</strong></span><span>相对基准差值<strong>${n(model.excess7)} 个百分点</strong></span><span>近7日 / 前期均量<strong>${n(model.past.volumeRatio)} 倍</strong></span></div><p class="pr-muted">${esc(model.past.source)} · ${esc(model.past.from?.slice(0,10)||'--')} 至 ${esc(model.past.to?.slice(0,10)||'--')} · ${esc(model.past.basis)}。仅日期与复权口径一致才比较基准；不代表风险调整后超额收益。</p><details><summary>价格路径与计算口径</summary>${table(['日期','收盘 / 来源口径','单日变化','累计变化','基准累计'],model.path.map(p=>[p.date,n(p.close,4),pct(p.change),pct(p.cumulative),pct(p.benchmark)]))}<p>${esc(model.method)}</p></details><p>价格窗口内取得 ${model.relatedCount} 条公司相关样本，未逐条核验事件关联的新闻不计作事件证据。</p>${model.links.map(link=>`<article class="pr-report-row"><h5>${esc(link.event.title)}</h5><p><b>${esc(link.event.date)} · ${link.days===0?'今天':link.days+'日后'} · ${esc(link.status)}</b></p><p>${link.sources.map(e=>source(e)+' · 快照 '+esc(when(e.data_as_of||e.observed_at))).join('<br>')}</p><p><b>事实：</b>${esc(link.facts)}</p><p><b>解释（待验证）：</b>${esc(link.interpretation)}</p><p><b>预期基准：</b>${esc(link.expected)}</p><p>${esc(link.chronology)} ${esc(link.receipt)}</p><details><summary>事件证据与缺项 · ${link.evidenceCount} 条匹配</summary>${link.evidence.map(p=>`<p>${source(p)} · ${esc(p.title||'原文')}<br>公开 ${esc(when(p.published_at))} · ${esc(p.timing)}<br>采到 ${esc(when(p.observed_at||p.collected_at))} · ${esc(p.receipt)}</p>`).join('')||'<p>没有直接匹配的新闻，不把公司泛新闻当作事件证据。</p>'}<ul>${link.gaps.map(g=>`<li>${esc(g)}</li>`).join('')}</ul><p><b>成立条件：</b>${esc(link.confirmation)}</p><p><b>失效条件：</b>${esc(link.invalidation)}</p></details><button type="button" class="secondary-button" data-event-draft="${esc(link.key)}" ${link.canDraft?'':'disabled'}>将此事件带入研究草稿</button></article>`).join('')||'<p>未取得未来14日事件，不代表没有事件。可更新研究数据或导入带日期与来源的公告。</p>'}<p class="pr-muted">${esc(model.risk)}</p></section>`;
  }
  const metricLabels={market_cap:'市值',pe:'市盈率 PE',ps:'市销率 PS',net_margin:'净利率 %',dividend_yield:'股息率 %',leverage:'债务/权益',volatility:'波动率 %',net_income:'净利润',pb:'市净率 PB',roe:'净资产收益率 %',npl:'不良贷款率 %',revenue_growth:'收入增长 %',fcf_margin:'自由现金流率 %',retention:'留存率 %'};
  function peerRanking(engine) {
    const signed=x=>(x>0?'+':'')+n(x);
    const ranking=table(['候选内排名 / 公司','相对研究分','证据覆盖','判断边界'],engine.candidates.map(r=>[
      `${r.rank??'未排序'} · ${esc(r.name)}<br>${esc(r.target)}`,
      r.score===null?'不评分':`${n(r.score)} / 100<br><small>当前标的基准 50<br>资料缺口区间 ${n(r.interval[0])} 至 ${n(r.interval[1])}</small>`,
      `${n(r.coverage)}%<br><small>同组共同权重 ${engine.commonCoverage}%</small>`,esc(r.reason)+'<br>'+esc(r.drivers||'')
    ]));
    const evidence=engine.candidates.map(r=>`<details class="pr-report-row"><summary>${esc(r.name)} · 评分依据与缺项</summary><p>${r.dimensions.map(d=>esc(d.name)+' '+(d.used?signed(d.contribution)+'分':'未覆盖')).join('；')}</p>${table(['指标 / 权重 / 尺度下限','候选 / 当前','分数贡献','证据与限制'],r.evidence.map(e=>[`${esc(e.label)} · ${e.weight}% · ${e.floor}`,`${n(e.candidate)} / ${n(e.current)}`,e.used?signed(e.contribution):'不计入',esc(e.reason)+(e.valid&&!e.used?'；未进入共同评分指标':'')]))}<p>${source(r)}</p><button class="secondary-button" data-research-target="${esc(r.target)}">研究 ${esc(r.name)}</button></details>`).join('');
    return `<section id="pr-peer-ranking" class="pr-report" aria-label="同行算法排名"><h4>同行研究评分 · ${esc(engine.version)} · ${esc(engine.profile.name)}</h4><p>${esc(engine.profile.note)}</p><p>${esc(engine.note)}</p><p>${esc(engine.stability)}</p>${ranking}<p class="pr-muted">${esc(engine.method)}</p><details><summary>算法、权重与候选范围</summary><p>${esc(engine.formula)}</p><p>${Object.entries(engine.profile.weights).map(([k,w])=>esc(metricLabels[k])+` ${w}%`).join('；')}</p><p>${esc(engine.universe)}</p><p>至少60%指标权重、3个维度且有估值和经营质量证据，才进入共同指标比较。市值不直接加分；组合适配单列，不混入公司得分。</p><p>数据检查时间 ${esc(engine.asOf)}；系数、尺度和权重为研究规则，尚未经过收益有效性校准。</p></details>${evidence}<details><summary>未纳入的标的 · ${engine.excluded.length}</summary>${engine.excluded.map(r=>`<p>${esc(r.name||r.target)} · ${esc(r.target)}：${esc(r.reason)}</p>`).join('')||'<p>当前资料中没有额外排除项。</p>'}</details></section>`;
  }
  function dividendSection(rows) {
    return `<section id="pr-dividends" class="pr-report" aria-label="股息与派息"><h4>股息与派息</h4><p>股息率看相对股价的股息收益；派息率看盈利分配比例。派息率不计入越高越好的评分，缺失不视为0，也不由股息率与PE反推。</p><details><summary>金额、发放与两种比率的区别</summary>${C.dividendTerms.map(r=>`<p><b>${esc(r.term)}：</b>${esc(r.meaning)}</p>`).join('')}<p>股息率与派息率的特别股息、期间及股份类别口径可能不同，不能直接混算。未取得派息日或到账记录时，不表示已到账。</p></details>${table(['公司','股息率 / 股价口径','派息率 / 盈利口径','核查事项 / 来源'],rows.map(row=>{const r=C.dividendDisclosure(row);return [esc(row.name)+'<br>'+esc(row.target),esc(r.yieldInfo.text)+'<br><small>'+esc(r.yieldInfo.basis)+'</small>',esc(r.payout.text)+'<br><small>'+esc(r.payout.basis)+'</small>',esc(r.note||'仍须核对EPS、现金流和公司公告；不作分红承诺。')+'<br>'+source(row)];}))}</section>`;
  }
  const assets=document.createElement('section');assets.className='portfolio-research';assets.id='portfolio-insights';
  assets.innerHTML=`<div class="pr-heading pr-analysis-heading"><h3>组合分析</h3><button id="pr-open-research" class="secondary-button">同行与事件研究</button></div>
    <div class="pr-analysis-navigation"><div class="pr-tabs" role="tablist" aria-label="组合分析分区">${[['overview','重点概览'],['correlation','相关性'],['distribution','持仓分布'],['diagnosis','诊断研究'],['settings','设置与来源']].map(([id,label])=>`<button type="button" role="tab" id="pr-analysis-tab-${id}" aria-controls="pr-analysis-${id}" data-analysis-tab="${id}">${label}</button>`).join('')}</div><label class="pr-basis-label">仓位口径<select id="pr-risk-basis"><option value="total">含现金总资产</option><option value="invested">证券市值</option></select></label></div>
    <div id="pr-allocation">${['overview','correlation','distribution','diagnosis','settings'].map(id=>`<section id="pr-analysis-${id}" class="pr-analysis-pane" role="tabpanel" aria-labelledby="pr-analysis-tab-${id}" tabindex="0" ${id==='overview'?'':'hidden'}></section>`).join('')}</div>`;
  assets.hidden=true;assets.setAttribute('role','tabpanel');assets.setAttribute('aria-labelledby','investment-tab-analysis');
  assets.querySelector('.pr-heading').insertAdjacentHTML('afterend',`<section id="pr-performance" aria-labelledby="pr-performance-title"><div class="pr-heading"><h4 id="pr-performance-title" title="当前权重买入持有模拟，含现金、固定当前汇率、未扣交易费用；与真实账户收益分开。">当前权重模拟 · 非账户收益</h4><div class="pr-controls"><label>年份<select id="pr-performance-year">${Array.from({length:performanceYear-1999},(_,i)=>`<option value="${performanceYear-i}">${performanceYear-i}${i===0?'（今年截至）':''}</option>`).join('')}</select></label><button id="pr-performance-refresh" type="button" class="secondary-button">更新年度行情</button></div></div><div id="pr-performance-result"></div><div class="pr-data-status"><p id="pr-performance-status" class="pr-muted" role="status"></p><button type="button" id="pr-data-status-button" data-analysis-tab="settings" class="text-button" hidden></button></div><p id="pr-performance-error" class="pr-warning" role="alert"></p></section>`);
  assets.querySelector('#pr-analysis-correlation').innerHTML='<div id="pr-annual-result"></div><details id="pr-rolling-correlations"><summary>近期相关性</summary><div class="pr-controls"><label>窗口<select id="pr-risk-window"><option value="60">60个交易日</option><option value="120">120个交易日</option></select></label></div><div id="pr-rolling-result"></div></details>';
  assets.querySelector('#pr-analysis-settings').innerHTML='<details open class="pr-account-settings"><summary>账户与风险规则</summary><form id="pr-account"></form></details><p class="pr-warning" id="pr-account-error" role="alert"></p><div id="pr-data-issues" class="pr-warning"></div><div id="pr-method-result"></div><div id="pr-fx-result"></div>';
  assets.querySelector('#pr-analysis-diagnosis').innerHTML='<div id="pr-diagnosis-result"></div>';
  const performanceTitle=assets.querySelector('#pr-performance-title');
  assets.querySelector('#pr-analysis-settings').prepend(performanceTitle);
  performanceTitle.insertAdjacentHTML('afterend','<p id="pr-source-time" class="pr-muted"></p>');
  const performanceYearInput=assets.querySelector('#pr-performance-year');
  performanceYearInput.setAttribute('aria-label','统计年份');
  assets.querySelector('#pr-performance-refresh').textContent='更新分析';
  assets.querySelector('#pr-performance-refresh').title='更新公开行情并按本地公式重算，不调用 AI';
  performanceYearInput.parentElement.firstChild.remove();
  assets.querySelector('#pr-analysis-diagnosis').append(document.querySelector('.portfolio-analysis-column'));
  const exportAnalysisButton = el('button', 'secondary-button', '导出组合分析 PDF');
  exportAnalysisButton.type = 'button'; exportAnalysisButton.id = 'pr-export-analysis';
  assets.querySelector('#pr-performance-refresh').after(exportAnalysisButton);
  const exportExcelButton = el('button', 'secondary-button', '导出 Excel 明细');
  exportExcelButton.type = 'button'; exportExcelButton.id = 'pr-export-analysis-excel';
  assets.querySelector('#pr-analysis-settings').append(exportExcelButton);
  assets.querySelector('#pr-performance .pr-controls').append(assets.querySelector('#pr-open-research'));
  assets.querySelector('.pr-analysis-heading').remove();
  document.getElementById('investment-original').after(assets);
  function selectAnalysisTab(id) {
    if(!assets.querySelector('#pr-analysis-'+id))return;
    analysisTab=id;
    assets.querySelectorAll('.pr-analysis-navigation [role="tab"]').forEach(button=>{const chosen=button.dataset.analysisTab===id;button.setAttribute('aria-selected',String(chosen));button.tabIndex=chosen?0:-1;document.getElementById(button.getAttribute('aria-controls')).hidden=!chosen;});
  }
  assets.addEventListener('click',event=>{const button=event.target.closest('[data-analysis-tab]');if(button)selectAnalysisTab(button.dataset.analysisTab);});
  assets.querySelector('.pr-analysis-navigation [role="tablist"]').addEventListener('keydown',event=>{const keys=['overview','correlation','distribution','diagnosis','settings'],index=keys.indexOf(analysisTab);let next;if(event.key==='ArrowRight')next=keys[(index+1)%keys.length];if(event.key==='ArrowLeft')next=keys[(index+keys.length-1)%keys.length];if(event.key==='Home')next=keys[0];if(event.key==='End')next=keys.at(-1);if(next){event.preventDefault();selectAnalysisTab(next);document.getElementById('pr-analysis-tab-'+next).focus();}});
  selectAnalysisTab(analysisTab);
  document.getElementById('portfolio-account-button').onclick=()=>{window.Investment.selectSection('analysis');selectAnalysisTab('settings');assets.querySelector('.pr-account-settings').open=true;document.getElementById('pr-account').querySelector('input')?.focus();};
  const research=document.createElement('section');research.className='portfolio-research';research.id='portfolio-event-research';
  research.innerHTML=`<div class="pr-heading"><h3>同行与事件研究</h3><button id="pr-return-portfolio" class="secondary-button">返回组合</button></div><div class="pr-controls"><label>研究标的<input id="pr-target" list="pr-holdings" value="US:AAPL" maxlength="32"><datalist id="pr-holdings"></datalist></label><button id="pr-refresh" class="secondary-button">更新研究数据</button><button id="pr-import" class="secondary-button">导入证据</button><button id="pr-template" class="secondary-button">资料格式</button><button id="pr-export" class="icon-button" title="导出当前组合研究" aria-label="导出当前组合研究">${uiIcon('download').outerHTML}</button></div><p id="pr-status" role="status"></p><p id="pr-errors" class="pr-warning" role="alert"></p>
    <div class="pr-tabs" role="tablist" aria-label="组合研究视图">${[['events','同行与事件'],['propagation','传播检验'],['review','判断复盘']].map(([id,label])=>`<button type="button" role="tab" id="pr-tab-${id}" aria-controls="pr-panel-${id}" data-pr-tab="${id}">${label}</button>`).join('')}</div>
    <div id="pr-panel-events" role="tabpanel" aria-labelledby="pr-tab-events"><div id="pr-results"></div></div>
    <div id="pr-panel-propagation" role="tabpanel" aria-labelledby="pr-tab-propagation" hidden><div class="pr-controls"><label>领先平台<input id="pr-from" value="新闻"></label><label>后续平台<input id="pr-to" value="小红书"></label><button id="pr-lag" class="secondary-button">检验滞后</button></div><div id="pr-propagation"></div></div>
    <div id="pr-panel-review" role="tabpanel" aria-labelledby="pr-tab-review" hidden><details open><summary>冻结14日研究判断</summary><form id="pr-freeze"><input type="hidden" name="parent_id"><div class="pr-controls">${field('作者署名','author','','text')}${field('比较基准','benchmark','US:SPY','text')}<label>方向<select name="direction"><option value="0">中性</option><option value="1">有条件看多</option><option value="-1">有条件看空</option></select></label></div><div class="pr-form-grid">${[['note','判断依据'],['expectation','市场预期基准与来源'],['confirmation','成立条件'],['invalidation','反证与失效条件']].map(([name,label])=>`<label>${label}<textarea name="${name}" maxlength="${name==='note'?4000:2000}" required></textarea></label>`).join('')}</div><div class="pr-controls">${field('单边手续费 bps','fee_bps',5)}${field('单边滑点 bps','slippage_bps',5)}${field('往返税费合计 bps','tax_bps',0)}${field('年化借券费 bps','borrow_bps',0)}</div><p class="pr-muted" id="pr-draft-state">研究模拟参数，不代表实际成交；作者署名不是登录认证。</p><div class="pr-controls"><button class="primary-button">冻结判断</button><button id="pr-new-judgment" type="button" class="secondary-button">新建独立判断</button></div></form></details><label class="pr-record-search">筛选记录<input id="pr-record-filter" type="search" placeholder="作者、标的或依据"></label><div id="pr-backtest"></div></div>`;
  document.getElementById('research-peers-panel').append(research);
  const platformList=document.createElement('datalist');platformList.id='pr-platforms';research.append(platformList);
  for(const id of ['pr-from','pr-to'])document.getElementById(id).setAttribute('list','pr-platforms');
  document.getElementById('pr-panel-propagation').insertAdjacentHTML('beforeend',`<section class="pr-report" aria-label="热度算法回测"><h4>热度算法 · 费用后留出对照</h4><div class="pr-controls"><label>单边手续费 bps<input id="pr-heat-fee" type="number" min="0" max="1000" value="5"></label><label>单边滑点 bps<input id="pr-heat-slip" type="number" min="0" max="1000" value="5"></label><label>卖出税费 bps<input id="pr-heat-tax" type="number" min="0" max="1000" value="0"></label><button id="pr-heat-run" class="secondary-button">运行新旧对照</button></div><div id="pr-heat-result" role="status"></div><button id="pr-heat-save" class="secondary-button" disabled>保存到回测库</button><p id="pr-heat-status" role="status"></p></section>`);
  document.getElementById('pr-panel-events').insertAdjacentHTML('beforeend',`<section class="pr-report" aria-labelledby="pr-earnings-title"><h4 id="pr-earnings-title">未来90日财报与盈利预期</h4><div id="pr-earnings"></div><h4>从价格反推盈利要求</h4><p id="pr-price-basis" class="pr-muted"></p><form id="pr-valuation"><div class="pr-form-grid">${field('全年盈利期间','period','','text')}${field('年度EPS假设（报价币种/股）','annual_eps','')}${field('估值PE假设（倍）','multiple','')}<label>假设依据 / 公开来源<textarea name="basis" maxlength="500" required></textarea></label></div><p class="pr-muted">此处输入的是研究假设，不自动冒充分析师一致预期。不得把季度EPS直接乘四；亏损公司不适用正PE模型。</p><button type="submit" class="secondary-button">计算反推与敏感性</button></form><p id="pr-valuation-error" role="alert" class="pr-warning"></p><div id="pr-valuation-result"></div><button id="pr-use-valuation" type="button" class="secondary-button" hidden>带入判断草稿</button></section>`);
  function error(message){document.getElementById('pr-errors').textContent=message||'';}
  function namedRows(rows,dashboard=state.dashboard){return rows.map(row=>({...row,name:instrumentName({...row,target:row.target||row.key},dashboard)}));}
  function namedFunds(funds,dashboard=state.dashboard){return Object.fromEntries(Object.entries(funds||{}).map(([id,fund])=>[id,{...fund,holdings:(fund.holdings||[]).map(row=>(row.market||/^(CN|HK|US|KR|FUND):/.test(row.target||row.key||''))?namedRows([row],dashboard)[0]:row)}]));}
  function combined(key){const rows=[...(data[key]||[]),...(data.imports||[]).flatMap(row=>row[key]||[])];return key==='peers'?namedRows(rows):rows;}
  async function exportAnalysis(format = 'pdf') {
    format = format === 'xlsx' ? 'xlsx' : 'pdf';
    const exportLabel = format === 'pdf' ? 'PDF' : 'Excel';
    const selected = portfolioContext.id;
    const year = performanceYear, basis = riskBasis, windowDays = riskWindow;
    if (portfolioContext.switching || portfolioContext.writes || state.portfolioDeletePending || portfolio.id !== selected || state.dashboard?._startup_cache) {
      showToast('正在读取或保存组合，请稍后导出', 'info');
      return;
    }
    if (state.portfolioPrincipalDirty || document.getElementById('pr-account').dataset.dirty === '1') {
      showToast('请先保存账户设置和本金，再导出分析', 'info');
      return;
    }
    exportAnalysisButton.disabled = true;
    exportExcelButton.disabled = true;
    exportAnalysisButton.setAttribute('aria-busy', 'true');
    exportAnalysisButton.textContent = '正在采集行情…';
    try {
      const snapshot = await api('/api/portfolio/research', {method:'POST', body:{action:'prepare_analysis_export', year}});
      if (selected !== portfolioContext.id || snapshot.portfolio_id !== selected) throw Error('组合已切换，请重新导出');
      const dashboard = await api('/api/dashboard?details=0');
      const current = dashboard.portfolio;
      if (selected !== portfolioContext.id || current?.id !== selected) throw Error('组合已切换，请重新导出');
      const annual = snapshot.performance;
      const positions = namedRows((current.positions || []).map(row=>({...row,...snapshot.products?.[`${row.market}:${row.symbol}`]})),dashboard);
      if (positions.length !== Object.keys(snapshot.histories).length ||
          positions.some(row=>!Object.hasOwn(snapshot.histories, `${row.market}:${row.symbol}`))) {
        throw Error('持仓已变更，请重新导出');
      }
      const account = {...current.account, base_currency:current.base_currency, cash_base:current.summary?.cash_base,receivable_base:current.summary?.receivable_base,payable_base:current.summary?.payable_base};
      const histories = Object.fromEntries(positions.map(row=>{
        const key = `${row.market}:${row.symbol}`, history = snapshot.histories[key];
        return [key, history && history.currency === row.currency ? history : {points:[]}];
      }));
      const peers = namedRows(snapshot.peers || [],dashboard), funds = namedFunds(snapshot.funds,dashboard);
      const performance = C.portfolioPerformance(positions, account, annual.histories, {year, allowPartial:true});
      const risk = C.portfolio(positions, account, histories, peers, {basis, window:windowDays, strictAdjusted:true});
      if (basis !== 'total') risk.alerts = C.portfolio(positions, account, {}, peers).alerts;
      risk.largest = risk.largest.map(row => ({target:`${row.market}:${row.symbol}`,name:row.name, value:row.market_value_base, weight:row.weight}));
      risk.sector = risk.sector.map(row => ({...row,name:C.classificationName(row.name)}));
      const diagnosis = C.portfolioReport(positions, account, histories, peers, funds, {window:windowDays}).sections;
      const classifications = C.classificationRows(positions);
      const holdings = positions.map((row,index)=>{
        const classification = classifications[index], history = histories[classification.target];
        return {target:classification.target, name:row.name, type:classification.type, currency:row.currency, quantity:row.quantity,
          price:row.price, value:row.market_value_base, cost:row.cost_value_base, pnl:row.pnl_base, pnl_pct:row.pnl_pct,
          quote_time:row.quote_time||row.nav_date, quote_source:row.quote_source, quote_status:row.quote_status, quote_url:row.source_url,
          description:classification.description, classification_source:classification.source, classification_as_of:classification.as_of,
          classification_url:classification.source_url, rolling_source:history.source, rolling_observed_at:history.observed_at,
          rolling_to:C.bars(history.points).at(-1)?.time,
          notes:[row.quote_error, row.identity_warning, classification.note, !history.points?.length&&'短窗日线未取得'].filter(Boolean).join('；')};
      });
      const fx = Object.entries(current.fx_details || {}).map(([pair,row])=>({pair,...row}));
      const {rows,overlaps,pairs,dates,unknown,note} = C.lookthrough(positions, funds, {basis,cash_base:account.cash_base,receivable_base:account.receivable_base,payable_base:account.payable_base});
      const disclosed = rows.filter(row=>!row.overlap&&row.origins.some(origin=>snapshot.funds?.[origin]?.partial));
      exportAnalysisButton.textContent = `正在生成 ${exportLabel}…`;
      const response = await fetch(`/api/portfolio/analysis.${format}`, {method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({portfolio_id:selected, year, performance, risk, diagnosis, holdings, fx, exposure:{overlaps,disclosed,pairs,dates,unknown,note}, context:{
          observed_at:annual.observed_at, annual_errors:annual.errors, risk_errors:snapshot.errors,
          risk_as_of:snapshot.observed_at, quote_as_of:current.runtime?.updated_at, risk_window:windowDays, risk_basis:basis
        }})});
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw Error(detail.error || '组合分析导出失败');
      }
      await downloadAttachment(response, `STock-组合分析.${format}`);
      showToast(`${current.name || '组合'}的分析 ${exportLabel} 已生成`, 'success');
    } catch (error) {
      showToast(error.message || '组合分析导出失败', 'error');
    } finally {
      exportAnalysisButton.disabled = false;
      exportExcelButton.disabled = false;
      exportAnalysisButton.removeAttribute('aria-busy');
      exportAnalysisButton.textContent = '导出组合分析 PDF';
    }
  }
  exportAnalysisButton.addEventListener('click', exportAnalysis);
  exportExcelButton.addEventListener('click', () => exportAnalysis('xlsx'));
  function renderPerformance() {
    const packet=data.performance||{},selected=packet.year===performanceYear,history=selected?packet.histories||{}:{};
    const model=C.portfolioPerformance(portfolio.positions||[],{cash_base:portfolio.summary?.cash_base,receivable_base:portfolio.summary?.receivable_base,payable_base:portfolio.summary?.payable_base},history,{year:performanceYear,allowPartial:true});
    const output=document.getElementById('pr-performance-result'),expanded=new Set([...assets.querySelectorAll('details[open]')].map(row=>row.id));
    const percentage=value=>C.finite(value)?`${value>0?'+':''}${n(value)}%`:'--',tone=value=>C.finite(value)?value>0?'pr-positive':value<0?'pr-negative':'':'';
    const card=(label,value,note,color='')=>`<div class="pr-performance-stat" title="${esc(note)}"><span>${esc(label)}</span><strong class="${color}">${value}</strong><small>${esc(note)}</small></div>`;
    const period=model.from&&model.to?`${model.from} → ${model.to}`:packet.busy?'正在补齐历史行情':'暂无共同有效区间';
    const drawdown=model.troughDate?`${model.peakDate} → ${model.troughDate}；${model.recoveryDate?'恢复 '+model.recoveryDate:'区间内未恢复'}`:'共同日期净值的峰值至谷值';
    const currentYear=new Date().getUTCFullYear();
    output.innerHTML=`<div class="pr-performance-grid">${card(model.status==='partial'?'共同区间模拟收益':`${performanceYear}${performanceYear===currentYear?'年截至':'年'}模拟收益`,percentage(model.annualReturn),period,tone(model.annualReturn))}${card('最大回撤',percentage(model.maxDrawdown),drawdown,tone(model.maxDrawdown))}${card('年化波动',C.finite(model.volatility)?n(model.volatility)+'%':'--',`${model.samples} 个共同收益区间 · 按252日年化`)}${card('持仓平均相关系数',n(model.averageCorrelation,2),`有效 ${model.validPairs} / ${model.totalPairs} 对 · 中位 ${n(model.medianCorrelation,2)} · ${model.highPairs.length} 对 > 0.7`)}</div>`;
    const coefficient=value=>C.finite(value)?`<span class="${tone(value)}">${n(value,2)}</span>`:'--';
    const matrix=table(['标的',...model.targets.map(row=>holdingLabel(row.target))],model.targets.map((row,i)=>[holdingLabel(row.target),...model.matrix[i].map(coefficient)]));
    output.insertAdjacentHTML('beforeend',`<details id="pr-annual-correlations" ${expanded.has('pr-annual-correlations')?'open':''}><summary>相关性矩阵与高相关持仓 · ${model.validPairs} 对有效</summary><p class="pr-muted">所选年份内同起止日期收益的Pearson相关系数；每对至少20个区间。平均值按有效持仓对等权计算，剔除对角线和重复配对，覆盖不足时只代表这些有效配对。红色为正相关，绿色为负相关；不表示收益好坏。</p><div class="pr-correlation-matrix">${matrix}</div><h4>高相关持仓对（大于0.7）</h4>${table(['持仓A','持仓B','相关系数','区间数'],model.highPairs.map(row=>[holdingLabel(row.a),holdingLabel(row.b),coefficient(row.correlation),row.samples]))}<details><summary>全部配对与缺失原因</summary>${table(['持仓A','持仓B','相关系数','有效区间','日期 / 原因'],model.pairs.map(row=>[holdingLabel(row.a),holdingLabel(row.b),coefficient(row.correlation),row.samples,esc(row.reason||`${row.from} 至 ${row.to}`)]))}</details></details><details id="pr-performance-method" ${expanded.has('pr-performance-method')?'open':''}><summary>计算口径、权重与行情来源</summary><p>${esc(model.method)}</p><p>现金权重 ${C.finite(model.cashWeight)?n(model.cashWeight*100)+'%':'--'}。按报告起止日的共同有效收盘模拟；缺少年初历史时单列共同区间，不冒充全年收益。新增上市、来源缺失、币种不一致等不从组合中剔除。</p>${table(['标的','初始模拟权重','报价币种','来源'],model.targets.map(row=>[holdingLabel(row.target),C.finite(model.weights.find(weight=>weight.target===row.target)?.weight)?n(model.weights.find(weight=>weight.target===row.target).weight*100)+'%':'--',esc(row.currency||'未知'),source(row)]))}${table(['日期','模拟净值','累计收益','回撤'],model.path.map(row=>[row.date,n(row.value,6),percentage(row.change),percentage(row.drawdown)]))}</details>`);
    const annual=output.querySelector('#pr-annual-correlations');annual.open=true;
    const correlationNote=annual.querySelector('.pr-muted');correlationNote.title=correlationNote.textContent;correlationNote.textContent='年度收益相关性 · 红色正相关，绿色负相关 · 至少20个共同区间';
    document.getElementById('pr-annual-result').replaceChildren(annual);
    document.getElementById('pr-method-result').replaceChildren(output.querySelector('#pr-performance-method'));
    document.getElementById('pr-performance-refresh').disabled=!!packet.busy;
    document.getElementById('pr-performance-year').disabled=!!packet.busy;
    document.getElementById('pr-source-time').textContent=`本地公式计算 · 持仓估值 ${when(portfolio.runtime?.updated_at)} · 历史采集 ${selected?when(packet.observed_at):'等待采集'}`;
    document.getElementById('pr-performance-status').textContent=packet.busy?'正在自动更新分析…':!(portfolio.positions||[]).length?'添加持仓后自动计算':selected?'':'正在准备本年度分析…';
    const issues=selected?[...model.issues,...(packet.errors||[]),...(data.analysis_errors||[])]:[];
    document.getElementById('pr-data-issues').textContent=issues.join('\n');
    const issueButton=document.getElementById('pr-data-status-button');issueButton.hidden=!issues.length;issueButton.textContent=`数据缺项 ${issues.length} · 查看`;
    issueButton.parentElement.hidden=!packet.busy&&selected&&!issues.length;
  }
  function allocations() {
    renderPerformance();
    const a=portfolio.account||{},summary=portfolio.summary||{},current=document.getElementById('pr-account');
    if(portfolio.account && (!current.dataset.ready || current.dataset.version!==JSON.stringify(a)&&current.dataset.dirty!=='1'&&!current.contains(document.activeElement))) {
      current.innerHTML=`<div class="pr-controls">${field('负责人','owner',a.owner||'','text')}${field('策略分类','strategy',a.strategy||'','text')}${field('比较基准（市场:代码）','benchmark',a.benchmark||'','text')}${select('显示基准币种','base_currency',a.base_currency||'CNY')}${field('累计净投入本金','principal',a.principal)}${select('本金原币','principal_currency',a.principal_currency||'CNY')}<label>库存现金口径<select name="cash_mode"><option value="auto" ${a.cash_mode==='auto'?'selected':''}>自动估算</option><option value="manual" ${a.cash_mode==='manual'?'selected':''} ${Object.keys(a.cash_balances||{}).length||a.cash!=null?'':'disabled'}>手动录入</option></select></label><button type="button" class="secondary-button" data-edit-cash>修改库存现金（多币种）</button>${field('单仓上限 %','position_limit',a.position_limit||35)}${field('前五大上限 %','top5_limit',a.top5_limit||80)}${field('行业上限 %','sector_limit',a.sector_limit||50)}<button class="secondary-button">保存账户口径</button></div><p class="pr-muted">独立资金账户；风险上限按含现金总资产计算，策略分类不重复汇总资产。</p>`;
      current.dataset.ready='1';
      current.dataset.version=JSON.stringify(a);
    }
    const histories=Object.fromEntries(Object.entries(data.histories||{}).filter(([id])=>(portfolio.positions||[]).some(p=>p.market+':'+p.symbol===id)));
    const analysis=C.portfolio(portfolio.positions||[],{...a,cash_base:summary.cash_base,receivable_base:summary.receivable_base,payable_base:summary.payable_base},histories,combined('peers'),{basis:riskBasis,window:riskWindow,strictAdjusted:true});
    const alerts=riskBasis==='total'?analysis.alerts:C.portfolio(portfolio.positions||[],{...a,cash_base:summary.cash_base,receivable_base:summary.receivable_base,payable_base:summary.payable_base},{},combined('peers')).alerts;
    const unit=esc(portfolio.base_currency||'CNY');
    document.getElementById('pr-analysis-overview').innerHTML=`<div class="pr-summary"><span>总资产 · ${unit}<strong>${n(summary.net_assets_base)}</strong></span><span>最大持仓<strong>${pct(analysis.max)}</strong></span><span>前五大持仓<strong>${pct(analysis.top5)}</strong></span><span>现金占比<strong>${pct(analysis.cash)}</strong></span></div><div class="pr-overview-grid"><section><h4>前五大持仓</h4>${table(['标的','市值 · '+unit,'仓位'],analysis.largest.map(r=>[esc(r.name||r.symbol),n(r.value),pct(r.weight)]))}</section><section class="pr-focus"><h4>关注重点 <span>${alerts.length?alerts.length+' 项超限':'集中度'}</span></h4><div class="pr-risk-notices" role="status">${alerts.length?alerts.map(a=>`<p class="pr-warning">${esc(a)}</p>`).join(''):`<p>${analysis.complete?'未触及集中度上限':'数据不足 · 风险待核验'}</p>`}</div><dl class="pr-quality"><div><dt>持仓估值覆盖</dt><dd>${analysis.coverage.valued} / ${analysis.coverage.total}</dd></div><div><dt>行业覆盖</dt><dd>${pct(analysis.coverage.sector)}</dd></div><div><dt>行业未知市值</dt><dd>${n(analysis.coverage.unknownValue)} ${unit}</dd></div></dl><div id="pr-identity-issues"></div><button type="button" class="text-button" data-analysis-tab="diagnosis">查看诊断与警戒线 →</button></section></div>`;
    const overview=document.getElementById('pr-analysis-overview');overview.querySelector('.pr-focus').prepend(overview.querySelector('.pr-summary'));
    document.getElementById('pr-rolling-result').innerHTML=table(['标的','对比标的','相关系数','对齐区间','数据状态'],analysis.correlations.map(r=>[holdingLabel(r.a),holdingLabel(r.b),n(r.correlation,3),r.samples,esc(r.reason||'来源复权收盘')]));
    document.getElementById('pr-rolling-correlations').querySelector('summary').textContent=`近期相关性 · ${riskWindow}日窗口`;
    document.getElementById('pr-analysis-distribution').innerHTML=`<div class="pr-bands">${[['sector','行业'],['market','市场'],['currency','计价币种']].map(([id,label])=>`<section><h4>${label}分布</h4>${table([label,'仓位'],analysis[id].map(r=>[esc(id==='sector'?C.classificationName(r.name):id==='market'?({CN:'A股',HK:'港股',US:'美股',KR:'韩股',FUND:'基金'}[r.name]||r.name):r.name),pct(r.weight)]))}</section>`).join('')}</div><div id="pr-exposure-result"></div>`;
    renderExposure();
    const classifications=C.classificationRows(portfolio.positions||[]);
    document.getElementById('pr-analysis-distribution').insertAdjacentHTML('beforeend',`<details id="pr-classifications"><summary>行业与基金投资方向 · ${classifications.length} 项</summary>${table(['标的 / 类型','行业或投资方向','资料来源 / 时间'],classifications.map(r=>[`${esc(r.name)}<br><small>${esc(r.target)} · ${esc(r.type)}</small>`,`${esc(r.description)}${r.note?`<br><small>${esc(r.note)}</small>`:''}`,`${source(r)}<br><small>${esc(/^\d{4}-\d{2}-\d{2}$/.test(r.as_of)?r.as_of:when(r.as_of))}</small>`]))}</details>`);
    const report=C.portfolioReport(portfolio.positions||[],{...a,base_currency:portfolio.base_currency,cash_base:summary.cash_base,receivable_base:summary.receivable_base,payable_base:summary.payable_base},histories,combined('peers'),namedFunds(data.funds),{window:riskWindow});
    document.getElementById('pr-diagnosis-result').innerHTML=`<div class="pr-diagnosis-grid"><section aria-label="组合诊断全文"><h4>组合诊断</h4>${report.sections.map(r=>`<details class="pr-diagnosis-row"><summary>${esc(r.title)}</summary><p>${esc(r.fact)}</p><p>${esc(r.meaning)}</p><p><b>检查重点：</b>${esc(r.action)}</p></details>`).join('')}</section><section><h4>持仓研究</h4><div class="pr-holding-research">${[...new Map((portfolio.positions||[]).map(p=>[p.market+':'+p.symbol,p])).entries()].map(([id,p])=>`<button type="button" class="secondary-button" data-research-target="${esc(id)}">${esc(p.name||p.symbol)} →</button>`).join('')||'<p>暂无持仓</p>'}</div></section></div>`;
    const questionable=(portfolio.positions||[]).filter(r=>r.identity_warning||r.quote_error);
    document.getElementById('pr-identity-issues').innerHTML=questionable.length?`<details><summary>待核对 ${questionable.length} 项</summary>${questionable.map(r=>`<p>${esc(r.name||r.symbol)} · ${esc(r.quote_error||'证券名称待确认')} <button class="text-button" data-correct="${esc(r.market+':'+r.symbol)}">核验</button></p>`).join('')}</details>`:'';
  }

  function render() {
    allocations();
    const benchmark=(data.target===target?data.benchmark:null)||portfolio.account?.benchmark||({US:'US:SPY',CN:'CN:510300.SS',HK:'HK:2800.HK',KR:'KR:069500.KS'})[target.split(':')[0]]||'US:SPY';
    const model=C.eventStudy(data.histories?.[target],combined('events'),target,Date.now(),data.histories?.[benchmark],data.posts||[]);eventModel=model;
    const own=(portfolio.positions||[]).find(p=>p.market+':'+p.symbol===target);
    const metrics=C.researchMetrics(combined('peers'),data.histories);
    const profile=C.peerProfile(metrics.find(r=>r.target===target)?.industry||own?.industry),peerRows=C.peers(target,metrics),metric=(row,key)=>row[key]?`${n(row[key].value)}<br><small>${esc(row[key].basis)}<br>${esc(row[key].as_of?.slice(0,10))} · ${esc(({percent:'%',ratio:'倍',unverified:'单位待核验'})[row[key].currency]||row[key].currency)}<br>${esc(row[key].period?'报告期 '+row[key].period:'报告期未取得')}</small>`:'未取得';
    document.getElementById('pr-holdings').innerHTML=(portfolio.positions||[]).map(p=>`<option value="${esc(p.market+':'+p.symbol)}">${esc(p.name)}</option>`).join('');
    const form=document.getElementById('pr-freeze');if(!form.elements.author.value)form.elements.author.value=portfolio.account?.owner||'';
    if(!form.dataset.dirty)form.elements.benchmark.value=benchmark;
    document.getElementById('pr-status').textContent=(data.busy?'正在分批采集；已保留上次结果。':'资料采集于 '+when(data.observed_at))+' '+(data.coverage||'');
    document.getElementById('pr-refresh').disabled=!!data.busy;
    const related=C.dedupePosts(data.posts||[]).filter(r=>r.targets.includes(target));
    document.getElementById('pr-results').innerHTML=`<h4>同行比较 · ${esc(profile.name)}</h4>${table(['公司',...profile.metrics.map(k=>metricLabels[k]),'来源'],[...metrics.filter(r=>r.target===target),...peerRows].map(r=>[esc(r.name)+'<br>'+esc(r.target),...profile.metrics.map(k=>metric(r,k)),source(r)]))}<p class="pr-muted">${esc(profile.note)} 行情快照日期不等于财报报告期。股息率不是派息率；缺项保留，不自动推荐买入。</p><h4>相关原文 · 去重后 ${related.length} 条</h4>${related.slice(0,8).map(r=>`<p>${source({...r,source_url:r.url})} · ${esc(r.published_at||'时间未知')}<br>${esc(r.title||r.excerpt||'')}</p>`).join('')||'<p>尚无可归属到该证券的公开样本。</p>'}`;
    lag();
    document.getElementById('pr-results').insertAdjacentHTML('beforeend',`<details><summary>可比差异 · 仅供复核</summary>${peerRows.map(r=>`<p><b>${esc(r.name)}</b>：${r.comparison.map(c=>esc(metricLabels[c.metric]+' '+(c.delta==null?'不可比':c.delta===0?'与当前标的一致':(c.delta>0?'高于':'低于')+'当前标的 '+n(Math.abs(c.delta))))).join('；')}。</p>`).join('')||'<p>当前没有同口径的同行差异结果。</p>'}</details>`);
    const candidates=C.peerInsights(target,metrics,portfolio.positions||[],{...portfolio.account,cash_base:portfolio.summary?.cash_base,receivable_base:portfolio.summary?.receivable_base,payable_base:portfolio.summary?.payable_base});
    document.getElementById('pr-results').insertAdjacentHTML('afterbegin',`<section class="pr-report"><h4>关联分析 · 为什么值得比较</h4>${candidates.map(r=>`<article class="pr-report-row"><h5>${esc(r.name)} · ${esc(r.status)}</h5><p>${esc(r.conclusion)}</p>${[['相对优势',r.advantages],['反向证据',r.tradeoffs],['规模与背景',r.context],['待补资料',r.unknown]].map(([label,items])=>`<p><b>${label}：</b>${items.length?items.map(esc).join('；'):'当前同口径样本未发现，不代表不存在。'}</p>`).join('')}<p><b>组合影响：</b>${esc(r.impact)}</p><p><b>还要验证：</b>${esc(r.checks)}</p><p class="pr-muted">${esc(r.note)} ${source(r)}</p><button class="secondary-button" data-research-target="${esc(r.target)}">研究 ${esc(r.name)}</button></article>`).join('')||'<p>尚无同市场、同细分行业可比资料。更新研究数据后显示实际候选，不预设哪家公司更好。</p>'}</section>`);
    document.getElementById('pr-results').insertAdjacentHTML('afterbegin',peerRanking(C.peerAnalysis(target,metrics))+dividendSection([...metrics.filter(r=>r.target===target),...peerRows]));
    document.getElementById('pr-results').insertAdjacentHTML('afterbegin',eventSection(model,benchmark,own?.name||metrics.find(r=>r.target===target)?.name||target));
    renderOutlook();renderReview();
  }
  document.getElementById('pr-earnings').insertAdjacentHTML('afterend','<section id="pr-consensus" aria-label="自动一致预期与反推"></section>');
  const valuationKey=()=>`stock.valuation-draft.${portfolioContext.id}.${target}`;
  function renderConsensus(outlook){
    const snapshot=data.consensus?.[target],key=portfolioContext.id+'|'+target+'|'+(snapshot?.observed_at||'')+'|'+outlook.currency;
    if(consensusKey!==key){consensusKey=key;consensusPeriod='';consensusConfirmed=false;}
    const model=C.consensusModel(target,snapshot,outlook,{period:consensusPeriod,confirmedUnit:consensusConfirmed});
    const rows=table(['年度期间','一致预期EPS / 低 / 高','预期样本数','近4周上调 / 下调次数'],model.rows.map(r=>[esc(r.label),`${n(r.eps,4)} / ${n(r.low,4)} / ${n(r.high,4)}${r.rangeValid?'':'<br>高低区间未核验'}`,n(r.samples,0),n(r.up,0)+' / '+n(r.down,0)]));
    document.getElementById('pr-consensus').innerHTML=`<h4>自动一致预期与反推</h4><p>${source(model)} · 采集 ${esc(when(model.observed_at))} · 来源更新日 ${esc(model.sourceAsOf)}</p><p class="pr-muted">${esc(model.unitNote)} 采集时间不是分析师更新预测的时间，样本数不等于独立分析师人数。</p>${rows}${model.rows.length?`<div class="pr-controls"><label>预测财年<select id="pr-consensus-period">${model.rows.map(r=>`<option value="${esc(r.period)}" ${r.period===model.selected?.period?'selected':''}>${esc(r.label)}</option>`).join('')}</select></label><label><input type="checkbox" id="pr-consensus-unit" ${consensusConfirmed?'checked':''}>已核对预测EPS与报价 ${esc(outlook.currency||'币种未知')} 的币种、每股/每ADS口径一致</label></div>`:''}<p id="pr-consensus-status" role="status">${esc(model.reason)}</p><p>预期分歧宽度：${pct(model.dispersion)}；当下隐含预测PE：${n(model.forwardPE)} 倍。</p><p class="pr-muted">分歧宽度=(最高EPS−最低EPS)/|均值|；来源高低值不是置信区间，上下调次数不能当作概率。</p>${model.grid.length?table(['独立假设PE','当前价要求EPS','相对一致预期差','均值EPS对应估值','低 / 高EPS对应估值'],model.grid.map(r=>[n(r.multiple)+' 倍',n(r.required,4),pct(r.gap),n(r.value,4)+' '+esc(outlook.currency),n(r.low,4)+' / '+n(r.high,4)]))+`<p class="pr-muted">这些PE是固定敏感性情景，不是合理估值结论。正预期差表示该PE下所需盈利高于一致预期，不能据此断定股价将涨跌或预期已充分定价。</p><div class="pr-controls"><button type="button" id="pr-consensus-assumption" class="secondary-button">带入手动情景</button><button type="button" id="pr-consensus-draft" class="secondary-button">带入判断草稿</button></div>`:''}`;
    document.getElementById('pr-consensus-period')?.addEventListener('change',event=>{consensusPeriod=event.target.value;renderConsensus(outlook);document.getElementById('pr-consensus-period')?.focus();});
    document.getElementById('pr-consensus-unit')?.addEventListener('change',event=>{consensusConfirmed=event.target.checked;renderConsensus(outlook);document.getElementById('pr-consensus-unit')?.focus();});
    document.getElementById('pr-consensus-assumption')?.addEventListener('click',()=>{const form=document.getElementById('pr-valuation');form.elements.period.value='FY '+model.selected.period;form.elements.annual_eps.value=model.selected.eps;form.elements.basis.value=`参考来源年度EPS ${model.selected.eps}（${model.selected.period}）：${model.source_url}；采集 ${model.observed_at}；样本 ${model.selected.samples} 份；用户确认 ${outlook.currency}/上市股口径。手动修改后的数值属于研究假设。`;form.dispatchEvent(new Event('input',{bubbles:true}));form.elements.multiple.focus();});
    document.getElementById('pr-consensus-draft')?.addEventListener('click',()=>{const form=document.getElementById('pr-freeze'),text=[form.elements.expectation.value,model.note].filter(Boolean).join('\n');if(text.length>2000){error('预期基准草稿接近长度上限，请先精简。');return;}form.elements.expectation.value=text;saveDraft();selectTab('review');form.elements.expectation.focus();});
  }
  function renderOutlook(){
    const outlook=C.earningsOutlook(combined('events'),target,data.histories?.[target]);
    renderConsensus(outlook);
    document.getElementById('pr-earnings').innerHTML=table(['发布日期 / 状态','预测盈利（非实际值）','数据时间 / 来源'],outlook.events.map(e=>[esc(e.date)+'<br>'+esc(e.statusText),esc(e.forecastText),esc(when(e.asOf))+'<br>'+source(e)]))+`<p class="pr-muted">${esc(outlook.note)}</p>`;
    document.getElementById('pr-price-basis').textContent=`参考收盘：${n(outlook.price,4)} ${outlook.currency}，${when(outlook.priceDate)}，${outlook.priceSource}。使用未复权价格进行每股估值，历史收益率仍使用复权序列。`;
    if(valuationTarget!==portfolioContext.id+'|'+target){valuationTarget=portfolioContext.id+'|'+target;const form=document.getElementById('pr-valuation');form.reset();try{const saved=JSON.parse(localStorage.getItem(valuationKey())||'null');if(saved)for(const key of ['period','annual_eps','multiple','basis'])form.elements[key].value=saved[key]||'';}catch(_){document.getElementById('pr-valuation-error').textContent='盈利假设草稿无法读取，原存储未删除。';}}
    calculateValuation(false);
  }
  function calculateValuation(showError=true){
    const form=document.getElementById('pr-valuation'),input=Object.fromEntries(new FormData(form));valuationResult=null;
    document.getElementById('pr-use-valuation').hidden=true;document.getElementById('pr-valuation-result').innerHTML='';document.getElementById('pr-valuation-error').textContent='';
    try{const outlook=C.earningsOutlook(combined('events'),target,data.histories?.[target]),result=C.valuationScenario(outlook,input);valuationResult=result;
      document.getElementById('pr-valuation-result').innerHTML=`<p>${esc(result.note)}</p>${table(['年度EPS假设','PE假设','条件估值 / '+esc(result.currency),'相对参考价'],result.scenarios.map(r=>[n(r.eps,4),n(r.multiple),n(r.value,4),pct(r.change)]))}<p class="pr-muted">敏感性网格：EPS为输入值的90% / 100% / 110%，PE为80% / 100% / 120%。固定扰动不是预测区间，不含股息、费用或汇率变化；不能反推出唯一的市场真实预期。</p>`;
      document.getElementById('pr-use-valuation').hidden=false;
    }catch(e){if(showError)document.getElementById('pr-valuation-error').textContent=e.message;}
  }
  document.getElementById('pr-valuation').onsubmit=e=>{e.preventDefault();calculateValuation();};
  document.getElementById('pr-valuation').oninput=()=>{calculateValuation(false);try{localStorage.setItem(valuationKey(),JSON.stringify(Object.fromEntries(new FormData(document.getElementById('pr-valuation')))));}catch(_){document.getElementById('pr-valuation-error').textContent='假设草稿未能保存；请导出研究备份。';}};
  document.getElementById('pr-use-valuation').onclick=()=>{if(!valuationResult)return;const form=document.getElementById('pr-freeze'),text=`${target}：${valuationResult.note}`;if((form.elements.expectation.value+'\n'+text).length>2000){error('预期基准草稿已接近长度上限，请先精简已有内容。');return;}form.elements.expectation.value=[form.elements.expectation.value,text].filter(Boolean).join('\n');saveDraft();selectTab('review');form.elements.expectation.focus();};
  async function openTarget(event){const button=event.target.closest('[data-research-target]');if(!button)return;if(data.busy){error('当前研究数据仍在更新，完成后可切换公司重新采集。');selectCompanyResearch('peers');research.scrollIntoView({block:'start'});return;}target=button.dataset.researchTarget;document.getElementById('pr-target').value=target;selectCompanyResearch('peers');selectTab('events');render();research.scrollIntoView({block:'start'});document.getElementById('pr-refresh').click();}
  document.getElementById('pr-account').addEventListener('click', event => { if(event.target.closest('[data-edit-cash]')) openPortfolioCash(); });
  assets.addEventListener('click',openTarget);research.addEventListener('click',openTarget);
  function renderReview(){
    const result=C.backtest(data.records||[],data.histories||{}),filter=document.getElementById('pr-record-filter').value.trim().toLowerCase();
    const rows=result.rows.filter(r=>[r.target,r.author,r.note].some(v=>String(v||'').toLowerCase().includes(filter))).reverse();
    document.getElementById('pr-backtest').innerHTML=`<h4>冻结记录 · ${result.rows.length} 条</h4><p>前段 ${result.train.samples} 条 / 平均费用后变化 ${pct(result.train.mean)}；后段 ${result.test.samples} 条 / ${pct(result.test.mean)}</p><p class="pr-muted">${esc(result.note)}</p>${rows.map(r=>`<details class="pr-record"><summary>${esc(r.target)} · ${esc(r.author||'旧版未署名')} · v${r.version||1} · ${esc({pending:'待到期',missing:'缺少对齐行情',evaluated:'已核对',invalid:'记录无效'}[r.status])}</summary><p class="pr-muted">冻结于 ${esc(r.created_at)} · 到期 ${esc(r.due_at)}${r.parent_id?' · 修订自 '+esc(r.parent_id):''}</p><div class="pr-summary"><span>费用后模拟变化<strong>${pct(r.net)}</strong></span><span>标的减基准<strong>${n(r.excess)} 个百分点</strong></span><span>最大不利变化<strong>${pct(r.adverse)}</strong></span></div><p>${esc(r.review||'等待足够的后续行情')}</p>${[['note','判断依据'],['expectation','预期基准'],['confirmation','成立条件'],['invalidation','失效条件']].map(([key,label])=>`<p><b>${label}：</b>${esc(r[key]||'旧版未单列')}</p>`).join('')}<p class="pr-muted">单边手续费 ${n(r.fee_bps)} / 滑点 ${n(r.slippage_bps)} bps；税费 ${n(r.tax_bps||0)} bps；年化借券 ${n(r.borrow_bps||0)} bps。${esc(r.benchmark||'旧版未指定基准')}；比较差值为标的收益减基准收益，不是做空策略超额收益。</p>${C.eventFollowup(r,combined('events')).map(e=>`<p>日程跟踪：${esc(e.title)} · 冻结 ${esc(e.frozenDate)} / 当前 ${esc(e.currentDate||'未取得')} · ${esc(e.status)}</p>`).join('')}${(r.inputs?.events||[]).map(e=>`<p>${esc(e.title)} · ${esc(e.date)} · ${source(e)}</p>`).join('')}${r.id?`<button type="button" class="secondary-button" data-revise="${esc(r.id)}">据此新建修订</button>`:''}</details>`).join('')||'<p>暂无匹配的冻结记录。</p>'}`;
  }
  function lag(){const result=C.propagation(combined('attention'),target,document.getElementById('pr-from').value,document.getElementById('pr-to').value);
    const prices=C.attentionPrice(combined('attention'),target,document.getElementById('pr-from').value,data.histories?.[target]);
    const samples=Object.values(C.attention(combined('attention'),target)).map(rows=>rows.at(-1));
    platformList.innerHTML=[...new Set(['新闻','小红书','抖音','X','Threads',...samples.map(r=>r.platform)])].map(p=>`<option value="${esc(p)}"></option>`).join('');
    document.getElementById('pr-propagation').innerHTML=`<h4>${esc(result.status)}</h4><p>新闻与社交平台分开采样；没有采到的内容不是零热度，也不是全平台无人讨论。</p><p>最近采样 ${esc(when(data.attention_sampling?.last_sample_at))} · 本机采样间隔 ${data.attention_sampling?.interval_seconds||'--'} 秒</p><p class="pr-muted">${esc(data.attention_sampling?.note||'当前使用已保存或导入样本。')}</p>`+table(['平台','最近样本日','提及 / 样本','收缩后占比','可用性'],samples.map(r=>[esc(r.platform),esc(r.date),`${r.count} / ${r.total}`,pct(r.smoothed*100),esc(r.quality)]))+table(['候选滞后(日)','前段样本','前段相关','后段样本','后段相关'],result.candidates.map(r=>[r.lag,r.train,n(r.trainCorrelation,3),r.test,n(r.testCorrelation,3)]))+`<p>同步相关 ${n(result.zeroCorrelation,3)} · 反向相关 ${n(result.reverseCorrelation,3)}</p><p class="pr-muted">${result.best?'探索性候选：'+result.best.lag+'日。':'样本不足，未得出有效传导结论。'} ${esc(result.note)}</p><details><summary>热度与随后价格变化</summary>`+table(['观察间隔(日)','前段样本','前段相关','后段样本','后段相关'],prices.map(r=>[r.lag,r.train,n(r.trainCorrelation,3),r.test,n(r.testCorrelation,3)]))+'<p class="pr-muted">使用采样首次入库之后的价格区间；事后导入的旧热度不能倒填成当时已知的信号。样本重叠，不作因果结论。</p></details>';
    heatBacktest();
  }
  function heatBacktest(){
    heatReport=C.attentionBacktest(combined('attention'),target,document.getElementById('pr-from').value,data.histories?.[target],window.STockBacktest,
      {fee_bps:Number(document.getElementById('pr-heat-fee').value),slippage_bps:Number(document.getElementById('pr-heat-slip').value),sell_tax_bps:Number(document.getElementById('pr-heat-tax').value)});
    document.getElementById('pr-heat-result').innerHTML=`<p><b>${esc(heatReport.status)}</b> · 当前有效 ${heatReport.samples} 日</p><p>${esc(heatReport.note)}</p>`+table(['版本','费用后收益','最大回撤','标的持有收益','已平仓笔数'],heatReport.runs.map(r=>[esc(r.version),pct(r.result.metrics.net_return),pct(r.result.metrics.max_drawdown),pct(r.result.metrics.benchmark_return),r.result.metrics.closed_trades]));
    document.getElementById('pr-heat-save').disabled=!heatReport.runs.length;
    document.getElementById('pr-heat-status').textContent='';
  }
  document.getElementById('pr-heat-run').onclick=heatBacktest;
  for(const id of ['pr-from','pr-to','pr-heat-fee','pr-heat-slip','pr-heat-tax'])document.getElementById(id).addEventListener('input',()=>{heatReport=null;document.getElementById('pr-heat-save').disabled=true;document.getElementById('pr-heat-result').textContent='输入已变更，请重新运行新旧对照。';});
  document.getElementById('pr-heat-save').onclick=async function(){
    if(!heatReport?.runs.length)return;const selected=portfolioContext.id,runs=heatReport.runs;this.disabled=true;
    let saved=0;try{for(const row of runs){if(selected!==portfolioContext.id)throw Error('组合已切换，停止保存');await api('/api/portfolio/research',{method:'POST',body:{action:'save_backtest',name:row.snapshot.options.target+' 热度重建 '+row.version,snapshot:row.snapshot}});saved++;}
      if(selected===portfolioContext.id)document.getElementById('pr-heat-status').textContent='已保存两份带版本、样本、费用与信号的回测快照，可在回测系统重现。';
    }catch(e){if(selected===portfolioContext.id)document.getElementById('pr-heat-status').textContent=`已保存 ${saved} 份；`+e.message;}finally{if(selected===portfolioContext.id)this.disabled=false;}
  };
  async function load(){
    const selected=portfolioContext.id,year=performanceYear,sequence=++loadSequence;
    clearTimeout(poll);
    try{
      const result=await api('/api/portfolio/research?year='+year);
      if(selected!==portfolioContext.id||year!==performanceYear||sequence!==loadSequence)return;
      data=result;render();error([data.error,...(data.errors||[])].filter(Boolean).join('\n'));
    }catch(e){if(selected===portfolioContext.id&&year===performanceYear&&sequence===loadSequence)error(e.message);}
    finally{
      if(selected===portfolioContext.id&&year===performanceYear&&sequence===loadSequence)
        poll=setTimeout(load,document.hidden?60000:data.busy||data.performance?.busy?2000:30000);
    }
  }
  function renderExposure(){
    const fx=Object.entries(portfolio.fx_details||{});
    document.getElementById('pr-fx-result').innerHTML=`<details><summary>估值汇率与来源时间</summary>${table(['货币对','报价','来源','来源时间 / 状态'],fx.map(([pair,row])=>[esc(pair),n(row.price,5),esc(row.source||'待核验'),esc(row.quote_time||'时间未取得')+' · '+esc(({live:'实时',delayed:'延迟',closed:'休市',stale:'旧汇率保留',error:'获取失败',missing:'缺失',reference:'参考',nav:'净值',manual:'手动',unavailable:'不可用'})[row.status]||'未知')]))}</details>`;
    const exposure=C.lookthrough(portfolio.positions||[],namedFunds(data.funds),{basis:riskBasis,cash_base:portfolio.summary?.cash_base,receivable_base:portfolio.summary?.receivable_base,payable_base:portfolio.summary?.payable_base});
    document.getElementById('pr-exposure-result').insertAdjacentHTML('beforeend',`<details><summary>ETF 穿透与重复敞口 · ${exposure.overlaps.length} 项已知重叠</summary>${table(['底层标的','穿透市值','占'+(riskBasis==='total'?'总资产':'证券市值'),'来自持仓'],exposure.overlaps.map(r=>[esc(r.name),n(r.value),pct(r.weight),[...new Set(r.origins)].map(holdingLabel).join(' / ')]))}${table(['ETF A','ETF B','共同股票数','已核验权重重叠率'],exposure.pairs.map(r=>[holdingLabel(r.a),holdingLabel(r.b),r.count,pct(r.overlap)]))}<p class="pr-muted">${esc(exposure.note)}<br>${exposure.dates.map(r=>esc(r.target+' 披露日 '+(r.as_of||'未知'))).join('；')}<br>${exposure.unknown.map(esc).join('；')}<br>仅覆盖已取得底仓，不构成全部风险敞口。行业分布是外层证券分类，不冒充穿透后的行业。</p></details>`);
  }
  function setPortfolio(value){
    const positions=namedRows(value.positions||[]);
    portfolio={...value,positions};
    holdingNames=new Map(positions.map(row=>[`${row.market}:${row.symbol}`,row.name]));
  }
  function updatePortfolio(value){
    setPortfolio(value);
    allocations();
    research.querySelector('h3').textContent=(portfolio.name||'默认组合')+' · 同行与事件研究';
    if(!data.performance?.busy&&(portfolio.positions||[]).some(p=>!data.performance?.targets?.includes(p.market+':'+p.symbol)))load();
  }
  document.getElementById('pr-account').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button:not([type="button"])');button.disabled=true;
    if(state.portfolioPrincipalDirty){document.getElementById('pr-account-error').textContent='我的资产中有未保存的本金，请先保存后再修改账户口径。';button.disabled=false;return;}
    try{const values=Object.fromEntries(new FormData(event.target));if(values.principal==='')delete values.principal;if(values.cash_mode==='auto')delete values.cash;await api('/api/portfolio/account',{method:'POST',body:values});event.target.dataset.dirty='';document.getElementById('pr-account-error').textContent='';await refreshDashboard();}
    catch(e){document.getElementById('pr-account-error').textContent=e.message;}finally{button.disabled=false;}});
  document.getElementById('pr-account').addEventListener('input',event=>{event.currentTarget.dataset.dirty='1';});
  const correction=document.createElement('dialog');correction.className='modal position-modal portfolio-research';document.body.append(correction);
  assets.addEventListener('click',async event=>{const button=event.target.closest('[data-correct]');if(!button)return;button.disabled=true;
    try{const row=(portfolio.positions||[]).find(r=>r.market+':'+r.symbol===button.dataset.correct);const identity=await api('/api/securities/resolve?market='+row.market+'&symbol='+encodeURIComponent(row.symbol));
      correction.innerHTML=`<h3>核验名称与原始成本</h3><p>${esc(identity.name)} · ${esc(identity.symbol)} · 报价 ${esc(identity.currency)}</p><form id="pr-correction"><input type="hidden" name="market" value="${esc(row.market)}"><input type="hidden" name="symbol" value="${esc(row.symbol)}"><div class="pr-controls">${field('原始单位成本','original_cost',row.average_cost)}${select('上方成本实际币种','cost_currency',row.currency)}${field('1原币折合报价币','cost_fx_rate','')}${field('换算率日期','cost_fx_date','','date')}</div><p class="pr-muted">异币种必须提供汇率和日期。旧名称、币种及成本会保留在校正记录中。</p><label><input name="confirmed" type="checkbox" required>我已核对原始成本，确认这次校正</label><p data-correction-error role="alert" class="pr-warning"></p><div class="pr-controls"><button class="primary-button">确认校正</button><button type="button" data-close-correction class="secondary-button">取消</button></div></form>`;
      correction.querySelector('[data-close-correction]').onclick=()=>correction.close();correction.querySelector('form').onsubmit=async e=>{e.preventDefault();try{const values=Object.fromEntries(new FormData(e.target));await api('/api/portfolio/identity',{method:'POST',body:{...values,confirmed:values.confirmed==='on'}});correction.close();await refreshDashboard();}catch(error){correction.querySelector('[data-correction-error]').textContent=error.message;}};correction.showModal();
    }catch(error){document.getElementById('pr-account-error').textContent=error.message;}finally{button.disabled=false;}});
  document.getElementById('pr-refresh').onclick=async()=>{try{target=document.getElementById('pr-target').value.trim().toUpperCase();await api('/api/portfolio/research',{method:'POST',body:{action:'refresh',target}});await load();}catch(e){error(e.message);}};
  document.getElementById('pr-target').onchange=()=>{target=document.getElementById('pr-target').value.trim().toUpperCase();render();};
  document.getElementById('pr-lag').onclick=lag;
  document.getElementById('pr-import').onclick=()=>{const input=document.createElement('input');input.type='file';input.accept='.json';input.onchange=async()=>{try{const file=input.files[0];if(!file)return;if(file.size>500000)throw new Error('资料须小于500KB');const value=C.validateImport(JSON.parse(await file.text()));await api('/api/portfolio/research',{method:'POST',body:{action:'import',data:value}});await load();}catch(e){error(e.message);}};input.click();};
  document.getElementById('pr-template').onclick=()=>{const value={events:[{target:'US:AAPL',title:'请替换为已核对事件',date:'2026-11-01',status:'estimated',source_url:'https://www.apple.com/newsroom/'}],attention:[{target:'US:AAPL',platform:'新闻',date:'2026-10-22',count:1,total:100,source:'填写实际采样来源',coverage:'填写固定采样范围，示例数值须替换',source_url:'https://www.apple.com/newsroom/'}],peers:[]};const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='STock-research-format.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const draftKey=()=>`stock.research-draft.${portfolioContext.id}`;
  document.getElementById('pr-freeze').insertAdjacentHTML('afterbegin','<input type="hidden" name="event_key"><input type="hidden" name="event_date"><p id="pr-event-selection" class="pr-muted"></p>');
  research.addEventListener('click',event=>{const button=event.target.closest('[data-event-draft]');if(!button)return;
    try{const form=document.getElementById('pr-freeze');if(draftTarget&&draftTarget!==target&&form.elements.note.value)throw new Error('草稿属于另一标的，请先保存或新建独立判断');
      const values=C.eventDraft(eventModel,button.dataset.eventDraft,Object.fromEntries(new FormData(form)));
      for(const [key,value] of Object.entries(values))form.elements[key].value=value;
      draftTarget=target;document.getElementById('pr-event-selection').textContent='关联事件：'+eventModel.links.find(r=>r.key===values.event_key).event.title+'；冻结仅保留这一事件。';
      saveDraft();selectTab('review');form.elements.note.focus();error('');
    }catch(e){error(e.message);}
  });
  function saveDraft(){draftTarget=draftTarget||target;const form=document.getElementById('pr-freeze');form.dataset.dirty='1';try{localStorage.setItem(draftKey(),JSON.stringify({target:draftTarget,values:Object.fromEntries(new FormData(form))}));document.getElementById('pr-draft-state').textContent='草稿已保留在此设备；冻结后修订会创建新版本。作者署名不是登录认证。';}catch(_){document.getElementById('pr-draft-state').textContent='设备存储不可用，草稿未保存；请导出研究备份。';}}
  function restoreDraft(){const form=document.getElementById('pr-freeze');try{const saved=JSON.parse(localStorage.getItem(draftKey())||'null');if(saved?.values){for(const [key,value] of Object.entries(saved.values))if(form.elements.namedItem(key)&&typeof value==='string')form.elements.namedItem(key).value=value;if(saved.target){draftTarget=saved.target;target=saved.target;document.getElementById('pr-target').value=target;}form.dataset.dirty='1';}}catch(_){error('草稿读取失败，已保留原存储；可新建独立判断。');}}
  function selectTab(id){if(!['events','propagation','review'].includes(id))return;researchTab=id;if(innerWidth<=760)document.getElementById('nav-research').open=false;research.querySelectorAll('[role="tab"]').forEach(el=>{const chosen=el.dataset.prTab===id;el.setAttribute('aria-selected',String(chosen));el.tabIndex=chosen?0:-1;document.getElementById(el.getAttribute('aria-controls')).hidden=!chosen;});}
  research.addEventListener('click',event=>{const tab=event.target.closest('[data-pr-tab]');if(tab)selectTab(tab.dataset.prTab);const button=event.target.closest('[data-revise]');if(button){const row=(data.records||[]).find(r=>r.id===button.dataset.revise);if(!row)return;const form=document.getElementById('pr-freeze');for(const el of form.elements)if(el.name&&row[el.name]!=null)el.value=row[el.name];form.elements.parent_id.value=row.id;form.elements.event_key.value=row.event_key||'';form.elements.event_date.value=row.event_date||'';draftTarget=row.target;target=row.target;document.getElementById('pr-target').value=target;saveDraft();form.scrollIntoView({block:'start'});form.elements.note.focus();}});
  research.querySelector('[role="tablist"]').addEventListener('keydown',event=>{const keys=['events','propagation','review'],index=keys.indexOf(researchTab);let next;if(event.key==='ArrowRight')next=keys[(index+1)%3];if(event.key==='ArrowLeft')next=keys[(index+2)%3];if(event.key==='Home')next=keys[0];if(event.key==='End')next=keys[2];if(next){event.preventDefault();selectTab(next);document.getElementById('pr-tab-'+next).focus();}});
  document.getElementById('pr-freeze').addEventListener('input',saveDraft);
  document.getElementById('pr-record-filter').oninput=renderReview;
  document.getElementById('pr-new-judgment').onclick=()=>{const form=document.getElementById('pr-freeze');form.reset();form.elements.parent_id.value='';form.elements.event_key.value='';form.elements.event_date.value='';draftTarget='';document.getElementById('pr-event-selection').textContent='';form.elements.author.value=portfolio.account?.owner||'';form.dataset.dirty='';try{localStorage.removeItem(draftKey());}catch(_){}render();document.getElementById('pr-draft-state').textContent='独立判断草稿；已有冻结记录未改变。';};
  document.getElementById('pr-freeze').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button:not([type="button"])');button.disabled=true;try{if(draftTarget&&draftTarget!==target)throw new Error('草稿标的已切换，请返回原标的或新建独立判断');const values={...Object.fromEntries(new FormData(event.target)),method:'event-forward-v2'};C.freeze(target,data.histories?.[target],combined('events'),values);await api('/api/portfolio/research',{method:'POST',body:{action:'freeze',target,...values,direction:Number(values.direction)}});try{localStorage.removeItem(draftKey());}catch(_){}event.target.reset();event.target.dataset.dirty='';draftTarget='';document.getElementById('pr-event-selection').textContent='';await load();document.getElementById('pr-draft-state').textContent='判断已冻结。原始依据、条件和来源已保留；修订请新建版本。';}catch(e){error(e.message);}finally{button.disabled=false;}};
  document.getElementById('pr-risk-basis').onchange=e=>{riskBasis=e.target.value;allocations();};
  document.getElementById('pr-risk-window').onchange=e=>{riskWindow=Number(e.target.value);allocations();};
  document.getElementById('pr-performance-year').onchange=e=>{performanceYear=Number(e.target.value);renderPerformance();load();};
  document.getElementById('pr-performance-refresh').onclick=async()=>{const selected=portfolioContext.id,button=document.getElementById('pr-performance-refresh');button.disabled=true;document.getElementById('pr-performance-error').textContent='';try{await api('/api/portfolio/research',{method:'POST',body:{action:'refresh_performance',year:performanceYear}});await api('/api/portfolio/refresh',{method:'POST',body:{}});if(selected===portfolioContext.id){pollPortfolioStatus();await load();}}catch(e){if(selected===portfolioContext.id)document.getElementById('pr-performance-error').textContent=e.message;}finally{if(selected===portfolioContext.id)button.disabled=!!data.performance?.busy;}};
  document.getElementById('pr-open-research').onclick=()=>{selectCompanyResearch('peers');research.scrollIntoView({block:'start'});document.getElementById('pr-target').focus();};
  document.getElementById('pr-return-portfolio').onclick=()=>{setView('portfolio');window.PortfolioBooks.open(portfolioContext.id);};
  document.getElementById('pr-export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({...data,portfolio_id:portfolioContext.id,portfolio_name:portfolio.name,exported_at:new Date().toISOString()},null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='STock-private-research.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  let sequence=0,timer;
  async function identify(){const id=++sequence,market=document.getElementById('position-market').value,symbol=document.getElementById('position-symbol').value.trim(),form=document.getElementById('position-form'),session=form.dataset.openSequence,dialog=document.getElementById('position-dialog');const name=document.getElementById('position-name'),status=document.getElementById('position-identity-status');const current=()=>id===sequence&&dialog.open&&form.dataset.openSequence===session&&document.getElementById('position-market').value===market&&document.getElementById('position-symbol').value.trim()===symbol;name.value='';if(!symbol||!dialog.open)return;status.textContent='正在核验正式名称…';
    try{const result=await api('/api/securities/resolve?market='+market+'&symbol='+encodeURIComponent(symbol));if(!current())return;name.value=result.name;document.getElementById('position-currency').value=result.currency;status.textContent=result.source+' · '+(result.as_of||'本次公开目录核验');if(typeof syncPositionWhitelist==='function')syncPositionWhitelist();}
    catch(e){if(current()){status.textContent=e.message;if(typeof syncPositionWhitelist==='function')syncPositionWhitelist();}}}
  document.getElementById('position-symbol').addEventListener('input',()=>{sequence++;clearTimeout(timer);timer=setTimeout(identify,450);});
  document.getElementById('position-market').addEventListener('change',identify);
  document.getElementById('position-currency').addEventListener('change',()=>{document.getElementById('position-currency').value=({US:'USD',CN:'CNY',HK:'HKD',KR:'KRW',FUND:'CNY'})[document.getElementById('position-market').value];});
  window.PortfolioResearch={update:updatePortfolio};
  if(state.dashboard){setPortfolio(state.dashboard.portfolio||{});render();}
  window.addEventListener('portfolio-change',()=>{clearTimeout(poll);data={histories:{},events:[],peers:[],imports:[],records:[]};setPortfolio({});target='US:AAPL';draftTarget='';document.getElementById('pr-event-selection').textContent='';document.getElementById('pr-target').value=target;document.getElementById('pr-account').dataset.ready='';document.getElementById('pr-account').dataset.dirty='';document.getElementById('pr-freeze').reset();document.getElementById('pr-freeze').dataset.dirty='';restoreDraft();render();load();});
  restoreDraft();selectTab(researchTab);
  load();
})();
