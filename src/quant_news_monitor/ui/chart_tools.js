/* Shared chart subpanels; no independent price or indicator polling. */
(() => {
  'use strict';
  const node = id => document.getElementById(id);
  const currencies = {CN:'CNY', HK:'HKD', US:'USD', KR:'KRW'};
  const valid = value => typeof value === 'number' && Number.isFinite(value);
  const number = value => valid(value) ? value.toFixed(2) : '--';
  let subject = null, generation = 0, mode = 'both';
  const storageKey = item => `stock.chart.comparison.${item.market}:${item.symbol}`;
  function clear() {
    generation++;
    for (const id of ['chart-macd','chart-rsi']) {
      const canvas = node(id);
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    }
    node('chart-premium-result').replaceChildren();
  }
  function syncMode() {
    node('chart-premium').hidden = !subject || !currencies[subject.market] || state.portfolioChartMode !== 'daily';
    node('chart-macd').hidden = !['both','macd'].includes(mode);
    node('chart-rsi').hidden = !['both','rsi'].includes(mode);
  }
  function open(item) {
    subject = {...item}; generation++; node('chart-premium').open = false;
    const form = node('chart-premium-form'); form.reset();
    form.elements.market.value = item.market === 'HK' ? 'CN' : 'HK';
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey(item)) || 'null');
      if (saved) for (const field of ['kind','market','symbol','ratio','fx','fx_date']) {
        if (saved[field] != null) form.elements[field].value = saved[field];
      }
    } catch (_) { /* Settings are optional; quotation data is never stored here. */ }
    const adr = (state.dashboard?.watchlist || []).find(w => w.market === 'KR' && normalizeStockSymbol(w.symbol) === '000660')?.adr_premium;
    const existing = node('chart-adr-existing'); existing.replaceChildren();
    if ((item.market === 'KR' && normalizeStockSymbol(item.symbol) === '000660') || (item.market === 'US' && item.symbol === 'SKHY')) {
      existing.append(el('strong', '', '海力士 ADR 参考'), renderAdrPremium(item.adr_premium || adr));
    }
    updateFxLabel(); syncMode();
  }
  function draw(main, data, hovered) {
    syncMode();
    const geometry = main._portfolioChartGeometry;
    if (!geometry) return;
    const series = data.technical?.oscillators || {}, {entries, offset, left, right, xAt} = geometry;
    for (const kind of ['macd','rsi']) {
      const canvas = node('chart-' + kind); if (canvas.hidden) continue;
      const width = Math.round(canvas.clientWidth), height = 116;
      if (!width) continue;
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
      const ctx = canvas.getContext('2d'); ctx.setTransform(ratio,0,0,ratio,0,0);
      ctx.fillStyle = '#fff'; ctx.fillRect(0,0,width,height);
      ctx.font = `11px ${getComputedStyle(canvas).fontFamily}`;
      const keys = kind === 'rsi' ? ['rsi14'] : ['dif','dea','histogram'];
      const values = keys.map(key => (series[key] || []).slice(offset, offset + entries.length));
      const i = hovered ?? entries.length - 1;
      const labels = kind === 'rsi' ? ['RSI14'] : ['DIF','DEA','柱'];
      ctx.fillStyle = '#34445a'; ctx.textAlign = 'left';
      ctx.fillText(keys.map((_, n) => `${labels[n]} ${number(values[n][i])}`).join('   '), left, 14);
      canvas.setAttribute('aria-label', `${kind === 'rsi' ? 'RSI14' : 'MACD(12,26,9)，柱=DIF-DEA，不乘2'}，${portfolioChartDateLabel(entries[i], true)}，${labels.map((l,n) => `${l} ${number(values[n][i])}`).join('，')}`);
      const available = values.flat().filter(valid);
      if (!available.length) {ctx.fillText('样本不足，暂不绘制', left, 65); continue;}
      const top = 28, bottom = 106, min = kind === 'rsi' ? 0 : Math.min(0,...available), max = kind === 'rsi' ? 100 : Math.max(0,...available);
      const padding = kind === 'rsi' ? 0 : Math.max((max - min) * .1, .0001);
      const yAt = v => bottom - (v - min + padding) / Math.max(max - min + padding * 2, .0001) * (bottom - top);
      ctx.textAlign = 'right';
      for (const level of kind === 'rsi' ? [30,50,70] : [min,0,max]) {
        ctx.strokeStyle = '#e0e5ec'; ctx.beginPath(); ctx.moveTo(left,yAt(level)); ctx.lineTo(right,yAt(level)); ctx.stroke();
        ctx.fillStyle = '#5a6470'; ctx.fillText(number(level),left-8,yAt(level)+3);
      }
      if (kind === 'macd') values[2].forEach((v,index) => {
        if (!valid(v)) return;
        ctx.fillStyle = v >= 0 ? '#b9384c' : '#167459';
        const w = Math.max(1, Math.min(7, (right-left)/entries.length*.6));
        ctx.fillRect(xAt(index)-w/2,Math.min(yAt(0),yAt(v)),w,Math.max(1,Math.abs(yAt(v)-yAt(0))));
      });
      values.slice(0,kind==='rsi'?1:2).forEach((line,index) => {
        ctx.beginPath(); let drawing = false;
        line.forEach((v,i) => {if (!valid(v)) {drawing=false;return;} if (drawing) ctx.lineTo(xAt(i),yAt(v)); else ctx.moveTo(xAt(i),yAt(v)); drawing=true;});
        ctx.strokeStyle = index === 0 ? '#235bb2' : '#a86b12'; ctx.lineWidth = 1.3; ctx.stroke();
      });
      if (hovered != null && entries[hovered]) {
        ctx.save(); ctx.strokeStyle='#65758c'; ctx.setLineDash([3,3]); ctx.beginPath();
        ctx.moveTo(xAt(hovered),top); ctx.lineTo(xAt(hovered),bottom); ctx.stroke(); ctx.restore();
      }
    }
  }
  function updateFxLabel() {
    const fields = node('chart-premium-form').elements;
    const base = currencies[subject?.market], reference = currencies[fields.market.value];
    node('chart-premium-fx-label').textContent = `1 ${reference || '对应币种'} = 多少 ${base || '当前币种'}`;
    if (base === reference) fields.fx.value = '1';
    fields.fx.readOnly = base === reference;
    fields.ratio.readOnly = fields.kind.value === 'AH';
    if (fields.kind.value === 'AH') fields.ratio.value = '1';
  }
  function calculatePremium(base, reference, shares, fx) {
    if (![base,reference,shares,fx].every(v => valid(v) && v > 0)) throw Error('报价、对应份数和汇率须为有效正数');
    const equivalent = reference * shares * fx, pct = (base / equivalent - 1) * 100;
    if (!Number.isFinite(equivalent) || !Number.isFinite(pct)) throw Error('输入数值超出计算范围');
    return {equivalent, pct};
  }
  async function compare(event) {
    event.preventDefault();
    const form = node('chart-premium-form'), fields = form.elements, output = node('chart-premium-result');
    const current = {...subject}, chart = state.portfolioChartData, token = ++generation;
    const settings = Object.fromEntries(new FormData(form));
    if (!chart || chart.mode !== 'daily' || state.portfolioChartLoading) {output.textContent='请先等待日 K 行情加载完成。';return;}
    if (!fields.confirmed.checked) {output.textContent='请先核实发行人与对应份数。';return;}
    if (settings.kind === 'AH' && !['CN:HK','HK:CN'].includes(current.market + ':' + settings.market)) {output.textContent='A/H 对比须为 A 股与港股。';return;}
    const today = new Date().toLocaleDateString('en-CA');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(settings.fx_date) || settings.fx_date > today) {output.textContent='请填写有效的汇率日期，不可使用未来日期。';return;}
    const button = form.querySelector('[type=submit]'); button.disabled=true; output.textContent='正在加载对应证券日线…';
    try {
      const identity = await api('/api/securities/resolve?' + new URLSearchParams({market:settings.market,symbol:settings.symbol}));
      if (identity.market === current.market && identity.symbol === current.symbol) throw Error('请选择另一上市证券，不能与自身比较');
      const other = await api('/api/portfolio/chart?' + new URLSearchParams({market:identity.market,symbol:identity.symbol,mode:'daily',period:'1m'}));
      if (token !== generation || state.portfolioChartData !== chart || !node('portfolio-chart-dialog').open) return;
      const ownCurrency = currencies[current.market], otherCurrency = currencies[identity.market];
      if (chart.currency !== ownCurrency || other.currency !== otherCurrency) throw Error('行情币种与证券市场不一致，暂不计算');
      const mainBar = chart.candles?.at(-1), refBar = other.candles?.at(-1);
      if (!mainBar?.date || !refBar?.date) throw Error('缺少可核对时间的日线报价');
      const rate = ownCurrency === otherCurrency ? 1 : Number(settings.fx);
      const shares = settings.kind === 'AH' ? 1 : Number(settings.ratio);
      const result = calculatePremium(mainBar.close, refBar.close, shares, rate);
      const title = `${current.symbol} 相对 ${identity.symbol} ${result.pct >= 0 ? '溢价' : '折价'} ${Math.abs(result.pct).toFixed(2)}%`;
      output.replaceChildren(el('strong','',title));
      output.append(el('p','',`${current.name || current.symbol}：${mainBar.close} ${ownCurrency} · ${mainBar.date}；${identity.name}：${refBar.close} ${otherCurrency} · ${refBar.date}`));
      output.append(el('p','',`每 1 份当前证券对应 ${shares} 份对比证券；1 ${otherCurrency} = ${rate} ${ownCurrency}（手动汇率 ${settings.fx_date}）。折合参考价 ${result.equivalent.toFixed(4)} ${ownCurrency}。`));
      output.append(el('p','quiet-note',`计算：当前价 ÷（对比价 × 对应份数 × 汇率）− 1。${mainBar.date !== refBar.date ? '两地报价日期不同；' : ''}${chart.retained_from_cache || other.retained_from_cache ? '包含历史缓存；' : ''}各市场收盘时刻不同，仅为价格差异，不代表可套利收益。`));
      for (const [label,data] of [['当前证券行情',chart],['对应证券行情',other]]) {
        if (/^https:\/\//.test(data.source_url || '')) {const link=el('a','',label);link.href=data.source_url;link.target='_blank';link.rel='noopener noreferrer';output.append(link);}
      }
      try {localStorage.setItem(storageKey(current),JSON.stringify({...settings,symbol:identity.symbol,confirmed:undefined}));}
      catch (_) {output.append(el('p','quiet-note','本机无法记住对比设置，本次计算仍有效。'));}
    } catch (error) {if(token===generation) output.textContent=error.message;}
    finally {button.disabled=false;}
  }
  function bind() {
    node('chart-indicator-mode').addEventListener('change', event => {
      mode=event.target.value;syncMode();
      if(state.portfolioChartData) drawPortfolioChart(node('portfolio-chart-canvas'),state.portfolioChartData);
    });
    for (const id of ['chart-macd','chart-rsi']) {
      const canvas=node(id);
      canvas.addEventListener('pointermove', event => {
        const main=node('portfolio-chart-canvas'),g=main._portfolioChartGeometry;
        if(!g?.entries?.length)return;
        const x=event.clientX-canvas.getBoundingClientRect().left;
        const i=clamp(Math.round((x-g.xAt(0))/Math.max(1,g.xAt(g.entries.length-1)-g.xAt(0))*(g.entries.length-1)),0,g.entries.length-1);
        state.portfolioChartHoverIndex=i;drawPortfolioChart(main,g.data,i);
      });
      canvas.addEventListener('pointerleave',()=>{state.portfolioChartHoverIndex=null;if(state.portfolioChartData)drawPortfolioChart(node('portfolio-chart-canvas'),state.portfolioChartData);});
    }
    node('chart-premium-form').addEventListener('submit',compare);
    node('chart-premium-form').addEventListener('input',()=>{generation++;node('chart-premium-result').replaceChildren();updateFxLabel();});
    node('portfolio-chart-dialog').addEventListener('close',()=>{generation++;});
  }
  document.addEventListener('DOMContentLoaded',bind);
  window.StockChartTools={open,clear,draw,syncMode,calculatePremium};
})();
