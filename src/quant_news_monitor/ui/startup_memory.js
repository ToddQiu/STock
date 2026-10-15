"use strict";

// Display memory only: never restore credentials, unsaved forms, or server configuration.
const startupMemory = {
  version: 1, responses: new Map(), timer: null, database: null, ready: false, previewAt: null,
  controls: ["news-search", "news-language", "news-sort", "market-filter", "portfolio-search", "event-search", "event-scope", "event-sort",
    "social-search", "social-platform", "social-sort", "social-sample-search", "social-sample-scope",
    "strategy-search", "strategy-stance", "company-search", "company-layer", "company-entity",
    "research-asset", "research-horizon", "research-status", "research-kind",
    "etf-overlap-search", "etf-overlap-filter", "etf-holding-search", "etf-holding-type", "etf-holding-sort",
    "earnings-search", "earnings-days", "earnings-scope", "earnings-sort", "earnings-unknown"],
};
const STARTUP_PREFS_KEY = "quantify.startup.preferences.v1";
const startupPaths = new Set(["/api/dashboard", "/api/assets", "/api/news", "/api/social", "/api/research",
  "/api/research/inputs", "/api/research/cards", "/api/research/companies", "/api/research/earnings", "/api/research/earnings/annotations", "/api/fundamentals", "/api/indices"]);

function startupSafePayload(value) {
  return JSON.parse(JSON.stringify(value, (key, item) =>
    /^(?:password|token|api_key|secret|authorization|feed_url)$|_(?:token|password|secret|api_key)$/i.test(key) ? undefined : item));
}

function startupDatabase() {
  if (startupMemory.database) return startupMemory.database;
  startupMemory.database = new Promise(resolve => {
    if (!window.indexedDB) return resolve(null);
    let request;
    try { request = window.indexedDB.open("quantify-display-memory", 1); } catch { return resolve(null); }
    request.onupgradeneeded = () => request.result.createObjectStore("snapshots");
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = request.onblocked = () => resolve(null);
  });
  return startupMemory.database;
}

