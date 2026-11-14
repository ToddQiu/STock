(function(){
  'use strict';
  const C=window.STockValuation,host=document.getElementById('research-valuation-panel');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=v=>v==null?'未适用':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:3});
  const icon=name=>uiIcon(name).outerHTML;
  const defaults={basis:'fcff',flow_mode:'growth',cash_timing:'current',multiple_type:'pe',years:5,growth:5,discount:10,terminal_growth:2,claims:0};
  const fields={
    price:['参考股价 / 买入价','按每股输入，币种与盈利、现金流相同。估值相对价差不是上涨概率。'],
    basis:['现金流归属','FCFF属于全部资本，配WACC、扣债加现金；FCFE属于股东，配Ke，不再扣债。',[['fcff','企业现金流 FCFF'],['fcfe','股东现金流 FCFE']]],
    flow_mode:['现金流预测','稳定盈利可用统一增长率；亏损转盈或资本开支波动较大时，直接逐年填写。',[['growth','按增长率预测'],['explicit','逐年填写现金流']]],
    cash_timing:['基期现金流时点','当期CF0需要乘下一期增长因子；下一期CF1已经包含该增长，公式不同。',[['current','当期 / 正常化 CF0'],['next','下一期预测 CF1']]],
    cashflow:['基期现金流（百万）','必须与现金流归属一致。统一使用所选币种的百万单位；经营现金流减资本开支不自动等于FCFF。'],
    years:['预测年数','显性预测1至30年，现金流按每年末发生。'],growth:['显性期年增长率 %','未来每年相同的增长假设，不是已经实现的增长，也不是一致预期。'],
    discount:['必要回报率 / 折现率 %','FCFF用WACC，FCFE、股息和股票退出价值用股权成本Ke。它是要求的门槛，不是预测回报。'],
    terminal_growth:['永续增长率 %','显性期以后持续增长的假设，必须低于折现率。长期增长对估值影响很大。'],
    shares:['稀释后股数（百万股）','与股价、EPS使用同一股份类别。100百万股填100；不要把普通股与ADR数量混用。'],
    debt:['有息负债（百万）','企业价值桥接到股权价值时扣除的债务。租赁负债是否包含须与现金流/倍数口径一致。'],
    cash:['非经营现金（百万）','只加回未包含在经营价值中的现金等资产，避免把维持运营所需现金重复估值。'],
    claims:['其他优先索偿（百万）','例如优先股、少数股东权益；不存在时明确填0，避免把并非普通股股东的价值分给普通股。'],
    multiple_type:['相对估值口径','PE、PB直接计算每股股权价值；PS以收入推股权市值；EV/EBITDA先求企业价值再扣净债务。',[['pe','市盈率 PE'],['pb','市净率 PB'],['ps','市销率 P/S'],['ev_ebitda','EV / EBITDA']]],
    measure:['基准指标','PE填年度EPS；PB填每股账面净资产；PS填年度收入（百万）；EV/EBITDA填年度EBITDA（百万）。须统一年度、TTM或预期口径。'],
    multiple:['采用倍数','由同业或自己的研究假设确定，不预设“多少倍就是便宜”。负盈利不使用正PE。'],
    eps:['基期年度 EPS','采用可持续、已注明期间的每股盈利；与退出PE的口径一致。'],exit_pe:['退出时 PE','未来第N年末卖出价格 = 当年EPS × 假设退出PE；不是当前PE。'],
    dividend:['每年每股现金股息','假定每个年末相同金额，不是股息率或派息率。无股息填0；未计个人股息税。'],
    rf:['无风险利率 %','需匹配现金流币种与估值期限。不是直接使用任意国家的政策利率。'],beta:['股权 Beta','相对市场组合的风险暴露估计；并不是公司风险的完整度量。'],erp:['股权风险溢价 %','市场要求的股权回报超过无风险利率的部分，注意估计日期与市场。'],
    kd:['税前债务成本 %','企业当前融资的边际成本估计，不能直接拿账面利息率替代而不核实。'],tax:['可用利息税盾税率 %','只有利息可抵扣时才有税盾，不能使用税盾时填0。'],debt_weight:['债务市场价值权重 %','D/(D+E)，权益权重自动为其补数；不含其他资本类别。']
  };
  const methodFields={dcf:['basis','flow_mode','price','cashflow','years','growth','discount','terminal_growth','shares','debt','cash','claims'],ddm:['price','cashflow','years','growth','discount','terminal_growth'],reverse:['basis','cash_timing','price','cashflow','years','discount','terminal_growth','shares','debt','cash','claims'],multiples:['multiple_type','price','measure','multiple','shares','debt','cash','claims'],return:['price','eps','years','growth','exit_pe','dividend','discount'],capital:['rf','beta','erp','kd','tax','debt_weight']};
  const descriptions={dcf:'把未来经营或股东现金流折回今天，适合能够明确现金流路径的企业。',ddm:'以未来每股现金股息估值，适合可持续派息的企业。',multiples:'用同口径的盈利、净资产、收入或EBITDA乘以研究倍数。',reverse:'从当前股价反求现金流增长要求，承接原工作簿的“价格里包含什么预期”。',return:'按年度股息和期末卖出价计算IRR，并反推达到回报门槛所需的盈利增长。',capital:'估计股权必要回报率Ke及WACC，不计算未来实际收益，也不直接产生每股估值。'};
  const storage='stock.valuation.draft.v1';
  let model='dcf',drafts={},meta={currency:'USD'},active=null,result=null,calculated=null,busy=false,loaded=false,offset=0,total=0,records=[],loadSequence=0,recordContext=0,renderedMultipleType='pe';
  function help(label,text){return `<details class="val-help"><summary aria-label="${esc(label)}说明" title="${esc(label)}说明">${icon('message-circle')}</summary><p>${esc(text)}</p></details>`;}
  const input=(name,label,type='text',value='',extra='')=>`<label class="val-meta-field">${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
  host.innerHTML=`<div class="valuation-workspace"><div class="val-toolbar"><label>估值方法<select id="val-method">${Object.entries(C.methods).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><div class="val-commands"><button id="val-example" class="secondary-button" type="button">教学数据</button><button id="val-new" class="icon-button" type="button" title="新建空白研究" aria-label="新建空白研究">${icon('plus')}</button><button id="val-import" class="icon-button" type="button" title="导入估值快照" aria-label="导入估值快照">${icon('arrow-up')}</button></div></div><p id="val-purpose"></p>
    <div class="val-workspace-grid"><form id="val-form"><fieldset><legend>研究资料</legend><div class="val-fields val-meta">
    ${input('name','研究名称','text','','maxlength="120" placeholder="例如：苹果，中性情景"')}${input('target','市场与代码','text','','maxlength="32" placeholder="US:AAPL" list="pr-holdings"')}
    <label class="val-meta-field">统一计价币种<select name="currency">${['USD','CNY','HKD','KRW'].map(c=>`<option>${c}</option>`).join('')}</select></label>${input('data_as_of','财务资料日期','date')}${input('price_as_of','参考价格日期','date')}${input('source_url','资料来源链接','url','','maxlength="2048"')}
    </div></fieldset><fieldset><legend>模型假设</legend><p class="val-unit-note">总金额统一填“百万”，股数填“百万股”；股价、EPS、股息按每股。所有比例填百分数。</p><div id="val-fields" class="val-fields"></div><div id="val-flows" class="val-fields" hidden></div></fieldset>
    <label class="val-meta-field">假设依据、反证与失效条件<textarea name="notes" rows="3" maxlength="4000"></textarea></label><div class="val-commands"><button class="primary-button" type="submit">计算估值</button><button id="val-save" class="secondary-button" type="button" disabled>${icon('save')} 保存研究</button><button id="val-export" class="icon-button" type="button" disabled title="导出本次假设与计算结果" aria-label="导出本次假设与计算结果">${icon('download')}</button></div><p id="val-error" role="alert"></p><p id="val-status" role="status"></p></form>
    <section id="val-report" aria-label="估值结果"><div id="val-empty"><h3>估值取决于假设</h3><p>左侧填写现金流、增长、资本成本与来源；没有数据时不会自动补出估值。教学数据只用于核对计算。</p><dl><dt>必要回报率</dt><dd>你要求达到的回报门槛，用于折现。</dd><dt>内部回报率 IRR</dt><dd>根据买入价格和假设未来现金流反求的回报率。</dd><dt>隐含增长率</dt><dd>让模型价值等于当前股价所需的增长，并不是市场实际预测。</dd></dl></div><div id="val-result" hidden></div></section></div>
    <section class="val-archive"><div class="val-section-heading"><h3>估值研究记录</h3><button id="val-refresh" type="button" class="icon-button" aria-label="刷新估值研究记录" title="刷新估值研究记录">${icon('refresh-cw')}</button></div><p id="val-archive-status" role="status"></p><div id="val-records"></div><div class="val-commands"><button id="val-prev" class="icon-button" type="button" aria-label="上一页记录">${icon('arrow-left')}</button><span id="val-page"></span><button id="val-next" class="icon-button" type="button" aria-label="下一页记录">${icon('arrow-right')}</button></div></section>
    <details class="val-methodology"><summary>方法、原表口径与使用边界</summary><p>原表是反向现金流估值，不是IRR。若现金流是当期CF0，戈登模型包含(1+g)；只有把分子视作下一期CF1时，g = r - CF1/V才是精确式。本模块提供两种时点和两阶段求解，不把隐含增速或某个倍数阈值当作自动买卖结论。</p><p>负现金流阶段可逐年填入DCF，但永续期须转为正现金流。金融企业通常更适合股权、股息或净资产框架。所有来源与假设由研究者核对；没有自动下载完整财报，也没有把用户输入称作分析师一致预期。</p><p>方法参考：<a href="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/valquestions.htm" target="_blank" rel="noopener noreferrer">NYU Damodaran 估值框架</a>；<a href="https://support.microsoft.com/en-us/office/irr-function-64925eaa-9988-495b-b290-3ad0c163c1bc" target="_blank" rel="noopener noreferrer">Microsoft IRR 等间隔现金流说明</a>。本模块IRR仅支持一次买入、后续非负年度股息和正退出价值，不支持多次追加投资或不规则日期XIRR。</p></details></div>`;
  const q=id=>host.querySelector('#'+id),form=q('val-form');
  function values(){const x={...(drafts[model]||defaults)};for(const e of q('val-fields').querySelectorAll('[name]'))x[e.name]=e.value;const flows=[...q('val-flows').querySelectorAll('input')];if(flows.length)x.flows=flows.map(e=>e.value);return x;}
  function metadata(){return Object.fromEntries(['name','target','currency','data_as_of','price_as_of','source_url','notes'].map(k=>[k,form.elements[k].value.trim()]));}
  function status(text){q('val-status').textContent=text;}
  function dirty(){calculated=null;result=null;q('val-save').disabled=true;q('val-export').disabled=true;q('val-result').hidden=true;q('val-empty').hidden=false;status('输入已修改，计算后更新结果。');}
  function persist(){try{localStorage.setItem(storage,JSON.stringify({model,meta,drafts}));}catch{status('浏览器草稿未能保存；已存入研究库的记录不受影响，请导出快照保留本次输入。');}}
  function labelFor(key,x){if(key==='cashflow'&&model==='ddm')return '基期每股现金股息';if(key==='discount')return model==='dcf'||model==='reverse'?(x.basis==='fcff'?'WACC 折现率 %':'股权成本 Ke %'):'股权必要回报率 Ke %';if(key==='measure')return {pe:'年度每股收益 EPS',pb:'每股账面净资产',ps:'年度收入（百万）',ev_ebitda:'年度 EBITDA（百万）'}[x.multiple_type];return fields[key][0];}
  function renderFields(){
    const x={...defaults,...drafts[model]};renderedMultipleType=x.multiple_type;q('val-method').value=model;q('val-purpose').textContent=descriptions[model];
    q('val-fields').innerHTML=methodFields[model].map(key=>{
      if(['debt','cash','claims'].includes(key)&&!(model==='multiples'?x.multiple_type==='ev_ebitda':x.basis==='fcff'))return '';
      if(key==='shares'&&model==='multiples'&&['pe','pb'].includes(x.multiple_type))return '';
      if(['growth','cashflow'].includes(key)&&model==='dcf'&&x.flow_mode==='explicit')return '';
      const label=labelFor(key,x),definition=key==='cashflow'&&model==='ddm'?'基期每股现金股息，不是公司总派息，也不是股息率百分数。':fields[key][1],opts=fields[key][2];
      return `<div class="val-field"><div class="val-field-head"><label for="val-${key}">${esc(label)}</label>${help(label,definition)}</div>${opts?`<select id="val-${key}" name="${key}">${opts.map(([v,t])=>`<option value="${v}" ${x[key]===v?'selected':''}>${t}</option>`).join('')}</select>`:`<input id="val-${key}" name="${key}" type="number" step="${key==='years'?'1':'any'}" value="${esc(x[key]??'')}" ${key==='years'?'min="1" max="30"':''}>`}</div>`;
    }).join('');
    const count=Math.max(1,Math.min(30,Math.trunc(Number(x.years)||5)));q('val-flows').hidden=!(model==='dcf'&&x.flow_mode==='explicit');
    q('val-flows').innerHTML=q('val-flows').hidden?'':Array.from({length:count},(_,i)=>input('cash_year_'+i,'第 '+(i+1)+' 年现金流（百万）','number',x.flows?.[i]??'','step="any"')).join('');
    for(const [key,value] of Object.entries(meta))if(form.elements[key])form.elements[key].value=value;
  }
  function table(headers,rows){return `<div class="val-table-scroll" tabindex="0"><table><thead><tr>${headers.map(t=>`<th scope="col">${esc(t)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(t=>`<td>${esc(t)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function calculate(){
    q('val-error').textContent='';drafts[model]=values();meta=metadata();
    try{
      if(meta.source_url){const source=new URL(meta.source_url);if(!['http:','https:'].includes(source.protocol)||source.username||source.password)throw Error('来源须为不含账号密码的HTTP(S)链接');}
      result=C.calculate(model,drafts[model]);calculated={engine:C.VERSION,model,meta:{...meta},inputs:JSON.parse(JSON.stringify(drafts[model]))};
      q('val-empty').hidden=true;q('val-result').hidden=false;
      const s=result.sensitivity;
      q('val-result').innerHTML=`<div class="val-result-heading"><h3>${esc(meta.name||C.methods[model])}</h3><p>${esc(meta.target||'未指定标的')} · ${esc(meta.currency)} · ${esc(C.methods[model])}</p></div><p class="val-provenance">财务资料 ${esc(meta.data_as_of||'未注明日期')} · 价格 ${esc(meta.price_as_of||'未注明日期')} · ${meta.source_url?`<a href="${esc(meta.source_url)}" target="_blank" rel="noopener noreferrer">核对来源</a>`:'尚无来源链接'}</p><dl class="val-metrics">${result.metrics.map(m=>`<div><dt>${esc(m.label)} ${m.help?help(m.label,m.help):''}</dt><dd>${fmt(m.value)}<small>${esc(m.unit)}</small></dd></div>`).join('')}</dl><p class="val-formula">${esc(result.formula)}</p><ul class="val-warnings">${result.warnings.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>${result.rows.length?`<details open><summary>逐年现金流与现值 ${help('逐年现金流','第N年现金流在年末发生。DCF表格不包含终值，终值现值另列；投资回报表最后一年含卖出收入。DDM与投资回报表为每股单位，DCF与反推为百万单位。')}</summary>${table(['年度','现金流','折现系数','现值'],result.rows.map(row=>[row.year,fmt(row.cash),fmt(row.discount_factor),fmt(row.pv)]))}</details>`:''}${s?`<details open><summary>敏感度：每股估值</summary><p>行：折现率；列：永续增长率。其他输入不变，未适用表示违反模型条件。</p>${table(['折现率 / 永续增速',...s.growths.map(g=>fmt(g)+'%')],s.rates.map((d,i)=>[fmt(d)+'%',...s.values[i].map(fmt)]))}</details>`:''}<p class="val-result-note">条件估值，不是收益承诺。资料与假设改变后，应重新计算并保留版本。</p>`;
      q('val-save').disabled=busy;q('val-export').disabled=false;status(active?'已重算。保存将新增研究版本，不覆盖旧版。':'计算完成；尚未保存到研究库。');persist();
    }catch(e){dirty();q('val-error').textContent=e.message;status('请修正上述输入。');}
  }
  function restore(s){if(s?.engine!==C.VERSION)throw Error('估值快照版本不受支持');C.snapshot(s.model,s.meta,s.inputs);model=s.model;meta={...s.meta};drafts[model]={...s.inputs};renderFields();calculate();}
  async function load(){
    const sequence=++loadSequence;
    q('val-archive-status').textContent='正在读取研究库…';
    try{const data=await api('/api/research/valuations?offset='+offset);if(sequence!==loadSequence)return;records=data.items;total=data.total;loaded=true;
      q('val-records').innerHTML=records.length?records.map((row,i)=>`<button type="button" class="val-record" data-record="${i}"><strong>${esc(row.snapshot.meta.name)}</strong><span>${esc(row.snapshot.meta.target)} · ${esc(C.methods[row.snapshot.model])}</span><small>v${row.version} · ${esc(row.observed_at.slice(0,16).replace('T',' '))} UTC</small>${icon('arrow-right')}</button>`).join(''):'<p>尚无估值研究记录。</p>';
      q('val-archive-status').textContent=`${total} 份研究 · 保存到本项目 SQLite 研究库`;
      q('val-page').textContent=`${Math.floor(offset/30)+1} / ${Math.max(1,Math.ceil(total/30))}`;q('val-prev').disabled=offset===0;q('val-next').disabled=offset+30>=total;
    }catch(e){if(sequence===loadSequence)q('val-archive-status').textContent='研究库暂不可用：'+e.message+'。计算仍可使用；可导出快照保留。';}
  }
  form.addEventListener('submit',e=>{e.preventDefault();calculate();});
  form.addEventListener('input',()=>{drafts[model]=values();meta=metadata();dirty();persist();});
  form.addEventListener('change',e=>{drafts[model]=values();meta=metadata();dirty();
    if(e.target.name==='multiple_type'&&drafts[model].multiple_type!==renderedMultipleType){
      drafts[model].measure='';drafts[model].multiple='';status('估值口径已切换，请重新填写基准指标与倍数。');
    }
    if(['basis','flow_mode','multiple_type','years'].includes(e.target.name))renderFields();persist();});
  q('val-method').onchange=()=>{drafts[model]=values();meta=metadata();model=q('val-method').value;active=null;++recordContext;dirty();renderFields();persist();};
  host.addEventListener('toggle',e=>{if(e.target.matches('.val-help')&&e.target.open)host.querySelectorAll('.val-help[open]').forEach(d=>{if(d!==e.target)d.open=false;});},true);
  host.addEventListener('click',e=>{const row=e.target.closest('[data-record]');if(row){try{const selected=records[Number(row.dataset.record)];restore(selected.snapshot);active=selected;++recordContext;status('已重算研究 v'+active.version+'；旧版假设与来源时间保留。');}catch(error){q('val-error').textContent=error.message;}}});
  q('val-save').onclick=async()=>{
    if(busy||!calculated)return;
    const context=recordContext;
    try{const s=C.snapshot(calculated.model,calculated.meta,calculated.inputs);busy=true;q('val-save').disabled=true;const payload={snapshot:s,base_version:active?.version||0};if(active)payload.id=active.id;
      const saved=await api('/api/research/valuations',{method:'POST',body:JSON.stringify(payload)}),unchanged=JSON.stringify(calculated)===JSON.stringify(s);
      // Edits keep their draft, but the next save must build on the version just committed.
      if(context===recordContext){active=saved;status('已保存研究 v'+saved.version+'；历史版本保留。'+(unchanged?'':'保存期间输入已改变，当前输入尚未保存。'));}
      offset=0;await load();
    }catch(e){q('val-error').textContent=e.message;status('未保存成功，输入仍保留；可导出快照。');}finally{busy=false;q('val-save').disabled=!calculated;}
  };
  q('val-export').onclick=()=>{if(!calculated)return;try{const s=C.snapshot(calculated.model,calculated.meta,calculated.inputs),url=URL.createObjectURL(new Blob([JSON.stringify({snapshot:s,result},null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='STock-valuation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){q('val-error').textContent=e.message;}};
  const importFile=document.createElement('input');importFile.id='val-import-file';importFile.type='file';importFile.accept='.json';importFile.hidden=true;host.append(importFile);
  importFile.onchange=async()=>{try{const f=importFile.files[0];if(!f)return;if(f.size>100000)throw Error('估值快照不能超过100KB');const data=JSON.parse(await f.text());restore(data.snapshot||data);active=null;++recordContext;status('已根据输入重算导入文件；来源与假设仍需自行核验。');}catch(e){q('val-error').textContent=e.message;}finally{importFile.value='';}};
  q('val-import').onclick=()=>importFile.click();
  q('val-example').onclick=()=>{active=null;++recordContext;meta={name:'教学示例（非真实公司数据）',target:'US:DEMO',currency:'USD',data_as_of:'2025-01-01',price_as_of:'2025-01-01',source_url:'',notes:'仅用于核对模型，不是实际公司或市场预测。'};drafts[model]={...defaults,price:100,cashflow:model==='ddm'?3:100,shares:10,debt:200,cash:100,claims:0,measure:5,multiple:20,eps:5,exit_pe:20,dividend:2,rf:4,beta:1.1,erp:5,kd:6,tax:25,debt_weight:20};renderFields();calculate();};
  q('val-new').onclick=()=>{if(!confirm('新建空白研究？当前未保存输入会被清空，已保存记录不受影响。'))return;active=null;++recordContext;drafts[model]={...defaults};meta={currency:meta.currency||'USD',name:'',target:'',data_as_of:'',price_as_of:'',source_url:'',notes:''};dirty();renderFields();q('val-error').textContent='';persist();};
  q('val-refresh').onclick=load;q('val-prev').onclick=()=>{offset=Math.max(0,offset-30);load();};q('val-next').onclick=()=>{offset+=30;load();};
  try{const text=localStorage.getItem(storage);if(text){if(text.length>100000)throw Error('草稿过大');const s=JSON.parse(text);if(!Object.hasOwn(C.methods,s.model)||!s.meta||!s.drafts)throw Error('草稿格式不正确');model=s.model;meta=s.meta;drafts=s.drafts;}}catch(e){status('估值草稿未恢复：'+e.message+'；原存储未主动删除。');}
  renderFields();new MutationObserver(()=>{if(!host.hidden&&!loaded)load();}).observe(host,{attributes:true,attributeFilter:['hidden']});
})();
