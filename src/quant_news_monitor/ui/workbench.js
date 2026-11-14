"use strict";

const workbenchState = { eventLimit: 60, eventSignature: "", instrumentNews: null, newsRequest: 0 };

function instrumentNewsSymbol(value) {
  return String(value || '').trim().toUpperCase().split(':').pop().replace(/\.(KS|KQ|HK|SS|SZ)$/, '');
}

function openPortfolioNews(item) {
  document.getElementById('event-search').value = `${item.market}:${normalizeStockSymbol(item.symbol)}`;
  document.getElementById('event-scope').value = 'all';
  document.getElementById('event-sort').value = 'latest';
  workbenchState.eventLimit = 60;
  workbenchState.instrumentNews = null;
  setView('signals'); renderEventCenter();
}

async function loadInstrumentNews(market, symbol) {
  const key = `${market}:${instrumentNewsSymbol(symbol)}`.toLowerCase(), request = ++workbenchState.newsRequest;
  workbenchState.instrumentNews = {key, loading: true, news: [], error: ''};
  workbenchState.eventSignature = '';
  try {
    const result = await api(`/api/news/instrument?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`);
    if (request !== workbenchState.newsRequest) return;
    if (`${result.market}:${instrumentNewsSymbol(result.symbol)}`.toLowerCase() !== key || !Array.isArray(result.news)) throw Error('新闻返回的标的不匹配，请重试');
    workbenchState.instrumentNews = {key, loading: false, news: result.news, error: '',
      refreshError: result.news_refresh?.error, lookbackHours: result.lookback_hours};
  } catch (error) {
    if (request !== workbenchState.newsRequest) return;
    workbenchState.instrumentNews = {key, loading: false, news: [], error: error.message || '新闻读取失败'};
  }
  if (state.currentView === 'signals') renderEventCenter();
}

function diagnosticDetails(key, errors, opened) {
  const details = el("details", "freshness-diagnostics");
  details.dataset.statusKey = key;
  details.open = opened.has(key);
  details.append(el("summary", "", `技术详情（${errors.length} 条）`));
  const list = el("ul");
  errors.forEach(error => list.append(el("li", "", String(error))));
  details.append(list);
  return details;
}

function diagnosticMessages(key, errors) {
  const sources = {"bing-news-rss":"Bing 新闻", "google-news-rss":"Google 新闻", "google-news-zh-rss":"Google 中文新闻", "yahoo-finance-rss":"Yahoo 财经", "local-inbox":"本地消息"};
  return [...new Set(errors.map(error => {
    const raw = String(error), source = raw.match(/^数据源\s+(.+?)\s+(?:部分失败|失败)\s*[:：]/)?.[1];
    const label = sources[source] || (source ? "新闻来源" : /^分析\s/.test(raw) ? "新闻分析" : key === "portfolio" ? "持仓报价" : key === "movements" ? "异动检测" : "新闻更新");
    const reason = /429|限流/.test(raw) ? "访问频率受限" : /\b(?:401|403)\b/.test(raw) ? "来源拒绝访问"
      : /timeout|timed out|超时/i.test(raw) ? "连接超时" : /退避/.test(raw) ? "等待重试"
      : /URLError|SSL|EOF|WinError|OSError|连接|暂不可用/i.test(raw) ? "连接暂时中断"
      : /parse|XML|字段|格式/i.test(raw) ? "返回内容格式异常" : "更新未完成";
    return `${label}：${reason}`;
  }))];
}

function latestNewsStatus(dashboard, liveNews) {
  const packets = [dashboard, liveNews].filter(packet => packet?.runtime);
  return packets.sort((a,b) => Number(Boolean(b._startup_cache))-Number(Boolean(a._startup_cache))
    || (Date.parse(a.runtime.last_run?.finished_at)||0)-(Date.parse(b.runtime.last_run?.finished_at)||0)
    || (Date.parse(a.generated_at)||0)-(Date.parse(b.generated_at)||0)).at(-1) || {};
}

function diagnosticNotice(key, errors) {
  const box = el("div", "freshness-notice");
  const button = el("button", "text-button", "数据状态"); button.type="button"; button.dataset.diagnosticKey=key;
  button.title = `${key === "portfolio" ? "持仓行情" : "新闻采集"}：查看 ${errors.length} 条更新记录`;
  button.setAttribute("aria-label", button.title);
  button.append(uiIcon("arrow-right"));
  button.addEventListener("click", () => {
    setView("settings");
    openSettingsDiagnostics(key);
  });
  box.append(button); return box;
}

function openSettingsDiagnostics(key) {
  const dialog=document.getElementById("settings-diagnostics-dialog");
  if (!dialog.open) dialog.showModal();
  const heading=document.getElementById(`data-status-${key}`) || document.getElementById("data-status-title");
  heading.scrollIntoView({block:"nearest"}); heading.focus({preventScroll:true});
}

