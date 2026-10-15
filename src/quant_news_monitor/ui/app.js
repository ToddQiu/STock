"use strict";

const ASSET_UI_POLL_MS = 1_000;
const ASSET_NAMES = { GOLD: "黄金", BTC: "BTC", OIL: "WTI 原油", US5Y: "美债（五年期）" };
const KOREA_STOCK_UI_POLL_MS = 1_000;
const PORTFOLIO_SORT_KEY_STORAGE = "quantify.portfolio.sortKey";
const PORTFOLIO_SORT_DIRECTION_STORAGE = "quantify.portfolio.sortDirection";
const PULSE_SORT_STORAGE_KEY = "quantify.overview.sentimentSort";
const PULSE_SORT_MODES = ["strength", "bullish", "bearish", "news", "watchlist"];
const PORTFOLIO_CHART_CONFIG = {
  intraday: { label: "1 分钟 K", defaultPeriod: "1d", periods: { "1d": "1 日", "5d": "5 日" } },
  daily: { label: "日 K", defaultPeriod: "3m", periods: { "1m": "1 月", "3m": "3 月", "6m": "6 月", "1y": "1 年" } },
  weekly: { label: "周 K", defaultPeriod: "1y", periods: { "6m": "6 月", "1y": "1 年" } },
};
const PORTFOLIO_CHART_INTERVAL_LABELS = { "1m": "1 分钟", "5m": "5 分钟", "1d": "日线", "1wk": "周线", trading_day: "日净值" };
const PORTFOLIO_OVERLAY_COLORS = { ma5: "#8b5b0b", ma20: "#27738d", vwap: "#7c5ca8" };

// Identity is market + code, never an editable display name (e.g. PALU named PANW).
const instrumentBriefs = new Map([
  ["US:AAPL", "主营：iPhone、Mac 等消费电子，以及软件与数字服务。", "https://www.apple.com/business/"],
  ["US:MU", "主营：DRAM 内存与 NAND 闪存等存储芯片及存储解决方案。", "https://www.micron.com/about/company/corporate-profile"],
  ["US:MP", "主营：稀土开采、分离加工与永磁材料制造。", "https://www.mpmaterials.com/"],
  ["US:HOOD", "主营：面向个人的股票、期权与加密资产交易及金融服务。", "https://robinhood.com/us/en/support/articles/investments-you-can-make-on-robinhood/"],
  ["US:SPCX", "主营：SpaceX 火箭与航天发射、Starlink 卫星通信及 AI 相关业务。", "https://www.sec.gov/Archives/edgar/data/1924868/000199937126012604/spcu-497k_061126.htm"],
  ["US:CEG", "主营：美国发电与能源供应，覆盖核电、天然气等电源。", "https://www.constellationenergy.com/about/business-overview.html"],
  ["US:BRK-B", "主营：保险与再保险、铁路、能源及多元制造、服务和投资业务。", "https://www.berkshirehathaway.com/subs/sublinks.html"],
  ["US:MS", "主营：投资银行、证券交易、财富管理与投资管理。", "https://www.morganstanley.com/about-us"],
  ["US:GOOG", "主营：Google 搜索与广告、YouTube、云计算及其他科技业务。", "https://abc.xyz/investor/"],
  ["US:GOOGL", "主营：Google 搜索与广告、YouTube、云计算及其他科技业务。", "https://abc.xyz/investor/"],
  ["US:PALU", "产品：每日 2 倍做多 Palo Alto Networks 的杠杆 ETF，并非 PANW 公司股票。", "https://www.direxion.com/product/daily-panw-bull-and-bear-leveraged-single-stock-etfs"],
  ["US:CONL", "产品：每日 2 倍做多 Coinbase 的杠杆 ETF，并非 COIN 股票或直接持币。", "https://graniteshares.com/etfs/conl/"],
  ["US:AVGX", "产品：每日 2 倍做多博通 AVGO 的杠杆 ETF，并非博通公司股票。", "https://www.defianceetfs.com/avgx/"],
  ["US:GDXU", "产品：黄金矿企指数每日 3 倍做多 ETN，非现货黄金；有发行人信用风险。", "https://www.bmoetns.com/ETN/GDXU.P/"],
  ["US:SCO", "产品：WTI 原油期货指数每日反向 2 倍 ETF，非现货油价的长期反向 2 倍。", "https://www.proshares.com/our-etfs/leveraged-and-inverse/sco"],
  ["US:BUG", "产品：投资网络安全软件及相关基础设施企业的行业 ETF。", "https://www.globalxetfs.com/funds/bug"],
  ["US:OKTA", "主营：企业员工与客户的身份验证、访问管理及身份安全服务。", "https://www.okta.com/"],
  ["US:PANW", "主营：企业网络安全、云安全与安全运营平台及相关服务。", "https://www.paloaltonetworks.com/"],
  ["US:FTNT", "主营：面向企业的网络安全产品与服务。", "https://www.fortinet.com/"],
  ["US:RBRK", "主营：企业数据保护、备份恢复及身份与数据安全服务。", "https://www.rubrik.com/"],
  ["US:SOXX", "产品：投资美国上市半导体及设备企业的行业 ETF。", "https://www.ishares.com/us/products/239705/ishares-phlx-semiconductor-etf"],
  ["US:IGV", "产品：北美软件及部分互动媒体、服务企业的行业 ETF，采用修正市值加权。", "https://www.ishares.com/us/products/239771/ishares-north-american-techsoftware-etf"],
  ["US:XSW", "产品：美国软件与服务行业 ETF，采用修正等权配置。", "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-software-services-etf-xsw"],
  ["US:WCLD", "产品：聚焦美国上市云软件与服务企业的 ETF，跟踪等权云计算指数。", "https://www.wisdomtree.com/us/products/megatrends/wcld"],
  ["US:SOXL", "产品：半导体指数每日 3 倍做多 ETF，长期收益不保证为指数的 3 倍。", "https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs"],
  ["US:RSP", "产品：标普 500 等权重 ETF，采用等权配置而非市值加权。", "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker=RSP"],
  ["US:MAGS", "产品：等权配置美国七大科技股的 ETF，非单一公司股票。", "https://www.roundhillinvestments.com/etf/mags/"],
  ["US:DRNZ", "产品：投资无人机及相关产业企业的主题 ETF。", "https://www.rexshares.com/drnz/"],
  ["KR:005930.KS", "主营：存储芯片、芯片代工，以及手机、电视与家电等电子产品。", "https://www.samsung.com/global/ir/ir-resources/our-business/"],
  ["KR:000660.KS", "主营：DRAM、HBM 与 NAND 闪存等存储芯片。", "https://research-user.skhynix.com/research-areas/future-memory-technologies/evolutionary-memory"],
  ["CN:601168.SS", "主营：铜、铅、锌等金属的采选、冶炼与相关资源开发。", "https://www.westmining.com/tzzgx/yjgg/202508/P020250826572372549202.pdf"],
  ["FUND:016452", "产品：跟踪纳斯达克 100 指数的 QDII 指数基金。", "https://www.nffund.com/main/nffund/personal-financing/detail.shtml?fundCode=016452"],
  ["FUND:040046", "产品：通过目标 ETF 跟踪纳斯达克 100 指数的 QDII 联接基金。", "https://www.huaan.com.cn/funds/040046/index.shtml"],
  ["FUND:014978", "产品：通过目标 ETF 跟踪纳斯达克 100 指数的 QDII 联接基金（C 类）。", "https://www.huaan.com.cn/upload2010/2023/07/21/021707353_0_40b8433e-64fd-3886-8a04-49eeb95cf44b.pdf"],
  ["FUND:016470", "产品：跟踪纳斯达克生物科技指数的 QDII 指数基金（人民币 C 类）。", "https://www.gffunds.com.cn/funds/?fromSearch=1&fundcode=016470"],
  ["FUND:013328", "产品：全球价值投资方向的 QDII 股票基金。", "https://www.jsfund.cn/main/a/20211025/482884.shtml"],
  ["FUND:000043", "产品：美国成长股票方向的 QDII 基金。", "https://www.jsfund.cn/Services/cn/jsp/product/DetailList.jsp?type=7"],
  ["FUND:018147", "产品：新兴市场方向的 QDII 混合基金（C 类，按产品名称归类）。"],
  ["FUND:539002", "产品：新兴市场方向的 QDII 混合基金（A 类，按产品名称归类）。"],
  ["FUND:027170", "产品：环球精选价值方向的 QDII 混合基金（C 类，按产品名称归类）。"],
  ["FUND:028222", "产品：恒生生物科技 ETF 联接基金（C 类，按产品名称归类）。"],
  ["FUND:012752", "产品：纳斯达克 100 指数 QDII 基金（人民币 C 类，按产品名称归类）。"],
  ["FUND:539001", "产品：纳斯达克 100 指数 QDII 基金（人民币 A 类，按产品名称归类）。"],
  ["FUND:024974", "产品：科创板半导体材料设备主题 ETF 联接基金（A 类，按产品名称归类）。"],
  ["ASSET:GOLD", "资产：黄金；本页报价以 XAU/USD 现货为主，不是黄金矿企股票。", "https://www.gold.org/about-gold"],
  ["ASSET:BTC", "资产：比特币，去中心化数字资产；不是公司股票。", "https://bitcoin.org/zh_CN/"],
  ["ASSET:OIL", "资产：WTI 原油期货，反映特定合约价格；不是原油公司股票。", "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html"],
  ["ASSET:US5Y", "资产：美国五年期国债收益率；收益率上升不等于债券价格上涨。", "https://home.treasury.gov/resource-center-data-chart-center/interest-rates"],
].map(([key, text, source]) => [key, { text, source, reviewedAt: source ? "2026-10-06" : "", basis: source ? "官网业务 / 产品资料" : "当前产品名称分类，投资范围须核对基金合同" }]));

