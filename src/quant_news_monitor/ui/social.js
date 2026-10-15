"use strict";

const socialState = { data: null, hours: 24, platform: "all", selected: null, request: 0, pending: false, pendingKey: "", busy: false, sort: "heat", settingsDirty: false, readFailed: false };
const socialPlatforms = { xiaohongshu: "小红书", douyin: "抖音", x: "X", threads: "Threads", valuepickr: "ValuePickr", tradingqna: "Trading Q&A", hackernews: "Hacker News" };
const socialWebState = { busy: false };
const socialSampleState = {scope:"all", query:"", timer:null};
const socialKeywordState = {busy:false, drafts:new Map()};
let socialView = "discussion";
let socialScope = "discovery";

function socialRankingData() {
  const data = socialState.data;
  return socialScope === "discovery" && data?.discovery ? {...data, ...data.discovery} : data;
}

function setSocialView(view) {
  socialView = view === "trump" ? "trump" : "discussion";
  document.getElementById("social-discussion-panel").hidden=socialView!=="discussion";
  document.getElementById("social-trump-panel").hidden=socialView!=="trump";
  document.querySelectorAll("#social-view-tabs button").forEach(button=>{
    const active=button.dataset.socialView===socialView;
    button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));
  });
}
const socialStatuses = { connected: "已连接", partial: "部分可用", not_configured: "待接入", waiting: "待验证", error: "连接异常", stale: "数据过期", blocked: "访问受限" };
const socialFailureStages = {network:"网络连接",access_rules:"访问规则",access:"访问权限",rate_limit:"来源限流",parse:"内容解析",storage:"数据保存",analysis:"情绪分析",request:"来源请求"};

function socialDiagnosticLines(source) {
  const details = source.diagnostics || {};
  const health = source.health || {};
  const lines = [];
  if (details.query_total) lines.push(`本轮查询 ${details.query_completed}/${details.query_total} 已完成${details.query_pending ? ` · ${details.query_pending} 项待续采` : ""}`);
  if (source.last_received_at) lines.push(`最近收到帖子 ${formatDate(source.last_received_at, true)}`);
  if (details.query_retry_at) lines.push(`失败查询重试不早于 ${formatDate(details.query_retry_at, true)}`);
  if (health.latest_error) {
    const recovered = ["connected", "partial"].includes(source.status) && !details.latest_error && Date.parse(source.checked_at) > Date.parse(health.latest_error_at);
    lines.push(`${recovered ? "上次异常（已恢复）" : "最近异常"} · ${socialFailureStages[health.failure_stage] || "来源请求"}${health.latest_error_at ? ` · ${formatDate(health.latest_error_at, true)}` : ""}：${health.latest_error}`);
  }
  return lines;
}
const socialConnections = {
  xiaohongshu: ["等待授权的舆情数据源。也可导入已取得的公开帖子。", "https://open.xiaohongshu.com/"],
  douyin: ["等待授权数据源；抖音关键词视频搜索需要平台专项权限。", "https://open.douyin.com/platform/resource/docs/accession-guide/type-and-permission"],
  x: ["官方搜索需要 X Bearer Token 与读取权限。与 Trump 模块共用令牌，平台读取可能计费。", "https://docs.x.com/x-api/posts/search/introduction"],
  threads: ["官方搜索需要 Threads Access Token 和 threads_keyword_search 权限。", "https://developers.facebook.com/docs/threads/keyword-search/"],
};

async function refreshSocial({ silent = false } = {}) {
  const params = new URLSearchParams({hours:socialState.hours, platform:socialState.platform, sample_scope:socialSampleState.scope, sample_query:socialSampleState.query});
  const key=params.toString();
  if(silent && socialState.pending && socialState.pendingKey===key)return false;
  const request = ++socialState.request;
  socialState.pending=true;socialState.pendingKey=key;
  try {
    const data = await api(`/api/social?${params}`);
    if (request !== socialState.request) return;
    socialState.data = data;
    socialState.readFailed = false;
    renderSocial();
    return true;
  } catch (error) {
    if (request !== socialState.request) return;
    socialState.readFailed = true;
    document.getElementById("social-health").dataset.status = "error";
    document.getElementById("social-status-title").textContent = "后台状态读取失败";
    document.getElementById("social-notice").textContent = `情绪数据暂时不可用：${error.message}`;
    document.getElementById("social-sync").textContent = "读取失败";
    document.getElementById("social-refresh-button").disabled = true;
    renderSocialConnectionStatus();
    document.getElementById("social-web-save").disabled = true;
    document.getElementById("social-keyword-save").disabled = true;
    if (!silent) showToast(error.message, "error");
    return false;
  } finally {
    if(request===socialState.request)socialState.pending=false;
  }
}

function socialNumber(value, suffix = "") {
  return value == null ? "--" : `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${suffix}`;
}

