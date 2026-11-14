"use strict";

// Display memory only: never restore credentials, unsaved forms, or server configuration.
const startupMemory = {
  version: 1, responses: new Map(), timer: null, database: null, ready: false, previewAt: null,
  writes: Promise.resolve(), writeRevision: 0, storageError: null,
  controls: ["news-search", "news-language", "news-sort", "market-filter", "portfolio-search", "event-search", "event-scope", "event-sort",
    "social-search", "social-platform", "social-sort", "social-sample-search", "social-sample-scope",
    "strategy-search", "strategy-stance", "company-search", "company-layer", "company-entity",
    "research-asset", "research-horizon", "research-status", "research-kind",
    "etf-overlap-search", "etf-overlap-filter", "etf-holding-search", "etf-holding-type", "etf-holding-sort",
    "earnings-search", "earnings-days", "earnings-scope", "earnings-sort", "earnings-unknown"],
};
const STARTUP_PREFS_KEY = "quantify.startup.preferences.v1";
const startupPaths = new Set(["/api/dashboard", "/api/assets", "/api/news", "/api/social", "/api/research",
  "/api/research/inputs", "/api/research/etf", "/api/research/cards", "/api/research/companies", "/api/research/earnings", "/api/research/earnings/annotations", "/api/fundamentals", "/api/indices"]);

function startupJson(value) {
  return JSON.stringify(value, (key, item) =>
    /^(?:password|token|api_key|secret|authorization|feed_url)$|_(?:token|password|secret|api_key)$/i.test(key) ? undefined : item);
}

function startupSafePayload(value) {
  return JSON.parse(startupJson(value));
}

async function encodeStartupRecord(record) {
  const json = startupJson(record.payload);
  if (typeof CompressionStream !== "undefined" && typeof DecompressionStream !== "undefined") {
    try {
      const stream = new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"));
      const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
      return {saved_at:record.saved_at, encoding:"gzip", bytes};
    } catch { /* Older webviews can retain the original JSON without dropping the dashboard. */ }
  }
  return {saved_at:record.saved_at, payload:JSON.parse(json)};
}

