/* Stock-pool controls reuse the portfolio chart and news-detail windows. */
(() => {
  'use strict';
  let editing = null, identity = null, version = 0, saving = false;
  let part = 'monitor', formPart = 'monitor';
  const profileKeys = ['pool_status', 'rating', 'sector', 'industry', 'asset_class', 'themes', 'reference_price', 'price_as_of', 'target_price', 'usage', 'added_date', 'current_pe', 'pe_as_of', 'valuation_range', 'research_tracking', 'meeting_notes', 'committee_notes', 'business', 'pe_analysis'];
  const form = () => document.getElementById('stock-form');
  const message = text => { document.getElementById('stock-form-status').textContent = text; };
  const key = () => `${form().elements.market.value}:${form().elements.symbol.value.trim().toUpperCase()}`;
  function action(label, callback, className = 'watch-link') {
    const button = el('button', className, label); button.type = 'button';
    button.addEventListener('click', callback); return button;
  }
  function factorCell(factors, side) {
    const label = side === 'bullish' ? '利多' : '利空';
    const cell = el('div', `watch-factors watch-${side}`);
    cell.append(el('small', 'watch-factor-label', label + '因素'));
    const rows = factors?.[side] || [];
    if (!rows.length) cell.append(el('span', 'quiet-note', '暂无明确因素'));
    for (const item of rows) {
      const button = action(item.impact_reason || item.analysis_summary, () => openDetail(item), 'watch-factor-link');
      button.title = newsTitle(item);
      const source = el('small', '', `${item.source || '来源未标明'} · ${formatDate(item.published_at, true)}`);
      button.append(source); cell.append(button);
    }
    if (rows.length) cell.append(el('small', 'quiet-note', `近 ${factors.window_hours || 72} 小时 · ${factors[side + '_count'] || rows.length} 条`));
    return cell;
  }
  function renderMonitored(data) {
    const container = document.getElementById('watchlist-table');
    const signature = JSON.stringify(data.watchlist || []);
    if (container.dataset.signature === signature) return;
    container.dataset.signature = signature;
    const focused = container.contains(document.activeElement) ? document.activeElement.dataset.watchAction : null;
    container.replaceChildren();
    for (const item of data.watchlist || []) {
      const row = el('div', 'watch-row'), code = el('div', 'watch-code'), name = el('div', 'watch-name');
      const chart = () => openPortfolioChart({...item, currency:{US:'USD',HK:'HKD',CN:'CNY',KR:'KRW'}[item.market]});
      const ticker = action(item.symbol, chart), company = action(instrumentName(item), chart);
      ticker.title = company.title = `打开 ${instrumentLabel(item)} K 线`;
      ticker.dataset.watchAction = `${item.market}:${item.symbol}:code`;
      company.dataset.watchAction = `${item.market}:${item.symbol}:name`;
      code.append(el('span', 'market-badge', marketLabels[item.market] || item.market), ticker);
      name.append(company, instrumentBriefNode(item));
      const factor = item.trump_factor || {}, trump = el('div', 'watch-trump');
      trump.append(el('small', 'watch-factor-label', 'Trump 因子'),
        el('strong', scoreClass(factor.score), factor.post_count > 0 ? formatScore(factor.score) : '--'),
        el('span', '', factor.theme_labels?.join('、') || '暂无相关动态'));
      trump.title = factor.reason || '暂无相关动态';
      const edit = action('', () => open(item), 'icon-button watch-edit');
      edit.append(uiIcon('notebook-pen')); edit.title = `编辑 ${item.symbol}`; edit.setAttribute('aria-label', edit.title);
      edit.dataset.watchAction = `${item.market}:${item.symbol}:edit`;
      row.append(code, name, factorCell(item.news_factors, 'bullish'), factorCell(item.news_factors, 'bearish'), trump, edit);
      container.append(row);
    }
    if (!container.children.length) container.append(emptyState('股票池为空'));
    if (focused) [...container.querySelectorAll('[data-watch-action]')].find(n => n.dataset.watchAction === focused)?.focus({preventScroll:true});
  }
  function renderWhitelist(data) {
    const container = document.getElementById('stock-whitelist-list'), rows = data.stock_whitelist || [];
    const signature = JSON.stringify([rows, Boolean(data._startup_cache), data.portfolio?.id, data.portfolio?.name,
      (data.portfolio?.positions || []).map(item => `${item.market}:${item.symbol}`)]);
    if (container.dataset.signature === signature) return;
    container.dataset.signature = signature; container.replaceChildren();
    if (!rows.length) {
      container.append(emptyState('白名单还没有标的', '点击“加入白名单”，记下你的分析和加入理由。'));
      return;
    }
    for (const item of rows) {
      const row = el('article', 'stock-whitelist-row'), identity = el('div', 'stock-whitelist-identity');
      const title = action(instrumentName(item), () => openPortfolioChart({...item, currency:{US:'USD',HK:'HKD',CN:'CNY',KR:'KRW'}[item.market]}));
      identity.append(title, el('span', 'quiet-note', `${marketLabels[item.market] || item.market} · ${item.symbol}`),
        el('small', 'quiet-note', `加入 ${formatDate(item.created_at)}`));
      const profile=item.profile || {};
      identity.append(el('small','quiet-note',[profile.pool_status,profile.rating,profile.sector,profile.asset_class].filter(Boolean).join(' · ')));
      const quote = item.quote || {};
      const quoted = Number.isFinite(quote.price) && quote.price > 0;
      const reference = !quoted && Number.isFinite(profile.reference_price) && profile.reference_price > 0;
      const status = quoted && data._startup_cache ? '上次报价' :
        ({live:'已报价', closed:'休市报价', delayed:'延迟报价', stale:'上次报价', error:'报价待确认'})[quote.quote_status] || '等待行情';
      const amount = quoted ? quote.price : reference ? profile.reference_price : null;
      const currency = quote.currency || ({CN:'CNY', HK:'HKD', US:'USD', KR:'KRW'})[item.market];
      const value = el('div', 'stock-whitelist-quote');
      value.append(el('strong', '', `${reference ? '参考价' : '现价'} ${amount === null ? '--' : formatPortfolioUnitPrice(amount, currency, item.market)}`));
      const timing = quoted && quote.quote_time ? formatDate(quote.quote_time, true) : reference ? profile.price_as_of : '';
      value.append(el('small', 'quiet-note', [reference ? '非实时' : status, timing].filter(Boolean).join(' · ')));
      if (quoted) value.title = [quote.quote_source, quote.quote_error].filter(Boolean).join(' · ');
      identity.append(value);
      row.append(identity);
      for (const [field, label] of [['analysis','分析 & 行动'],['reason','加入理由']]) {
        const note = el('div', 'stock-whitelist-note'), text = el('p', '', item[field]);
        note.append(el('small', 'watch-factor-label', label), text); row.append(note);
      }
      const tools = el('div', 'stock-whitelist-actions');
      const held = (data.portfolio?.positions || []).some(row => row.market === item.market && row.symbol === item.symbol);
      const buy = action(held ? '加仓' : '建仓', () => {
        if (portfolioContext.switching || portfolioContext.writes || (state.dashboard?.portfolio?.id || 'default') !== portfolioContext.id) {
          showToast('组合正在更新，请稍后再登记', 'error'); return;
        }
        const current = (state.dashboard?.portfolio?.positions || []).find(row => row.market === item.market && row.symbol === item.symbol);
        if (current) openPositionAdjust(current, 'buy'); else openPositionDialog(item);
      }, 'text-button');
      buy.title = `登记到“${data.portfolio?.name || '当前组合'}”，带入已有分析和理由`;
      tools.append(buy);
      tools.append(action('查看 / 编辑', () => open(item, {whitelist:true}), 'text-button'));
      row.append(tools); container.append(row);
    }
  }
  function render(data) {
    document.getElementById('stock-monitor-count').textContent = (data.watchlist || []).length;
    document.getElementById('stock-whitelist-count').textContent = (data.stock_whitelist || []).length;
    if (part === 'whitelist') renderWhitelist(data); else renderMonitored(data);
  }
  function setPart(value) {
    if (saving || !['monitor','whitelist'].includes(value)) return;
    part = value;
    document.getElementById('stock-monitor-part').hidden = part !== 'monitor';
    document.getElementById('stock-whitelist-part').hidden = part !== 'whitelist';
    document.querySelectorAll('[data-stock-part]').forEach(node => {
      node.classList.toggle('active', node.dataset.stockPart === part);
      node.setAttribute('aria-pressed', String(node.dataset.stockPart === part));
    });
    document.getElementById('add-stock-button').textContent = part === 'whitelist' ? '＋ 加入白名单' : '＋ 新增股票';
    if (state.dashboard) render(state.dashboard);
  }
  function open(item = null, {whitelist = part === 'whitelist'} = {}) {
    if (saving) return;
    version++; editing = item; identity = whitelist && item ? item : null;
    formPart = whitelist ? 'whitelist' : 'monitor';
    form().reset();
    if (item) for (const field of ['market','symbol','name']) form().elements[field].value = item[field];
    document.getElementById('stock-whitelist-fields').hidden = !whitelist;
    for (const field of ['analysis','reason']) {
      const node = form().elements[field]; node.disabled = !whitelist; node.required = whitelist;
      node.value = whitelist ? item?.[field] || '' : '';
    }
    for(const field of profileKeys){const control=form().elements['profile_'+field];if(control){control.disabled=!whitelist;control.value=whitelist?(item?.profile?.[field] ?? ''):'';}}
    form().elements.market.disabled = Boolean(whitelist && item);
    form().elements.symbol.readOnly = Boolean(whitelist && item);
    document.getElementById('stock-resolve').hidden = Boolean(whitelist && item);
    document.getElementById('stock-form-title').textContent = whitelist ? item ? '编辑白名单标的' : '加入白名单' : item ? '编辑监测股票' : '新增监测股票';
    document.getElementById('stock-save').textContent = item ? '保存修改' : whitelist ? '加入白名单' : '加入股票池';
    document.getElementById('stock-delete').hidden = !item;
    document.getElementById('stock-delete').textContent = whitelist ? '移出白名单' : '删除监测';
    message(whitelist ? '' : item ? '仅调整监测池，不改变持仓和历史研究。' : '');
    document.getElementById('stock-dialog').showModal(); form().elements.symbol.focus();
  }
  async function resolve() {
    const current = ++version, requested = key(); identity = null;
    form().elements.name.value = ''; message('正在识别证券…');
    try {
      const data = await api('/api/securities/resolve?' + new URLSearchParams({market:form().elements.market.value, symbol:form().elements.symbol.value.trim()}));
      if (current !== version || requested !== key()) return null;
      identity = data; form().elements.symbol.value = data.symbol; form().elements.name.value = data.name;
      message(`${data.currency} · ${data.source || '证券目录'}`); return data;
    } catch (error) { if (current === version) message(error.message); return null; }
  }
  function lock(value) {
    saving = value;
    form().querySelectorAll('button,input,select,textarea').forEach(node => { node.disabled = value; });
    if (!value) {
      form().elements.market.disabled = formPart === 'whitelist' && Boolean(editing);
      for (const field of ['analysis','reason',...profileKeys.map(k=>'profile_'+k)]) if(form().elements[field])form().elements[field].disabled = formPart !== 'whitelist';
    }
  }
  async function refreshWhitelist() {
    const data = await api('/api/watchlist/whitelist');
    if (state.dashboard) {
      state.dashboard.stock_whitelist = data.items;
      render(state.dashboard);
    }
  }
  function bind() {
    document.querySelectorAll('[data-stock-part]').forEach(node => node.addEventListener('click', () => setPart(node.dataset.stockPart)));
    for (const field of ['market','symbol']) form().elements[field].addEventListener('input', () => {
      version++; identity = null; form().elements.name.value = ''; message('');
    });
    form().elements.symbol.addEventListener('blur', () => { if (!saving && !identity && form().elements.symbol.value.trim()) resolve(); });
    document.getElementById('stock-resolve').addEventListener('click', resolve);
    document.getElementById('stock-dialog').addEventListener('cancel', event => {if (saving) event.preventDefault();});
    document.getElementById('stock-dialog').addEventListener('close', () => { version++; });
    form().addEventListener('submit', async event => {
      event.preventDefault(); if (saving) return;
      const original = editing;
      lock(true);
      try {
        const notes = {};
        if (formPart === 'whitelist') {
          for (const [field,label] of [['analysis','分析 & 行动'],['reason','加入理由']]) {
            notes[field] = form().elements[field].value.trim();
            if (!notes[field]) throw Error(`请填写${label}`);
            if (notes[field].length > 2000) throw Error(`${label}最多2000字`);
          }
        }
        const target = identity || await resolve();
        if (!target) return;
        if (formPart === 'whitelist') {
          await api('/api/watchlist/whitelist', {method:'POST', body:{market:target.market,symbol:target.symbol,
            action:original ? 'update' : 'add', ...notes, profile:Object.fromEntries(profileKeys.filter(k=>form().elements['profile_'+k]).map(k=>[k,form().elements['profile_'+k].value.trim()]))}});
          document.getElementById('stock-dialog').close();
          showToast(original ? '白名单资料已保存' : '已加入白名单和新闻监测', 'success');
          try { await refreshWhitelist(); await refreshDashboard({silent:true}); } catch (error) { showToast(`已保存，列表暂未刷新：${error.message}`, 'error'); }
          return;
        }
        const changed = original && `${original.market}:${original.symbol}` !== `${target.market}:${target.symbol}`;
        if (changed && !window.confirm(`将 ${original.name}（${original.symbol}）替换为 ${target.name}（${target.symbol}）？仅改变监测池。`)) return;
        await api(original ? '/api/watchlist/replace' : '/api/watchlist', {
          method:'POST', body:{market:target.market,symbol:target.symbol,name:target.name,
            ...(original ? {previous:{market:original.market,symbol:original.symbol}} : {})},
        });
        document.getElementById('stock-dialog').close();
        showToast(original ? '监测股票已更新' : '股票已加入监测池', 'success');
        await refreshDashboard({silent:true});
      } catch (error) { message(error.message); }
      finally { lock(false); }
    });
    document.getElementById('stock-delete').addEventListener('click', async () => {
      if (!editing || saving) return;
      lock(true);
      try {
        if (formPart === 'whitelist') {
          if (!window.confirm(`将 ${editing.name}（${editing.symbol}）移出白名单？已有新闻监测会保留。`)) return;
          await api(`/api/watchlist/whitelist/${encodeURIComponent(editing.market)}/${encodeURIComponent(editing.symbol)}`, {method:'DELETE'});
          document.getElementById('stock-dialog').close(); showToast('已移出白名单', 'success');
          await refreshWhitelist();
        } else if (await removeWatchItem(editing)) document.getElementById('stock-dialog').close();
      } catch (error) { message(error.message); showToast(error.message, 'error'); }
      finally { lock(false); }
    });
  }
  window.StockWatchlist = {render, open, bind, setPart};
})();
