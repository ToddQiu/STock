"use strict";

const indicesState = {data: null, pending: null, lastFetch: 0, error: "", root: null};
const indicesDefinitions = [
  {id: "kospi", symbol: "^KS11", name: "韩国综合指数", short_name: "KOSPI", market: "KR", timezone: "Asia/Seoul", news_query: '"KOSPI" index'},
  {id: "star50", symbol: "000688.SS", name: "科创50", short_name: "000688", market: "CN", timezone: "Asia/Shanghai", news_query: '"科创50" 指数'},
  {id: "sox", symbol: "^SOX", name: "费城半导体指数", short_name: "SOX", market: "US", timezone: "America/New_York", news_query: '"PHLX Semiconductor" index'},
  {id: "nasdaq", symbol: "^IXIC", name: "纳斯达克综合指数", short_name: "IXIC", market: "US", timezone: "America/New_York", news_query: '"Nasdaq Composite" index'},
];

function indexNumber(value, signed = false) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return `${signed && value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
}

function indexTime(value, zone) {
  if (!value || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return "--";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"}).format(date);
}

function indexMetric(label, value) {
  const row = el("div", "index-metric");
  row.append(el("dt", "", label), el("dd", "", value));
  return row;
}

function indexLink(label, value, definition, kind) {
  try {
    const url = new URL(value);
    const symbol = encodeURIComponent(definition.symbol);
    const allowed = kind === "source"
      ? url.hostname === "finance.yahoo.com" && url.pathname === `/quote/${symbol}/`
      : url.hostname === "www.bing.com" && url.pathname === "/news/search" && url.searchParams.get("q") === definition.news_query;
    if (!allowed || url.protocol !== "https:" || url.username || url.password || url.port) return null;
    const link = el("a", "index-link", label);
    link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", `${definition.name} · ${label}（新窗口）`);
    return link;
  } catch (_) { return null; }
}

function indexCard(definition, row) {
  const card = el("article", "index-card");
  card.dataset.index = definition.id;
  const heading = el("header", "index-card-heading"), title = el("h2", "", definition.name);
  title.id = `index-heading-${definition.id}`;
  card.setAttribute("aria-labelledby", title.id);
  heading.append(title, el("span", "index-symbol", definition.short_name));
  const descriptions = {kospi:"韩国主板市场综合指数，不是单一公司。",star50:"上证科创板50成份指数，代码 000688。",sox:"费城半导体行业指数，不是 SOXX ETF。",nasdaq:"纳斯达克综合指数，不是纳斯达克100。"};
  heading.append(el("p", "index-brief", descriptions[definition.id]));
  const hasPrice = typeof row.price === "number" && Number.isFinite(row.price) && row.price > 0;
  const status = row.restored_from_cache || row.cached ? "stale" : row.status || "waiting";
  const labels = {waiting: "等待报价", unavailable: "行情暂不可用", stale: "末次有效值 · 待确认",
    delayed: "延迟行情", available: "已取得报价", closed: "休市 · 末次报价", break: "午间休市 · 末次报价"};
  const statusLine = el("p", `index-status index-status-${Object.hasOwn(labels, status) ? status : "waiting"}`, labels[status] || "状态待确认");
  const price = el("p", "index-value", hasPrice ? indexNumber(row.price) : "--");
  price.append(el("small", "index-unit", "点"));
  const hasChange = hasPrice && typeof row.change_pct === "number" && Number.isFinite(row.change_pct);
  const change = el("p", `index-change${hasChange ? row.change_pct > 0 ? " index-up" : row.change_pct < 0 ? " index-down" : "" : ""}`,
    hasChange ? `${indexNumber(row.change_pct, true)}%` : "--");
  change.append(el("span", "", "较前收盘"));
  const metrics = el("dl", "index-metrics");
  metrics.append(indexMetric("前收盘", indexNumber(row.previous_close)), indexMetric("日内高点", indexNumber(row.day_high)),
    indexMetric("日内低点", indexNumber(row.day_low)));
  const zoneLabel = {KR: "首尔", CN: "上海", US: "纽约"}[definition.market];
  const times = el("dl", "index-times");
  times.append(indexMetric(`数据时间 · ${zoneLabel}`, indexTime(row.quote_time, definition.timezone)),
    indexMetric("源确认 · 北京时间", indexTime(row.fetched_at, "Asia/Shanghai")));
  let delay = "延迟情况未披露";
  if (row.delay_status === "delayed") delay = typeof row.delay_seconds === "number" && row.delay_seconds > 0
    ? `来源标注延迟 ${row.delay_seconds / 60} 分钟` : "来源标注延迟 · 时长未披露";
  if (row.delay_status === "reported_realtime") delay = "来源标注实时 · 非实时保证";
  const session = {open: "来源交易时段内", closed: "休市时段", break: "午间休市", unknown: "交易状态待确认"}[row.session_state] || "交易状态待确认";
  const note = el("div", "index-provenance");
  note.append(el("p", "", delay), el("p", "", session));
  if (row.session_basis === "regular_hours_not_holiday_calendar") note.append(el("p", "", "常规时段判断 · 未核验节假日"));
  if (row.error) {
    const error = el("p", "index-error", hasPrice ? "来源暂未确认，保留末次有效值。" : "来源暂不可用，等待重试。");
    error.title = String(row.error); note.append(error);
  }
  const links = el("footer", "index-links");
  const source = indexLink("Yahoo Finance 原始报价", row.source_url, definition, "source");
  const news = row.news_kind === "external_search" ? indexLink("检索指数相关新闻", row.news_url, definition, "news") : null;
  if (source) links.append(source);
  if (news) links.append(news);
  card.append(heading, statusLine, price, change, metrics, times, note, links);
  return card;
}

function initIndexMonitor() {
  const root = document.getElementById("index-monitor");
  if (!root || indicesState.root === root) return;
  indicesState.root = root;
  const toolbar = el("div", "indices-toolbar"), caption = el("p", "indices-caption");
  caption.id = "indices-caption";
  const refresh = el("button", "icon-button indices-refresh");
  refresh.id = "indices-refresh"; refresh.type = "button";
  refresh.title = "刷新指数快照"; refresh.setAttribute("aria-label", "刷新指数快照");
  refresh.append(uiIcon("refresh-cw"));
  refresh.addEventListener("click", () => refreshIndices());
  toolbar.append(caption, refresh);
  const message = el("p", "indices-message"); message.id = "indices-message"; message.setAttribute("role", "status");
  const grid = el("div", "indices-grid"); grid.id = "indices-grid";
  root.replaceChildren(toolbar, message, grid);
  renderIndices();
}

function renderIndices(data) {
  if (data !== undefined) indicesState.data = data;
  if (!document.getElementById("index-monitor")) return;
  initIndexMonitor();
  const snapshot = indicesState.data;
  const rows = Array.isArray(snapshot?.quotes) ? snapshot.quotes : [];
  const grid = document.getElementById("indices-grid");
  grid.setAttribute("aria-busy", String(Boolean(indicesState.pending)));
  const cards = indicesDefinitions.map(definition => {
    const row = rows.find(item => item?.id === definition.id && item.symbol === definition.symbol && item.instrument_type === "INDEX") || {};
    return indexCard(definition, row);
  });
  const focusedLink = document.activeElement?.closest?.(".index-card a");
  const focusedHref = focusedLink && grid.contains(focusedLink) ? focusedLink.href : null;
  grid.replaceChildren(...cards);
  if (focusedHref) [...grid.querySelectorAll("a")].find(link => link.href === focusedHref)?.focus({preventScroll: true});
  const caption = document.getElementById("indices-caption");
  caption.textContent = snapshot?.updated_at ? `快照检查 ${indexTime(snapshot.updated_at, "Asia/Shanghai")} · 北京时间` : "等待指数行情";
  document.getElementById("indices-message").textContent = indicesState.error || (snapshot?.restored_from_cache ? "上次快照 · 等待来源确认" : "");
  const refresh = document.getElementById("indices-refresh");
  refresh.disabled = Boolean(indicesState.pending);
  refresh.setAttribute("aria-busy", String(Boolean(indicesState.pending)));
}

function refreshIndices({silent = false} = {}) {
  if (indicesState.pending) return indicesState.pending;
  if (silent && Date.now() - indicesState.lastFetch < 15000) return Promise.resolve(indicesState.data);
  indicesState.error = "";
  // The parent owns polling and navigation; this module never starts a second timer.
  indicesState.pending = Promise.resolve().then(() => api("/api/indices")).then(data => {
    if (!data || data.schema_version !== 1 || !Array.isArray(data.quotes)) throw new Error("指数快照格式无效");
    indicesState.lastFetch = Date.now(); indicesState.data = data;
    return data;
  }).catch(error => {
    indicesState.error = `行情读取失败：${error.message || "连接中断"}。保留上次结果，可重新刷新。`;
    if (indicesState.data) indicesState.data = {...indicesState.data, restored_from_cache: true,
      quotes: indicesState.data.quotes.map(row => ({...row, restored_from_cache: true}))};
    return indicesState.data;
  }).finally(() => { indicesState.pending = null; renderIndices(); });
  renderIndices();
  return indicesState.pending;
}