async function startupRead() {
  const database = await startupDatabase();
  if (!database) return null;
  return new Promise(resolve => {
    try {
      const request = database.transaction("snapshots").objectStore("snapshots").get("latest");
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}

async function flushStartupMemory() {
  window.clearTimeout(startupMemory.timer);
  startupMemory.timer = null;
  const records = [];
  let bytes = 0;
  for (const [path, record] of [...startupMemory.responses].reverse()) {
    try {
      const payload = startupSafePayload(record.payload), size = JSON.stringify(payload).length * 2;
      if (size > 2_000_000 || bytes + size > 10_000_000) continue;
      records.unshift([path, {saved_at: record.saved_at, payload}]);
      bytes += size;
    } catch { /* Non-serializable responses are not display snapshots. */ }
  }
  const generation = typeof apiReadGeneration === "undefined" ? 0 : apiReadGeneration;
  const database = await startupDatabase();
  if (!database || (typeof apiReadGeneration !== "undefined" && generation !== apiReadGeneration)) return;
  try {
    await new Promise(resolve => {
      const transaction = database.transaction("snapshots", "readwrite");
      transaction.objectStore("snapshots").put({version:startupMemory.version, records}, "latest");
      transaction.oncomplete = transaction.onerror = transaction.onabort = () => resolve();
    });
  } catch { /* Storage may be disabled or full; normal network reads still work. */ }
}

function rememberStartupResponse(path, payload) {
  if (path === "/api/dashboard") {
    startupMemory.ready = true;
    startupMemory.previewAt = null;
  }
  if (!startupPaths.has(path.split("?")[0])) return;
  const firstRead = !startupMemory.responses.has(path);
  startupMemory.responses.delete(path);
  startupMemory.responses.set(path, {saved_at: new Date().toISOString(), payload});
  while (startupMemory.responses.size > 24) startupMemory.responses.delete(startupMemory.responses.keys().next().value);
  if (firstRead) { flushStartupMemory(); return; }
  if (startupMemory.timer === null) startupMemory.timer = window.setTimeout(flushStartupMemory, 10_000);
}

function invalidateStartupMemory() {
  startupMemory.responses.clear();
  flushStartupMemory();
}

function captureStartupPreferences() {
  const controls = {};
  for (const id of startupMemory.controls) {
    const node = document.getElementById(id);
    if (node) controls[id] = node.type === "checkbox" ? node.checked : node.value;
  }
  return {
    version: 1, presentationVersion: 2, view: state.currentView, controls,
    overviewLayout: typeof workbenchState === "undefined" ? "compass" : workbenchState.layout,
    state: {sentimentFilter:state.sentimentFilter, movementMarket:state.movementMarket, movementSortDirection:state.movementSortDirection,
      portfolioTypeFilter:state.portfolioTypeFilter, portfolioSortKey:state.portfolioSortKey,
      portfolioSortDirection:state.portfolioSortDirection},
    research: typeof researchState === "undefined" ? {} : {
      tab:researchState.tab, companyId:researchState.companyId, etfSelected:researchState.etfSelected,
      etfPair:researchState.etfPair, cardOffset:researchState.cardOffset, offset:researchState.offset, inputOffset:researchState.inputOffset},
    social: typeof socialState === "undefined" ? {} : {view:socialView, scope:socialScope, hours:socialState.hours, selected:socialState.selected},
    earningsOffset: typeof earningsState === "undefined" ? 0 : earningsState.offset,
  };
}

function saveStartupPreferences() {
  try { localStorage.setItem(STARTUP_PREFS_KEY, JSON.stringify(captureStartupPreferences())); } catch {}
}

function applyStartupPreferences(saved) {
  if (!saved || saved.version !== 1) return;
  for (const id of startupMemory.controls) {
    const node = document.getElementById(id), value = saved.controls?.[id];
    if (!node || value === undefined) continue;
    if (node.type === "checkbox") node.checked = value === true;
    else if (typeof value === "string" && (node.tagName !== "SELECT" || [...node.options].some(option => option.value === value))) node.value = value.slice(0, 200);
  }
  if (saved.presentationVersion !== 2) document.getElementById("news-language").value = "all";
  state.newsLanguage = document.getElementById("news-language").value;
  state.newsSort = document.getElementById("news-sort").value;
  state.portfolioSearch = document.getElementById("portfolio-search").value;
  for (const key of ["sentimentFilter", "movementMarket", "portfolioTypeFilter", "portfolioSortKey", "portfolioSortDirection"]) {
    if (typeof saved.state?.[key] === "string") state[key] = saved.state[key];
  }
  if (["asc", "desc"].includes(saved.state?.movementSortDirection)) state.movementSortDirection = saved.state.movementSortDirection;
  if (typeof updateMovementSortControls === "function") updateMovementSortControls();
  if (typeof setOverviewLayout === "function") setOverviewLayout(saved.presentationVersion === 2 ? saved.overviewLayout : "compass");
  for (const [selector, key, attribute] of [["#sentiment-filter button", "sentimentFilter", "value"],
    ["#movement-market-filter button", "movementMarket", "value"], ["[data-portfolio-type]", "portfolioTypeFilter", "portfolioType"]]) {
    document.querySelectorAll(selector).forEach(button => button.classList.toggle("active", button.dataset[attribute] === state[key]));
  }
  if (typeof researchState !== "undefined") {
    const prefs = saved.research || {};
    if ([...document.querySelectorAll("#research-tabs button")].some(button => button.dataset.tab === prefs.tab)) researchState.tab = prefs.tab;
    for (const key of ["companyId", "etfSelected"]) if (typeof prefs[key] === "string") researchState[key] = prefs[key];
    if (Array.isArray(prefs.etfPair) && prefs.etfPair.length === 2 && prefs.etfPair.every(symbol => ["IGV","XSW","WCLD","BUG"].includes(symbol))) researchState.etfPair = prefs.etfPair;
    for (const key of ["cardOffset", "offset", "inputOffset"]) if (Number.isInteger(prefs[key]) && prefs[key] >= 0) researchState[key] = prefs[key];
  }
  if (typeof socialState !== "undefined") {
    const prefs = saved.social || {};
    socialState.platform = document.getElementById("social-platform").value;
    socialState.sort = document.getElementById("social-sort").value;
    if ([1,24,168].includes(prefs.hours)) socialState.hours = prefs.hours;
    if (typeof prefs.selected === "string") socialState.selected = prefs.selected;
    socialSampleState.scope = document.getElementById("social-sample-scope").value;
    socialSampleState.query = document.getElementById("social-sample-search").value;
    socialScope = prefs.scope === "watchlist" ? "watchlist" : "discovery";
    setSocialView(prefs.view);
    document.querySelectorAll("#social-window button").forEach(button => button.classList.toggle("active", Number(button.dataset.hours) === socialState.hours));
    document.querySelectorAll("#social-scope-tabs button").forEach(button => button.classList.toggle("active", button.dataset.scope === socialScope));
  }
  if (typeof earningsState !== "undefined" && Number.isInteger(saved.earningsOffset) && saved.earningsOffset >= 0) earningsState.offset = saved.earningsOffset;
}

function startupPreview(path, record) {
  const data = startupSafePayload(record.payload);
  data._startup_cache = record.saved_at;
  if (path === "/api/indices") {
    data.refresh_in_progress = false;
    data.restored_from_cache = true;
    (data.quotes || []).forEach(quote => {quote.restored_from_cache = true; quote.status = "stale";});
  }
  if (data.runtime) Object.assign(data.runtime, {refresh_in_progress:false, scan_in_progress:false, movement_scan_in_progress:false, restored_from_cache:true});
  if (path === "/api/assets") {
    (data.assets || []).forEach(asset => {asset.quote_status="stale";asset.quote_note="上次快照，等待新报价确认";asset.anomaly={triggered:false};});
    (data.probabilities || []).forEach(row => Object.assign(row, {available:false, up_probability:null, down_probability:null, confidence:null, probability_change:null, signal:"待确认", drivers:["上次快照，等待新报价确认"]}));
    (data.outlooks || []).forEach(item => {item.forecast={available:false, tone:"待确认", text:"上次快照，等待新报价确认"};});
  }
  if (path === "/api/dashboard") {
    const portfolio = data.portfolio;
    if (portfolio) {
      Object.assign(portfolio.runtime, {status:"stale", restored_from_cache:true, refresh_in_progress:false});
      Object.assign(portfolio.summary, {daily_pnl_cny:null, daily_pnl_complete:false, principal_status:"unavailable", principal_pnl_cny:null, principal_return_pct:null, principal_loss_pct:null, stale_valued_count:portfolio.positions.length});
      portfolio.positions.forEach(row => {row.quote_status="stale";row.daily_pnl_cny=null;row.signal_available=false;row.analysis={...row.analysis,direction:"待确认",action:"等待有效数据"};});
    }
    (data.watchlist || []).forEach(item => {if (item.adr_premium) Object.assign(item.adr_premium, {status:"stale", error:"上次快照，等待新报价确认"});});
    if (data.korea_quotes) {
      data.korea_quotes.status="stale";
      (data.korea_quotes.quotes || []).forEach(quote => {quote.status="stale";});
    }
  }
  if (data.trump?.runtime) Object.assign(data.trump.runtime, {status:"stale", connected:false, refresh_in_progress:false, message:"上次帖子快照，当前连接待验证"});
  if (path.startsWith("/api/social?")) {
    Object.assign(data.runtime, {state:"stale", can_refresh:false, connected_count:0, web_connected_count:0, platform_connected_count:0});
    (data.sources || []).forEach(source => {source.status="stale";source.message="上次采集快照，当前连接待验证";});
  }
  if (path.startsWith("/api/research/earnings?")) Object.assign(data.runtime, {stale:true, error:null, warnings:["上次目录快照，正在读取最新记录"]});
  return data;
}

async function restoreStartupMemory() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(STARTUP_PREFS_KEY)); } catch {}
  applyStartupPreferences(saved);
  if (saved?.version === 1 && [...document.querySelectorAll(".view")].some(node => node.dataset.view === saved.view)) setView(saved.view, {refresh:false});
  const bundle = await Promise.race([startupRead(), new Promise(resolve => window.setTimeout(() => resolve(null), 500))]);
  if (bundle?.version === startupMemory.version && Array.isArray(bundle.records)) {
    for (const [path, record] of bundle.records.slice(-24).filter(item => Array.isArray(item) && item.length === 2)) {
      if (typeof path !== "string" || !startupPaths.has(path.split("?")[0]) || !record?.payload || typeof record.payload !== "object" || Array.isArray(record.payload)) continue;
      const age = Date.now() - Date.parse(record.saved_at);
      if (!Number.isFinite(age) || age < -60_000 || age > 30 * 86400_000) continue;
      startupMemory.responses.set(path, record);
    }
  }
  const restore = (path, render) => {
    const record = startupMemory.responses.get(path);
    if (!record) return;
    try {
      render(startupPreview(path, record));
      startupMemory.previewAt = startupMemory.previewAt || record.saved_at;
    } catch { startupMemory.responses.delete(path); }
  };
  if (typeof registerInstrumentEarnings === "function") restore("/api/research/earnings/annotations", registerInstrumentEarnings);
  restore("/api/dashboard", renderDashboard);
  restore("/api/assets", renderAssets);
  if (typeof renderIndices === "function") restore("/api/indices", renderIndices);
  restore(`/api/news?language=${state.newsLanguage}`, data => {state.liveNews=data;renderOverview();applyNewsFilters();});
  if (typeof socialState !== "undefined") {
    const params = new URLSearchParams({hours:socialState.hours, platform:socialState.platform, sample_scope:socialSampleState.scope, sample_query:socialSampleState.query});
    restore(`/api/social?${params}`, data => {socialState.data=data;renderSocial();});
  }
  if (typeof researchState !== "undefined") {
    const paths = [["/api/research/companies", "landscape"], ["/api/fundamentals", "fundamentals"],
      [`/api/research/cards?${new URLSearchParams({search:document.getElementById("strategy-search").value,stance:document.getElementById("strategy-stance").value,offset:researchState.cardOffset})}`, "cards"],
      [`/api/research?${researchFilters()}&offset=${researchState.offset}`, "data"],
      [`/api/research/inputs?kind=${document.getElementById("research-kind").value}&offset=${researchState.inputOffset}`, "inputs"]];
    paths.forEach(([path, field]) => restore(path, data => {researchState[field]=data;}));
    restore("/api/research/inputs?kind=etf_holdings", data => {researchState.etf=data.items.find(item => item.identity === "IGV-XSW-WCLD-BUG") || null;});
    if (typeof earningsState !== "undefined") restore(`/api/research/earnings?${earningsParams()}`, data => {earningsState.data=data;});
    try {
      renderResearch();
      applyStartupPreferences(saved);
      renderResearch();
    } catch {
      for (const field of ["data", "inputs", "cards", "landscape", "fundamentals", "etf"]) researchState[field] = null;
      if (typeof earningsState !== "undefined") earningsState.data = null;
    }
    if (startupMemory.previewAt) document.getElementById("research-runtime").textContent += " · 上次快照";
  }
  if (startupMemory.previewAt) renderConnection();
  document.addEventListener("input", saveStartupPreferences);
  document.addEventListener("change", saveStartupPreferences);
  document.addEventListener("click", () => window.setTimeout(saveStartupPreferences, 0));
  window.addEventListener("pagehide", () => {saveStartupPreferences();flushStartupMemory();});
  document.addEventListener("visibilitychange", () => {if(document.visibilityState === "hidden"){saveStartupPreferences();flushStartupMemory();}});
}
