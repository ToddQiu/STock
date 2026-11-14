(() => {
  'use strict';
  const dialog = document.getElementById('portfolio-pending-dialog');
  const form = document.getElementById('portfolio-pending-form');
  const note = document.getElementById('portfolio-pending-note');
  const submit = form.querySelector('button[type="submit"]');
  let candidates = new Map();
  let bookId = '';
  const key = row => positionWhitelistKey(row.market, row.symbol);
  const currentBook = () => state.dashboard?.portfolio;

  function action(text, callback) {
    const control = el('button', 'text-button', text);
    control.type = 'button';
    control.disabled = state.portfolioDeletePending;
    control.addEventListener('click', callback);
    return control;
  }

  function fact(label, value, detail = '') {
    const node = el('div', 'pw-fact');
    node.append(el('span', 'pw-label', label), el('strong', '', value));
    if (detail) node.append(el('small', '', detail));
    node.title = [value, detail].filter(Boolean).join(' · ');
    return node;
  }

  function render(item) {
    const book = currentBook()?.id;
    const row = el('article', 'portfolio-position-row portfolio-position-compact portfolio-pending-row');
    const asset = el('div', 'portfolio-asset');
    const heading = el('div', 'portfolio-asset-heading');
    heading.append(el('span', 'market-badge', marketLabels[item.market] || item.market), el('strong', '', instrumentName(item)));
    asset.append(heading, el('span', 'portfolio-asset-meta pw-pending-status', `${item.symbol} · 待建仓`));

    const source = item.whitelist_source || {}, profile = source.profile || {}, quote = item.quote || {};
    const hasQuote = Number.isFinite(quote.price);
    const currentPrice = hasQuote ? quote.price : profile.reference_price;
    const status = {live:'已采集', closed:'休市', delayed:'延迟', stale:'旧报价'}[quote.quote_status] || '报价待确认';
    const quoteNote = hasQuote ? [status, quote.quote_time ? formatDate(quote.quote_time, true) : ''].filter(Boolean).join(' · ')
      : Number.isFinite(currentPrice) ? '手填参考价' : '';
    const content = el('div', 'pw-content pw-panel');
    const facts = el('div', 'pw-facts pw-pending-facts');
    facts.append(fact('行情参考', formatPortfolioUnitPrice(currentPrice, item.currency, item.market), quoteNote),
      fact('白名单资料', [profile.rating, profile.sector].filter(Boolean).join(' · ') || '已关联'),
      fact('加入理由', source.reason || '未填写'));
    content.append(facts);

    const controls = el('div', 'portfolio-actions');
    controls.append(action('建仓', () => { if (book === portfolioContext.id) openPositionDialog(item); }),
      action('移除', async () => {
        if (book !== portfolioContext.id || portfolioContext.switching || portfolioContext.writes) return;
        if (!window.confirm(`将 ${instrumentName(item)} 移出当前组合的待建仓？`)) return;
        try { await save('remove', item, book); } catch (error) { showToast(error.message, 'error'); }
      }));
    row.append(asset, content, controls);
    return row;
  }

  async function save(action, item, book) {
    if (book !== portfolioContext.id || portfolioContext.switching) throw Error('组合已切换，请重新选择');
    const result = await api('/api/portfolio/pending', {method:'POST', body:{portfolio_id:book, action, market:item.market, symbol:item.symbol}});
    if (result.portfolio_id === portfolioContext.id) await refreshDashboard({silent:true});
  }

  function preview() {
    const item = candidates.get(form.elements.target.value);
    note.textContent = item ? `加入理由 · ${item.reason || '未填写'}` : '白名单中没有可添加的标的。';
  }

  document.getElementById('portfolio-add-pending').addEventListener('click', () => {
    const book = currentBook();
    if (portfolioContext.switching || portfolioContext.writes || !book || book.id !== portfolioContext.id) {
      showToast('组合正在更新，请稍后再添加', 'info'); return;
    }
    bookId = book.id;
    const registered = new Set([...(book.positions || []), ...(book.pending_positions || [])].map(key));
    const items = (state.dashboard.stock_whitelist || []).filter(item => key(item) && !registered.has(key(item)));
    candidates = new Map(items.map(item => [key(item), item]));
    form.elements.target.replaceChildren(...items.map(item => {
      const option = el('option', '', `${instrumentName(item)} · ${item.symbol}`);
      option.value = key(item);
      return option;
    }));
    form.elements.target.disabled = submit.disabled = !items.length;
    document.getElementById('portfolio-pending-book').textContent = book.name || '当前组合';
    preview();
    dialog.showModal();
  });
  form.elements.target.addEventListener('change', preview);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const item = candidates.get(form.elements.target.value);
    if (!item || submit.disabled) return;
    submit.disabled = true;
    try {
      await save('add', item, bookId);
      dialog.close();
      showToast('已加入待建仓', 'success');
    } catch (error) { note.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  window.addEventListener('portfolio-change', () => dialog.close());
  window.PortfolioPending = {render};
})();