let companyBriefSignature = "";
const instrumentEarningsState = {data:null, items:new Map(), signature:"", pending:false, readError:null};
const earningsDayFormatter = new Intl.DateTimeFormat("en-US", {timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"});

function earningsDateValue(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === value ? date : null;
}

function earningsToday() {
  const parts = earningsDayFormatter.formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type,part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function instrumentEarningsInfo(item, today = null) {
  const key = instrumentBriefKey(item);
  const target = key.startsWith("COMPANY:") ? instrumentBriefs.get(key)?.earningsKey : key;
  if (!target?.startsWith("US:")) return null;
  const event = instrumentEarningsState.items.get(target);
  if (!event) return null;
  const date = earningsDateValue(event.earnings_date), day = earningsDateValue(today || earningsToday());
  const runtime = instrumentEarningsState.data?.runtime || {};
  const age = Date.now() - Date.parse(runtime.collected_at);
  const stale = Boolean(instrumentEarningsState.data?._startup_cache || runtime.restored_from_cache || runtime.stale || runtime.error || instrumentEarningsState.readError)
    || !Number.isFinite(age) || age < -60_000 || age >= Number(runtime.interval_seconds || 21600) * 1000;
  const dates = [...new Set((event.sources || []).map(source => source.date).filter(earningsDateValue))];
  const conflict = event.date_status === "conflict" || dates.length > 1;
  const until = date && day ? Math.round((date-day)/86400_000) : null;
  const calendarDate = date ? `${event.earnings_date.replaceAll("-", "/")}（周${"日一二三四五六"[date.getUTCDay()]}）` : "";
  const session = {before_open:"盘前",after_close:"盘后",unknown:"时段未公布"}[event.session] || "时段未公布";
  let text = !date ? "财报日期待公布" : conflict ? `财报日期有分歧 · ${calendarDate} 等（美东）`
    : until === null || until < 0 ? `上次财报预告 ${calendarDate} · 待更新（美东）`
      : `预计财报 ${calendarDate} · ${until === 0 ? "今天" : `${until}天后`} · ${session}（美东）`;
  if (stale) text += instrumentEarningsState.readError ? " · 同步失败，待确认" : " · 旧目录待确认";
  const sources = (event.sources || []).map(source => `${source.name || "来源"}：${source.date || "日期未取得"}`).join("；");
  return {text, target, until:conflict ? null : until,
    status:conflict ? "conflict" : stale || !date || until < 0 ? "stale" : until <= 7 ? "soon" : "scheduled",
    title:["日期按美东日历，倒计时为自然日；公开来源预计，不等于公司确认。",sources,
      runtime.collected_at && `目录采集 ${formatDate(runtime.collected_at)}`,instrumentEarningsState.readError].filter(Boolean).join("\n")};
}

function updateInstrumentEarnings(node, item) {
  const info = instrumentEarningsInfo(item === undefined ? node.getAttribute("data-instrument-brief") : item);
  if (!info) { node.earningsNote?.remove(); node.earningsNote=null; return; }
  if (!node.earningsNote) {
    node.earningsNote = el("span", "instrument-earnings");
    node.append(node.earningsNote);
  }
  const note = node.earningsNote;
  note.textContent = info.text;
  note.title = info.title;
  note.setAttribute("data-instrument-earnings", info.target);
  note.setAttribute("data-status", info.status);
}

function repaintInstrumentEarnings() {
  document.querySelectorAll("[data-instrument-brief]").forEach(node => updateInstrumentEarnings(node));
}

function registerInstrumentEarnings(data) {
  if (!Array.isArray(data.items)) throw new Error("财报日期目录格式无效");
  const items = new Map();
  data.items.forEach(item => {
    if (item?.market !== "US" || typeof item.symbol !== "string") return;
    items.set(instrumentBriefKey(item), {...item, sources:Array.isArray(item.sources) ? item.sources.filter(source => source && typeof source === "object") : []});
  });
  const recovered = Boolean(instrumentEarningsState.readError);
  const signature = JSON.stringify([data,earningsToday()]);
  instrumentEarningsState.data = data;
  instrumentEarningsState.items = items;
  instrumentEarningsState.readError = null;
  if (signature === instrumentEarningsState.signature && !recovered) return;
  instrumentEarningsState.signature = signature;
  repaintInstrumentEarnings();
}

async function refreshInstrumentEarnings() {
  if (instrumentEarningsState.pending) return;
  instrumentEarningsState.pending = true;
  try { registerInstrumentEarnings(await api("/api/research/earnings/annotations")); }
  catch (error) {instrumentEarningsState.readError=error.message;repaintInstrumentEarnings();}
  finally {instrumentEarningsState.pending=false;}
}

function instrumentBriefKey(item = {}) {
  if (!item) return "";
  if (typeof item === "string") item = { target: item };
  let market = typeof item.market === "string" ? item.market.toUpperCase() : "";
  let symbol = String(item.symbol || item.code || "").trim().toUpperCase();
  const target = item.target || (!symbol && /^(US|CN|HK|KR|JP|GB|CA|FUND|ASSET|COMPANY):/i.test(item.key || "") ? item.key : "");
  if (target) {
    const match = /^(US|CN|HK|KR|JP|GB|CA|FUND|ASSET|COMPANY):(.+)$/i.exec(String(target).trim());
    if (match) { market = match[1].toUpperCase(); symbol = match[2].trim().toUpperCase(); }
  }
  if (!market && Object.hasOwn(ASSET_NAMES, item.asset || item.key || "")) {
    market = "ASSET"; symbol = item.asset || item.key;
  }
  if (market === "COMPANY") return `COMPANY:${symbol.toLowerCase()}`;
  if (market === "US" && /^BRK[.-][AB]$/.test(symbol)) symbol = symbol.replace(".", "-");
  if (market === "CN") {
    symbol = symbol.replace(/\.SH$/, ".SS");
    if (/^\d{6}$/.test(symbol)) symbol += symbol.startsWith("6") ? ".SS" : symbol.startsWith("0") || symbol.startsWith("3") ? ".SZ" : ".BJ";
  }
  if (market === "KR" && /^\d{6}$/.test(symbol)) symbol += ".KS";
  if (market === "HK" && /^\d{1,5}(?:\.HK)?$/.test(symbol)) symbol = `${String(Number(symbol.replace(/\.HK$/, ""))).padStart(4, "0")}.HK`;
  return market && symbol ? `${market}:${symbol}` : "";
}

function instrumentBrief(item) {
  const key = instrumentBriefKey(item);
  return instrumentBriefs.get(key) || {
    text: key.startsWith("FUND:") ? "产品：公募基金，具体投资范围资料待核实。" : key.startsWith("ASSET:") ? "资产简介资料待核实。" : "主营业务资料待核实。",
    source: "", reviewedAt: "", basis: "尚无核实资料，不根据新闻、名称或情绪猜测业务",
  };
}

function updateInstrumentBrief(node, item) {
  const brief = instrumentBrief(item);
  node.textContent = brief.text;
  node.earningsNote = null;
  node.setAttribute("data-instrument-brief", instrumentBriefKey(item));
  node.hidden = !instrumentBriefKey(item);
  node.title = [brief.basis, brief.reviewedAt && `资料核验 ${brief.reviewedAt}`, brief.source].filter(Boolean).join(" · ");
  updateInstrumentEarnings(node, item);
}

function instrumentBriefNode(item) {
  const node = el("small", "instrument-brief");
  updateInstrumentBrief(node, item);
  return node;
}

function briefAfterTitle(id, item) {
  const title = document.getElementById(id);
  if (!title) return;
  const briefId = `${id}-brief`;
  let node = document.getElementById(briefId);
  if (!node) { node = instrumentBriefNode(item); node.id = briefId; title.after(node); }
  else updateInstrumentBrief(node, item);
}

function registerCompanyBriefs(data) {
  const signature = JSON.stringify([data.reviewed_at, (data.companies || []).map(company => [company.id, company.entity, company.ticker, company.business, company.sources])]);
  if (signature === companyBriefSignature) return;
  companyBriefSignature = signature;
  (data.companies || []).forEach(company => {
    if (!company.business || !company.id) return;
    const entry = { text: `${company.entity === "listed" ? "主营" : "业务"}：${company.business.split("。")[0]}。`, source: company.sources?.[0]?.url || "", reviewedAt: data.reviewed_at || "", basis: "研究库官网业务陈述（非研究推断）" };
    if (company.entity === "listed" && company.ticker) entry.earningsKey = instrumentBriefKey({market:"US",symbol:company.ticker});
    instrumentBriefs.set(`COMPANY:${company.id}`, entry);
    if (company.ticker) instrumentBriefs.set(instrumentBriefKey({market:"US",symbol:company.ticker}), entry);
  });
  document.querySelectorAll("[data-instrument-brief]").forEach(node => updateInstrumentBrief(node, node.getAttribute("data-instrument-brief")));
}

async function loadCompanyBriefs() {
  try { registerCompanyBriefs(await api("/api/research/companies")); }
  catch (_) { /* An unavailable research catalog must not block prices, news or holdings. */ }
}

function positionFormBrief() {
  const form = document.getElementById("position-form");
  briefAfterTitle("position-name", {market:form.elements.market.value,symbol:form.elements.symbol.value});
}

const state = {
  dashboard: null,
  liveNews: null,
  newsPending: false,
  newsSignature: null,
  newsLanguage: "all",
  newsSort: "latest",
  assets: null,
  currentView: "overview",
  sentimentFilter: "all",
  pulseSort: "strength",
  movementMarket: "all",
  movementSortDirection: "desc",
  analysisMode: "auto",
  scanPoller: null,
  movementPoller: null,
  assetPoller: null,
  trumpPoller: null,
  assetAlertsEnabled: true,
  koreaStockPoller: null,
  portfolioPoller: null,
  portfolioDeletePending: false,
  portfolioPrincipalDirty: false,
  portfolioPrincipalSaving: false,
  portfolioSortKey: "default",
  portfolioSortDirection: "desc",
  portfolioTypeFilter: "all",
  portfolioSearch: "",
  portfolioChartItem: null,
  portfolioChartMode: "daily",
  portfolioChartPeriod: "3m",
  portfolioChartData: null,
  portfolioChartLoadedKey: null,
  portfolioChartRetryKey: null,
  portfolioChartRetryAt: 0,
  portfolioChartRequestId: 0,
  portfolioChartHoverIndex: null,
  portfolioChartWindowStart: null,
  portfolioChartResizeFrame: null,
  portfolioChartPoller: null,
  portfolioChartLoading: false,
  fundSearchTimer: null,
};

const KOREA_STOCK_DEFINITIONS = [
  {
    id: "samsung",
    market: "KR",
    symbol: "005930.KS",
    shortName: "三星电子",
    heading: "三星电子（005930）",
    alias: "SAMSUNG",
    label: "SAMSUNG",
  },
  {
    id: "hynix",
    market: "KR",
    symbol: "000660.KS",
    shortName: "SK 海力士",
    heading: "SK 海力士（000660）",
    alias: "SK HYNIX",
    label: "HYNIX",
  },
];

const eventLabels = {
  earnings: "财报",
  guidance: "业绩指引",
  regulation: "监管",
  order: "订单",
  capital_action: "资本动作",
  capacity: "扩产",
  legal: "法律",
  management: "管理层",
  m_and_a: "并购",
  analyst: "机构评级",
  product: "产品研发",
  unconfirmed: "待确认",
  other: "其他",
};

const movementTypeLabels = {
  extended_price: "盘前盘后价格异动",
  price_volume: "价量异动",
  price: "价格异动",
  volume: "放量异动",
};

const assetEventLabels = {
  macro: "宏观利率",
  fund_flows: "资金流向",
  tax_policy: "税费政策",
  regulation: "监管政策",
  market_structure: "市场结构",
  geopolitics: "地缘风险",
  supply_demand: "供需变化",
  market_news: "市场消息",
};

const marketLabels = { CN: "A股", HK: "港股", KR: "韩国", US: "美股", FUND: "中国基金" };
const pendingGetRequests = new Map();
let apiReadGeneration = 0;

async function api(path, options = {}) {
  const isRead = String(options.method || "GET").toUpperCase() === "GET";
  if (!isRead && typeof startupMemory !== "undefined" && !startupMemory.ready) throw new Error("正在确认当前配置，请等待总览同步后再修改");
  if (isRead && pendingGetRequests.has(path)) return pendingGetRequests.get(path);
  const generation = apiReadGeneration;
  const operation = (async () => {
    const request = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
    if (request.body && typeof request.body !== "string") request.body = JSON.stringify(request.body);
    const controller = isRead ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), 45_000) : null;
    if (controller) request.signal = controller.signal;
    try {
      const response = await fetch(path, request);
      let payload;
      try { payload = await response.json(); } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new Error(`服务返回了无效数据 (${response.status})，请稍后重试`);
      }
      if (!response.ok) {
        const error = new Error(payload.error || `请求失败 (${response.status})`);
        error.status = response.status;
        error.retryAfterSeconds = Number(payload.retry_after_seconds) || 0;
        throw error;
      }
      if (isRead && generation !== apiReadGeneration) throw new Error("数据已变更，等待下一次刷新");
      if (!isRead) {
        apiReadGeneration += 1;
        pendingGetRequests.clear();
        if (typeof invalidateStartupMemory === "function") invalidateStartupMemory();
      }
      if (isRead && typeof rememberStartupResponse === "function") rememberStartupResponse(path, payload);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("读取数据超时，稍后将重新尝试");
      throw error;
    } finally {
      if (timer !== null) window.clearTimeout(timer);
    }
  })();
  if (isRead) pendingGetRequests.set(path, operation);
  try { return await operation; } finally {
    if (pendingGetRequests.get(path) === operation) pendingGetRequests.delete(path);
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function uiIcon(name) {
  const namespace = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(namespace, "svg");
  icon.setAttribute("class", "ui-icon");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(namespace, "use");
  use.setAttribute("href", `/icons.svg#${name}`);
  icon.append(use);
  return icon;
}

function scoreClass(score) {
  if (score > 0.04) return "positive";
  if (score < -0.04) return "negative";
  return "neutral";
}

function scoreLabel(score) {
  if (score > 0.04) return "利多";
  if (score < -0.04) return "利空";
  return "中性";
}

function formatScore(score) {
  const value = Number(score || 0);
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatDate(value, compact = false) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  const options = compact
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function formatPrice(value) {
  const number = Number(value || 0);
  const digits = Math.abs(number) < 1 ? 4 : 2;
  return number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function formatAssetPrice(item) {
  const number = Number(item.price || 0);
  const digits = item.key === "US5Y" ? 3 : 2;
  return number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signedPercent(value, digits = 2) {
  if (value === null || value === undefined) return "--";
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function intervalLabel(value, fallback = 60) {
  const seconds = Math.max(1, Math.round(Number(value || fallback)));
  return seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function normalizeStockSymbol(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/\.KS$/i, "")
    .replace(/\.KQ$/i, "")
    .trim();
}

function koreaStockDefinitionBySymbol(symbol) {
  const normalized = normalizeStockSymbol(symbol);
  return KOREA_STOCK_DEFINITIONS.find((item) => normalizeStockSymbol(item.symbol) === normalized);
}

function confidenceLabel(value) {
  const confidence = Number(value || 0);
  if (confidence >= 0.7) return "高置信";
  if (confidence >= 0.45) return "中置信";
  return "低置信";
}

function confidenceClass(value) {
  const confidence = Number(value || 0);
  if (confidence >= 0.7) return "high";
  if (confidence >= 0.45) return "medium";
  return "low";
}

function trumpDirectionLabel(factor) {
  const score = Number(factor?.score || 0);
  return score > 0.08 ? "偏多" : score < -0.08 ? "偏空" : "中性";
}

function trumpBadge(factor) {
  const score = Number(factor?.score || 0);
  return el("span", `trump-factor-badge ${scoreClass(score)}`, `X ${formatScore(score)}`);
}

function emptyState(title, copy = "等待新的数据进入监测器") {
  const box = el("div", "empty-state");
  const inner = el("div");
  inner.append(el("strong", "", title), el("span", "", copy));
  box.append(inner);
  return box;
}

function marketBadge(item) {
  return el("span", "market-badge", `${marketLabels[item.market] || item.market} · ${item.symbol}`);
}

function newsTitle(item) {
  return item.title || item.title_zh || "暂无标题";
}

function translationLabel(item, field = "title") {
  const original = String(item[field] || "").trim();
  if (!original || !/[A-Za-z]{3,}/.test(original)) return "";
  const translated = String(item[`${field}_zh`] || "").trim();
  if (translated && translated !== original && /[\u3400-\u9fff]/.test(translated)) return `中文译文（机器翻译）：${translated}`;
  const chinese = (original.match(/[\u3400-\u9fff]/g) || []).length;
  if (chinese && (original.match(/[A-Za-z]/g) || []).length <= chinese * 3) return "";
  const status = item[`${field}_translation_status`];
  return status === "disabled" ? "中文翻译未开启" : status === "too_long" ? "内容过长，暂未生成中文译文"
    : status === "unavailable" ? "中文译文暂不可用，保留原文并自动重试" : "中文译文生成中";
}

function appendNewsTranslation(node, item, field = "title") {
  const text = translationLabel(item, field);
  if (text) node.append(el("span", "news-translation", text));
  return node;
}

function newsDisplayKey(item) {
  return `${item.market || ""}:${item.symbol || ""}:${item.fingerprint || item.url || item.title || ""}`;
}

function renderNewsDetailText(item) {
  const dialog = document.getElementById("detail-dialog");
  dialog.newsItem = item;
  document.getElementById("detail-title").textContent = newsTitle(item);
  const translated = document.getElementById("detail-original");
  translated.textContent = translationLabel(item);
  translated.hidden = !translated.textContent;
  const summary = document.getElementById("detail-news-summary");
  const field = item.news_summary ? "news_summary" : "summary";
  summary.textContent = item[field] ? `原文摘要：${item[field]}` : "";
  summary.hidden = !item[field];
  if (item[field]) appendNewsTranslation(summary, item, field);
}

function renderNews(container, items, { expanded = false, limit = null, emptyTitle = null, emptyCopy = undefined } = {}) {
  const dialog = document.getElementById("detail-dialog");
  if (dialog?.open && dialog.newsItem) {
    const fresh = items.find(item => newsDisplayKey(item) === newsDisplayKey(dialog.newsItem));
    if (fresh) renderNewsDetailText(fresh);
  }
  const visible = limit ? items.slice(0, limit) : items;
  const signature = JSON.stringify([expanded, state.newsLanguage, visible, emptyTitle, emptyCopy]);
  if (container.newsRenderSignature === signature) return;
  container.newsRenderSignature = signature;
  const focusedKey = container.contains?.(document.activeElement)
    ? document.activeElement.getAttribute("data-news-key") : null;
  container.replaceChildren();
  if (!visible.length) {
    container.append(emptyState(emptyTitle || (state.newsLanguage === "zh" ? "暂无已分析的中文新闻" : "暂无已分析新闻"), emptyCopy));
    return;
  }
  const fragment = document.createDocumentFragment();
  visible.forEach((item) => {
    const row = el("article", "news-row");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `查看 ${item.symbol} 新闻详情`);
    row.setAttribute("data-news-key", newsDisplayKey(item));

    const main = el("div", "news-main");
    const top = el("div", "news-topline");
    top.append(marketBadge(item), el("span", "news-source", item.source || "未知来源"));
    {
      const published = el("time", "news-inline-time", formatDate(item.published_at, true));
      published.dateTime = item.published_at || "";
      published.title = `发布时间：${formatDate(item.published_at)}`;
      top.append(published);
    }
    if (Number(item.trump_factor?.post_count || 0) > 0) {
      const badge = trumpBadge(item.trump_factor);
      badge.title = item.trump_factor.reason || "Trump 动态因子";
      top.append(badge);
    }
    if (item.evidence_status === "insufficient") top.append(el("small", "news-source", "方向证据不足"));
    if (item.evidence_status === "mixed") top.append(el("small", "news-source", "正反证据并存"));
    if (item.evidence_status === "legacy") top.append(el("small", "news-source", "旧规则记录"));
    const headline = el("h3", "news-title", newsTitle(item));
    appendNewsTranslation(headline, item);
    headline.title = item.title || "";
    main.append(top, instrumentBriefNode(item), headline);
    if (item.impact_reason) {
      const impact = el("div", `news-impact-explainer ${scoreClass(item.score)}`);
      impact.title = item.analysis_summary || item.impact_reason;
      impact.append(
        el("span", "news-impact-label", item.impact_label || "影响判断"),
        el("strong", "news-impact-area", item.impact_area || "待确认"),
        el("p", "news-impact-reason", item.impact_reason),
      );
      main.append(impact);
    } else if (item.analysis_summary) {
      main.append(el("p", "news-analysis", item.analysis_summary));
    }

    const event = el("div", "news-event");
    event.append(el("strong", "", eventLabels[item.event_type] || "其他"), el("span", "", item.horizon || "--"));

    const score = el("div", "score-cell");
    const cls = scoreClass(item.score);
    score.append(el("strong", `score-value ${cls}`, formatScore(item.score)));
    const track = el("div", "score-track");
    const fill = el("span", cls);
    fill.style.width = `${Math.max(3, Math.min(100, Math.abs(Number(item.score || 0)) * 100))}%`;
    track.append(fill);
    score.append(track);

    row.append(main, event, score);
    if (expanded) row.append(el("time", "news-time", formatDate(item.published_at, true)));
    row.addEventListener("click", () => openDetail(item));
    row.addEventListener("keydown", (eventKey) => {
      if (eventKey.key === "Enter" || eventKey.key === " ") {
        eventKey.preventDefault();
        openDetail(item);
      }
    });
    fragment.append(row);
  });
  container.append(fragment);
  if (focusedKey) {
    const replacement = Array.from(container.querySelectorAll("[data-news-key]"))
      .find(node => node.getAttribute("data-news-key") === focusedKey);
    replacement?.focus({ preventScroll: true });
  }
}

function renderAlerts(container, alerts, { table = false, limit = null } = {}) {
  container.replaceChildren();
  const visible = limit ? alerts.slice(0, limit) : alerts;
  if (!visible.length) {
    container.append(emptyState("暂无重点告警", "达到阈值的事件会出现在这里"));
    return;
  }
  const fragment = document.createDocumentFragment();
  visible.forEach((item) => {
    if (table) {
      const row = el("article", "alert-table-row");
      const signal = el("div");
      signal.append(el("span", `polarity-badge ${item.polarity}`, scoreLabel(item.score)), el("strong", "alert-score", formatScore(item.score)));
      const copy = el("div");
      copy.append(appendNewsTranslation(el("h3", "", `${item.market}:${item.symbol} · ${newsTitle(item)}`), item), instrumentBriefNode(item), el("p", "", item.analysis_summary || ""));
      row.append(signal, copy, el("span", "event-badge", eventLabels[item.event_type] || "其他"), el("time", "news-time", formatDate(item.alert_created_at, true)));
      row.addEventListener("click", () => openDetail(item));
      fragment.append(row);
    } else {
      const card = el("article", "alert-item");
      const top = el("div", "alert-item-top");
      top.append(el("span", `polarity-badge ${item.polarity}`, `${scoreLabel(item.score)} · ${item.symbol}`), el("span", "alert-score", formatScore(item.score)));
      card.append(top, instrumentBriefNode(item), appendNewsTranslation(el("h3", "", newsTitle(item)), item), el("small", "", `${eventLabels[item.event_type] || "其他"} · ${item.horizon || "--"}`));
      card.addEventListener("click", () => openDetail(item));
      fragment.append(card);
    }
  });
  container.append(fragment);
}

function sortedPulseRows(data, mode = state.pulseSort) {
  const values = new Map((data.sentiment_by_symbol || []).map((item) => [`${item.market}:${item.symbol}`, item]));
  const rows = (data.watchlist || []).map((watch) => ({
      watch,
      item: values.get(`${watch.market}:${watch.symbol}`) || {
        average_score: 0,
        news_count: 0,
        directional_count: 0,
        bullish_count: 0,
        bearish_count: 0,
      },
    }));
  if (mode === "watchlist") return rows;
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const score = item => item.average_score != null && Number.isFinite(Number(item.average_score)) &&
    (number(item.news_count) > 0 || number(item.trump_factor?.post_count) > 0 || number(item.average_score) !== 0)
    ? Number(item.average_score) : null;
  return rows.sort((left, right) => {
    const a = score(left.item), b = score(right.item);
    const newsDelta = number(right.item.news_count) - number(left.item.news_count);
    if (mode === "news" && newsDelta) return newsDelta;
    // Missing observations must not outrank an observed neutral or negative score.
    if (a == null || b == null) return (a == null) - (b == null) || newsDelta;
    const scoreDelta = mode === "bullish" ? b - a : mode === "bearish" ? a - b : Math.abs(b) - Math.abs(a);
    return scoreDelta || number(right.item.directional_count) - number(left.item.directional_count) || newsDelta;
  });
}

function bindPulseSortControl() {
  const control = document.getElementById("sentiment-sort");
  try {
    const saved = window.localStorage.getItem(PULSE_SORT_STORAGE_KEY);
    if (PULSE_SORT_MODES.includes(saved)) state.pulseSort = saved;
  } catch (_) { /* Keep session defaults when storage is unavailable. */ }
  control.value = state.pulseSort;
  control.addEventListener("change", () => {
    if (!PULSE_SORT_MODES.includes(control.value)) return;
    state.pulseSort = control.value;
    try { window.localStorage.setItem(PULSE_SORT_STORAGE_KEY, state.pulseSort); } catch (_) { /* Session-only preference. */ }
    if (state.dashboard) renderPulse(state.dashboard);
  });
}

function renderPulse(data) {
  const container = document.getElementById("sentiment-pulse");
  container.replaceChildren();
  const ranked = sortedPulseRows(data).slice(0, 12);
  if (!ranked.length) {
    container.append(emptyState("股票池为空"));
    return;
  }
  ranked.forEach(({ watch, item }) => {
    const score = Number(item.average_score || 0);
    const row = el("div", "sentiment-row");
    const identity = el("div", "sentiment-identity");
    identity.append(
      el("strong", "sentiment-symbol", watch.symbol),
      el("small", "", `${Number(item.directional_count || 0)}/${Number(item.news_count || 0)} 条有方向`),
    );
    row.append(identity);
    const track = el("div", "sentiment-track");
    const fill = el("span", `sentiment-fill ${scoreClass(score)}`);
    fill.style.width = `${Math.max(score === 0 ? 0 : 2, Math.min(50, Math.abs(score) * 50))}%`;
    track.append(fill);
    row.append(track, el("span", `sentiment-number ${scoreClass(score)}`, formatScore(score)));
    row.append(instrumentBriefNode(watch));
    const latest = item.latest_at ? ` · 更新 ${formatDate(item.latest_at, true)}` : "";
    row.title = `加权新闻 ${formatScore(item.base_average_score ?? item.average_score)} · 利多 ${Number(item.bullish_count || 0)} / 利空 ${Number(item.bearish_count || 0)}${latest}`;
    if (item.trump_factor) row.title += ` · Trump ${formatScore(item.trump_factor.score)} · 合成 ${formatScore(score)}`;
    container.append(row);
  });
}

function renderWatchlist(data) {
  const container = document.getElementById("watchlist-table");
  container.replaceChildren();
  (data.watchlist || []).forEach((item) => {
    const row = el("div", "watch-row");
    const code = el("div", "watch-code");
    code.append(el("span", "market-badge", marketLabels[item.market] || item.market), el("span", "", item.symbol));
    const premium = renderAdrPremium(item.adr_premium);
    const factor = item.trump_factor || {};
    const trump = el("div", "watch-trump");
    trump.append(
      el("strong", scoreClass(factor.score), formatScore(factor.score)),
      el("span", "", factor.theme_labels?.join("、") || "暂无相关动态"),
    );
    trump.title = factor.reason || "暂无相关动态";
    const remove = el("button", "icon-button danger-button", "×");
    remove.type = "button";
    remove.title = `移除 ${item.symbol}`;
    remove.addEventListener("click", () => removeWatchItem(item));
    const name = el("div", "watch-name");
    name.append(el("strong", "", item.name), instrumentBriefNode(item));
    row.append(code, name, premium, trump, el("div", "watch-query", item.query), remove);
    container.append(row);
  });
}

function formatPortfolioMoney(value, currency = "CNY", compact = false) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const symbols = { CNY: "¥", HKD: "HK$", USD: "$", KRW: "₩" };
  const prefix = symbols[currency] || `${currency} `;
  const absolute = Math.abs(number);
  if (compact && absolute >= 100_000_000) return `${number < 0 ? "-" : ""}${prefix}${(absolute / 100_000_000).toFixed(2)}亿`;
  if (compact && absolute >= 10_000) return `${number < 0 ? "-" : ""}${prefix}${(absolute / 10_000).toFixed(2)}万`;
  const digits = currency === "KRW" ? 0 : 2;
  return `${number < 0 ? "-" : ""}${prefix}${absolute.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function formatPositionQuantity(value) {
  return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
}

function formatPortfolioUnitPrice(value, currency, market) {
  if (value === null || value === undefined || value === "") return "--";
  if (market !== "FUND") return formatPortfolioMoney(value, currency);
  const number = Number(value);
  return Number.isFinite(number) ? `¥${number.toFixed(4)}` : "--";
}

function sortedPortfolioPositions(positions) {
  if (state.portfolioSortKey === "default") return [...positions];
  const fields = {
    market_value: "market_value_cny",
    daily_pnl: "daily_pnl_cny",
    total_pnl: "pnl_cny",
  };
  const field = fields[state.portfolioSortKey];
  if (!field) return [...positions];
  const direction = state.portfolioSortDirection === "asc" ? 1 : -1;
  return [...positions].sort((left, right) => {
    const leftValue = Number(left[field]);
    const rightValue = Number(right[field]);
    const leftMissing = left[field] === null || left[field] === undefined || !Number.isFinite(leftValue);
    const rightMissing = right[field] === null || right[field] === undefined || !Number.isFinite(rightValue);
    if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
    if (leftValue !== rightValue) return (leftValue - rightValue) * direction;
    return String(left.name || left.symbol).localeCompare(String(right.name || right.symbol), "zh-CN");
  });
}

function syncPortfolioSortControls() {
  const select = document.getElementById("portfolio-sort-key");
  const direction = document.getElementById("portfolio-sort-direction");
  select.value = state.portfolioSortKey;
  direction.replaceChildren(uiIcon(state.portfolioSortDirection === "asc" ? "arrow-up" : "arrow-down"));
  direction.disabled = state.portfolioSortKey === "default";
  direction.title = state.portfolioSortDirection === "asc" ? "当前升序，点击改为降序" : "当前降序，点击改为升序";
}

function portfolioStat(className, label, primary, secondary, tone = "") {
  const node = el("div", `portfolio-stat ${className}`);
  node.append(
    el("span", "portfolio-cell-label", label),
    el("strong", tone, primary),
    el("small", tone, secondary),
  );
  return node;
}

function setPortfolioMetric(id, value, tone = "neutral") {
  const node = document.getElementById(id);
  node.textContent = value;
  node.classList.remove("positive", "negative");
  node.parentElement.classList.remove("positive", "negative");
  if (tone !== "neutral") node.classList.add(tone);
  if (tone !== "neutral") node.parentElement.classList.add(tone);
}

function renderPortfolio(data) {
  const portfolio = data.portfolio || {};
  const summary = portfolio.summary || {};
  const runtime = portfolio.runtime || {};
  const positions = portfolio.positions || [];
  const principalInput = document.getElementById("portfolio-principal-input");
  if (!state.portfolioPrincipalDirty && !state.portfolioPrincipalSaving && document.activeElement !== principalInput) {
    principalInput.value = summary.principal_cny == null ? "" : Number(summary.principal_cny).toFixed(2);
  }
  const principalReturn = summary.principal_return_pct;
  const loss = principalReturn != null && principalReturn < 0;
  document.getElementById("portfolio-capital-rate-label").textContent = loss ? "持仓相对本金亏损率" : principalReturn > 0 ? "持仓相对本金收益率" : "持仓相对本金盈亏率";
  setPortfolioMetric("portfolio-capital-rate", principalReturn == null ? "--" : `${Math.abs(principalReturn).toFixed(2)}%`, loss ? "negative" : principalReturn > 0 ? "positive" : "neutral");
  document.getElementById("portfolio-capital-status").textContent = ({
    unset: "本金为零或尚未设置", empty: "暂无持仓，不计算亏损率", incomplete: "缺少行情或汇率，暂不计算",
    unavailable: "行情过期或异常，暂不计算", available: "按有效持仓估值计算",
  })[summary.principal_status] || "尚未设置本金";
  document.getElementById("portfolio-clear-button").disabled = !positions.length || state.portfolioDeletePending;
  document.getElementById("add-position-button").disabled = state.portfolioDeletePending;
  const query = state.portfolioSearch.trim().toLocaleLowerCase();
  const filteredPositions = positions.filter((item) => {
    const typeMatches = state.portfolioTypeFilter === "all"
      || (state.portfolioTypeFilter === "fund" && item.market === "FUND")
      || (state.portfolioTypeFilter === "stock" && item.market !== "FUND");
    const haystack = `${item.name || ""} ${item.symbol || ""} ${item.fund_type || ""}`.toLocaleLowerCase();
    return typeMatches && (!query || haystack.includes(query));
  });
  const displayPositions = sortedPortfolioPositions(filteredPositions);
  syncPortfolioSortControls();
  setPortfolioMetric("portfolio-market-value", formatPortfolioMoney(summary.total_market_value_cny, "CNY", true));
  setPortfolioMetric("portfolio-cost-value", formatPortfolioMoney(Number(summary.total_cost_cny || 0), "CNY", true));
  const pnl = summary.total_pnl_cny;
  const dailyPnl = summary.daily_pnl_cny;
  setPortfolioMetric("portfolio-total-pnl", formatPortfolioMoney(pnl, "CNY", true), pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral");
  setPortfolioMetric("portfolio-daily-pnl", formatPortfolioMoney(dailyPnl, "CNY", true), dailyPnl > 0 ? "positive" : dailyPnl < 0 ? "negative" : "neutral");
  document.getElementById("portfolio-daily-coverage").textContent = `可计 ${summary.daily_pnl_count ?? 0}/${summary.position_count || 0}${summary.daily_pnl_complete === false ? " · 部分汇总" : ""}`;
  const pnlPct = summary.total_pnl_pct;
  const pnlPctNode = document.getElementById("portfolio-total-pnl-pct");
  pnlPctNode.textContent = signedPercent(pnlPct);
  pnlPctNode.className = pnlPct > 0 ? "positive" : pnlPct < 0 ? "negative" : "";
  document.getElementById("portfolio-position-count").textContent = Number(summary.position_count || 0);
  document.getElementById("portfolio-priced-count").textContent = `已估值 ${summary.valued_count ?? summary.priced_count ?? 0}/${summary.position_count || 0}${summary.valuation_complete === false ? " · 部分汇总" : ""}${summary.stale_valued_count ? ` · ${summary.stale_valued_count} 项旧报价` : ""}`;
  document.getElementById("portfolio-result-count").textContent = filteredPositions.length === positions.length
    ? `${positions.length} 个持仓`
    : `显示 ${filteredPositions.length} / ${positions.length}`;

  const dot = document.getElementById("portfolio-live-dot");
  const statusText = document.getElementById("portfolio-live-text");
  dot.className = runtime.restored_from_cache ? "warning" : runtime.refresh_in_progress ? "running" : (runtime.errors || []).length ? "warning" : "";
  statusText.textContent = !positions.length
    ? "等待持仓"
    : runtime.restored_from_cache ? "上次行情快照 · 待确认" : runtime.refresh_in_progress ? "正在更新行情" : (runtime.errors || []).length ? `${runtime.errors.length} 项行情警告` : "持仓行情已同步";
  const hasFunds = positions.some((item) => item.market === "FUND");
  document.getElementById("portfolio-update-caption").textContent = runtime.updated_at
    ? `${hasFunds ? `股票行情 ${intervalLabel(runtime.interval_seconds, 20)}更新 · 基金净值按交易日确认` : `行情 ${intervalLabel(runtime.interval_seconds, 20)}更新`} · ${formatDate(runtime.updated_at, true)}`
    : "所有持仓数据保存在本机";

  const list = document.getElementById("portfolio-position-list");
  list.replaceChildren();
  if (!positions.length) {
    list.append(emptyState("还没有持仓", "点击“新增持仓”录入第一笔仓位"));
  } else if (!displayPositions.length) {
    list.append(emptyState("没有匹配的持仓", "调整筛选条件或搜索关键词"));
  } else {
    const fragment = document.createDocumentFragment();
    displayPositions.forEach((item) => fragment.append(renderPortfolioPosition(item)));
    list.append(fragment);
  }

  const analysis = portfolio.analysis || {};
  document.getElementById("portfolio-analysis-title").textContent = analysis.title || "等待组合分析";
  document.getElementById("portfolio-analysis-method").textContent = `${analysis.method || "价格与消息综合分析"} · ${analysis.disclaimer || "仅供研究参考"}`;
  const risk = document.getElementById("portfolio-risk-level");
  risk.textContent = analysis.risk_level ? `${analysis.risk_level}风险${summary.valuation_complete === false ? " · 估值不全" : ""}` : "待分析";
  risk.className = `portfolio-risk-badge ${analysis.risk_level === "高" ? "high" : analysis.risk_level === "中" ? "medium" : ""}`;
  const suggestions = document.getElementById("portfolio-suggestions");
  suggestions.replaceChildren();
  (analysis.suggestions || ["添加持仓后生成组合建议。"]).forEach((text) => suggestions.append(el("p", "portfolio-suggestion", text)));

  const allocationList = document.getElementById("portfolio-allocations");
  allocationList.replaceChildren();
  const allocations = portfolio.allocations || [];
  if (!allocations.length) {
    allocationList.append(emptyState("暂无配置数据", "获得有效行情后显示市场占比"));
  } else {
    allocations.forEach((item) => {
      const row = el("div", "portfolio-allocation-row");
      const track = el("div", "portfolio-allocation-track");
      const fill = el("i");
      fill.style.width = `${clamp(Number(item.weight_pct || 0), 2, 100)}%`;
      track.append(fill);
      row.append(el("span", "", marketLabels[item.market] || item.market), track, el("strong", "", `${Number(item.weight_pct || 0).toFixed(1)}%`));
      allocationList.append(row);
    });
  }
}

function renderPortfolioPosition(item) {
  const isFund = item.market === "FUND";
  const row = el("article", "portfolio-position-row");
  row.classList.toggle("fund-position", isFund);
  row.tabIndex = 0;
  row.title = isFund ? `双击查看 ${item.name} 历史净值` : `双击查看 ${item.name} K 线`;
  row.setAttribute("aria-label", `${item.name} 持仓，${row.title}`);
  row.addEventListener("dblclick", (event) => {
    if (event.target instanceof Element && event.target.closest("button, a")) return;
    event.preventDefault();
    openPortfolioChart(item);
  });
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target === row) openPortfolioChart(item);
  });
  const asset = el("div", "portfolio-asset");
  const assetTop = el("div", "portfolio-asset-heading");
  assetTop.append(el("span", "market-badge", marketLabels[item.market] || item.market), el("strong", "", item.name));
  assetTop.title = item.name;
  const assetMeta = el("span", "portfolio-asset-meta", isFund && item.fund_type ? `${item.symbol} · ${item.fund_type}` : item.symbol);
  assetMeta.title = assetMeta.textContent;
  const quoteLine = el("div", "portfolio-asset-quote");
  const quoteLink = el("a", "portfolio-quote-link", formatPortfolioUnitPrice(item.price, item.currency, item.market));
  quoteLink.href = item.source_url;
  quoteLink.target = "_blank";
  quoteLink.rel = "noopener noreferrer";
  quoteLink.title = isFund ? `${item.price_label || "已确认净值"} · ${item.quote_source || "基金数据源"}` : `打开 ${item.name} 行情页`;
  const quoteCaption = isFund
    ? `${signedPercent(item.change_pct)} · ${item.nav_date ? item.nav_date.slice(5) : "等待净值"}`
    : signedPercent(item.change_pct);
  quoteLine.append(quoteLink, el("span", scoreClass(item.change_pct), quoteCaption));
  asset.append(assetTop, instrumentBriefNode(item), assetMeta, quoteLine);
  if(item.market === "US") {
    const sessionLine=el("small","portfolio-session-note",`${item.price_label || "最新价"} · ${item.quote_time ? formatDate(item.quote_time,true) : "等待报价"}`);
    sessionLine.title=[item.source_note,item.quote_error].filter(Boolean).join(" · ");
    if(item.quote_status !== "live")sessionLine.append(el("span","",item.quote_status === "closed" ? " · 休市" : " · 待更新"));
    if(item.extended_change_pct != null)sessionLine.append(el("span",scoreClass(item.extended_change_pct),` · 时段 ${signedPercent(item.extended_change_pct)}`));
    asset.append(sessionLine);
  }

  const quantity = portfolioStat(
    "portfolio-quantity",
    isFund ? "持有份额" : "持仓数量",
    `${formatPositionQuantity(item.quantity)} ${isFund ? "份" : "股"}`,
    `均价 ${formatPortfolioUnitPrice(item.average_cost, item.currency, item.market)}`,
  );
  const value = portfolioStat(
    "portfolio-value",
    "折合市值",
    formatPortfolioMoney(item.market_value_cny, "CNY", true),
    formatPortfolioMoney(item.market_value, item.currency, true),
  );
  const dailyValue = Number(item.daily_pnl_cny || 0);
  const dailyTone = dailyValue > 0 ? "positive" : dailyValue < 0 ? "negative" : "";
  const daily = portfolioStat(
    "portfolio-daily-pnl",
    "今日盈亏",
    formatPortfolioMoney(item.daily_pnl_cny, "CNY", true),
    isFund && item.nav_date
      ? `${item.nav_date.slice(5)} 净值日 ${formatPortfolioMoney(item.latest_nav_pnl_cny, "CNY", true)}`
      : item.quote_status === "stale" ? "报价待更新" : item.price == null ? "等待价格" : signedPercent(item.change_pct),
    dailyTone,
  );
  const pnlValue = Number(item.pnl || 0);
  const pnlTone = pnlValue > 0 ? "positive" : pnlValue < 0 ? "negative" : "";
  const pnl = portfolioStat(
    "portfolio-pnl",
    "浮动盈亏",
    formatPortfolioMoney(item.pnl_cny, "CNY", true),
    item.pnl_pct === null ? "--" : signedPercent(item.pnl_pct),
    pnlTone,
  );
  const weight = el("div", "portfolio-stat portfolio-weight");
  weight.append(
    el("span", "portfolio-cell-label", "仓位"),
    el("strong", "", item.weight_pct === null ? "--" : `${Number(item.weight_pct).toFixed(1)}%`),
  );
  const weightTrack = el("div", "portfolio-weight-track");
  const weightFill = el("i");
  weightFill.style.width = `${clamp(Number(item.weight_pct || 0), 0, 100)}%`;
  weightTrack.append(weightFill);
  weight.append(weightTrack);
  const analysis = item.analysis || {};
  const signal = el("div", "portfolio-signal");
  signal.append(
    el("strong", analysis.direction === "偏多" ? "positive" : analysis.direction === "偏空" ? "negative" : "", `${analysis.action || "继续观察"} · ${analysis.direction || "中性"}`),
    el("span", "", analysis.reason || "等待行情与新闻"),
    el("small", "", `${Number(item.news_count || 0)} 条相关消息 · 综合信号 ${item.signal_available === false ? "--" : formatScore(item.signal_score)}`),
  );
  signal.title = (analysis.risks || []).join("；") || "暂未发现突出风险";
  const research = el("button", "text-button", "研究 / 复盘");
  research.type = "button";
  research.addEventListener("click", () => openStrategyEditor({name:item.name,target:`${item.market}:${item.symbol}`,kind:"stock",risks:(analysis.risks || []).join("\n")}));
  signal.append(research);

  const actions = el("div", "portfolio-actions");
  const buy = el("button", "icon-button");
  buy.append(uiIcon("plus"));
  buy.type = "button";
  buy.title = `加仓 ${item.name}`;
  buy.setAttribute("aria-label", buy.title);
  buy.addEventListener("click", () => openPositionAdjust(item, "buy"));
  const sell = el("button", "icon-button");
  sell.append(uiIcon("minus"));
  sell.type = "button";
  sell.title = `减仓 ${item.name}`;
  sell.setAttribute("aria-label", sell.title);
  sell.addEventListener("click", () => openPositionAdjust(item, "sell"));
  const clear = el("button", "icon-button danger-button delete-position-button");
  const trash = el("span", "trash-icon");trash.setAttribute("aria-hidden", "true");clear.append(trash);
  clear.type = "button";
  clear.title = `删除 ${item.name} 的持仓记录`;
  clear.setAttribute("aria-label", clear.title);
  clear.addEventListener("click", () => removePortfolioPosition(item));
  [buy, sell, clear].forEach(button => button.disabled = state.portfolioDeletePending);
  actions.append(buy, sell, clear);
  row.append(asset, quantity, value, daily, pnl, weight, actions, signal);
  return row;
}

function formatPortfolioChartValue(value, item, withCurrency = true) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (!withCurrency) {
    const digits = item?.market === "FUND" || Math.abs(number) < 10 ? 4 : Math.abs(number) >= 1000 ? 0 : 2;
    return number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  if (item?.currency === "%") return `${number.toFixed(3)}%`;
  return formatPortfolioUnitPrice(number, item?.currency || "", item?.market || "");
}

function formatChartVolume(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "--";
  if (number >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿`;
  if (number >= 10_000) return `${(number / 10_000).toFixed(1)} 万`;
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function syncPortfolioChartPeriodControls() {
  const item = state.portfolioChartItem;
  document.querySelectorAll("#portfolio-chart-modes button").forEach((button) => {
    const unavailable = item?.market === "FUND" && button.dataset.chartMode === "intraday";
    const fundLabels = { intraday: "1 分钟 K", daily: "日净值", weekly: "周净值" };
    button.textContent = item?.market === "FUND" ? fundLabels[button.dataset.chartMode] : PORTFOLIO_CHART_CONFIG[button.dataset.chartMode].label;
    button.disabled = unavailable;
    button.classList.toggle("active", button.dataset.chartMode === state.portfolioChartMode);
  });
  const btcMinutes = item?.market === "ASSET" && item.symbol === "BTC" && state.portfolioChartMode === "intraday";
  const config = btcMinutes ? {periods:{"1d":"近 300 根"}} : PORTFOLIO_CHART_CONFIG[state.portfolioChartMode];
  const container = document.getElementById("portfolio-chart-periods");
  const expected = JSON.stringify(config.periods);
  if (container.dataset.periods !== expected) {
    container.dataset.periods = expected;
    container.replaceChildren();
    Object.entries(config.periods).forEach(([period, label]) => {
      const button = el("button", "", label);
      button.type = "button";
      button.dataset.chartPeriod = period;
      container.append(button);
    });
  }
  container.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.chartPeriod === state.portfolioChartPeriod);
    button.disabled = state.portfolioChartLoading;
  });
  document.getElementById("portfolio-chart-retry").disabled = state.portfolioChartLoading;
}

function resetPortfolioChartMetrics() {
  document.getElementById("portfolio-chart-latest").textContent = "--";
  document.getElementById("portfolio-chart-change").textContent = "--";
  document.getElementById("portfolio-chart-change").className = "";
  document.getElementById("portfolio-chart-high").textContent = "--";
  document.getElementById("portfolio-chart-low").textContent = "--";
  document.getElementById("portfolio-chart-points").textContent = "--";
  document.getElementById("portfolio-chart-interval").textContent = "日线";
  document.getElementById("portfolio-chart-high-label").textContent = "区间最高";
  document.getElementById("portfolio-chart-low-label").textContent = "区间最低";
  renderPortfolioTechnicalAnalysis(null);
}

function setPortfolioChartStatus(message, type = "loading") {
  const status = document.getElementById("portfolio-chart-status");
  status.textContent = message;
  status.className = `portfolio-chart-status${type === "error" ? " error" : ""}`;
  status.hidden = false;
}

function portfolioChartLoadingMessage(item = state.portfolioChartItem) {
  if (item?.market === "FUND") return state.portfolioChartMode === "weekly" ? "正在载入周净值…" : "正在载入历史确认净值…";
  if (state.portfolioChartMode === "intraday") return "正在载入 1 分钟 K 线…";
  return state.portfolioChartMode === "weekly" ? "正在载入周 K 行情…" : "正在载入日 K 行情…";
}

function formatTechnicalIndicator(item) {
  if (item?.value == null || typeof item.value === "boolean" || (typeof item.value === "string" && !item.value.trim())) return "--";
  const value = Number(item?.value);
  if (!Number.isFinite(value)) return "--";
  if (item.format === "price") return formatPortfolioChartValue(value, state.portfolioChartItem);
  if (item.format === "ratio") return `${value.toFixed(2)}x`;
  if (item.format === "signed") {
    const digits = Math.abs(value) < 10 ? 4 : 2;
    return `${value >= 0 ? "+" : ""}${value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  return value.toFixed(1);
}

function renderPortfolioTechnicalAnalysis(technical) {
  const title = document.getElementById("portfolio-technical-title");
  const badge = document.getElementById("portfolio-technical-badge");
  const summary = document.getElementById("portfolio-technical-summary");
  const indicators = document.getElementById("portfolio-indicator-strip");
  const signals = document.getElementById("portfolio-technical-signals");
  indicators.replaceChildren();
  signals.replaceChildren();
  if (!technical) {
    title.textContent = "正在计算技术指标";
    badge.textContent = "等待数据";
    badge.className = "portfolio-technical-badge";
    summary.textContent = "均线、动量、量能与关键位置将显示在这里。";
    document.getElementById("portfolio-technical-disclaimer").textContent = "技术指标用于观察趋势和风险，不构成买卖建议。";
    return;
  }
  title.textContent = technical.headline || "技术指标样本不足";
  const score = Number(technical.score || 0);
  badge.textContent = `${technical.stance || "中性"} · ${score >= 0 ? "+" : ""}${score} · ${technical.confidence || "低"}置信`;
  badge.className = `portfolio-technical-badge ${technical.stance === "偏多" ? "positive" : technical.stance === "偏空" ? "negative" : ""}`;
  summary.textContent = technical.summary || "等待更多行情数据。";
  (technical.indicators || []).forEach((item) => {
    const node = el("div", "portfolio-indicator");
    node.append(el("span", "", item.label), el("strong", item.tone || "", formatTechnicalIndicator(item)));
    indicators.append(node);
  });
  if (!(technical.indicators || []).length) indicators.append(el("div", "portfolio-indicator", "样本不足"));
  (technical.signals || []).forEach((item) => {
    const node = el("div", `portfolio-technical-signal ${item.tone || "neutral"}`);
    node.append(el("strong", "", item.label), el("span", "", item.text));
    signals.append(node);
  });
  document.getElementById("portfolio-technical-disclaimer").textContent = technical.disclaimer || "技术指标用于观察趋势和风险，不构成买卖建议。";
}

function syncPortfolioChartPolling() {
  window.clearInterval(state.portfolioChartPoller);
  state.portfolioChartPoller = null;
  if (state.portfolioChartMode !== "intraday" || !document.getElementById("portfolio-chart-dialog").open) return;
  state.portfolioChartPoller = window.setInterval(() => {
    if (document.visibilityState === "visible") loadPortfolioChart({ silent: true });
  }, Math.max(20, Number(state.portfolioChartData?.refresh_seconds || 20)) * 1000);
}

function openPortfolioChart(item, mode = "daily") {
  const isFund = item.market === "FUND";
  const dialog = document.getElementById("portfolio-chart-dialog");
  state.portfolioChartRequestId += 1;
  state.portfolioChartLoading = false;
  window.clearInterval(state.portfolioChartPoller);
  state.portfolioChartPoller = null;
  state.portfolioChartItem = item;
  state.portfolioChartMode = PORTFOLIO_CHART_CONFIG[mode] ? mode : "daily";
  state.portfolioChartPeriod = PORTFOLIO_CHART_CONFIG[state.portfolioChartMode].defaultPeriod;
  state.portfolioChartData = null;
  state.portfolioChartHoverIndex = null;
  state.portfolioChartWindowStart = null;
  document.getElementById("portfolio-chart-market").textContent = item.market === "ASSET" ? "资产参考" : marketLabels[item.market] || item.market;
  document.getElementById("portfolio-chart-kind").textContent = isFund ? "日净值" : PORTFOLIO_CHART_CONFIG[state.portfolioChartMode].label;
  document.getElementById("portfolio-chart-title").textContent = item.name;
  briefAfterTitle("portfolio-chart-title", item);
  if (item.chartBrief) {
    const brief = document.getElementById("portfolio-chart-title-brief");
    brief.textContent = item.chartBrief;
    brief.removeAttribute("data-instrument-brief");
  }
  document.getElementById("portfolio-chart-symbol").textContent = item.symbol;
  document.getElementById("portfolio-chart-price-label").textContent = isFund ? "最新确认净值" : state.portfolioChartMode === "intraday" ? "最新分钟收" : "最新收盘";
  document.getElementById("portfolio-chart-note").textContent = item.chartBrief || (isFund
    ? "基金不提供盘中开高低收；这里展示历史确认单位净值，并非股票 K 线。"
    : "股票显示真实开、高、低、收与成交量。");
  const source = document.getElementById("portfolio-chart-source");
  const sourceUrls = {
    GOLD: "https://quote.eastmoney.com/unify/r/101.GC00Y", OIL: "https://quote.eastmoney.com/unify/r/102.CL00Y",
    US5Y: "https://quote.eastmoney.com/unify/r/171.US5Y", BTC: "https://www.coinbase.com/advanced-trade/spot/BTC-USD",
  };
  source.href = item.market === "ASSET" ? sourceUrls[item.symbol] || "" : item.market === "FUND"
    ? `https://fund.eastmoney.com/${encodeURIComponent(item.symbol)}.html`
    : `https://finance.yahoo.com/quote/${encodeURIComponent(item.symbol)}/chart/`;
  source.textContent = "查看原始行情 ↗";
  source.hidden = !source.href;
  resetPortfolioChartMetrics();
  syncPortfolioChartPeriodControls();
  setPortfolioChartStatus(portfolioChartLoadingMessage(item));
  const tooltip = document.getElementById("portfolio-chart-tooltip");
  tooltip.hidden = true;
  if (!dialog.open) dialog.showModal();
  window.requestAnimationFrame(() => loadPortfolioChart());
}

async function loadPortfolioChart({ silent = false } = {}) {
  const item = state.portfolioChartItem;
  if (!item || (silent && state.portfolioChartLoading)) return;
  const chartKey = `${item.market}:${item.symbol}:${state.portfolioChartMode}:${state.portfolioChartPeriod}`;
  const keepExisting = Boolean(state.portfolioChartData && state.portfolioChartLoadedKey === chartKey);
  const requestId = ++state.portfolioChartRequestId;
  if (state.portfolioChartRetryKey === chartKey && Date.now() < state.portfolioChartRetryAt) {
    state.portfolioChartLoading = false;
    if (!keepExisting) {
      state.portfolioChartData = null;
      state.portfolioChartWindowStart = null;
      resetPortfolioChartMetrics();
      renderPortfolioTechnicalAnalysis(null);
      const seconds = Math.ceil((state.portfolioChartRetryAt - Date.now()) / 1000);
      setPortfolioChartStatus(`行情源暂不可用，约 ${seconds} 秒后可重试。`, "error");
    }
    syncPortfolioChartPeriodControls();
    syncPortfolioChartPolling();
    return;
  }
  const isFund = item.market === "FUND";
  state.portfolioChartLoading = true;
  if (!keepExisting) {
    state.portfolioChartData = null;
    state.portfolioChartWindowStart = null;
    resetPortfolioChartMetrics();
    renderPortfolioTechnicalAnalysis(null);
  }
  state.portfolioChartHoverIndex = null;
  document.getElementById("portfolio-chart-tooltip").hidden = true;
  syncPortfolioChartPeriodControls();
  if (!keepExisting) setPortfolioChartStatus(portfolioChartLoadingMessage(item));
  try {
    const query = new URLSearchParams({ market: item.market, symbol: item.symbol, mode: state.portfolioChartMode, period: state.portfolioChartPeriod });
    const data = await api(`/api/portfolio/chart?${query.toString()}`);
    if (requestId !== state.portfolioChartRequestId) return;
    state.portfolioChartData = data;
    state.portfolioChartLoadedKey = chartKey;
    state.portfolioChartRetryKey = null;
    state.portfolioChartRetryAt = 0;
    const stats = data.stats || {};
    const displayItem = { ...item, currency: data.currency || item.currency };
    if (item.market === "ASSET") {
      briefAfterTitle("portfolio-chart-title", item);
      const brief = document.getElementById("portfolio-chart-title-brief");
      brief.textContent = data.instrument_note || item.chartBrief || brief.textContent;
      brief.removeAttribute("data-instrument-brief");
    }
    const latest = Number(stats.latest);
    document.getElementById("portfolio-chart-price-label").textContent = isFund ? "最新净值" : data.mode === "intraday"
      ? data.session_status === "closed" ? "休市前分钟收" : data.retained_from_cache ? "已保存的分钟收" : "最新分钟收"
      : data.retained_from_cache ? "已保存的收盘" : "最新收盘";
    const change = Number(stats.change_pct || 0);
    document.getElementById("portfolio-chart-latest").textContent = formatPortfolioChartValue(latest, displayItem);
    const changeNode = document.getElementById("portfolio-chart-change");
    changeNode.textContent = signedPercent(change);
    changeNode.className = change > 0 ? "positive" : change < 0 ? "negative" : "neutral";
    document.getElementById("portfolio-chart-high").textContent = formatPortfolioChartValue(stats.high, displayItem);
    document.getElementById("portfolio-chart-low").textContent = formatPortfolioChartValue(stats.low, displayItem);
    const pointsUnit = data.mode === "intraday" ? "根" : data.mode === "weekly" ? "个交易周" : "个交易日";
    document.getElementById("portfolio-chart-points").textContent = `${Number(stats.points || 0)} ${pointsUnit}`;
    const periodLabel = item.market === "ASSET" && item.symbol === "BTC" && data.mode === "intraday" ? "近 300 根" : PORTFOLIO_CHART_CONFIG[data.mode]?.periods?.[data.period] || "区间";
    document.getElementById("portfolio-chart-interval").textContent = `${periodLabel} · ${PORTFOLIO_CHART_INTERVAL_LABELS[data.interval] || data.interval}`;
    const kindLabel = isFund ? (data.mode === "weekly" ? "周净值" : "日净值") : PORTFOLIO_CHART_CONFIG[data.mode]?.label || "行情";
    document.getElementById("portfolio-chart-kind").textContent = (data.extended_hours ? `${kindLabel} · 含盘前盘后` : kindLabel)
      + (data.session_status === "closed" ? " · 休市" : data.retained_from_cache ? " · 历史快照" : "");
    document.getElementById("portfolio-chart-high-label").textContent = "区间最高";
    document.getElementById("portfolio-chart-low-label").textContent = "区间最低";
    const cutoff = portfolioChartCutoff(data);
    const asOf = cutoff ? ` 数据截至 ${cutoff}。` : "";
    document.getElementById("portfolio-chart-note").textContent = (data.note || "股票显示真实日线开、高、低、收与成交量。") + asOf;
    const source = document.getElementById("portfolio-chart-source");
    source.href = data.source_url;
    source.textContent = `打开 ${data.source || "数据源"} ↗`;
    source.hidden = false;
    document.getElementById("portfolio-chart-canvas").setAttribute(
      "aria-label",
      data.kind === "nav" ? `${item.name} 历史单位净值走势` : `${item.name} ${kindLabel}与成交量`,
    );
    renderPortfolioTechnicalAnalysis(data.technical);
    document.getElementById("portfolio-chart-status").hidden = true;
    window.requestAnimationFrame(() => drawPortfolioChart(document.getElementById("portfolio-chart-canvas"), data));
  } catch (error) {
    if (requestId !== state.portfolioChartRequestId) return;
    state.portfolioChartRetryKey = chartKey;
    const delay = Number(error.retryAfterSeconds);
    state.portfolioChartRetryAt = Date.now() + (Number.isFinite(delay) ? Math.max(0, delay) : 0) * 1000;
    if (keepExisting) {
      const lastLabel = portfolioChartCutoff(state.portfolioChartData) || "时间未知";
      const retryText = state.portfolioChartMode === "intraday" ? "将自动重试" : "稍后可重新打开";
      document.getElementById("portfolio-chart-note").textContent = `本次刷新失败，保留上一份图表（数据截至 ${lastLabel}），${retryText}。${error.message || ""}`;
    } else {
      setPortfolioChartStatus(error.message || "历史行情载入失败", "error");
    }
  } finally {
    if (requestId === state.portfolioChartRequestId) {
      state.portfolioChartLoading = false;
      syncPortfolioChartPeriodControls();
      syncPortfolioChartPolling();
    }
  }
}

function portfolioChartEntries(data) {
  return data?.kind === "nav" ? (data.points || []) : (data?.candles || []);
}

function portfolioChartCutoff(data) {
  const last = portfolioChartEntries(data).at(-1);
  if (data?.mode !== "intraday" && /^\d{4}-\d{2}-\d{2}$/.test(last?.date || "")) return last.date.replaceAll("-", "/");
  const stamp = Number(data?.stats?.latest_timestamp || 0);
  if (!Number.isFinite(stamp) || stamp <= 0) return "";
  const options = { year: "numeric", month: "2-digit", day: "2-digit", timeZone: data.exchange_timezone || "UTC" };
  if (data.mode === "intraday") Object.assign(options, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  let zone = options.timeZone, label;
  try { label = new Intl.DateTimeFormat("zh-CN", options).format(new Date(stamp * 1000)); }
  catch (_) { zone = "UTC"; label = new Intl.DateTimeFormat("zh-CN", { ...options, timeZone: zone }).format(new Date(stamp * 1000)); }
  const zoneLabel = { "Asia/Shanghai": "北京时间", "Asia/Hong_Kong": "香港时间", "Asia/Seoul": "首尔时间", "America/New_York": "美东时间" }[zone] || zone;
  return label + (data.mode === "intraday" ? `（${zoneLabel}）` : "");
}

function portfolioChartDateLabel(entry, detailed = false) {
  const date = String(entry?.date || "");
  if (!date) return "--";
  if (state.portfolioChartData?.mode === "intraday" && entry.timestamp) {
    const options = state.portfolioChartPeriod === "1d"
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
    if (detailed) Object.assign(options, { year: "numeric", month: "2-digit", day: "2-digit" });
    const timezone = state.portfolioChartData.exchange_timezone;
    if (timezone) options.timeZone = timezone;
    try {
      const label=new Intl.DateTimeFormat("zh-CN", options).format(new Date(Number(entry.timestamp) * 1000));
      return detailed && entry.session ? `${label} · ${{pre:"盘前",regular:"常规",post:"盘后",unknown:"时段未确认"}[entry.session] || ""}` : label;
    } catch (_) {
      return new Intl.DateTimeFormat("zh-CN", { ...options, timeZone: undefined }).format(new Date(Number(entry.timestamp) * 1000));
    }
  }
  if (detailed) return date;
  return state.portfolioChartPeriod === "1y" ? date.slice(2, 7) : date.slice(5);
}

function drawPortfolioChart(canvas, data, hoverIndex = null) {
  const allEntries = portfolioChartEntries(data);
  const context = canvas.getContext("2d");
  const width = Math.max(1, Math.round(canvas.clientWidth || 300));
  const height = Math.max(1, Math.round(canvas.clientHeight || 240));
  const pixelRatio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  canvas.width = Math.round(width * pixelRatio);
  canvas.height = Math.round(height * pixelRatio);
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  if (!allEntries.length) return;

  const isNav = data.kind === "nav";
  const isIntraday = data.mode === "intraday";
  const hasVolume = !isNav;
  const isLineChart = isNav;
  const left = width < 520 ? 64 : 78;
  const right = width - 16;
  const top = 29;
  const bottom = height - 32;
  const priceBottom = hasVolume ? top + (bottom - top) * 0.73 : bottom;
  const volumeTop = priceBottom + 18;
  const volumeBottom = bottom;
  const plotWidth = Math.max(1, right - left);
  const visibleCount = isIntraday ? Math.max(30, Math.floor(plotWidth / 6)) : allEntries.length;
  const maxStart = Math.max(0, allEntries.length - visibleCount);
  const offset = state.portfolioChartWindowStart === null ? maxStart : clamp(state.portfolioChartWindowStart, 0, maxStart);
  const entries = allEntries.slice(offset, offset + visibleCount);
  const range = document.getElementById("portfolio-chart-window");
  const navigator = document.getElementById("portfolio-chart-navigator");
  if (range && navigator) {
    navigator.hidden = !isIntraday || !maxStart;
    range.max = String(maxStart);
    range.value = String(offset);
    range.setAttribute("aria-valuetext", `${portfolioChartDateLabel(entries[0], true)} 至 ${portfolioChartDateLabel(entries.at(-1), true)}`);
  }
  const overlays = (data.technical?.overlays || []).map(overlay => ({...overlay, values:(overlay.values || []).slice(offset, offset + visibleCount)}));
  const overlayValues = overlays.flatMap((overlay) => overlay.values).filter((value) => value !== null && Number.isFinite(Number(value))).map(Number);
  const rawHigh = Math.max(...entries.map((entry) => Number(isNav ? entry.nav : entry.high)), ...overlayValues);
  const rawLow = Math.min(...entries.map((entry) => Number(isNav ? entry.nav : entry.low)), ...overlayValues);
  const rawSpan = Math.max(rawHigh - rawLow, Math.abs(rawHigh) * 0.008, 0.0001);
  const high = rawHigh + rawSpan * 0.08;
  const low = Math.max(0, rawLow - rawSpan * 0.08);
  const span = Math.max(high - low, 0.0001);
  const xAt = (index) => isLineChart && entries.length > 1
    ? left + (index / (entries.length - 1)) * plotWidth
    : left + ((index + 0.5) / entries.length) * plotWidth;
  const yAt = (value) => top + ((high - Number(value)) / span) * (priceBottom - top);

  context.font = `11px ${getComputedStyle(canvas).fontFamily}`;
  let legendX = left;
  overlays.forEach((overlay) => {
    if (!(overlay.values || []).some((value) => value !== null)) return;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillStyle = PORTFOLIO_OVERLAY_COLORS[overlay.key] || "#66737a";
    context.fillText(overlay.label, legendX, 13);
    legendX += context.measureText(overlay.label).width + 16;
  });
  context.textBaseline = "middle";
  context.textAlign = "right";
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = top + ratio * (priceBottom - top);
    const value = high - ratio * span;
    context.strokeStyle = index === 4 ? "#ccd4d7" : "#edf0f1";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(left, Math.round(y) + 0.5);
    context.lineTo(right, Math.round(y) + 0.5);
    context.stroke();
    context.fillStyle = "#616977";
    context.fillText(formatPortfolioChartValue(value, state.portfolioChartItem, false), left - 8, y);
  }

  const tickCount = clamp(Math.floor(plotWidth / (isIntraday && data.period === "5d" ? 100 : 72)), 2, 5);
  const tickIndexes = [...new Set(Array.from({length:tickCount}, (_, index) => Math.round((entries.length - 1) * index / (tickCount - 1))))];
  context.textAlign = "center";
  context.textBaseline = "bottom";
  tickIndexes.forEach((index) => {
    const x = xAt(index);
    context.fillStyle = "#616977";
    context.fillText(portfolioChartDateLabel(entries[index]), x, height - 8);
  });

  const previousClose = Number(data.stats?.previous_close);
  if (isIntraday && Number.isFinite(previousClose) && previousClose >= low && previousClose <= high) {
    const baselineY = yAt(previousClose);
    context.save();
    context.setLineDash([4, 4]);
    context.strokeStyle = "#a8b1b5";
    context.beginPath();
    context.moveTo(left, baselineY);
    context.lineTo(right, baselineY);
    context.stroke();
    context.restore();
  }

  if (isLineChart) {
    const lineColor = isNav ? "#27738d" : Number(data.stats?.change_pct || 0) >= 0 ? "#0c8a62" : "#c94b55";
    context.beginPath();
    entries.forEach((entry, index) => {
      const x = xAt(index);
      const y = yAt(isNav ? entry.nav : entry.close);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.lineTo(xAt(entries.length - 1), priceBottom);
    context.lineTo(xAt(0), priceBottom);
    context.closePath();
    context.fillStyle = isNav ? "rgba(39, 115, 141, .10)" : Number(data.stats?.change_pct || 0) >= 0 ? "rgba(12, 138, 98, .08)" : "rgba(201, 75, 85, .08)";
    context.fill();
    context.beginPath();
    entries.forEach((entry, index) => {
      const x = xAt(index);
      const y = yAt(isNav ? entry.nav : entry.close);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = lineColor;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.stroke();
  } else {
    const step = plotWidth / Math.max(1, entries.length);
    const bodyWidth = clamp(step * 0.62, 1, 11);
    entries.forEach((entry, index) => {
      const x = xAt(index);
      const open = Number(entry.open);
      const close = Number(entry.close);
      const color = close >= open ? "#0c8a62" : "#c94b55";
      context.strokeStyle = color;
      context.fillStyle = color;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(Math.round(x) + 0.5, yAt(entry.high));
      context.lineTo(Math.round(x) + 0.5, yAt(entry.low));
      context.stroke();
      const bodyTop = yAt(Math.max(open, close));
      const bodyBottom = yAt(Math.min(open, close));
      context.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, Math.max(1, bodyBottom - bodyTop));
    });
  }

  if (hasVolume) {
    const step = plotWidth / Math.max(1, entries.length);
    const volumeWidth = clamp(step * 0.62, 1, 11);
    const maxVolume = Math.max(1, ...entries.map((entry) => Number(entry.volume || 0)));
    context.strokeStyle = "#edf0f1";
    context.beginPath();
    context.moveTo(left, Math.round(volumeTop) + 0.5);
    context.lineTo(right, Math.round(volumeTop) + 0.5);
    context.stroke();
    entries.forEach((entry, index) => {
      const x = xAt(index);
      const color = Number(entry.close) >= Number(entry.open) ? "#0c8a62" : "#c94b55";
      const volumeHeight = (Number(entry.volume || 0) / maxVolume) * Math.max(1, volumeBottom - volumeTop);
      context.globalAlpha = 0.34;
      context.fillStyle = color;
      context.fillRect(x - volumeWidth / 2, volumeBottom - volumeHeight, volumeWidth, volumeHeight);
      context.globalAlpha = 1;
    });
  }

  overlays.forEach((overlay) => {
    const values = overlay.values || [];
    context.beginPath();
    let drawing = false;
    values.forEach((value, index) => {
      if (value === null || value === undefined || !Number.isFinite(Number(value)) || !entries[index]) {
        drawing = false;
        return;
      }
      const x = xAt(index);
      const y = yAt(value);
      if (!drawing) context.moveTo(x, y);
      else context.lineTo(x, y);
      drawing = true;
    });
    context.strokeStyle = PORTFOLIO_OVERLAY_COLORS[overlay.key] || "#66737a";
    context.lineWidth = overlay.key === "vwap" ? 1.4 : 1.15;
    context.globalAlpha = 0.9;
    context.stroke();
    context.globalAlpha = 1;
  });

  if (hoverIndex !== null && entries[hoverIndex]) {
    const entry = entries[hoverIndex];
    const x = xAt(hoverIndex);
    const value = Number(isNav ? entry.nav : entry.close);
    const y = yAt(value);
    context.save();
    context.setLineDash([4, 4]);
    context.strokeStyle = "rgba(39, 54, 61, .42)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(x, top);
    context.lineTo(x, bottom);
    context.moveTo(left, y);
    context.lineTo(right, y);
    context.stroke();
    context.restore();
    context.beginPath();
    context.arc(x, y, 3.5, 0, Math.PI * 2);
    context.fillStyle = isNav ? "#27738d" : Number(entry.close) >= Number(entry.open) ? "#0c8a62" : "#c94b55";
    context.fill();
    context.strokeStyle = "#ffffff";
    context.lineWidth = 1.5;
    context.stroke();
  }

  canvas._portfolioChartGeometry = { data, entries, offset, left, right, top, bottom, xAt };
}

function updatePortfolioChartTooltip(event) {
  const canvas = event.currentTarget;
  const geometry = canvas._portfolioChartGeometry;
  const tooltip = document.getElementById("portfolio-chart-tooltip");
  if (!geometry?.entries?.length) {
    tooltip.hidden = true;
    return;
  }
  const canvasRect = canvas.getBoundingClientRect();
  const stageRect = document.getElementById("portfolio-chart-stage").getBoundingClientRect();
  const localX = event.clientX - canvasRect.left;
  if (localX < geometry.left || localX > geometry.right) {
    tooltip.hidden = true;
    return;
  }
  const ratio = (localX - geometry.xAt(0)) / Math.max(1, geometry.xAt(geometry.entries.length - 1) - geometry.xAt(0));
  const index = clamp(Math.round(ratio * (geometry.entries.length - 1)), 0, geometry.entries.length - 1);
  const entry = geometry.entries[index];
  if (state.portfolioChartHoverIndex !== index) {
    state.portfolioChartHoverIndex = index;
    drawPortfolioChart(canvas, geometry.data, index);
  }
  tooltip.replaceChildren(el("strong", "", portfolioChartDateLabel(entry, true)));
  if (geometry.data.kind === "nav") {
    tooltip.append(
      el("span", "", `单位净值 ${formatPortfolioChartValue(entry.nav, state.portfolioChartItem)}`),
      el("span", scoreClass(entry.change_pct), `当日 ${entry.change_pct === null ? "--" : signedPercent(entry.change_pct)}`),
    );
  } else {
    tooltip.append(
      el("span", "", `开 ${formatPortfolioChartValue(entry.open, state.portfolioChartItem)}  高 ${formatPortfolioChartValue(entry.high, state.portfolioChartItem)}`),
      el("span", "", `低 ${formatPortfolioChartValue(entry.low, state.portfolioChartItem)}  收 ${formatPortfolioChartValue(entry.close, state.portfolioChartItem)}`),
      el("span", "", `成交量 ${formatChartVolume(entry.volume)}`),
    );
  }
  const overlayText = (geometry.data.technical?.overlays || []).map((overlay) => {
    const value = overlay.values?.[index + geometry.offset];
    return value === null || value === undefined ? null : `${overlay.label} ${formatPortfolioChartValue(value, state.portfolioChartItem, false)}`;
  }).filter(Boolean).join("  ");
  if (overlayText) tooltip.append(el("span", "portfolio-chart-tooltip-overlays", overlayText));
  tooltip.hidden = false;
  const pointerX = event.clientX - stageRect.left;
  const pointerY = event.clientY - stageRect.top;
  const preferredLeft = pointerX + 14;
  const left = preferredLeft + tooltip.offsetWidth <= stageRect.width - 8
    ? preferredLeft
    : pointerX - tooltip.offsetWidth - 14;
  tooltip.style.left = `${clamp(left, 8, stageRect.width - tooltip.offsetWidth - 8)}px`;
  tooltip.style.top = `${clamp(pointerY - tooltip.offsetHeight / 2, 8, stageRect.height - tooltip.offsetHeight - 8)}px`;
}

function hidePortfolioChartTooltip(event) {
  document.getElementById("portfolio-chart-tooltip").hidden = true;
  state.portfolioChartHoverIndex = null;
  if (state.portfolioChartData) drawPortfolioChart(event.currentTarget, state.portfolioChartData);
}

function normalizeNewsScore(value) {
  const raw = Number(value || 0);
  if (!Number.isFinite(raw)) return 0;
  return clamp(raw, -1, 1);
}

function collectKoreaStockNews(data, definition) {
  return (data.news || []).filter((item) => {
    const itemSymbol = normalizeStockSymbol(item.symbol);
    const targetSymbol = normalizeStockSymbol(definition.symbol);
    return String(item.market || "").toUpperCase() === definition.market && itemSymbol === targetSymbol;
  });
}

function collectKoreaStockMovement(data, definition) {
  const target = normalizeStockSymbol(definition.symbol);
  const candidates = (data.movements || []).filter((item) =>
    String(item.market || "").toUpperCase() === definition.market && normalizeStockSymbol(item.symbol) === target,
  );
  if (!candidates.length) return null;
  return candidates.sort((a, b) =>
    Number(new Date(b.detected_at || 0)) - Number(new Date(a.detected_at || 0))
  )[0];
}

function collectKoreaStockQuote(data, definition) {
  const target = normalizeStockSymbol(definition.symbol);
  return ((data.korea_quotes || {}).quotes || []).find((item) =>
    normalizeStockSymbol(item.symbol) === target,
  ) || null;
}

function calculateKoreaProbability(item, definition) {
  const now = Date.now();
  const quote = collectKoreaStockQuote(item, definition);
  const news = collectKoreaStockNews(item, definition);
  const quoteAge = now - Date.parse(String(quote?.quote_time || ""));
  const price = Number(quote?.price);
  const marketOpen = item.korea_quotes?.market_open === true;
  const available = marketOpen && quote?.status === "live"
    && Number.isFinite(quoteAge) && quoteAge >= -60_000 && quoteAge <= 120_000
    && Number.isFinite(price) && price > 0
    && quote.change_pct != null && Number.isFinite(Number(quote.change_pct));
  const unavailableReason = !marketOpen ? "休市或交易状态未确认，暂停预测"
    : "缺少新鲜有效行情，暂停预测";
  const horizons = [
    { key: "15m", label: "15m", hours: 0.25 },
    { key: "1h", label: "1h", hours: 1 },
    { key: "24h", label: "24h", hours: 24 },
  ];

  return horizons.map((horizon) => {
    const windowItems = news.filter((newsItem) => {
      const publishedAt = Date.parse(String(newsItem.published_at || ""));
      return Number.isFinite(publishedAt) && publishedAt <= now && (now - publishedAt) <= horizon.hours * 3600_000;
    });
    if (!available) return {
      horizon: horizon.label, available: false, up_probability: null, down_probability: null,
      confidence: null, direction: "待确认", story_count: windowItems.length,
      headlines: [], reason: unavailableReason,
    };
    let positiveWeight = 0;
    let negativeWeight = 0;
    for (const row of windowItems) {
      const direction = normalizeNewsScore(row.score);
      const ageHours = (now - Date.parse(String(row.published_at))) / 3600000;
      const recency = Math.exp(-ageHours / 12);
      if (direction >= 0) positiveWeight += direction * recency;
      else negativeWeight += -direction * recency;
    }

    const total = positiveWeight + negativeWeight || 1;
    const sentimentScore = (positiveWeight - negativeWeight) / total;
    const liveChangePct = quote.change_pct;
    const movementBoost = clamp(Number(liveChangePct || 0), -1.5, 1.5) * 4;
    const upProbability = clamp(50 + sentimentScore * 36 + movementBoost, 8, 92);
    const downProbability = 100 - upProbability;
    const confidence = Math.round(clamp(
      (windowItems.length / 8) * 35 + Math.abs(Number(liveChangePct || 0)) * 8,
      8,
      92,
    ));
    const directionText = upProbability >= 58 ? "偏多" : upProbability <= 42 ? "偏空" : "震荡";
    return {
      horizon: horizon.label,
      available: true,
      up_probability: Number(upProbability.toFixed(1)),
      down_probability: Number(downProbability.toFixed(1)),
      confidence,
      direction: directionText,
      story_count: windowItems.length,
      headlines: windowItems.slice(0, 2).map((entry) => String(entry.title || "")),
    };
  });
}

function getKoreaWatchItem(data, definition) {
  return (data.watchlist || []).find((item) =>
    String(item.market || "").toUpperCase() === definition.market
    && normalizeStockSymbol(item.symbol) === normalizeStockSymbol(definition.symbol)
  );
}

function collectKoreaStockMovementHistory(data, definition, limit = 14) {
  const target = normalizeStockSymbol(definition.symbol);
  return (data.movements || [])
    .filter((item) =>
      String(item.market || "").toUpperCase() === definition.market
      && normalizeStockSymbol(item.symbol) === target,
    )
    .sort((left, right) => Date.parse(String(left.detected_at || "")) - Date.parse(String(right.detected_at || "")))
    .slice(-limit);
}

function buildKoreaStockOutlook(definition, data, movement, quote, probabilities) {
  const now = Date.now();
  const cutoff = now - 24 * 3600_000;
  const recentNews = collectKoreaStockNews(data, definition)
    .map((row) => {
      const publishedAt = Date.parse(String(row.published_at || ""));
      return { row, publishedAt };
    })
    .filter((record) => Number.isFinite(record.publishedAt) && record.publishedAt >= cutoff && record.publishedAt <= now)
    .map((record) => record.row);

  let weightedScore = 0;
  let totalWeight = 0;
  const themeRank = new Map();
  const channelSet = new Set();
  recentNews.forEach((item) => {
    const score = normalizeNewsScore(item.score);
    const publishedAt = Date.parse(String(item.published_at || ""));
    const ageHours = Number.isFinite(publishedAt) ? (now - publishedAt) / 3600_000 : 12;
    const weight = Math.exp(-ageHours / 14) * (0.65 + Math.min(0.35, Number(item.source_count || 1) * 0.05));
    weightedScore += score * weight;
    totalWeight += weight;
    const eventType = String(item.event_type || "market_news");
    themeRank.set(eventType, (themeRank.get(eventType) || 0) + Math.abs(score) * (1 + Math.exp(-ageHours / 20)));
    (item.channels || (item.source ? [item.source] : [])).forEach((channel) => {
      if (channel) channelSet.add(String(channel));
    });
  });

  const aggregateScore = totalWeight === 0 ? 0 : weightedScore / totalWeight;
  const positive = recentNews.filter((row) => normalizeNewsScore(row.score) > 0).length;
  const negative = recentNews.filter((row) => normalizeNewsScore(row.score) < 0).length;
  const neutral = recentNews.length - positive - negative;
  const trendScore = Number(quote?.change_pct ?? movement?.change_pct ?? 0);
  const tone = !recentNews.length ? "样本不足" : aggregateScore > 0.08 ? "偏多" : aggregateScore < -0.08 ? "偏空" : "中性";
  const themes = [...themeRank.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 2)
    .map(([item]) => eventLabels[item] || item);
  const topForecast = probabilities.find((item) => item.horizon === "24h")
    || probabilities[probabilities.length - 1]
    || null;
  const available = topForecast?.available === true;
  const up = topForecast ? topForecast.up_probability : 50;
  const down = topForecast ? topForecast.down_probability : 50;
  const basePrice = Number(quote?.price ?? movement?.price ?? 0);
  const quotePrices = (quote?.chart || []).map((record) => Number(record[1])).filter(Number.isFinite);
  const historyPrices = quotePrices.length > 2
    ? quotePrices
    : collectKoreaStockMovementHistory(data, definition, 14)
      .map((record) => Number(record.price || 0))
      .filter(Number.isFinite);
  const rangePct = historyPrices.length > 2
    ? (Math.max(...historyPrices) - Math.min(...historyPrices)) / Math.max(...historyPrices) * 100
    : 0;
  const baseMovePct = Math.max(0.4, Math.min(6.8, Math.abs(aggregateScore) * 100));
  const rangeBoost = clamp(rangePct, 0.2, 1.4);
  const trendBoost = clamp(Math.abs(trendScore) * 1.1, 0.25, 2);
  const forecastMove = clamp(baseMovePct + rangeBoost + trendBoost, 0.5, 9.5);
  const expectedLow = basePrice > 0 ? basePrice * (1 - forecastMove / 100) : null;
  const expectedHigh = basePrice > 0 ? basePrice * (1 + forecastMove / 100) : null;
  const confidence = clamp(
    Math.round((topForecast?.confidence || 45) * 0.75 + Math.min(20, recentNews.length * 2.8) + Math.abs(trendScore) * 0.7),
    10,
    95,
  );
  const forecastDirection = topForecast ? topForecast.direction : tone;

  const topEvents = recentNews
    .map((item) => {
      const publishedAt = Date.parse(String(item.published_at || ""));
      const ageHours = Number.isFinite(publishedAt) ? Math.max(0, (now - publishedAt) / 3600_000) : 999;
      const score = normalizeNewsScore(item.score);
      const weight = Math.exp(-ageHours / 10) * (0.9 + Math.abs(score));
      return { item, weight };
    })
    .sort((left, right) => right.weight - left.weight)
    .slice(0, 3)
    .map((entry) => ({
      title: String(entry.item.title || ""),
      title_zh: entry.item.title_zh,
      title_translation_status: entry.item.title_translation_status,
      url: String(entry.item.url || ""),
      source: String(entry.item.source || "新闻来源"),
      direction: normalizeNewsScore(entry.item.score) > 0 ? "bullish" : normalizeNewsScore(entry.item.score) < 0 ? "bearish" : "neutral",
      event_type: String(entry.item.event_type || "market_news"),
    }));

  const riskOpposite = forecastDirection === "偏多" ? "bearish" : "bullish";
  const opposingEvent = topEvents.find((event) => event.direction === riskOpposite);
  const riskText = opposingEvent
    ? `反向风险：${opposingEvent.title || "最新反向事件"}`
    : "反向风险：突发宏观波动与资金行为切换";

  return {
    summary: {
      tone,
      story_count: recentNews.length,
      channel_count: channelSet.size,
      bullish_count: positive,
      bearish_count: negative,
      neutral_count: neutral,
      text: `过去24小时共收录 ${recentNews.length} 条有效消息，利多 ${positive} 条、利空 ${negative} 条、中性 ${neutral} 条。${themes.length ? ` 重点方向：${themes.join("、")}` : ""}`,
      key_events: topEvents,
    },
    forecast: available ? {
      available: true,
      tone: forecastDirection,
      up_probability: up,
      down_probability: down,
      confidence,
      expected_move_pct: forecastMove,
      text: `未来24小时研究信号${forecastDirection}，启发式波动范围约 ±${forecastMove.toFixed(2)}%，尚未校准。`,
      expected_low: expectedLow,
      expected_high: expectedHigh,
      base_price: basePrice,
      currency: String(quote?.currency || movement?.currency || "KRW"),
      risk_text: `${riskText}，置信度 ${confidence}%`,
      drivers: topEvents.map((event) => `${event.event_type ? (eventLabels[event.event_type] || "市场消息") : "市场消息"}（${event.direction === "bullish" ? "利多" : event.direction === "bearish" ? "利空" : "中性"}）`),
    } : {
      available: false, tone: "待确认", up_probability: null, down_probability: null,
      confidence: null, expected_move_pct: null, expected_low: null, expected_high: null,
      base_price: null, currency: "KRW", drivers: [],
      text: topForecast?.reason || "输入不足，暂停预测", risk_text: "暂无有效预测区间",
    },
  };
}

function renderKoreaStockProbability(definition, rows) {
  const container = document.getElementById(`kr-stock-probability-${definition.id}`);
  container.replaceChildren();
  if (!rows.length) {
    container.append(emptyState("暂无三星/海力士研究信号", "等待新闻与异动输入"));
    return;
  }
  const fragment = document.createDocumentFragment();
  rows.forEach((row) => {
    const probabilityRow = el("article", "probability-row");
    const identity = el("div", "probability-identity");
    const horizonLabel = row.horizon === "15m" ? "15 分钟" : row.horizon === "1h" ? "1 小时" : "24 小时";
    identity.append(el("strong", "", horizonLabel), el("span", "", row.horizon));
    const up = probabilityCell(row.up_probability, "up");
    const down = probabilityCell(row.down_probability, "down");
    const signalClass = row.direction === "偏多" ? "bullish" : row.direction === "偏空" ? "bearish" : "neutral";
    const signal = el("span", `probability-signal ${signalClass}`, row.direction);
    const confidence = el("div", "probability-confidence");
    confidence.append(el("strong", "", row.confidence == null ? "--" : `${Number(row.confidence).toFixed(0)} 点`), el("span", "", `${row.story_count || 0} 条消息`));
    const drivers = el("div", "probability-drivers");
    (row.headlines || []).forEach((headline) => drivers.append(el("span", "", headline)));
    drivers.append(el("span", "", row.reason || "未校准研究信号"));
    probabilityRow.append(identity, up, down, signal, confidence, drivers);
    fragment.append(probabilityRow);
  });
  container.append(fragment);
}

function collectKoreaStockPriceSeries(data, definition) {
  return collectKoreaStockMovementHistory(data, definition, 14)
    .map((item) => [Number(item.detected_at || 0), Number(item.price || 0)])
    .filter((item) => Number.isFinite(item[1]));
}

function renderKoreaStockPrice(data, definition, movement, quoteData) {
  const container = document.getElementById(`kr-stock-price-${definition.id}`);
  container.replaceChildren();
  const watchItem = getKoreaWatchItem(data, definition);
  const premium = watchItem ? watchItem.adr_premium : null;
  const sourceUrl = quoteData?.source_url || `https://finance.yahoo.com/quote/${encodeURIComponent(definition.symbol)}/`;
  const item = quoteData || movement || {};
  const changePct = Number(item.change_pct || 0);
  const hasChange = item.change_pct != null && Number.isFinite(Number(item.change_pct));
  const hasQuote = Number.isFinite(Number(item.price)) && Number(item.price) > 0;
  const changeClass = !hasQuote ? "neutral" : changePct >= 0 ? "positive" : "negative";
  const quoteChart = (quoteData?.chart || []).filter((point) =>
    Array.isArray(point) && Number.isFinite(Number(point[1])),
  );
  const history = collectKoreaStockMovementHistory(data, definition, 14);
  const priceHistory = quoteChart.length
    ? quoteChart.map((record) => Number(record[1]))
    : history.map((record) => Number(record.price || 0)).filter(Number.isFinite);
  const historyHigh = Number(quoteData?.day_high) || (priceHistory.length ? Math.max(...priceHistory) : null);
  const historyLow = Number(quoteData?.day_low) || (priceHistory.length ? Math.min(...priceHistory) : null);
  const identity = el("div", "asset-identity");
  identity.append(
    el("span", "asset-key", definition.symbol.split(".")[0]),
    el("strong", "", definition.shortName),
    instrumentBriefNode(definition),
    el("small", "", `${definition.alias} · ${definition.symbol}`),
  );
  const header = el("div", "asset-card-header");
  const marketOpen = Boolean(data.korea_quotes?.market_open);
  const sourceLabel = quoteData?.status === "stale"
    ? "上一有效报价 ↗"
    : marketOpen ? "最新报价 ↗" : "最近收盘 ↗";
  const source = el("a", "asset-source-link", sourceLabel);
  source.href = sourceUrl;
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  source.title = `打开 ${definition.heading} 行情页；以页面显示的报价时间为准`;
  header.append(identity, source);

  const quote = el("div", "asset-quote-row");
  const current = el("div", "asset-current-price");
  current.append(
    el("strong", "", hasQuote ? formatPrice(item.price) : "--"),
    el("span", "", String(item.currency || "KRW")),
  );
  const change = el("div", `asset-change ${changeClass}`);
  change.append(
    el("strong", "", hasQuote ? hasChange ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%` : "--" : "正在连接"),
    el("span", "", "较前收盘"),
  );
  quote.append(current, change);
  const canvas = document.createElement("canvas");
  canvas.className = "asset-sparkline";
  canvas.width = 640;
  canvas.height = 92;
  canvas.setAttribute("aria-label", `${definition.shortName} 当日分钟价格轨迹`);
  const chartSeries = quoteChart.length ? quoteChart : collectKoreaStockPriceSeries(data, definition);
  window.requestAnimationFrame(() => drawAssetSparkline(canvas, chartSeries, changePct >= 0));
  const metrics = el("div", "asset-mini-metrics");
  metrics.append(
    koreaStockMetric("日内高点", historyHigh === null ? "--" : formatPrice(historyHigh)),
    koreaStockMetric("日内低点", historyLow === null ? "--" : formatPrice(historyLow)),
    koreaStockMetric("报价时间", hasQuote ? formatDate(item.quote_time || item.detected_at, true) : "正在连接"),
  );

  const card = el("article", `asset-price-card asset-${definition.id}`);
  card.append(header, quote, canvas, metrics, minuteChartButton({...definition, market:"KR", currency:"KRW"}));
  card.append(definition.id === "hynix" ? renderKoreaPremiumStrip(premium) : renderKoreaPrimaryStrip(definition));
  container.append(card);
}

function renderKoreaPrimaryStrip(definition) {
  const strip = el("div", "korea-premium-strip korea-primary-strip");
  const copy = el("div", "korea-premium-copy");
  copy.append(el("span", "", "KOREA PRIMARY QUOTE"), el("strong", "", "韩国原股基准"));
  const result = el("div", "korea-premium-result");
  result.append(el("strong", "neutral", "KRX 原股"), el("span", "", `${definition.symbol} · 无 ADR 溢价换算`));
  strip.append(copy, result);
  return strip;
}

function renderKoreaPremiumStrip(premium) {
  const strip = el("div", "korea-premium-strip");
  const copy = el("div", "korea-premium-copy");
  copy.append(el("span", "", "SKHY ADR PREMIUM"), el("strong", "", "海力士 ADR 溢价"));
  const available = Boolean(premium?.available) && premium?.premium_pct !== null && premium?.premium_pct !== undefined;
  const value = available ? Number(premium.premium_pct) : null;
  const result = el("div", "korea-premium-result");
  result.append(
    el("strong", value === null ? "neutral" : value >= 0 ? "positive" : "negative", value === null ? "等待报价" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`),
    el("span", "", value === null ? (premium?.error || "正在连接韩美行情") : `SKHY $${Number(premium.adr_price_usd || 0).toFixed(2)} · 韩股等值 $${Number(premium.local_equivalent_usd || 0).toFixed(2)}`),
  );
  strip.append(copy, result);
  strip.title = premium?.note || "韩美交易时段不同，溢价仅供参考";
  return strip;
}

function koreaStockMetric(label, value) {
  const metric = el("div");
  metric.append(el("span", "", label), el("strong", "", value));
  return metric;
}

function renderKoreaStockNews(definition, newsItems) {
  const container = document.getElementById(`kr-stock-news-${definition.id}`);
  const countElement = document.getElementById(`kr-stock-news-count-${definition.id}`);
  container.replaceChildren();
  if (!newsItems.length) {
    container.append(emptyState(`暂无${definition.shortName}最新新闻`, "等检测跑完并触发后会自动补齐"));
    if (countElement) countElement.textContent = "0 条";
    return;
  }
  const channelCount = new Set(
    newsItems.flatMap((item) => {
      const channels = item.channels || [];
      if (channels.length) return channels;
      return [item.source || "新闻来源"];
    }),
  ).size;
  countElement.textContent = `${newsItems.length} 条 · ${channelCount} 渠道`;
  const fragment = document.createDocumentFragment();
  newsItems
    .slice(0, 8)
    .forEach((item) => {
      const row = el("article", "asset-news-row");
      const badge = el("span", `asset-news-badge asset-news-${definition.id}`, definition.label);
      const direction = normalizeNewsScore(item.score);
      const directionText = direction > 0 ? "利多" : direction < 0 ? "利空" : "中性";
      const meta = el("div", "asset-news-meta");
      meta.append(
        badge,
        el("span", "event-badge", `${eventLabels[item.event_type] || "市场消息"} · ${directionText}`),
        marketBadge(item),
        el("span", "asset-news-provenance", String(item.source || "新闻来源")),
      );
      if (Number(item.source_count || 1) > 1) {
        meta.append(el("span", "source-confirmation-badge", `${Number(item.source_count)} 源确认`));
      }
      const time = el("time", "news-time", formatDate(item.published_at, true));
      meta.append(time);
      const title = el("h3", "", newsTitle(item));
      appendNewsTranslation(title, item);
      const summary = el("p", "", String(item.summary || ""));
      appendNewsTranslation(summary, item, "summary");
      const impact = el("article", "asset-news-impact");
      impact.append(
        el("strong", `score-value ${confidenceClass(item.score)}`, formatScore(item.score)),
        el("time", "", `发布时间：${formatDate(item.published_at, true)}`),
      );
      const openLink = item.url && /^https?:\/\//i.test(String(item.url))
        ? el("a", "asset-news-link", "查看原文 ↗")
        : null;
      if (openLink) {
        openLink.href = String(item.url);
        openLink.target = "_blank";
        openLink.rel = "noopener noreferrer";
      }
      const copy = el("div", "asset-news-main");
      copy.append(meta, instrumentBriefNode(definition), title, summary, impact);
      if (openLink) copy.append(openLink);
      row.append(copy);
      row.addEventListener("click", () => openDetail(item));
      row.tabIndex = 0;
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openDetail(item);
      });
      fragment.append(row);
    });
  container.append(fragment);
}

function renderKoreaStockOutlook(definition, outlook) {
  const container = document.getElementById(`kr-stock-outlook-${definition.id}`);
  container.replaceChildren();
  if (!outlook || typeof outlook !== "object") {
    container.append(emptyState("暂无可用预测", "请先触发一次标的扫描"));
    return;
  }

  const summary = outlook.summary || {};
  const forecast = outlook.forecast || {};
  const summarySection = el("article", "asset-outlook-section");
  const summaryHeader = el("div", "asset-outlook-header");
  const summaryTitle = el("div", "asset-outlook-title");
  summaryTitle.append(el("span", "", "LAST 24H"), el("strong", "", "过去24小时新闻总结"));
  const summaryTone = summary.tone === "偏多" ? "positive" : summary.tone === "偏空" ? "negative" : "neutral";
  summaryHeader.append(summaryTitle, el("span", `asset-outlook-state ${summaryTone}`, summary.tone || "中性"));
  summarySection.append(summaryHeader, el("p", "asset-outlook-text", summary.text || "正在汇总过去24小时消息"));

  const summaryMetrics = el("div", "asset-outlook-metrics");
  summaryMetrics.append(
    el("span", "", `${Number(summary.story_count || 0)} 条消息`),
    el("span", "positive", `利多 ${Number(summary.bullish_count || 0)}`),
    el("span", "negative", `利空 ${Number(summary.bearish_count || 0)}`),
    el("span", "", `${Number(summary.channel_count || 0)} 渠道`),
  );
  summarySection.append(summaryMetrics);

  const events = el("div", "asset-outlook-events");
  (summary.key_events || []).forEach((event) => {
    if (!event.url) return;
    const link = el("a", "asset-outlook-event", `${newsTitle(event)} ↗`);
    appendNewsTranslation(link, event);
    link.href = String(event.url);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = `打开 ${event.source || "新闻来源"} 原文`;
    events.append(link);
  });
  summarySection.append(events);

  const forecastSection = el("article", "asset-outlook-section");
  const forecastHeader = el("div", "asset-outlook-header");
  const forecastTitle = el("div", "asset-outlook-title");
  forecastTitle.append(el("span", "", "NEXT 24H"), el("strong", "", "未来24小时预测"));
  const forecastTone = forecast.tone === "偏多" ? "positive" : forecast.tone === "偏空" ? "negative" : "neutral";
  forecastHeader.append(forecastTitle, el("span", `asset-outlook-state ${forecastTone}`, forecast.tone || "震荡"));
  forecastSection.append(forecastHeader, el("p", "asset-outlook-text", forecast.text || "正在生成未来24小时研究信号"));

  if (forecast.available !== true) {
    container.append(summarySection, forecastSection);
    return;
  }

  const forecastMetrics = el("div", "asset-outlook-metrics");
  forecastMetrics.append(
    el("span", "positive", `多向 ${Number(forecast.up_probability).toFixed(1)} 点`),
    el("span", "negative", `空向 ${Number(forecast.down_probability).toFixed(1)} 点`),
    el("span", "", `信号强度 ${Number(forecast.confidence || 0).toFixed(0)} 点`),
    el("span", "", `波动 ±${Number(forecast.expected_move_pct || 0).toFixed(2)}%`),
  );
  const low = Number.isFinite(Number(forecast.expected_low)) && Number(forecast.expected_low) > 0
    ? formatPrice(Number(forecast.expected_low))
    : "--";
  const high = Number.isFinite(Number(forecast.expected_high)) && Number(forecast.expected_high) > 0
    ? formatPrice(Number(forecast.expected_high))
    : "--";
  const rangeText = low === "--" || high === "--" ? "--" : `${low}–${high}`;
  const riskText = `基准区间 ${rangeText} ${forecast.currency || "KRW"} · ${forecast.risk_text || ""}`;
  const range = el("div", "asset-outlook-risk", riskText);
  forecastSection.append(forecastMetrics, range);
  container.append(summarySection, forecastSection);
}

function renderKoreaStocks(data) {
  const allNews = KOREA_STOCK_DEFINITIONS.flatMap((definition) => collectKoreaStockNews(data, definition));
  const quoteSnapshot = data.korea_quotes || {};
  const quoteItems = quoteSnapshot.quotes || [];
  const latestQuoteAt = quoteItems
    .map((item) => Date.parse(String(item.quote_time || "")))
    .filter(Number.isFinite)
    .sort((left, right) => right - left)[0];
  const latestNewsAt = allNews
    .map((item) => Date.parse(String(item.published_at || "")))
    .filter(Number.isFinite)
    .sort((left, right) => right - left)[0];
  const channelCount = new Set(allNews.flatMap((item) => item.channels || (item.source ? [item.source] : []))).size;
  const caption = document.getElementById("korea-stock-update-caption");
  const liveDot = document.getElementById("korea-stock-live-dot");
  const liveText = document.getElementById("korea-stock-live-text");
  const newsSync = document.getElementById("korea-stock-news-sync");
  const marketOpen = Boolean(quoteSnapshot.market_open);
  const refreshSeconds = Number(quoteSnapshot.interval_seconds || 5);
  if (caption) caption.textContent = latestQuoteAt
    ? `三星 / 海力士 · 交易时段每 ${refreshSeconds} 秒更新 · 报价于 ${formatDate(latestQuoteAt, true)}`
    : "正在连接三星电子（005930）与 SK 海力士（000660）行情";
  if (liveDot) liveDot.className = quoteSnapshot.restored_from_cache || quoteSnapshot.status === "stale" || quoteSnapshot.status === "error" || quoteItems.length < 2 ? "warning" : marketOpen ? "" : "closed";
  if (liveText) liveText.textContent = quoteSnapshot.restored_from_cache || quoteSnapshot.status === "stale"
    ? "上次行情快照 · 待确认" : quoteItems.length < 2
    ? "行情连接中"
    : marketOpen ? "两只股票行情监控中" : "韩国市场已收盘";
  if (newsSync) newsSync.textContent = latestNewsAt
    ? `新闻同步 ${formatDate(latestNewsAt, true)} · ${channelCount} 渠道`
    : "新闻等待同步";
  KOREA_STOCK_DEFINITIONS.forEach((definition) => {
    const movement = collectKoreaStockMovement(data, definition);
    const quote = collectKoreaStockQuote(data, definition);
    const newsItems = collectKoreaStockNews(data, definition);
    const probabilities = calculateKoreaProbability(data, definition);
    const outlook = buildKoreaStockOutlook(definition, data, movement, quote, probabilities);
    renderKoreaStockPrice(data, definition, movement, quote);
    renderKoreaStockProbability(definition, probabilities);
    renderKoreaStockNews(definition, newsItems);
    renderKoreaStockOutlook(definition, outlook);
  });
}

function refreshKoreaStocks({ silent = false } = {}) {
  return refreshDashboard({ silent })
    .then((data) => {
      if (data && !silent) showToast("三星/海力士监控已更新", "success");
      return data;
    });
}

function renderAdrPremium(premium) {
  const cell = el("div", "watch-premium");
  if (!premium) {
    cell.append(el("strong", "", "--"), el("span", "", "仅海力士计算"));
    return cell;
  }
  if (!premium.available) {
    cell.append(el("strong", "", "等待报价"), el("span", "", premium.error || "正在连接 SKHY 与韩股行情"));
    return cell;
  }
  const value = Number(premium.premium_pct || 0);
  const label = value >= 0 ? "溢价" : "折价";
  const stateClass = value >= 0 ? "premium-positive" : "premium-negative";
  cell.append(
    el("strong", stateClass, `${value >= 0 ? "+" : ""}${value.toFixed(2)}% ${label}`),
    el("span", "", `SKHY $${Number(premium.adr_price_usd).toFixed(2)} · 韩股等值 $${Number(premium.local_equivalent_usd).toFixed(2)}`),
    el("small", "", `₩${Number(premium.local_price_krw).toLocaleString("zh-CN")} · USD/KRW ${Number(premium.krw_per_usd).toFixed(2)} · 10 ADR = 1 股`),
  );
  cell.title = premium.note || "韩美交易时段不同，溢价仅供参考";
  return cell;
}

function renderRuntime(data) {
  const runtime = data.runtime || {};
  const lastRun = runtime.last_run;
  const dot = document.getElementById("runtime-indicator");
  const sideDot = document.getElementById("sidebar-status-dot");
  dot.className = "runtime-indicator";
  sideDot.className = "status-dot";
  if (data._startup_cache) {
    dot.classList.add("warning");
    sideDot.classList.add("warning");
    document.getElementById("runtime-title").textContent = "上次监测快照";
    document.getElementById("runtime-copy").textContent = "后台正在确认当前运行状态";
    document.getElementById("sidebar-status").textContent = "已恢复快照";
    document.getElementById("sidebar-status-detail").textContent = "等待本次同步";
  } else if (runtime.scan_in_progress || runtime.movement_scan_in_progress) {
    dot.classList.add("running");
    sideDot.classList.add("running");
    document.getElementById("runtime-title").textContent = "正在检测";
    document.getElementById("runtime-copy").textContent = runtime.movement_scan_in_progress
      ? "正在检测全部持仓股与韩国核心股异动，并匹配公开消息"
      : "正在收集并分析最新消息";
    document.getElementById("sidebar-status").textContent = "检测进行中";
    document.getElementById("sidebar-status-detail").textContent = "请稍候";
  } else if ((lastRun && lastRun.errors && lastRun.errors.length) || (runtime.last_movement_run?.errors || []).length) {
    const errors = [...(lastRun?.errors || []), ...(runtime.last_movement_run?.errors || [])];
    dot.classList.add("warning");
    sideDot.classList.add("warning");
    document.getElementById("runtime-title").textContent = "检测完成，有警告";
    document.getElementById("runtime-copy").textContent = `${errors.length} 项来源异常，已保留有效数据；详情见数据状态。`;
    document.getElementById("sidebar-status").textContent = "数据源警告";
    document.getElementById("sidebar-status-detail").textContent = `${errors.length} 项`;
  } else if (lastRun) {
    document.getElementById("runtime-title").textContent = "最近检测正常";
    document.getElementById("runtime-copy").textContent = `抓取 ${lastRun.fetched} · 新增 ${lastRun.inserted} · 告警 ${lastRun.alert_count}`;
    document.getElementById("sidebar-status").textContent = "系统正常";
    document.getElementById("sidebar-status-detail").textContent = formatDate(lastRun.finished_at, true);
  } else {
    document.getElementById("runtime-title").textContent = "监测器就绪";
    document.getElementById("runtime-copy").textContent = "等待第一次检测";
  }
  updateScanButton(Boolean(runtime.scan_in_progress));
  updateMovementButton(Boolean(runtime.movement_scan_in_progress));
}

function renderMovements(data) {
  const extended=data.extended_monitor || {rows:[],eligible_count:0,triggered_count:0,threshold_pct:2};
  const extendedRows=extended.rows || [];
  const sessionCounts={pre:0,regular:0,post:0,closed:0,unknown:0};
  extendedRows.forEach(row=>{sessionCounts[row.market_session]=(sessionCounts[row.market_session] || 0)+1;});
  document.getElementById("us-extended-status").textContent=extendedRows.length
    ? `美股 ${extendedRows.length} 只 · 盘前 ${sessionCounts.pre} / 常规 ${sessionCounts.regular} / 盘后 ${sessionCounts.post} / 休市 ${sessionCounts.closed} / 时段待确认 ${sessionCounts.unknown} · 盘前盘后有效报价 ${extended.eligible_count} · 触发 ${extended.triggered_count} · 阈值 ±${extended.threshold_pct}%（对常规收盘）`
    : "美股盘前盘后：暂无美股持仓";
  const stats = data.movement_stats || {};
  const scope = data.movement_scope || {};
  const scopeMarkets = new Set(scope.markets || []);
  if (state.movementMarket !== "all" && !scopeMarkets.has(state.movementMarket)) {
    state.movementMarket = "all";
  }
  document.querySelectorAll("#movement-market-filter button").forEach((button) => {
    const market = button.dataset.value;
    button.hidden = market !== "all" && !scopeMarkets.has(market);
    button.classList.toggle("active", market === state.movementMarket);
  });
  document.getElementById("movement-scope-note").textContent = scope.mode === "all" ? "A股 / 港股 / 美股公开异动扫描 · 韩国仅三星与海力士 · 有流动性门槛，非逐笔全量行情" : scope.count
    ? `监测 ${scope.count} 只股票 · ${scope.portfolio_count || 0} 只持仓 · 固定包含三星电子与 SK 海力士`
    : "固定监测三星电子与 SK 海力士";
  document.getElementById("movement-count").textContent = scope.mode === "all" ? "全市场" : stats.monitored_count ?? scope.count ?? 0;
  document.getElementById("movement-up").textContent = stats.up || 0;
  document.getElementById("movement-down").textContent = stats.down || 0;
  document.getElementById("movement-evidence").textContent = stats.direct_evidence || 0;
  const runtime = data.runtime || {};
  const finishedAt = runtime.last_movement_run?.finished_at || stats.scan_id;
  const staleMarkets = (stats.stale_markets || []).map((market) => marketLabels[market] || market);
  document.getElementById("movement-scan-time").textContent = finishedAt
    ? `更新于 ${formatDate(finishedAt, true)}${staleMarkets.length ? ` · ${staleMarkets.join("、")}沿用上次` : ""}`
    : "尚未扫描";
  applyMovementFilters();
}

function movementChangePercent(item) {
  const value = item.change_pct;
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function updateMovementSortControls() {
  const ascending = state.movementSortDirection === "asc";
  document.querySelectorAll("[data-movement-sort]").forEach(button => {
    button.replaceChildren(el("span", "", "时段涨跌"), uiIcon(ascending ? "arrow-up" : "arrow-down"));
    button.dataset.direction = ascending ? "asc" : "desc";
    button.title = `当前${ascending ? "升序（从低到高）" : "降序（从高到低）"}，点击切换为${ascending ? "降序" : "升序"}`;
    button.setAttribute("aria-label", `时段涨跌：${button.title}`);
  });
}

function bindMovementSort() {
  document.querySelectorAll("[data-movement-sort]").forEach(button => button.addEventListener("click", () => {
    state.movementSortDirection = state.movementSortDirection === "asc" ? "desc" : "asc";
    applyMovementFilters();
    if (typeof saveStartupPreferences === "function") saveStartupPreferences();
  }));
  updateMovementSortControls();
}

function applyMovementFilters() {
  updateMovementSortControls();
  if (!state.dashboard) return;
  const items = (state.dashboard.movements || []).filter((item) => state.movementMarket === "all" || item.market === state.movementMarket);
  const direction = state.movementSortDirection === "asc" ? 1 : -1;
  items.sort((a, b) => {
    const left = movementChangePercent(a), right = movementChangePercent(b);
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    return (left - right) * direction;
  });
  document.getElementById("movement-result-count").textContent = `${items.length} 个异动`;
  renderMovementTable(items);
}

function renderMovementTable(items) {
  const container = document.getElementById("movement-table");
  container.replaceChildren();
  if (!items.length) {
    container.append(emptyState("当前未发现持仓异动", "系统仍在监测全部股票持仓以及三星、海力士"));
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const isHongKong = item.market === "HK";
    const primaryLabel = isHongKong ? String(item.name || "").trim() || item.symbol : item.symbol;
    const row = el("article", "movement-row");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", isHongKong ? `查看 ${primaryLabel}（${item.symbol}）异动原因` : `查看 ${item.symbol} 异动原因`);

    const asset = el("div", "movement-asset");
    const assetTop = el("div");
    const primary = el("strong", isHongKong ? "movement-symbol movement-company-name" : "movement-symbol", primaryLabel);
    primary.title = primaryLabel;
    assetTop.append(primary);
    const metadata = el("div", "movement-meta");
    const secondary = el("span", "movement-name", isHongKong ? item.symbol : item.name);
    secondary.title = secondary.textContent;
    metadata.append(
      secondary,
      el("span", "market-badge", marketLabels[item.market] || item.market),
      el("span", `movement-source-badge ${item.monitor_source || "portfolio"}`, item.monitor_source_label || "持仓"),
    );
    asset.append(assetTop, metadata, instrumentBriefNode(item));
    if(item.market === "US")asset.append(el("small","",item.session_label || `常规扫描 · ${formatDate(item.detected_at,true)}`));

    const changePct = movementChangePercent(item);
    const change = el("strong", `movement-change ${changePct === null ? "neutral" : changePct >= 0 ? "positive" : "negative"}`,
      changePct === null ? "--" : `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`);
    const relative = el("div", "movement-relative");
    relative.append(el("strong", "", item.relative_volume == null ? "--" : `${Number(item.relative_volume).toFixed(1)}x`), el("span", "", item.live_extended ? "暂无时段量比" : "10日均量"));

    const intensity = el("div", "intensity-cell");
    intensity.append(el("strong", "", Number(item.intensity_score || 0).toFixed(0)));
    const track = el("div", "intensity-track");
    const fill = el("span");
    fill.style.width = `${Math.max(4, Math.min(100, Number(item.intensity_score || 0)))}%`;
    track.append(fill);
    intensity.append(track);

    const cause = el("div", "movement-cause");
    const causeTop = el("div");
    causeTop.append(
      el("span", `cause-category ${item.reason_category === "unconfirmed" ? "unconfirmed" : ""}`, eventLabels[item.reason_category] || "公开消息"),
      el("span", `confidence-badge confidence-${confidenceClass(item.confidence)}`, item.live_extended ? "原因待确认" : `${confidenceLabel(item.confidence)} ${Math.round(Number(item.confidence || 0) * 100)}%`),
    );
    if (Number(item.trump_factor?.post_count || 0) > 0) {
      const badge = trumpBadge(item.trump_factor);
      badge.title = item.trump_factor.reason || "Trump 动态因子";
      causeTop.append(badge);
    }
    cause.append(causeTop, el("p", "", item.reason_summary));

    const price=el("div","movement-price");price.append(el("strong","",formatPrice(item.price)));
    if(item.quote_time)price.append(el("small","",formatDate(item.quote_time,true)));
    row.append(asset, price, change, relative, intensity, cause);
    row.addEventListener("click", () => openMovementDetail(item));
    row.addEventListener("keydown", (eventKey) => {
      if (eventKey.key === "Enter" || eventKey.key === " ") openMovementDetail(item);
    });
    fragment.append(row);
  });
  container.append(fragment);
}


async function toggleAssetAlerts(enabled) {
  const toggle = document.getElementById("asset-alert-toggle");
  try {
    const result = await api("/api/assets/notifications", {
      method: "POST",
      body: { enabled },
    });
    state.assetAlertsEnabled = Boolean(result.enabled);
    toggle.checked = state.assetAlertsEnabled;
  } catch (error) {
    toggle.checked = !enabled;
    showToast(error.message, "error");
  }
}

async function testAssetAlerts() {
  const button = document.getElementById("asset-alert-test-button");
  const original = button.textContent;
  button.disabled = true;
  try {
    await api("/api/assets/alert-test", { method: "POST", body: {} });
    button.textContent = "✓";
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    window.setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1200);
  }
}

function renderAssets(data) {
  if (state.assets === data) return;
  state.assets = data;
  const runtime = data.runtime || {};
  const errors = runtime.errors || [];
  const updating = Boolean(runtime.refresh_in_progress);
  const caption = document.getElementById("asset-update-caption");
  caption.textContent = runtime.last_updated_at
    ? `黄金/BTC ${intervalLabel(runtime.price_interval_seconds)} · 原油/美债 ${intervalLabel(runtime.oil_price_interval_seconds, 60)} · RSS ${intervalLabel(runtime.news_interval_seconds, 15)} · 搜索 ${intervalLabel(runtime.search_interval_seconds,60)} · 更新于 ${formatDate(runtime.last_updated_at, true)}`
    : updating ? "正在连接行情" : "等待首次行情";
  const liveDot = document.getElementById("asset-live-dot");
  liveDot.className = runtime.restored_from_cache ? "warning" : updating ? "running" : errors.length ? "warning" : "";
  document.getElementById("asset-live-text").textContent = updating
    ? "正在更新"
    : runtime.restored_from_cache ? "上次行情快照 · 待确认" : errors.length ? `${errors.length} 项数据警告` : "实时监控中";
  document.getElementById("asset-news-sync").textContent = runtime.news_updated_at
    ? `新闻同步 ${formatDate(runtime.news_updated_at, true)} · ${Number(runtime.news_channel_count || 0)} 渠道`
    : "新闻等待同步";
  state.assetAlertsEnabled = Boolean(runtime.system_notifications_enabled);
  const alertToggle = document.getElementById("asset-alert-toggle");
  alertToggle.checked = state.assetAlertsEnabled;
  alertToggle.disabled = !runtime.system_notifications_available;
  document.getElementById("asset-alert-test-button").disabled = !runtime.system_notifications_available;
  document.getElementById("probability-method").textContent = `${runtime.probability_method || "多因子研究信号"}；不代表真实命中率，也不构成投资建议。`;
  updateAssetRefreshButton(updating);
  renderAssetPriceCards(data.assets || [], runtime);
  renderProbabilityTable(data.probabilities || []);
  renderOverviewFreshness();
  renderAssetOutlooks(data.outlooks || []);
  renderAssetNewsColumns(data.news || []);
}

function renderAssetPriceCards(items, runtime = {}) {
  const containers = {
    GOLD: document.getElementById("asset-price-gold"),
    BTC: document.getElementById("asset-price-btc"),
    OIL: document.getElementById("asset-price-oil"),
    US5Y: document.getElementById("asset-price-us5y"),
  };
  Object.values(containers).forEach((container) => container.replaceChildren());
  items.forEach((item) => {
    const container = containers[item.key];
    if (!container) return;
    const card = el("article", `asset-price-card asset-${item.key.toLowerCase()}`);
    const header = el("div", "asset-card-header");
    const identity = el("div", "asset-identity");
    identity.append(el("span", "asset-key", item.key === "OIL" ? "WTI" : item.key === "US5Y" ? "US 5Y" : item.key), el("strong", "", item.name), el("small", "", `${item.subtitle} · ${item.symbol}`));
    identity.append(instrumentBriefNode({asset:item.key}));
    const quoteAgeLimit = ["OIL", "US5Y"].includes(item.key) ? 1800 : 180;
    const quoteState = item.quote_status === "closed" ? "休市 / 延迟报价 ↗" : item.quote_status === "stale" || Number(item.age_seconds || 0) > quoteAgeLimit
      ? "上一有效报价 ↗"
      : item.delayed ? "延迟报价 ↗" : item.key === "GOLD" ? "即时现货 ↗" : "实时成交 ↗";
    const source = el("a", "asset-source-link", quoteState);
    source.href = item.source_url;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.title = item.key === "GOLD"
      ? `打开 ${item.source}；XAU/USD 为即时现货，技术走势图使用 COMEX 分钟线校准`
      : `打开 ${item.source}`;
    header.append(identity, source);

    const quote = el("div", "asset-quote-row");
    const price = el("div", "asset-current-price");
    price.append(el("strong", "", formatAssetPrice(item)), el("span", "", item.unit || item.currency || "USD"));
    const yieldQuote = item.key === "US5Y";
    const changeValue = yieldQuote ? item.change_bps : item.change_24h;
    const change = el("div", `asset-change ${changeValue == null ? "neutral" : Number(changeValue) >= 0 ? "positive" : "negative"}`);
    const changeText = yieldQuote ? (changeValue == null ? "--" : `${Number(changeValue) >= 0 ? "+" : ""}${Number(changeValue).toFixed(2)} bp`) : signedPercent(changeValue);
    change.append(el("strong", "", changeText), el("span", "", yieldQuote ? "较前收 · 收益率变化" : "近24小时"));
    quote.append(price, change);

    const canvas = document.createElement("canvas");
    canvas.className = "asset-sparkline";
    canvas.width = 640;
    canvas.height = 92;
    canvas.setAttribute("aria-label", `${item.name} 近八小时价格走势`);

    const metrics = el("div", "asset-mini-metrics");
    metrics.append(
      assetMetric("24h 高点", formatAssetPrice({ ...item, price: item.high_24h })),
      assetMetric("24h 低点", formatAssetPrice({ ...item, price: item.low_24h })),
      assetMetric("报价时间", formatDate(item.price_time, true)),
    );
    const chartContexts = {
      GOLD: ["黄金期货 · COMEX", "COMEX 黄金期货参考 K 线，不是上方的 XAU/USD 现货报价。"],
      BTC: ["Bitcoin · BTC/USD", "BTC/USD 现货；分钟 K 来自 Coinbase，日 / 周 K 来自 Yahoo 公开参考。"],
      OIL: ["WTI 原油期货", "WTI 连续期货参考 K 线，具体交割月份以来源为准。"],
      US5Y: ["五年期美债收益率", "Cboe 五年期收益率指标，单位 %；不是债券价格。"],
    };
    const chartContext = chartContexts[item.key];
    card.append(header, quote, canvas, metrics);
    if (chartContext) card.append(minuteChartButton({market:"ASSET", symbol:item.key, name:chartContext[0], currency:item.key === "US5Y" ? "%" : "USD", chartBrief:chartContext[1]}));
    if (item.quote_note) {
      card.append(el("p", "asset-quote-note", `${item.contract_name || ""} · ${item.quote_note}`));
    }
    container.append(card);
    window.requestAnimationFrame(() => drawAssetSparkline(canvas, item.chart || [], Number(item.change_24h) >= 0));
  });
  Object.entries(containers).forEach(([key, container]) => {
    if (!container.children.length) {
      const failed = (runtime.slow_errors?.[key] || []).length;
      const description = failed ? "报价源暂不可用，将按分钟自动重试；新闻独立更新" : "首次连接通常需要几秒钟";
      container.append(emptyState(`${failed ? "暂未获取" : "正在获取"}${ASSET_NAMES[key]}行情`, description));
    }
  });
}

function minuteChartButton(item) {
  const button = el("button", "asset-chart-button");
  button.append(uiIcon("chart-no-axes-combined"), el("span", "", "查看分钟 K"));
  button.type = "button";
  button.title = `${item.name || item.shortName} · 1 分钟 K 线`;
  button.addEventListener("click", () => openPortfolioChart({...item, name:item.name || item.shortName}, "intraday"));
  return button;
}

function assetMetric(label, value) {
  const metric = el("div");
  metric.append(el("span", "", label), el("strong", "", value));
  return metric;
}

function drawAssetSparkline(canvas, points, positive) {
  const context = canvas.getContext("2d");
  if (!context || points.length < 2) return;
  const values = points.map((point) => Number(point[1])).filter(Number.isFinite);
  if (values.length < 2) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(0.0001, max - min);
  const padding = 7;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#dfe4e6";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(padding, canvas.height - padding);
  context.lineTo(canvas.width - padding, canvas.height - padding);
  context.stroke();
  context.strokeStyle = positive ? "#0c8a62" : "#c94b55";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.beginPath();
  values.forEach((value, index) => {
    const x = padding + index / (values.length - 1) * (canvas.width - padding * 2);
    const y = padding + (max - value) / span * (canvas.height - padding * 2);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.stroke();
}

function renderProbabilityTable(items) {
  const containers = {
    GOLD: document.getElementById("probability-gold"),
    BTC: document.getElementById("probability-btc"),
    OIL: document.getElementById("probability-oil"),
    US5Y: document.getElementById("probability-us5y"),
  };
  Object.values(containers).forEach((container) => container.replaceChildren());
  items.forEach((item) => {
    const container = containers[item.asset];
    if (!container) return;
    const row = el("div", "probability-row");
    const identity = el("div", "probability-identity");
    identity.append(el("strong", "", item.horizon_label), el("span", "", item.horizon));
    if (item.available === false || item.up_probability == null || item.down_probability == null) {
      row.append(identity, el("span", "", "--"), el("span", "", "--"), el("span", "probability-signal neutral", "待确认"));
      row.append(el("div", "probability-unavailable", (item.drivers || []).join("；") || "行情不足，暂不提供研究信号"));
      container.append(row);
      return;
    }
    const up = probabilityCell(item.up_probability, "up");
    const down = probabilityCell(item.down_probability, "down");
    const signalClass = ["偏多", "上行"].includes(item.signal) ? "bullish" : ["偏空", "下行"].includes(item.signal) ? "bearish" : "neutral";
    const signal = el("span", `probability-signal ${signalClass}`, item.signal);
    const confidence = el("div", "probability-confidence");
    confidence.append(el("strong", "", `${Number(item.confidence).toFixed(0)} 点`), el("span", "", `变化 ${Number(item.probability_change) >= 0 ? "+" : ""}${Number(item.probability_change).toFixed(1)}`));
    const drivers = el("div", "probability-drivers");
    (item.drivers || []).forEach((driver) => drivers.append(el("span", "", driver)));
    if (item.basis_note) drivers.append(el("span", "probability-basis", `${item.basis_note} · 报价 ${formatDate(item.quote_time, true)}`));
    row.append(identity, up, down, signal, confidence, drivers);
    container.append(row);
  });
  Object.values(containers).forEach((container) => {
    if (!container.children.length) {
      container.append(emptyState("正在计算研究信号", "行情与新闻到齐后会自动生成"));
    }
  });
}

function probabilityCell(value, direction) {
  const cell = el("div", `probability-cell probability-${direction}`);
  const available = value !== null && value !== undefined && Number.isFinite(Number(value));
  cell.append(el("strong", "", available ? `${Number(value).toFixed(1)} 点` : "--"));
  const track = el("div", "probability-track");
  const fill = el("span");
  fill.style.width = `${available ? clamp(Number(value), 0, 100) : 0}%`;
  track.append(fill);
  cell.append(track);
  return cell;
}

function renderAssetOutlooks(items) {
  const containers = {
    GOLD: document.getElementById("asset-outlook-gold"),
    BTC: document.getElementById("asset-outlook-btc"),
    OIL: document.getElementById("asset-outlook-oil"),
    US5Y: document.getElementById("asset-outlook-us5y"),
  };
  Object.values(containers).forEach((container) => container.replaceChildren());
  items.forEach((item) => {
    const container = containers[item.asset];
    if (!container) return;
    const summary = item.news_summary || {};
    const forecast = item.forecast || {};

    const summarySection = el("article", "asset-outlook-section");
    const summaryHeader = el("div", "asset-outlook-header");
    const summaryTitle = el("div", "asset-outlook-title");
    summaryTitle.append(el("span", "", "LAST 24H"), el("strong", "", "过去24小时新闻总结"));
    const toneClass = summary.tone === "偏多" ? "positive" : summary.tone === "偏空" ? "negative" : "neutral";
    summaryHeader.append(summaryTitle, el("span", `asset-outlook-state ${toneClass}`, summary.tone || "中性"));
    summarySection.append(summaryHeader, el("p", "asset-outlook-text", summary.text || "正在汇总过去24小时消息"));
    const summaryMetrics = el("div", "asset-outlook-metrics");
    summaryMetrics.append(
      el("span", "", `${Number(summary.story_count || 0)} 条消息`),
      el("span", "positive", `利多 ${Number(summary.bullish_count || 0)}`),
      el("span", "negative", `利空 ${Number(summary.bearish_count || 0)}`),
      el("span", "", `${Number(summary.channel_count || 0)} 渠道`),
    );
    summarySection.append(summaryMetrics);
    const events = el("div", "asset-outlook-events");
    (summary.key_events || []).slice(0, 2).forEach((event) => {
      const link = el("a", "asset-outlook-event", `${newsTitle(event)} ↗`);
      appendNewsTranslation(link, event);
      link.href = event.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = `打开 ${event.source || "新闻来源"} 原文`;
      events.append(link);
    });
    summarySection.append(events);

    const forecastSection = el("article", "asset-outlook-section");
    const forecastHeader = el("div", "asset-outlook-header");
    const forecastTitle = el("div", "asset-outlook-title");
    forecastTitle.append(el("span", "", "NEXT 24H"), el("strong", "", "未来24小时预测"));
    const signalClass = ["偏多", "上行"].includes(forecast.signal) ? "positive" : ["偏空", "下行"].includes(forecast.signal) ? "negative" : "neutral";
    forecastHeader.append(forecastTitle, el("span", `asset-outlook-state ${signalClass}`, forecast.signal || "震荡"));
    forecastSection.append(forecastHeader, el("p", "asset-outlook-text", forecast.text || "正在生成未来24小时研究信号"));
    if (forecast.basis_note) forecastSection.append(el("p", "asset-quote-note", forecast.basis_note));
    if (forecast.available === false) {
      container.append(summarySection, forecastSection);
      return;
    }
    const forecastMetrics = el("div", "asset-outlook-metrics");
    forecastMetrics.append(
      el("span", "positive", `${item.asset === "US5Y" ? "收益率上行" : "多向"} ${Number(forecast.up_probability ?? 50).toFixed(1)} 点`),
      el("span", "negative", `${item.asset === "US5Y" ? "收益率下行" : "空向"} ${Number(forecast.down_probability ?? 50).toFixed(1)} 点`),
      el("span", "", `信号强度 ${Number(forecast.confidence || 0).toFixed(0)} 点`),
      el("span", "", item.asset === "US5Y" ? `波动 ±${Number(forecast.expected_move_bps || 0).toFixed(2)} bp` : `波动 ±${Number(forecast.expected_move_pct || 0).toFixed(2)}%`),
    );
    const range = el(
      "div",
      "asset-outlook-risk",
      `基准区间 ${formatAssetPrice({ key: item.asset, price: forecast.expected_low })}–${formatAssetPrice({ key: item.asset, price: forecast.expected_high })} ${forecast.currency || "USD"} · ${forecast.risk_text || ""}`,
    );
    forecastSection.append(forecastMetrics, range);
    container.append(summarySection, forecastSection);
  });
  Object.values(containers).forEach((container) => {
    if (!container.children.length) {
      container.append(emptyState("正在生成24小时展望", "行情与新闻到齐后会自动更新"));
    }
  });
}

function renderAssetNewsColumns(items) {
  Object.keys(ASSET_NAMES).forEach((asset) => {
    const assetItems = items.filter((item) => item.asset === asset);
    const channelCount = new Set(assetItems.flatMap((item) => item.channels || [])).size;
    document.getElementById(`asset-news-count-${asset.toLowerCase()}`).textContent = `${assetItems.length} 条 · ${channelCount} 渠道`;
    renderAssetNews(document.getElementById(`asset-news-${asset.toLowerCase()}`), assetItems);
  });
}

function renderAssetNews(container, items) {
  container.replaceChildren();
  if (!items.length) {
    container.append(emptyState("暂无可验证的关联新闻", "新闻服务会按高频周期自动重试"));
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const row = el("article", "asset-news-row");
    const badge = el("span", `asset-news-badge asset-news-${item.asset.toLowerCase()}`, ASSET_NAMES[item.asset] || item.asset);
    const main = el("div", "asset-news-main");
    const meta = el("div", "asset-news-meta");
    const channelLabel = Number(item.channel_count || 1) > 1
      ? `${Number(item.channel_count)} 渠道`
      : (item.channels || [])[0] || "直接来源";
    const provenance = el("span", "asset-news-provenance", `${item.source || "新闻来源"} · ${channelLabel}`);
    provenance.title = (item.channels || []).join("、");
    meta.append(badge, el("span", "event-badge", assetEventLabels[item.event_type] || "市场消息"), provenance);
    if (item.relevance_reason==="宏观间接关联") meta.append(el("span","event-badge","宏观间接关联"));
    if (Number(item.source_count || 1) > 1) {
      meta.append(el("span", "source-confirmation-badge", `${Number(item.source_count)} 源确认`));
    }
    const headline = el("h3", "", newsTitle(item));
    appendNewsTranslation(headline, item);
    headline.title = item.title || "";
    main.append(meta, instrumentBriefNode({asset:item.asset}), headline, appendNewsTranslation(el("p", "", item.summary || ""), item, "summary"));
    const impact = el("div", "asset-news-impact");
    const impactClass = item.direction === "bullish" ? "positive" : item.direction === "bearish" ? "negative" : "neutral";
    impact.append(el("strong", impactClass, item.probability_eligible===false ? "观察 · 方向待确认" : `${scoreLabel(item.impact_score)} ${formatScore(item.impact_score)}`), el("time", "", formatDate(item.published_at, true)));
    const link = el("a", "text-button asset-news-link", "原文 ↗");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = `打开 ${item.source || "新闻来源"} 原文`;
    row.append(main, impact, link);
    fragment.append(row);
  });
  container.append(fragment);
}

async function refreshAssets({ silent = false } = {}) {
  try {
    const data = await api("/api/assets");
    renderAssets(data);
  } catch (error) {
    if (!silent) showToast(error.message, "error");
  }
}

function updateAssetRefreshButton(running) {
  const button = document.getElementById("asset-refresh-button");
  button.disabled = running;
  button.lastChild.textContent = running ? " 更新中" : " 刷新价格与新闻";
  button.querySelector(".scan-symbol").classList.toggle("spinning", running);
}

async function runAssetRefresh() {
  updateAssetRefreshButton(true);
  try {
    await api("/api/assets/refresh", { method: "POST", body: {} });
    showToast("黄金、BTC、原油与美债更新已开始", "success");
    pollAssetStatus();
  } catch (error) {
    if (error.status !== 409) showToast(error.message, "error");
    pollAssetStatus();
  }
}

function pollAssetStatus() {
  window.clearInterval(state.assetPoller);
  state.assetPoller = window.setInterval(async () => {
    try {
      const data = await api("/api/assets");
      renderAssets(data);
      if (!data.runtime.refresh_in_progress) {
        window.clearInterval(state.assetPoller);
        state.assetPoller = null;
        const errors = data.runtime.errors || [];
        showToast(errors.length ? `更新完成，${errors.length} 项数据警告` : "黄金、BTC、原油与美债已更新", errors.length ? "error" : "success");
      }
    } catch (_) { /* regular refresh will recover */ }
  }, 1000);
}

function renderTrump(data) {
  const trump = (data.trump?.independent ? data.trump : state.currentView === "social" && socialState.data?.trump) || data.trump || { posts: [], factors: [], runtime: {} };
  const runtime = trump.runtime || {};
  const posts = trump.posts || [];
  const factors = trump.factors || [];
  const globalFactor = trump.global_factor || {};
  const dot = document.getElementById("trump-status-dot");
  dot.className = runtime.refresh_in_progress
    ? "running"
    : runtime.connected ? "connected" : runtime.status === "error" ? "error" : "";
  const publicMode = runtime.source_mode === "public_rss" || runtime.public_enabled;
  document.getElementById("trump-status-text").textContent = runtime.connected
    ? publicMode ? "公开媒体已连接" : "X API 已连接"
    : runtime.status === "error" ? "连接异常" : publicMode || runtime.configured ? "正在连接" : "尚未配置";
  document.getElementById("trump-status-detail").textContent = runtime.last_checked_at
    ? `${runtime.message || "Trump 动态监测"} · ${intervalLabel(runtime.poll_interval_seconds, 30)}轮询 · 检查于 ${formatDate(runtime.last_checked_at, true)}`
    : runtime.message || (publicMode ? "等待公开媒体更新" : "等待连接 X API");

  const globalScore = document.getElementById("trump-global-score");
  globalScore.textContent = globalFactor.post_count ? formatScore(globalFactor.score) : "--";
  globalScore.className = scoreClass(globalFactor.score);
  document.getElementById("trump-global-direction").textContent = globalFactor.post_count ? trumpDirectionLabel(globalFactor) : "暂无有效因子";
  document.getElementById("trump-post-count").textContent = posts.length;
  document.getElementById("trump-last-post").textContent = posts.length ? formatDate(posts[0].created_at, true) : "--";
  const covered = factors.filter((item) => Number(item.post_count || 0) > 0).length;
  document.getElementById("trump-covered-assets").textContent = `${covered}/${factors.length}`;
  document.getElementById("trump-post-result").textContent = `${posts.length} 条`;
  document.getElementById("trump-factor-count").textContent = `${factors.length} 个标的`;

  const refresh = document.getElementById("trump-refresh-button");
  refresh.disabled = Boolean(runtime.refresh_in_progress) || !(publicMode || runtime.configured);
  refresh.lastChild.textContent = runtime.refresh_in_progress ? " 正在读取" : " 刷新 Trump 动态";
  refresh.querySelector(".scan-symbol").classList.toggle("spinning", Boolean(runtime.refresh_in_progress));
  renderTrumpPosts(posts, runtime);
  renderTrumpFactors(factors);
}

function renderTrumpPosts(posts, runtime) {
  const container = document.getElementById("trump-posts");
  const publicMode = runtime.source_mode === "public_rss" || runtime.public_enabled;
  container.replaceChildren();
  if (!posts.length) {
    const empty = emptyState(
      publicMode ? "当前窗口暂无相关政策报道" : runtime.configured ? "暂无已读取的 Trump 帖文" : "尚未连接 X API",
      publicMode ? "公开媒体按计划更新，无需 X Token。" : runtime.configured ? "监控器会在下一轮自动重试" : "可使用默认的公开政策来源，无需购买接口。",
    );
    if (!runtime.configured && !publicMode) {
      const button = el("button", "text-button", "打开设置 →");
      button.type = "button";
      button.addEventListener("click", () => setView("settings"));
      empty.firstElementChild?.append(button);
    }
    container.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  posts.forEach((post) => {
    const analysis = post.analysis || {};
    const row = el("article", "trump-post-row");
    const meta = el("div", "trump-post-meta");
    const reported = post.source_kind === "media_report" || (!post.source_kind && publicMode);
    meta.append(el("span", "market-badge", reported ? post.publisher || post.source || "公开媒体报道" : "@realDonaldTrump"));
    (analysis.theme_labels || []).slice(0, 2).forEach((theme) => meta.append(el("span", "trump-theme-badge", theme)));
    meta.append(el("time", "", formatDate(post.created_at, true)));

    const main = el("div", "trump-post-analysis");
    main.append(appendNewsTranslation(el("p", "", post.text || ""), post, "text"), el("small", "", `${reported ? "规则推断（非已核实政策事实）：" : "分析："}${analysis.summary || "等待主题分析"}`));
    const score = el("div", "trump-post-score");
    score.append(el("strong", scoreClass(analysis.market_score), formatScore(analysis.market_score)));
    const link = el("a", "", reported ? "查看媒体原文" : "查看原帖");
    if (/^https?:\/\//i.test(post.url || "")) link.href = post.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    score.append(link);
    row.append(meta, main, score);
    fragment.append(row);
  });
  container.append(fragment);
}

function renderTrumpFactors(factors) {
  const container = document.getElementById("trump-factor-table");
  container.replaceChildren();
  if (!factors.length) {
    container.append(emptyState("股票池暂无 Trump 因子"));
    return;
  }
  const fragment = document.createDocumentFragment();
  factors.forEach((factor) => {
    const row = el("article", "trump-factor-row");
    const asset = el("div", "trump-factor-asset");
    asset.append(el("strong", "", factor.symbol), el("span", "", `${marketLabels[factor.market] || factor.market} · ${factor.name}`));
    asset.append(instrumentBriefNode(factor));
    const signal = el("div", "trump-factor-signal");
    signal.append(el("strong", scoreClass(factor.score), factor.post_count ? formatScore(factor.score) : "--"), el("span", "", factor.post_count ? trumpDirectionLabel(factor) : "暂无相关样本"));
    const reason = el("div", "trump-factor-reason");
    reason.append(el("p", "", factor.reason || "暂无相关动态"), el("small", "", factor.theme_labels?.join(" · ") || "无活跃主题"));
    const time = el("div", "trump-factor-time");
    time.append(el("span", "", factor.latest_post_at ? formatDate(factor.latest_post_at, true) : "--"));
    if (/^https?:\/\//i.test(factor.latest_post_url || "")) {
      const link = el("a", "", "依据来源");
      link.href = factor.latest_post_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      time.append(link);
    }
    row.append(asset, signal, reason, time);
    fragment.append(row);
  });
  container.append(fragment);
}

async function runTrumpRefresh() {
  try {
    await api("/api/trump/refresh", { method: "POST", body: {} });
    showToast("Trump 动态刷新已开始", "success");
  } catch (error) {
    if (error.status !== 409) showToast(error.message, "error");
  }
  pollTrumpStatus();
}

function pollTrumpStatus() {
  window.clearInterval(state.trumpPoller);
  state.trumpPoller = window.setInterval(async () => {
    try {
      const data = await api("/api/dashboard");
      renderDashboard(data);
      if (!data.trump?.runtime?.refresh_in_progress) {
        window.clearInterval(state.trumpPoller);
        state.trumpPoller = null;
        const runtime = data.trump?.runtime || {};
        showToast(runtime.connected ? "Trump 动态已同步" : runtime.message || "X API 未连接", runtime.connected ? "success" : "error");
      }
    } catch (_) { /* regular refresh will recover */ }
  }, 1000);
}

async function saveXToken() {
  const input = document.getElementById("x-token-input");
  const token = input.value.trim();
  if (!token) {
    showToast("请输入 X API Bearer Token", "error");
    return;
  }
  const button = document.getElementById("x-token-save-button");
  button.disabled = true;
  try {
    await api("/api/trump/token", { method: "POST", body: { token } });
    input.value = "";
    showToast("Token 已加密保存，正在验证连接", "success");
    pollTrumpStatus();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function clearXToken() {
  if (!window.confirm("清除本机保存的 X API Token？")) return;
  try {
    await api("/api/trump/token", { method: "POST", body: { clear: true } });
    document.getElementById("x-token-input").value = "";
    showToast("X API Token 已清除", "success");
    await refreshDashboard({ silent: true });
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderSettings(data) {
  const settings = data.settings;
  if (!state.settingsDirty) {
  document.getElementById("setting-interval").value = settings.poll_interval_seconds || settings.poll_interval_minutes * 60;
  document.getElementById("setting-lookback").value = settings.lookback_hours;
  document.getElementById("setting-candidates").value = settings.max_items_per_asset;
  document.getElementById("setting-asset-news-interval").value = settings.asset_news_interval_seconds;
  document.getElementById("setting-threshold").value = settings.alert_threshold;
  document.getElementById("threshold-output").value = Number(settings.alert_threshold).toFixed(2);
  document.getElementById("setting-rss").checked = settings.google_news_rss;
  document.getElementById("setting-yahoo-rss").checked = settings.yahoo_finance_rss;
  document.getElementById("setting-bing-rss").checked = settings.bing_news_rss;
  document.getElementById("setting-translation").checked = settings.news_translation_enabled;
  document.getElementById("setting-fundamentals").checked = settings.fundamentals_enabled;
  document.getElementById("setting-movement-scope").value = settings.movement_scope || "portfolio";
  state.analysisMode = settings.analysis_mode;
  document.querySelectorAll("#analysis-mode-control button").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === state.analysisMode);
  });
  }
  const engine = settings.api_key_configured ? `${settings.model} 可用` : "未配置 API Key · 本地规则可用";
  document.getElementById("model-status").textContent = engine;
  document.getElementById("analysis-engine-caption").textContent = engine;
  const translation = data.translation || {};
  document.getElementById("translation-status").textContent = translation.error || `${translation.state || "等待"} · 待处理 ${translation.pending || 0} · 原文保留`;
  const xRuntime = data.trump?.runtime || {};
  document.getElementById("x-token-status").textContent = xRuntime.configured
    ? `${xRuntime.message || "已配置"}${xRuntime.credential_source === "environment" ? " · 环境变量" : " · Windows 加密存储"}`
    : "尚未配置 X API";
  if (!state.settingsReady) {
    document.querySelectorAll("#settings-form input, #settings-form button").forEach(control=>{control.disabled=false;});
    state.settingsReady=true;
  }
}

async function refreshFeatureHealth() {
  const status = document.getElementById("feature-health-status");
  try {
    const result = await api("/api/features");
    renderFeatureHealth(result);
  } catch (error) {
    status.textContent = `状态读取失败：${error.message}`;
  }
}

function renderFeatureHealth(result) {
  const labels = {running:"已启用",ready:"已配置",disabled:"已关闭",blocked:"待授权",empty:"待录入持仓",error:"需要检查"};
  const container = document.getElementById("feature-health");
  container.replaceChildren();
  for (const feature of result.features) {
    const row = el("div", "field-row"), text = el("span");
    text.append(el("strong", "", feature.name), el("small", "", feature.detail));
    row.append(text, el("span", feature.state === "error" || feature.state === "blocked" ? "negative" : "quiet-note", labels[feature.state] || feature.state));
    container.append(row);
  }
  document.getElementById("feature-health-status").textContent = `检查于 ${formatDate(result.checked_at, true)} · ${result.blocked.length} 项仍需授权`;
}

function renderManualWatchlist(data) {
  const select = document.getElementById("manual-watch-select");
  const previous = select.value;
  select.replaceChildren();
  (data.watchlist || []).forEach((item) => {
    const option = el("option", "", `${marketLabels[item.market] || item.market} · ${item.symbol} · ${item.name}`);
    option.value = `${item.market}:${item.symbol}`;
    select.append(option);
  });
  if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
  briefAfterTitle("manual-watch-select", select.value);
}

function renderConnection(error = null) {
  const strip = document.getElementById("connection-strip");
  if (!strip) return;
  strip.className = `connection-strip${error ? " connection-error" : ""}`;
  document.getElementById("connection-message").textContent = error
    ? `总览同步失败，保留上次数据 · ${error.message}`
    : typeof startupMemory !== "undefined" && startupMemory.previewAt
      ? `已恢复上次快照 ${formatDate(startupMemory.previewAt, true)} · 正在后台更新`
    : `本地服务已连接 · 总览同步于 ${formatDate(new Date().toISOString(), true)}`;
  document.getElementById("connection-retry").hidden = !error;
}

function renderDashboard(data) {
  if (state.dashboard === data) return;
  state.dashboard = data;
  const stats = data.stats;
  document.getElementById("threshold-label").textContent = `${stats.window_hours || 72}H 加权`;

  const autoToggle = document.getElementById("auto-toggle");
  autoToggle.checked = Boolean(data.runtime.auto_enabled);
  document.getElementById("auto-caption").textContent = data.runtime.auto_enabled ? `每轮后等待 ${data.settings.poll_interval_seconds || data.settings.poll_interval_minutes * 60} 秒` : "已关闭";

  renderOverview();
  renderAlerts(document.getElementById("overview-alerts"), data.alerts || [], { limit: 4 });
  renderAlerts(document.getElementById("all-alerts"), data.alerts || [], { table: true });
  document.getElementById("alert-summary").textContent = `${(data.alerts || []).length} 条告警记录`;
  renderPulse(data);
  renderWatchlist(data);
  renderPortfolio(data);
  renderSettings(data);
  renderTrump(data);
  renderManualWatchlist(data);
  renderKoreaStocks(data);
  renderRuntime(data);
  renderMovements(data);
  applyNewsFilters();
  if (typeof renderEventCenter === "function") renderEventCenter();
  if (typeof updateResearchSourceSites === "function") updateResearchSourceSites();
}

async function refreshDashboard({ silent = false } = {}) {
  try {
    const data = await api("/api/dashboard");
    renderDashboard(data);
    renderConnection();
    return data;
  } catch (error) {
    renderConnection(error);
    if (!silent) showToast(error.message, "error");
  }
}

function currentNews() {
  const matchesLanguage = item => {
    if (state.newsLanguage !== "zh") return true;
    if (item.title_translation_status === "translated" && /[\u3400-\u9fff]/.test(item.title_zh || "")) return true;
    let title = item.title || "";
    const suffix = ` - ${item.source}`;
    if (item.source && title.endsWith(suffix)) title = title.slice(0, -suffix.length);
    const count = (title.match(/[\u3400-\u9fff]/g) || []).length;
    return count >= 3 && count * 3 >= (title.match(/[A-Za-z]/g) || []).length;
  };
  return state.liveNews && (!state.liveNews.language || state.liveNews.language === state.newsLanguage)
    ? state.liveNews.news : (state.dashboard?.news || []).filter(matchesLanguage);
}

function overviewEvents(items, positions) {
  const keys=new Set(positions.map(item=>`${item.market}:${normalizeStockSymbol(item.symbol)}`));
  const held=item=>keys.has(`${item.market}:${normalizeStockSymbol(item.symbol)}`);
  const priority=[...items].sort((a,b)=>Number(held(b))-Number(held(a)) || Math.abs(Number(b.score) || 0)-Math.abs(Number(a.score) || 0) || (Date.parse(b.published_at) || 0)-(Date.parse(a.published_at) || 0));
  return {priority,holdings:priority.filter(held)};
}

function renderOverview() {
  if(!state.dashboard)return;
  const positions=state.dashboard.portfolio?.positions || [];
  const events=overviewEvents(currentNews(),positions);
  renderNews(document.getElementById("overview-news"),events.priority,{limit:8});
  renderNews(document.getElementById("holdings-events"),events.holdings,{limit:2,
    emptyTitle:positions.length ? "暂无持仓相关事件" : "尚未录入持仓",
    emptyCopy:positions.length ? "当前新闻窗口内没有匹配记录" : "当前组合为空"});
  renderOverviewFreshness();
  if (typeof renderCompass === "function") renderCompass();
  if (typeof renderEventCenter === "function") renderEventCenter();
}

function renderOverviewFreshness() {
  if(!state.dashboard)return;
  if (typeof renderFreshnessStatus === "function") { renderFreshnessStatus(); return; }
  const container=document.getElementById("overview-freshness"), fresh=state.dashboard.freshness || {},rows=state.liveNews?.news || [];
  const latest=(key,fallback)=>[fallback,...rows.map(row=>row[key])].filter(Boolean).sort().at(-1);
  const published=latest("published_at",fresh.news_published_at),collected=latest("discovered_at",fresh.news_collected_at),analyzed=latest("analyzed_at",fresh.news_analyzed_at);
  const runtime=state.liveNews?.runtime || state.dashboard.runtime || {},run=runtime.last_run;
  const quoteStates={live:"有效报价",delayed:"延迟",closed:"休市",stale:"陈旧报价"};
  const priceCopy=(state.assets?.assets || []).map(item=>`${ASSET_NAMES[item.key]} ${formatDate(item.price_time,true)} · ${quoteStates[item.quote_status] || "状态未注明"}`).join("；") || "报价尚未就绪";
  const portfolio=state.dashboard.portfolio?.runtime || {};
  const emptyPortfolio=!(state.dashboard.portfolio?.positions || []).length;
  container.replaceChildren();
  [["股票新闻",`最新原文 ${formatDate(published,true)}`,`采集 ${formatDate(collected,true)} · 判断 ${formatDate(analyzed,true)} · ${runtime.scan_in_progress?"采集中":run?.errors?.length?"采集有错误":run?"上一轮完成":"尚未完成采集"}`],
   ["资产专题报价","黄金 / BTC / 原油 / 五年期美债",priceCopy],
   ["我的资产",emptyPortfolio ? "当前无持仓" : `行情快照 ${formatDate(portfolio.updated_at,true)}`,emptyPortfolio ? "0 项持仓" : portfolio.errors?.length?`${portfolio.errors.slice(0,2).join("；")}${portfolio.errors.length>2?`；另 ${portfolio.errors.length-2} 项警告`:""}`:({live:"已取得行情",partial:"部分行情可用",waiting:"等待行情"}[portfolio.status] || "状态待确认")]].forEach(([title,value,note])=>{const cell=el("div");cell.append(el("span","",title),el("strong","",value),el("small","",note));container.append(cell);});
}

async function refreshNews() {
  if (state.newsPending) return;
  state.newsPending = true;
  try {
    const language = state.newsLanguage;
    const data = await api(`/api/news?language=${language}`);
    if (language !== state.newsLanguage) return;
    if (state.liveNews && data.generated_at < state.liveNews.generated_at) return;
    state.liveNews = data;
    const caption = document.getElementById("auto-caption");
    caption.textContent = data.runtime.auto_enabled ? `${data.runtime.scan_in_progress ? "采集中" : "等待下一轮"} · 间隔 ${data.runtime.poll_interval_seconds} 秒` : "已关闭";
    caption.title = data.runtime.source_note;
    const signature = JSON.stringify(currentNews());
    if (signature !== state.newsSignature) {
      state.newsSignature = signature;
      renderOverview();
      applyNewsFilters();
    }
  } catch (error) {
    const caption = document.getElementById("auto-caption");
    caption.textContent = "新闻同步失败";
    caption.title = error.message;
  } finally {
    state.newsPending = false;
  }
}

function applyNewsFilters() {
  if (!state.dashboard) return;
  const query = document.getElementById("news-search").value.trim().toLocaleLowerCase();
  const market = document.getElementById("market-filter").value;
  const items = currentNews().filter((item) => {
    const sentimentMatch = state.sentimentFilter === "all" || item.polarity === state.sentimentFilter;
    const marketMatch = market === "all" || item.market === market;
    const text = `${item.symbol} ${item.title} ${item.title_zh || ""} ${item.source || ""} ${item.analysis_summary} ${eventLabels[item.event_type] || ""}`.toLocaleLowerCase();
    return sentimentMatch && marketMatch && (!query || text.includes(query));
  });
  items.sort((a, b) => {
    const strength = state.newsSort === "strength" ? Math.abs(Number(b.score) || 0) - Math.abs(Number(a.score) || 0) : 0;
    return strength || (Date.parse(b.published_at) || 0) - (Date.parse(a.published_at) || 0);
  });
  document.getElementById("news-result-count").textContent = `${items.length} 条记录`;
  renderNews(document.getElementById("all-news"), items, { expanded: true });
}

function openDetail(item) {
  const dialog = document.getElementById("detail-dialog");
  const market = document.getElementById("detail-market");
  market.textContent = `${marketLabels[item.market] || item.market} · ${item.symbol}`;
  briefAfterTitle("detail-market", item);
  document.getElementById("detail-event").textContent = eventLabels[item.event_type] || "其他";
  renderNewsDetailText(item);
  document.getElementById("detail-source").textContent = `${item.source || "未知来源"} · ${formatDate(item.published_at)}`;
  const score = document.getElementById("detail-score");
  score.textContent = `${scoreLabel(item.score)} ${formatScore(item.score)}`;
  score.className = scoreClass(item.score);
  document.getElementById("detail-horizon").textContent = item.horizon || "--";
  document.getElementById("detail-analysis").textContent = item.analysis_summary || "暂无分析摘要";

  const factors = document.getElementById("detail-factors");
  factors.replaceChildren();
  [
    ["重要性", item.importance],
    ["预期差线索", item.analysis_metadata?.expected_baseline_available ? item.surprise : null],
    ["相关性", item.relevance],
    ["置信度", item.confidence],
  ].forEach(([label, value]) => {
    const box = el("div", "factor");
    box.append(el("span", "", label), el("strong", "", value == null ? "基准缺失" : Number(value).toFixed(2)));
    const bar = el("div", "factor-bar");
    const fill = el("i");
    fill.style.width = `${Math.max(0, Math.min(100, Number(value || 0) * 100))}%`;
    bar.append(fill);
    box.append(bar);
    factors.append(box);
  });
  if (item.trump_factor) {
    const trump = item.trump_factor;
    const box = el("div", "factor");
    box.title = trump.reason || "Trump 动态因子";
    box.append(el("span", "", "Trump因子"), el("strong", scoreClass(trump.score), formatScore(trump.score)));
    const bar = el("div", "factor-bar");
    const fill = el("i", scoreClass(trump.score));
    fill.style.width = `${Math.max(0, Math.min(100, Math.abs(Number(trump.score || 0)) * 100))}%`;
    bar.append(fill);
    box.append(bar);
    factors.append(box);
  }

  const risks = document.getElementById("detail-risks");
  risks.replaceChildren();
  (item.risk_flags || []).forEach((risk) => risks.append(el("span", "risk-tag", risk)));
  const researchButton = document.getElementById("detail-research");
  researchButton.onclick = () => {
    dialog.close();
    const watch = state.dashboard?.watchlist?.find(row => row.market === item.market && normalizeStockSymbol(row.symbol) === normalizeStockSymbol(item.symbol));
    openStrategyEditor({name:watch?.name || item.symbol,target:`${item.market}:${item.symbol}`,kind:"stock",horizon:item.horizon,
      data_as_of:(item.published_at || "").slice(0,10),fundamentals:item.analysis_summary || "",risks:(item.risk_flags || []).join("\n"),
      evidence:/^https?:\/\//i.test(item.url || "") ? [{url:item.url,excerpt:item.analysis_metadata?.evidence_text || item.news_summary || item.title,role:Number(item.score)<0 ? "counter":"support",as_of:(item.published_at || "").slice(0,10)}] : []});
  };
  const link = document.getElementById("detail-link");
  if (/^https?:\/\//i.test(item.url || "")) {
    link.href = item.url;
    link.title = `打开 ${item.source || "新闻来源"} 原文`;
    link.hidden = false;
  } else {
    link.removeAttribute("href");
    link.removeAttribute("title");
    link.hidden = true;
  }
  dialog.showModal();
}

function openMovementDetail(item) {
  const dialog = document.getElementById("movement-dialog");
  document.getElementById("movement-detail-market").textContent = `${marketLabels[item.market] || item.market} · ${item.symbol}`;
  document.getElementById("movement-detail-type").textContent = movementTypeLabels[item.movement_type] || "异动";
  document.getElementById("movement-detail-title").textContent = item.name;
  briefAfterTitle("movement-detail-title", item);
  document.getElementById("movement-detail-meta").textContent = `${item.exchange}${item.sector ? ` · ${item.sector}` : ""}${item.session_label ? ` · ${item.session_label}` : ""} · ${formatDate(item.quote_time || item.detected_at)}`;
  document.getElementById("movement-detail-price").textContent = `${formatPrice(item.price)} ${item.currency || ""}`.trim();
  const change = document.getElementById("movement-detail-change");
  change.textContent = `${item.change_pct >= 0 ? "+" : ""}${Number(item.change_pct).toFixed(2)}%`;
  change.className = item.change_pct >= 0 ? "positive" : "negative";
  document.getElementById("movement-detail-volume").textContent = item.relative_volume == null ? "--" : `${Number(item.relative_volume).toFixed(2)}x`;
  document.getElementById("movement-detail-intensity").textContent = `${Number(item.intensity_score || 0).toFixed(0)} / 100`;
  document.getElementById("movement-detail-confidence").textContent = item.live_extended ? "消息原因待确认" : `${confidenceLabel(item.confidence)} · ${Math.round(Number(item.confidence || 0) * 100)}%`;
  document.getElementById("movement-detail-reason").textContent = item.reason_summary;

  const trump = item.trump_factor || {};
  const trumpScore = document.getElementById("movement-trump-score");
  trumpScore.textContent = formatScore(trump.score);
  trumpScore.className = scoreClass(trump.score);
  document.getElementById("movement-trump-reason").textContent = trump.reason || "暂无相关动态";
  const trumpLink = document.getElementById("movement-trump-link");
  if (/^https?:\/\//i.test(trump.latest_post_url || "")) {
    trumpLink.href = trump.latest_post_url;
    trumpLink.hidden = false;
  } else {
    trumpLink.removeAttribute("href");
    trumpLink.hidden = true;
  }

  const evidenceBlock = document.getElementById("movement-evidence-block");
  const link = document.getElementById("movement-detail-link");
  const hasLink = /^https?:\/\//i.test(item.evidence_url || "");
  evidenceBlock.hidden = !hasLink;
  link.hidden = !hasLink;
  if (hasLink) {
    document.getElementById("movement-detail-evidence").textContent = item.evidence_title;
    appendNewsTranslation(document.getElementById("movement-detail-evidence"), item, "evidence_title");
    document.getElementById("movement-detail-source").textContent = `${item.evidence_source || "新闻来源"} · ${formatDate(item.evidence_published_at)}`;
    link.href = item.evidence_url;
    link.title = `打开 ${item.evidence_source || "新闻来源"} 原文`;
  } else {
    link.removeAttribute("href");
    link.removeAttribute("title");
  }
  dialog.showModal();
}

function updateScanButton(running) {
  const button = document.getElementById("scan-button");
  button.disabled = running;
  button.lastChild.textContent = running ? " 检测中" : " 立即检测";
  button.querySelector(".scan-symbol").classList.toggle("spinning", running);
}

function updateMovementButton(running) {
  const button = document.getElementById("movement-scan-button");
  if (!button) return;
  button.disabled = running;
  button.lastChild.textContent = running ? " 扫描中" : " 扫描全部标的";
  button.querySelector(".scan-symbol").classList.toggle("spinning", running);
}

async function runScan() {
  updateScanButton(true);
  try {
    await api("/api/scan", { method: "POST", body: { offline: false } });
    showToast("检测任务已开始", "success");
    pollScanStatus();
  } catch (error) {
    if (error.status === 409) {
      showToast("检测已经在进行中");
      pollScanStatus();
    } else {
      updateScanButton(false);
      showToast(error.message, "error");
    }
  }
}

function pollScanStatus() {
  window.clearInterval(state.scanPoller);
  state.scanPoller = window.setInterval(async () => {
    try {
      const data = await api("/api/dashboard");
      renderDashboard(data);
      if (!data.runtime.scan_in_progress && !data.runtime.movement_scan_in_progress) {
        window.clearInterval(state.scanPoller);
        state.scanPoller = null;
        const errors = data.runtime.last_run?.errors || [];
        showToast(errors.length ? `检测完成，${errors.length} 项警告` : "检测完成", errors.length ? "error" : "success");
      }
    } catch (_) { /* regular refresh will recover */ }
  }, 1000);
}

async function runMovementScan() {
  updateMovementButton(true);
  try {
    await api("/api/movements/scan", { method: "POST", body: {} });
    showToast("全部持仓股与韩国核心股异动扫描已开始", "success");
    pollMovementStatus();
  } catch (error) {
    if (error.status === 409) {
      showToast("持仓异动扫描已经在进行中");
      pollMovementStatus();
    } else {
      updateMovementButton(false);
      showToast(error.message, "error");
    }
  }
}

function pollMovementStatus() {
  window.clearInterval(state.movementPoller);
  state.movementPoller = window.setInterval(async () => {
    try {
      const data = await api("/api/dashboard");
      renderDashboard(data);
      if (!data.runtime.movement_scan_in_progress) {
        window.clearInterval(state.movementPoller);
        state.movementPoller = null;
        const run = data.runtime.last_movement_run;
        const errors = run?.errors || [];
        const message = errors.length
          ? `扫描完成：${run?.found || 0} 个异动，${errors.length} 项待核查`
          : `扫描完成：发现 ${run?.found || 0} 个异动`;
        showToast(message, errors.length ? "error" : "success");
      }
    } catch (_) { /* regular refresh will recover */ }
  }, 1000);
}

async function toggleAuto(enabled) {
  try {
    await api("/api/auto", { method: "POST", body: { enabled } });
    showToast(enabled ? "自动监测已开启" : "自动监测已关闭", "success");
    await refreshDashboard({ silent: true });
    if (enabled) pollScanStatus();
  } catch (error) {
    document.getElementById("auto-toggle").checked = !enabled;
    showToast(error.message, "error");
  }
}

async function removeWatchItem(item) {
  if (!window.confirm(`从股票池移除 ${item.market}:${item.symbol}？`)) return;
  try {
    await api(`/api/watchlist/${encodeURIComponent(item.market)}/${encodeURIComponent(item.symbol)}`, { method: "DELETE" });
    showToast(`${item.symbol} 已移除`, "success");
    await refreshDashboard({ silent: true });
  } catch (error) {
    showToast(error.message, "error");
  }
}

function setPositionAction(action) {
  const form = document.getElementById("position-adjust-form");
  const isFund = form.elements.market.value === "FUND";
  form.elements.action.value = action;
  document.querySelectorAll("#position-action-control button").forEach((button) => {
    button.classList.toggle("active", button.dataset.positionAction === action);
  });
  document.getElementById("position-adjust-submit").textContent = action === "buy" ? "确认加仓" : "确认减仓";
  document.getElementById("position-adjust-note").textContent = action === "buy"
    ? "加仓将按成交价格重新计算平均成本。"
    : `减仓不会改变剩余${isFund ? "份额" : "持仓"}的平均成本；数量减至零会自动清仓。`;
}

function openPositionAdjust(item, action) {
  const dialog = document.getElementById("position-adjust-dialog");
  const form = document.getElementById("position-adjust-form");
  form.reset();
  form.elements.market.value = item.market;
  form.elements.symbol.value = item.symbol;
  const priceDigits = item.market === "FUND" ? 4 : item.currency === "KRW" ? 0 : 2;
  form.elements.execution_price.value = Number(item.price || item.average_cost || 0).toFixed(priceDigits);
  form.elements.quantity.max = action === "sell" ? String(item.quantity) : "";
  document.getElementById("position-adjust-title").textContent = `${item.name} · ${formatPositionQuantity(item.quantity)} ${item.market === "FUND" ? "份" : "股"}`;
  briefAfterTitle("position-adjust-title", item);
  setPositionAction(action);
  dialog.showModal();
}

function updatePositionFormMarket(market) {
  const form = document.getElementById("position-form");
  const isFund = market === "FUND";
  const currencies = { CN: "CNY", HK: "HKD", US: "USD", KR: "KRW", FUND: "CNY" };
  const currency = document.getElementById("position-currency");
  currency.value = currencies[market] || "CNY";
  currency.disabled = isFund;
  document.getElementById("fund-search-section").hidden = !isFund;
  document.getElementById("position-symbol-label").textContent = isFund ? "基金代码" : "股票代码";
  document.getElementById("position-name-label").textContent = isFund ? "基金名称" : "公司名称";
  document.getElementById("position-quantity-label").textContent = isFund ? "持有份额" : "持仓数量";
  document.getElementById("position-cost-label").textContent = isFund ? "每份平均成本" : "平均成本";
  form.elements.symbol.placeholder = isFund ? "例如 110022" : "例如 MU / 0700.HK";
  form.elements.name.placeholder = isFund ? "可通过上方检索自动填写" : "例如 Micron Technology";
  document.getElementById("position-form-note").textContent = isFund
    ? "基金市值按份额和最近确认净值估算；实际份额、成本与到账金额以支付宝为准。"
    : "纯数字代码会按市场自动补全后缀；新增持仓也会自动加入新闻监测池。";
  if (!isFund) {
    form.elements.fund_type.value = "";
    document.getElementById("fund-search-query").value = "";
    document.getElementById("fund-search-results").replaceChildren();
  }
}

function openPositionDialog() {
  const form = document.getElementById("position-form");
  form.reset();
  updatePositionFormMarket("CN");
  positionFormBrief();
  document.getElementById("fund-search-results").replaceChildren();
  document.getElementById("position-dialog").showModal();
}

async function searchFunds() {
  const query = document.getElementById("fund-search-query").value.trim();
  const results = document.getElementById("fund-search-results");
  const button = document.getElementById("fund-search-button");
  results.replaceChildren();
  if (query.length < 2) {
    results.append(el("p", "fund-search-status", "请输入至少 2 个字符或完整六位代码。"));
    return;
  }
  button.disabled = true;
  results.append(el("p", "fund-search-status", "正在检索基金…"));
  try {
    const data = await api(`/api/funds/search?q=${encodeURIComponent(query)}`);
    results.replaceChildren();
    if (!(data.items || []).length) {
      results.append(el("p", "fund-search-status", "没有找到匹配的公募基金。"));
      return;
    }
    data.items.forEach((item) => {
      const result = el("button", "fund-search-result");
      result.type = "button";
      const identity = el("div");
      identity.append(
        el("strong", "", item.name),
        instrumentBriefNode({market:"FUND",symbol:item.code}),
        el("span", "", `${item.code} · ${item.fund_type || "公募基金"}${item.fund_company ? ` · ${item.fund_company}` : ""}`),
      );
      const nav = el("em", "", item.latest_nav === null ? "净值 --" : `¥${Number(item.latest_nav).toFixed(4)}`);
      result.append(identity, nav);
      result.title = `选择 ${item.name}${item.nav_date ? ` · 净值日期 ${item.nav_date}` : ""}`;
      result.addEventListener("click", () => {
        const form = document.getElementById("position-form");
        form.elements.symbol.value = item.code;
        form.elements.name.value = item.name;
        form.elements.fund_type.value = item.fund_type || "公募基金";
        positionFormBrief();
        document.querySelectorAll(".fund-search-result").forEach((node) => node.classList.toggle("selected", node === result));
      });
      results.append(result);
    });
  } catch (error) {
    results.replaceChildren(el("p", "fund-search-status negative", error.message));
  } finally {
    button.disabled = false;
  }
}

function loadPortfolioSortPreferences() {
  try {
    const key = window.localStorage.getItem(PORTFOLIO_SORT_KEY_STORAGE);
    const direction = window.localStorage.getItem(PORTFOLIO_SORT_DIRECTION_STORAGE);
    if (["default", "market_value", "daily_pnl", "total_pnl"].includes(key)) state.portfolioSortKey = key;
    if (["asc", "desc"].includes(direction)) state.portfolioSortDirection = direction;
  } catch (_) {
    // The in-memory defaults remain available when browser storage is blocked.
  }
  syncPortfolioSortControls();
}

function savePortfolioSortPreferences() {
  try {
    window.localStorage.setItem(PORTFOLIO_SORT_KEY_STORAGE, state.portfolioSortKey);
    window.localStorage.setItem(PORTFOLIO_SORT_DIRECTION_STORAGE, state.portfolioSortDirection);
  } catch (_) {
    // Sorting still works for the current session.
  }
}

async function removePortfolioPosition(item = null) {
  if (state.portfolioDeletePending) return;
  const count = state.dashboard?.portfolio?.positions?.length || 0;
  if (!item && !count) return;
  const message = item
    ? `确认删除 ${item.name}（${item.symbol}）的持仓记录？股票池和研究记录保留，不会执行卖出。`
    : `确认清空全部 ${count} 项持仓（包括股票和基金）？组合市值和盈亏将归零。原持仓将先备份，股票池与研究记录保留，不会执行卖出。`;
  if (!window.confirm(message)) return;
  state.portfolioDeletePending = true;
  document.getElementById("portfolio-clear-button").disabled = true;
  document.getElementById("add-position-button").disabled = true;
  document.querySelectorAll(".portfolio-actions button").forEach(button => button.disabled = true);
  try {
    const result = await api(item ? `/api/portfolio/positions/${encodeURIComponent(item.market)}/${encodeURIComponent(item.symbol)}` : "/api/portfolio/positions", {
      method: "DELETE", ...(item ? {} : {body: {confirmed: true}}),
    });
    showToast(item ? `${item.name} 已从持仓移除` : `已清空 ${result.removed_count} 项持仓，原记录已备份`, "success");
    await refreshDashboard({ silent: true });
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    state.portfolioDeletePending = false;
    if (state.dashboard) renderPortfolio(state.dashboard);
  }
}

async function refreshPortfolioQuotes() {
  const button = document.getElementById("portfolio-refresh-button");
  button.disabled = true;
  try {
    await api("/api/portfolio/refresh", { method: "POST", body: {} });
    showToast("正在刷新持仓行情", "success");
    pollPortfolioStatus();
  } catch (error) {
    button.disabled = false;
    showToast(error.message, "error");
  }
}

function pollPortfolioStatus() {
  window.clearInterval(state.portfolioPoller);
  const initialUpdatedAt = state.dashboard?.portfolio?.runtime?.updated_at || null;
  let sawRefreshing = false;
  let attempts = 0;
  state.portfolioPoller = window.setInterval(async () => {
    attempts += 1;
    try {
      const data = await api("/api/dashboard");
      renderDashboard(data);
      const runtime = data.portfolio?.runtime || {};
      const refreshing = Boolean(runtime.refresh_in_progress);
      sawRefreshing ||= refreshing;
      const receivedNewSnapshot = Boolean(runtime.updated_at && runtime.updated_at !== initialUpdatedAt);
      if ((!refreshing && (sawRefreshing || receivedNewSnapshot || attempts >= 5)) || attempts >= 20) {
        window.clearInterval(state.portfolioPoller);
        state.portfolioPoller = null;
        document.getElementById("portfolio-refresh-button").disabled = false;
      }
    } catch (_) {
      if (attempts >= 20) {
        window.clearInterval(state.portfolioPoller);
        state.portfolioPoller = null;
        document.getElementById("portfolio-refresh-button").disabled = false;
      }
    }
  }, 1000);
}

function setView(view, { refresh = true } = {}) {
  if(view === "trump") { view="social";setSocialView("trump"); }
  const inEvents = ["signals", "feed", "movements", "alerts"].includes(view);
  if (!document.querySelector(`.view[data-view="${view}"]`)) view = "overview";
  state.currentView = view;
  document.querySelector(".topbar").classList.toggle("social-header", !["overview","signals","feed","alerts","watchlist"].includes(view));
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.querySelectorAll(".nav-item").forEach((node) => {
    const active = node.dataset.viewTarget === view || (node.dataset.viewTarget === "signals" && inEvents) || (node.dataset.viewTarget === "indices" && view === "korea-stocks") || (view === "research" && node.dataset.tab === researchState.tab);
    node.classList.toggle("active", active);
    if (active) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  const marketGroup = document.getElementById("nav-market");
  const inMarket = inEvents || view === "korea-stocks" || Boolean(marketGroup.querySelector(`[data-view-target="${view}"]`));
  marketGroup.querySelector("summary").classList.toggle("has-current",inMarket);
  marketGroup.open = inMarket && window.innerWidth > 760;
  const researchGroup = document.getElementById("nav-research");
  if (researchGroup) {
    researchGroup.querySelector("summary").classList.toggle("has-current", view === "research");
    researchGroup.open = view === "research" && window.innerWidth > 760;
  }
  const eventTabs = document.getElementById("event-center-tabs");
  if (eventTabs) {
    eventTabs.hidden = !inEvents;
    document.querySelector(".view-stack").classList.toggle("event-center-active", inEvents);
    eventTabs.querySelectorAll("button").forEach(button => {
      button.classList.toggle("active", button.dataset.viewTarget === view);
      button.setAttribute("aria-current", button.dataset.viewTarget === view ? "page" : "false");
    });
  }
  const titles = {
    overview: ["市场情报", "监测总览"],
    social: ["社交讨论观察", "情绪仪表盘"],
    research: ["公司研究 · 条件判断 · 版本留痕", "研究复盘"],
    signals: ["市场情报", "事件中心"],
    feed: ["事件数据库", "事件中心"],
    alerts: ["重点信号", "事件中心"],
    movements: ["持仓雷达", "事件中心"],
    assets: ["跨资产雷达", "黄金、Bitcoin、原油与五年期美债"],
    "korea-stocks": ["韩国核心股", "三星 / 海力士"],
    indices: ["全球指数", "韩国综合、科创50、费城半导体、纳斯达克"],
    portfolio: ["个人资产", "我的资产"],
    trump: ["政策事件雷达", "Trump X 动态"],
    watchlist: ["监测范围", "股票池"],
    settings: ["本地配置", "系统设置"],
  };
  document.getElementById("page-eyebrow").textContent = titles[view][0];
  document.getElementById("page-title").textContent = titles[view][1];
  if (typeof saveStartupPreferences === "function") saveStartupPreferences();
  if (!refresh) return;
  if (view === "signals" && typeof renderEventCenter === "function") renderEventCenter();
  if (view === "indices" && typeof refreshIndices === "function") refreshIndices();
  if (view === "korea-stocks") {
    window.setTimeout(() => refreshKoreaStocks({ silent: true }), 0);
  }
  if (view === "portfolio") {
    window.setTimeout(() => refreshDashboard({ silent: true }), 0);
  }
  if (view === "social") refreshSocial();
  if (view === "settings") refreshFeatureHealth();
  if (view === "research") refreshResearch();
}

function showToast(message, type = "info") {
  const toast = el("div", `toast ${type}`, message);
  document.getElementById("toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function bindDialogs() {
  document.querySelectorAll(".close-dialog").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
      if (outside) dialog.close();
    });
  });
}

function bindForms() {
  document.querySelectorAll("#settings-form input, #settings-form button").forEach(control=>{control.disabled=true;});
  document.getElementById("portfolio-principal-input").addEventListener("input", () => {
    state.portfolioPrincipalDirty = true;
    document.getElementById("portfolio-principal-error").textContent = "";
  });
  document.getElementById("portfolio-principal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.portfolioPrincipalSaving) return;
    const input = document.getElementById("portfolio-principal-input");
    const button = document.getElementById("portfolio-principal-save");
    state.portfolioPrincipalSaving = true;
    input.disabled = button.disabled = true;
    try {
      const result = await api("/api/portfolio/principal", { method: "POST", body: { principal_cny: Number(input.value) } });
      input.value = Number(result.principal_cny).toFixed(2);
      state.portfolioPrincipalDirty = false;
      document.getElementById("portfolio-principal-error").textContent = "";
      showToast("本金已保存", "success");
      await refreshDashboard({ silent: true });
    } catch (error) {
      document.getElementById("portfolio-principal-error").textContent = error.message;
    } finally {
      state.portfolioPrincipalSaving = false;
      input.disabled = button.disabled = false;
    }
  });
  document.getElementById("position-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api("/api/portfolio/positions", {
        method: "POST",
        body: {
          market: form.get("market"),
          symbol: form.get("symbol"),
          name: form.get("name"),
          quantity: Number(form.get("quantity")),
          average_cost: Number(form.get("average_cost")),
          currency: form.get("currency"),
          fund_type: form.get("fund_type"),
        },
      });
      document.getElementById("position-dialog").close();
      formElement.reset();
      updatePositionFormMarket("CN");
      showToast("持仓已保存，正在获取行情", "success");
      setView("portfolio");
      pollPortfolioStatus();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  document.getElementById("position-adjust-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api("/api/portfolio/adjust", {
        method: "POST",
        body: {
          market: form.get("market"),
          symbol: form.get("symbol"),
          action: form.get("action"),
          quantity: Number(form.get("quantity")),
          execution_price: Number(form.get("execution_price")),
        },
      });
      document.getElementById("position-adjust-dialog").close();
      showToast(result.closed ? "持仓已减至零并清仓" : form.get("action") === "buy" ? "加仓已记录" : "减仓已记录", "success");
      pollPortfolioStatus();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  document.getElementById("stock-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api("/api/watchlist", {
        method: "POST",
        body: {
          market: form.get("market"), symbol: form.get("symbol"), name: form.get("name"),
          query: form.get("query"), aliases: form.get("aliases"),
        },
      });
      document.getElementById("stock-dialog").close();
      formElement.reset();
      showToast("股票已加入监测池", "success");
      await refreshDashboard({ silent: true });
    } catch (error) { showToast(error.message, "error"); }
  });

  document.getElementById("manual-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const [market, symbol] = String(form.get("watch_key")).split(":");
    try {
      await api("/api/inbox", {
        method: "POST",
        body: { market, symbol, title: form.get("title"), summary: form.get("summary"), url: form.get("url") },
      });
      document.getElementById("manual-dialog").close();
      formElement.reset();
      showToast("消息已保存，正在分析", "success");
      pollScanStatus();
    } catch (error) { showToast(error.message, "error"); }
  });

  document.getElementById("settings-form").addEventListener("input",event=>{
    if (["setting-interval","setting-lookback","setting-candidates","setting-asset-news-interval","setting-threshold","setting-rss","setting-yahoo-rss","setting-bing-rss","setting-translation","setting-fundamentals","setting-movement-scope"].includes(event.target.id)) {
      state.settingsDirty=true;
      document.getElementById("settings-state").textContent="有未保存更改";
    }
  });
  document.getElementById("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/settings", {
        method: "POST",
        body: {
          poll_interval_seconds: Number(document.getElementById("setting-interval").value),
          lookback_hours: Number(document.getElementById("setting-lookback").value),
          max_items_per_asset:Number(document.getElementById("setting-candidates").value),
          asset_news_interval_seconds:Number(document.getElementById("setting-asset-news-interval").value),
          alert_threshold: Number(document.getElementById("setting-threshold").value),
          news_translation_enabled: document.getElementById("setting-translation").checked,
          fundamentals_enabled: document.getElementById("setting-fundamentals").checked,
          movement_scope: document.getElementById("setting-movement-scope").value,
          analysis_mode: state.analysisMode,
          google_news_rss: document.getElementById("setting-rss").checked,
          yahoo_finance_rss: document.getElementById("setting-yahoo-rss").checked,
          bing_news_rss: document.getElementById("setting-bing-rss").checked,
        },
      });
      document.getElementById("settings-state").textContent = "刚刚保存";
      state.settingsDirty=false;
      showToast("设置已保存", "success");
      await refreshDashboard({ silent: true });
    } catch (error) { showToast(error.message, "error"); }
  });
}