function renderDataDiagnostics(runtime) {
  const container = document.getElementById("data-diagnostics"); if (!container) return;
  const portfolio = state.dashboard?.portfolio?.runtime || {}, movement = state.dashboard?.runtime?.last_movement_run;
  const groups = [
    {key:"news", title:"新闻采集", errors:runtime.last_run?.errors || [], time:runtime.last_run?.finished_at,
      note:runtime.auto_enabled === false ? "自动采集已暂停。" : runtime.auto_enabled ? "自动采集已开启；请求失败后按来源退避规则再试。" : "自动采集状态待确认。"},
    {key:"portfolio", title:"持仓行情", errors:portfolio.errors || [], time:portfolio.updated_at, note:"已有报价保留，时间以报价来源为准。"},
    {key:"movements", title:"异动检测", errors:movement?.errors || [], time:movement?.finished_at, note:"原因未核实的异动仍标记为待核实。"}
  ];
  const warningGroups=groups.filter(group=>group.errors.length).length;
  document.getElementById("settings-diagnostics-status").textContent = warningGroups ? `${warningGroups} 类提醒` : groups.some(group=>group.time) ? "记录无异常" : "等待数据";
  const signature = JSON.stringify(groups); if (container.dataset.signature === signature) return;
  const opened = new Set([...container.querySelectorAll("details[open]")].map(node=>node.dataset.statusKey));
  container.dataset.signature=signature; container.replaceChildren();
  groups.forEach(group => {
    const section=el("section", "data-status-item"), title=el("h4", "", group.title);
    title.id=`data-status-${group.key}`; title.tabIndex=-1;
    section.append(title, el("p", "quiet-note", group.time ? `最近完成 ${formatDate(group.time,true)}` : "尚无完成记录"));
    if (group.errors.length) {
      const list=el("ul", "data-status-messages"); diagnosticMessages(group.key,group.errors).forEach(text=>list.append(el("li","",text)));
      section.append(list, el("p","quiet-note",group.note), diagnosticDetails(group.key,group.errors,opened));
    } else section.append(el("p","quiet-note",group.time ? "本次记录无异常。" : "等待本次检测结果。"));
    container.append(section);
  });
}

function overviewPortfolioBooks(data) {
  if (Array.isArray(data.portfolios) && data.portfolios.length) return data.portfolios;
  const book = data.portfolio;
  return book ? [{...book, id:book.id || "default", name:book.name || "默认组合", updated_at:book.runtime?.updated_at}] : [];
}

function overviewPortfolioMetrics(book, cached = false) {
  const s = book.summary || {}, number = value => typeof value === "number" && Number.isFinite(value) ? value : null;
  const count = number(s.position_count) ?? book.positions?.length ?? null;
  const complete = (s.base_valuation_complete ?? s.valuation_complete) === true;
  const principal = number(s.principal_base), netAssets = complete ? number(s.net_assets_base) : null;
  const stale = cached || s.valuation_current === false || number(s.stale_valued_count) > 0 || book.runtime?.restored_from_cache || ["stale","error","waiting"].includes(book.runtime?.status);
  const usable = complete && !stale;
  const costReturn = usable && count > 0 ? number(s.total_pnl_pct) : null;
  const pnl = costReturn == null ? null : number(s.total_pnl_base);
  const dailyUsable = !cached && !book.runtime?.restored_from_cache && !['stale','error','waiting'].includes(book.runtime?.status)
    && (usable || Boolean(s.daily_pnl_quote_time));
  const dailyPnl = dailyUsable && complete && count > 0 && s.daily_pnl_complete === true
    ? number(s.daily_pnl_base) : null;
  const value = number(s.total_market_value_base), previousValue = value == null || dailyPnl == null ? null : value - dailyPnl;
  // This is the current holdings' price move, not a cash-flow-adjusted account return.
  const dailyReturn = previousValue > 0 ? number(dailyPnl / previousValue * 100) : null;
  const capitalChange = usable && s.principal_status === "available" && principal > 0 && netAssets != null ? netAssets - principal : null;
  const reference = !usable && portfolioReferenceValuation(book, cached);
  const referencePnl = reference && count > 0 ? number(s.total_pnl_base) : null;
  const referenceReturn = referencePnl == null ? null : number(s.total_pnl_pct);
  const referenceCapital = reference && principal > 0 && netAssets != null ? netAssets - principal : null;
  const dailyCount = number(s.daily_pnl_count) || 0;
  const partialDaily = dailyUsable && !s.daily_pnl_complete && dailyCount > 0 ? number(s.daily_pnl_base) : null;
  return {count, principal, netAssets, costReturn, pnl, dailyPnl, dailyReturn, capitalChange,
    reference, referencePnl, referenceReturn, referenceCapital, partialDaily, dailyCount,
    capitalReturn:capitalChange == null ? null : number(capitalChange / principal * 100),
    status:stale ? "上次估值 · 待行情确认" : !complete ? "估值不完整 · 待行情或汇率" : count === 0 ? "暂无证券持仓" : s.principal_status === "unavailable" ? "参考估值 · 来源有提醒" : "估值已更新"};
}

