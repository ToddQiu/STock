"use strict";

const indicesState = {data: null, pending: null, lastFetch: 0, error: "", root: null, draft: null, saving: false, revision: 0, charts: new Map()};
const indicesDefinitions = [
  {id: "kospi", symbol: "^KS11", name: "韩国综合指数", short_name: "KOSPI", market: "KR", timezone: "Asia/Seoul", news_query: '"KOSPI" index'},
  {id: "star50", symbol: "000688.SS", name: "科创50", short_name: "000688", market: "CN", timezone: "Asia/Shanghai", news_query: '"科创50" 指数'},
  {id: "sox", symbol: "^SOX", name: "费城半导体指数", short_name: "SOX", market: "US", timezone: "America/New_York", news_query: '"PHLX Semiconductor" index'},
  {id: "nasdaq", symbol: "^IXIC", name: "纳斯达克综合指数", short_name: "IXIC", market: "US", timezone: "America/New_York", news_query: '"Nasdaq Composite" index'},
];

function indexCatalog() { return indicesState.data?.catalog || indicesDefinitions; }
function selectedIndices() {
  const catalog = indexCatalog();
  const ids = indicesState.data?.selected_ids || indicesDefinitions.map(d => d.id);
  return ids.map(id => catalog.find(d => d.id === id)).filter(Boolean);
}

function indexAction(icon, label, action) {
  const button = el("button", "icon-button"); button.type = "button";
  button.title = label; button.setAttribute("aria-label", label); button.append(uiIcon(icon));
  button.addEventListener("click", action); return button;
}

function renderIndexEditor() {
  const list = document.getElementById("indices-selection"); list.replaceChildren();
  const catalog = indexCatalog(), draft = indicesState.draft;
  draft.forEach((id, index) => {
    const row = el("div", "indices-selection-row");
    const select = el("select"); select.setAttribute("aria-label", `第 ${index + 1} 列指数`);
    catalog.filter(d => d.id === id || !draft.includes(d.id)).forEach(d => {
      const option = el("option", "", `${d.name} · ${d.short_name}`); option.value = d.id; select.append(option);
    });
    select.value = id;
    select.addEventListener("change", () => { draft[index] = select.value; renderIndexEditor(); document.querySelectorAll("#indices-selection select")[index]?.focus(); });
    const move = step => {
      [draft[index], draft[index + step]] = [draft[index + step], draft[index]];
      renderIndexEditor(); document.querySelectorAll("#indices-selection select")[index + step]?.focus();
    };
    const before = indexAction("arrow-up", `${catalog.find(d => d.id === id).name} 前移`, () => move(-1)); before.disabled = index === 0;
    const after = indexAction("arrow-down", `${catalog.find(d => d.id === id).name} 后移`, () => move(1)); after.disabled = index === draft.length - 1;
    const remove = indexAction("x", `移除 ${catalog.find(d => d.id === id).name}`, () => {
      draft.splice(index, 1); renderIndexEditor();
      (document.querySelectorAll("#indices-selection select")[Math.min(index, draft.length - 1)] || document.getElementById("indices-add")).focus();
    });
    row.append(el("span", "quiet-note", String(index + 1)), select, before, after, remove); list.append(row);
  });
  if (!draft.length) list.append(el("p", "quiet-note", "未选择指数。保存后暂停指数采集，可随时重新添加。"));
  const add = document.getElementById("indices-add");
  const remaining = catalog.filter(d => !draft.includes(d.id));
  add.replaceChildren(...remaining.map(d => {const option = el("option", "", d.name); option.value = d.id; return option;}));
  add.disabled = !remaining.length; document.getElementById("indices-add-button").disabled = !remaining.length;
}

async function saveIndexSelection(event) {
  event.preventDefault(); if (indicesState.saving) return;
  indicesState.saving = true; ++indicesState.revision;
  const form = event.currentTarget, fields = form.querySelector("fieldset"); fields.disabled = true;
  const message = document.getElementById("indices-edit-message"); message.textContent = "正在保存…";
  try {
    // Finish earlier reads before saving, so cached preferences cannot roll back the new list.
    if (indicesState.pending) await indicesState.pending;
    const data = await api("/api/indices/settings", {method: "POST", body: {selected_ids: indicesState.draft.slice()}});
    indicesState.error = ""; indicesState.lastFetch = 0; renderIndices(data);
    if (typeof rememberStartupResponse === "function") rememberStartupResponse("/api/indices", data);
    form.hidden = true; document.getElementById("indices-manage").setAttribute("aria-expanded", "false");
    document.getElementById("indices-manage").focus();
  } catch (error) { message.textContent = `未保存：${error.message}。原有设置不变。`; }
  finally { indicesState.saving = false; fields.disabled = false; }
}