function renderSocial() {
  const data = socialState.data;
  if (!data) return;
  const sources = document.getElementById("social-sources");
  const webSources = document.getElementById("social-web-sources");
  sources.replaceChildren(); webSources.replaceChildren();
  data.sources.forEach((source) => {
    const button = el("button", `social-source ${source.key}`);
    button.type = "button";
    button.title = `${source.name}：${source.message}${source.checked_at ? ` · 最近请求 ${formatDate(source.checked_at)}` : ""}`;
    const identity = el("div", "social-source-identity");
    identity.append(el("span", "social-platform-mark", source.mode === "web" ? source.name[0] : source.key === "xiaohongshu" ? "红" : source.key === "douyin" ? "抖" : source.key === "x" ? "X" : "@"), el("strong", "", source.name));
    const status = el("span", `social-source-status ${source.status}`, source.mode === "web" && !source.configured ? "未启用" : socialStatuses[source.status] || "未知");
    button.append(identity, status,
      el("small", "social-source-message", source.message),
      el("small", "", `${source.mode === "web" ? source.format === "search" ? "公开社区检索" : "公开 RSS 样本" : source.mode === "api" ? "官方搜索" : "授权数据源"} · 当前窗口 ${source.window_post_count} 条 · 匹配 ${source.health?.matched_count ?? 0} 条`),
      el("small", "", source.last_success_at ? `最近成功 ${formatDate(source.last_success_at, true)}` : "尚无成功采集"));
    if(source.health?.attempts_24h)button.append(el("small","",`24 小时完整成功 ${source.health.successes_24h}/${source.health.attempts_24h} · 部分完成 ${source.health.partial_count_24h || 0} 次${source.health.consecutive_failures ? ` · 连续失败 ${source.health.consecutive_failures} 次` : ""}`));
    socialDiagnosticLines(source).forEach(line => button.append(el("small", "social-source-diagnostic", line)));
    if (source.retry_at) button.append(el("small", "negative", `重试不早于 ${formatDate(source.retry_at, true)}`));
    else if (source.next_request_at) button.append(el("small", "", `下次采样 ${formatDate(source.next_request_at, true)} · ${intervalLabel(source.interval_seconds)}`));
    if (source.mode === "web") button.append(el("small", "", source.scope));
    button.addEventListener("click", () => source.mode === "web" ? openSocialWebSettings() : openSocialSettings(source.key));
    (source.mode === "web" ? webSources : sources).append(button);
  });
  renderSocialHealth(data);
  const selectedSources = data.sources.filter(s => data.platform === "all" || s.key === data.platform);
  const hasObservation = data.summary.collected_count > 0 || selectedSources.some(s => s.status === "connected");
  const ranking = socialRankingData();
  document.getElementById("social-total").textContent = socialNumber(hasObservation ? ranking.summary.post_count : null);
  document.getElementById("social-received").textContent = `已采集 ${socialNumber(data.summary.collected_count)} 条 · 明确观点去重`;
  document.getElementById("social-discussed").textContent = socialNumber(hasObservation ? ranking.summary.discussed_count : null);
  document.getElementById("social-cold-count").textContent = socialNumber(ranking.stocks.some(s => s.covered_platforms.length) ? ranking.summary.cold_count : null);
  document.getElementById("social-unknown").textContent = socialNumber(ranking.summary.unknown_count);
  document.getElementById("social-scope").textContent = `${ranking.summary.stock_count} 只${socialScope === "discovery" ? "股票池外候选" : "监测股票"}`;
  document.getElementById("social-method").textContent = ranking.method;
  document.getElementById("social-ranking-title").textContent = socialScope === "discovery" ? "新机会候选" : "股票池热议";
  const catalog = data.discovery?.catalog;
  document.getElementById("social-discovery-status").textContent = socialScope === "discovery"
    ? `${catalog?.scope || "证券目录尚未就绪"} · ${catalog?.symbol_count || 0} 个已核验代码${catalog?.error ? ` · ${catalog.error}` : ""}` : "当前股票池的讨论与情绪";
  renderSocialRankings();
  renderSocialConnectionStatus();
  renderSocialWebSamples(data);
  if(data.trump)renderTrump({trump:data.trump});
  document.getElementById("social-web-save").disabled = socialWebState.busy || socialState.readFailed;
  document.getElementById("social-keyword-save").disabled = socialKeywordState.busy || socialState.readFailed;
  const webCount = data.sources.filter(s => s.mode === "web").length;
  const webStatus = `公开社区 ${data.runtime.web_connected_count}/${webCount} 完整可用${data.runtime.web_partial_count ? ` · ${data.runtime.web_partial_count} 个部分可用` : ""}`;
  document.getElementById("social-web-sync").textContent = webStatus;
  document.getElementById("social-platform-status").textContent = `${data.runtime.platform_connected_count}/4 已连接`;
  document.getElementById("social-web-settings-status").textContent = `${webStatus} · 科技检索 60 秒，论坛 ${intervalLabel(data.runtime.web_interval_seconds)}${data.runtime.refresh_in_progress ? " · 正在采集" : ""}${!data.runtime.enabled ? " · 社交自动采集已暂停" : ""}`;
  const diagnostics = document.getElementById("social-web-diagnostics");
  diagnostics.replaceChildren();
  data.sources.filter(source => source.mode === "web" && source.configured).forEach(source => {
    const row = el("div", "social-connection-status");
    row.append(el("strong", "", `${source.name} · ${socialStatuses[source.status]}`), el("span", "", source.message));
    socialDiagnosticLines(source).forEach(line => row.append(el("small", "", line)));
    diagnostics.append(row);
  });
}

