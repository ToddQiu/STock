(function () {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key = r => `${r.market}:${r.symbol}`;
  const money = (v, currency) => v == null ? '--' : `${Number(v).toLocaleString('zh-CN', {minimumFractionDigits:currency === 'KRW' ? 0 : 2, maximumFractionDigits:currency === 'KRW' ? 0 : 2})} ${currency}`;
  const icon = (action, name, title, id = '') => `<button type="button" class="icon-button" data-dividend-action="${action}" data-id="${esc(id)}" title="${esc(title)}" aria-label="${esc(title)}">${name === 'trash-2' ? '<span class="trash-icon" aria-hidden="true"></span>' : `<svg class="ui-icon" aria-hidden="true"><use href="/icons.svg#${name}"></use></svg>`}</button>`;
  const root = document.createElement('section');
  root.id = 'portfolio-dividends'; root.className = 'investment-surface'; root.hidden = true;
  root.setAttribute('role', 'tabpanel'); root.setAttribute('aria-labelledby', 'investment-tab-dividends');
  document.getElementById('investment-original').after(root);
  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-dividend-dialog'; dialog.className = 'investment-dialog investment-surface';
  dialog.setAttribute('aria-labelledby', 'dividend-dialog-title'); document.body.append(dialog);
  let portfolio = {}, busy = false, filter = 'all', editing = null, selectedBook = '', renderedData = '';
  const records = () => portfolio.dividends?.records || [];
  const displayName = r => instrumentName((portfolio.positions || []).find(p => key(p) === key(r)) || r);
  const securities = () => [...new Map([...records(), ...(portfolio.positions || [])].map(r => [key(r), r])).values()];

  function recordRow(r) {
    const status = r.status === 'received' ? '已到账' : '待派发';
    const cash = r.status === 'expected' ? '未入账' : r.cash_action === 'credit' ? '已加入现金' : '仅登记';
    return `<tr><td>${esc(r.payment_date)}${r.ex_date ? `<small>除息 ${esc(r.ex_date)}</small>` : ''}</td>
      <td><strong>${esc(displayName(r))}</strong><small>${status} · ${esc(r.symbol)}</small>${r.note ? `<small>${esc(r.note)}</small>` : ''}</td>
      <td>${money(r.gross_amount,r.currency)}</td><td>${money(r.tax,r.currency)}<small>费用 ${money(r.fee,r.currency)}</small></td>
      <td>${money(r.net_amount,r.currency)}<small>${cash}</small></td>
      <td><div class="dividend-tools">${icon('edit','notebook-pen','编辑股息',r.id)}${icon('delete','trash-2','删除股息',r.id)}</div></td></tr>`;
  }

  function render() {
    const summary = portfolio.dividends || {}, base = portfolio.base_currency || 'CNY';
    const rows = records().filter(r => filter === 'all' || r.status === filter).slice().sort((a,b) => b.payment_date.localeCompare(a.payment_date));
    root.innerHTML = `<header class="dividend-heading"><h3>股息记录</h3><div class="dividend-tools"><select aria-label="股息状态" data-dividend-filter><option value="all">全部记录</option><option value="expected">待派发</option><option value="received">已到账</option></select>${icon('add','plus','登记股息')}</div></header>
      <dl class="dividend-totals"><div><dt>已收净股息</dt><dd>${money(summary.received_net_base,base)}</dd></div><div><dt>待派发（税前）</dt><dd>${money(summary.expected_gross_base,base)}</dd></div><div><dt>已登记</dt><dd>${summary.record_count || 0} 笔</dd></div></dl>
      <div class="dividend-table-wrap"><table class="dividend-table"><thead><tr><th>当前持仓</th><th>已收净股息</th><th>待派发（税前）</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>${(portfolio.positions || []).map(r => `<tr><td><strong>${esc(instrumentName(r))}</strong><small>${esc(r.symbol)}</small></td><td>${r.dividends?.received_count ? money(r.dividends.received_net_base,base) : '未登记'}</td><td>${money(r.dividends?.expected_gross_base,base)}</td><td>${icon('add','plus',`登记${instrumentName(r)}股息`,key(r))}</td></tr>`).join('')}</tbody></table></div>
      <div class="dividend-records">${rows.length ? `<div class="dividend-table-wrap"><table class="dividend-table"><thead><tr><th>派发 / 到账日</th><th>标的 / 状态</th><th>税前金额</th><th>扣税 / 费用</th><th>净额 / 现金</th><th>操作</th></tr></thead><tbody>${rows.map(recordRow).join('')}</tbody></table></div>` : '<p class="dividend-empty">暂无股息记录</p>'}</div>`;
    root.querySelector('[data-dividend-filter]').value = filter;
    root.querySelector('[data-dividend-action="add"]').disabled = !securities().length;
    root.querySelector('.dividend-totals').title = '仅汇总已登记记录；外币按当前参考汇率折合，不代表历史换汇收益';
  }

  function open(row = null, securityKey = '') {
    if (busy) return;
    editing = row; selectedBook = portfolioContext.id;
    const options = securities();
    const selected = row ? key(row) : securityKey || (options[0] && key(options[0]));
    if (!selected) return;
    const date = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai'}).format(new Date());
    const field = (label,name,value,type='number',attrs='') => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;
    dialog.innerHTML = `<form><header class="dividend-heading"><h3 id="dividend-dialog-title">${row ? '编辑' : '登记'}股息</h3>${icon('close','x','关闭')}</header>
      <div class="dividend-form-grid"><label class="dividend-wide">持仓<select name="security" ${row ? 'disabled' : ''}>${options.map(r => `<option value="${esc(key(r))}" ${key(r) === selected ? 'selected' : ''}>${esc(instrumentName(r))} · ${esc(r.symbol)}</option>`).join('')}</select></label>
      <label>状态<select name="status"><option value="received">已到账</option><option value="expected">待派发</option></select></label><label>到账币种<select name="currency">${['CNY','USD','HKD','KRW'].map(c => `<option>${c}</option>`).join('')}</select></label>
      ${field('派发 / 到账日','payment_date',row?.payment_date || date,'date','required')}${field('除息日（选填）','ex_date',row?.ex_date || '','date')}
      ${field('税前股息总额','gross_amount',row?.gross_amount ?? '','number','required min="0.01" max="1000000000000" step="0.01"')}${field('扣税总额','tax',row?.tax ?? '','number','min="0" max="1000000000000" step="0.01" placeholder="无扣税填 0"')}
      ${field('费用','fee',row?.fee ?? 0,'number','min="0" max="1000000000000" step="0.01"')}<label>净到账<output name="net">--</output></label>
      <label class="dividend-wide">现金处理<select name="cash_action"><option value="record_only">仅登记，不改现金</option><option value="credit">净额加入组合现金</option></select></label>
      ${field('备注 / 凭证（选填）','note',row?.note || '','text','maxlength="300" class="dividend-note"')}</div>
      <p class="dividend-hint">按实际到账登记，不扣减原始均价。ETF 只记基金派给你的分红。</p><p class="investment-error" role="alert"></p>
      <footer class="dividend-footer"><button type="submit" class="primary-button">保存股息</button></footer></form>`;
    const f = dialog.querySelector('form').elements;
    f.status.value = row?.status || 'received'; f.currency.value = row?.currency || options.find(r => key(r) === selected)?.currency || 'CNY';
    f.cash_action.value = row?.cash_action || 'record_only';
    dialog.dataset.recordId = row?.id || crypto.randomUUID();
    preview(); dialog.showModal();
  }

  function preview() {
    const f = dialog.querySelector('form').elements, received = f.status.value === 'received';
    f.tax.required = received; f.fee.required = received;
    f.cash_action.disabled = !received;
    if (!received) f.cash_action.value = 'record_only';
    const unit = f.currency.value === 'KRW' ? '1' : '0.01';
    f.gross_amount.min = unit;
    for (const name of ['gross_amount','tax','fee']) f[name].step = unit;
    const gross = f.gross_amount.valueAsNumber, tax = f.tax.valueAsNumber, fee = f.fee.valueAsNumber;
    f.net.value = [gross,tax,fee].every(Number.isFinite) && gross >= tax + fee ? money(gross-tax-fee,f.currency.value) : '--';
    f.cash_action.title = '现金已包含这笔股息时选“仅登记”；修改或删除已加入现金的记录会同步调整现金';
  }

  root.addEventListener('change', e => {if (e.target.matches('[data-dividend-filter]')) {filter=e.target.value;render();}});
  root.addEventListener('click', async e => {
    const b = e.target.closest('[data-dividend-action]'); if (!b || busy) return;
    const row = records().find(r => r.id === b.dataset.id);
    if (b.dataset.dividendAction === 'add') return open(null,b.dataset.id);
    if (b.dataset.dividendAction === 'edit') return open(row);
    if (!row || !window.confirm(`删除${displayName(row)} ${row.payment_date}的股息记录？${row.cash_action === 'credit' ? '将同时从组合现金撤回 '+money(row.net_amount,row.currency)+'。' : '不改变组合现金。'}`)) return;
    busy=true; b.disabled=true;
    try {await api(`/api/portfolio/dividends/${row.id}`,{method:'DELETE',body:{confirmed:true}}); await refreshDashboard(); showToast('股息记录已删除');}
    catch(error) {showToast(error.message,'error');}
    finally {busy=false; b.disabled=false;}
  });
  dialog.addEventListener('click', e => {if (!busy && e.target.closest('[data-dividend-action="close"]')) dialog.close();});
  dialog.addEventListener('cancel', e => {if (busy) e.preventDefault();});
  dialog.addEventListener('input', preview);
  dialog.addEventListener('change', e => {
    if (e.target.name === 'security') dialog.querySelector('form').elements.currency.value = securities().find(r => key(r) === e.target.value)?.currency || 'CNY';
    preview();
  });
  dialog.addEventListener('submit', async e => {
    e.preventDefault(); if (busy || selectedBook !== portfolioContext.id) return;
    const f = e.target.elements, security = securities().find(r => key(r) === f.security.value);
    const payload = {id:dialog.dataset.recordId, market:security.market, symbol:security.symbol};
    for (const k of ['status','currency','payment_date','ex_date','gross_amount','tax','fee','cash_action','note']) payload[k]=f[k].value;
    const changesCash = payload.cash_action === 'credit' || editing?.cash_action === 'credit';
    if (changesCash && !window.confirm('本次保存会同步调整组合现金，请确认这笔股息没有重复计入现金。')) return;
    busy=true;
    const buttons=[...dialog.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    dialog.querySelector('[role="alert"]').textContent='';
    try {await api('/api/portfolio/dividends',{method:'POST',body:payload}); dialog.close(); await refreshDashboard(); showToast('股息已保存，原始均价不变');}
    catch(error) {dialog.querySelector('[role="alert"]').textContent=error.message;}
    finally {busy=false;buttons.forEach(b=>b.disabled=false);}
  });
  window.addEventListener('portfolio-change', () => {dialog.close(); portfolio={};filter='all';renderedData='';render();});
  window.PortfolioDividends = {update(value) {
    portfolio=value || {};
    const data=JSON.stringify([portfolio.base_currency,portfolio.dividends,(portfolio.positions || []).map(r=>[key(r),instrumentName(r),r.dividends])]);
    if(data!==renderedData) {renderedData=data;render();}
  }};
  window.PortfolioDividends.update(state.dashboard?.portfolio);
})();
