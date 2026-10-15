"use strict";

const workbenchState = { layout: "compass", eventLimit: 60, eventSignature: "" };

function setOverviewLayout(layout) {
  workbenchState.layout = layout === "list" ? "list" : "compass";
  const compass = workbenchState.layout === "compass";
  document.querySelector('[data-view="overview"]').classList.toggle("compass-mode", compass);
  document.getElementById("overview-compass").hidden = !compass;
  document.querySelectorAll("#overview-layout button").forEach(button => {
    button.classList.toggle("active", button.dataset.layout === workbenchState.layout);
    button.setAttribute("aria-pressed", String(button.dataset.layout === workbenchState.layout));
  });
  renderCompass();
}

function diagnosticDetails(key, errors, opened) {
  const details = el("details", "freshness-diagnostics");
  details.dataset.statusKey = key;
  details.open = opened.has(key);
  details.append(el("summary", "", `查看 ${errors.length} 项诊断`));
  const list = el("ul");
  errors.forEach(error => list.append(el("li", "", String(error))));
  details.append(list);
  return details;
}

function renderFreshnessStatus() {
  const container = document.getElementById("overview-freshness");
  if (!container || !state.dashboard) return;
  const opened = new Set([...container.querySelectorAll("details[open]")].map(node => node.dataset.statusKey));
  const fresh = state.dashboard.freshness || {}, rows = state.liveNews?.news || [];
  const latest = (key, fallback) => [fallback, ...rows.map(row => row[key])].filter(value => Number.isFinite(Date.parse(value))).sort((a,b) => Date.parse(a)-Date.parse(b)).at(-1);
  const runtime = state.liveNews?.runtime || state.dashboard.runtime || {};
  const newsErrors = runtime.last_run?.errors || [];
  const news = el("div", "freshness-cell");
  news.append(el("span", "freshness-title", "新闻采集"), el("strong", "", runtime.scan_in_progress ? "采集中" : newsErrors.length ? "部分来源连接异常" : runtime.last_run ? "已完成上一轮" : "等待首次采集"));
  news.append(el("small", "", `原文 ${formatDate(latest("published_at", fresh.news_published_at), true)}`));
  news.append(el("small", "", `采集 ${formatDate(latest("discovered_at", fresh.news_collected_at), true)} · 分析 ${formatDate(latest("analyzed_at", fresh.news_analyzed_at), true)}`));
  if (newsErrors.length) news.append(diagnosticDetails("news", newsErrors, opened));
  const quotes = el("div", "freshness-cell");
  quotes.append(el("span", "freshness-title", "资产报价"));
  const quoteRows = el("div", "freshness-quotes");
  for (const [key, name] of Object.entries(ASSET_NAMES)) {
    const asset = state.assets?.assets?.find(row => row.key === key);
    const status = !asset ? "待获取" : ({live:"来源有效报价",delayed:"延迟",closed:"休市末价",stale:"上次报价"}[asset.quote_status] || "待确认");
    const row = el("div");
    row.append(el("span", "", name), el("span", asset?.quote_status === "stale" || !asset ? "status-warning" : "", status), el("time", "", asset?.price_time ? formatDate(asset.price_time, true) : "--"));
    quoteRows.append(row);
  }
  quotes.append(quoteRows);
  const portfolio = state.dashboard.portfolio?.runtime || {}, positions = state.dashboard.portfolio?.positions || [];
  const errors = portfolio.errors || [];
  const holdings = el("div", "freshness-cell");
  holdings.append(el("span", "freshness-title", "持仓行情"), el("strong", "", !positions.length ? "当前无持仓" : errors.length || portfolio.status === "partial" ? "部分行情待更新" : portfolio.status === "stale" ? "保留上次快照" : portfolio.status === "live" ? "行情已同步" : "等待行情确认"));
  holdings.append(el("small", "", positions.length ? `快照 ${formatDate(portfolio.updated_at,true)}` : "添加持仓后显示行情状态"));
  if (errors.length && positions.length) {
    holdings.append(el("small", "status-warning", "部分来源暂未响应；保留已有数据，后台自动重试。"));
    holdings.append(diagnosticDetails("portfolio", errors, opened));
  }
  container.replaceChildren(news, quotes, holdings);
}