function renderSocialHealth(data) {
  const runtime = data.runtime;
  if (data._startup_cache) {
    document.getElementById("social-health").dataset.status = "stale";
    document.getElementById("social-status-title").textContent = "上次讨论快照";
    document.getElementById("social-notice").textContent = `快照保存于 ${formatDate(data._startup_cache, true)}；连接状态和讨论排名正在后台重新确认。`;
    document.getElementById("social-sync").textContent = "正在读取最新记录";
    document.getElementById("social-refresh-button").disabled = true;
    document.getElementById("social-connect-button").hidden = false;
    return;
  }
  const titles = { not_configured: "自动采集尚未接通", collecting: "正在采集社交讨论", waiting: "连接已配置，等待验证", paused: "自动采集已暂停", connected: "社交数据采集正常", partial: "社交采集部分可用", error: "社交采集异常", stale: "社交采集结果已过期" };
  const health = document.getElementById("social-health");
  health.dataset.status = runtime.state;
  document.getElementById("social-status-title").textContent = titles[runtime.state] || "采集状态待确认";
  const windowLabel = data.hours === 168 ? "近 7 天" : `近 ${data.hours} 小时`;
  let message = `公开社区 ${runtime.web_connected_count}/${data.sources.filter(s => s.mode === "web").length} 可用，原四平台 ${runtime.platform_connected_count}/4 已连接；${windowLabel}收到 ${data.summary.collected_count} 条帖子，股票池内 ${data.summary.post_count} 条、池外候选 ${data.discovery?.summary.post_count || 0} 条去重讨论。`;
  if (runtime.web_partial_count) message += ` 另有 ${runtime.web_partial_count} 个公开来源部分返回，剩余查询将自动续采。`;
  if (!runtime.configured_count) message = "网页采集未启用，原四个平台也尚未接通。";
  if (runtime.last_error) message += ` ${runtime.last_error}。`;
  if (data.summary.imported_count) message += ` 当前窗口含 ${data.summary.imported_count} 条手动导入样本。`;
  if (!data.summary.collected_count) message += " 尚无样本，不能据此判断市场冷清或情绪中性。";
  else if (!data.summary.post_count && !data.discovery?.summary.post_count) message += " 当前没有明确看多或看空的股票观点。";
  if (data.platform !== "all") message += ` 当前筛选：${socialPlatforms[data.platform]}。`;
  document.getElementById("social-notice").textContent = message;
  const sync = runtime.refresh_in_progress ? "采集中" : !runtime.configured_count ? "待接入数据源" : runtime.enabled ? "自动采集已开启" : "自动采集已暂停";
  document.getElementById("social-sync").textContent = sync;
  document.getElementById("social-sync").title = runtime.last_success_at ? `最近成功采集 ${formatDate(runtime.last_success_at)}` : "尚无成功采集";
  const refresh = document.getElementById("social-refresh-button");
  refresh.disabled = !runtime.can_refresh;
  refresh.title = runtime.refresh_in_progress ? "采集正在进行" : !runtime.configured_count ? "尚未接入可采集的数据源" : !runtime.can_refresh ? "所选平台尚未接入或仍在请求等待期" : "刷新所选平台讨论";
  const connect = document.getElementById("social-connect-button");
  connect.hidden = runtime.state === "collecting" || (runtime.connected_count === runtime.configured_count && runtime.state === "connected");
  connect.textContent = ["error", "partial", "stale"].includes(runtime.state) ? "检查连接" : "采集设置";
}

function renderSocialRankings() {
  const data = socialRankingData();
  if (!data) return;
  const query = document.getElementById("social-search").value.trim().toLowerCase();
  let stocks = data.stocks.filter(s => !query || `${s.name} ${s.symbol}`.toLowerCase().includes(query));
  const sort = socialState.sort;
  if (sort !== "heat") stocks.sort((a, b) => {
    if (sort === "signal") return (b.signal?.level || 0) - (a.signal?.level || 0) || b.authors - a.authors || b.mentions - a.mentions;
    const field = ["change", "cooling"].includes(sort) ? "change_pct" : "score";
    if (a[field] == null || b[field] == null) return (a[field] == null) - (b[field] == null);
    return (a[field] - b[field]) * (["cooling", "bearish"].includes(sort) ? 1 : -1);
  });
  document.getElementById("social-result-count").textContent = `${stocks.length} 只股票`;
  const hot = document.getElementById("social-hot-list");
  const cold = document.getElementById("social-cold-list");
  hot.replaceChildren(); cold.replaceChildren();
  const active = stocks.filter(s => s.mentions > 0);
  const visible = stocks.filter(s => s.mentions || s.covered_platforms.length);
  if (!socialState.selected || !visible.some(s => s.key === socialState.selected)) socialState.selected = visible[0]?.key || null;
  if (active.length) active.forEach((stock, index) => hot.append(socialStockRow(stock, index + 1)));
  else hot.append(emptyState(query ? "没有匹配的讨论" : "暂无有效讨论", connectedSocialCopy(data)));
  const coldStocks = stocks.filter(s => data.cold.includes(s.key));
  if (coldStocks.length) coldStocks.forEach((stock, index) => cold.append(socialStockRow(stock, index + 1)));
  else cold.append(emptyState("暂无可确认的冷门股票", "有效检索覆盖不足时，冷门判断保留为空"));
  renderSocialDetail();
}