function renderPortfolioOverview(data) {
  const cell = el("div", "freshness-cell freshness-portfolios"), books = overviewPortfolioBooks(data);
  const heading = el("div", "overview-summary-heading");
  heading.append(el("span", "freshness-title", "持仓组合"));
  const errors=data.portfolio?.runtime?.errors || [];
  if (errors.length) {
    const notice = diagnosticNotice("portfolio", errors), button = notice.children[0];
    button.className = "icon-button overview-diagnostic";
    button.replaceChildren(uiIcon("activity"));
    heading.append(notice);
  }
  cell.append(heading);
  if (!books.length) {
    cell.append(el("strong", "", "正在读取组合"), el("small", "", "尚未取得组合数据"));
    return cell;
  }
  const list = el("div", "overview-portfolios");
  list.tabIndex=0; list.setAttribute("role","region"); list.setAttribute("aria-label","持仓组合概况");
  books.forEach(book => {
    const metrics = overviewPortfolioMetrics(book, Boolean(data._startup_cache)), currency = book.base_currency || "CNY";
    const tone = value => value == null || value === 0 ? "neutral" : value > 0 ? "positive" : "negative";
    const money = value => formatPortfolioMoney(value, currency, true);
    const signedMoney = value => value == null ? "--" : `${value > 0 ? "+" : ""}${money(value)}`;
    const section = el("article", "overview-portfolio"), header = el("div", "overview-portfolio-header");
    const button = el("button", "text-button", book.name || "未命名组合"); button.type="button";
    const valuationNote = `${metrics.status}${book.updated_at ? ` · ${formatDate(book.updated_at,true)}` : " · 尚无行情快照"}`;
    button.title = valuationNote; button.setAttribute("aria-description", valuationNote);
    button.dataset.overviewPortfolio=book.id; button.append(uiIcon("arrow-right"));
    button.addEventListener("click", () => {
      if (window.PortfolioBooks) { setView("portfolio", {refresh:false}); window.PortfolioBooks.open(book.id); }
      else setView("portfolio");
    });
    header.append(button, el("span", "", metrics.count == null ? "持仓待确认" : `${metrics.count} 项`));
    const values = el("dl", "overview-portfolio-metrics");
    const add = (key, label, text, value, note) => {
      const row=el("div"), dd=el("dd",tone(value),text); row.dataset.metric=key;
      row.title = note;
      row.append(el("dt","",label),dd); values.append(row);
    };
    const costReturn = metrics.costReturn ?? metrics.referenceReturn, pnl = metrics.pnl ?? metrics.referencePnl;
    add("cost-return", metrics.reference ? "持仓·参考" : "持仓涨跌", costReturn == null ? "--" : `${signedMoney(pnl)} ${signedPercent(costReturn)}`, costReturn, `持仓涨跌相对成本。参考值采用已有报价及汇率。${valuationNote}`);
    const dailyNotes = portfolioDailyQuoteNotes(book.summary || {});
    const dailyLabel = book.summary?.daily_pnl_delayed_count ? '前收·延迟' : '较前收参考';
    const partial = metrics.partialDaily != null;
    add("daily-return", partial ? `前收${metrics.dailyCount}/${metrics.count}` : dailyLabel, partial ? signedMoney(metrics.partialDaily) : metrics.dailyReturn == null ? "--" : `${signedMoney(metrics.dailyPnl)} ${signedPercent(metrics.dailyReturn)}`, partial ? metrics.partialDaily : metrics.dailyReturn, `按当前仓位估算较前收的价格变化。${partial ? '仅汇总有有效前收数据的持仓，覆盖不足，整体涨跌幅待补齐。' : ''}${dailyNotes.join(' · ') || valuationNote}`);
    add("principal", "登记本金", metrics.principal == null || metrics.principal === 0 ? "未设置" : money(metrics.principal), null, "手动登记的组合本金");
    add("net-assets", metrics.reference ? "参考净资产" : "净资产", money(metrics.netAssets), null, `净资产含现金。${valuationNote}`);
    const capitalChange = metrics.capitalChange ?? metrics.referenceCapital;
    const capitalReturn = capitalChange == null ? null : capitalChange / metrics.principal * 100;
    add("capital-change", metrics.reference ? "本金·参考" : "较本金增减", capitalChange == null ? (metrics.principal > 0 ? "待估值确认" : "本金未设置") : `${signedMoney(capitalChange)} ${signedPercent(capitalReturn)}`, capitalChange, `净资产较本金的增减，未分离出入金，不等于投资收益。${valuationNote}`);
    if (!(metrics.principal > 0) && window.PortfolioBooks) {
      const action = el('button', 'text-button', '设置本金'); action.type = 'button';
      action.addEventListener('click', async () => {
        setView('portfolio', {refresh:false});
        await window.PortfolioBooks.open(book.id);
        if (portfolioContext.id === book.id && !portfolioContext.switching) {
          document.getElementById('portfolio-principal-input')?.focus();
        }
      });
      values.lastElementChild.querySelector('dd').replaceChildren(action);
    }
    section.append(header,values);
    list.append(section);
  });
  cell.append(list);
  return cell;
}