function eventIdentity(item) {
  // Keep company attribution: one article can have different implications for two companies.
  return `${item.market || ""}:${normalizeStockSymbol(item.symbol || "")}:${item.fingerprint || item.news_id || item.url || item.id || item.title || ""}`;
}

function combinedEvents(news, alerts, movements) {
  const merged = new Map();
  for (const item of news) merged.set(eventIdentity(item), {kind:"news", item, time:item.published_at, alert:false});
  for (const item of alerts) {
    const key = eventIdentity(item), prior = merged.get(key);
    merged.set(key, {kind:"news", item:{...prior?.item,...item}, time:item.alert_created_at || item.published_at || prior?.time, alert:true});
  }
  for (const item of movements) merged.set(`movement:${item.market}:${item.symbol}`, {kind:"movement", item, time:item.observed_at || item.detected_at || item.updated_at || item.quote_time || item.price_time, alert:false});
  return [...merged.values()];
}

function renderEventCenter() {
  const container = document.getElementById("event-timeline");
  if (!container || !state.dashboard) return;
  const search = document.getElementById("event-search").value.trim().toLocaleLowerCase();
  const holdingsOnly = document.getElementById("event-scope").value === "holdings";
  const keys = new Set((state.dashboard.portfolio?.positions || []).map(item => `${item.market}:${normalizeStockSymbol(item.symbol)}`));
  const events = combinedEvents(currentNews(), state.dashboard.alerts || [], state.dashboard.movements || []).filter(event => {
    const item = event.item;
    if (holdingsOnly && !keys.has(`${item.market}:${normalizeStockSymbol(item.symbol)}`)) return false;
    return !search || [item.symbol,item.name,item.title,item.title_zh,item.source,item.analysis_summary,item.reason_summary].join(" ").toLocaleLowerCase().includes(search);
  });
  const priority = document.getElementById("event-sort").value === "priority";
  events.sort((a,b) => (priority ? Number(b.alert)-Number(a.alert) : 0) || (Date.parse(b.time)||0)-(Date.parse(a.time)||0));
  document.getElementById("event-count").textContent = `${events.length} 条动态`;
  document.getElementById("event-more").hidden = events.length <= workbenchState.eventLimit;
  const visible = events.slice(0, workbenchState.eventLimit), signature = JSON.stringify(visible);
  if (signature === workbenchState.eventSignature) return;
  workbenchState.eventSignature = signature;
  container.replaceChildren();
  if (!visible.length) { container.append(emptyState("暂无匹配动态", "当前范围没有新闻、异动或告警记录")); return; }
  visible.forEach(event => {
    const item = event.item, row = el("article", "event-row");
    const meta = el("div", "event-row-meta");
    meta.append(el("span", `event-kind${event.alert ? " status-warning" : ""}`, event.kind === "movement" ? "行情异动" : event.alert ? "新闻 · 告警" : "新闻"), el("time", "", event.time ? formatDate(event.time,true) : "数据时间未提供"));
    const button = el("button", "event-title", event.kind === "movement" ? `${item.name || item.symbol} · 时段涨跌 ${signedPercent(movementChangePercent(item))}` : newsTitle(item));
    if (event.kind === "news") appendNewsTranslation(button, item);
    button.type = "button";
    button.addEventListener("click", () => event.kind === "movement" ? openMovementDetail(item) : openDetail(item));
    const body = el("div", "event-row-body");
    body.append(button, el("p", "", item.reason_summary || item.analysis_summary || "暂无已核实的分析结论"));
    const evidence = el("div", "event-row-evidence");
    evidence.append(el("span", "", `${marketLabels[item.market] || item.market || ""} · ${item.symbol || ""}`));
    if (event.kind === "movement") evidence.append(el("span", "", item.reason_category === "unconfirmed" ? "原因待核实" : "公开信息关联线索"));
    else if (/^https?:\/\//i.test(item.url || "")) {
      const link = el("a", "", item.source || "原文网站");
      link.href = item.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      evidence.append(link);
    }
    row.append(meta, body, evidence); container.append(row);
  });
}