function connectedSocialCopy(data) {
  const sources = data.sources.filter(s => data.platform === "all" || s.key === data.platform);
  if (!sources.some(s => s.configured)) return "所选平台尚未接通自动数据源，股票池已同步";
  if (!sources.some(s => ["connected", "partial"].includes(s.status))) return "所选平台尚无近期成功采集，连接状态见上方";
  return "当前时间窗口内暂无明确看多或看空的股票观点";
}

function socialStockRow(stock, rank) {
  const row = el("button", `social-stock-row${stock.key === socialState.selected ? " selected" : ""}`);
  row.type = "button";
  row.setAttribute("aria-label", `查看 ${stock.name} 社交情绪`);
  row.append(el("span", "social-rank-number", String(rank).padStart(2, "0")));
  const name = el("div", "social-stock-name");
  name.append(el("strong", "", stock.name), el("small", "", `${stock.symbol} · ${stock.signal?.label || stock.attention}${stock.signal?.burst ? " · 升温" : ""}`));
  name.append(instrumentBriefNode(stock));
  const heat = el("div", "social-heat-cell");
  heat.append(el("strong", "", `${stock.mentions} 条`));
  const bar = el("div", "social-heat-track");
  const fill = el("i"); fill.style.width = `${stock.heat || 0}%`; bar.append(fill); heat.append(bar);
  heat.title = `相对热度 ${socialNumber(stock.heat)} / 100 · ${stock.authors} 位作者`;
  const direction = el("div", "social-direction-cell");
  direction.append(el("strong", scoreClass((stock.score || 0) / 100), stock.score == null ? "暂无观点" : stock.score > 0 ? `看多 ${socialNumber(stock.score)}` : stock.score < 0 ? `看空 ${socialNumber(Math.abs(stock.score))}` : "多空分歧"),
    el("small", "", stock.change_pct == null ? "基线不足" : `声量 ${stock.change_pct >= 0 ? "+" : ""}${socialNumber(stock.change_pct, "%")}`));
  row.append(name, heat, direction);
  row.addEventListener("click", () => { socialState.selected = stock.key; renderSocialRankings(); });
  return row;
}

function renderSocialDetail() {
  const stock = socialRankingData().stocks.find(s => s.key === socialState.selected);
  const detail = document.getElementById("social-stock-detail");
  const posts = document.getElementById("social-posts");
  detail.replaceChildren(); posts.replaceChildren();
  document.getElementById("social-evidence-count").textContent = "";
  if (!stock) { detail.append(emptyState("尚无有效股票讨论", "当前筛选范围内没有可展示的情绪依据")); return; }
  const head = el("div", "social-detail-heading");
  const identity = el("div"); identity.append(el("p", "section-label", `${marketLabels[stock.market] || stock.market} · ${stock.symbol}`), el("h2", "", stock.name));
  identity.append(instrumentBriefNode(stock));
  head.append(identity, el("span", "social-quality", stock.quality));
  if (socialScope === "discovery") {
    const add = el("button", "secondary-button", "加入股票池");
    add.type = "button";
    add.title = "加入观察列表，不创建持仓或下单";
    add.addEventListener("click", async () => {
      add.disabled = true;
      try {
        await api("/api/watchlist", {method:"POST", body:{market:stock.market, symbol:stock.symbol, name:stock.name, query:stock.name}});
        showToast("已加入股票池，未改变持仓", "success");
        await refreshSocial();
        refreshDashboard({silent:true});
      } catch (error) {
        showToast(error.message, "error");
        add.disabled = false;
      }
    });
    head.append(add);
  }
  detail.append(head, el("p", "social-conclusion", stock.conclusion));
  appendSocialSignal(detail, stock.signal);
  const split = el("div", "social-opinion-split");
  [["看多",stock.bullish,"positive"],["看空",stock.bearish,"negative"]].forEach(([name,count,cls]) => {
    const metric = el("div"); metric.append(el("span", "", name), el("strong", cls, String(count))); split.append(metric);
  });
  detail.append(split);
  const distribution = el("div", "social-distribution");
  distribution.setAttribute("aria-label", `看多 ${stock.bullish}，看空 ${stock.bearish}`);
  [[stock.bullish,"positive"],[stock.bearish,"negative"]].forEach(([count,cls])=>{ const bar=el("i",cls); bar.style.width=`${stock.mentions ? count/stock.mentions*100 : 0}%`; distribution.append(bar); });
  detail.append(distribution);
  const platformCounts = el("div", "social-platform-counts");
  Object.entries(socialPlatforms).forEach(([key,name]) => platformCounts.append(el("span", "", `${name} ${stock.platform_counts[key] || 0}`)));
  detail.append(platformCounts);
  const quality = el("p", "social-evidence-note", `已剔除 ${stock.duplicates} 条重复文本${stock.latest_at ? ` · 最近讨论 ${formatDate(stock.latest_at,true)}` : ""}`);
  detail.append(quality);
  const factor=stock.trump_factor;
  if(factor) {
    const policy=el("div","social-stock-policy");
    policy.append(el("strong","",`Trump 独立因子 · ${factor.score == null ? "--" : formatScore(factor.score)}`),el("p","",factor.reason));
    if(factor.post_count && !socialState.data.trump?.runtime?.connected)policy.append(el("small","", "近 72 小时缓存样本 · 当前连接不可用"));
    if(factor.latest_post_url && /^https?:\/\//i.test(factor.latest_post_url)) {
      const link=el("a","text-button",`政策依据来源 · ${formatDate(factor.latest_post_at,true)}`);
      link.href=factor.latest_post_url;link.target="_blank";link.rel="noopener noreferrer";policy.append(link);
    }
    detail.append(policy);
  }
  document.getElementById("social-evidence-count").textContent = `${stock.evidence.length} / ${stock.mentions} 条`;
  if (!stock.evidence.length) { posts.append(emptyState("暂无原帖", "等待数据源返回可匹配的讨论")); return; }
  stock.evidence.forEach(post => {
    const item = el("article", "social-post");
    const meta = el("div", "social-post-meta");
    const time = el("time", "", formatDate(post.published_at, true)); time.dateTime=post.published_at;
    meta.append(el("strong", "", socialPlatforms[post.platform]), el("span", "", post.author || "作者未提供"), time);
    item.append(meta, appendNewsTranslation(el("p", "social-post-text", post.text), post, "text"));
    const signals = post.analysis.signals || [];
    if (signals.length) item.append(el("p", "social-evidence-note", `判断依据：${[...new Set(signals.map(s=>`${s.kind}「${s.evidence}」`))].join(" · ")}`));
    if (post.origin === "web") appendWebProvenance(item, post);
    const footer = el("div", "social-post-footer");
    const analysis = post.analysis;
    const badge = el("span", scoreClass(analysis.score), `${analysis.label} · ${post.origin === "web" ? "网页样本" : post.origin === "import" ? "导入样本" : "采集样本"}`);
    badge.title=analysis.evidence.length ? `依据：${analysis.evidence.join("、")} · ${analysis.method}` : "无明确看多或看空表达";
    const link=el("a", "text-button", "原帖 ↗"); link.href=post.url; link.target="_blank"; link.rel="noopener noreferrer";
    footer.append(badge,link); item.append(footer); posts.append(item);
  });
}

