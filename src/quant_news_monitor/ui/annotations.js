/* Optional UI extension. Load after app.js; no requests, timers, or app-state reads. */
(() => {
  "use strict";
  if (window.StockAnnotations) return;

  const guide = "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/";
  const terms = {
    MA5: {
      title: "MA5 · 5 期简单均线",
      definition: "最近 5 根 K 线收盘值的算术平均；基金使用确认净值。5 期跟随图表粒度，不一定是 5 天。",
      limitation: "均线滞后于价格，短均线容易受噪声影响。仅凭当前值不能确认刚发生穿越，也不能确定支撑有效。",
      url: guide + "sma",
    },
    MA20: {
      title: "MA20 · 20 期简单均线",
      definition: "最近 20 根 K 线收盘值的算术平均；基金使用确认净值。20 期跟随图表粒度，不一定是 20 天。",
      limitation: "平滑程度高于 MA5，但反应也更慢。震荡中容易反复穿越；当前相对位置不能证明均线正在上升。",
      url: guide + "sma",
    },
    RSI14: {
      title: "RSI14 · 相对强弱指标",
      definition: "比较收盘值上涨与下跌幅度的 14 期 Wilder 平滑均值，数值范围 0 至 100；它不是相对其他股票的排名。",
      limitation: "70 / 30 是常见参考阈值，不是买卖指令。强趋势下可长期处于高位或低位；单点数值不能确认背离或反转。",
      url: guide + "RSI",
    },
    MACD: {
      title: "MACD 柱 · 动量差值",
      definition: "本图 MACD 线为 EMA12 减 EMA26，信号线为该差值的 EMA9；柱值等于 MACD 线减信号线，本实现不乘 2。",
      limitation: "仅凭正负不能判断柱在放大还是缩小，也不代表价格必然涨跌。不同软件可能乘 2；绝对大小不宜跨标的比较。",
      url: guide + "macd",
    },
    VWAP: {
      title: "VWAP · 成交量加权均价",
      definition: "本图以每根 K 线的（最高 + 最低 + 收盘）/ 3 乘成交量后累加，再除以累计成交量；按行情日期重置。",
      limitation: "仅用于有成交量的分钟图。缺失成交量、盘前盘后范围和日期边界会影响结果；这是 K 线近似值，不是逐笔成交均价或你的持仓成本。",
      url: "https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/",
    },
  };
  const sourceSelector = "a.research-evidence-link, a.asset-news-link, a#detail-link, a#movement-detail-link, a[data-source-url], #trump-posts .trump-post-score a";
  const sourceRoots = '[data-view="research"], #research-detail-body, #detail-dialog, #movement-dialog, .asset-news-list, #trump-posts';
  const observedSources = new WeakSet();
  const sourceDirectories = new WeakMap();
  // BBC/CNBC correspond to PUBLIC_POLICY_FEEDS; White House is manual verification only.
  const referenceSites = [
    {name: "Yahoo Finance", url: "https://finance.yahoo.com/", kind: "媒体 / 行情服务", note: "现有 Yahoo RSS 渠道对应网站"},
    {name: "BBC News", url: "https://www.bbc.com/news", kind: "二手媒体 / 政策报道", note: "后端 BBC 公开政策 RSS 对应网站"},
    {name: "CNBC", url: "https://www.cnbc.com/", kind: "二手媒体 / 财经报道", note: "后端 CNBC 公开政策 RSS 对应网站"},
    {name: "白宫 · 公开政策", url: "https://www.whitehouse.gov/presidential-actions/", kind: "官方政策 / 一手来源", note: "手动核验政策原文；不代表已接通自动采集"},
    {name: "Google 新闻", url: "https://news.google.com/", kind: "新闻聚合入口", note: "现有 Google RSS 渠道对应网站"},
    {name: "Bing 新闻", url: "https://www.bing.com/news", kind: "新闻聚合入口", note: "现有 Bing RSS 渠道对应网站"},
    {name: "SEC EDGAR", url: "https://www.sec.gov/edgar/search/", kind: "官方披露 / 监管", note: "公司原始申报检索"},
    {name: "iShares · IGV", url: "https://www.ishares.com/us/products/239771/ishares-north-american-techsoftware-etf", kind: "官方资料 / 基金发行方", note: "现有 IGV 研究的基金资料与持仓入口"},
    {name: "S&P Dow Jones Indices", url: "https://www.spglobal.com/spdji/en/indices/equity/sp-north-american-expanded-technology-software-index-custom/", kind: "官方资料 / 指数机构", note: "现有 IGV 研究的指数依据"},
  ];
  let chart = null;
  let activeKey = null;
  let activeTrigger = null;
  let restoringFocus = false;

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function read(id) {
    return document.getElementById(id)?.textContent.trim() || "";
  }

  function numberFromDisplay(text) {
    // Only parse the chart's number/currency formats, never arbitrary prose or placeholders.
    const valueText = String(text).trim().replace(/\u2212/g, "-").replace(/^([+-]?)(?:HK\$|[¥￥$₩€£]|[A-Z]{3,5}\s*)\s*/, "$1");
    const match = valueText.match(/^([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s*(?:[A-Z]{3,5}|%|元))?$/);
    if (!match) return null;
    const value = Number(match[1].replace(/,/g, ""));
    return Number.isFinite(value) ? value : null;
  }

  function keyOf(trigger) {
    const label = (trigger?.dataset.annotation || trigger?.querySelector("span")?.textContent || "").replace(/\s/g, "").toUpperCase();
    if (label === "MACD柱" || label === "MACD") return "MACD";
    return Object.hasOwn(terms, label) ? label : null;
  }

  function interpretation(key, display) {
    const value = numberFromDisplay(display);
    if (value === null) return "当前数值缺失或格式不可识别，无法判断方向；不将缺失值当作 0。";
    if (key === "RSI14") {
      if (value < 0 || value > 100) return "当前值超出 RSI 的 0 至 100 范围，需核对数据，暂不解释方向。";
      if (value >= 70) return "当前位于常用的高位区（70 及以上），反映近期上涨幅度相对较强；不等于马上下跌。";
      if (value <= 30) return "当前位于常用的低位区（30 及以下），反映近期下跌幅度相对较强；不等于马上反弹。";
      return "当前处于 30 至 70 之间，未进入常用的高低极值区；中间区间也不能单独确认趋势。";
    }
    if (key === "MACD") return value === 0
      ? "按显示精度，MACD 线与信号线的差为 0；四舍五入可能隐藏微小差异，不能据此确认交叉。"
      : `柱值为${value > 0 ? "正" : "负"}，表示 MACD 线在信号线${value > 0 ? "上方" : "下方"}；这不等于 MACD 线本身在零轴同一侧。`;
    const latestText = read("portfolio-chart-latest");
    const latest = numberFromDisplay(latestText);
    if (latest === null) return "指标已有数值，但图表最新值缺失，暂不能比较相对位置。";
    const relation = latest > value ? "高于" : latest < value ? "低于" : "按显示精度等于";
    return `图表最新值 ${latestText} ${relation} ${key}（${display}）。这只说明当前值与${key === "VWAP" ? "当日量价基准" : "历史均值"}的相对位置，不表示之后的方向。`;
  }

  function externalLink(link, url) {
    if (link.getAttribute("href") !== url) link.setAttribute("href", url);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.referrerPolicy = "no-referrer";
    link.removeAttribute("ping");
  }

  function close() {
    if (!chart || chart.panel.hidden) return;
    const returnFocus = chart.panel.contains(document.activeElement);
    chart.panel.hidden = true;
    chart.host.classList.remove("stock-annotation-open");
    activeTrigger?.setAttribute("aria-expanded", "false");
    activeTrigger?.removeAttribute("aria-describedby");
    if (returnFocus && activeTrigger?.isConnected) {
      restoringFocus = true;
      activeTrigger.focus({preventScroll: true});
      restoringFocus = false;
    }
    activeTrigger = null;
    activeKey = null;
  }

  function show(trigger) {
    const key = keyOf(trigger);
    if (!chart || !key || restoringFocus) return;
    if (activeTrigger !== trigger) {
      activeTrigger?.setAttribute("aria-expanded", "false");
      activeTrigger?.removeAttribute("aria-describedby");
    }
    activeTrigger = trigger;
    activeKey = key;
    const term = terms[key];
    const display = trigger.querySelector("[data-annotation-value], strong")?.textContent.trim() || "--";
    const copy = {
      title: term.title,
      value: `当前显示值：${display}`,
      definition: term.definition,
      interpretation: interpretation(key, display),
      limitation: term.limitation,
      context: [read("portfolio-chart-title"), read("portfolio-chart-symbol"), read("portfolio-chart-interval"), read("portfolio-chart-note")].filter(Boolean).join(" · "),
    };
    for (const [name, text] of Object.entries(copy)) {
      if (chart[name].textContent !== text) chart[name].textContent = text;
    }
    const referenceText = key === "VWAP" ? "TradingView 官方原理" : "Fidelity 官方原理";
    if (chart.reference.textContent !== referenceText) chart.reference.textContent = referenceText;
    externalLink(chart.reference, term.url);
    chart.panel.hidden = false;
    chart.host.classList.add("stock-annotation-open");
    trigger.setAttribute("aria-expanded", "true");
    trigger.setAttribute("aria-describedby", "stock-annotation-definition stock-annotation-interpretation");
  }

  function refreshIndicators() {
    if (!chart) return;
    let replacement = null;
    chart.strip.querySelectorAll(".portfolio-indicator, [data-annotation]").forEach(trigger => {
      const key = keyOf(trigger);
      if (!key) return;
      trigger.classList.add("stock-annotation-trigger");
      if (!trigger.matches("button, a[href], input, select, textarea")) {
        trigger.tabIndex = 0;
        trigger.setAttribute("role", "button");
      }
      trigger.setAttribute("aria-controls", "stock-indicator-annotation");
      trigger.setAttribute("aria-expanded", String(key === activeKey));
      trigger.setAttribute("title", `${key}：定义、当前值与限制`);
      if (key === activeKey) replacement = trigger;
    });
    if (activeKey) replacement ? show(replacement) : close();
  }

  function initChart() {
    const strip = document.getElementById("portfolio-indicator-strip");
    const host = document.getElementById("portfolio-technical-panel");
    if (chart || !strip || !host) return;
    const panel = node("aside", "stock-annotation");
    panel.id = "stock-indicator-annotation";
    panel.hidden = true;
    panel.setAttribute("aria-labelledby", "stock-annotation-title");
    const header = node("header", "stock-annotation-header");
    const title = node("h4");
    title.id = "stock-annotation-title";
    const dismiss = node("button", "stock-annotation-close");
    dismiss.type = "button";
    dismiss.title = "关闭评注";
    dismiss.setAttribute("aria-label", "关闭指标评注");
    if (typeof uiIcon === "function") dismiss.append(uiIcon("x"));
    else dismiss.textContent = "关闭";
    header.append(title, dismiss);
    panel.append(header);
    chart = {strip, host, panel, title};
    for (const [name, label] of [["value", ""], ["definition", "定义"], ["interpretation", "当前值解读"], ["limitation", "限制"], ["context", "图表依据"]]) {
      if (label) panel.append(node("h5", "", label));
      chart[name] = node("p", `stock-annotation-${name}`);
      chart[name].id = `stock-annotation-${name}`;
      panel.append(chart[name]);
    }
    panel.append(node("p", "stock-annotation-disclaimer", "以上是历史量价描述，不是上涨概率或买卖建议。显示值已四舍五入，时间与数据源以当前图表为准。"));
    chart.reference = node("a", "stock-annotation-reference");
    panel.append(chart.reference);
    strip.after(panel);
    dismiss.addEventListener("click", close);
    const triggerFor = event => event.target.closest?.(".stock-annotation-trigger");
    strip.addEventListener("pointerover", event => {
      const trigger = triggerFor(event);
      // Pointer travel must not replace a keyboard-selected annotation en route to its close button.
      if (strip.contains(document.activeElement) || panel.contains(document.activeElement)) return;
      if (event.pointerType !== "touch" && trigger && !trigger.contains(event.relatedTarget)) show(trigger);
    });
    strip.addEventListener("focusin", event => { const trigger = triggerFor(event); if (trigger) show(trigger); });
    strip.addEventListener("click", event => { const trigger = triggerFor(event); if (trigger) show(trigger); });
    strip.addEventListener("keydown", event => {
      const trigger = triggerFor(event);
      if (trigger && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); show(trigger); }
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !panel.hidden) {
        event.preventDefault();
        event.stopImmediatePropagation();
        close();
      }
    }, true);
    const dialog = strip.closest("dialog");
    dialog?.addEventListener("close", close);
    // Observe only the indicator strip and the exact chart labels used by the explanation.
    const observer = new MutationObserver(refreshIndicators);
    for (const target of [strip, ...["portfolio-chart-latest", "portfolio-chart-interval", "portfolio-chart-note", "portfolio-chart-title", "portfolio-chart-symbol"].map(id => document.getElementById(id))]) {
      if (target) observer.observe(target, {childList: true, characterData: true, subtree: true});
    }
    refreshIndicators();
  }

  function safeURL(value) {
    if (typeof value !== "string" || !/^https?:\/\//i.test(value.trim())) return null;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && url.hostname && !url.username && !url.password ? url : null;
    } catch (_) { return null; }
  }

  function sourceInfo(value) {
    let url = safeURL(value);
    if (!url) return null;
    // Bing already exposes a publisher URL in some RSS links; decoding needs no network.
    if (["bing.com", "www.bing.com"].includes(url.hostname)) url = safeURL(url.searchParams.get("url")) || url;
    const domain = url.hostname.replace(/^www\./, "");
    const aggregate = domain === "news.google.com" || domain === "bing.com";
    return {url: url.href, domain, kind: aggregate ? "聚合入口，发布方原文未解析" : "来源网页"};
  }

  function sourceCategory(domain) {
    const official = [["sec.gov", "官方披露 / 监管"], ["whitehouse.gov", "官方政策 / 一手来源"], ["ishares.com", "官方资料 / 基金发行方"], ["ssga.com", "官方资料 / 基金发行方"], ["wisdomtree.com", "官方资料 / 基金发行方"], ["globalxetfs.com", "官方资料 / 基金发行方"], ["spglobal.com", "官方资料 / 指数机构"]];
    const match = official.find(([host]) => domain === host || domain.endsWith(`.${host}`));
    if (match) return match[1];
    if (["news.google.com", "bing.com"].includes(domain)) return "新闻聚合入口";
    if (["finance.yahoo.com", "reuters.com", "bbc.com", "bbc.co.uk", "cnbc.com", "bloomberg.com", "ft.com", "wsj.com"].some(host => domain === host || domain.endsWith(`.${host}`))) return "二手媒体 / 资讯服务";
    return "来源网站 / 类型待核验";
  }

  function enhanceLink(link) {
    const info = sourceInfo(link.getAttribute("href"));
    let caption = link.querySelector(":scope > .stock-source-domain");
    if (!info) {
      caption?.remove();
      if (link.hasAttribute("href")) {
        link.removeAttribute("href");
        link.setAttribute("aria-disabled", "true");
        link.title = "来源地址无效，无法打开";
      }
      return;
    }
    externalLink(link, info.url);
    link.removeAttribute("aria-disabled");
    if (!caption) { caption = node("span", "stock-source-domain"); link.append(caption); }
    if (caption.textContent !== info.domain) caption.textContent = info.domain;
    // The policy-feed action column is narrow; keep provenance to one bounded line.
    link.style.maxWidth = "100%";
    Object.assign(caption.style, {maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis"});
    link.title = `${sourceCategory(info.domain)} · 打开${info.kind}：${info.url}`;
  }

  function enhanceSources(root) {
    if (!root?.querySelectorAll) return;
    if (root.matches?.(sourceSelector)) enhanceLink(root);
    root.querySelectorAll(sourceSelector).forEach(enhanceLink);
  }

  function sourceHost() {
    let host = document.getElementById("annotation-source-sites");
    if (!host) {
      const inputs = document.getElementById("research-inputs-panel");
      if (!inputs) return null;
      host = node("section", "stock-source-sites");
      host.id = "annotation-source-sites";
      inputs.prepend(host);
    }
    return host;
  }

  function createSourceDirectory(host) {
    host.classList.add("stock-source-sites");
    const heading = node("h4", "", "媒体与研究网站");
    heading.tabIndex = -1;
    const content = node("div", "stock-source-list");
    const records = node("div");
    const empty = node("p", "", "当前记录没有可打开的来源链接；下列网站仅供手动核验。");
    content.append(node("h5", "", "当前记录中的来源"), records, empty);
    content.append(node("h5", "", "参考网站 · 手动打开"));
    content.append(node("p", "", "网站入口不代表已开启采集或连接成功。当前记录的原文链接与下列参考入口分别列出。"));
    const references = node("ul", "stock-reference-sites");
    for (const site of referenceSites) {
      const row = node("li");
      const link = node("a", "", site.name);
      externalLink(link, site.url);
      link.title = `手动打开参考网站：${site.url}`;
      row.append(link, node("span", "stock-source-domain", new URL(site.url).hostname.replace(/^www\./, "")), node("small", "", `${site.kind} · 手动核验 · ${site.note}`));
      references.append(row);
    }
    content.append(references);
    host.replaceChildren(heading, content);
    const directory = {groups: new Map(), heading, records, empty};
    sourceDirectories.set(host, directory);
    return directory;
  }

  function visibleFocus(root) {
    const focused = document.activeElement;
    return root.contains(focused) && focused.getClientRects().length ? focused : null;
  }

  function restoreSourceFocus(focused, fallback) {
    if (!focused) return;
    const target = focused.isConnected && focused.getClientRects().length ? focused : fallback;
    if (target?.getClientRects().length && document.activeElement !== target) target.focus({preventScroll: true});
  }

  function reconcileSourceNodes(parent, children) {
    const desired = new Set(children);
    for (const child of [...parent.children]) if (!desired.has(child)) child.remove();
    children.forEach((child, index) => {
      if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
    });
  }

  function renderSourceGroup(entry, focused = visibleFocus(entry.group)) {
    const {group, summary, list, more, articles, limit, rows} = entry;
    const visible = group.open ? articles.slice(0, limit) : [];
    // Keep a still-valid focused article visible even if newer records push it past the loaded window.
    if (group.open && list.contains(focused)) {
      const retained = articles.find(article => article.info.url === focused.getAttribute("href"));
      if (retained && !visible.includes(retained)) visible.push(retained);
    }
    const retainedURLs = new Set(visible.map(article => article.info.url));
    for (const url of rows.keys()) if (!retainedURLs.has(url)) rows.delete(url);
    const children = visible.map(({info, title, translation, name}) => {
      let row = rows.get(info.url);
      if (!row) {
        row = node("li");
        const link = node("a");
        externalLink(link, info.url);
        link.title = `打开${info.kind}：${info.url}`;
        row.append(link, node("small"));
        rows.set(info.url, row);
      }
      const caption = `${name} · ${sourceCategory(info.domain)} · 记录原文链接 · ${info.kind}`;
      if (row.firstElementChild.textContent !== title + translation) {
        row.firstElementChild.textContent = title;
        if (translation) row.firstElementChild.append(node("span", "news-translation", translation));
      }
      if (row.lastElementChild.textContent !== caption) row.lastElementChild.textContent = caption;
      return row;
    });
    reconcileSourceNodes(list, children);
    const remaining = articles.length - visible.length;
    more.hidden = !group.open || remaining <= 0;
    const label = `加载更多（${Math.min(20, Math.max(0, remaining))} 条）`;
    if (more.textContent !== label) more.textContent = label;
    more.setAttribute("aria-label", `${group.dataset.sourceDomain}：${label}，已显示 ${visible.length} / ${articles.length} 条`);
    restoreSourceFocus(focused, summary);
  }

  function createSourceGroup(domain) {
    const group = node("details", "stock-source-group");
    group.dataset.sourceDomain = domain;
    const summary = node("summary");
    const list = node("ul");
    const more = node("button", "secondary-button stock-source-more");
    more.type = "button";
    more.hidden = true;
    group.append(summary, list, more);
    const entry = {group, summary, list, more, articles: [], limit: 10, rows: new Map()};
    group.addEventListener("toggle", () => renderSourceGroup(entry));
    more.addEventListener("click", () => {
      entry.limit += 20;
      renderSourceGroup(entry);
    });
    return entry;
  }

  function renderSources(items, host = sourceHost()) {
    if (!host) return;
    const directory = sourceDirectories.get(host) || createSourceDirectory(host);
    const focused = visibleFocus(host);
    const focusedDomain = focused?.closest(".stock-source-group")?.dataset.sourceDomain;
    const groups = new Map();
    const seen = new Set();
    for (const item of Array.isArray(items) ? items : []) {
      const info = sourceInfo(item?.url || item?.source_url);
      if (!info || seen.has(info.url)) continue;
      seen.add(info.url);
      if (!groups.has(info.domain)) groups.set(info.domain, []);
      groups.get(info.domain).push({info, name: String(item.source || item.publisher || info.domain), title: String(item.title || item.title_zh || "打开来源网页"), translation: typeof translationLabel === "function" ? translationLabel(item) : ""});
    }
    for (const domain of directory.groups.keys()) if (!groups.has(domain)) directory.groups.delete(domain);
    const children = [];
    for (const [domain, articles] of groups) {
      let entry = directory.groups.get(domain);
      if (!entry) { entry = createSourceGroup(domain); directory.groups.set(domain, entry); }
      entry.articles = articles;
      const aggregateName = domain === "news.google.com" ? "Google 新闻聚合 · " : domain === "bing.com" ? "Bing 新闻聚合 · " : "";
      const label = `${aggregateName}${domain} · ${articles.length} 条`;
      if (entry.summary.textContent !== label) entry.summary.textContent = label;
      renderSourceGroup(entry, focusedDomain === domain ? focused : null);
      children.push(entry.group);
    }
    reconcileSourceNodes(directory.records, children);
    directory.empty.hidden = groups.size > 0;
    restoreSourceFocus(focused, directory.groups.get(focusedDomain)?.summary || children[0]?.firstElementChild || directory.heading);
  }

  function init() {
    initChart();
    document.querySelectorAll(sourceRoots).forEach(root => {
      if (observedSources.has(root)) return;
      observedSources.add(root);
      enhanceSources(root);
      const observer = new MutationObserver(records => {
        for (const record of records) {
          if (record.type === "attributes") enhanceSources(record.target);
          else {
            if (record.target.matches?.(sourceSelector)) enhanceLink(record.target);
            record.addedNodes.forEach(added => enhanceSources(added));
          }
        }
      });
      observer.observe(root, {childList: true, subtree: true, attributes: true, attributeFilter: ["href"]});
    });
    const host = sourceHost();
    if (host && !sourceDirectories.has(host)) renderSources([], host);
  }

  window.StockAnnotations = Object.freeze({init, refreshIndicators, enhanceSources, renderSources, close});
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})();