function renderFreshnessStatus() {
  const container = document.getElementById("overview-freshness");
  if (!container || !state.dashboard) return;
  const activeQuote = document.activeElement?.closest?.(".freshness-quote")?.dataset.asset;
  const activePortfolio = document.activeElement?.closest?.("[data-overview-portfolio]")?.dataset.overviewPortfolio;
  const portfolioListFocused = document.activeElement?.classList?.contains?.("overview-portfolios");
  const portfolioScroll = container.querySelector?.(".overview-portfolios")?.scrollTop || 0;
  const fresh = state.dashboard.freshness || {}, rows = state.liveNews?.news || [];
  const latest = (key, fallback) => [fallback, ...rows.map(row => row[key])].filter(value => Number.isFinite(Date.parse(value))).sort((a,b) => Date.parse(a)-Date.parse(b)).at(-1);
  const newsState = latestNewsStatus(state.dashboard,state.liveNews), runtime = newsState.runtime || {};
  const newsErrors = runtime.last_run?.errors || [];
  const readState = newsState.news_refresh || {};
  const news = el("div", "freshness-cell freshness-news");
  news.append(el("span", "freshness-title", "新闻采集"), el("strong", "", newsState._startup_cache ? "上次状态 · 待确认" : readState.error ? "同步失败 · 保留上次数据" : runtime.scan_in_progress ? "采集中" : newsErrors.length ? "部分来源未更新" : runtime.last_run ? "已完成上一轮" : "等待首次采集"));
  news.append(el("small", "", `原文 ${formatDate(latest("published_at", fresh.news_published_at), true)}`));
  news.append(el("small", "", `采集 ${formatDate(latest("discovered_at", fresh.news_collected_at), true)} · 分析 ${formatDate(latest("analyzed_at", fresh.news_analyzed_at), true)}`));
  if (newsErrors.length) news.append(diagnosticNotice("news", newsErrors));
  const quotes = el("div", "freshness-cell freshness-assets");
  quotes.append(el("span", "freshness-title", "资产报价"));
  const quoteRows = el("div", "freshness-quotes");
  for (const [key, name] of Object.entries(ASSET_NAMES)) {
    const asset = state.assets?.assets?.find(row => row.key === key);
    const status = !asset ? "待获取" : ({live:"来源有效报价",delayed:"延迟",closed:"休市末价",stale:"上次报价"}[asset.quote_status] || "待确认");
    const rawChange = key === "US5Y" ? asset?.change_bps : asset?.change_24h;
    const change = (typeof rawChange === "number" || typeof rawChange === "string" && rawChange.trim() !== "") && Number.isFinite(Number(rawChange)) ? Number(rawChange) : null;
    const changeText = change == null ? "--" : key === "US5Y" ? `${change >= 0 ? "+" : ""}${change.toFixed(2)} bp` : signedPercent(change);
    const row = el("div", "freshness-quote"), label = el("button", "text-button", name);
    row.dataset.asset=key; label.type="button"; label.addEventListener("click",()=>setView("assets"));
    const changeNode = el("strong", `freshness-change ${change == null || change === 0 ? "neutral" : change > 0 ? "positive" : "negative"}`, changeText);
    changeNode.title=key === "US5Y" ? "较前收的收益率变化；1 bp = 0.01 个百分点，不是债券价格涨跌幅" : "近24小时报价涨跌幅";
    changeNode.setAttribute("aria-label", `${name}${key === "US5Y" ? "收益率变化" : "近24小时涨跌幅"} ${changeText}`);
    row.append(label, changeNode, el("span", `freshness-quote-status${asset?.quote_status === "stale" || !asset ? " status-warning" : ""}`, status), el("time", "", asset?.price_time ? formatDate(asset.price_time, true) : "--"));
    quoteRows.append(row);
  }
  quotes.append(quoteRows);
  const holdings = renderPortfolioOverview(state.dashboard);
  container.replaceChildren(news, quotes, holdings);
  const portfolioList = holdings.querySelector?.(".overview-portfolios");
  if (portfolioList) portfolioList.scrollTop=portfolioScroll;
  if (portfolioListFocused) portfolioList?.focus({preventScroll:true});
  if (activeQuote) container.querySelector(`[data-asset="${activeQuote}"] button`)?.focus({preventScroll:true});
  if (activePortfolio) [...holdings.querySelectorAll("[data-overview-portfolio]")].find(node=>node.dataset.overviewPortfolio===activePortfolio)?.focus({preventScroll:true});
  renderDataDiagnostics(runtime);
}

