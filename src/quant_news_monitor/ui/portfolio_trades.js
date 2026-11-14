(function () {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (value, currency, signed = false) => !finite(value) ? '--' : (signed && value > 0 ? '+' : '') + formatPortfolioMoney(value, currency);
  const requestId = () => window.crypto?.randomUUID?.() || `trade-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  let portfolio = {}, selected = '', busy = false, editing = null, operation = '', detailRequest = 0;
  const dialog = el('dialog', 'modal portfolio-trades-dialog'); dialog.id = 'portfolio-trades-dialog';
  dialog.setAttribute('aria-label', '成交账本与已实现净盈亏'); document.body.append(dialog);
  const manual = el('dialog', 'modal portfolio-trade-manual-dialog'); manual.id = 'portfolio-trade-manual-dialog';
  manual.setAttribute('aria-label', '补录历史成交或费用'); document.body.append(manual);

  function update(value) {
    const changed = selected && selected !== (value?.id || portfolioContext.id);
    if (changed) { dialog.close(); manual.close(); editing = null; }
    portfolio = value || {}; selected = portfolio.id || portfolioContext.id;
    const summary = portfolio.trades?.summary;
    const amount = summary?.realized_net_base;
    const valueNode = document.getElementById('portfolio-realized-pnl');
    const status = document.getElementById('portfolio-realized-status');
    const tile = document.getElementById('portfolio-realized-tile');
    valueNode.textContent = money(amount, portfolio.base_currency || 'CNY', true);
    valueNode.className = finite(amount) && amount > 0 ? 'positive' : finite(amount) && amount < 0 ? 'negative' : '';
    const pctNode = document.getElementById('portfolio-realized-pct'), pct = summary?.realized_return_pct;
    pctNode.textContent = finite(pct) ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '--';
    pctNode.className = finite(pct) && pct > 0 ? 'positive' : finite(pct) && pct < 0 ? 'negative' : '';
    pctNode.title = '累计已实现净盈亏 ÷ 已卖出部分对应成本（含未计入均价的买入费用）；仅统计已记录卖出，费用或汇率缺失、尚无卖出成本时不计算。';
    tile.classList.toggle('positive', finite(amount) && amount > 0); tile.classList.toggle('negative', finite(amount) && amount < 0);
    const missing = Number(summary?.incomplete_count || 0);
    status.textContent = !summary ? '等待成交账本' : missing ? `${missing} 笔费用待核 · 查看明细`
      : summary.fx_complete === false ? '缺换汇数据 · 查看原币'
      : `已记录 ${Number(summary.sell_count || 0)} 笔卖出 · 查看明细`;
    valueNode.title = status.title = `${valueNode.textContent}；${status.textContent}。累计仅包含已记录成交，不自动推测旧交易。`;
    document.getElementById('portfolio-realized-open').setAttribute('aria-label', `累计已实现净盈亏 ${valueNode.textContent}，${status.textContent}`);
    if (dialog.open) open();
  }

  function recordRow(row) {
    const sell = row.action === 'sell';
    const dividend = row.action === 'dividend';
    const action = row.trade_type || (sell ? row.closed ? '清仓' : '卖出' : '买入');
    const fees = finite(row.fee) ? money(row.fee + (row.extra_buy_fee || 0), row.currency) : '费用待补';
    const feeNote = dividend ? '扣税及费用' : sell && !row.fee_complete ? '成本或费用未核对' : !sell ? '已知买费计入均价' : '未重复扣已含成本的买费';
    const net = !sell ? '不计已实现' : money(row.net_pnl, row.currency, true);
    const unitCost = sell ? row.average_cost : finite(row.basis_amount) && row.quantity > 0 ? row.basis_amount / row.quantity : null;
    const converted = sell && finite(row.net_pnl_base) ? `参考折合 ${money(row.net_pnl_base, portfolio.base_currency, true)}` : sell && finite(row.net_pnl) ? '缺换汇数据，保留原币净额' : '';
    const rationale = dividend ? '<button type="button" class="text-button" data-trade-dividends>查看股息</button>' : `<details class="trade-rationale"><summary>分析与买卖原因</summary><dl>${[
      ['买入分析', 'entry_analysis'], ['买入原因', 'buy_reason'],
      ...(sell ? [['卖出分析', 'exit_analysis'], ['卖出原因', 'sell_reason']] : []),
    ].map(([label, key]) => `<dt>${label}</dt><dd>${esc(row[key] || '未填写')}</dd>`).join('')}</dl></details><button type="button" class="text-button" data-trade-template="${esc(row.id)}">模板资料</button>`;
    const closedReturn = sell && row.closed ? `<small>清仓后总收益率 ${finite(row.closed_return_pct) ? `${row.closed_return_pct > 0 ? '+' : ''}${row.closed_return_pct.toFixed(2)}%` : '待补齐'}</small>${row.closed_return_status ? `<small>${esc(row.closed_return_status)}</small>` : ''}` : '';
    return `<tr><td>${esc(row.trade_date)}${row.trade_time ? ` ${esc(row.trade_time)}` : ''}<small>${esc(action)}${row.origin === 'manual' ? ' · 历史补录' : ''}</small>${row.closed_date ? `<small>清仓日期 ${esc(row.closed_date)}</small>` : ''}${finite(row.holding_days) ? `<small>持仓 ${row.holding_days} 天</small>` : ''}</td>
      <td>${esc(instrumentName(row))}<small>${esc(row.market)}:${esc(row.symbol)} · ${esc(row.currency)}</small><small class="trade-record-id">编号 ${esc(row.id)}</small>${row.note ? `<small>${esc(row.note)}</small>` : ''}${rationale}</td>
      <td>${dividend ? '到账股息' : `${esc(row.quantity)}<small>成交价 ${money(row.execution_price,row.currency)}</small>`}<small>${dividend ? '税前金额' : '交易资金'} ${money(row.gross_amount,row.currency)}</small></td>
      <td>${money(row.basis_amount ?? row.cost_amount,row.currency)}${dividend ? '' : `<small>单位成本 ${money(unitCost,row.currency)}</small>`}<small>资金变动 ${money(row.cash_change,row.currency,true)}</small></td>
      <td>${fees}<small>${esc(feeNote)}</small>${row.fees_completed_at ? '<small>费用已补全</small>' : ''}</td>
      <td class="${sell && finite(row.net_pnl) ? row.net_pnl > 0 ? 'positive' : row.net_pnl < 0 ? 'negative' : '' : ''}">${net}<small>${converted}</small>${closedReturn}
      ${sell && !row.fee_complete ? `<button type="button" class="text-button" data-complete-fees="${esc(row.id)}">补全费用</button>` : ''}</td></tr>`;
  }

  function render() {
    const summary = portfolio.trades?.summary || {}, records = portfolio.trades?.records || [], base = portfolio.base_currency || 'CNY';
    const incomplete = Number(summary.incomplete_count || 0);
    const coverage = incomplete ? `${incomplete}笔成本或费用未核对，完整累计暂不计算。` : summary.fx_complete === false ? '换汇数据不全，完整折合累计暂不计算。' : '已扣除已记录且未计入成本的相关买卖费用。';
    dialog.innerHTML = `<article><header class="trade-heading"><h2>成交账本 · 已实现净盈亏</h2><button type="button" class="secondary-button" data-trades-close>关闭</button></header>
      <div class="trade-summary"><span>累计已实现净盈亏<strong>${money(summary.realized_net_base,base,true)}</strong></span><span>已登记买卖费用<strong>${money(summary.fees_base,base)}</strong></span><span>已记录卖出<strong>${Number(summary.sell_count || 0)}笔</strong></span></div>
      <p class="form-note">${esc(coverage)} ${finite(summary.known_realized_net_base) && !finite(summary.realized_net_base) ? `已知部分 ${money(summary.known_realized_net_base,base,true)}，不是完整累计。` : ''}</p>
      <div class="trade-table-scroll"><table><thead><tr><th>成交日期</th><th>标的</th><th>数量 / 成交价</th><th>对应成本</th><th>费用</th><th>已实现净盈亏</th></tr></thead><tbody>${records.length ? records.slice().sort((a,b) => String(b.trade_date).localeCompare(String(a.trade_date)) || String(b.recorded_at).localeCompare(String(a.recorded_at))).map(recordRow).join('') : '<tr><td colspan="6">尚无成交记录；过去已清仓但未保存的交易可手工补录。</td></tr>'}</tbody></table></div>
      <p class="form-note">清仓后总收益率按整轮原币扣费净盈亏除以买入总成本计算。到账股息沿用股息记录，单列展示。资金变动为账本现金流，现金余额以账户设置为准。</p>
      <footer class="trade-footer"><button type="button" class="primary-button" data-manual-trade>补录历史成交</button><button type="button" class="secondary-button" data-trade-dividends>股息管理</button><span class="quiet-note">补录仅保存到账本。</span></footer></article>`;
  }

  async function open() {
    const request = ++detailRequest, book = selected;
    if (portfolio.trades?.records_loaded !== false) { render(); if (!dialog.open) dialog.showModal(); return; }
    dialog.innerHTML = '<article><header class="trade-heading"><h2>成交账本 · 已实现净盈亏</h2><button type="button" class="secondary-button" data-trades-close>关闭</button></header><p role="status">正在读取成交明细…</p></article>';
    if (!dialog.open) dialog.showModal();
    try {
      const data = await api('/api/portfolio/details');
      if (request !== detailRequest || book !== portfolioContext.id || data.portfolio_id !== book || !dialog.open) return;
      portfolio = {...portfolio, trades:data.trades}; render();
    } catch (error) {
      if (request !== detailRequest || book !== portfolioContext.id || !dialog.open) return;
      dialog.innerHTML = `<article><header class="trade-heading"><h2>成交账本 · 已实现净盈亏</h2><button type="button" class="secondary-button" data-trades-close>关闭</button></header><p role="alert">${esc(error.message)}</p><button type="button" class="primary-button" data-trades-retry>重新读取</button></article>`;
    }
  }

  function openTemplate(row) {
    if (!row || busy) return;
    const book = selected;
    const field = (label,key,type='text') => `<label>${label}<input name="${key}" type="${type}" value="${esc(row[key])}" ${type === 'number' ? 'step="any"' : type === 'time' ? 'step="1"' : ''}></label>`;
    manual.innerHTML = `<form><h2>${esc(instrumentName(row))} · 成交模板资料</h2><div class="form-grid two-columns">${field('账户名称','account_name')}${field('交易时间（账单当地时间）','trade_time','time')}${field('建仓日期','opened_date','date')}${field('当期总收益率（% · 按账单）','period_return_pct','number')}</div><label>收益率口径（填写收益率时必填，含起止日期）<textarea name="period_return_basis" maxlength="2000" rows="3">${esc(row.period_return_basis)}</textarea></label><label>交易数量计算（方法或依据）<textarea name="quantity_calculation" maxlength="2000" rows="3">${esc(row.quantity_calculation)}</textarea></label><p class="form-note">成交日期：${esc(row.trade_date)}；持仓天数按建仓日期与成交日期计算。这些资料不更改成交数量、费用或实现盈亏。</p><p role="alert" class="negative"></p><div class="modal-actions"><button type="button" class="secondary-button" data-manual-close>取消</button><button type="submit" class="primary-button">保存模板资料</button></div></form>`;
    const form = manual.querySelector('form');
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      busy = true; form.querySelectorAll('button').forEach(n => n.disabled = true);
      try {
        if (book !== portfolioContext.id) throw Error('组合已切换，请重新打开');
        await api('/api/portfolio/trades', {method:'POST',body:{...Object.fromEntries(new FormData(form)),action:'template_update',id:row.id,portfolio_id:book}});
        manual.close(); await refreshDashboard(); showToast('成交模板资料已保存','success');
      } catch (error) {form.querySelector('[role="alert"]').textContent = error.message;}
      finally {busy = false; form.querySelectorAll('button').forEach(n => n.disabled = false);}
    };
    dialog.close(); manual.showModal();
  }

  function openManual(record = null) {
    if (busy) return;
    editing = record; operation = requestId();
    const row = record || {}, currencies = ['CNY','HKD','USD','KRW'];
    const market = row.market || portfolio.positions?.[0]?.market || 'US';
    const currency = row.currency || ({CN:'CNY',HK:'HKD',US:'USD',KR:'KRW',FUND:'CNY'})[market] || 'USD';
    const options = (values, chosen) => values.map(v => `<option value="${v}" ${v === chosen ? 'selected' : ''}>${v}</option>`).join('');
    const input = (label, name, type, value = '', required = true) => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${required ? 'required' : ''} ${type === 'number' ? `min="${['fee','extra_buy_fee'].includes(name) ? '0' : '0.000001'}" max="1000000000000" step="any"` : ''}></label>`;
    manual.innerHTML = `<form id="portfolio-trade-manual-form"><h2>${record ? '补全已记录卖出的费用' : '补录历史成交'}</h2><div class="form-grid two-columns">
      ${record ? '' : '<label>成交方向<select name="manual_action"><option value="sell">卖出 / 清仓</option><option value="buy">建仓 / 买入</option></select></label>'}
      <label>市场<select name="market">${options(['US','CN','HK','KR','FUND'],market)}</select></label><label>金额币种（随市场）<select name="currency" disabled>${options(currencies,currency)}</select></label>
      ${input('证券代码','symbol','text',row.symbol || '')}${input('证券名称（可选）','name','text',row.name || '',false)}
      ${input('成交日期（按账单）','trade_date','date',row.trade_date || '')}${input('成交数量','quantity','number',row.quantity ?? '')}
      ${input('对应单位平均成本','average_cost','number',row.average_cost ?? '')}${input('成交价格','execution_price','number',row.execution_price ?? '')}
      ${input('成交费用合计','fee','number',row.fee ?? '')}${input('本次分摊买入费用（未含均价部分）','extra_buy_fee','number',row.extra_buy_fee ?? 0)}
      ${record ? '' : input('本轮建仓日期（用于整轮收益计算）','opened_date','date','',false)}
      </div><label class="risk-checkbox"><input type="checkbox" name="cost_fees_confirmed" required>已核对成本及费用，已含均价的买费不再重复填写。</label>
      <label class="risk-checkbox"><input type="checkbox" name="closed" ${row.closed ? 'checked' : ''}>这是该轮持仓的最后一笔卖出（清仓）。</label>
      ${record ? '' : '<label class="risk-checkbox"><input type="checkbox" name="opening">这是该轮持仓的第一笔买入（建仓）。</label>'}
      <label>买入分析（历史可补录）<textarea name="entry_analysis" maxlength="2000" rows="2">${esc(row.entry_analysis || '')}</textarea></label>
      <label>买入原因（历史可补录）<textarea name="buy_reason" maxlength="2000" rows="2">${esc(row.buy_reason || '')}</textarea></label>
      <label>卖出分析（选填）<textarea name="exit_analysis" maxlength="2000" rows="2">${esc(row.exit_analysis || '')}</textarea></label>
      <label>卖出原因${record ? '' : '（必填）'}<textarea name="sell_reason" maxlength="2000" rows="2" ${record ? '' : 'required'}>${esc(row.sell_reason || '')}</textarea></label>
      <label>备注<textarea name="note" maxlength="2000" rows="2">${esc(row.note || '')}</textarea></label>
      <p class="form-note">所有金额使用同一币种；依据真实账单填写。未登记的旧交易不会自动推算，本次操作不改变当前持仓、均价或现金。</p>
      <p class="negative" role="alert"></p><div class="modal-actions"><button type="button" class="secondary-button" data-manual-close>取消</button><button type="submit" class="primary-button">${record ? '确认补全费用' : '保存历史成交'}</button></div></form>`;
    const form = manual.querySelector('form');
    if (record) for (const key of ['market','currency','symbol','name','trade_date','quantity','average_cost','execution_price','closed']) form.elements[key].disabled = true;
    if (record) for (const key of ['note','entry_analysis','buy_reason','exit_analysis','sell_reason']) form.elements[key].readOnly = true;
    if (!record) {
      const syncType = () => {
        const selling = form.elements.manual_action.value !== 'buy';
        for (const key of ['average_cost','extra_buy_fee','cost_fees_confirmed','closed','exit_analysis','sell_reason','opening']) {
          const field = form.elements[key], visible = key === 'opening' ? !selling : selling;
          field.closest('label').hidden = field.disabled = !visible;
          field.required = visible && ['average_cost','extra_buy_fee','cost_fees_confirmed','sell_reason'].includes(key);
        }
      };
      form.elements.manual_action.addEventListener('change', syncType);
      syncType();
    }
    form.addEventListener('submit', saveManual);
    form.elements.market.addEventListener('change', () => { form.elements.currency.value = ({CN:'CNY',HK:'HKD',US:'USD',KR:'KRW',FUND:'CNY'})[form.elements.market.value]; });
    dialog.close(); manual.showModal();
  }

  async function saveManual(event) {
    event.preventDefault(); if (busy || selected !== portfolioContext.id) return;
    const form = event.currentTarget, fields = form.elements;
    const selling = editing || fields.manual_action.value !== 'buy';
    const body = {action:editing ? 'complete_fees' : selling ? 'sell' : 'buy', request_id:operation, portfolio_id:selected, fee:fields.fee.value};
    if (selling) {
      body.extra_buy_fee = fields.extra_buy_fee.value;
      body.cost_fees_confirmed = fields.cost_fees_confirmed.checked;
    }
    if (editing) body.id = editing.id;
    else {
      for (const key of ['market','currency','symbol','name','trade_date','quantity','execution_price','note']) body[key] = fields[key].value;
      if (selling) { body.average_cost = fields.average_cost.value; body.closed = fields.closed.checked; }
      else body.opening = fields.opening.checked;
      if (fields.opened_date.value) body.opened_date = fields.opened_date.value;
    }
    busy = true; const buttons = [...form.querySelectorAll('button')]; buttons.forEach(node => { node.disabled = true; });
    form.querySelector('[role="alert"]').textContent = '';
    try {
      if (!editing) {
        if (selling) Object.assign(body, positionRationale(new FormData(form), 'sell'));
        for (const key of ['entry_analysis','buy_reason']) {
          const text = fields[key].value;
          if (text.length > 2000) throw Error('买入分析与原因须为不超过2000字的文字');
          if (key === 'entry_analysis' || text.trim()) body[key] = text.trim();
        }
      }
      await api('/api/portfolio/trades', {method:'POST',body}); manual.close();
      try { await refreshDashboard(); showToast(editing ? '成交费用已补全' : '历史成交已保存', 'success'); }
      catch (error) { showToast(`记录已保存，界面暂未刷新：${error.message}`, 'error'); }
    } catch (error) { form.querySelector('[role="alert"]').textContent = error.message; }
    finally { busy = false; buttons.forEach(node => { node.disabled = false; }); }
  }

  dialog.addEventListener('click', event => {
    if (event.target.closest('[data-trades-retry]')) open();
    if (event.target.closest('[data-trades-close]')) dialog.close();
    if (event.target.closest('[data-trade-dividends]')) { dialog.close(); window.Investment?.selectSection('dividends'); }
    if (event.target.closest('[data-manual-trade]')) openManual();
    const complete = event.target.closest('[data-complete-fees]');
    if (complete) openManual((portfolio.trades?.records || []).find(row => row.id === complete.dataset.completeFees));
    const template = event.target.closest('[data-trade-template]');
    if (template) openTemplate((portfolio.trades?.records || []).find(row => row.id === template.dataset.tradeTemplate));
  });
  manual.addEventListener('click', event => { if (!busy && event.target.closest('[data-manual-close]')) manual.close(); });
  manual.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  document.getElementById('portfolio-realized-open').addEventListener('click', open);
  window.addEventListener('portfolio-change', () => { detailRequest++; dialog.close(); manual.close(); portfolio = {}; selected = portfolioContext.id; editing = null; update(portfolio); });
  window.PortfolioTrades = {update, open, openManual};
  update(state.dashboard?.portfolio);
})();