function bindControls() {
  document.getElementById("restore-features").addEventListener("click", async event => {
    if (state.settingsDirty && !window.confirm("有未保存设置。恢复免费功能将重新加载已保存配置，是否继续？")) return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api("/api/features/restore", {method:"POST",body:{}});
      state.settingsDirty = false;
      renderFeatureHealth(result.features);
      showToast(`免费功能已开启 · 新建 ${result.research_drafts_created} 份研究草稿 · 未修改持仓`, "success");
      await refreshDashboard({silent:true});
    } catch (error) {showToast(error.message,"error");}
    finally {button.disabled=false;}
  });
  document.querySelectorAll("[data-ui-icon]").forEach(node => node.replaceChildren(uiIcon(node.dataset.uiIcon)));
  document.querySelectorAll(".close-dialog").forEach(button => {
    button.replaceChildren(uiIcon("x"));
    if (!button.hasAttribute("aria-label")) button.setAttribute("aria-label", button.title || "关闭");
  });
  document.querySelectorAll("[data-view-target]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.viewTarget)));
  document.getElementById("scan-button").addEventListener("click", runScan);
  document.getElementById("movement-scan-button").addEventListener("click", runMovementScan);
  document.getElementById("asset-refresh-button").addEventListener("click", runAssetRefresh);
  document.getElementById("korea-stock-refresh-button").addEventListener("click", runMovementScan);
  loadPortfolioSortPreferences();
  bindPulseSortControl();
  document.getElementById("add-position-button").addEventListener("click", openPositionDialog);
  document.getElementById("portfolio-refresh-button").addEventListener("click", refreshPortfolioQuotes);
  document.getElementById("portfolio-clear-button").addEventListener("click", () => removePortfolioPosition());
  document.getElementById("position-market").addEventListener("change", (event) => {
    const form = document.getElementById("position-form");
    form.elements.symbol.value = "";
    form.elements.name.value = "";
    form.elements.fund_type.value = "";
    updatePositionFormMarket(event.target.value);
  });
  document.getElementById("fund-search-button").addEventListener("click", searchFunds);
  document.getElementById("fund-search-query").addEventListener("input", (event) => {
    window.clearTimeout(state.fundSearchTimer);
    const results = document.getElementById("fund-search-results");
    if (event.target.value.trim().length < 2) {
      results.replaceChildren();
      return;
    }
    state.fundSearchTimer = window.setTimeout(searchFunds, 450);
  });
  document.getElementById("fund-search-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      window.clearTimeout(state.fundSearchTimer);
      searchFunds();
    }
  });
  document.getElementById("portfolio-sort-key").addEventListener("change", (event) => {
    state.portfolioSortKey = event.target.value;
    savePortfolioSortPreferences();
    if (state.dashboard) renderPortfolio(state.dashboard);
  });
  document.getElementById("portfolio-sort-direction").addEventListener("click", () => {
    state.portfolioSortDirection = state.portfolioSortDirection === "asc" ? "desc" : "asc";
    savePortfolioSortPreferences();
    if (state.dashboard) renderPortfolio(state.dashboard);
  });
  document.querySelectorAll("#portfolio-type-filter button").forEach((button) => {
    button.addEventListener("click", () => {
      state.portfolioTypeFilter = button.dataset.portfolioType;
      document.querySelectorAll("#portfolio-type-filter button").forEach((item) => item.classList.toggle("active", item === button));
      if (state.dashboard) renderPortfolio(state.dashboard);
    });
  });
  document.getElementById("portfolio-search").addEventListener("input", (event) => {
    state.portfolioSearch = event.target.value;
    if (state.dashboard) renderPortfolio(state.dashboard);
  });
  document.querySelectorAll("#portfolio-chart-modes button").forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.chartMode;
      if (!PORTFOLIO_CHART_CONFIG[mode] || mode === state.portfolioChartMode || button.disabled) return;
      state.portfolioChartMode = mode;
      state.portfolioChartPeriod = PORTFOLIO_CHART_CONFIG[mode].defaultPeriod;
      syncPortfolioChartPolling();
      syncPortfolioChartPeriodControls();
      loadPortfolioChart();
    });
  });
  document.getElementById("portfolio-chart-retry").addEventListener("click", () => loadPortfolioChart());
  document.getElementById("portfolio-chart-periods").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-chart-period]");
    if (!button || button.disabled) return;
    const period = button.dataset.chartPeriod;
    if (!PORTFOLIO_CHART_CONFIG[state.portfolioChartMode].periods[period] || period === state.portfolioChartPeriod) return;
      state.portfolioChartPeriod = period;
      syncPortfolioChartPeriodControls();
      loadPortfolioChart();
  });
  const portfolioChartCanvas = document.getElementById("portfolio-chart-canvas");
  document.getElementById("portfolio-chart-window").addEventListener("input", (event) => {
    state.portfolioChartWindowStart = Number(event.target.value) === Number(event.target.max) ? null : Number(event.target.value);
    state.portfolioChartHoverIndex = null;
    document.getElementById("portfolio-chart-tooltip").hidden = true;
    if (state.portfolioChartData) drawPortfolioChart(portfolioChartCanvas, state.portfolioChartData);
  });
  document.getElementById("portfolio-chart-latest-window").addEventListener("click", () => {
    state.portfolioChartWindowStart = null;
    state.portfolioChartHoverIndex = null;
    document.getElementById("portfolio-chart-tooltip").hidden = true;
    if (state.portfolioChartData) drawPortfolioChart(portfolioChartCanvas, state.portfolioChartData);
  });
  portfolioChartCanvas.addEventListener("mousemove", updatePortfolioChartTooltip);
  portfolioChartCanvas.addEventListener("mouseleave", hidePortfolioChartTooltip);
  document.getElementById("portfolio-chart-dialog").addEventListener("close", () => {
    if (document.getElementById("portfolio-chart-dialog").open) return;
    state.portfolioChartRequestId += 1;
    state.portfolioChartLoading = false;
    state.portfolioChartItem = null;
    state.portfolioChartData = null;
    state.portfolioChartHoverIndex = null;
    window.clearInterval(state.portfolioChartPoller);
    state.portfolioChartPoller = null;
    document.getElementById("portfolio-chart-tooltip").hidden = true;
  });
  window.addEventListener("resize", () => {
    if (!document.getElementById("portfolio-chart-dialog").open || !state.portfolioChartData) return;
    window.cancelAnimationFrame(state.portfolioChartResizeFrame);
    state.portfolioChartResizeFrame = window.requestAnimationFrame(() => {
      drawPortfolioChart(portfolioChartCanvas, state.portfolioChartData);
    });
  });
  document.querySelectorAll("#position-action-control button").forEach((button) => {
    button.addEventListener("click", () => setPositionAction(button.dataset.positionAction));
  });
  document.getElementById("asset-alert-toggle").addEventListener("change", (event) => toggleAssetAlerts(event.target.checked));
  document.getElementById("asset-alert-test-button").addEventListener("click", testAssetAlerts);
  document.getElementById("trump-refresh-button").addEventListener("click", runTrumpRefresh);
  document.getElementById("x-token-save-button").addEventListener("click", saveXToken);
  document.getElementById("x-token-clear-button").addEventListener("click", clearXToken);
  document.getElementById("auto-toggle").addEventListener("change", (event) => toggleAuto(event.target.checked));
  document.getElementById("manual-news-button").addEventListener("click", () => document.getElementById("manual-dialog").showModal());
  document.getElementById("add-stock-button").addEventListener("click", () => document.getElementById("stock-dialog").showModal());
  document.getElementById("news-search").addEventListener("input", applyNewsFilters);
  document.getElementById("news-sort").addEventListener("change", event => {
    state.newsSort = event.target.value;
    applyNewsFilters();
  });
  document.getElementById("connection-retry").addEventListener("click", () => refreshDashboard({ silent: true }));
  document.getElementById("news-language").addEventListener("change", event => {
    state.newsLanguage = event.target.value;
    state.liveNews = null;
    state.newsSignature = null;
    renderOverview();
    applyNewsFilters();
    refreshNews();
  });
  document.getElementById("market-filter").addEventListener("change", applyNewsFilters);
  document.querySelectorAll("#sentiment-filter button").forEach((button) => button.addEventListener("click", () => {
    state.sentimentFilter = button.dataset.value;
    document.querySelectorAll("#sentiment-filter button").forEach((item) => item.classList.toggle("active", item === button));
    applyNewsFilters();
  }));
  bindMovementSort();
  document.querySelectorAll("#movement-market-filter button").forEach((button) => button.addEventListener("click", () => {
    state.movementMarket = button.dataset.value;
    document.querySelectorAll("#movement-market-filter button").forEach((item) => item.classList.toggle("active", item === button));
    applyMovementFilters();
  }));
  document.querySelectorAll("#analysis-mode-control button").forEach((button) => button.addEventListener("click", () => {
    state.analysisMode = button.dataset.value;
    state.settingsDirty=true;
    document.getElementById("settings-state").textContent="有未保存更改";
    document.querySelectorAll("#analysis-mode-control button").forEach((item) => item.classList.toggle("active", item === button));
  }));
  document.getElementById("setting-threshold").addEventListener("input", (event) => {
    document.getElementById("threshold-output").value = Number(event.target.value).toFixed(2);
  });
}