function eventIdentity(item) {
  // Keep company attribution: one article can have different implications for two companies.
  return `${item.market || ""}:${normalizeStockSymbol(item.symbol || "")}:${item.fingerprint || item.news_id || item.url || item.id || item.title || ""}`;
}

function combinedEvents(news, alerts, movements, portfolioAlerts = []) {
  const merged = new Map();
  for (const item of news) merged.set(eventIdentity(item), {kind:"news", item, time:item.published_at, alert:false});
  for (const item of alerts) {
    const key = eventIdentity(item), prior = merged.get(key);
    merged.set(key, {kind:"news", item:{...prior?.item,...item}, time:item.alert_created_at || item.published_at || prior?.time, alert:true});
  }
  for (const item of movements) merged.set(`movement:${item.market}:${item.symbol}`, {kind:"movement", item, time:item.observed_at || item.detected_at || item.updated_at || item.quote_time || item.price_time, alert:false});
  for (const item of portfolioAlerts) merged.set(`portfolio:${item.id}`, {kind:"portfolio", item, time:item.created_at, alert:true});
  return [...merged.values()];
}

function renderEventCenter() {
  const container = document.getElementById("event-timeline");
  if (!container || !state.dashboard) return;
  const search = document.getElementById("event-search").value.trim().toLocaleLowerCase();
  const newsTarget = search.match(/^(cn|hk|us|kr|fund):(.+)$/i);
  const newsTargetKey = newsTarget ? `${newsTarget[1]}:${instrumentNewsSymbol(newsTarget[2]).toLowerCase()}` : '';
  if (newsTarget && workbenchState.instrumentNews?.key !== newsTargetKey) loadInstrumentNews(newsTarget[1].toUpperCase(), normalizeStockSymbol(newsTarget[2]));
  const targeted = newsTarget ? workbenchState.instrumentNews : null;
  if (targeted?.loading || targeted?.error) {
    workbenchState.eventSignature = '';
    document.getElementById('event-count').textContent = targeted.loading ? '正在读取标的新闻…' : '新闻读取失败';
    document.getElementById('event-more').hidden = true;
    const message = el('p', targeted.error ? 'negative' : 'quiet-note', targeted.loading ? '正在读取该标的的相关消息…' : targeted.error);
    message.setAttribute('role', targeted.error ? 'alert' : 'status');
    container.replaceChildren(message);
    if (targeted.error) {
      const retry = el('button', 'secondary-button', '重新读取新闻'); retry.type = 'button';
      retry.addEventListener('click', () => { workbenchState.instrumentNews = null; renderEventCenter(); });
      container.append(retry);
    }
    return;
  }
  const holdingsOnly = document.getElementById("event-scope").value === "holdings";
  const keys = new Set((state.dashboard.portfolio?.positions || []).map(item => `${item.market}:${normalizeStockSymbol(item.symbol)}`));
  const events = combinedEvents(targeted ? targeted.news : currentNews(), targeted ? [] : state.dashboard.alerts || [], targeted ? [] : state.dashboard.movements || [], targeted ? [] : state.dashboard.portfolio_risk_events || state.dashboard.portfolio?.risk?.events || []).filter(event => {
    const item = event.item;
    if (holdingsOnly && !keys.has(`${item.market}:${normalizeStockSymbol(item.symbol)}`)) return false;
    if (newsTarget) return event.kind === 'news' && String(item.market).toLowerCase() === newsTarget[1]
      && instrumentNewsSymbol(item.symbol) === instrumentNewsSymbol(newsTarget[2]);
    return !search || [instrumentLabel(item),item.title,item.title_zh,item.source,item.analysis_summary,item.reason_summary].join(" ").toLocaleLowerCase().includes(search);
  });
  const priority = document.getElementById("event-sort").value === "priority";
  events.sort((a,b) => (priority ? Number(b.alert)-Number(a.alert) : 0) || (Date.parse(b.time)||0)-(Date.parse(a.time)||0));
  document.getElementById("event-count").textContent = targeted ? `${events.length} 条相关新闻${targeted.refreshError ? ' · 更新失败，保留上次数据' : ''}` : `${events.length} 条动态`;
  document.getElementById("event-more").hidden = events.length <= workbenchState.eventLimit;
  const visible = events.slice(0, workbenchState.eventLimit), signature = JSON.stringify({target: newsTargetKey, visible});
  if (signature === workbenchState.eventSignature) return;
  workbenchState.eventSignature = signature;
  container.replaceChildren();
  if (!visible.length) { container.append(emptyState(targeted ? '暂无相关新闻' : '暂无匹配动态', targeted ? `近 ${targeted.lookbackHours || 72} 小时暂无相关新闻` : "当前范围没有新闻、异动或告警记录")); return; }
  visible.forEach(event => {
    const item = event.item, row = el("article", "event-row");
    const meta = el("div", "event-row-meta");
    meta.append(el("span", `event-kind${event.alert ? " status-warning" : ""}`, event.kind === "portfolio" ? "持仓 · 警戒线" : event.kind === "movement" ? "行情异动" : event.alert ? "新闻 · 告警" : "新闻"), el("time", "", event.time ? formatDate(event.time,true) : "数据时间未提供"));
    const button = el("button", "event-title", event.kind === "movement" ? `${instrumentLabel(item)} · 时段涨跌 ${signedPercent(movementChangePercent(item))}` : newsTitle(item));
    if (event.kind === "news") appendNewsTranslation(button, item);
    button.type = "button";
    button.addEventListener("click", () => {
      if (event.kind === "portfolio") {
        openPortfolioAlert(item);
      } else if (event.kind === "movement") openMovementDetail(item);
      else openDetail(item);
    });
    const body = el("div", "event-row-body");
    body.append(button);
    if (event.kind === "movement") appendMovementExplanation(body,item);
    else body.append(el("p", "", event.kind === "news" ? newsAnalysisPreview(item) || "暂无已核实结论" : item.reason_summary || item.analysis_summary || "暂无已核实结论"));
    const evidence = el("div", "event-row-evidence");
    evidence.append(el("span", "", `${instrumentLabel(item)} · ${marketLabels[item.market] || item.market || ""}`));
    if (event.kind === "movement") {
      const note = movementExplanation(item);
      evidence.append(el("span", "", note.label));
      if (note.ready) {
        const link=el("a","",item.evidence_source || "原文"); link.href=item.evidence_url; link.target="_blank"; link.rel="noopener noreferrer";
        evidence.append(link,el("time","",`报道 ${formatDate(item.evidence_published_at,true)}`));
      }
    }
    else if (/^https?:\/\//i.test(item.url || "")) {
      const link = el("a", "", item.source || "原文网站");
      link.href = item.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      evidence.append(link);
    }
    row.append(meta, body, evidence); container.append(row);
  });
}

