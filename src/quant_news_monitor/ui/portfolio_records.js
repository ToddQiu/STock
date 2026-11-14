(function () {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog = document.getElementById('portfolio-records-panel');
  let selected = '', data = {}, part = 'meetings', editing = '', busy = false, request = 0;
  const input = (label, key, value, type = 'text') => `<label>${label}<input name="${key}" type="${type}" value="${esc(value)}" ${type === 'number' ? 'min="0" step="any"' : 'maxlength="120"'}></label>`;
  const area = (label, key, value) => `<label>${label}<textarea name="${key}" maxlength="6000" rows="3">${esc(value)}</textarea></label>`;
  function select(label, key, value, choices) {
    const options = value && !choices.includes(value) ? [...choices, value] : choices;
    return `<label>${label}<select name="${key}"><option value="">未填写</option>
      ${options.map(choice => `<option value="${esc(choice)}"${choice === value ? ' selected' : ''}>${esc(choice)}</option>`).join('')}
    </select></label>`;
  }
  function showQuotaEstimate(form, market) {
    const average = form.elements[`${market}_average20_value`];
    const output = form.elements[`${market}_quota_estimate`];
    const renderEstimate = () => {
      const value = average.valueAsNumber;
      if (!Number.isFinite(value) || value < 0 || value > 1e15) {
        output.value = average.value ? '请填写有效市值' : '待填实际市值';
        return;
      }
      const estimate = value < 10000 ? 0 : Math.floor(value / 5000) * 500;
      output.value = `${estimate.toLocaleString('zh-CN')} 股`;
    };
    average.addEventListener('input', renderEstimate);
    renderEstimate();
  }
  const values = form => JSON.stringify([...new FormData(form)].filter(([key]) => key !== 'meeting_id'));
  function discard() {
    const form = dialog.querySelector('form');
    return !form || form.dataset.original === values(form) || window.confirm('有未保存的修改，放弃修改并切换？');
  }
  function render() {
    const row = (data.meetings || []).find(item => item.id === editing) || {}, ex = data.execution || {};
    let body;
    if (part === 'meetings') body = `<label>已保存例会<select name="meeting_id"><option value="">新建例会</option>${(data.meetings || []).map(m => `<option value="${esc(m.id)}" ${m.id === editing ? 'selected' : ''}>${esc(m.date || '未填日期')} · ${esc(m.name)}</option>`).join('')}</select></label>
      <div class="form-grid two-columns">${input('会议名称','name',row.name)}${select('会议类型','kind',row.kind,['晨会','周会','月会'])}${input('会议日期','date',row.date,'date')}<p class="form-note">例会编号：${esc(row.id || '保存后自动生成')}</p></div>
      ${[['会议纪要','minutes'],['会议结论','conclusion'],['调研讨论','research'],['个股讨论','stocks']].map(([label,key]) => area(label,key,row[key])).join('')}`;
    else body = `${input('打新资金上限（人民币元）','budget',ex.budget,'number')}${area('执行说明','notes',ex.notes)}<p class="form-note">金额单位为人民币元。模板推算额度随实际 20 日均市值更新，券商核实结果单独填写。历史导入额度请按执行说明复核。</p><div class="records-markets">${[['SH','沪市'],['SZ','深市']].map(([market,label]) => {
      const item = ex.markets?.[market] || {};
      return `<fieldset><legend>${label}</legend>
        ${input('目标普通股市值',`${market}_target_value`,item.target_value,'number')}
        ${input('整手模拟市值',`${market}_simulated_value`,item.simulated_value,'number')}
        ${input('实际20日均市值',`${market}_average20_value`,item.average20_value,'number')}
        <p class="form-note">模板推算额度 <output name="${market}_quota_estimate"></output></p>
        ${select('主板权限',`${market}_permission`,item.permission,['待确认','已开通','未开通'])}
        ${input('已录入额度（股）',`${market}_quota`,item.quota,'number')}
        <label>当前状态<textarea name="${market}_status" maxlength="500" rows="2">${esc(item.status)}</textarea></label>
      </fieldset>`;
    }).join('')}</div>`;
    dialog.innerHTML = `<article><header class="trade-heading"><h2>${part === 'meetings' ? '例会管理' : '打新与执行'} · ${esc(state.dashboard?.portfolio?.name || '')}</h2></header><form>${body}<p role="alert" class="negative"></p><footer class="modal-actions">${part === 'meetings' && editing ? '<button type="button" class="secondary-button danger-button" data-delete>删除例会</button>' : ''}<span class="form-note">保存到当前组合，随“导出复盘”写入 Excel。</span><button type="submit" class="primary-button">${part === 'meetings' ? '保存例会' : '保存执行记录'}</button></footer></form></article>`;
    const form = dialog.querySelector('form');
    if (part === 'meetings') {
      form.elements.name.required = true;
      form.elements.meeting_id.onchange = event => {if (busy || !discard()) {event.target.value = editing; return;} editing = event.target.value; render();};
    } else {
      for (const market of ['SH','SZ']) showQuotaEstimate(form, market);
    }
    dialog.querySelector('[data-delete]')?.addEventListener('click', () => {if (window.confirm('删除这条例会记录？')) save({action:'meeting_delete',id:editing});});
    form.onsubmit = event => {
      event.preventDefault(); const fields = Object.fromEntries(new FormData(form));
      if (part === 'meetings') save({...fields,action:'meeting_save',id:editing});
      else {
        const execution = {budget:fields.budget,notes:fields.notes,markets:{}};
        for (const market of ['SH','SZ']) execution.markets[market] = Object.fromEntries(['target_value','simulated_value','average20_value','quota','permission','status'].map(key => [key,fields[`${market}_${key}`]]));
        save({action:'execution_save',execution});
      }
    };
    form.dataset.original = values(form);
  }
  async function save(body) {
    if (busy) return;
    const form = dialog.querySelector('form'), alert = form.querySelector('[role="alert"]');
    busy = true; dialog.querySelectorAll('button,select').forEach(n => n.disabled = true);
    try {
      if (selected !== portfolioContext.id) throw Error('组合已切换，请重新打开');
      data = await api('/api/portfolio/records', {method:'POST',body:{...body,portfolio_id:selected}});
      if (body.action === 'meeting_delete') editing = '';
      else if (body.action === 'meeting_save') editing = body.id || data.meetings.at(-1).id;
      if (selected === portfolioContext.id && !dialog.hidden) render();
      showToast('组合记录已保存','success');
    } catch (error) {alert.textContent = error.message;}
    finally {busy = false; dialog.querySelectorAll('button,select').forEach(n => n.disabled = false);}
  }
  async function open(section = 'meetings') {
    if (portfolioContext.switching || portfolioContext.writes) return;
    selected = portfolioContext.id; const current = ++request, book = selected; editing = ''; part = section === 'execution' ? 'execution' : 'meetings';
    dialog.hidden = false; dialog.setAttribute('aria-labelledby', 'pw-tab-' + part); dialog.textContent = '正在读取组合记录…';
    try {
      data = await api(`/api/portfolio/records?portfolio_id=${encodeURIComponent(book)}`);
      if (current !== request || book !== portfolioContext.id) return;
      render();
    } catch (error) {if (current === request && book === portfolioContext.id) {dialog.textContent = error.message; showToast(error.message,'error');}}
  }
  function hide() { ++request; dialog.hidden = true; }
  function canLeave() { return dialog.hidden || (!busy && discard()); }
  window.addEventListener('portfolio-change', hide);
  window.PortfolioRecords = {open, hide, canLeave};
})();