function appendSocialSignal(detail, signal) {
  if (!signal) return;
  const section = el("section", "social-signal-analysis");
  const metrics = el("div", "social-signal-metrics");
  [["近 1 小时", `${signal.recent_hour} 条`], ["前 1 小时", socialNumber(signal.previous_hour, " 条")],
    ["样本变化", signal.hour_change_pct == null ? "基线不足" : `${signal.hour_change_pct > 0 ? "+" : ""}${socialNumber(signal.hour_change_pct, "%")}`]].forEach(([title,value])=>{
      const metric=el("div");metric.append(el("small","",title),el("strong","",value));metrics.append(metric);
    });
  section.append(metrics);
  if (signal.risks.length) section.append(el("p", "social-signal-risk", signal.risks.join(" · ")));
  const reasons=el("div","social-signal-reasons");
  [["bullish","看多依据","positive"],["bearish","看空依据","negative"]].forEach(([key,title,cls])=>{
    const column=el("div");column.append(el("h3",cls,title));
    if (!signal.reasons[key].length) column.append(el("p","social-evidence-note","暂无明确观点"));
    signal.reasons[key].forEach(reason=>{
      const item=el("div","social-signal-reason");
      item.append(el("strong","",`${reason.kind}${reason.drivers.length ? ` · ${reason.drivers.join(" / ")}` : ""}`),el("blockquote","",reason.excerpt));
      const link=el("a","text-button",`${socialPlatforms[reason.platform]} · ${formatDate(reason.published_at,true)}`);
      link.href=reason.url;link.target="_blank";link.rel="noopener noreferrer";item.append(link);column.append(item);
    });
    reasons.append(column);
  });
  section.append(reasons);detail.append(section);
}

function appendWebProvenance(item, post) {
  const provenance = el("div", "social-web-provenance");
  provenance.append(el("span", "", `采集 ${formatDate(post.collected_at, true)}`));
  if (post.context?.license_url) {
    const license = el("a", "text-button", post.context.license);
    license.href=post.context.license_url; license.target="_blank"; license.rel="noopener noreferrer";
    provenance.append(license, el("span", "", post.context.adaptation));
  }
  item.append(provenance);
}

function renderSocialWebSamples(data) {
  const section = document.getElementById("social-web-samples-section");
  section.hidden = data.platform !== "all" && !data.sources.some(s=>s.key===data.platform && s.mode==="web");
  const list = document.getElementById("social-web-samples");
  const expanded = new Set([...list.querySelectorAll("details[open]")].map(detail=>detail.dataset.postKey));
  list.replaceChildren();
  const samples = data.web_samples || [];
  document.getElementById("social-web-sample-count").textContent = `最近 ${samples.length} / ${data.sample_selection?.total ?? data.summary.web_sample_count ?? 0} 条 · 当前筛选`;
  const total=data.summary.web_sample_count || 0, directional=data.summary.web_directional_count || 0;
  document.getElementById("social-sample-quality").textContent=`窗口内采集 ${total} 条 · 明确股票观点 ${directional} 条`;
  if (!samples.length) {list.append(emptyState("暂无明确观点", "当前筛选范围内暂无明确看多或看空的股票观点"));return;}
  samples.forEach(post=>{
    const item=el("article","social-web-sample");
    const meta=el("div","social-post-meta");
    const time=el("time","",`发布 ${formatDate(post.published_at,true)}`);time.dateTime=post.published_at;
    meta.append(el("strong","",socialPlatforms[post.platform]),el("span","",post.author || "作者未提供"),time);
    item.append(meta,appendNewsTranslation(el("h3","",post.context?.title || "公开讨论"),post.context || {}));
    const text=el("p","social-post-text",post.text.length>600 ? `${post.text.slice(0,600)}…` : post.text);
    if(post.text.length<=600)appendNewsTranslation(text,post,"text");
    item.append(text);
    if(post.text.length>600){const more=el("details");more.dataset.postKey=`${post.platform}:${post.id}`;more.open=expanded.has(more.dataset.postKey);more.append(el("summary","","完整样本与译文"),appendNewsTranslation(el("p","social-post-text",post.text),post,"text"));item.append(more);}
    const matches=el("div","social-web-matches");
    Object.entries(post.stock_opinions || {}).forEach(([key,analysis])=>{
      const identity=el("div");identity.append(el("strong","",`${key} ${analysis.label}`),instrumentBriefNode(key));matches.append(identity);
    });
    item.append(matches);
    const link=el("a","text-button","原帖 ↗");link.href=post.url;link.target="_blank";link.rel="noopener noreferrer";
    item.append(link);appendWebProvenance(item,post);list.append(item);
  });
}

