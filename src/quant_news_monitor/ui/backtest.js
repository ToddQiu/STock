(function(){
  'use strict';
  const B=window.STockBacktest,esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const L=window.STockBacktestLearning;
  const n=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'--';
  const unit=(value,suffix='')=>typeof value==='number'&&Number.isFinite(value)?n(value)+suffix:'未适用';
  const helpButton=key=>`<button type="button" class="bt-help-button" data-bt-help="${key}" aria-label="${esc(L.help[key][0])}说明" title="${esc(L.help[key][0])}说明" aria-controls="bt-help" aria-expanded="false">${uiIcon('message-circle').outerHTML}</button>`;
  const host=document.getElementById('research-backtest-panel');
  const panel=document.createElement('div');panel.id='backtest-system';panel.className='bt-system';
  const numeric=[['capital','模拟本金'],['allocation','入场仓位 %'],['fee_bps','单边手续费 bps'],['slippage_bps','单边滑点 bps'],['sell_tax_bps','卖出税费 bps'],['stop_loss','收盘停损 %（0关闭）'],['fast','短均线日数'],['slow','长均线日数'],['breakout','突破窗口日数'],['exit_window','离场窗口日数'],['hold_days','判断有效天数']];
  const field=(key,label,input)=>`<div class="bt-field" data-param="${key}"><div class="bt-label"><label for="bt-field-${key}">${label}</label>${helpButton(key)}</div>${input.replace('<input ',`<input id="bt-field-${key}" `).replace('<select ',`<select id="bt-field-${key}" `)}</div>`;
  const numberField=([key,label])=>field(key,label,`<input name="${key}" type="number" step="${['fast','slow','breakout','exit_window','hold_days'].includes(key)?'1':'any'}" min="0" value="${B.defaults[key]}" required>`);
  const advanced=['fee_bps','slippage_bps','sell_tax_bps','stop_loss'];
  panel.innerHTML=`<div class="bt-intro"><div><strong>把一套规则放回历史，检查赚亏与风险</strong><p>单标的日线模拟 · 下一条日线收盘成交 · 不修改真实持仓</p></div><div class="bt-actions"><div class="bt-command"><button type="button" class="secondary-button" data-bt-demo>运行教学案例</button>${helpButton('demo')}</div><div class="bt-command"><button type="button" class="text-button" data-bt-guide="tutorial">使用教程</button>${helpButton('tutorial')}</div></div></div>
    <form id="bt-form"><h3>研究设置</h3><div class="bt-fields">${field('target','研究标的','<input name="target" value="US:AAPL" list="pr-holdings" maxlength="32" required>')}${field('benchmark','比较基准（可留空）','<input name="benchmark" value="US:SPY" maxlength="32">')}${field('strategy','策略',`<select name="strategy">${Object.entries(B.strategies).map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}</select>`)}${field('price_mode','价格口径','<select name="price_mode"><option value="adjusted">来源复权收盘</option><option value="raw">未复权收盘</option></select>')}${field('start','开始日期（可留空）','<input type="date" name="start">')}${field('end','结束日期（可留空）','<input type="date" name="end">')}${numeric.filter(([k])=>!advanced.includes(k)).map(numberField).join('')}</div>
    <details id="bt-advanced"><summary>费用与成交假设 <span id="bt-cost-summary"></span></summary><div class="bt-fields">${numeric.filter(([k])=>advanced.includes(k)).map(numberField).join('')}${field('liquidate','期末结算持仓','<input name="liquidate" type="checkbox" checked>')}</div><p class="quiet-note">默认费用仅是模拟假设，不代表任何市场或券商的实际收费。</p></details>
    <section id="bt-preflight" class="bt-preflight" aria-label="运行前检查"><h3>运行前检查 ${helpButton('preflight')}</h3><div id="bt-data-status" role="status"></div></section>
    <div class="bt-actions"><div class="bt-command"><button type="submit" class="primary-button">运行回测</button>${helpButton('run')}</div><div class="bt-command"><button type="button" id="bt-refresh" class="secondary-button">更新日线</button>${helpButton('refresh')}</div><div class="bt-command"><button type="button" id="bt-import" class="secondary-button">导入快照</button>${helpButton('import')}</div></div></form>
    <p id="bt-status" role="status"></p><p id="bt-error" class="pr-warning" role="alert"></p><div id="bt-output" hidden></div>
    <section class="bt-archive"><h3>历史记录</h3><div class="bt-actions"><label>已保存回测<select id="bt-archives"><option value="">选择记录</option></select></label><button id="bt-open" type="button" class="secondary-button">重现记录</button>${helpButton('save')}</div></section>`;
  const context=document.createElement('div');context.className='bt-actions';
  context.innerHTML=`<div class="bt-field"><div class="bt-label"><label for="bt-book">所属组合</label>${helpButton('book')}</div><select id="bt-book" aria-label="回测所属组合"><option value="">正在读取组合</option></select></div><span class="quiet-note">私有研究快照 · 不修改实际持仓</span>`;
  panel.querySelector('.bt-intro').after(context);host.append(panel);
  const settings=panel.querySelector('#bt-form > .bt-fields');
  for(const [id,title,keys] of [
    ['bt-instruments','1. 选择标的与规则',['target','benchmark','strategy','price_mode']],
    ['bt-budget','2. 确定区间与资金',['start','end','capital','allocation']],
    ['bt-parameters','3. 设置规则参数',['fast','slow','breakout','exit_window','hold_days']]
  ]){
    const group=document.createElement('fieldset');group.id=id;group.className='bt-form-group';
    group.innerHTML=`<legend>${title}</legend><div class="bt-fields"></div>`;
    keys.forEach(key=>group.querySelector('.bt-fields').append(settings.querySelector(`[data-param="${key}"]`)));
    settings.before(group);
  }
  settings.remove();
  const helpPanel=document.createElement('aside');helpPanel.id='bt-help';helpPanel.className='bt-help';helpPanel.hidden=true;helpPanel.setAttribute('aria-labelledby','bt-help-title');
  helpPanel.innerHTML=`<div><h3 id="bt-help-title"></h3><button type="button" class="icon-button" aria-label="关闭说明" title="关闭说明">${uiIcon('x').outerHTML}</button></div><small class="bt-help-state"></small><p class="bt-help-body"></p><section class="bt-help-example" hidden><strong>举个例子</strong><p></p></section>`;
  document.body.append(helpPanel);let helpTrigger=null,helpPinned=false,suppressFocus=false;
  function closeHelp(restoreFocus=false){helpPanel.hidden=true;helpPinned=false;helpTrigger?.setAttribute('aria-expanded','false');if(restoreFocus){suppressFocus=true;helpTrigger?.focus({preventScroll:true});suppressFocus=false;}}
  function showHelp(button,pin=false){
    const key=button.dataset.btHelp,[title,body]=L.help[key];
    helpTrigger?.setAttribute('aria-expanded','false');helpTrigger=button;helpPinned=pin;
    helpPanel.querySelector('h3').textContent=title;helpPanel.querySelector('.bt-help-body').textContent=body;
    helpPanel.querySelector('.bt-help-state').textContent=pin?'评注 · 已固定':'评注';
    const example=helpPanel.querySelector('.bt-help-example');example.hidden=!L.examples[key];example.querySelector('p').textContent=L.examples[key]||'';
    helpPanel.dataset.pinned=String(pin);helpPanel.hidden=false;helpPanel.scrollTop=0;button.setAttribute('aria-expanded','true');
  }
  const helpTarget=node=>node.closest('[data-bt-help]')||node.closest('.bt-field,.bt-summary>div,.bt-command,.bt-analysis h4,.bt-system th')?.querySelector('[data-bt-help]');
  helpPanel.querySelector('button').onclick=()=>closeHelp(true);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!helpPanel.hidden){e.preventDefault();closeHelp(true);}});
  document.addEventListener('pointerover',e=>{if(e.pointerType==='touch'||helpPinned)return;const button=e.target.closest('[data-bt-help]')||e.target.closest('.bt-label,.bt-summary>div,.bt-reading dt,.bt-analysis h4,.bt-system th')?.querySelector('[data-bt-help]');if(button&&(button!==helpTrigger||helpPanel.hidden))showHelp(button);});
  document.addEventListener('focusin',e=>{if(suppressFocus||helpPinned)return;const button=helpTarget(e.target);if(button)showHelp(button);});
  document.addEventListener('click',e=>{const button=e.target.closest('[data-bt-help]');if(button){e.preventDefault();if(helpPinned&&helpTrigger===button&&!helpPanel.hidden)closeHelp();else showHelp(button,true);}else if(!helpPanel.contains(e.target))closeHelp();});
  const form=panel.querySelector('form'),q=id=>panel.querySelector('#'+id);
  let research={histories:{},records:[]},snapshot=null,result=null,diagnostics=null,busy=false,loadedBook=null,serial=0,page=0;
  window.getBacktestAssistantContext=()=>{
    if(!result||!snapshot)return null;
    const r=result,m=r.metrics;
    return {target:r.options.target,strategy:B.strategies[r.options.strategy],currency:r.currency,
      created_at:snapshot.created_at,engine:B.VERSION,source:r.audit.source,from:r.audit.from,to:r.audit.to,
      capital:r.options.capital,net_return:m.net_return,max_drawdown:m.max_drawdown,
      closed_trades:m.closed_trades,win_rate:m.win_rate,fees:m.fees,slippage:m.slippage,
      samples:r.audit.samples,benchmark_return:m.benchmark_return,
      warnings:L.preflight(snapshot).warnings.slice(0,12).map(text=>text.slice(0,256))};
  };
  const book=()=>portfolioContext.id,key=()=>`stock.backtest.preferences.${book()}`;
  const visible=()=>!host.hidden&&state.currentView==='backtest';
  function friendly(message){return String(message||'').replace(/\b(capital|allocation|fee_bps|slippage_bps|sell_tax_bps|fast|slow|breakout|exit_window|hold_days|stop_loss)参数无效/g,(_,key)=>L.help[key][0]+'参数无效，请检查范围与整数要求');}
  function error(e){q('bt-error').textContent=friendly(e?.message||e);}
  function setBusy(value){busy=value;panel.setAttribute('aria-busy',String(value));panel.querySelectorAll('button').forEach(b=>b.disabled=value);form.querySelectorAll('input,select').forEach(el=>el.disabled=value);q('bt-book').disabled=value;if(!value&&result&&q('bt-trades'))trades();}
  function values(){const o={};for(const k of Object.keys(B.defaults)){const el=form.elements[k];o[k]=k==='liquidate'?el.checked:typeof B.defaults[k]==='number'?Number(el.value):el.value.trim();}o.target=o.target.toUpperCase();o.benchmark=o.benchmark.toUpperCase();return B.options(o);}
  function apply(o){for(const k of Object.keys(B.defaults))if(o[k]!==undefined){if(k==='liquidate')form.elements[k].checked=o[k];else form.elements[k].value=o[k];}parameters();}
  function parameters(){const strategy=form.elements.strategy.value;for(const key of ['fast','slow','breakout','exit_window','hold_days'])panel.querySelector(`[data-param="${key}"]`).hidden=!({fast:'sma',slow:'sma',breakout:'breakout',exit_window:'breakout',hold_days:'frozen'}[key]===strategy);q('bt-parameters').hidden=strategy==='buy_hold';q('bt-cost-summary').textContent=`手续费 ${form.elements.fee_bps.value} / 滑点 ${form.elements.slippage_bps.value} bps · 停损 ${Number(form.elements.stop_loss.value)||'关闭'}`;}
  function currentSnapshot(){const o=values();return B.snapshot(o,research.histories[o.target],research.histories[o.benchmark],research.records);}
  function dataStatus(){
    const h=research.histories?.[form.elements.target.value.trim().toUpperCase()];
    if(!h){q('bt-data-status').textContent='未取得该标的日线。请先更新日线；也可以运行不联网的教学案例。';return;}
    let check;try{check=L.preflight(currentSnapshot());}catch(e){check={ready:false,errors:[e.message],warnings:[]};}
    q('bt-data-status').innerHTML=check.ready?`<p>${esc(check.from)} 至 ${esc(check.to)} · ${check.samples} 条日线 · 起点前预热 ${check.warmup} 条 · ${esc(check.currency)}</p><p class="quiet-note">${esc(h.source)} · 数据取得 ${esc(h.observed_at||'未提供')}</p>${check.warnings.length?`<ul>${check.warnings.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:'<p>数据可运行，未发现上述覆盖或基准问题；这不代表策略有效。</p>'}`:`<p class="negative">${check.errors.map(t=>esc(friendly(t))).join('；')}。请调整参数或更新日线。</p>`;
  }
  async function archives(){const id=book(),r=await api('/api/portfolio/backtests');if(id!==book())return;q('bt-archives').innerHTML='<option value="">选择记录</option>'+r.items.map(r=>`<option value="${r.id}">${esc(r.name)} · ${esc(r.target)} · ${esc(r.observed_at.slice(0,10))}</option>`).join('');}
  async function load(){if(busy)return;const id=book(),token=++serial;setBusy(true);error('');try{
    const data=await api('/api/portfolio/research');if(id!==book()||token!==serial)return;
    data.histories ||= {};
    for(const [target,h] of Object.entries(research.histories||{}))if(Date.parse(h.observed_at)>Date.parse(data.histories[target]?.observed_at||'1970-01-01'))data.histories[target]=h;
    research=data;research.records ||= [];loadedBook=id;dataStatus();await archives();
    const list=await api('/api/portfolios');if(id!==book()||token!==serial)return;
    q('bt-book').replaceChildren(...list.portfolios.map(row=>new Option(row.name,row.id)));q('bt-book').value=id;
  }catch(e){if(token===serial)error(e);}finally{if(token===serial)setBusy(false);}}
  window.refreshBacktest=load;
  new MutationObserver(()=>{if(visible()&&loadedBook!==book())load();}).observe(host,{attributes:true,attributeFilter:['hidden']});
  q('bt-book').addEventListener('change',async()=>{if(busy)return;const id=q('bt-book').value;await PortfolioBooks.open(id);q('bt-book').value=book();});
  form.addEventListener('change',()=>{parameters();dataStatus();if(result)q('bt-status').textContent='参数已修改；下方仍是上次回测结果与快照。';try{localStorage.setItem(key(),JSON.stringify(values()));}catch(e){error(e);}});
  function reset(){serial++;loadedBook=null;research={histories:{},records:[]};snapshot=null;result=null;diagnostics=null;form.reset();q('bt-output').hidden=true;q('bt-archives').innerHTML='<option value="">选择记录</option>';q('bt-status').textContent='';q('bt-book').value=book();setBusy(false);error('');try{const saved=JSON.parse(localStorage.getItem(key())||'null');if(saved)apply(B.options(saved));}catch(e){error('回测偏好无法读取，保留原存储。');}parameters();dataStatus();if(visible())load();}
  window.addEventListener('portfolio-change',reset);reset();
  q('bt-refresh').addEventListener('click',async()=>{
    if(busy)return;const id=book(),token=++serial;error('');setBusy(true);
    try{const o=values(),success=[],failed=[];for(const target of [...new Set([o.target,o.benchmark].filter(Boolean))]){
      try{const [market,symbol]=target.split(':'),h=await api(`/api/portfolio/chart?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}&period=1y&mode=daily`);
        if(id!==book()||serial!==token)return;research.histories[target]=B.history(h);success.push(target);
      }catch(e){if(id!==book()||serial!==token)return;failed.push(target+'：'+e.message);if(research.histories[target])research.histories[target]={...research.histories[target],retained_from_cache:true};}
    }dataStatus();q('bt-status').textContent=(success.length?'已取得 '+success.join('、')+' 日线。':'未取得新日线。')+' 已显示的回测仍保留原快照，运行后才使用新数据。';if(failed.length)error('以下未更新，有旧数据的已保留，请核对日期：'+failed.join('；'));}
    catch(e){if(token===serial)error(e);}finally{if(token===serial)setBusy(false);}
  });
  function execute(s){const report=B.report(s);snapshot=JSON.parse(JSON.stringify(s));result=report.result;diagnostics=report;page=0;render();q('bt-status').textContent='回测已完成 · '+snapshot.created_at+' · '+B.VERSION+' · '+report.version;}
  function adoptSnapshot(s){apply(s.options);research.histories[s.options.target]=JSON.parse(JSON.stringify(s.history));if(s.benchmark_history&&s.options.benchmark!==s.options.target)research.histories[s.options.benchmark]=JSON.parse(JSON.stringify(s.benchmark_history));research.records=s.records||[];parameters();dataStatus();}
  form.addEventListener('submit',async e=>{e.preventDefault();if(busy)return;const token=++serial;error('');setBusy(true);try{const o=values();if(o.strategy==='frozen'){const latest=await api('/api/portfolio/research');if(token!==serial)return;research.records=latest.records||[];}execute(currentSnapshot());dataStatus();}catch(e){if(token===serial)error(e);}finally{if(token===serial)setBusy(false);}});
  function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  q('bt-import').addEventListener('click',()=>{const id=book(),token=serial,input=document.createElement('input');input.type='file';input.accept='.json';input.onchange=async()=>{try{const file=input.files[0];if(!file)return;if(file.size>500000)throw Error('快照不能超过500KB');const data=JSON.parse(await file.text()),s=data.snapshot||data;if(id!==book()||token!==serial)return;execute(s);adoptSnapshot(s);q('bt-status').textContent+=' · 已导入原始行情；更新日线后才使用新行情。';error('');}catch(e){if(id===book()&&token===serial)error(e);}};input.click();});
  q('bt-open').addEventListener('click',async()=>{if(busy)return;const id=q('bt-archives').value;if(!id){error('请选择已保存记录');return;}const token=++serial;setBusy(true);error('');try{const data=await api('/api/portfolio/backtests?id='+encodeURIComponent(id));if(token!==serial)return;execute(data.snapshot);adoptSnapshot(data.snapshot);q('bt-status').textContent='已由原始输入重算 · SHA-256 '+data.sha256;}catch(e){if(token===serial)error(e);}finally{if(token===serial)setBusy(false);}});
  function render(){
    closeHelp();
    const r=result,m=r.metrics;q('bt-output').hidden=false;
    q('bt-output').innerHTML=`<h4>${esc(r.options.target)} · ${esc(B.strategies[r.options.strategy])} · ${esc(r.currency)}</h4><div class="bt-summary">${[['期末净值',n(m.final_equity)],['费用后收益',n(m.net_return)+'%'],['最大回撤',n(m.max_drawdown)+'%'],['基准收益',n(m.benchmark_return)+'%'],['相对差值',n(m.excess)+' 个百分点'],['已平仓 / 胜率',m.closed_trades+' / '+n(m.win_rate)+'%']].map(([k,v])=>`<div><span>${k}</span><strong>${v}</strong></div>`).join('')}</div><div class="bt-chart-legend"><span>策略净值</span><span>基准买入持有</span></div><canvas id="bt-chart" height="240" tabindex="0" aria-label="每日净值曲线；下方可展开每日记账表"></canvas><p id="bt-inspect"></p><p>${esc(r.audit.from)} 至 ${esc(r.audit.to)} · ${r.audit.samples} 条 · 预热 ${r.audit.warmup} 条 · 手续费与税 ${n(m.fees)} · 滑点成本 ${n(m.slippage)}（已在成交价体现） · 年化 ${n(m.annual_return)}%</p><details><summary>数据口径与限制</summary><p>${esc(r.audit.source)} · 数据取得 ${esc(r.audit.observed_at)} · ${r.options.price_mode==='adjusted'?'来源复权':'未复权'}</p>${r.warnings.map(t=>`<p>${esc(t)}</p>`).join('')}</details><div class="bt-actions"><label>记录名称<input id="bt-name" maxlength="80" value="${esc(r.options.target+' '+B.strategies[r.options.strategy])}"></label><button id="bt-save" class="secondary-button" type="button">保存快照</button><button id="bt-export" class="icon-button" type="button" title="导出私有快照" aria-label="导出私有快照">${uiIcon('download').outerHTML}</button><button id="bt-csv" class="secondary-button" type="button">导出逐笔 CSV</button></div><h4>逐笔模拟成交</h4><div id="bt-trades"></div><div class="bt-actions"><button id="bt-prev" class="secondary-button" aria-label="上一页成交">←</button><span id="bt-page"></span><button id="bt-next" class="secondary-button" aria-label="下一页成交">→</button></div><details><summary>每日记账表</summary><div class="bt-scroll"><table><thead><tr><th>日期</th><th>净值</th><th>现金</th><th>研究单位</th><th>回撤 %</th></tr></thead><tbody>${r.curve.map(row=>`<tr><td>${row.date}</td><td>${n(row.equity)}</td><td>${n(row.cash)}</td><td>${n(row.units)}</td><td>${n(row.drawdown)}</td></tr>`).join('')}</tbody></table></div></details>`;
    q('bt-output').querySelector('details').insertAdjacentHTML('afterend',reportMarkup());
    const check=L.preflight(snapshot),teaching=snapshot.history.source==='教学合成数据（非真实行情）',reading=L.readout(diagnostics,snapshot);
    q('bt-output').querySelector('h4').insertAdjacentHTML('afterend',`<section class="bt-result-review" aria-labelledby="bt-reading-title"><h4 id="bt-reading-title">${teaching?'教学案例 · 本次结果怎么读':'本次结果怎么读'} ${helpButton('readout')}</h4><p>${teaching?'合成行情，不是市场成绩；此处使用教学快照，不是当前表单。':''}实际覆盖 ${esc(r.audit.from)} 至 ${esc(r.audit.to)}，${r.audit.samples} 条日线。</p><dl class="bt-reading">${reading.items.map(row=>`<div><dt>${esc(row.title)} ${helpButton(row.help)}</dt><dd>${esc(row.text)}</dd></div>`).join('')}</dl><strong>接下来核对什么</strong><ul>${reading.checks.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>${check.warnings.length?`<details><summary>数据与样本提醒 · ${check.warnings.length} 项</summary><ul>${check.warnings.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></details>`:''}</section>`);
    const summary=q('bt-output').querySelector('.bt-summary');
    const metricHelp=['equity','returns','drawdown','benchmark','excess','winrate'];
    [...summary.children].forEach((node,index)=>node.querySelector('span').insertAdjacentHTML('beforeend',helpButton(metricHelp[index])));
    summary.children[3].querySelector('strong').textContent=unit(m.benchmark_return,'%');
    summary.children[4].querySelector('strong').textContent=unit(m.excess,' 个百分点');
    summary.children[5].querySelector('strong').textContent=m.closed_trades+' / '+unit(m.win_rate,'%');
    const detailLine=q('bt-output').querySelector('#bt-inspect + p');detailLine.textContent=detailLine.textContent.replace('年化 --%','年化未适用（不足一年）');
    const addHelp=(node,key)=>node?.insertAdjacentHTML('beforeend',helpButton(key));
    for(const [id,key] of [['bt-save','save'],['bt-export','export'],['bt-csv','csv'],['bt-prev','pagination'],['bt-next','pagination']]){
      const button=q(id),command=document.createElement('div');command.className='bt-command';button.before(command);command.append(button);command.insertAdjacentHTML('beforeend',helpButton(key));
    }
    const nameLabel=q('bt-name').parentElement,nameHelp=document.createElement('div'),nameInput=q('bt-name');nameHelp.className='bt-field bt-record-name';nameLabel.before(nameHelp);nameHelp.innerHTML=`<div class="bt-label"><label for="bt-name">记录名称</label>${helpButton('record_name')}</div>`;nameHelp.append(nameInput);nameLabel.remove();
    addHelp(q('bt-output').querySelector('.bt-chart-legend'),'chart');
    addHelp(detailLine,'annual');
    addHelp(q('bt-output').querySelector('details:not(.bt-result-review details) > summary'),'provenance');
    const daily=q('bt-output').lastElementChild;addHelp(daily.querySelector('summary'),'daily');
    [...daily.querySelectorAll('th')].forEach((node,i)=>addHelp(node,['daily','equity','cash','units','drawdown'][i]));
    q('bt-compare').onclick=()=>{
      const rows=L.compareStrategies(snapshot),o=result.options;
      q('bt-comparison').innerHTML=`<p>绑定上方结果快照 · ${esc(result.audit.from)} 至 ${esc(result.audit.to)} · ${n(o.capital)} ${esc(result.currency)} · 入场 ${n(o.allocation)}% · 均线 ${o.fast}/${o.slow} · 突破/离场 ${o.breakout}/${o.exit_window} 日</p>${tableMarkup(['规则','费用后收益 %','最大回撤 %','持仓时间 %','已平仓','核对事项'],rows.map(row=>[row.label+(row.current?'（本次）':''),row.error?'未运行':n(row.metrics.net_return),row.error?'--':n(row.metrics.max_drawdown),row.error?'--':n(row.metrics.exposure),row.error?'--':row.metrics.closed_trades,row.error||row.notes.join('；')||'同数据描述，不代表未来有效']),['strategy','returns','drawdown','exposure','winrate','compare'])}<p class="quiet-note">表单改动不会改变此表；重新运行回测后才采用新参数。各规则预热要求不同，少成交或保持现金不能据此判定更优。</p>`;
      q('bt-comparison').hidden=false;
    };
    if(teaching)q('bt-name').value='教学案例（合成行情）';
    q('bt-save').onclick=async()=>{if(busy)return;const token=++serial,name=q('bt-name').value;setBusy(true);error('');try{const data=await api('/api/portfolio/research',{method:'POST',body:JSON.stringify({action:'save_backtest',name,snapshot})});if(token!==serial)return;q('bt-status').textContent='已保存完整输入快照 · '+data.observed_at;await archives();}catch(e){if(token===serial)error(e);}finally{if(token===serial)setBusy(false);}};
    if(snapshot.attention_model){q('bt-output').querySelector('h4').textContent=r.options.target+' · '+r.strategy_label+' · '+r.currency;q('bt-name').value=r.options.target+' '+r.strategy_label;}
    q('bt-export').onclick=()=>download('STock-backtest.json',JSON.stringify(snapshot),'application/json');
    q('bt-csv').onclick=()=>download('STock-backtest-trades.csv',B.csv(result),'text/csv;charset=utf-8');
    q('bt-prev').onclick=()=>{page--;trades();};q('bt-next').onclick=()=>{page++;trades();};trades();draw();
  }
  function trades(){const fills=result.fills,keys=['execution_date','signal_date','side','price','units','costs','realized','reason'];q('bt-trades').innerHTML=`<div class="bt-scroll" tabindex="0"><table><thead><tr>${['成交日','信号日','方向','价格','研究单位','费用与税','已实现盈亏','原因'].map((t,i)=>`<th scope="col">${t} ${helpButton(keys[i])}</th>`).join('')}</tr></thead><tbody>${fills.slice(page*30,page*30+30).map(r=>`<tr><td>${r.date}</td><td>${r.signal_date||'预设期末'}</td><td>${r.side==='buy'?'买入':'卖出'}</td><td>${n(r.price)}</td><td>${n(r.units)}</td><td>${n(r.fee)}</td><td>${r.pnl==null?'未适用':n(r.pnl)}</td><td>${esc(r.reason)}</td></tr>`).join('')||'<tr><td colspan="8">没有成交</td></tr>'}</tbody></table></div>`;q('bt-page').textContent=`${page+1} / ${Math.max(1,Math.ceil(fills.length/30))}`;q('bt-prev').disabled=page===0;q('bt-next').disabled=(page+1)*30>=fills.length;}
  function draw(){const canvas=q('bt-chart');if(!canvas||!visible())return;const width=canvas.clientWidth;if(!width)return;const height=240,dpr=window.devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);const p=B.plot(result,width,height);ctx.clearRect(0,0,width,height);for(const [key,color] of [['by','#bc681f'],['y','#285bb1']]){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();let started=false;for(const point of p.points){if(point[key]===null)continue;if(started)ctx.lineTo(point.x,point[key]);else{ctx.moveTo(point.x,point[key]);started=true;}}ctx.stroke();}const inspect=i=>{const r=result.curve[i];q('bt-inspect').textContent=`${r.date} · 策略 ${n(r.equity)} · 基准 ${n(r.benchmark)} · 回撤 ${n(r.drawdown)}%`;canvas.dataset.index=i;};canvas.onpointermove=e=>inspect(Math.max(0,Math.min(p.points.length-1,Math.round((e.offsetX-12)/(width-24)*(p.points.length-1)))));canvas.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();inspect(Math.max(0,Math.min(p.points.length-1,Number(canvas.dataset.index||0)+(e.key==='ArrowLeft'?-1:1))));}};inspect(result.curve.length-1);}
  function tableMarkup(headers,rows,keys){return `<div class="bt-scroll" tabindex="0"><table><thead><tr>${headers.map((t,i)=>`<th scope="col">${esc(t)} ${helpButton(keys[i])}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(t=>`<td>${esc(t)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function reportMarkup(){
    const d=diagnostics,r=d.risk;
    const keys=['volatility','sharpe','profit_factor','losing_streak','underwater','exposure'];
    return `<section class="bt-analysis" aria-labelledby="bt-risk-title"><h4 id="bt-risk-title">风险与收益质量 ${helpButton('risk')}</h4><div class="bt-summary">${[['年化波动率',unit(r.annual_volatility,'%')],['夏普比率',unit(r.sharpe)],['盈利因子',unit(r.profit_factor)],['最长连续亏损',r.max_losing_streak+' 笔'],['最长未回前高',r.underwater_bars+' 个交易日'],['持仓时间占比',unit(r.exposure,'%')]].map(([k,v],i)=>`<div><span>${k} ${helpButton(keys[i])}</span><strong>${v}</strong></div>`).join('')}</div><p>平均盈利 ${unit(r.average_win)} · 平均亏损 ${unit(r.average_loss)} ${esc(result.currency)} ${helpButton('average_trade')} · 费用与滑点占初始本金 ${n(r.cost_pct)}% ${helpButton('costs')}</p><details><summary>指标口径与样本限制</summary><ul class="bt-notes">${d.notes.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></details></section>
      <section class="bt-analysis" aria-labelledby="bt-compare-title"><div class="bt-section-heading"><h4 id="bt-compare-title">同条件策略对照 ${helpButton('compare')}</h4><div class="bt-command"><button id="bt-compare" class="secondary-button" type="button">${uiIcon('refresh-cw').outerHTML} 运行策略对照</button>${helpButton('compare')}</div></div><p>保持本次快照的行情、区间、资金与成本，只更换规则。按固定顺序列出，不自动挑选赢家。</p><div id="bt-comparison" hidden></div></section>
      <section class="bt-analysis" aria-labelledby="bt-period-title"><h4 id="bt-period-title">前后区间对照 ${helpButton('periods')}</h4><p>按观测交易日分为前70% / 后30%，固定当前参数，各自从模拟本金重启。前段行情仅用于指标预热，不承接仓位；不是独立样本外验证，不自动寻优。</p>${d.periods.length?tableMarkup(['区间','日期','样本','净收益 %','最大回撤 %','基准 %','已平仓'],d.periods.map(p=>[p.label,p.from+' 至 '+p.to,p.samples,n(p.net_return),n(p.max_drawdown),unit(p.benchmark_return),p.closed_trades]),['periods','periods','preflight','returns','drawdown','benchmark','winrate']):'<p>不足90条完整日线，暂无区间对照。</p>'}</section>
      <section class="bt-analysis" aria-labelledby="bt-stress-title"><h4 id="bt-stress-title">交易成本压力测试 ${helpButton('stress')}</h4><p>每档同时增加单边手续费和滑点，卖出税率不变，重跑完整策略。滑点可能改变停损触发，收益变化不只来自费用扣减。</p>${tableMarkup(['情景','单边手续费 / 滑点 bps','净收益 %','最大回撤 %'],d.stress.map(p=>[p.extra_bps?'各增加 '+p.extra_bps+' bps':'当前假设',p.fee_bps+' / '+p.slippage_bps,p.error||n(p.net_return),n(p.max_drawdown)]),['stress','costs','returns','drawdown'])}</section>
      <details id="bt-months"><summary>月度表现 · 所选区间内 ${helpButton('months')}</summary>${tableMarkup(['月份','覆盖交易日','策略 %','基准 %'],d.months.map(p=>[p.month,p.samples,n(p.net_return),unit(p.benchmark_return)]),['months','preflight','returns','benchmark'])}</details>`;
  }
  new ResizeObserver(()=>{if(result)draw();}).observe(panel);
  const tabs={workbench:['回测工作台',host],tutorial:['使用教程',document.getElementById('backtest-tutorial-panel')],rules:['规则与结果解读',document.getElementById('backtest-rules-panel')]};
  tabs.tutorial[1].innerHTML=L.tutorial;tabs.rules[1].innerHTML=L.rules;
  window.BacktestWorkspace={tab:'workbench',select(tab){
    if(!tabs[tab])tab='workbench';this.tab=tab;closeHelp();
    Object.entries(tabs).forEach(([key,[,node]])=>node.hidden=key!==tab);
    document.getElementById('backtest-current-title').textContent=tabs[tab][0];
    document.getElementById('backtest-refresh').hidden=tab!=='workbench';
    document.querySelectorAll('[data-backtest-tab]').forEach(button=>{const active=button.dataset.backtestTab===tab&&state.currentView==='backtest';button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
    if(tab==='workbench'&&result)requestAnimationFrame(draw);
  }};
  document.getElementById('backtest-refresh').onclick=load;
  document.querySelectorAll('[data-backtest-tab],[data-bt-guide]').forEach(button=>button.addEventListener('click',()=>{window.BacktestWorkspace.select(button.dataset.backtestTab||button.dataset.btGuide);setView('backtest');document.getElementById('backtest-current-title').scrollIntoView({block:'start'});}));
  document.querySelectorAll('[data-bt-demo]').forEach(button=>button.addEventListener('click',()=>{if(busy)return;window.BacktestWorkspace.select('workbench');setView('backtest',{refresh:false});error('');execute(L.demo());q('bt-status').textContent='教学案例已完成：合成行情、不修改当前参数或实际持仓。';q('bt-output').scrollIntoView({block:'start'});}));
})();
