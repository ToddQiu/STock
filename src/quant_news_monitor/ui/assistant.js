(function () {
  'use strict';
  const icon = name => uiIcon(name).outerHTML;
  const launcher = document.createElement('button');
  launcher.id = 'assistant-launch'; launcher.type = 'button'; launcher.className = 'assistant-launch';
  launcher.setAttribute('aria-controls', 'stock-assistant'); launcher.setAttribute('aria-expanded', 'false');
  launcher.innerHTML = icon('message-circle') + '<span>STock Ai</span>';
  const drawer = document.createElement('aside');
  drawer.id = 'stock-assistant'; drawer.hidden = true; drawer.setAttribute('aria-labelledby', 'assistant-title');
  drawer.innerHTML = `<header class="assistant-header"><div><h2 id="assistant-title">STock Ai</h2><p id="assistant-model-status" role="status"></p></div><button type="button" id="assistant-close" class="icon-button" title="关闭助手" aria-label="关闭助手">${icon('x')}</button></header>
    <div class="assistant-tools"><button type="button" id="assistant-explain" class="text-button">解释当前回测</button><button type="button" id="assistant-clear" class="text-button">清空对话</button></div>
    <div id="assistant-thread" aria-label="本页问答记录"><section class="assistant-welcome"><h3>先把规则弄明白</h3><p>金融基础、软件用法与回测解释。只读，不下单；不查询实时价格。</p><div class="assistant-suggestions"><button type="button" data-assistant-question="回测系统是什么？">回测是什么？</button><button type="button" data-assistant-question="最大回撤和收益率有什么区别？">回撤与收益</button><button type="button" data-assistant-question="股息率和派息率有什么区别？">股息与派息</button><button type="button" data-assistant-question="为什么持仓相关性需要历史数据？">持仓相关性</button></div></section></div>
    <p id="assistant-progress" role="status" aria-live="polite"></p><p id="assistant-error" role="alert"></p>
    <form id="assistant-form"><label for="assistant-question">你的问题</label><textarea id="assistant-question" rows="3" maxlength="500" placeholder="例如：为什么有信号却没有成交？" required></textarea><div class="assistant-compose"><span id="assistant-count">0 / 500</span><button type="button" id="assistant-stop" class="text-button" hidden>停止回答</button><button id="assistant-send" type="submit" class="primary-button">${icon('arrow-up')}<span>提问</span></button></div></form>
    <p class="assistant-privacy">本机离线解释 · 每问独立回答 · 对话不写入磁盘<br>服务内存最多保留10分钟；刷新或切换组合清空页面记录。</p>`;
  const sidebarFooter = document.querySelector('.sidebar-footer');
  const dock = document.createElement('div'); dock.className = 'assistant-dock';
  sidebarFooter.before(dock); dock.append(launcher); document.body.append(drawer);
  const q = id => drawer.querySelector('#assistant-' + id), thread = q('thread');
  const welcome = thread.innerHTML;
  let active = null, busy = false, generation = 0, timer = null, trigger = launcher;
  const text = (tag, content, className) => { const el = document.createElement(tag); el.textContent = content; if (className) el.className = className; return el; };
  async function request(path, body) {
    const controller = new AbortController(), deadline = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch('/api/assistant/' + path, {method:body === undefined ? 'GET' : 'POST', headers:body === undefined ? {} : {'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal, cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || '助手暂时不可用');
      return data;
    } finally { clearTimeout(deadline); }
  }
  async function status() {
    try {
      const data = await request('status');
      q('model-status').textContent = data.installed ? '' : '模型未安装 · 当前仅检索本地手册';
    } catch (_) { q('model-status').textContent = '本地服务未连接'; }
  }
  function open(source = launcher) {
    trigger = source; drawer.hidden = false; launcher.setAttribute('aria-expanded', 'true');
    q('explain').disabled = busy || !window.getBacktestAssistantContext?.();
    status(); q('question').focus();
  }
  function close() { drawer.hidden = true; launcher.setAttribute('aria-expanded', 'false'); trigger?.focus(); }
  launcher.onclick = () => drawer.hidden ? open() : close(); q('close').onclick = close;
  drawer.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } });
  function setBusy(value) {
    busy = value; q('send').disabled = value; q('stop').hidden = !value;
    q('question').readOnly = value; q('explain').disabled = value || !window.getBacktestAssistantContext?.();
    drawer.querySelectorAll('[data-assistant-question]').forEach(b => b.disabled = value);
  }
  function scroll() { thread.scrollTop = thread.scrollHeight; }
  function navigate(ref) {
    if (!['backtest','portfolio','settings'].includes(ref.view)) return;
    if (ref.view === 'backtest' && ['workbench','tutorial','rules'].includes(ref.tab)) window.BacktestWorkspace?.select(ref.tab);
    setView(ref.view); close();
    const heading = document.querySelector(`.view[data-view="${ref.view}"] h2`);
    if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus(); }
  }
  function answer(data) {
    const section = document.createElement('section'); section.className = 'assistant-answer';
    section.append(text('h3', data.mode === 'local_model' ? '本地模型整理' : '手册检索'));
    section.append(text('p', data.answer, 'assistant-answer-text'));
    if (data.truncated) section.append(text('p', '已达到本次长度上限；请针对其中一点单独提问。', 'assistant-caution'));
    if (data.notice) section.append(text('p', data.notice, 'assistant-caution'));
    if (data.context) {
      const summary = document.createElement('details'); summary.append(text('summary', '本次使用的回测摘要'));
      const pct = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) + '%' : '--';
      summary.append(text('p', `${data.context.target} · ${data.context.strategy} · ${data.context.from} 至 ${data.context.to}\n费用后收益 ${pct(data.context.net_return)} · 最大回撤 ${pct(data.context.max_drawdown)}\n${data.context.source || '来源未提供'}\n摘要取自点击时的已运行结果，不读取尚未运行的表单参数。`)); section.append(summary);
    }
    sources(section, data.sources);
    section.append(text('small', `完成 · ${data.seconds ?? 0} 秒`)); thread.append(section);
    // Keep this ephemeral panel bounded; never add it to startup-memory snapshots.
    while (thread.children.length > 20) thread.firstElementChild.remove();
    thread.scrollTop += section.getBoundingClientRect().top - thread.getBoundingClientRect().top;
  }
  function sources(section, refs) {
    for (const ref of refs || []) {
      const details = document.createElement('details'); details.append(text('summary', ref.title + ' · 原文'));
      details.append(text('p', ref.text)); details.append(text('small', ref.source + ' · ' + ref.version));
      if (ref.view) { const button = text('button', '打开相关页面', 'text-button'); button.type = 'button'; button.onclick = () => navigate(ref); details.append(button); }
      section.append(details);
    }
  }
  async function ask(question, context = null) {
    if (busy || !question.trim()) return;
    const token = ++generation, start = Date.now(); setBusy(true); q('error').textContent = '';
    thread.querySelector('.assistant-welcome')?.remove(); thread.append(text('p', question, 'assistant-user'));
    if (context) thread.append(text('small', `已附加 ${context.target} 的回测摘要，仅在本机使用。`));
    q('question').value = ''; q('count').textContent = '0 / 500'; scroll();
    timer = setInterval(() => { q('progress').textContent = `本地模型处理中 · ${Math.round((Date.now() - start) / 1000)}秒 · 可停止`; }, 1000);
    q('progress').textContent = '正在检索本地手册；首次回答需加载模型';
    let id = null, preview = null;
    try {
      const job = await request('ask', {question, context}); id = job.id;
      if (token !== generation) { await request('cancel', {id}); return; }
      active = id;
      while (token === generation) {
        const data = await request('jobs/' + encodeURIComponent(id));
        if (token !== generation) return;
        if (data.state !== 'working') { preview?.remove(); if (data.state === 'done') answer(data); break; }
        if (!preview && data.sources?.length) {
          preview = document.createElement('section'); preview.className = 'assistant-answer assistant-preview';
          preview.append(text('h3', '手册已找到，可先查看原文')); sources(preview, data.sources); thread.append(preview); scroll();
        }
        if (Date.now() - start > 150000) throw Error('回答超时，请稍后重试');
        await new Promise(resolve => setTimeout(resolve, 650));
      }
    } catch (e) {
      if (id) request('cancel', {id}).catch(() => {});
      if (token === generation) q('error').textContent = e.name === 'AbortError' ? '本地服务响应超时，请重试。' : e.message;
    } finally {
      if (token === generation) { clearInterval(timer); active = null; setBusy(false); q('progress').textContent = ''; status(); }
    }
  }
  async function stop() {
    generation++; clearInterval(timer); const id = active; active = null;
    if (id) { try { const result = await request('cancel', {id}); if (result.pending) q('error').textContent = '模型正在收尾，请稍后再提问。'; } catch (_) { q('error').textContent = '未确认停止；服务会在本次超时后结束。'; } }
    setBusy(false); q('progress').textContent = '已停止等待回答'; status();
  }
  q('stop').onclick = stop;
  function clear() { stop(); thread.innerHTML = welcome; q('question').value = ''; q('count').textContent = '0 / 500'; q('error').textContent = ''; }
  q('clear').onclick = clear; window.addEventListener('portfolio-change', clear);
  q('question').addEventListener('input', () => { q('count').textContent = q('question').value.length + ' / 500'; });
  q('form').addEventListener('submit', e => { e.preventDefault(); ask(q('question').value); });
  thread.addEventListener('click', e => { const button = e.target.closest('[data-assistant-question]'); if (button) ask(button.dataset.assistantQuestion); });
  q('explain').onclick = () => { const context = window.getBacktestAssistantContext?.(); if (context) ask('请解释这份回测结果，说明费用后收益、最大回撤、样本限制和下一步应核对什么。', context); };
  const backtestTools = document.querySelector('#backtest-system .bt-intro .bt-actions');
  if (backtestTools) { const button = text('button', '问助手', 'text-button'); button.type = 'button'; button.id = 'backtest-ask-assistant'; button.onclick = () => open(button); backtestTools.append(button); }
  fetch('/api/assistant/status', {cache:'no-store'}).then(response => {
    if (response.status === 403) { launcher.hidden = true; document.getElementById('backtest-ask-assistant')?.remove(); }
  }).catch(() => {});
  window.addEventListener('pagehide', () => { if (active) fetch('/api/assistant/cancel', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:active}),keepalive:true}).catch(() => {}); });
})();