function populateSocialKeywords() {
  const key=document.getElementById("social-keyword-stock").value;
  briefAfterTitle("social-keyword-stock", key);
  const item=socialState.data?.keyword_settings?.find(item=>item.key===key);
  document.getElementById("social-keyword-base").textContent=item ? [item.symbol,...item.base].join(" · ") : "";
  document.getElementById("social-keyword-custom").value=socialKeywordState.drafts.get(key) ?? (item?.custom || []).join("\n");
  document.getElementById("social-keyword-status").textContent=socialKeywordState.drafts.has(key) ? "有未保存修改" : "";
  document.getElementById("social-keyword-error").textContent="";
}

async function openSocialKeywords() {
  if(socialKeywordState.busy)return;
  if((!socialState.data || socialState.readFailed) && !await refreshSocial())return;
  socialKeywordState.drafts.clear();
  const select=document.getElementById("social-keyword-stock");select.replaceChildren();
  (socialState.data.keyword_settings || []).forEach(item=>select.add(new Option(`${item.symbol} · ${item.name}`,item.key)));
  populateSocialKeywords();document.getElementById("social-keywords-dialog").showModal();
}

async function openSocialWebSettings() {
  if(socialWebState.busy || !await refreshSocial())return;
  const data=socialState.data;
  document.getElementById("social-web-auto").checked=data.runtime.enabled;
  const select=document.getElementById("social-web-interval");
  const interval=String(data.runtime.web_interval_seconds);
  if(![...select.options].some(option=>option.value===interval))select.add(new Option(`${interval} 秒`,interval));
  select.value=interval;
  const options=document.getElementById("social-web-options");options.replaceChildren();
  data.sources.filter(source=>source.mode==="web").forEach(source=>{
    const row=el("div","social-web-option");
    const label=el("label");const checkbox=el("input");checkbox.type="checkbox";checkbox.value=source.key;checkbox.name="web-source";checkbox.checked=source.configured;
    label.append(checkbox,el("strong","",source.name));row.append(label,el("small","",source.scope));
    const links=el("div","social-settings-links");
    [[source.format === "search" ? "公开检索" : "公开订阅",source.public_url],[source.format === "search" ? "接口说明" : "来源条款",source.terms_url]].forEach(([title,url])=>{const a=el("a","text-button",title);a.href=url;a.target="_blank";a.rel="noopener noreferrer";links.append(a);});
    row.append(links);options.append(row);
  });
  document.getElementById("social-web-settings-error").textContent="";
  document.getElementById("social-web-settings-dialog").showModal();
}

function populateSocialConnection() {
  const key = document.getElementById("social-config-platform").value;
  const source = socialState.data?.sources.find(s => s.key === key);
  document.getElementById("social-connection-note").textContent = socialConnections[key][0];
  document.getElementById("social-provider-link").href = socialConnections[key][1];
  document.getElementById("social-feed-url").value = source?.feed_url || "";
  document.getElementById("social-token").value = "";
  socialState.settingsDirty = false;
  document.getElementById("social-feed-label").textContent = ["xiaohongshu", "douyin"].includes(key) ? "授权数据源地址（自动采集必需）" : "授权数据源地址（可选）";
  document.getElementById("social-settings-error").textContent = "";
  renderSocialConnectionStatus();
}