function compassDestination(label, note, target, direction, tab) {
  const button = el("button", `compass-point compass-${direction}`);
  button.type = "button";
  button.title = `${label} · ${note}`;
  button.setAttribute("aria-label", `${label}：${note}`);
  const icon = {north:"chart-no-axes-combined", northeast:"chart-no-axes-combined", east:"message-circle", southeast:"newspaper", south:"notebook-pen", southwest:"activity", west:"activity", northwest:"wallet"}[direction];
  button.append(uiIcon(icon), el("strong", "", label));
  button.addEventListener("click", () => {
    if (tab) document.querySelector(`#research-tabs [data-tab="${tab}"]`).click();
    else setView(target);
  });
  return button;
}

function holdingEventInterpretation(item, position = {}) {
  const metadata = item.analysis_metadata || {}, status = item.evidence_status || metadata.evidence_status;
  const supported = status === "supported" && metadata.direction_eligible !== false && Number.isFinite(item.score);
  const tone = supported && item.score > 0.04 ? "positive" : supported && item.score < -0.04 ? "negative" : "neutral";
  const label = status === "mixed" ? "多空并存" : status === "legacy" ? "旧记录待复核"
    : status === "price_only" ? "行情事实" : status === "neutral" ? "中性观察"
    : status === "insufficient" ? "待核实" : !supported ? "方向待确认" : tone === "positive" ? "研究信号偏多" : tone === "negative" ? "研究信号偏空" : "中性观察";
  const analysis = status === "mixed" ? "正反证据并存，尚不能确认对该持仓的净影响，需分别核对支持与反对线索。"
    : status === "legacy" ? "这条旧记录尚未按当前证据规则复核，暂不沿用原方向判断。"
    : status === "price_only" ? "仅描述已发生的行情变化，暂不判断未来利多利空。"
    : status === "neutral" ? item.analysis_summary || "已评估现有材料，暂无明确方向影响。"
    : !supported ? "现有报道不足以确认对该持仓盈利、估值或资金面的净影响，暂作为观察线索，不据此判断涨跌。"
    : item.analysis_summary || item.impact_reason || "已有方向线索，但缺少影响传导说明，仍需核对原始披露。";
  const followups = {
    earnings:"核对财报原文、收入与利润口径、现金流及一次性项目；有同口径预期基准后再判断预期差。",
    guidance:"核对指引覆盖期间、新旧区间及关键假设，并跟踪后续经营数据是否兑现。",
    order:"核对订单金额、交付周期、收入确认和回款条件，区分框架协议与已确认订单。",
    capital_action:"核对公告、实施时间与规模，以及对股本、每股收益和现金流的影响；计划不等于已执行。",
    capacity:"跟踪投产时间、利用率、供需价格与资本开支，验证新增产能能否产生现金回报。",
    regulation:"核对政策原文、适用主体和生效日期，再检查收入、成本或经营许可是否实际受影响。",
    legal:"核对案件进度、涉案金额和公司披露，区分指控、调查与已经生效的裁决。",
    m_and_a:"核对交易对价、融资方式、审批与交割条件，并跟踪整合成本和协同兑现。",
    management:"核对任免生效时间、职责与战略变化，并观察后续经营执行情况。",
    analyst:"核对评级与目标价的估值假设、预测期间及修订原因；机构观点不等于公司已披露事实。",
    product:"跟踪产品落地、客户采用、订单与利润贡献，区分技术发布和商业兑现。",
  };
  const fund = position.market === "FUND" || /ETF|基金/i.test(position.name || "");
  const watch = fund
    ? "核对基金份额、净值与折溢价，并结合底层资产变化验证。融资买卖额不等于基金申购赎回，单日资金变化也不能确认后续趋势。"
    : followups[item.event_type] || "核对原始公告或完整报道，跟踪后续披露及量价变化；区分已发生的数据与未来假设，不把相关性当成因果。";
  const risks = [...new Set((item.risk_flags || []).filter(value => typeof value === "string" && value.trim()))];
  if (!risks.length) risks.push("证据范围尚未说明，需回到原文核实。");
  if (metadata.expected_baseline_available !== true && !risks.some(text => /预期基准/.test(text))) risks.push("缺少可核对的预期基准，不确认超预期。");
  return {tone, label, analysis, watch, risks};
}