function updateClock() {
  document.getElementById("local-clock").textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date());
}

async function init() {
  bindDialogs();
  bindForms();
  bindControls();
  if (typeof initWorkbench === "function") initWorkbench();
  if (typeof initIndexMonitor === "function") initIndexMonitor();
  document.querySelectorAll("[data-instrument-target]").forEach(title => title.after(instrumentBriefNode(title.dataset.instrumentTarget)));
  const positionForm = document.getElementById("position-form");
  positionForm.addEventListener("input", positionFormBrief);
  positionForm.addEventListener("change", positionFormBrief);
  document.getElementById("manual-watch-select").addEventListener("change", event => briefAfterTitle("manual-watch-select", event.target.value));
  updateClock();
  window.setInterval(updateClock, 1000);
  if (typeof restoreStartupMemory === "function") await restoreStartupMemory();
  loadCompanyBriefs();
  refreshInstrumentEarnings();
  refreshDashboard();
  refreshAssets();
  refreshNews();
  if (state.currentView === "research") refreshResearch({silent:true});
  if (state.currentView === "social") refreshSocial({silent:true});
  if (state.currentView === "settings") refreshFeatureHealth();
  if (state.currentView === "indices" && typeof refreshIndices === "function") refreshIndices();
  window.setInterval(() => {
    if (document.visibilityState === "visible") refreshInstrumentEarnings();
  }, 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshInstrumentEarnings();
  });
  window.setInterval(() => {
    if (document.visibilityState === "visible" && ["overview", "signals", "feed", "movements", "alerts"].includes(state.currentView)) refreshNews();
  }, 1000);
  window.setInterval(() => {
    if (document.visibilityState === "visible" && !state.scanPoller) refreshDashboard({ silent: true });
    if (document.visibilityState === "visible" && state.currentView === "indices" && typeof refreshIndices === "function") refreshIndices({silent: true});
  }, 15000);
  window.setInterval(() => {
    if (!state.assetPoller) refreshAssets({ silent: true });
  }, ASSET_UI_POLL_MS);
  state.koreaStockPoller = window.setInterval(() => {
    if (document.visibilityState === "visible" && state.currentView === "korea-stocks") {
      refreshKoreaStocks({ silent: true });
    }
  }, KOREA_STOCK_UI_POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
