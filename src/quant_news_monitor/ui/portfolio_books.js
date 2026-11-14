(function () {
  'use strict';
  const root = document.querySelector('[data-view="portfolio"]');
  const detail = el('div', 'portfolio-book-detail');
  detail.id = 'portfolio-book-detail';
  while (root.firstChild) detail.append(root.firstChild);
  const header = el('div', 'portfolio-book-header');
  const heading = el('h2', '', '持仓组合');
  const tools = el('div', 'portfolio-book-actions');
  function button(label, icon, handler, primary = false) {
    const node = el('button', primary ? 'primary-button' : 'icon-button');
    node.type = 'button'; node.title = label; node.setAttribute('aria-label', label);
    const glyph = icon === 'trash' ? el('span', 'trash-icon') : uiIcon(icon);
    glyph.setAttribute('aria-hidden', 'true'); node.append(glyph); if (primary) node.append(el('span', '', label));
    node.addEventListener('click', handler); return node;
  }
  const back = button('返回组合列表', 'arrow-left', () => {
    show(false);cards.get(portfolioContext.id)?.querySelector('.portfolio-book-open').focus();
  });
  back.className='icon-button portfolio-book-back';
  const create = button('新建组合', 'plus', () => edit(), true);
  const refresh = button('刷新组合', 'refresh-cw', async () => {
    refresh.disabled = true;
    try { await api('/api/portfolio/refresh', {method:'POST', body:{}}); await refreshDashboard(); }
    catch (e) { status.textContent = e.message; } finally { refresh.disabled = false; }
  });
  tools.append(refresh, create); header.append(heading, tools);
  const status = el('p', 'portfolio-book-status'); status.setAttribute('role', 'status');
  const list = el('div', 'portfolio-book-list'); list.id = 'portfolio-book-list';
  root.append(header, status, list, detail);
  const pageHeading = document.querySelector('.page-heading');
  detail.querySelector('.portfolio-commandbar').prepend(back);
  function syncHeader() {
    const inPortfolio = state.currentView === 'portfolio';
    const inDetail = inPortfolio && portfolioContext.detail;
    document.querySelector('.topbar').classList.toggle('portfolio-header', inPortfolio);
    pageHeading.classList.toggle('portfolio-heading', inDetail);
    back.hidden = !inDetail;
    if (inPortfolio) document.getElementById('page-title').textContent = portfolioContext.detail ? heading.textContent : '持仓组合';
  }
  const formDialog = el('dialog', 'modal portfolio-book-dialog');
  formDialog.innerHTML = '<form><h3 id="book-dialog-title">新建组合</h3><label for="book-name">组合名称</label><input id="book-name" name="name" maxlength="60" required autocomplete="off"><p class="negative" role="alert"></p><div class="portfolio-book-actions"><button type="button" class="secondary-button" data-cancel>取消</button><button class="primary-button" type="submit">保存</button></div></form>';
  formDialog.setAttribute('aria-labelledby', 'book-dialog-title'); document.body.append(formDialog);
  const form = formDialog.querySelector('form'); let editing, books = [], loading = false;
  const cards = new Map();
  function edit(identifier) {
    if (portfolioContext.switching || portfolioContext.writes) return;
    editing = identifier;
    form.reset(); form.querySelector('[role="alert"]').textContent = '';
    document.getElementById('book-dialog-title').textContent = identifier ? '重命名组合' : '新建组合';
    form.elements.name.value = books.find(b => b.id === identifier)?.name || '';
    formDialog.showModal(); form.elements.name.focus();
  }
  formDialog.querySelector('[data-cancel]').onclick = () => formDialog.close();
  form.addEventListener('submit', async event => {
    event.preventDefault(); const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
    try {
      const saved = await api('/api/portfolios', {method:'POST', body:{name:form.elements.name.value, ...(editing ? {id:editing} : {})}});
      formDialog.close(); await refreshList();
      if (!editing) await open(saved.id);
      else refreshDashboard({silent:true});
    } catch (e) { form.querySelector('[role="alert"]').textContent = e.message; }
    finally { submit.disabled = false; }
  });
  function show(value) {
    portfolioContext.detail = value;
    root.classList.toggle('portfolio-directory', !value);
    detail.hidden = !value || loading; list.hidden = value;
    back.hidden = !value; create.hidden = value;
    refresh.hidden = value;
    heading.textContent = value ? books.find(b => b.id === portfolioContext.id)?.name || '正在读取组合' : '持仓组合';
    header.hidden = value;
    syncHeader();
  }
  function canSwitch(identifier) {
    if (portfolioContext.writes || loading) { status.textContent = '正在保存或读取，请稍候再切换组合'; return false; }
    if (identifier === portfolioContext.id) return true;
    if (window.PortfolioRecords?.canLeave() === false) return false;
    const dirty = document.getElementById('pr-account')?.dataset.dirty === '1' || state.portfolioPrincipalDirty;
    return !dirty || window.confirm('账户口径有未保存的修改，放弃修改并切换组合？');
  }
  function select(identifier) {
    portfolioContext.id = identifier; apiReadGeneration += 1; pendingGetRequests.clear();
    try { localStorage.setItem('stock.portfolio.selected', identifier); } catch {}
    state.portfolioSearch = ''; document.getElementById('portfolio-search').value = '';
    state.portfolioPrincipalDirty = false;document.getElementById('portfolio-principal-error').textContent='';
    window.dispatchEvent(new Event('portfolio-change'));
  }
  async function open(identifier) {
    if (!canSwitch(identifier)) return;
    loading = true; portfolioContext.switching = true; status.textContent = '正在读取组合…';
    if (portfolioContext.id !== identifier) select(identifier);
    window.Investment.selectSection('holdings');
    show(true);
    try {
      const result = await refreshDashboard({silent:true});
      if (!result) throw new Error('组合暂时无法读取，请重试');
      status.textContent = '';
    } catch (e) { status.textContent = e.message; portfolioContext.detail = false; }
    finally { loading = false; portfolioContext.switching = false; show(portfolioContext.detail); window.PortfolioWorkspace?.syncAnalysis(); }
  }
  function card(book) {
    const row = el('article', 'portfolio-book'); row.dataset.portfolioId = book.id;
    const openButton = el('button', 'portfolio-book-open'); openButton.type = 'button'; openButton.onclick = () => open(book.id);
    const identity = el('span', 'portfolio-book-identity');
    const mark = el('span', 'portfolio-book-mark'); mark.append(uiIcon('wallet')); mark.setAttribute('aria-hidden', 'true');
    const name = el('span', 'portfolio-book-name'); name.append(el('h3'), el('span', 'portfolio-book-count'));
    identity.append(mark, name);
    const balance = el('span', 'portfolio-book-metric');
    balance.append(el('span', 'portfolio-book-label', '净资产'), el('strong', 'portfolio-book-value'));
    const daily = el('span', 'portfolio-book-metric');
    const dailyValue = el('span', 'portfolio-book-daily-value');
    dailyValue.append(el('strong', 'portfolio-book-daily'), el('small', 'portfolio-book-daily-pct'));
    daily.append(el('span', 'portfolio-book-label', '当日盈亏'), dailyValue, el('small', 'portfolio-book-daily-status'));
    const performance = el('span', 'portfolio-book-metric');
    performance.append(el('span', 'portfolio-book-label', '持仓盈亏'), el('strong', 'portfolio-book-pnl'), el('small', 'portfolio-book-valuation'));
    openButton.append(identity, balance, daily, performance, el('small', 'portfolio-book-time'));
    const actions = el('div', 'portfolio-book-actions');
    actions.append(button('重命名组合', 'notebook-pen', () => edit(book.id)));
    actions.append(button('删除空组合', 'trash', async () => {
      if (!canSwitch() || !window.confirm('删除这个空组合？有持仓、待建仓标的、本金、现金、股息或成交记录的组合不能删除。')) return;
      try {
        await api('/api/portfolios/' + book.id, {method:'DELETE', body:{confirmed:true}});
        await refreshList();
        if (portfolioContext.id === book.id) select(books[0].id);
        refreshDashboard({silent:true}); status.textContent = '空组合已删除';
      } catch (e) { status.textContent = e.message; }
    }));
    row.append(openButton, actions); list.append(row); return row;
  }
  function render(data) {
    books = data.portfolios || [{id:'default', name:'默认组合', base_currency:data.portfolio?.base_currency, summary:data.portfolio?.summary || {}}];
    const ids = new Set(books.map(b => b.id));
    for (const [id, row] of cards) if (!ids.has(id)) { row.remove(); cards.delete(id); }
    books.forEach(book => {
      let row = cards.get(book.id); if (!row) { row = card(book); cards.set(book.id, row); }
      const s = book.summary, currency = book.base_currency || 'CNY';
      row.querySelector('h3').textContent = book.name;
      row.querySelector('.portfolio-book-open').setAttribute('aria-label', '打开组合 ' + book.name);
      row.querySelector('.portfolio-book-count').textContent = `${s.position_count || 0} 项持仓 · ${currency}`;
      row.querySelector('.portfolio-book-value').textContent = formatPortfolioMoney(s.net_assets_base, currency, true);
      const daily = row.querySelector('.portfolio-book-daily');
      daily.textContent = formatPortfolioMoney(s.daily_pnl_base, currency, true);
      const dailyPct = row.querySelector('.portfolio-book-daily-pct');
      dailyPct.textContent = signedPercent(s.daily_pnl_pct);
      for (const node of [daily, dailyPct]) {
        node.classList.toggle('positive', s.daily_pnl_base > 0);
        node.classList.toggle('negative', s.daily_pnl_base < 0);
      }
      row.querySelector('.portfolio-book-daily-status').textContent = !s.daily_pnl_count && s.position_count ? '等待有效行情' : s.daily_pnl_complete === false ? `部分汇总 ${s.daily_pnl_count}/${s.position_count}` : '';
      daily.parentElement.title = [...portfolioDailyQuoteNotes(s), '按当前持仓数量估算，不含日内交易和汇兑损益'].join('；');
      const pnl = row.querySelector('.portfolio-book-pnl');
      pnl.textContent = formatPortfolioMoney(s.total_pnl_base, currency, true);
      const confirmed = s.base_valuation_complete !== false && Number.isFinite(s.total_pnl_base);
      pnl.classList.toggle('positive', confirmed && s.total_pnl_base > 0);
      pnl.classList.toggle('negative', confirmed && s.total_pnl_base < 0);
      row.querySelector('.portfolio-book-valuation').textContent = s.base_valuation_complete === false ? '估值不完整' : '';
      row.querySelector('.portfolio-book-time').textContent = book.updated_at ? '行情截至 ' + formatDate(book.updated_at, true) : '尚无行情快照';
    });
    show(portfolioContext.detail);
  }
  async function refreshList() {
    if (!books.length) status.textContent = '正在读取组合列表…';
    try {
      render(await api('/api/portfolios'));
      if (status.textContent === '正在读取组合列表…') status.textContent = '';
    } catch (e) { status.textContent = e.message; }
  }
  window.PortfolioBooks = {render, open, show, syncHeader, async recoverMissing() {
    const data = await api('/api/portfolios');
    const next = data.portfolios?.[0];
    if (!next || next.id === portfolioContext.id) return;
    select(next.id); render(data); show(false); status.textContent = '原组合已不存在，已返回组合列表';
    return refreshDashboard({silent:true});
  }};
  // Navigation always enters the list, independent of the research account selection.
  document.addEventListener('click', event => {
    if (event.target.closest('[data-view-target="portfolio"]')) { show(false); refreshList(); }
  });
  if (state.dashboard) render(state.dashboard); else show(false);
  refreshList();
})();