async function decodeStartupRecord(record) {
  if (record?.encoding !== "gzip") return record;
  const stream = new Blob([record.bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return {saved_at:record.saved_at, payload:JSON.parse(await new Response(stream).text())};
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
  const revision = ++startupMemory.writeRevision;
  const generation = typeof apiReadGeneration === "undefined" ? 0 : apiReadGeneration;
  const current = () => revision === startupMemory.writeRevision &&
    (typeof apiReadGeneration === "undefined" || generation === apiReadGeneration);
  const write = async () => {
    if (!current()) return;
    try {
      const database = await startupDatabase();
      if (!current()) return;
      if (!database) throw new Error("Display cache unavailable");
      const records = [];
      let bytes = 0;
      // Reserve the last usable dashboard before optional page snapshots consume the budget.
      const entries = [...startupMemory.responses].reverse().sort(([a], [b]) => Number(b === "/api/dashboard") - Number(a === "/api/dashboard"));
      for (const [path, record] of entries) {
        record.encoded ||= encodeStartupRecord(record);
        const encoded = await record.encoded;
        if (!current()) return;
        const size = encoded.bytes?.byteLength ?? JSON.stringify(encoded.payload).length * 2;
        if (path !== "/api/dashboard" && bytes + size > 10_000_000) continue;
        records.push([path, encoded]);
        bytes += size;
      }
      await new Promise((resolve, reject) => {
        const transaction = database.transaction("snapshots", "readwrite");
        transaction.objectStore("snapshots").put({version:startupMemory.version, records}, "latest");
        transaction.oncomplete = resolve;
        transaction.onerror = transaction.onabort = () => reject(new Error("Display cache write failed"));
      });
      startupMemory.storageError = null;
    } catch {
      // A failed transaction keeps the previous committed snapshot, never an empty replacement.
      startupMemory.storageError = "启动记忆未能保存，已保留上一次快照";
    }
  };
  startupMemory.writes = startupMemory.writes.then(write, write);
  return startupMemory.writes;
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
  while (startupMemory.responses.size > 24) startupMemory.responses.delete([...startupMemory.responses.keys()].find(key => key !== "/api/dashboard"));
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
    backtestTab: window.BacktestWorkspace?.tab || "workbench",
    state: {sentimentFilter:state.sentimentFilter, movementMarket:state.movementMarket, movementSortDirection:state.movementSortDirection,
      portfolioTypeFilter:state.portfolioTypeFilter, portfolioSortKey:state.portfolioSortKey,
      portfolioSortDirection:state.portfolioSortDirection},
    research: typeof researchState === "undefined" ? {} : {
      tab:researchState.tab, companyId:researchState.companyId, etfSelected:researchState.etfSelected,
      etfSymbols:researchState.etfSymbols, etfPair:researchState.etfPair, cardOffset:researchState.cardOffset, offset:researchState.offset, inputOffset:researchState.inputOffset},
    social: typeof socialState === "undefined" ? {} : {view:socialView, scope:socialScope, hours:socialState.hours, selected:socialState.selected},
    earningsOffset: typeof earningsState === "undefined" ? 0 : earningsState.offset,
    earningsSelected: typeof earningsState === "undefined" ? "" : earningsState.selectedSymbol,
  };
}

function saveStartupPreferences() {
  try { localStorage.setItem(STARTUP_PREFS_KEY, JSON.stringify(captureStartupPreferences())); } catch {}
}

function applyStartupPreferences(saved) {
  if (!saved || saved.version !== 1) return;
  window.BacktestWorkspace?.select(saved.backtestTab || "workbench");
  for (const id of startupMemory.controls) {
    if(id.startsWith("etf-") && typeof researchState!=="undefined" && researchState.etfSelectionEdited)continue;
    if(/^(company|strategy)-/.test(id) && typeof researchState!=="undefined" && researchState.navigationEdited)continue;
    if(id.startsWith("earnings-") && typeof earningsState!=="undefined" && earningsState.selectionEdited)continue;
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
  for (const [selector, key, attribute] of [["#sentiment-filter button", "sentimentFilter", "value"],
    ["#movement-market-filter button", "movementMarket", "value"], ["[data-portfolio-type]", "portfolioTypeFilter", "portfolioType"]]) {
    document.querySelectorAll(selector).forEach(button => button.classList.toggle("active", button.dataset[attribute] === state[key]));
  }
  if (typeof researchState !== "undefined") {
    const prefs = saved.research || {};
    if (!researchState.navigationEdited) {
      const tab=prefs.tab==="fundamentals" ? "earnings" : prefs.tab;
      if ([...document.querySelectorAll("#research-tabs button, #market-nav [data-tab], #backtest-tabs [data-tab], #company-research-views button, #research-evidence-views button")].some(button => button.dataset.tab === tab)) researchState.tab = tab;
      if (typeof prefs.companyId === "string") researchState.companyId = prefs.companyId;
    }
    if (!researchState.etfSelectionEdited) {
      if(typeof prefs.etfSelected === "string")researchState.etfSelected=prefs.etfSelected;
      if (Array.isArray(prefs.etfSymbols) && typeof parseEtfSelection==="function") {
        try {researchState.etfSymbols=parseEtfSelection(prefs.etfSymbols.join(","));}catch { /* Keep defaults for invalid saved values. */ }
      }
      if (researchState.etfSymbols && !researchState.etfSymbols.includes(researchState.etfSelected)) researchState.etfSelected=researchState.etfSymbols[0];
      if (Array.isArray(prefs.etfPair) && prefs.etfPair.length === 2 && prefs.etfPair.every(symbol => (researchState.etfSymbols || ["IGV","XSW","WCLD","BUG"]).includes(symbol))) researchState.etfPair = prefs.etfPair;
    }
    for (const key of ["cardOffset", "offset", "inputOffset"]) if (!researchState.navigationEdited && Number.isInteger(prefs[key]) && prefs[key] >= 0) researchState[key] = prefs[key];
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
  if (typeof earningsState !== "undefined" && !earningsState.selectionEdited) {
    if(Number.isInteger(saved.earningsOffset) && saved.earningsOffset >= 0)earningsState.offset=saved.earningsOffset;
    if(typeof saved.earningsSelected==="string" && /^[A-Z0-9][A-Z0-9.-]{0,19}$/.test(saved.earningsSelected))earningsState.selectedSymbol=saved.earningsSelected;
  }
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

async function restoreStartupBundle(bundle, saved, generation) {
  const current = () => !startupMemory.ready &&
    (typeof apiReadGeneration === "undefined" || generation === apiReadGeneration);
  const restored = new Map();
  if (!current()) return;
  if (bundle?.version === startupMemory.version && Array.isArray(bundle.records)) {
    for (const [path, stored] of bundle.records.slice(-24).filter(item => Array.isArray(item) && item.length === 2)) {
      let record;
      try { record = await decodeStartupRecord(stored); } catch (error) {
        console.warn("Display cache decode failed", path, error?.name);
        continue;
      }
      if (!current()) return;
      if (typeof path !== "string" || !startupPaths.has(path.split("?")[0]) || !record?.payload || typeof record.payload !== "object" || Array.isArray(record.payload)) continue;
      const age = Date.now() - Date.parse(record.saved_at);
      if (!Number.isFinite(age) || age < -60_000 || age > 30 * 86400_000) continue;
      if (!startupMemory.responses.has(path)) {
        startupMemory.responses.set(path, record);
        restored.set(path, record);
      }
    }
  }
  const restore = (path, render) => {
    if (!restored.has(path) || !current()) return;
    const record = startupMemory.responses.get(path);
    if (!record || restored.get(path) !== record) return;
    if (path === "/api/dashboard" && typeof portfolioContext !== "undefined" &&
        (record.payload.portfolio?.id || "default") !== portfolioContext.id) return;
    try {
      render(startupPreview(path, record));
      startupMemory.previewAt = startupMemory.previewAt || record.saved_at;
    } catch (error) {
      console.warn("Display cache render failed", path, error?.message);
      startupMemory.responses.delete(path);
    }
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
  if (typeof researchState !== "undefined" && [...restored.keys()].some(path => path.startsWith("/api/research") || path === "/api/fundamentals")) {
    const paths = [["/api/research/companies", "landscape"], ["/api/fundamentals", "fundamentals"],
      [`/api/research/cards?${new URLSearchParams({search:document.getElementById("strategy-search").value,stance:document.getElementById("strategy-stance").value,offset:researchState.cardOffset})}`, "cards"],
      [`/api/research?${researchFilters()}&offset=${researchState.offset}`, "data"],
      [`/api/research/inputs?kind=${document.getElementById("research-kind").value}&offset=${researchState.inputOffset}`, "inputs"]];
    paths.forEach(([path, field]) => restore(path, data => {researchState[field]=data;}));
    restore("/api/research/inputs?kind=etf_holdings", data => {researchState.etf=data.items.find(item => item.identity === "IGV-XSW-WCLD-BUG") || null;});
    for(const symbol of researchState.etfSymbols || [])restore(`/api/research/etf?symbol=${encodeURIComponent(symbol)}`,data=>{
      researchState.etfRemote ||= {};researchState.etfRemote[symbol]=data;
      if(data.fund){researchState.etf ||= {payload:{funds:{}}};const old=researchState.etf.payload.funds[symbol];if(!old || data.fund.as_of>=old.as_of)researchState.etf.payload.funds[symbol]=data.fund;}
    });
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
}

async function restoreStartupMemory() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(STARTUP_PREFS_KEY)); } catch {}
  applyStartupPreferences(saved);
  const savedView = saved?.view === "research" && saved.research?.tab === "backtest" ? "backtest" : saved?.view;
  if (saved?.version === 1 && [...document.querySelectorAll(".view")].some(node => node.dataset.view === savedView)) setView(savedView, {refresh:false});
  const generation = typeof apiReadGeneration === "undefined" ? 0 : apiReadGeneration;
  const pending = startupRead();
  const bundle = await Promise.race([pending, new Promise(resolve => window.setTimeout(() => resolve(null), 500))]);
  await restoreStartupBundle(bundle, saved, generation);
  // Slow disks must not delay network refresh or silently lose the last usable display.
  if (!bundle) pending.then(value => restoreStartupBundle(value, null, generation)).catch(() => {});
  document.addEventListener("input", saveStartupPreferences);
  document.addEventListener("change", saveStartupPreferences);
  document.addEventListener("click", () => window.setTimeout(saveStartupPreferences, 0));
  window.addEventListener("pagehide", () => {saveStartupPreferences();flushStartupMemory();});
  document.addEventListener("visibilitychange", () => {if(document.visibilityState === "hidden"){saveStartupPreferences();flushStartupMemory();}});
}
