(function () {
  'use strict';
  const modes = ['holdings', 'plan', 'research', 'meetings', 'execution'];
  let mode = 'holdings';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const target = item => `${item.market}:${item.symbol}`;
  const price = (value, item) => formatPortfolioUnitPrice(value, item.currency, item.market);
  const date = value => value ? String(value).slice(0, 10) : '未录入';
  const stance = {bullish:'有条件看多', neutral:'中性', bearish:'有条件看空'};
  let refreshTimer = null;
  const analysisPath = '/api/portfolio/instrument-analysis';
  const portfolioExportLabel = '导出全组合 AI 分析';
  const analysisStatus = {waiting:'等待分析', stale:'Skill 已更新，等待重新分析', pending:'正在生成分析', ready:'OpenAI 分析', insufficient:'公开资料不足', error:'分析暂未完成'};
  const ratingTones = new Map([['买入','positive'], ['观察','neutral'], ['卖出','negative']]);
  let analysisRows = {}, analysisKey = '', analysisError = '', analysisTimer = null, analysisVersion = 0;
  const exporting = new Set();

  function portfolioActive(book) {
    return state.currentView === 'portfolio' && portfolioContext.detail
      && portfolioContext.id === book && state.dashboard?.portfolio?.id === book;
  }

  function analysisActive(book) {
    return mode === 'research' && portfolioActive(book);
  }

  function canGenerateAnalysis() {
    return Boolean(state.dashboard?.settings?.api_credential?.configured);
  }

  function syncAnalysisSetup() {
    const note = document.getElementById('pw-ai-config');
    if (!note) return;
    const enabled = canGenerateAnalysis();
    note.hidden = enabled || !analysisActive(portfolioContext.id);
    document.getElementById('pw-ai-config-status').textContent = enabled
      ? 'API Key 已读取 · 新分析使用 DSA 方法'
      : '请在「设置 → API Key」填写你自己的 OpenAI Key';
    document.getElementById('pw-ai-config-settings').hidden = enabled;
  }

  function ratingBadge(rating) {
    const tone = ratingTones.get(rating);
    return tone ? el('span', `pw-rating ${tone}`, rating) : null;
  }

  function fillAnalysisSummary(node) {
    const result = analysisRows[node.dataset.analysisTarget] || {}, analysis = result.analysis;
    const status = analysisError ? '分析读取失败' : analysisStatus[result.status] || '等待自动分析';
    const updated = analysis?.generated_at ? `${analysis.workflow ? 'DSA 适配' : 'OpenAI'} · ${formatDate(analysis.generated_at, true)}` : '';
    const issue = result.error || analysisError;
    const summary = result.status === 'insufficient' ? issue || status : analysis?.summary || issue || status;
    const rating = ratingBadge(result.rating);
    const heading = el('span', 'pw-label pw-rating-heading');
    heading.append(el('span', '', '自动评级'));
    heading.append(rating || el('span', '', result.status === 'pending' ? '生成中…' : '待评级'));
    const timing = result.status === 'ready' && !issue ? updated : rating && result.status !== 'insufficient' ? `上次评级 · ${status}` : status;
    if (timing) heading.append(el('span', 'pw-rating-time', timing));
    const reason = result.rating_reason || (issue && analysis ? issue : analysis?.watch || updated || status);
    node.replaceChildren(fact(heading, summary, '', [summary, updated].filter(Boolean).join('\n')),
      fact(result.rating_reason ? '评级依据' : '观察重点', reason, '',
        [reason, result.rating_condition, updated, issue].filter(Boolean).join('\n')));
  }

  function applyAnalysis() {
    document.querySelectorAll('.pw-auto-summary').forEach(fillAnalysisSummary);
    document.querySelectorAll('[data-analysis-refresh]').forEach(node => {
      const pending = analysisRows[node.dataset.analysisRefresh]?.status === 'pending';
      node.disabled = Boolean(state.portfolioDeletePending || pending || !canGenerateAnalysis());
      node.textContent = pending ? '生成中…' : '更新分析';
    });
    document.querySelectorAll('[data-analysis-export]').forEach(syncExportButton);
    syncPortfolioExport();
    syncAnalysisSetup();
  }

  function syncExportButton(node) {
    const available = canGenerateAnalysis();
    const busy = exporting.has(`${node.dataset.exportBook}:${node.dataset.analysisExport}`);
    node.disabled = !available || !portfolioActive(node.dataset.exportBook) || Boolean(portfolioContext.switching || state.portfolioDeletePending || busy);
    if (node.classList.contains('pw-position-export')) {
      const icon = uiIcon(busy ? 'refresh-cw' : 'download');
      if (busy) icon.classList.add('scan-symbol', 'spinning');
      node.replaceChildren(icon);
    } else {
      node.textContent = busy ? '生成中…' : node.dataset.exportLabel || '导出 PDF';
    }
    node.setAttribute('aria-busy', String(busy));
    node.title = busy ? '正在采集最新资料并生成分析 PDF…' : available
      ? '生成分析 PDF：重新采集当前行情、基本面和技术面'
      : '在设置 → API Key 填写密钥后即可生成';
  }

  function syncPortfolioExport() {
    const node = document.getElementById('pw-export-portfolio-analysis');
    if (!node) return;
    const hasPositions = Boolean(state.dashboard?.portfolio?.positions?.length);
    node.hidden = mode !== 'research';
    node.disabled = !hasPositions || !canGenerateAnalysis() || !analysisActive(portfolioContext.id) || Boolean(
      portfolioContext.switching || state.portfolioDeletePending || exporting.has(`${portfolioContext.id}:*`));
    node.title = !hasPositions ? '当前组合暂无持仓' : canGenerateAnalysis()
      ? '重新分析当前组合全部持仓，完成后下载一份 PDF'
      : '在设置中填写自己的 API Key 后即可导出';
  }

  async function exportAnalysis(node, item) {
    const book = portfolioContext.id;
    if (node.disabled || portfolioContext.switching || state.portfolioDeletePending || !(item ? portfolioActive(book) : analysisActive(book))) return;
    if (item && node.dataset.exportBook !== book) return;
    const key = `${book}:${item ? target(item) : '*'}`;
    if (exporting.has(key)) return;
    const payload = {portfolio_id:book, ...(item ? {target:target(item)} : {})};
    const path = analysisPath + (item ? '.pdf' : '/portfolio.pdf');
    const syncButton = item ? () => syncExportButton(node) : syncPortfolioExport;
    const syncAll = () => { syncButton(); document.querySelectorAll('[data-analysis-export]').forEach(syncExportButton); syncPortfolioExport(); };
    exporting.add(key);
    node.dataset.exporting = '1';
    if (!item) node.textContent = '重新分析中…';
    syncAll();
    try {
      const response = await fetch(path, {method:'POST', cache:'no-store',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw Error(detail.error || 'AI 分析 PDF 导出失败');
      }
      await downloadAttachment(response, item ? 'STock-个股AI分析.pdf' : 'STock-全组合AI分析.pdf');
      if (analysisActive(book)) await requestAnalysis('read');
    } catch (error) {
      showToast(error.message || 'AI 分析 PDF 导出失败', 'error');
    } finally {
      delete node.dataset.exporting;
      exporting.delete(key);
      if (!item) node.textContent = portfolioExportLabel;
      syncAll();
    }
  }

  function analysisExportButton(item, compact = true) {
    const node = button(compact ? '' : '导出 PDF', node => exportAnalysis(node, item));
    node.dataset.analysisExport = target(item);
    node.dataset.exportBook = portfolioContext.id;
    node.dataset.exportLabel = '导出 PDF';
    if (compact) {
      node.classList.remove('text-button');
      node.classList.add('icon-button', 'pw-position-export');
    }
    node.setAttribute('aria-label', `生成 ${instrumentName(item)} 的最新基本面与技术面分析 PDF`);
    syncExportButton(node);
    return node;
  }

  async function requestAnalysis(action, item) {
    const book = portfolioContext.id, version = ++analysisVersion;
    clearTimeout(analysisTimer);
    analysisError = '';
    if (item) {
      analysisRows[target(item)] = {...analysisRows[target(item)], status:'pending', error:''};
      applyAnalysis();
    }
    const current = () => version === analysisVersion && analysisActive(book);
    async function load(write = false) {
      if (!current()) return;
      try {
        const response = write ? await api(analysisPath, {method:'POST', body:{portfolio_id:book, action,
          ...(item ? {market:item.market, symbol:item.symbol} : {})}}) : await api(analysisPath);
        if (!current() || response.portfolio_id !== book) return;
        analysisRows = response.items || {};
        applyAnalysis();
        if (Object.values(analysisRows).some(row => row.status === 'pending')) {
          analysisTimer = setTimeout(() => load(), 3000);
        }
      } catch (error) {
        if (!current()) return;
        if (!write && error.message === '数据已变更，等待下一次刷新') {
          analysisTimer = setTimeout(() => load(), 3000);
          return;
        }
        analysisError = error.message;
        for (const [key, row] of Object.entries(analysisRows)) {
          if (row.status === 'pending') analysisRows[key] = {...row, status:'error', error:analysisError};
        }
        applyAnalysis();
      }
    }
    await load(action !== 'read');
  }

  function syncAnalysis() {
    document.querySelectorAll('[data-analysis-export]').forEach(syncExportButton);
    syncPortfolioExport();
    syncAnalysisSetup();
    const book = portfolioContext.id;
    if (!analysisActive(book)) {
      if (analysisKey) { analysisKey = ''; ++analysisVersion; clearTimeout(analysisTimer); }
      return;
    }
    if (portfolioContext.switching || state.dashboard._startup_cache) return;
    const symbols = (state.dashboard.portfolio.positions || []).map(target).sort();
    const enabled = canGenerateAnalysis();
    const key = JSON.stringify([book, symbols, enabled]);
    if (!symbols.length || key === analysisKey) return;
    analysisKey = key;
    requestAnalysis(enabled ? 'ensure' : 'read');
  }

  function riskPrices(item) {
    const risk = item.risk_settings || {}, cost = item.average_cost;
    return {
      profit: finite(cost) && finite(risk.take_profit_pct) ? cost * (1 + risk.take_profit_pct / 100) : risk.take_profit_price ?? null,
      stop: finite(cost) && finite(risk.stop_loss_pct) ? cost * (1 - risk.stop_loss_pct / 100) : risk.stop_price ?? null,
    };
  }

  function valuation(item) {
    const plan = item.plan || {}, low = plan.valuation_low, high = plan.valuation_high;
    if (!finite(low) || !finite(high)) return {label:'未设置估值区间', tone:'', detail:'填写合理估值上下限及依据'};
    const detail = `${price(low, item)} – ${price(high, item)}`;
    if (plan.currency !== item.currency) return {label:'估值币种待核对', tone:'', detail:'原区间币种与当前持仓不一致'};
    if (!finite(item.price) || ['stale','error','waiting'].includes(item.quote_status)) return {label:'现价待确认', tone:'', detail};
    return {label:item.price < low ? '低于估值区间' : item.price > high ? '高于估值区间' : '处于估值区间',
      tone:'', detail};
  }

  function fact(label, value, note = '', title = '') {
    const node = el('div', 'pw-fact');
    node.append(typeof label === 'string' ? el('span', 'pw-label', label) : label, el('strong', '', value));
    if (note) node.append(el('small', '', note));
    if (title) node.title = title;
    return node;
  }

  function button(text, action) {
    const node = el('button', 'text-button', text); node.type = 'button';
    node.disabled = state.portfolioDeletePending;
    node.addEventListener('click', event => { event.stopPropagation(); action(node); });
    return node;
  }

  const profileActions = el('div', 'pw-profile-actions');
  profileActions.id = 'pw-profile-actions';
  profileActions.setAttribute('popover', 'auto');
  profileActions.setAttribute('role', 'group');
  document.body.append(profileActions);

  function openProfileActions(item, trigger) {
    profileActions.hidePopover?.();
    profileActions.setAttribute('aria-label', `${instrumentName(item)} 标的资料`);
    const open = action => () => { profileActions.hidePopover(); action(item); };
    const edit = button('编辑资料', open(editPlan));
    profileActions.replaceChildren(edit, button('止盈止损', open(openPositionRisk)),
      button('更新行业 / PE', node => refreshMetrics(item, node)),
      button('买入分析 / 完整资料', open(showDetails)));
    profileActions.showPopover({source:trigger});
    const anchor = trigger.getBoundingClientRect(), width = profileActions.offsetWidth, height = profileActions.offsetHeight;
    profileActions.style.left = `${Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8))}px`;
    const top = anchor.bottom + 6 + height <= window.innerHeight - 8 ? anchor.bottom + 6 : anchor.top - height - 6;
    profileActions.style.top = `${Math.max(8, top)}px`;
    edit.focus({preventScroll:true});
  }

  function researchDetails(item, card) {
    const details = el('section', 'pw-research');
    details.append(el('h3', '', `公司与策略 · ${stance[card.stance] || '未判断'} · v${card.version}`));
    const content = el('div', 'pw-research-body');
    content.append(el('p', 'pw-source', `${card.name} · 资料 ${date(card.data_as_of)} · 研究期限 ${card.horizon || '未填写'}`));
    for (const [key, label] of Object.entries(strategyFields)) {
      if (card[key]) content.append(fact(label, card[key]));
    }
    for (const [key, label] of Object.entries(stance)) {
      const scenario = card.scenarios?.[key] || {};
      if (Object.values(scenario).some(Boolean)) content.append(fact(label, scenario.thesis || '未填写',
        `成立：${scenario.conditions || '未填写'}；失效：${scenario.invalidation || '未填写'}`));
    }
    for (const evidence of card.evidence || []) {
      const line = el('p', 'pw-evidence');
      if (/^https?:\/\//.test(evidence.url || '')) {
        const link = el('a', '', evidence.role === 'counter' ? '反证原文' : '证据原文');
        link.href = evidence.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; line.append(link);
      }
      line.append(el('span', '', ` ${date(evidence.as_of)} · ${evidence.excerpt || ''}`)); content.append(line);
    }
    content.append(button('在此编辑 / 复盘', () => editResearch(item, card)));
    details.append(content); return details;
  }

  function facts(item, compact = false) {
    const workspace = item.workspace || {}, plan = item.plan || {}, risk = riskPrices(item), zone = valuation(item);
    const pe = workspace.pe || {}, peText = pe.not_applicable ? '不适用' : !finite(pe.value) ? '未取得' : pe.value <= 0 ? '不适用（非正PE）' : pe.value.toFixed(2) + ' 倍';
    const ruleNote = item.risk_settings?.enabled === false ? '警戒提醒已关闭' : '相对当前平均成本';
    const make = (label, value, note = '', source = '') => fact(label, value, compact ? '' : note,
      [value, note, source].filter(Boolean).join(' · '));
    const grid = el('div', 'pw-facts');
    grid.append(
      make('行业 / 分类', (window.STockPortfolioResearch?.classificationName(workspace.sector) || workspace.sector) || '未取得 · 可补录', [workspace.sector_source, workspace.sector].filter(Boolean).join(' · '), workspace.sector_source_url || ''),
      make('标的用法', plan.usage || '未填写 · 可编辑'),
      make('市盈率（PE）', peText, [pe.basis, pe.as_of ? `资料 ${date(pe.as_of)}` : ''].filter(Boolean).join(' · '), [workspace.pe_source, workspace.pe_source_url].filter(Boolean).join(' · ')),
      make('建仓时间', date(plan.opened_date), !plan.opened_date && item.created_at ? `系统录入 ${date(item.created_at)}，非建仓日期` : ''),
      make('止盈价格', risk.profit == null ? '未设置' : price(risk.profit, item), risk.profit == null ? '' : item.risk_settings?.take_profit_price != null ? '原币固定价' : ruleNote),
      make('止损价格', risk.stop == null ? '未设置' : price(risk.stop, item), risk.stop == null ? '' : item.risk_settings?.stop_price != null ? '原币固定价' : ruleNote),
      make('当前区间 · 估值', zone.label, zone.detail, [plan.valuation_basis, plan.valuation_as_of].filter(Boolean).join(' · ')),
      compact
        ? make('估值依据 / 买入原因', `${plan.valuation_basis || '估值未填写'} · ${plan.buy_reason || '买入原因未填写'}`, '',
          `估值依据：${plan.valuation_basis || '未填写'}；买入分析：${plan.entry_analysis || '未填写'}；买入原因：${plan.buy_reason || '未填写'}`)
        : make('估值依据', plan.valuation_basis || '未填写', plan.valuation_as_of ? `资料 ${date(plan.valuation_as_of)} · ${plan.currency}` : ''),
    );
    if (!compact) {
      grid.append(make('持仓状态', item.holding_status || '已建仓'),
        make('持仓编号', item.holding_id || `${portfolioContext.id}:${target(item)}`),
        make('持仓天数', Number.isInteger(item.holding_days) ? `${item.holding_days} 天` : '未取得',
          '按建仓日期至北京时间今天的自然日计算'),
        make('买入分析', plan.entry_analysis || '未填写'), make('买入原因', plan.buy_reason || '未填写'));
      for (const [key,label] of [['account_name','账户名称'],['rating','个股评级'],['strategy','个股策略'],['asset_class','资产分类'],['business','标的概况'],['risks','主要风险']]) {
        grid.append(make(label, plan[key] || '未填写'));
      }
    }
    return grid;
  }

  function render(item, metrics = {}) {
    const core = el('section', 'pw-content');
    core.setAttribute('aria-label', `${item.name} 持仓资料与研究`);
    const panel = name => {
      const node = el('div', `pw-panel pw-${name}`);
      node.dataset.pwPanel = name; node.hidden = mode !== name;
      core.append(node); return node;
    };
    const holdings = panel('holdings'), numbers = el('div', 'pw-metrics');
    for (const key of ['quantity', 'cost', 'value', 'daily', 'pnl', 'weight']) if (metrics[key]) numbers.append(metrics[key]);
    const inline = el('div', 'pw-inline'), risk = riskPrices(item), zone = valuation(item);
    inline.append(el('span', risk.profit == null ? '' : 'positive', `止盈 ${risk.profit == null ? '未设置' : price(risk.profit, item)}`),
      el('span', risk.stop == null ? '' : 'negative', `止损 ${risk.stop == null ? '未设置' : price(risk.stop, item)}`),
      el('span', 'pw-inline-zone', zone.label));
    inline.title = zone.detail;
    holdings.append(numbers, inline);
    panel('plan').append(facts(item, true));
    const research = panel('research'), cards = item.workspace?.cards || [], card = cards[0];
    const summary = el('div', 'pw-research-summary pw-auto-summary');
    summary.dataset.analysisTarget = target(item);
    fillAnalysisSummary(summary);
    research.append(summary);
    if (card) {
      const manual = `${stance[card.stance] || '未判断'} · v${card.version} · 研究期限 ${card.horizon || '未填写'}`;
      research.append(el('p', 'pw-manual-summary', `公司与策略 · ${manual}`));
    }
    const actions = el('div', 'pw-tools');
    const pending = analysisRows[target(item)]?.status === 'pending';
    const update = button(pending ? '生成中…' : '更新分析', () => requestAnalysis('refresh', item));
    update.dataset.analysisRefresh = target(item);
    update.disabled ||= pending || !canGenerateAnalysis();
    const download = analysisExportButton(item, false);
    actions.append(button('查看分析全文', () => showDetails(item)), download, update,
      button(card ? '编辑研究' : '新建研究', () => editResearch(item)));
    research.append(actions);
    return core;
  }

  function setMode(value) {
    if (!modes.includes(value)) return false;
    if (value !== mode && window.PortfolioRecords?.canLeave() === false) return false;
    if (value !== mode && (portfolioContext.switching || portfolioContext.writes)) return false;
    const changed = value !== mode;
    if (value !== mode && value !== 'research') { analysisKey = ''; ++analysisVersion; clearTimeout(analysisTimer); }
    mode = value;
    const records = mode === 'meetings' || mode === 'execution';
    document.getElementById('portfolio-holdings-toolbar').dataset.mode = mode;
    document.getElementById('portfolio-position-list').hidden = records;
    document.querySelectorAll('#portfolio-holdings-toolbar > .portfolio-type-filter, #portfolio-holdings-toolbar > .portfolio-search-box, #portfolio-holdings-toolbar > .portfolio-list-tools').forEach(node => { node.hidden = records; });
    if (records) {
      if (changed) window.PortfolioRecords?.open(mode);
    } else window.PortfolioRecords?.hide();
    document.querySelectorAll('.pw-panel').forEach(node => { node.hidden = node.dataset.pwPanel !== mode; });
    document.querySelectorAll('[data-pw-mode]').forEach(node => {
      node.setAttribute('aria-selected', String(node.dataset.pwMode === mode));
      node.classList.toggle('active', node.dataset.pwMode === mode);
    });
    document.getElementById('portfolio-position-list')?.setAttribute('aria-labelledby', 'pw-tab-' + mode);
    syncAnalysis();
    return true;
  }

  function bindControls() {
    const tabs = document.querySelectorAll('[data-pw-mode]');
    tabs.forEach((node, index) => {
      node.addEventListener('click', () => setMode(node.dataset.pwMode));
      node.addEventListener('keydown', event => {
        const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : step ? (index + step + tabs.length) % tabs.length : -1;
        if (next < 0) return;
        event.preventDefault(); setMode(tabs[next].dataset.pwMode); tabs[next].focus();
      });
    });
    setMode(mode);
  }

  const detailsDialog = el('dialog', 'modal pw-details-dialog');
  detailsDialog.setAttribute('aria-label', '完整持仓资料与公司策略'); document.body.append(detailsDialog);
  async function loadCards(item) {
    const selected = portfolioContext.id;
    try {
      const data = await api(`/api/portfolio/details?market=${encodeURIComponent(item.market)}&symbol=${encodeURIComponent(item.symbol)}`);
      if (selected !== portfolioContext.id || data.portfolio_id !== selected) return false;
      item.workspace = {...item.workspace, cards:data.cards, cards_loaded:true};
      return true;
    } catch (error) { if (selected === portfolioContext.id) showToast(`研究全文读取失败：${error.message}`, 'error'); return false; }
  }

  async function showDetails(item) {
    if (item.workspace?.cards_loaded === false && !await loadCards(item)) return;
    const body = el('article', 'pw-details-body'), heading = el('div', 'pw-heading');
    heading.append(el('h2', '', `${instrumentName(item)} · 完整资料`), button('关闭', () => detailsDialog.close()));
    body.append(heading);
    const auto = analysisRows[target(item)], result = auto?.analysis;
    if (auto) {
      const section = el('section', 'pw-research');
      section.append(el('h3', '', '标的自动分析'));
      const content = el('div', 'pw-research-body');
      const rating = ratingBadge(result?.rating || (result && auto.status !== 'ready' ? null : auto.rating));
      if (rating) {
        const label = el('span', 'pw-label pw-rating-heading', '自动评级');
        label.append(rating);
        content.append(label);
      } else if (result) content.append(el('p', 'pw-source', '此份分析待更新评级'));
      if (result?.summary) content.append(fact('综合判断', result.summary));
      if (result?.custom_analysis) content.append(fact('自定义 Skill 分析', result.custom_analysis));
      const ratingFields = result && auto.status !== 'ready' ? result : {...auto, ...result};
      if (ratingFields.rating_reason) content.append(fact('评级依据', ratingFields.rating_reason));
      if (result?.risks) content.append(fact('风险与反证', result.risks));
      if (ratingFields.rating_condition) content.append(fact('复核条件', ratingFields.rating_condition));
      if (result) {
        if (result.drivers) content.append(fact('驱动因素', result.drivers));
        if (result.watch) content.append(fact('观察重点', result.watch));
        content.append(el('p', 'pw-source', [auto.status !== 'ready' ? '上次分析' : '', result.workflow,
          result.model || 'OpenAI', formatDate(result.generated_at, true)].filter(Boolean).join(' · ')));
        for (const evidence of result.evidence || []) {
          const stamp = evidence.as_of?.length === 10 ? evidence.as_of : evidence.as_of ? formatDate(evidence.as_of, true) : '';
          const line = el('p', 'pw-evidence', [`[${evidence.id}] ${evidence.label}`, evidence.source, stamp].filter(Boolean).join(' · '));
          if (/^https?:\/\//.test(evidence.url || '')) {
            const link = el('a', '', '查看来源');
            link.href = evidence.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; line.append(' ', link);
          }
          content.append(line);
        }
      }
      if (auto.error || auto.status !== 'ready') content.append(el('p', 'pw-source', auto.error || analysisStatus[auto.status]));
      section.append(content); body.append(section);
    }
    body.append(facts(item));
    const analysis = item.analysis || {};
    const action = fact('分析 & 行动', `${analysis.action || '继续观察'} · ${analysis.direction || '中性'}`,
      [analysis.reason, ...(analysis.risks || [])].filter(Boolean).join('；'));
    action.classList.add('pw-analysis'); body.append(action);
    body.append(fact('相关消息与信号', `${Number(item.news_count || 0)} 条相关消息`,
      item.signal_available === false ? '综合信号资料不足' : `综合信号 ${finite(item.signal_score) ? item.signal_score.toFixed(2) : '未取得'}`));
    if (item.dividends?.received_count) {
      body.append(fact('已收净股息', formatPortfolioMoney(item.dividends.received_net_base, state.dashboard?.portfolio?.base_currency || 'CNY', true), '已登记净股息，独立于浮动盈亏；原始均价不变'));
    }
    const cards = item.workspace?.cards || [];
    if (cards.length) cards.forEach(card => body.append(researchDetails(item, card)));
    else body.append(fact('公司与策略', '尚无对应研究'), button('在此新建研究', () => editResearch(item)));
    detailsDialog.replaceChildren(body); detailsDialog.showModal();
  }

  const planDialog = el('dialog', 'modal pw-plan-dialog');
  planDialog.setAttribute('aria-label', '编辑持仓资料'); document.body.append(planDialog);
  function editPlan(item) {
    const plan = item.plan || {}, selected = portfolioContext.id;
    const input = (label, name, type = 'text') => `<label>${label}<input name="${name}" type="${type}" value="${esc(plan[name] ?? '')}" ${type === 'number' ? 'min="0.000001" step="any"' : ''}></label>`;
    planDialog.innerHTML = `<form><h2>${esc(instrumentName(item))} · 持仓资料</h2><div class="form-grid two-columns">
      ${input('账户名称','account_name')}${input('个股评级','rating')}
      <label>个股策略<input name="strategy" maxlength="500" list="pw-strategy-options" value="${esc(plan.strategy || '')}"><datalist id="pw-strategy-options"><option>待清仓</option><option>2个月获利</option><option>半年获利</option><option>1年半获利</option><option>长期持有</option><option>定投</option><option>待建仓</option></datalist></label>
      ${input('资产分类','asset_class')}${input('行业 / 分类（手动补充）','sector')}${input('建仓日期','opened_date','date')}
      ${[ ["business","标的概况"],["risks","主要风险"] ].map(([key,label])=>`<label class="pw-full">${label}<textarea name="${key}" maxlength="2000" rows="3">${esc(plan[key] || "")}</textarea></label>`).join("")}
      <label class="pw-full">标的用法<input name="usage" maxlength="500" list="pw-usage-options" value="${esc(plan.usage || '')}" placeholder="例如长期核心、波段、对冲、短线"><datalist id="pw-usage-options"><option>长期核心</option><option>波段</option><option>对冲</option><option>短线</option></datalist></label>
      <label class="pw-full">买入分析（选填）<textarea name="entry_analysis" maxlength="2000" rows="3">${esc(plan.entry_analysis || '')}</textarea></label>
      <label class="pw-full">买入原因${plan.buy_reason ? '（必填）' : '（旧持仓可补录）'}<textarea name="buy_reason" maxlength="2000" rows="3" ${plan.buy_reason ? 'required' : ''}>${esc(plan.buy_reason || '')}</textarea></label>
      ${input(`合理估值下限（${esc(item.currency)} / 每股或份）`,'valuation_low','number')}${input(`合理估值上限（${esc(item.currency)} / 每股或份）`,'valuation_high','number')}
      ${input('估值资料日期','valuation_as_of','date')}<label class="pw-full">估值依据<textarea name="valuation_basis" maxlength="2000" rows="3">${esc(plan.valuation_basis || '')}</textarea></label>
      </div><p class="form-note">估值区间是手动研究假设；现价与区间比较独立于止盈、止损价。建仓日期留空即为未录入。</p>
      <p class="negative" role="alert"></p><div class="modal-actions"><button type="button" class="secondary-button" data-cancel>取消</button><button class="primary-button" type="submit">保存资料</button></div></form>`;
    const form = planDialog.querySelector('form');
    form.elements.sector.maxLength = 120;
    form.querySelector('[data-cancel]').onclick = () => planDialog.close();
    form.onsubmit = async event => {
      event.preventDefault(); const submit = event.submitter; submit.disabled = true;
      try {
        if (selected !== portfolioContext.id) throw Error('组合已切换，请重新打开资料');
        const values = Object.fromEntries(new FormData(form));
        values.entry_analysis = (values.entry_analysis || '').trim();
        values.buy_reason = (values.buy_reason || '').trim();
        if (plan.buy_reason && !values.buy_reason) throw Error('请填写买入原因');
        if (!values.buy_reason) delete values.buy_reason;
        await api('/api/portfolio/plan', {method:'POST', body:{market:item.market, symbol:item.symbol, portfolio_id:selected, plan:values}});
        planDialog.close();
        try { await refreshDashboard(); showToast('持仓资料已保存', 'success'); }
        catch (error) { showToast(`资料已保存，界面暂未刷新：${error.message}`, 'error'); }
      } catch (error) { form.querySelector('[role="alert"]').textContent = error.message; }
      finally { submit.disabled = false; }
    };
    planDialog.showModal();
  }

  const researchDialog = el('dialog', 'modal pw-research-dialog');
  researchDialog.setAttribute('aria-label', '在持仓组合编辑公司与策略');
  const researchForm = el('form', 'strategy-editor'); researchDialog.append(researchForm); document.body.append(researchDialog);
  researchForm.addEventListener('submit', saveStrategyCard);
  async function editResearch(item, card = null) {
    if (item.workspace?.cards_loaded === false) {
      if (!await loadCards(item)) return;
      card = card ? item.workspace.cards.find(row => row.id === card.id) : null;
    }
    if (detailsDialog.open) detailsDialog.close();
    card ||= item.workspace?.cards?.[0] || {name:item.name, target:target(item), kind:item.market === 'FUND' ? 'asset' : 'stock',
      risks:(item.analysis?.risks || []).join('\n')};
    const selected = portfolioContext.id;
    openStrategyEditor(card, {form:researchForm, close:() => researchDialog.close(), saved:async () => {
      researchDialog.close(); researchState.cards = null;
      try {
        if (selected === portfolioContext.id) await refreshDashboard();
        showToast('公司与策略已保存新版本', 'success');
      } catch (error) { showToast(`研究版本已保存，界面暂未刷新：${error.message}`, 'error'); }
    }});
    researchDialog.showModal();
    researchForm.elements.target.readOnly = true;
    const note = el('p', 'form-note', '公司与策略按标的共享；保存会新增研究版本。持仓用法、建仓日期与估值区间分别保存在当前组合。');
    researchForm.prepend(note); researchForm.elements.name.focus();
  }

  async function refreshMetrics(item, node) {
    const selected = portfolioContext.id; node.disabled = true; node.textContent = '正在更新…';
    try {
      const result = await api('/api/portfolio/research', {method:'POST', body:{action:'refresh', target:target(item), portfolio_id:selected}});
      if (!result.started) showToast('当前组合已有研究更新任务，等待完成');
      const poll = async () => {
        if (selected !== portfolioContext.id) return;
        try {
          const data = await api('/api/portfolio/research');
          if (selected !== portfolioContext.id) return;
          if (data.busy) { refreshTimer = setTimeout(poll, 2000); return; }
          await refreshDashboard();
          const errors = [data.error, ...(data.errors || [])].filter(Boolean);
          showToast(errors.length ? `更新完成，部分资料未取得：${errors.join('；')}` : '行业与PE资料已更新', errors.length ? 'error' : 'success');
        } catch (error) { showToast(error.message, 'error'); }
        finally { node.disabled = false; node.textContent = '更新行业 / PE'; }
      };
      clearTimeout(refreshTimer); await poll();
    } catch (error) { showToast(error.message, 'error'); node.disabled = false; node.textContent = '更新行业 / PE'; }
  }
  window.addEventListener('portfolio-change', () => {
    window.PortfolioRecords?.hide();
    if (mode === 'meetings' || mode === 'execution') { mode = 'holdings'; setMode(mode); }
    clearTimeout(refreshTimer); clearTimeout(analysisTimer); ++analysisVersion;
    analysisRows = {}; analysisKey = ''; analysisError = '';
    syncPortfolioExport();
    syncAnalysisSetup();
    profileActions.hidePopover?.();
    planDialog.close(); researchDialog.close(); detailsDialog.close();
  });
  let openSkillEditor;
  document.getElementById('pw-skills').onclick = event => {
    openSkillEditor ||= window.createAnalysisSkillEditor({
      endpoint:'/api/analysis-skills', title:'分析 Skill',
      description:'所有组合共用。上传 Markdown 或直接粘贴；留空使用默认分析。',
      sections:[['instrument','个股分析 Skill','用于股票、ETF 的分析与 PDF'],['portfolio','组合分析 Skill','用于全组合 AI 报告的综合研判']],
      afterSave:async () => { if (analysisActive(portfolioContext.id)) await requestAnalysis('read'); }
    });
    return openSkillEditor(event);
  };
  document.getElementById('pw-export-portfolio-analysis')?.addEventListener('click', event => exportAnalysis(event.currentTarget));
  document.getElementById('pw-ai-config-settings')?.addEventListener('click', () => {
    setView('settings');
    const field = document.getElementById('analysis-key-input');
    if (field?.disabled) document.getElementById('settings-analysis')?.scrollIntoView({block:'start'});
    else field?.focus();
  });
  window.PortfolioWorkspace = {render, setMode, getMode:() => mode, bindControls, showDetails, editPlan, editResearch, riskPrices, valuation, syncAnalysis, openProfileActions, analysisExportButton};
  bindControls();
})();