function renderSocialConnectionStatus() {
  const key = document.getElementById("social-config-platform").value;
  const data = socialState.data;
  const source = data?.sources.find(s => s.key === key);
  const status = document.getElementById("social-connection-status");
  status.replaceChildren();
  status.append(el("strong", "", socialState.readFailed ? "后台状态读取失败" : `${socialStatuses[source?.status] || "状态读取中"}${socialState.settingsDirty ? " · 有未保存修改" : ""}`));
  if (source) {
    status.append(el("span", "", source.message),
      el("small", "", source.last_success_at ? `最近成功采集：${formatDate(source.last_success_at)}` : "尚无成功采集记录"));
    if (source.checked_at) status.append(el("small", "", `最近请求：${formatDate(source.checked_at)}`));
    socialDiagnosticLines(source).forEach(line => status.append(el("small", "", line)));
    if (source.next_request_at) status.append(el("small", "", `下次请求不早于：${formatDate(source.next_request_at)}`));
    if (data.runtime.refresh_in_progress) status.append(el("small", "", "采集任务运行中"));
  }
  const test = document.getElementById("social-test-connection");
  test.disabled = socialState.busy || socialState.readFailed || socialState.settingsDirty || !source?.configured || data?.runtime.refresh_in_progress || Boolean(source?.next_request_at);
  test.title = socialState.settingsDirty ? "有未保存的连接修改" : "向已保存的数据源发起一次实际采集，遵守平台等待时间";
  document.getElementById("social-save-connection").disabled = socialState.busy || socialState.readFailed || !data;
  document.getElementById("social-token").disabled = socialState.busy || source?.credential_source === "environment";
  document.getElementById("social-token-status").textContent = source?.credential_source === "environment" ? "令牌来自启动环境" : source?.credential_source === "windows_dpapi" ? "令牌已加密保存在本机" : "尚未配置令牌";
  document.getElementById("social-clear-token").disabled = socialState.busy || socialState.readFailed || source?.credential_source !== "windows_dpapi";
}