function renderHoldingEvent(item, position, portfolio) {
  const note = holdingEventInterpretation(item, position), key = newsDisplayKey(item);
  const article = el("article", "holding-event"), heading = el("h3");
  const button = el("button", "event-title", `${instrumentLabel(position)} · ${newsTitle(item)}`);
  button.type="button"; button.dataset.holdingEvent=`detail:${key}`;
  appendNewsTranslation(button,item);
  button.addEventListener("click",()=>openDetail(item)); heading.append(button);
  const meta = el("div", "holding-event-meta"), published = el("time", "", `发布 ${formatDate(item.published_at,true)}`);
  published.dateTime=item.published_at || "";
  meta.append(el("strong",note.tone,note.label), el("span","",item.source || "来源待确认"),published);
  if (item.analyzed_at) meta.append(el("span","",`分析 ${formatDate(item.analyzed_at,true)}`));
  const horizons = {intraday:"日内","1-5d":"1–5 日","1-3m":"1–3 个月","3m+":"3 个月以上"};
  if (item.horizon) meta.append(el("span","",`观察期限 ${horizons[item.horizon] || item.horizon}`));
  article.append(heading,meta,instrumentBriefNode(position));
  const content = el("dl","holding-event-notes");
  const add = (label,text) => {
    const row=el("div"), detail=el("dd","",text); row.append(el("dt","",label),detail); content.append(row); return detail;
  };
  const summaryField = item.news_summary ? "news_summary" : "summary";
  if (item[summaryField]) appendNewsTranslation(add("来源摘要",item[summaryField]),item,summaryField);
  else add("证据范围","当前仅有新闻标题，尚未取得原文摘要，不能视为已核验完整报道。");
  const quantity = Number.isFinite(position.quantity) ? ` · 持有 ${formatPositionQuantity(position.quantity)} ${position.market === "FUND" || /ETF|基金/i.test(position.name || "") ? "份" : "股"}` : "";
  add("关联持仓",`${portfolio.name || "当前组合"} · ${instrumentName(position)}${quantity}。仅说明事件与该持仓的关联，不代表已造成相应盈亏。`);
  add("影响解读",note.analysis);
  add("后续观察",note.watch);
  add("风险与限制",note.risks.join("；"));
  article.append(content);
  if (/^https?:\/\//i.test(item.url || "")) {
    const link=el("a","holding-event-source","查看原文"); link.href=item.url; link.target="_blank"; link.rel="noopener noreferrer";
    link.dataset.holdingEvent=`source:${key}`; link.append(uiIcon("arrow-right"));
    article.append(link);
  }
  return article;
}