function compassDestination(label, note, target, direction) {
  const button = el("button", `compass-point compass-${direction}`);
  button.type = "button";
  button.append(el("strong", "", label), el("span", "", note));
  button.addEventListener("click", () => setView(target));
  return button;
}

function renderCompass() {
  const container = document.getElementById("overview-compass");
  if (!container || container.hidden) return;
  const active = document.activeElement?.closest?.(".compass-point")?.className;
  const data = state.dashboard || {}, assets = state.assets?.assets || [];
  const positions = data.portfolio?.positions || [];
  const events = overviewEvents(currentNews(), positions);
  const dial = el("div", "compass-dial");
  const center = el("div", "compass-center");
  center.append(el("strong", "", "市场罗盘"));
  dial.append(center,
    compassDestination("跨资产", "黄金 · BTC · 原油 · 美债", "assets", "north"),
    compassDestination("政策与情绪", "Trump · 公开讨论", "social", "east"),
    compassDestination("公司研究", "基本面 · 财报 · 复盘", "research", "south"),
    compassDestination("市场事件", "新闻 · 异动 · 告警", "signals", "west"));
  const observations = el("div", "compass-observations");
  observations.append(el("h2", "", "资产方向快照"));
  for (const [key, name] of Object.entries(ASSET_NAMES)) {
    const item = assets.find(row => row.key === key), row = el("div", "compass-quote");
    const label = el("button", "text-button", name); label.type = "button"; label.addEventListener("click",()=>setView("assets"));
    const change = key === "US5Y" ? item?.change_bps : item?.change_24h;
    row.append(label, el("strong", change == null ? "" : change >= 0 ? "positive" : "negative", change == null ? "--" : key === "US5Y" ? `${Number(change).toFixed(2)} bp` : signedPercent(change)),
      el("small", "", item ? `${key === "US5Y" ? "收益率变化" : "近24小时"} · ${formatDate(item.price_time,true)} · ${{live:"有效报价",delayed:"延迟",closed:"休市",stale:"上次报价"}[item.quote_status] || "待确认"}` : "待获取"));
    observations.append(row);
  }
  observations.append(el("p", "quiet-note", "展示来源报价变化，不是收益预测；美债一栏为收益率，不是债券价格。"));
  const focus = el("section", "compass-focus");
  focus.append(el("h2", "", "持仓重点变化"));
  if (!events.holdings.length) focus.append(el("p", "quiet-note", positions.length ? "当前窗口暂无匹配事件" : "尚未录入持仓"));
  events.holdings.slice(0,3).forEach(item => {
    const button = el("button", "event-title", `${item.symbol} · ${newsTitle(item)}`); button.type="button";
    appendNewsTranslation(button, item);
    button.addEventListener("click",()=>openDetail(item)); focus.append(button);
  });
  container.replaceChildren(dial, observations, focus);
  if (active) [...container.querySelectorAll(".compass-point")].find(node=>node.className===active)?.focus({preventScroll:true});
}

function initWorkbench() {
  initNativeWindowControls();
  setOverviewLayout(workbenchState.layout);
  document.querySelectorAll("#overview-layout button").forEach(button => button.addEventListener("click", () => {
    setOverviewLayout(button.dataset.layout); saveStartupPreferences();
  }));
  ["event-search", "event-scope", "event-sort"].forEach(id => document.getElementById(id).addEventListener(id === "event-search" ? "input" : "change", () => {
    workbenchState.eventLimit = 60; renderEventCenter(); saveStartupPreferences();
  }));
  document.getElementById("event-more").addEventListener("click", () => { workbenchState.eventLimit += 60; renderEventCenter(); });
  // Native disclosure keeps the same keyboard behavior as Market Monitoring.
  ["nav-market", "nav-research"].forEach(id => {
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