function indexNumber(value, signed = false) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return `${signed && value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
}

function indexTime(value, zone) {
  if (!value || (typeof value !== "number" && !/(Z|[+-]\d{2}:\d{2})$/.test(value))) return "--";
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

function indexReference(definition, row) {
  const expected = {sox: ["SOXX", "ETF", "USD"], nasdaq: ["NQ.CFD", "CFD", "points"], nasdaq100: ["NQ.CFD", "CFD", "points"]}[definition.id];
  const reference = row?.reference;
  return expected && reference?.active === true && reference.symbol === expected[0]
    && reference.instrument_type === expected[1] && reference.unit === expected[2]
    && typeof reference.price === "number" && Number.isFinite(reference.price) && reference.price > 0
    && indexTime(reference.quote_time, "America/New_York") !== "--" ? reference : null;
}

function indexChartItem(definition, row) {
  const reference = indexReference(definition, row);
  if (!reference) return {...definition, instrument_type:"INDEX", unit:"points", chartBrief:"指数点位"};
  const name = reference.symbol === "SOXX" ? "SOXX 半导体 ETF" : "纳指 CFD";
  return {id:definition.id, market:"US", symbol:reference.symbol, name, short_name:reference.symbol === "SOXX" ? "SOXX ETF参考" : "纳指CFD参考",
    timezone:reference.timezone || (reference.instrument_type === "CFD" ? "Asia/Shanghai" : "America/New_York"), instrument_type:reference.instrument_type, unit:reference.unit,
    currency:"USD", chartBrief:`${definition.name}盘外参考；${name}自身报价与涨跌幅。`};
}

function indexQuote(definition) {
  return (indicesState.data?.quotes || []).find(item => item?.id === definition.id && item.symbol === definition.symbol && item.instrument_type === "INDEX") || {};
}

function indexChartKey(definition, item) { return item.symbol === definition.symbol ? definition.id : `${definition.id}:${item.symbol}`; }

function indexReferenceUnavailable(definition, row) {
  const reference = row?.reference, expected = {sox:[["SOXX","ETF"]],nasdaq:[["NQ=F","FUTURE"],["NQ.CFD","CFD"]],nasdaq100:[["NQ=F","FUTURE"],["NQ.CFD","CFD"]]}[definition.id];
  if (!expected || !reference || reference.active || !expected.some(([symbol,type]) => reference.symbol === symbol && reference.instrument_type === type)
      || !["closed","break"].includes(row.session_state) || !(reference.delay_seconds > 0)) return null;
  return {label:"盘外实时未取得", detail:`${reference.symbol === "SOXX" ? "SOXX ETF" : reference.instrument_type === "CFD" ? "纳指 CFD" : "纳指100期货"}来源延迟 ${reference.delay_seconds / 60} 分钟，未启用为主报价；保留指数末次报价。`};
}

function indexLink(label, value, definition, kind) {
  try {
    const url = new URL(value);
    const symbol = encodeURIComponent(definition.symbol);
    const allowed = kind === "source"
      ? (url.hostname === "finance.yahoo.com" && url.pathname === `/quote/${symbol}/`)
        || (definition.id === "kospi" && url.hostname === "m.stock.naver.com" && url.pathname === "/domestic/index/KOSPI/total")
        || (definition.id === "star50" && definition.symbol === "000688.SS" && url.hostname === "gu.qq.com" && url.pathname === "/sh000688")
        || (["nasdaq","nasdaq100"].includes(definition.id) && definition.symbol === "NQ.CFD" && url.hostname === "finance.sina.com.cn" && url.pathname === "/futures/quotes/NQ.shtml")
      : url.hostname === "www.bing.com" && url.pathname === "/news/search" && url.searchParams.get("q") === definition.news_query;
    if (!allowed || url.protocol !== "https:" || url.username || url.password || url.port) return null;
    const link = el("a", "index-link", label);
    link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", `${definition.name} · ${label}（新窗口）`);
    return link;
  } catch (_) { return null; }
}

function indexCard(definition, row) {
  const original = row, reference = indexReference(definition, row), chartItem = indexChartItem(definition, row);
  const unavailableReference = indexReferenceUnavailable(definition, original);
  if (reference) row = {...reference, cached: reference.cached || indicesState.data?._startup_cache || Boolean(indicesState.error)};
  const card = el("article", "index-card");
  card.dataset.index = definition.id;
  const heading = el("header", "index-card-heading"), title = el("h2", "", definition.name);
  title.id = `index-heading-${definition.id}`;
  card.setAttribute("aria-labelledby", title.id);
  heading.append(title, el("span", "index-symbol", definition.short_name));
  heading.append(minuteChartButton(chartItem));
  const hasPrice = typeof row.price === "number" && Number.isFinite(row.price) && row.price > 0;
  const status = row.restored_from_cache || row.cached ? "stale" : row.status || "waiting";
  const labels = {waiting: "等待报价", unavailable: "行情暂不可用", live: "实时", stale: "待更新",
    delayed: "延迟行情", available: "已取得报价", closed: "休市 · 末次报价", break: "午间休市 · 末次报价"};
  const price = el("p", "index-value");
  price.append(el("span", "index-number", hasPrice ? indexNumber(row.price) : "--"), el("small", "index-unit", chartItem.unit === "USD" ? "美元" : "点"));
  const hasChange = hasPrice && typeof row.change_pct === "number" && Number.isFinite(row.change_pct);
  const change = el("p", `index-change${hasChange ? row.change_pct > 0 ? " index-up" : row.change_pct < 0 ? " index-down" : "" : ""}`,
    hasChange ? `${indexNumber(row.change_pct, true)}%` : "--");
  change.append(el("span", "", reference ? row.baseline_label || "较参考标的前收盘" : "较前收盘"));
  if (definition.id === "kospi" && hasPrice) {
    const quoteTime = indexTime(row.quote_time, "Asia/Shanghai");
    const liveTime = el("span", "index-quote-time", `${labels[status] || "待确认"} ${quoteTime === "--" ? "" : quoteTime.slice(11)}`);
    liveTime.title = `报价时间（东八区）${quoteTime}；每 10 秒检查更新`;
    change.append(liveTime);
  }
  const metrics = el("dl", "index-metrics");
  if (reference) {
    metrics.append(indexMetric("参考前收", indexNumber(row.previous_close)), indexMetric("指数末次点位", indexNumber(original.price)),
      indexMetric("指数涨跌幅", typeof original.change_pct === "number" && Number.isFinite(original.change_pct) ? `${indexNumber(original.change_pct, true)}%` : "--"));
  } else metrics.append(indexMetric("前收盘", indexNumber(row.previous_close)), indexMetric("日内高点", indexNumber(row.day_high)),
    indexMetric("日内低点", indexNumber(row.day_low)));
  const zoneLabel = {"Asia/Seoul":"首尔", "Asia/Shanghai":"上海", "America/New_York":"纽约"}[chartItem.timezone] || chartItem.timezone;
  let delay = "延迟情况未披露";
  if (row.delay_status === "delayed") delay = typeof row.delay_seconds === "number" && row.delay_seconds > 0
    ? `来源标注延迟 ${row.delay_seconds / 60} 分钟` : "来源标注延迟 · 时长未披露";
  if (row.delay_status === "reported_realtime") delay = "来源标注实时 · 非实时保证";
  const session = reference ? row.label || {pre:"盘前参考",post:"盘后参考",regular:"期货交易时段",closed:"休市时段"}[row.session] || "参考时段待确认"
    : {open: "来源交易时段内", closed: "休市时段", break: "午间休市", unknown: "交易状态待确认"}[row.session_state] || "交易状态待确认";
  price.title = [status === "stale" ? "上次有效报价" : labels[status] || "状态待确认",
    reference ? `${chartItem.name} · 盘外参考` : "",
    `${status === "stale" ? "上次报价时间" : "数据时间"} · ${zoneLabel} ${indexTime(row.quote_time, chartItem.timezone)}`,
    `源确认 · 北京时间 ${indexTime(row.fetched_at, "Asia/Shanghai")}`, delay, session,
    row.session_basis === "regular_hours_not_holiday_calendar" ? "常规时段判断 · 未核验节假日" : "",
    row.error && !hasPrice ? "来源暂不可用，等待重试。" : "",
    reference ? `${definition.name}末次 ${indexNumber(original.price)} 点 · ${indexTime(original.quote_time, definition.timezone)}` : "",
    unavailableReference?.detail || ""].filter(Boolean).join("\n");
  price.tabIndex = 0;
  price.setAttribute("aria-label", `${price.textContent}，${price.title}`);
  const links = el("footer", "index-links");
  const sourceName = row.source === "Naver Finance" ? "Naver Finance" : definition.id === "star50" && row.source_url === "https://gu.qq.com/sh000688" ? "腾讯财经"
    : reference?.symbol === "NQ.CFD" && row.source_url === "https://finance.sina.com.cn/futures/quotes/NQ.shtml" ? "新浪财经" : "Yahoo Finance";
  const source = indexLink(`${sourceName} 原始报价`, row.source_url, chartItem, "source");
  const news = original.news_kind === "external_search" ? indexLink("检索指数相关新闻", original.news_url, definition, "news") : null;
  if (source) links.append(source);
  if (news) links.append(news);
  const chart = el("button", "index-chart"); chart.type = "button";
  chart.dataset.symbol = chartItem.symbol;
  chart.title = `${chartItem.name} · ${chartItem.instrument_type === "CFD" ? "查看分时采样" : "查看分钟 K、日 K、周 K"}`;
  chart.setAttribute("aria-label", `${chartItem.name}${reference ? "盘外参考" : ""}分时走势，打开${chartItem.instrument_type === "CFD" ? "分时采样" : " K 线"}`);
  const canvas = el("canvas"); canvas.width = 560; canvas.height = 144;
  canvas.setAttribute("aria-hidden", "true");
  chart.append(canvas, el("span", "index-chart-status", reference ? `${chartItem.short_name} · 加载中` : unavailableReference?.label || "分时行情加载中"));
  chart.addEventListener("click", () => openPortfolioChart(chartItem, "intraday"));
  card.append(heading, price, change, chart, metrics, links);
  if (!hasPrice) card.append(el("p", row.error ? "index-error" : `index-status index-status-${status}`,
    row.error ? "来源暂不可用，等待重试。" : status === "waiting" ? "等待报价" : "暂无可用报价"));
  return card;
}

function renderIndexChart(definition) {
  const chart = document.querySelector(`[data-index="${definition.id}"] .index-chart`);
  if (!chart) return;
  const row = indexQuote(definition), reference = indexReference(definition, row), item = indexChartItem(definition, row), unavailableReference = indexReferenceUnavailable(definition, row);
  const entry = indicesState.charts.get(indexChartKey(definition, item));
  const canvas = chart.querySelector("canvas"), status = chart.querySelector(".index-chart-status");
  const points = (entry?.data?.candles || []).map(c => [c.timestamp, c.close]);
  canvas.hidden = points.length < 2;
  chart.dataset.hasPoints = String(points.length > 1);
  const sessionDate = entry?.data?.session_date;
  const sessionLabel = reference ? `${item.short_name}${reference.instrument_type === "CFD" ? "" : ` · ${reference.label || "盘外"}`}` : unavailableReference?.label || (definition.market === "US" && sessionDate ? `${sessionDate.slice(5).replace("-", "/")} 盘中 · 美东` : "");
  status.textContent = entry?.error ? (reference ? `${item.short_name} · ${points.length > 1 ? "上次走势" : "走势暂不可用"}` : points.length > 1 ? "上次分时走势" : "分时行情暂不可用")
    : entry?.data ? entry.data.retained_from_cache ? `上次分时走势 ${sessionLabel}`.trim() : sessionLabel : reference ? `${item.short_name} · 加载中` : unavailableReference?.label || "分时行情加载中";
  status.hidden = !status.textContent;
  chart.title = `${item.name} · ${item.instrument_type === "CFD" ? "查看分时采样" : "查看分钟 K、日 K、周 K"}${entry?.data ? `\n数据截至 ${indexTime(entry.data.stats?.latest_timestamp * 1000, item.timezone)}` : ""}${unavailableReference ? `\n${unavailableReference.detail}` : ""}`;
  if (points.length > 1) drawAssetSparkline(canvas, points, points.at(-1)[1] >= points[0][1]);
}

function refreshIndexCharts() {
  selectedIndices().forEach(definition => {
    renderIndexChart(definition);
    const item = indexChartItem(definition, indexQuote(definition)), key = indexChartKey(definition, item);
    const previous = indicesState.charts.get(key);
    if (previous?.pending || Date.now() < (previous?.nextFetch || 0)) return;
    const entry = {...previous, pending:true};
    indicesState.charts.set(key, entry);
    const query = new URLSearchParams({market:item.market, symbol:item.symbol, mode:"intraday", period:"1d"});
    api(`/api/portfolio/chart?${query}`).then(data => {
      if (data.symbol && data.symbol !== item.symbol) throw new Error("分时行情主体不符");
      entry.data = data; entry.error = false;
    }).catch(error => {
      entry.error = true;
      entry.retryAfter = Number(error.retryAfterSeconds) || 60;
    }).finally(() => {
      entry.pending = false;
      const interval = definition.id === "kospi" ? 10 : item.instrument_type === "CFD" ? 20 : 60;
      entry.nextFetch = Date.now() + (entry.error ? Math.max(interval, entry.retryAfter) : interval) * 1000;
      renderIndexChart(definition);
    });
  });
}

function initIndexMonitor() {
  const root = document.getElementById("index-monitor");
  if (!root || indicesState.root === root) return;
  indicesState.root = root;
  const toolbar = el("div", "indices-toolbar");
  const refresh = el("button", "icon-button indices-refresh");
  refresh.id = "indices-refresh"; refresh.type = "button";
  refresh.title = "刷新指数快照"; refresh.setAttribute("aria-label", "刷新指数快照");
  refresh.append(uiIcon("refresh-cw"));
  refresh.addEventListener("click", () => refreshIndices());
  const manage = el("button", "secondary-button", "管理指数"); manage.type = "button"; manage.id = "indices-manage";
  manage.prepend(uiIcon("settings")); manage.setAttribute("aria-controls", "indices-editor"); manage.setAttribute("aria-expanded", "false");
  manage.addEventListener("click", () => {
    if (indicesState.saving) return;
    const form = document.getElementById("indices-editor"); form.hidden = !form.hidden;
    manage.setAttribute("aria-expanded", String(!form.hidden));
    if (!form.hidden) {
      indicesState.draft = selectedIndices().map(d => d.id); renderIndexEditor();
      document.getElementById("indices-edit-message").textContent = "";
      form.querySelector("select")?.focus();
    }
  });
  const previous = indexAction("arrow-left", "向左查看指数", () => grid.scrollBy({left: -grid.clientWidth, behavior: "auto"})); previous.id = "indices-left";
  const next = indexAction("arrow-right", "向右查看指数", () => grid.scrollBy({left: grid.clientWidth, behavior: "auto"})); next.id = "indices-right";
  toolbar.append(manage, previous, next, refresh);
  const message = el("p", "indices-message"); message.id = "indices-message"; message.setAttribute("role", "status");
  const grid = el("div", "indices-grid"); grid.id = "indices-grid";
  grid.tabIndex = 0; grid.setAttribute("role", "region"); grid.setAttribute("aria-label", "指数行情横向列表");
  grid.addEventListener("scroll", updateIndexScroll);
  new ResizeObserver(updateIndexScroll).observe(grid);
  grid.addEventListener("keydown", event => {
    if (event.target !== grid || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home" || event.key === "End") grid.scrollLeft = event.key === "Home" ? 0 : grid.scrollWidth;
    else grid.scrollLeft += (event.key === "ArrowRight" ? 1 : -1) * grid.clientWidth;
  });
  const editor = el("form", "indices-editor"); editor.id = "indices-editor"; editor.hidden = true;
  const fields = el("fieldset"), legend = el("legend", "", "自选指数与显示顺序");
  const selection = el("div"); selection.id = "indices-selection";
  const actions = el("div", "indices-editor-actions"), add = el("select"); add.id = "indices-add"; add.setAttribute("aria-label", "选择要添加的指数");
  const addButton = el("button", "secondary-button", "添加指数"); addButton.type = "button"; addButton.id = "indices-add-button"; addButton.prepend(uiIcon("plus"));
  addButton.addEventListener("click", () => {if (add.value && !indicesState.draft.includes(add.value)) {indicesState.draft.push(add.value); renderIndexEditor();}});
  const save = el("button", "primary-button", "保存设置"); save.type = "submit";
  const cancel = el("button", "text-button", "取消"); cancel.type = "button"; cancel.addEventListener("click", () => manage.click());
  actions.append(add, addButton, save, cancel);
  const editMessage = el("p", "indices-message"); editMessage.id = "indices-edit-message"; editMessage.setAttribute("role", "status");
  fields.append(legend, selection, actions); editor.append(fields, editMessage); editor.addEventListener("submit", saveIndexSelection);
  root.replaceChildren(toolbar, editor, message, grid);
  renderIndices();
}

function updateIndexScroll() {
  const grid = document.getElementById("indices-grid"); if (!grid) return;
  document.getElementById("indices-left").disabled = grid.scrollLeft < 2;
  document.getElementById("indices-right").disabled = grid.scrollLeft + grid.clientWidth >= grid.scrollWidth - 2;
}

function renderOverviewIndices() {
  const container = document.getElementById("overview-index-quotes");
  if (!container) return;
  const focused = document.activeElement?.closest?.("[data-overview-index]")?.dataset.overviewIndex;
  const scroll = container.scrollLeft, snapshot = indicesState.data;
  const quotes = Array.isArray(snapshot?.quotes) ? snapshot.quotes : [];
  const buttons = selectedIndices().map(definition => {
    const original = quotes.find(item => item.id === definition.id && item.symbol === definition.symbol && item.instrument_type === "INDEX") || {};
    const reference = indexReference(definition, original), row = reference || original, item = indexChartItem(definition, original), unavailableReference = indexReferenceUnavailable(definition, original);
    const priced = typeof row.price === "number" && Number.isFinite(row.price) && row.price > 0;
    const change = priced && typeof row.change_pct === "number" && Number.isFinite(row.change_pct) ? row.change_pct : null;
    const cached = snapshot?._startup_cache || indicesState.error || row.restored_from_cache || row.cached
      || (!reference && (snapshot?.restored_from_cache || original.restored_from_cache || original.cached));
    const status = !priced ? "待获取" : cached || row.status === "stale" ? "上次报价" : unavailableReference?.label ||
      ({closed:"休市",break:"午休",delayed:"延迟",available:"已报价",live:"实时"}[row.status] || "待确认");
    const time = indexTime(row.quote_time, "Asia/Shanghai");
    const button = el("button", "overview-index"); button.type = "button"; button.dataset.overviewIndex = definition.id;
    const name = el("span", "overview-index-name", reference ? `${definition.id === "sox" ? "费半" : definition.id === "nasdaq100" ? "纳指100" : "纳指"}参考 · ${item.name}` : definition.name);
    const values = el("span", "overview-index-values");
    values.append(el("strong", "", priced ? `${indexNumber(row.price)}${reference?.unit === "USD" ? " 美元" : ""}` : "--"),
      el("strong", change == null || change === 0 ? "neutral" : change > 0 ? "positive" : "negative", change == null ? "--" : `${indexNumber(change, true)}%`));
    button.append(name, values, el("small", "overview-index-time", `${status} · ${time === "--" ? "时间未提供" : time.slice(5,16)}`));
    const baseline = reference ? row.baseline_label || "较参考标的前收盘" : "较前收盘";
    button.title = `${definition.name} (${definition.symbol})${reference ? `\n${item.name} · 盘外参考\n指数末次 ${indexNumber(original.price)} 点 · ${indexTime(original.quote_time, "Asia/Shanghai")}` : ""}\n${status} · 报价时间（东八区）${time}\n涨跌幅${baseline}；打开指数监测${unavailableReference ? `\n${unavailableReference.detail}` : ""}`;
    button.setAttribute("aria-label", `${reference ? name.textContent : definition.name}，${priced ? indexNumber(row.price) + (item.unit === "USD" ? "美元" : "点") : "报价未取得"}，${baseline} ${change == null ? "涨跌幅未取得" : indexNumber(change, true) + "%"}，${status}，打开指数监测`);
    button.addEventListener("click", () => {
      setView("indices");
      const heading = document.getElementById(`index-heading-${definition.id}`);
      if (heading) { heading.tabIndex = -1; heading.focus({preventScroll:true}); heading.scrollIntoView({block:"nearest",inline:"center"}); }
    });
    return button;
  });
  container.replaceChildren(...buttons);
  if (!buttons.length) container.append(el("span", "quiet-note", "未选择指数"));
  container.scrollLeft = scroll;
  if (focused) buttons.find(button => button.dataset.overviewIndex === focused)?.focus({preventScroll:true});
}

function renderIndices(data) {
  if (data !== undefined) indicesState.data = data;
  if (!document.getElementById("index-monitor")) return;
  initIndexMonitor();
  const snapshot = indicesState.data;
  const rows = Array.isArray(snapshot?.quotes) ? snapshot.quotes : [];
  const grid = document.getElementById("indices-grid");
  grid.setAttribute("aria-busy", String(Boolean(indicesState.pending)));
  const cards = selectedIndices().map(definition => {
    const row = rows.find(item => item?.id === definition.id && item.symbol === definition.symbol && item.instrument_type === "INDEX") || {};
    return indexCard(definition, row);
  });
  const focusedLink = document.activeElement?.closest?.(".index-card a");
  const focusedHref = focusedLink && grid.contains(focusedLink) ? focusedLink.href : null;
  const focusedChart = document.activeElement?.closest?.(".index-card button");
  const focusedIndex = focusedChart?.closest(".index-card")?.dataset.index;
  const scroll = grid.scrollLeft;
  grid.replaceChildren(...cards);
  if (state.currentView === "indices") refreshIndexCharts();
  if (!cards.length) grid.append(el("p", "indices-empty", "暂无自选指数，可在管理指数中添加。"));
  grid.scrollLeft = scroll;
  updateIndexScroll();
  if (focusedHref) [...grid.querySelectorAll("a")].find(link => link.href === focusedHref)?.focus({preventScroll: true});
  if (focusedIndex) cards.find(card => card.dataset.index === focusedIndex)?.querySelector(`.${focusedChart.className}`)?.focus({preventScroll:true});
  const caption = document.getElementById("indices-caption");
  caption.textContent = snapshot?.updated_at ? `快照检查 ${indexTime(snapshot.updated_at, "Asia/Shanghai")} · 北京时间` : "等待指数行情";
  caption.title = snapshot?.restored_from_cache ? "本地保留的快照；各指数实际报价时间见卡片。" : "快照检查时间不等于交易所报价时间；各指数报价时间见卡片。";
  document.getElementById("indices-message").textContent = indicesState.error;
  const refresh = document.getElementById("indices-refresh");
  refresh.disabled = Boolean(indicesState.pending);
  refresh.setAttribute("aria-busy", String(Boolean(indicesState.pending)));
  renderOverviewIndices();
}

function refreshIndices({silent = false} = {}) {
  if (indicesState.saving) return Promise.resolve(indicesState.data);
  if (indicesState.pending) return indicesState.pending;
  if (silent && Date.now() - indicesState.lastFetch < 9000) {
    if (state.currentView === "indices") refreshIndexCharts();
    return Promise.resolve(indicesState.data);
  }
  indicesState.error = "";
  const revision = indicesState.revision;
  // The parent owns polling and navigation; this module never starts a second timer.
  indicesState.pending = Promise.resolve().then(() => api("/api/indices")).then(data => {
    if (!data || data.schema_version !== 1 || !Array.isArray(data.quotes)) throw new Error("指数快照格式无效");
    if (revision !== indicesState.revision) return indicesState.data;
    indicesState.lastFetch = Date.now(); indicesState.data = data;
    return data;
  }).catch(error => {
    if (revision !== indicesState.revision) return indicesState.data;
    indicesState.error = `行情读取失败：${error.message || "连接中断"}。保留上次结果，可重新刷新。`;
    if (indicesState.data) indicesState.data = {...indicesState.data, restored_from_cache: true,
      quotes: indicesState.data.quotes.map(row => ({...row, restored_from_cache: true}))};
    return indicesState.data;
  }).finally(() => { indicesState.pending = null; renderIndices(); });
  renderIndices();
  return indicesState.pending;
}