async function openSocialSettings(platform = "x") {
  if (socialState.busy || !await refreshSocial()) return;
  document.getElementById("social-config-platform").value = platform;
  document.getElementById("social-auto").checked = socialState.data?.runtime.enabled ?? true;
  const interval = String(socialState.data?.runtime.interval_seconds || 60);
  const select = document.getElementById("social-interval");
  if (![...select.options].some(o => o.value === interval)) select.add(new Option(`${interval} 秒`, interval));
  select.value = interval;
  populateSocialConnection();
  document.getElementById("social-settings-dialog").showModal();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("#social-view-tabs button").forEach(button=>button.addEventListener("click",()=>{
    setSocialView(button.dataset.socialView);refreshSocial({silent:true});
  }));
  document.getElementById("social-sample-scope").addEventListener("change",event=>{socialSampleState.scope=event.target.value;refreshSocial();});
  document.getElementById("social-sample-search").addEventListener("input",event=>{
    socialSampleState.query=event.target.value;clearTimeout(socialSampleState.timer);
    socialSampleState.timer=setTimeout(()=>refreshSocial({silent:true}),300);
  });
  document.getElementById("social-keywords-button").addEventListener("click",openSocialKeywords);
  document.getElementById("social-keyword-stock").addEventListener("change",populateSocialKeywords);
  document.getElementById("social-keyword-custom").addEventListener("input",event=>{
    socialKeywordState.drafts.set(document.getElementById("social-keyword-stock").value,event.target.value);
    document.getElementById("social-keyword-status").textContent="有未保存修改";
  });
  document.getElementById("social-keywords-form").addEventListener("submit",async event=>{
    event.preventDefault();if(socialKeywordState.busy || socialState.readFailed)return;
    const key=document.getElementById("social-keyword-stock").value;
    const words=document.getElementById("social-keyword-custom").value.split(/[\n,，;；]/).map(word=>word.trim()).filter(Boolean);
    const controls=[...event.currentTarget.querySelectorAll("input,textarea,select,button")];
    socialKeywordState.busy=true;controls.forEach(control=>{control.disabled=true;});
    try {
      await api("/api/social/settings",{method:"POST",body:{stock_keywords:{[key]:words}}});
      socialKeywordState.drafts.delete(key);
      document.getElementById("social-keyword-custom").value=words.join("\n");
      document.getElementById("social-keyword-error").textContent="";
      document.getElementById("social-keyword-status").textContent="匹配词已保存";
      await refreshSocial();
    } catch(error){document.getElementById("social-keyword-error").textContent=error.message;}
    finally{socialKeywordState.busy=false;controls.forEach(control=>{control.disabled=false;});document.getElementById("social-keyword-save").disabled=socialState.readFailed;}
  });
  document.getElementById("social-search").addEventListener("input", renderSocialRankings);
  document.querySelectorAll("#social-scope-tabs button").forEach(button => button.addEventListener("click", () => {
    socialScope = button.dataset.scope;
    socialState.selected = null;
    document.querySelectorAll("#social-scope-tabs button").forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderSocial();
  }));
  document.getElementById("social-sort").addEventListener("change", e=>{socialState.sort=e.target.value;renderSocialRankings();});
  document.getElementById("social-platform").addEventListener("change",e=>{socialState.platform=e.target.value;refreshSocial();});
  document.querySelectorAll("#social-window button").forEach(button=>button.addEventListener("click",()=>{
    socialState.hours=Number(button.dataset.hours);
    document.querySelectorAll("#social-window button").forEach(b=>b.classList.toggle("active",b===button)); refreshSocial();
  }));
  document.getElementById("social-settings-button").addEventListener("click",()=>openSocialSettings());
  document.getElementById("social-web-settings-button").addEventListener("click",openSocialWebSettings);
  document.getElementById("social-web-settings-form").addEventListener("submit",async event=>{
    event.preventDefault();if(socialWebState.busy || socialState.readFailed)return;
    const payload={web_sources:[...document.querySelectorAll('#social-web-options input:checked')].map(input=>input.value),web_interval_seconds:Number(document.getElementById("social-web-interval").value),enabled:document.getElementById("social-web-auto").checked};
    socialWebState.busy=true;
    const controls=[...event.currentTarget.querySelectorAll("input,select,button")];controls.forEach(control=>{control.disabled=true;});
    try{await api("/api/social/settings",{method:"POST",body:payload});document.getElementById("social-web-settings-error").textContent="";showToast("网页采集设置已保存","success");await refreshSocial();}
    catch(error){document.getElementById("social-web-settings-error").textContent=error.message;}
    finally{socialWebState.busy=false;controls.forEach(control=>{control.disabled=false;});document.getElementById("social-web-save").disabled=socialState.readFailed;}
  });
  document.getElementById("social-connect-button").addEventListener("click",()=>{
    const source = socialState.platform !== "all" ? socialState.data?.sources.find(s=>s.key===socialState.platform) : socialState.data?.sources.find(s=>["error","stale","blocked"].includes(s.status));
    if(!source || source.mode==="web")openSocialWebSettings();else openSocialSettings(source.key);
  });
  document.getElementById("social-config-platform").addEventListener("change",populateSocialConnection);
  document.getElementById("social-settings-dialog").addEventListener("close",()=>{document.getElementById("social-token").value="";});
  document.getElementById("social-settings-form").addEventListener("input",event=>{
    if(event.target.id !== "social-config-platform") {socialState.settingsDirty=true;renderSocialConnectionStatus();}
  });
  document.getElementById("social-settings-form").addEventListener("submit",async event=>{
    event.preventDefault();
    if(socialState.busy || socialState.readFailed || !socialState.data)return;
    socialState.busy=true;
    const controls = [...event.currentTarget.querySelectorAll("input,select,button")];
    controls.forEach(control=>{control.disabled=true;});
    try {
      await api("/api/social/settings",{method:"POST",body:{platform:document.getElementById("social-config-platform").value,feed_url:document.getElementById("social-feed-url").value,token:document.getElementById("social-token").value,enabled:document.getElementById("social-auto").checked,interval_seconds:Number(document.getElementById("social-interval").value)}});
      document.getElementById("social-token").value=""; socialState.settingsDirty=false;
      document.getElementById("social-settings-error").textContent="";
      showToast("设置已保存，连接结果以实际采集为准", "success"); await refreshSocial();
    } catch(error){document.getElementById("social-settings-error").textContent=error.message;}
    finally {socialState.busy=false;controls.forEach(control=>{control.disabled=false;});renderSocialConnectionStatus();}
  });
  document.getElementById("social-test-connection").addEventListener("click",async()=>{
    if(socialState.busy)return;
    socialState.busy=true;renderSocialConnectionStatus();
    try {
      await api("/api/social/refresh",{method:"POST",body:{platform:document.getElementById("social-config-platform").value}});
      document.getElementById("social-settings-error").textContent="";
      await refreshSocial();
    } catch(error){document.getElementById("social-settings-error").textContent=error.message;}
    finally{socialState.busy=false;renderSocialConnectionStatus();}
  });
  document.getElementById("social-clear-token").addEventListener("click",async()=>{
    if(socialState.busy)return;
    socialState.busy=true;renderSocialConnectionStatus();
    try {await api("/api/social/settings",{method:"POST",body:{platform:document.getElementById("social-config-platform").value,clear_token:true}});document.getElementById("social-token").value="";await refreshSocial();}
    catch(error){document.getElementById("social-settings-error").textContent=error.message;}
    finally{socialState.busy=false;renderSocialConnectionStatus();}
  });
  document.getElementById("social-refresh-button").addEventListener("click",async()=>{
    try {await api("/api/social/refresh",{method:"POST",body:socialState.platform === "all" ? {} : {platform:socialState.platform}});await refreshSocial();} catch(error){showToast(error.message,"error");}
  });
  document.getElementById("social-import-button").addEventListener("click",()=>{
    document.getElementById("social-import-form").reset();
    document.getElementById("social-import-error").textContent="";
    document.getElementById("social-import-dialog").showModal();
  });
  document.getElementById("social-import-file").addEventListener("change",async event=>{
    const file=event.target.files[0];if(!file)return;
    try {
      if(file.size>64000)throw new Error("文件须小于 64 KB；每次最多 200 条");
      const value=JSON.parse(await file.text()); const posts=Array.isArray(value)?value:value.posts;
      const result=await api("/api/social/import",{method:"POST",body:{posts}});
      showToast(`新增 ${result.inserted} 条公开帖子`,"success");document.getElementById("social-import-dialog").close();await refreshSocial();
    } catch(error){document.getElementById("social-import-error").textContent=error.message;} finally {event.target.value="";}
  });
  document.getElementById("social-import-form").addEventListener("submit",async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try {
      const form=Object.fromEntries(new FormData(event.currentTarget));form.published_at=new Date(form.published_at).toISOString();
      const result=await api("/api/social/import",{method:"POST",body:{posts:[form]}});
      showToast(result.inserted ? "帖子已导入并分析" : "帖子已存在，内容已更新","success");
      document.getElementById("social-import-dialog").close();await refreshSocial();
    } catch(error){document.getElementById("social-import-error").textContent=error.message;}finally{button.disabled=false;}
  });
  window.setInterval(()=>{if(state.currentView==="social" && document.visibilityState==="visible")refreshSocial({silent:true});},5000);
});
