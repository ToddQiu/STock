(function () {
  'use strict';
  const host = document.getElementById('short-term-workspace');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => value == null ? '--' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  const percent = value => value == null ? '--' : number(value) + '%';
  const rate = value => value == null ? '--' : percent(value * 100);
  const icon = name => uiIcon(name).outerHTML;
  let mode = 'forecast', data = null, sequence = 0;
  let researchState = null, researchHorizon = 30, recordData = null, researchLoading = false;
  let selectedStrategy='vwap_reversion', plotWindow='selected', showBands=false;
  const durationLabel = minutes => {
    const hours=Math.floor(minutes/60), rest=minutes%60;
    return (hours?hours+'小时':'')+(rest?rest+'分钟':'');
  };
  const resultDuration = result => result.duration_label || durationLabel(result.duration_minutes ?? Math.round(result.hours*60));
  const time = stamp => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(stamp*1000));
  host.innerHTML = `<div class="st-toolbar">
    <form id="st-form"><label>市场与代码<input id="st-target" value="US:NVDA" list="st-targets" maxlength="32" required aria-label="市场与代码" title="支持 NVDA、US:NVDA 或 CN:515000.SS"><datalist id="st-targets"></datalist></label><label id="st-duration-label">预测时长<select id="st-duration" aria-label="预测时长"><option value="1">1分钟</option><option value="5" selected>5分钟</option><option value="10">10分钟</option><option value="15">15分钟</option><option value="30">30分钟</option><option value="45">45分钟</option><option value="60">1小时</option><option value="90">1小时30分钟</option><option value="120">2小时</option><option value="1440">24小时</option><option value="2880">48小时</option><option value="4320">3天</option><option value="10080">7天</option></select></label><button type="button" class="secondary-button" id="st-custom">${icon('notebook-pen')} 自定义时长</button><button class="primary-button" id="st-run" type="submit">${icon('chart-no-axes-combined')} 生成预测图</button></form>
    <div class="st-commands"><button id="st-chart" type="button" class="secondary-button">${icon('chart-no-axes-combined')} 分时 / K线</button><button id="st-export" type="button" class="icon-button" title="导出本次研判与原始数据" aria-label="导出本次研判与原始数据" disabled>${icon('download')}</button></div>
  </div>
  <div class="workspace-tabs st-tabs" role="group" aria-label="研判模式"><button type="button" data-mode="forecast" aria-pressed="true" class="active">走势预测</button><button type="button" data-mode="strategies" aria-pressed="false">策略信号</button><button type="button" data-mode="daily" aria-pressed="false">历史验证</button><button type="button" data-mode="intraday" aria-pressed="false">量价观察</button><button type="button" data-mode="research" aria-pressed="false">自动研究</button></div>
  <p id="st-status" role="status">尚未读取行情</p><p id="st-error" role="alert" hidden></p>
  <div id="st-output"><div class="st-empty">暂无研判记录</div></div>
  <details class="st-method"><summary>口径与参数</summary><div id="st-method-content"></div></details>
  <dialog id="st-duration-dialog" aria-labelledby="st-duration-title"><form id="st-duration-form"><div class="st-result-title"><h2 id="st-duration-title">自定义预测时长</h2><button class="icon-button" id="st-duration-close" type="button" aria-label="关闭自定义预测时长">${icon('x')}</button></div><label for="st-custom-minutes">预测分钟数</label><input id="st-custom-minutes" type="number" inputmode="numeric" min="1" max="43200" step="1" value="5" required aria-describedby="st-duration-help"><p class="st-note" id="st-duration-help">按整分钟输入，例如8、45或90。可选1至43200分钟（30天）；休市时从下次常规开盘起算，时长包含期间的休市时间。</p><button class="primary-button" type="submit">应用时长</button></form></dialog>`;
  const q = id => host.querySelector('#' + id);
  q('st-duration-label').insertAdjacentHTML('beforebegin','<label id="st-model-label">预测模型<select id="st-model"><option value="auto">自动研究所选模型</option></select></label>');
  async function loadCatalog() {
    try {
      const catalog=await api('/api/short-term/catalog');
      q('st-model').innerHTML='<option value="auto">自动研究所选模型</option>'+Object.entries(catalog.models).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
    } catch(e){q('st-error').hidden=false;q('st-error').textContent='策略列表加载失败：'+e.message;}
  }
  function method() {
    q('st-method-content').innerHTML = mode === 'research'
      ? '<p>10个固定候选。每个候选只在它、现任模型和价格不变基准都可计算的相同分钟起点配对比较。缺量、缺基准不当成零信号；各行有效样本可能不同，因此选模型用训练误差相对配对基准的比值，不横比不同样本的原始误差。</p><p>按交易日先后70%训练、30%验证，固定起点不重叠抽样，可能漏掉中间短暂触发的信号。至少5个训练日/30样本、3个新验证日/12样本。验证误差比同起点原模型及基准都改善至少5%，至少5笔交易，费用后收益为正且超过原模型，平仓回撤不高于原模型0.25个百分点才升级。已用验证窗口不会重用。</p><p>模拟只做多：预测涨幅超过往返费用加0.1%才在下一分钟开盘进入，到期收盘退出。偏空信号仅观察，不模拟做空；参考失效位不是自动止损。5/15/30/60分钟结果不能证明24小时预测有效。后台需保持运行，节假日和半日市尚未核验。</p>'
      : mode === 'strategies'
      ? '<p>六种日内假设都使用已完成的1分钟K线，开盘区间、回踩及收缩窗口不读取未来价格。ATR14为最近14根真实波幅的简单均值；EMA以窗口首价初始化；VWAP为分钟典型价(H+L+C)/3按当日成交量加权的近似，不是逐笔精确VWAP。</p><p>触发不等于盈利。未触发为观望，资料不足为不可判断。相对强弱默认基准：美股SPY、A股沪深300、港股恒生指数、韩国KOSPI。参考失效位供研究，不自动下单；收益模拟只做多，到期平仓。</p>'
      : mode === 'forecast'
      ? '<p>完整1分钟K线。默认20分钟趋势；自动研究通过后使用该标的30分钟验证所选模型，实际模型在图上标明。趋势模型限制延续时长，均值回归限制回归幅度，均不无限外推。波动带仍使用最近20分钟波动×交易分钟数平方根×2，没有已验证的多日覆盖概率。</p><p>交易中从生成时刻起算，休市时从下次常规开盘起算，均使用最新完整分钟收盘作条件锚点，不预测开盘跳空。跨日、午休和断档重新累计；未来K线、成交量、消息及除权未知。常规时段排除周末和午休，尚未核验节假日及半日市。</p>'
      : mode === 'daily'
      ? '<p>候选：过去20分钟平均对数收益 × 50% × 5/10/20分钟。基准：收益为0。参数固定，区间用当时已到期的最近60至252个预测误差校准。</p><p>训练和目标窗口都必须连续，不跨午休、隔夜或缺失分钟。方向判定使用±0.1%窄幅区。MAE与区间宽度单位为百分点。未来20分钟估计仅适用于连续交易，不是跨休市预测。</p><p>这是分钟历史快照上的滚动检验，不是前向实盘验证；相邻样本会重叠。80%是区间标称水平，不是上涨胜率，也不能证明24小时或多日预测有效。</p>'
      : '<p>完整1分钟线；此前20根均量，不含当前根。放量≥1.5倍，缩量≤0.7倍；价格变动在±0.01%内记为窄幅。连续5根放量上涨或缩量下跌只记录首次事件。</p><p>只取常规时段；断档、零量、缺量及跨日重新预热。支撑/阻力取此前20根低/高点。时间是分钟起点，数据延迟未保证；目前为按需观察，不发送后台通知。</p>';
  }
  function targets() {
    const items = [...(state.dashboard?.portfolio?.positions || []), ...(state.dashboard?.watchlist || [])];
    q('st-targets').replaceChildren(...[...new Map(items.filter(p => ['US','CN','HK','KR'].includes(p.market)).map(p => [p.market + ':' + p.symbol, p])).entries()].map(([key,p]) => {
      const option = el('option', '', p.name || key); option.value = key; return option;
    }));
  }
  function target() {
    let value = q('st-target').value.trim().toUpperCase();
    if(!value.includes(':')) {
      const market=/\.(SS|SZ)$/.test(value)?'CN':/\.HK$/.test(value)?'HK':/\.(KS|KQ)$/.test(value)?'KR':/^[A-Z][A-Z0-9.-]*$/.test(value)?'US':null;
      if(market)value=market+':'+value;
    }
    if (!/^(US|CN|HK|KR):[A-Z0-9.^=_-]{1,24}$/.test(value)) throw Error('请输入市场与代码，例如 US:NVDA 或 CN:515000.SS');
    const [market, symbol] = value.split(':');
    return {market, symbol, name: symbol};
  }
  function invalidate() {
    sequence++; data = null; researchState = recordData = null; q('st-export').disabled = true; q('st-run').disabled = false;
    q('st-output').replaceChildren(el('div', 'st-empty', '暂无当前条件的研判结果'));
    q('st-status').textContent = '条件已更改'; q('st-error').hidden = true;
  }
  function table(headers, rows) {
    return `<div class="st-table-scroll" tabindex="0" role="region" aria-label="研判数据表"><table><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => '<tr>' + row.map(cell => `<td>${esc(cell)}</td>`).join('') + '</tr>').join('')}</tbody></table></div>`;
  }
  function chartMarkup() {
    return '<canvas id="st-canvas" role="img" aria-label="历史1分钟K线及成交量；未来区域为空心预测K线情景，实体表示分钟起止估计，不含预测影线和成交量"></canvas><p id="st-chart-readout" class="st-note">1分钟K线 · 成交量 · 东八区时间</p>';
  }
  function unavailable(result) {
    return `<p class="st-empty">${esc(result.reason || '暂无完整的常规时段分钟K线')}</p>${contextView(result.context)}${result.history?.length ? chartMarkup() : ''}`;
  }
  function sourceLink(url, label) {
    return /^https?:\/\//i.test(url || '') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label);
  }
  function contextView(context) {
    if(!context)return '<p class="st-note">此记录未保存新闻与财报依据。</p>';
    const news=context.news||{}, fundamentals=context.fundamentals||{};
    const article=item=>`<article><div>${sourceLink(item.url,item.title)}</div><p class="st-note">${esc(item.source||'来源未注明')} · ${esc(formatDate(item.published_at))}${item.after_price?' · 最新价格之后发布':''} · ${item.direction>0?'偏多依据':item.direction<0?'偏空依据':'待确认 / 无方向依据'}</p><p>${esc(item.impact_reason||'尚无可核对的方向解释')}</p></article>`;
    const newsItems=news.items||[], metrics=Object.values(fundamentals.metrics||{});
    return `<details class="st-detail st-context"><summary>新闻与财报依据 · ${esc(news.summary || '暂无可用新闻')}</summary>
      <p class="st-note">截至 ${esc(formatDate(context.as_of))} · 读取已采集资料；消息用于综合判断，尚未校准为价格涨跌幅。</p>
      ${newsItems.slice(0,6).map(article).join('')}
      ${newsItems.length>6?`<details class="st-detail"><summary>其余${newsItems.length-6}条留档消息</summary>${newsItems.slice(6).map(article).join('')}</details>`:''}
      <p class="st-note">${esc(fundamentals.summary||'暂无可用财报快照')}</p>
      ${metrics.length?`<p class="st-note">${sourceLink(fundamentals.source_url,fundamentals.source||'年度财报来源')} · 采集 ${esc(formatDate(fundamentals.collected_at))}</p>${table(['年度指标','数值','同比','报告期末','披露日期'],metrics.map(item=>[item.label||'--',number(item.value)+' '+(item.unit||''),percent(item.growth_pct),item.end,item.filed]))}`:''}
    </details>`;
  }
  function plotPath(result) {
    const end=mode==='forecast'&&plotWindow==='short'?result.projection_basis?.horizon_timestamp:null;
    return end?result.path.filter(p=>p.timestamp<=end):result.path;
  }
  function predictionCandles(result, path) {
    const candles=[], cap=result.projection_basis?.horizon_timestamp??result.end_timestamp;
    for(let i=1;i<path.length;i++){
      const start=path[i-1], end=path[i];
      if(end.timestamp>cap||end.timestamp-start.timestamp!==60||end.session_active===false)continue;
      if(start.trading_minutes!=null&&end.trading_minutes!=null&&Math.abs(end.trading_minutes-start.trading_minutes-1)>1e-8)continue;
      candles.push({timestamp:start.timestamp,end_timestamp:end.timestamp,open:start.price,close:end.price});
    }
    return candles;
  }
  function projection(result) {
    if(!result.available)return unavailable(result);
    const last=result.path.at(-1);
    const marks=result.path.filter((p,i)=>i>0&&(i===result.path.length-1||(p.timestamp-result.start_timestamp)%86400===0));
    const basis=result.projection_basis, assessment=result.context?.assessment;
    return `<div class="st-result-title"><h2>${esc(data.symbol)} · ${result.starts_next_session?'休市研判':'短线研判'} · ${esc(resultDuration(result))}</h2><span>${esc(data.currency||'报价单位')} · 东八区时间</span></div>
      <div class="st-projection-summary"><strong>${esc(result.summary)}</strong><span>分钟收盘 ${number(result.anchor_price)} · ${time(result.history.at(-1).timestamp)}</span></div>
      ${assessment?`<div class="st-assessment"><strong>综合判断 · ${esc(assessment.label)}</strong><span>${esc(assessment.detail)}</span></div>`:''}
      <div class="st-chart-heading"><span>1分钟K线 / ${esc(result.model_name || '技术走势')}</span>${basis?`<label>图表范围 <select id="st-plot-window"><option value="selected" ${plotWindow==='selected'?'selected':''}>完整${esc(resultDuration(result))}</option><option value="short" ${plotWindow==='short'?'selected':''}>技术延续段 · 前${number(basis.effective_horizon_minutes)}交易分钟</option></select></label>`:''}<span><i class="st-legend candle"></i>预测K线（情景）</span><label class="st-band-toggle"><input id="st-bands" type="checkbox" ${showBands?'checked':''}>显示波动范围（未校准）</label></div>
      ${chartMarkup()}
      <p class="st-note">空心实体＝每分钟起止价格估计；分钟内高低点未知，不画预测影线及成交量。</p>
      <div id="st-plot-range" class="st-forecast-range"></div>
      ${basis?`<details class="st-detail"><summary>为什么波动带这么宽？</summary><p>用最近${basis.volatility_sample_count}个分钟收益的标准差 ${number(basis.minute_std_pct)}%，乘以2，再乘以未来交易分钟数的平方根。完整窗口约${number(basis.trading_minutes)}分钟，末端上下界相对锚点为 ${percent(basis.full_lower_pct)} ~ ${percent(basis.full_upper_pct)}。</p><p>这是波动率不变的外推假设，不是预计价格会走出的上下震荡，也不是已验证的95%概率区间。图表默认显示所选完整时段，也可聚焦前${number(basis.effective_horizon_minutes)}个交易分钟的技术延续段；之后的虚线只表示模型停止延续趋势。</p></details>`:''}
      ${contextView(result.context)}
      <h3>判断依据</h3><div class="st-reasons">${result.reasons.map(r=>`<section><h4>${esc(r.title)}</h4><p>${esc(r.detail)}</p></section>`).join('')}</div>
      <p class="st-note">趋势最多延续前${number(result.settings?.trend_minutes || 20)}个交易分钟；24小时及多日路径尚未通过相应期限验证。未来K线与成交量未知。</p>
      <details class="st-detail"><summary>完整${esc(resultDuration(result))}节点与数据边界 · 末端技术参考 ${number(last.price)}</summary>${table(['东八区时间','技术参考','外推下界','外推上界'],marks.map(p=>[time(p.timestamp),number(p.price),number(p.low),number(p.high)]))}<ul class="st-warnings">${result.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`;
  }
  function daily(result) {
    if (!result.available) return unavailable(result);
    return `<div class="st-result-title"><h2>${esc(data.symbol)} · 分钟历史验证</h2><span>${result.sample_count} 根分钟K线</span></div>
      ${chartMarkup()}
      <p class="st-note">5 / 10 / 20分钟连续交易收益 · 未证明具有稳定预测优势</p>
      ${table(['期限','点估计','50%区间','80%区间','证据状态'], result.evaluations.map(e => {const p=result.path.find(p=>p.horizon===e.horizon);return [e.horizon+'分钟',percent(p?.point),`${percent(p?.low50)} ~ ${percent(p?.high50)}`,`${percent(p?.low80)} ~ ${percent(p?.high80)}`,e.evidence];}))}
      <h3>历史滚动验证</h3>
      ${table(['期限','误差 MAE','零收益基准 MAE','误差改善','方向命中 / 历史多数基准','80%覆盖 / 宽度','样本 / 非重叠'], result.evaluations.map(e=> {const m=e.metrics;return [e.horizon+'分钟',number(m.mae),number(m.baseline_mae),rate(m.skill),rate(m.direction_hit)+' / '+rate(m.majority_hit),rate(m.coverage80)+' / '+number(m.width80),m.count+' / '+e.nonoverlap.count];}))}
      <details class="st-detail"><summary>区间质量与分段稳定性</summary>
      ${table(['期限','区间','覆盖率 / 基准','宽度 / 基准','区间分数 / 基准'],result.evaluations.flatMap(e=>[50,80].map(k=>[e.horizon+'分钟',k+'%',rate(e.metrics['coverage'+k])+' / '+rate(e.metrics['baseline_coverage'+k]),number(e.metrics['width'+k])+' / '+number(e.metrics['baseline_width'+k]),number(e.metrics['interval_score'+k])+' / '+number(e.metrics['baseline_interval_score'+k])])))}
      ${table(['期限','测试段（东八区）','条数','误差改善','80%覆盖','历史上涨比例'],result.evaluations.flatMap(e=>e.blocks.map(m=>[e.horizon+'分钟',m.from?time(Date.parse(m.from)/1000)+' ~ '+time(Date.parse(m.to)/1000):'--',m.count,rate(m.skill),rate(m.coverage80),rate(m.up_frequency)])))}
      <p class="st-note">测试期按时间等分三段；区间分数同时惩罚过宽与漏覆盖，越低越好。未进行参数挑选或收益回测。</p></details>`;
  }
  function minutes(result) {
    if (!result.available) return unavailable(result);
    const r=result.latest;
    return `<div class="st-result-title"><h2>${esc(data.symbol)} · ${esc(r.state)}</h2><span>${esc(result.status)}</span></div>
      <dl class="st-metrics">${[['分钟收盘',number(r.close)],['分钟涨跌',percent(r.change_pct)],['相对成交量',r.ratio==null?'--':number(r.ratio)+' 倍'],['连续形态',r.count+' 根'],['关键位置',r.level||'预热中']].map(([a,b])=>`<div><dt>${esc(a)}</dt><dd>${esc(b)}</dd></div>`).join('')}</dl>
      <div class="st-chart-heading"><span>1分钟K线与成交量</span><span>${esc(result.timezone)} · ${esc(r.local_time)}</span></div>${chartMarkup()}
      <h3>连续形态记录 <small>${result.events.length} 条</small></h3>
      ${result.events.length?table(['当地时间','事件','价格','量比','关键位置'],result.events.slice(-10).reverse().map(r=>[r.local_time,r.event,number(r.close),number(r.ratio),r.level])):'<p class="st-note">当前时段无连续5根形态</p>'}
      <details class="st-detail" open><summary>最近20分钟依据</summary>${table(['当地时间','收盘','涨跌','成交量 / 前20均量','量比','分类','连续'],result.history.slice(-20).reverse().map(r=>[r.local_time,number(r.close),percent(r.change_pct),number(r.volume)+' / '+number(r.average_volume),number(r.ratio),r.state,r.count]))}</details>`;
  }
  function strategyView(result) {
    const all=Object.values(result.signals), s=result.signals[selectedStrategy]||all[0];
    const stamp=result.history.at(-1)?.timestamp;
    return `<div class="st-result-title"><h2>${esc(data.symbol)} · 策略信号</h2><span>行情截至 ${stamp?time(stamp):'--'} · 基准 ${esc(data.benchmark.symbol)}</span></div>
      ${data.benchmark.error?`<p class="st-warning">基准：${esc(data.benchmark.error)}</p>`:''}
      <div class="st-table-scroll"><table><thead><tr><th>策略</th><th>当前状态</th><th>判断</th><th>参考失效位</th></tr></thead><tbody>${all.map(s=>`<tr><td><button class="st-strategy-link" type="button" data-strategy="${s.id}" aria-pressed="${s.id===selectedStrategy}">${esc(s.name)}</button></td><td>${!s.available?'不可判断':s.direction>0?'偏多触发':s.direction<0?'偏空触发':'观望'}</td><td class="st-signal-reason">${esc(s.reason)}</td><td>${number(s.invalidation)}</td></tr>`).join('')}</tbody></table></div>
      <section class="st-strategy-detail"><div class="st-chart-heading"><h3>${esc(s.name)} · 原理与依据</h3><button class="secondary-button" id="st-use-strategy" type="button">${icon('chart-no-axes-combined')} 用此策略预测</button></div>
      <p>${esc(s.principle)}</p><p class="st-note">${esc(s.rule)}</p>
      <dl class="st-strategy-facts">${Object.entries(s.facts).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${typeof v==='boolean'?(v?'是':'否'):number(v)}</dd></div>`).join('')}</dl>
      <p class="st-warning">${esc(s.risk)}</p></section>
      ${result.history.length?chartMarkup():'<p class="st-empty">暂无完整分钟行情</p>'}`;
  }
  const post = (path, payload) => api('/api/short-term/'+path,{method:'POST',body:JSON.stringify(payload)});
  function researchMarkup() {
    const s=researchState, item=target(), active=s.active[String(researchHorizon)];
    const c=s.iteration?.comparisons.find(c=>c.horizon===researchHorizon);
    const recordStatus={pending:'待到期 / 待补数据',evaluated:'已对照',missing:'缺失实际行情'};
    const metricRows=c?Object.entries(c.scores).map(([id,m])=>[s.models[id]+(id===active.model?' · 当前':''),number(m.train.mae),number(m.test.mae),number(m.test_baseline?.mae),rate(m.test.direction_hit),percent(m.test.net_return_pct),percent(m.test.max_drawdown_pct),m.test.count+' / '+m.test.trades]):[];
    q('st-output').innerHTML=`<div class="st-research-controls">
      <label><input type="checkbox" data-research-setting="enabled" ${s.settings.enabled?'checked':''}>后台研究</label>
      <label><input type="checkbox" data-research-setting="auto_promote" ${s.settings.auto_promote?'checked':''}>验证通过后自动切换</label>
      <label><input type="checkbox" id="st-watch" ${s.target?.enabled?'checked':''}>采集 ${esc(item.symbol)}</label>
      ${s.targets.length?`<label>跟踪标的 <select id="st-followed"><option value="">选择代码</option>${s.targets.map(t=>`<option value="${esc(t.market+':'+t.symbol)}" ${t.market===item.market&&t.symbol===item.symbol?'selected':''}>${esc(t.market+':'+t.symbol)}${t.enabled?'':'（暂停）'}</option>`).join('')}</select></label>`:''}
      <button class="secondary-button" type="button" id="st-research-run" ${s.busy?'disabled':''}>${icon('refresh-cw')} ${s.busy?'采集与验证中':'立即采集并验证'}</button>
    </div>
    <dl class="st-metrics">${[['完整分钟',number(s.data.bars)],['交易日',s.data.days],['数据截至',s.data.last?time(s.data.last):'尚未采集'],['分钟缺口',s.data.gaps],['缺量分钟',s.data.missing_volume]].map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    ${s.error||s.target?.error?`<p class="st-warning" role="alert">${esc(s.error||s.target.error)}</p>`:''}
    <div class="st-chart-heading"><h3>同条件模型比较</h3><label>验证期限 <select id="st-research-horizon">${s.horizons.map(h=>`<option value="${h}" ${h===researchHorizon?'selected':''}>${h}分钟</option>`).join('')}</select></label></div>
    <p class="st-note">当前 ${esc(s.models[active.model])} · v${active.version} · ${esc(c?.reason||'等待分钟数据，尚未完成验证')}</p>
    ${c?table(['候选','训练误差','新验证误差','同起点基准误差','方向命中','模拟净收益','平仓最大回撤','验证样本 / 交易'],metricRows):'<p class="st-empty">尚无比较结果</p>'}
    <p class="st-note">相对强弱基准 ${esc(s.benchmark?.symbol||'--')} · ${esc(s.benchmark?.error||'各候选与基准在相同有效起点配对；缺资料不计为观望信号。')}</p>
    ${c?`<p class="st-note">训练 ${c.train_days.length} 日 · 新验证 ${c.test_days.length} 日 · 单边手续费 ${number(s.iteration.policy.fee_bps)} bp / 滑点 ${number(s.iteration.policy.slippage_bps)} bp · 误差单位：百分点</p>`:''}
    <h3>预测留档与实际对照 <small>最近30条</small></h3>
    <div class="st-table-scroll"><table><thead><tr><th>生成 / 到期（东八区）</th><th>模型</th><th>类型</th><th>状态</th><th>末端误差</th><th></th></tr></thead><tbody>${s.records.map(r=>`<tr><td>${time(r.issued)} / ${time(r.due)}</td><td>${esc(s.models[r.model]||r.model||'--')}</td><td>${r.kind==='manual'?'手动预测':esc(r.kind.replace('auto-','自动 '))+'分钟'}</td><td>${recordStatus[r.status]}</td><td>${percent(r.outcome?.error_pct)}</td><td><button type="button" class="icon-button" data-record="${r.id}" title="预测与实际叠加" aria-label="预测与实际叠加">${icon('chart-no-axes-combined')}</button></td></tr>`).join('')||'<tr><td colspan="6">尚无记录；生成预测图时留档，盘中自动记录各期限预测。</td></tr>'}</tbody></table></div>
    <div id="st-record-output"></div>
    <details class="st-detail"><summary>迭代记录与参数</summary>
      <form id="st-cost-form" class="st-research-controls"><label>单边手续费（bp）<input type="number" name="fee_bps" min="0" max="100" step="0.1" value="${s.settings.fee_bps}" required></label><label>单边滑点（bp）<input type="number" name="slippage_bps" min="0" max="100" step="0.1" value="${s.settings.slippage_bps}" required></label><button class="icon-button" title="保存费用" aria-label="保存费用">${icon('save')}</button></form>
      <p class="st-note">费用变更后，已升级模型恢复默认并等待新样本验证，不复用旧测试结果。默认模型也不代表已验证有效。</p>
      ${table(['时间','期限','变更','原因'],s.changes.map(r=>[time(r.created),r.horizon+'分钟',s.models[r.previous]+' → '+s.models[r.current]+' · v'+r.version,r.reason]))}
      <button id="st-rollback" class="secondary-button" type="button" ${s.busy||!s.changes.find(r=>r.horizon===researchHorizon)?.iteration_id?'disabled':''}>${icon('undo-2')} 回滚${researchHorizon}分钟模型</button>
      ${table(['验证时间',researchHorizon+'分钟结论'],s.runs.map(r=>[time(Date.parse(r.created_at)/1000),r.results.find(c=>c.horizon===researchHorizon)?.reason||'--']))}
      <p class="st-note">已跟踪：${s.targets.map(t=>esc(t.market+':'+t.symbol)+(t.enabled?'':'（暂停）')).join('、')||'无'}</p>
    </details>`;
    if(recordData)renderRecord();
  }
  function renderRecord() {
    data={...recordData.forecast,forecast_id:recordData.id,outcome:recordData.outcome,actual:recordData.outcome.trajectory||recordData.outcome.actual};
    const r=data.result;
    q('st-record-output').innerHTML=`<div class="st-chart-heading"><h3>已留档预测 · ${esc(data.symbol)}</h3><span><i class="st-legend candle"></i>原预测情景 <i class="st-legend actual"></i>实际</span><label class="st-band-toggle"><input id="st-bands" type="checkbox" ${showBands?'checked':''}>显示波动范围（未校准）</label></div><p class="st-note">${time(r.start_timestamp)} 至 ${time(r.end_timestamp)} · 空心实体为分钟起止估计，高低点未知；已到期节点缺数据时留空</p>${chartMarkup()}`;
    if(r.candidate_prices)q('st-record-output').insertAdjacentHTML('beforeend',table(['同起点前向候选','当时预测价','到期误差','当时状态'],Object.entries(r.candidate_prices).map(([m,p])=>[researchState.models[m],number(p),percent(recordData.outcome.candidate_errors?.[m]),r.strategy_signals?.[m]?.reason||'固定价格模型'])));
    q('st-record-output').insertAdjacentHTML('beforeend',contextView(r.context));
    q('st-export').disabled=false;draw();
  }
  async function loadResearch() {
    if(researchLoading)return;
    const id=sequence;researchLoading=true;
    try {
      const item=target(), response=await api('/api/short-term/research?'+new URLSearchParams(item));
      if(id!==sequence||mode!=='research')return;
      researchState=response;researchMarkup();q('st-error').hidden=true;
      q('st-status').textContent=`${item.market}:${item.symbol} · ${response.busy?'采集与验证中':response.settings.enabled?'后台研究已开启':'后台研究已暂停'} · 本机留档`;
    } catch(e){if(id===sequence){q('st-error').hidden=false;q('st-error').textContent=e.message;}}
    finally {researchLoading=false;}
  }
  async function researchAction(work) {
    const id=sequence;
    try {await work();if(id===sequence)await loadResearch();}
    catch(e){if(id===sequence){q('st-error').hidden=false;q('st-error').textContent=e.message;}}
  }
  host.addEventListener('change',event=>{
    const e=event.target;
    if(e.id==='st-bands'){showBands=e.checked;draw();return;}
    if(e.id==='st-plot-window'){plotWindow=e.value;draw();return;}
    if(e.id==='st-followed'&&e.value){q('st-target').value=e.value;invalidate();loadResearch();return;}
    if(e.id==='st-research-horizon'){researchHorizon=Number(e.value);researchMarkup();}
    if(e.id==='st-watch')researchAction(()=>post('research/watch',{...target(),enabled:e.checked}));
    if(e.dataset.researchSetting)researchAction(()=>post('research/settings',{[e.dataset.researchSetting]:e.checked}));
  });
  host.addEventListener('submit',event=>{
    if(event.target.id!=='st-cost-form')return;event.preventDefault();
    const form=event.target;researchAction(()=>post('research/settings',{fee_bps:Number(form.elements.fee_bps.value),slippage_bps:Number(form.elements.slippage_bps.value)}));
  });
  host.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.dataset.strategy){selectedStrategy=b.dataset.strategy;q('st-output').innerHTML=strategyView(data.result);draw();}
    if(b.id==='st-use-strategy'){host.querySelector('[data-mode="forecast"]').click();q('st-model').value=selectedStrategy;run();}
    if(b.id==='st-research-run')researchAction(()=>post('research/run',target()));
    if(b.id==='st-rollback')researchAction(()=>post('research/rollback',{...target(),horizon:researchHorizon}));
    if(b.dataset.record){const id=sequence;researchAction(async()=>{const response=await api('/api/short-term/record?id='+encodeURIComponent(b.dataset.record));if(id===sequence)recordData=response;});}
  });
  setInterval(()=>{if(mode==='research'&&host.classList.contains('active')&&!q('st-cost-form')?.contains(document.activeElement))loadResearch();},15000);
  function draw() {
    const canvas=q('st-canvas'); if(!canvas || !data?.result.history?.length || !host.classList.contains('active')) return;
    const width=canvas.clientWidth, height=canvas.clientHeight; if(!width||!height)return;
    const scale=window.devicePixelRatio||1; canvas.width=Math.round(width*scale); canvas.height=Math.round(height*scale);
    const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.clearRect(0,0,width,height);
    const r=data.result, future=data.mode==='forecast' && r.available;
    const path=future?plotPath(r):[], endStamp=future?path.at(-1).timestamp:null;
    const predictions=future?predictionCandles(r,path):[];
    const actual=(data.actual||[]).filter(p=>!future||p.timestamp<=endStamp);
    const left=58,right=12,top=24,priceBottom=Math.round(height*.67),volumeTop=priceBottom+26,volumeBottom=height-26;
    const split=future?left+(width-left-right)*.52:width-right;
    const history=r.history.slice(-Math.min(120,Math.max(12,Math.floor((split-left)/5))));
    // One empty slot marks each gap; no invented candle or zero-volume filler.
    const slots=[];let slot=0;
    history.forEach((p,i)=>{if(i&&p.timestamp-history[i-1].timestamp!==60)slot++;slots.push(slot++);});
    const step=(split-left)/(slot+1), x=i=>left+(slots[i]+.7)*step;
    const body=Math.max(1,Math.min(8,step*.68));
    const values=[...history.flatMap(p=>[p.low,p.high]),...path.flatMap(p=>showBands?[p.low??p.price,p.high??p.price]:[p.price]),...actual.filter(p=>p.price!=null).map(p=>p.price)];
    let low=Math.min(...values),high=Math.max(...values);const pad=(high-low)*.08||1;low-=pad;high+=pad;
    const y=v=>top+(high-v)/(high-low)*(priceBottom-top);
    ctx.font='12px sans-serif';ctx.fillStyle='#616977';ctx.strokeStyle='#e0e4eb';ctx.lineWidth=1;
    for(let i=0;i<4;i++){const v=low+(high-low)*i/3;ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.fillText(number(v),0,y(v)+4,left-6);}
    if(future){
      const fx=t=>split+(t-r.start_timestamp)/(endStamp-r.start_timestamp||1)*(width-right-split);
      ctx.fillStyle='#edf3ff';ctx.fillRect(split,top,width-right-split,priceBottom-top);
      if(showBands){
        ctx.fillStyle='#d0deef';ctx.beginPath();path.forEach((p,i)=>i?ctx.lineTo(fx(p.timestamp),y(p.low??p.price)):ctx.moveTo(fx(p.timestamp),y(p.low??p.price)));
        [...path].reverse().forEach(p=>ctx.lineTo(fx(p.timestamp),y(p.high??p.price)));ctx.closePath();ctx.fill();
      }
      const cap=r.projection_basis?.horizon_timestamp??endStamp;
      predictions.forEach(p=>{
        const center=fx((p.timestamp+p.end_timestamp)/2), width=Math.max(1,Math.min(14,(fx(p.end_timestamp)-fx(p.timestamp))*.64));
        ctx.strokeStyle=p.close>p.open?'#b9384c':p.close<p.open?'#167459':'#6d7785';ctx.lineWidth=1.5;
        ctx.strokeRect(center-width/2,Math.min(y(p.open),y(p.close)),width,Math.max(1,Math.abs(y(p.open)-y(p.close))));
      });
      ctx.strokeStyle='#285bb1';ctx.lineWidth=2.5;
      if(!predictions.length){ctx.beginPath();path.filter(p=>p.timestamp<=cap).forEach((p,i)=>i?ctx.lineTo(fx(p.timestamp),y(p.price)):ctx.moveTo(fx(p.timestamp),y(p.price)));ctx.stroke();}
      if(endStamp>cap){ctx.setLineDash([5,4]);ctx.beginPath();path.filter(p=>p.timestamp>=cap).forEach((p,i)=>i?ctx.lineTo(fx(p.timestamp),y(p.price)):ctx.moveTo(fx(p.timestamp),y(p.price)));ctx.stroke();ctx.setLineDash([]);}
      if(actual.length){
        ctx.strokeStyle='#af6316';ctx.lineWidth=2;ctx.beginPath();let connected=false;
        actual.forEach(p=>{if(p.price==null){connected=false;return;}if(connected)ctx.lineTo(fx(p.timestamp),y(p.price));else ctx.moveTo(fx(p.timestamp),y(p.price));connected=true;});ctx.stroke();
      }
      const end=path.at(-1);
      ctx.fillStyle='#285bb1';
      if(!predictions.length){ctx.beginPath();ctx.arc(fx(end.timestamp)-3,y(end.price),3,0,Math.PI*2);ctx.fill();}
      ctx.textAlign='right';ctx.fillText(number(end.price),width-right-5,Math.max(top+12,y(end.price)-10),width-right-split-8);ctx.textAlign='left';
      ctx.fillText(predictions.length?'预测K线（情景） · 1分钟':'预测线 · 缺少逐分钟估计',split+4,14,width-right-split-8);
      if(q('st-plot-range'))q('st-plot-range').textContent=`${r.starts_next_session?'下次常规开盘起 · ':''}${time(r.start_timestamp)} 至 ${time(endStamp)} · 技术参考 ${number(end.price)} · 波动外推 ${number(end.low)} ~ ${number(end.high)}${endStamp>cap?' · 虚线段不再延续趋势':''}`;
    }
    const volumes=history.filter(p=>Number.isFinite(p.volume)&&p.volume>=0),maxVolume=Math.max(0,...volumes.map(p=>p.volume));
    ctx.strokeStyle='#e0e4eb';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,priceBottom+8);ctx.lineTo(width-right,priceBottom+8);ctx.moveTo(left,volumeBottom);ctx.lineTo(width-right,volumeBottom);ctx.stroke();
    history.forEach((p,i)=>{
      const color=p.close>p.open?'#b9384c':p.close<p.open?'#167459':'#6d7785';
      ctx.strokeStyle=ctx.fillStyle=color;ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(x(i),y(p.high));ctx.lineTo(x(i),y(p.low));ctx.stroke();
      ctx.fillRect(x(i)-body/2,Math.min(y(p.open),y(p.close)),body,Math.max(1,Math.abs(y(p.open)-y(p.close))));
      if(Number.isFinite(p.volume)&&p.volume>0&&maxVolume){
        const h=p.volume/maxVolume*(volumeBottom-volumeTop);ctx.fillRect(x(i)-body/2,volumeBottom-h,body,h);
      }
    });
    const compact=new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1});
    ctx.fillStyle='#616977';ctx.fillText('1分钟K线',left,14);ctx.fillText('成交量',left,volumeTop-6);
    if(volumes.length)ctx.fillText(compact.format(maxVolume),0,volumeTop+8);
    const missing=history.length-volumes.length;
    if(missing)ctx.fillText(volumes.length?missing+'根缺量':'成交量未提供',left+52,volumeTop-6);
    ctx.fillText(time(history[0].timestamp).slice(6),left,height-5);ctx.textAlign='right';
    ctx.fillText(time(future?endStamp:history.at(-1).timestamp),width-right,height-5);ctx.textAlign='left';
    const readout=p=>`${time(p.timestamp)} · 开 ${number(p.open)} 高 ${number(p.high)} 低 ${number(p.low)} 收 ${number(p.close)} · 量 ${number(p.volume)}`;
    q('st-chart-readout').textContent=readout(history.at(-1));
    canvas.onpointermove=event=>{
      const px=event.clientX-canvas.getBoundingClientRect().left;
      if(future&&px>=split&&px<=width-right){
        const stamp=r.start_timestamp+(px-split)/(width-right-split)*(endStamp-r.start_timestamp);
        const candle=predictions.find(p=>stamp>=p.timestamp&&stamp<p.end_timestamp);
        if(candle){q('st-chart-readout').textContent=`${time(candle.timestamp)}–${time(candle.end_timestamp).slice(6)} · 预测K线（情景） · 起点估计 ${number(candle.open)} / 终点估计 ${number(candle.close)} · 高低点、成交量未知`;return;}
        const p=path.reduce((best,item)=>Math.abs(item.timestamp-stamp)<Math.abs(best.timestamp-stamp)?item:best);
        q('st-chart-readout').textContent=`${time(p.timestamp)} · 技术参考 ${number(p.price)} · 外推下界 ${number(p.low)} / 上界 ${number(p.high)} · 非实际K线`;return;
      }
      if(px<left||px>split){q('st-chart-readout').textContent=readout(history.at(-1));return;}
      const i=history.reduce((best,_,index)=>Math.abs(x(index)-px)<Math.abs(x(best)-px)?index:best,0);
      q('st-chart-readout').textContent=readout(history[i]);
    };
    canvas.onpointerleave=()=>{q('st-chart-readout').textContent=readout(history.at(-1));};
  }
  async function run(event) {
    if(mode==='research'){event?.preventDefault();return loadResearch();}
    event?.preventDefault();const id=++sequence;q('st-error').hidden=true;q('st-export').disabled=true;data=null;plotWindow='selected';
    q('st-run').disabled=true;q('st-status').textContent='正在读取行情并计算…';q('st-output').replaceChildren();
    try {
      const item=target();const response=mode==='forecast'?await post('predict',{...item,minutes:Number(q('st-duration').value),model_id:q('st-model').value}):mode==='strategies'?await post('strategies',item):await api('/api/short-term?'+new URLSearchParams({...item,mode}));if(id!==sequence)return;
      data=response;q('st-output').innerHTML=mode==='forecast'?projection(data.result):mode==='daily'?daily(data.result):mode==='strategies'?strategyView(data.result):minutes(data.result);
      q('st-status').textContent=`${data.source || '来源未注明'} · 1分钟K线 · 检查 ${formatDate(data.generated_at)}${data.forecast_id?' · 已留档，后台对照实际走势':''}${data.retained_from_cache?' · 本次更新失败，保留历史缓存':''}`;
      q('st-export').disabled=false;draw();
    } catch(error){if(id===sequence){q('st-error').textContent=error.message;q('st-error').hidden=false;q('st-status').textContent='未生成本次结果';}}
    finally {if(id===sequence)q('st-run').disabled=false;}
  }
  q('st-form').addEventListener('submit',run);q('st-target').addEventListener('input',invalidate);q('st-target').addEventListener('focus',targets);
  q('st-duration').addEventListener('change',invalidate);
  q('st-model').addEventListener('change',invalidate);
  q('st-custom').addEventListener('click',()=>{q('st-custom-minutes').value=q('st-duration').value;q('st-duration-dialog').showModal();q('st-custom-minutes').focus();});
  q('st-duration-close').addEventListener('click',()=>q('st-duration-dialog').close());
  q('st-duration-form').addEventListener('submit',event=>{
    event.preventDefault();const minutes=Number(q('st-custom-minutes').value);if(!Number.isInteger(minutes)||minutes<1||minutes>43200)return;
    const value=String(minutes);if(![...q('st-duration').options].some(o=>o.value===value)){
      q('st-duration').querySelector('[data-custom]')?.remove();const option=el('option','',durationLabel(minutes));option.value=value;option.dataset.custom='1';q('st-duration').append(option);
    }
    q('st-duration').value=value;q('st-duration-dialog').close();invalidate();
  });
  host.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
    if(mode===button.dataset.mode)return; mode=button.dataset.mode;invalidate();method();
    q('st-duration-label').hidden=q('st-custom').hidden=q('st-model-label').hidden=mode!=='forecast';
    q('st-run').replaceChildren(uiIcon(mode==='forecast'?'chart-no-axes-combined':'refresh-cw'),document.createTextNode(mode==='forecast'?' 生成预测图':' 更新研判'));
    host.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',b===button?'true':'false');});
    if(mode==='research')loadResearch();
  }));
  q('st-chart').addEventListener('click',()=>{try{openPortfolioChart(target(),'intraday');}catch(e){q('st-error').hidden=false;q('st-error').textContent=e.message;}});
  q('st-export').addEventListener('click',()=>{
    if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const link=el('a');link.href=url;link.download=`STock-${data.market}-${data.symbol}-${data.mode}-${data.generated_at.slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  loadCatalog();
  new ResizeObserver(draw).observe(host);method();
})();