function renderCompass() {
  const container = document.getElementById("overview-compass");
  if (!container || container.hidden) return;
  const active = document.activeElement?.closest?.(".compass-point")?.className;
  const dial = el("div", "compass-dial");
  const center = el("div", "compass-center");
  center.append(uiIcon("chart-no-axes-combined"), el("strong", "", "市场罗盘"));
  dial.append(center,
    compassDestination("持仓组合", "仓位 · 配比 · 风险", "portfolio", "northwest"),
    compassDestination("跨资产", "黄金 · BTC · 原油 · 美债", "assets", "north"),
    compassDestination("指数监测", "自选指数 · 全球市场", "indices", "northeast"),
    compassDestination("事件中心", "新闻 · 异动 · 告警", "signals", "west"),
    compassDestination("政策与情绪", "Trump · 公开讨论", "social", "east"),
    compassDestination("回测系统", "历史检验 · 费用后复盘", "backtest", "southwest"),
    compassDestination("公司与策略", "基本面 · 估值 · 情景", "research", "south", "cards"),
    compassDestination("财报中心", "发布日期 · 预期 · 财报", "research", "southeast", "earnings"));
  container.replaceChildren(dial);
  if (active) [...container.querySelectorAll(".compass-point")].find(node=>node.className===active)?.focus({preventScroll:true});
}

function renderHoldingFocus(events) {
  const container = document.getElementById("holdings-events");
  if (!container) return;
  const activeEvent = document.activeElement?.closest?.("[data-holding-event]")?.dataset.holdingEvent;
  const data = state.dashboard || {}, positions = data.portfolio?.positions || [];
  const focus = el("div");
  if (events.length) focus.append(el("p","holding-focus-scope",`${data.portfolio?.name || "当前组合"} · 当前新闻窗口 ${events.length} 条关联记录${events.length>3 ? " · 展示重点 3 条" : ""}${state.liveNews?._startup_cache || !state.liveNews && data._startup_cache ? " · 上次缓存，待更新" : ""}`));
  if (!events.length) focus.append(el("p", "quiet-note", positions.length ? "当前窗口暂无匹配事件" : "尚未录入持仓"));
  events.slice(0,3).forEach(item => {
    const position=positions.find(row=>row.market===item.market && normalizeStockSymbol(row.symbol)===normalizeStockSymbol(item.symbol));
    focus.append(renderHoldingEvent(item,position,data.portfolio));
  });
  container.replaceChildren(focus);
  if (activeEvent) [...container.querySelectorAll("[data-holding-event]")].find(node=>node.dataset.holdingEvent===activeEvent)?.focus({preventScroll:true});
}

function initWorkbench() {
  initNativeWindowControls();
  renderCompass();
  ["event-search", "event-scope", "event-sort"].forEach(id => document.getElementById(id).addEventListener(id === "event-search" ? "input" : "change", () => {
    workbenchState.eventLimit = 60; renderEventCenter(); saveStartupPreferences();
  }));
  document.getElementById("event-more").addEventListener("click", () => { workbenchState.eventLimit += 60; renderEventCenter(); });
  // Native disclosure keeps the same keyboard behavior as Market Monitoring.
  ["nav-market", "nav-research", "nav-backtest"].forEach(id => {
    const group = document.getElementById(id), icon = uiIcon("arrow-down");
    icon.classList.add("nav-disclosure"); group.querySelector("summary").append(icon);
  });
}

function initNativeWindowControls() {
  const bridge = window.stockWindow, controls = document.getElementById("window-controls");
  if (!bridge || !controls) return;
  document.documentElement.classList.add("desktop-shell");
  controls.hidden = false;
  const applyState = value => {
    const button = controls.querySelector('[data-window-action="toggleMaximize"]');
    const label = value.maximized ? "还原窗口" : "最大化窗口";
    button.title = label; button.setAttribute("aria-label", label);
    document.documentElement.classList.toggle("window-inactive", !value.focused);
  };
  controls.querySelectorAll("button").forEach(button => button.addEventListener("click", async () => {
    try { applyState(await bridge[button.dataset.windowAction]()); }
    catch { showToast("窗口操作未完成，请重试", "error"); }
  }));
  const unsubscribe = bridge.onStateChanged(applyState);
  window.addEventListener("beforeunload", unsubscribe, {once:true});
  bridge.getState().then(applyState).catch(() => showToast("窗口控制暂不可用", "error"));
}

function updateResearchSourceSites() {
  if (!window.StockAnnotations || !state.dashboard) return;
  if (state.currentView !== "research" || typeof researchState === "undefined" || researchState.tab !== "inputs") return;
  const inputs = typeof researchState === "undefined" ? [] : researchState.inputs?.items || [];
  const profiles = typeof researchState === "undefined" ? [] : researchState.landscape?.companies || [];
  const sources = [...currentNews(), ...(state.dashboard.alerts || []), ...(state.assets?.news || []),
    ...inputs.map(item=>item.display || item.payload || item), ...profiles.flatMap(company=>company.sources || []),
    ...(state.dashboard.trump?.posts || []).map(item=>({...item,title:item.facts?.title || item.text,title_zh:item.facts?.title_zh || item.text_zh,title_translation_status:item.facts?.title_translation_status || item.text_translation_status}))];
  window.StockAnnotations.renderSources(sources);
}
