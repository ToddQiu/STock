"use strict";

const researchState = { tab: "cards", data: null, inputs: null, cards: null, landscape: null, fundamentals: null, etf: null, etfSelected: "BUG", etfSymbols: ["IGV","XSW","WCLD","BUG"], etfRemote: {}, etfLoad: 0, etfPair: null, companyId: null, cardOffset: 0, offset: 0, inputOffset: 0, request: 0, detailRequest: 0 };
const researchNames = { GOLD: "黄金", BTC: "BTC", "15m": "15 分钟", "1h": "1 小时", "24h": "24 小时" };
const researchStatuses = { pending_entry: "等待新报价", tracking: "追踪中", evaluated: "已核对", missed_entry: "缺入场报价", missed_exit: "缺到期报价" };
const researchKinds = { company_profile: "AI 产业公司资料", fundamentals: "公开年度财报", etf_holdings: "ETF 持仓快照", research_card: "公司与策略研究", valuation_model: "估值研究假设", news: "新闻源输入", social: "社交帖子", trump: "Trump 政策来源", forecast_inputs: "预测依据" };
const strategyComparison = new Map();
const researchEl = id => document.getElementById(id);
const companyResearchTabs = ["cards", "landscape", "peers", "allocation"];

function researchNumber(value, digits = 2, suffix = "") {
  return value == null || !Number.isFinite(Number(value)) ? "--" : Number(value).toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits}) + suffix;
}

function researchDate(value) {
  const time = new Date(value);
  return !value || !Number.isFinite(time.getTime()) || time.getUTCFullYear() < 1980 ? "--" : time.toLocaleString("zh-CN", {hour12:false});
}

function researchFilters() {
  return new URLSearchParams({asset:researchEl("research-asset").value, horizon:researchEl("research-horizon").value, status:researchEl("research-status").value});
}

async function refreshResearch({ silent = false } = {}) {
  if (researchState.tab === "fundamentals") researchState.tab = "earnings";
  if (researchState.tab === "backtest") {
    researchState.tab = "cards";
    return setView("backtest");
  }
  if (researchState.tab === "earnings") {
    ++researchState.request;
    renderResearch();
    return refreshEarningsCenter({silent});
  }
  if (["peers", "allocation", "valuation"].includes(researchState.tab)) {
    ++researchState.request;
    renderResearch();
    return;
  }
  const sequence = ++researchState.request;
  renderResearch();
  const button = researchEl("research-refresh"), status = researchEl("research-request-status");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  const hasData = researchState.tab === "etf" ? researchState.etf : researchState.tab === "cards" ? researchState.cards : researchState.tab === "landscape" ? researchState.landscape : researchState.tab === "inputs" ? researchState.inputs : researchState.data;
  if (!hasData) {
    researchEl("research-runtime").textContent = "正在读取";
    researchEl("research-notice").textContent = "";
  }
  status.classList.remove("error");
  status.hidden = Boolean(hasData);
  status.replaceChildren(el("span", "", "正在读取研究记录…"));
  for (let index = 0; index < 3; index++) {
    const line = el("div", "skeleton-row"); line.setAttribute("aria-hidden", "true"); status.append(line);
  }
  try {
    if (researchState.tab === "etf") {
      const data = await api("/api/research/inputs?kind=etf_holdings");
      if (sequence !== researchState.request) return;
      researchState.etf = data.items.find(item => item.identity === "IGV-XSW-WCLD-BUG") || {payload:{funds:{}}};
      renderResearch(); await loadEtfSelection(); return;
    }
    if (researchState.tab === "landscape") {
      const data = await api("/api/research/companies");
      if (sequence !== researchState.request) return;
      researchState.landscape = data; renderResearch(); return;
    }
    if (researchState.tab === "cards") {
      const params = new URLSearchParams({search:researchEl("strategy-search").value, stance:researchEl("strategy-stance").value, offset:researchState.cardOffset});
      const cards = await api(`/api/research/cards?${params}`);
      if (sequence !== researchState.request) return;
      researchState.cards = cards; renderResearch(); return;
    }
    if (researchState.tab === "inputs") {
      const inputs = await api(`/api/research/inputs?kind=${researchEl("research-kind").value}&offset=${researchState.inputOffset}`);
      if (sequence !== researchState.request) return;
      researchState.inputs = inputs; renderResearch(); return;
    }
    const params = researchFilters(); params.set("offset",researchState.offset);
    const data = await api(`/api/research?${params}`);
    if (sequence !== researchState.request) return;
    researchState.data = data;
    renderResearch();
  } catch (error) {
    if (sequence !== researchState.request) return;
    researchEl("research-notice").hidden = false;
    researchEl("research-notice").textContent = `记录服务不可用：${error.message}`;
    if (!silent) showToast(error.message,"error");
    if (!hasData) {
      status.classList.add("error");
      status.replaceChildren(el("span", "", "研究记录暂时不可用"));
    }
  } finally {
    if (sequence === researchState.request) {
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
      status.hidden = Boolean(hasData) || !status.classList.contains("error");
    }
  }
}

function renderResearch() {
  if (researchState.tab === "fundamentals") researchState.tab = "earnings";
  if (typeof updateResearchSourceSites === "function") updateResearchSourceSites();
  const earningsTab = researchState.tab === "earnings";
  researchEl("research-refresh").title=earningsTab ? "刷新财报中心" : "刷新研究记录";
  const valuationTab = researchState.tab === "valuation";
  const localModule = ["peers", "allocation", "valuation"].includes(researchState.tab);
  researchEl("research-export").hidden=localModule;
  researchEl("research-refresh").hidden=localModule;
  researchEl("research-runtime").hidden=localModule;
  researchEl("research-notice").hidden=localModule;
  researchEl("research-refresh").setAttribute("aria-label", researchEl("research-refresh").title);
  const cardsTab = researchState.tab === "cards", landscapeTab = researchState.tab === "landscape", etfTab = researchState.tab === "etf", inputsTab = researchState.tab === "inputs";
  researchEl("etf-coverage").hidden=!etfTab;
  const companyTab = companyResearchTabs.includes(researchState.tab);
  researchEl("company-research-views").hidden=!companyTab;
  researchEl("company-research-tools").hidden=!companyTab;
  researchEl("strategy-new").hidden=!(cardsTab || landscapeTab);
  researchEl("strategy-comparison").hidden=!cardsTab;
  if (!(cardsTab || landscapeTab)) researchEl("research-company-add").hidden = true;
  const evidenceTab = inputsTab || researchState.tab === "stats";
  researchEl("research-evidence-views").hidden = !evidenceTab;
  document.querySelectorAll("#research-evidence-views button").forEach(button => {
    const active = button.dataset.tab === researchState.tab;
    button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("#company-research-views button").forEach(button=>{
    const active=button.dataset.tab===researchState.tab;
    button.classList.toggle("active",active);button.setAttribute("aria-selected",String(active));button.tabIndex=active?0:-1;
  });
  document.querySelectorAll("#research-tabs button, #market-nav [data-tab], #backtest-tabs [data-tab]").forEach(button=>{
    const selected=button.dataset.tab===(companyTab ? "cards" : evidenceTab ? "inputs" : researchState.tab);
    const active=selected && state.currentView==="research";
    button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));
    if(active)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");
    if (selected && researchEl("research-current-title")) researchEl("research-current-title").textContent=button.textContent;
  });
  [...companyResearchTabs,"etf","earnings","forecasts","inputs","stats","valuation"].forEach(tab=>researchEl(`research-${tab}-panel`).hidden=researchState.tab!==tab);
  document.querySelector('.view[data-view="research"] .research-summary-band').hidden=companyTab || etfTab || earningsTab || inputsTab || valuationTab;
  document.querySelector('.view[data-view="research"] .probability-disclaimer').hidden=companyTab || etfTab || earningsTab || inputsTab || valuationTab;
  researchEl("research-settings-button").hidden=companyTab || etfTab || earningsTab || inputsTab || valuationTab;
  researchEl("research-forecast-filters").hidden=companyTab || etfTab || earningsTab || inputsTab || valuationTab;
  if (localModule) {
    researchEl("research-pagination").hidden=true;
    researchEl("research-request-status").hidden=true;
    researchEl("research-refresh").disabled=false;
    researchEl("research-refresh").setAttribute("aria-busy", "false");
    return;
  }
  if (inputsTab) {
    researchEl("research-runtime").textContent = "原始记录与出处";
    researchEl("research-notice").hidden = true;
    researchEl("research-notice").textContent = "";
    researchEl("research-export").href = `/api/research/export?type=inputs&kind=${researchEl("research-kind").value}`;
    if (researchState.inputs) renderResearchInputs(researchState.inputs);
    renderResearchPage(researchState.inputOffset, researchState.inputs?.total || 0);
    return;
  }
  if (earningsTab) {
    researchEl("research-pagination").hidden=true;
    researchEl("research-request-status").hidden=true;
    researchEl("research-refresh").disabled=false;
    researchEl("research-refresh").setAttribute("aria-busy", "false");
    researchEl("research-export").href=`/api/research/earnings/export?${earningsParams(false)}`;
    researchEl("research-notice").textContent="日程与 EPS 为预期；已公布财报采用年度申报，非季度或 TTM。";
    renderEarnings();
    renderCompanyFinancials();
    return;
  }
  if (etfTab) {
    researchEl("research-current-title").textContent="ETF 自选对比";
    researchEl("research-pagination").hidden=true;
    researchEl("research-export").href="/api/research/export?type=inputs&kind=etf_holdings";
    researchEl("research-runtime").textContent=researchState.etf?.version ? `持仓快照 v${researchState.etf.version}` : "等待持仓快照";
    researchEl("research-notice").hidden=true;
    researchEl("research-notice").textContent="";
    if (researchState.etf) renderEtfResearch();
    return;
  }
  if (landscapeTab) {
    if (!researchState.landscape) return;
    researchEl("research-runtime").textContent=`官网核验 ${researchState.landscape.reviewed_at}`;
    researchEl("research-notice").hidden=true;
    researchEl("research-export").href="/api/research/export?type=inputs&kind=company_profile";
    researchEl("research-pagination").hidden=true;
    renderCompanyLandscape(); return;
  }
  if (cardsTab) {
    if (!researchState.cards) return;
    researchEl("research-runtime").textContent="研究版本留痕";
    researchEl("research-notice").hidden=true;
    researchEl("research-notice").textContent="";
    researchEl("research-export").href="/api/research/export?type=inputs&kind=research_card";
    renderStrategyCards(researchState.cards);
    renderResearchPage(researchState.cardOffset,researchState.cards.total); return;
  }
  const data = researchState.data;
  if (!data) return;
  ["total","pending","evaluated","missing"].forEach(key => researchEl(`research-${key}`).textContent = researchNumber(data.summary[key],0));
  researchEl("research-runtime").textContent = `${data.settings.enabled ? "新增记录已开启" : "新增记录已暂停"} · ${data.runtime.last_tick ? researchDate(data.runtime.last_tick) : "等待后台启动"}`;
  researchEl("research-notice").textContent = data.runtime.error || `${data.summary.input_count} 个输入版本 · ${data.summary.analysis_count} 次分析留痕`;
  researchEl("research-export").href = researchState.tab === "inputs" ? `/api/research/export?type=inputs&kind=${researchEl("research-kind").value}` : `/api/research/export?${researchFilters()}`;
  ["forecasts","inputs","stats"].forEach(tab => researchEl(`research-${tab}-panel`).hidden = researchState.tab !== tab);
  researchEl("research-forecast-filters").hidden = researchState.tab === "inputs";
  researchEl("research-status").hidden = researchState.tab === "stats";
  researchEl("research-pagination").hidden = researchState.tab === "stats";
  if (researchState.tab === "forecasts") renderResearchRecords(data.records);
  if (researchState.tab === "inputs" && researchState.inputs) renderResearchInputs(researchState.inputs);
  if (researchState.tab === "stats") renderResearchStats(data.groups);
  const offset = researchState.tab === "inputs" ? researchState.inputOffset : researchState.offset;
  const count = researchState.tab === "inputs" ? researchState.inputs?.total || 0 : data.total;
  renderResearchPage(offset,count);
}

function renderResearchPage(offset,count) {
  researchEl("research-pagination").hidden=researchState.tab==="stats";
  researchEl("research-page-label").textContent = count ? `${offset+1}–${Math.min(offset+50,count)} / ${count} 条` : "0 条";
  researchEl("research-prev").disabled = offset === 0;
  researchEl("research-next").disabled = offset+50 >= count;
}

function researchCell(title, subtitle = "", className = "") {
  const cell = el("div",className); cell.append(el("strong","",title));
  if (subtitle) cell.append(el("small","",subtitle));
  return cell;
}

function researchInputTarget(item) {
  if (item.kind === "company_profile") return {target:`COMPANY:${item.payload.id || item.identity}`};
  if (item.kind === "fundamentals") return {target:item.identity};
  if (item.kind === "forecast_inputs") return {asset:item.identity};
  return item.payload;
}

function renderResearchRecords(records) {
  const container = researchEl("research-records"); container.replaceChildren();
  if (!records.length) {container.append(emptyState("暂无预测记录","新鲜行情到达后开始留存，过去的预测不会补填")); return;}
  records.forEach(record => {
    const row = el("button","research-record-row"); row.type="button";
    row.setAttribute("aria-label",`查看 ${researchNames[record.asset]} ${researchNames[record.horizon]} 预测记录 ${record.id.slice(0,8)}`);
    const outcome = record.outcome;
    const identity=researchCell(researchNames[record.asset],researchNames[record.horizon]);
    identity.append(instrumentBriefNode({asset:record.asset}));
    row.append(identity,
      researchCell(researchDate(record.created_at),`到期 ${researchDate(record.due_at)}`),
      researchCell(researchNumber(record.up_probability*100,1," 点"),record.direction>0 ? "模拟看多" : record.direction<0 ? "模拟看空" : "观望 / 不模拟交易"),
      researchCell(researchNumber(outcome?.actual_return_pct,3,"%"),"相对冻结参考价",scoreClass(outcome?.actual_return_pct || 0)),
      researchCell(researchNumber(outcome?.net_return_pct,3,"%"),`方案 ${record.cohort.slice(0,6)}`,scoreClass(outcome?.net_return_pct || 0)),
      researchCell(researchStatuses[record.status],outcome?.hit == null ? "" : outcome.hit ? "方向命中" : "方向未命中"));
    row.addEventListener("click",()=>openResearchDetail("forecasts",record.id)); container.append(row);
  });
}

function renderResearchInputs(data) {
  researchEl("research-input-count").textContent = `${data.total} 个留存版本`;
  const container = researchEl("research-inputs"), signature = JSON.stringify(data.items);
  if (container.inputSignature === signature) return;
  container.inputSignature = signature;
  container.replaceChildren();
  if (!data.items.length) {container.append(emptyState("暂无留存输入","启用后的原始内容和变更会从此开始记录")); return;}
  data.items.forEach(item => {
    const row = el("button","research-input-row"); row.type="button";
    const text = item.payload.title || item.payload.text || item.payload.name || `${item.payload.market?.name || item.identity} 预测输入快照`;
    const identity=researchCell(text,item.payload.url || item.identity);
    appendNewsTranslation(identity, item.display || item.payload, item.payload.title ? "title" : "text");
    const target=researchInputTarget(item);
    if (instrumentBriefKey(target)) identity.append(instrumentBriefNode(target));
    row.append(researchCell(researchKinds[item.kind] || item.kind,`版本 ${item.version}`),identity,
      researchCell(researchDate(item.observed_at),`首次记录 ${researchDate(item.first_received_at)}`));
    row.setAttribute("aria-label",`查看输入版本 ${item.id}`);
    row.addEventListener("click",()=>openResearchDetail("inputs",item.id)); container.append(row);
  });
}

function renderResearchStats(groups) {
  const select = researchEl("research-cohort"), previous = select.value;
  select.replaceChildren();
  groups.forEach(group => {const option=el("option","",`${researchNames[group.asset]} · ${researchNames[group.horizon]} · ${group.cohort.slice(0,6)} · ${group.evaluated} 条已核对`); option.value=[group.asset,group.horizon,group.cohort].join(":"); select.append(option);});
  if (Array.from(select.options).some(option=>option.value===previous)) select.value=previous;
  renderResearchGroup();
}

function renderResearchGroup() {
  const group = researchState.data.groups.find(g=>[g.asset,g.horizon,g.cohort].join(":")===researchEl("research-cohort").value);
  const summary = researchEl("research-group-stats"), bins = researchEl("research-calibration"); summary.replaceChildren(); bins.replaceChildren();
  briefAfterTitle("research-cohort", {asset:group?.asset});
  if (!group) {summary.append(emptyState("暂无验证样本","预测到期后才会出现统计结果")); return;}
  const metrics = el("div","research-group-band");
  [["已核对 / 全部",`${group.evaluated} / ${group.total}`],["缺失 / 待到期",`${group.missing} / ${group.pending}`],
   ["方向命中",researchNumber(group.hit_rate == null ? null : group.hit_rate*100,1,"%")],
   ["原始分数误差 / 中性基准",`${researchNumber(group.brier,4)} / ${researchNumber(group.baseline_brier,4)}`],
   ["平均模拟净收益",researchNumber(group.mean_net_return_pct,3,"%")]].forEach(([name,value])=>metrics.append(researchCell(value,name)));
  summary.append(metrics,el("p","research-notice",`${group.evaluated < 30 ? "样本不足，不能据此认定有效。" : "原始样本统计，未进行概率校准或显著性认证。"} 方向命中只统计 ${group.trades} 条非观望预测；均值包含观望记录，不复利、不累加为账户收益。`),
    el("p","quiet-note",`单边手续费 ${researchNumber(group.policy.fee_bps/100,3,"%")} · 单边滑点 ${researchNumber(group.policy.slippage_bps/100,3,"%")} · 执行等待 ${group.policy.latency_seconds} 秒 · 引擎 ${group.engine.code_sha256.slice(0,12)}`));
  const heading = el("div","research-bin-row research-bin-heading"); ["信号区间（点）","样本","平均多向信号","实际上涨频率"].forEach(t=>heading.append(el("span","",t))); bins.append(heading);
  group.bins.forEach(bucket=>{
    const row = el("div","research-bin-row"); row.append(el("span","",`${Math.round(bucket.low*100)}–${Math.round(bucket.high*100)} 点`),el("span","",String(bucket.count)));
    [[bucket.mean_prediction,"predicted"],[bucket.actual_up_rate,"observed"]].forEach(([value,cls])=>{
      const cell=el("div","research-bin-value"); cell.append(el("span","",researchNumber(value==null?null:value*100,1,cls==="predicted"?" 点":"%")));
      const track=el("div","research-bin-track"), fill=el("i",cls); fill.style.width=`${(value || 0)*100}%`; track.append(fill); cell.append(track); row.append(cell);
    }); bins.append(row);
  });
}

function researchField(container, title, value) {const row=el("div","research-detail-field"); row.append(el("span","",title),el("strong","",String(value ?? "--"))); container.append(row);}
function researchRaw(container, title, value) {const section=el("details","research-raw"); section.append(el("summary","",title),el("pre","",JSON.stringify(value,null,2))); container.append(section);}
function researchLink(container, title, url, item = null) {
  try {const parsed=new URL(url); if (!["https:","http:"].includes(parsed.protocol)) return;
    const link=el("a","research-evidence-link",title); link.href=parsed.href; link.target="_blank"; link.rel="noopener noreferrer";
    if (item) appendNewsTranslation(link, item);
    container.append(link);
  } catch (_) { /* Invalid source addresses stay plain text in the frozen snapshot. */ }
}

async function openResearchDetail(type, id) {
  const request=++researchState.detailRequest, dialog=researchEl("research-detail-dialog"), body=researchEl("research-detail-body");
  body.replaceChildren(el("p","quiet-note","读取冻结记录")); if (!dialog.open) dialog.showModal();
  try {
    const data=await api(`/api/research/${type}/${id}`);
    if (request!==researchState.detailRequest || !dialog.open) return;
    body.replaceChildren();
    if (type==="forecasts") {
      researchEl("research-detail-title").textContent=`${researchNames[data.asset]} · ${researchNames[data.horizon]} · ${researchStatuses[data.status]}`;
      briefAfterTitle("research-detail-title", {asset:data.asset});
      const timeline=el("div","research-timeline");
      [["参考报价时间",data.reference_quote.price_time],["基础信号生成",data.signal_generated_at],["依据快照留存",data.input_observed_at],
       ["最终判断冻结",data.created_at],["最早模拟入场",data.entry_not_before],["收到入场报价",data.entry?.observed_at],["预测到期",data.due_at],["结果记录",data.outcome?.recorded_at]].forEach(([label,value])=>researchField(timeline,label,researchDate(value)));
      body.append(timeline);
      const metrics=el("div","research-detail-metrics");
      [["冻结多向信号",researchNumber(data.up_probability*100,1," 点")],["参考价格",researchNumber(data.reference_quote.price,4)],
       ["入场报价",researchNumber(data.entry?.quote.price,4)],["到期报价",researchNumber(data.outcome?.quote?.price,4)],
       ["实际涨跌",researchNumber(data.outcome?.actual_return_pct,4,"%")],["扣费后模拟收益",researchNumber(data.outcome?.net_return_pct,4,"%")]].forEach(([label,value])=>researchField(metrics,label,value)); body.append(metrics);
      if (data.outcome?.reason) body.append(el("p","research-notice",data.outcome.reason));
      body.append(el("p","quiet-note",`方案 ${data.cohort} · 输入指纹 ${data.input_sha256}`));
      const sources=el("div","research-evidence"); (data.news_display || data.inputs.news || []).forEach(item=>researchLink(sources,item.title,item.url,item)); body.append(sources);
      researchRaw(body,"冻结输入：来源内容、行情和技术数据",data.inputs);
      researchRaw(body,"原始判断与执行参数",{signal:data.signal,policy:data.policy,engine:data.engine});
      researchRaw(body,"入场与到期原始记录",{entry:data.entry,outcome:data.outcome});
    } else {
      researchEl("research-detail-title").textContent=`${researchKinds[data.kind] || data.kind} · 版本 ${data.version}`;
      briefAfterTitle("research-detail-title", researchInputTarget(data));
      const versions=el("div","research-version-list");
      data.versions.forEach(version=>{const button=el("button",`text-button${version.id===data.id?" selected":""}`,`v${version.version} · ${researchDate(version.observed_at)}`); button.type="button"; button.addEventListener("click",()=>openResearchDetail("inputs",version.id)); versions.append(button);});
      body.append(versions);
      researchField(body,"原始发布时间",researchDate(data.published_at)); researchField(body,"首次记录时间",researchDate(data.versions[0]?.observed_at)); researchField(body,"此版本记录时间",researchDate(data.observed_at));
      const display = data.display || data.payload;
      body.append(appendNewsTranslation(el("p","research-original-text",data.payload.text || data.payload.title || data.payload.name || data.identity), display, data.payload.text ? "text" : "title"));
      if (data.payload.summary) body.append(appendNewsTranslation(el("p","research-original-text",data.payload.summary), display, "summary"));
      if (data.kind==="company_profile") {
        body.append(el("p","research-original-text",data.payload.business));
        researchField(body,"官网核验日期（非发布日期）",data.payload.reviewed_at);
        (data.payload.sources || []).forEach(source=>researchLink(body,source.title,source.url));
      }
      researchLink(body,"打开原文",data.payload.url);
      researchRaw(body,"此版本原始内容与互动量",data.payload);
      const heading=el("h3","research-analysis-heading",`${data.analyses.length} 次分析留痕`); body.append(heading);
      data.analyses.forEach(analysis=>{
        const section=el("section","research-analysis-entry");
        researchField(section,analysis.target,analysis.engine);
        if (instrumentBriefKey(analysis.target)) section.append(instrumentBriefNode(analysis.target));
        researchField(section,"分析开始",researchDate(analysis.started_at)); researchField(section,"分析完成",researchDate(analysis.completed_at));
        if (analysis.metadata.latency_seconds!=null) researchField(section,"分析耗时",`${researchNumber(analysis.metadata.latency_seconds,3)} 秒`);
        section.append(el("p","",analysis.output.summary || analysis.output.label || (analysis.output.status==="failed" ? "分析失败，未生成有效判断" : "已记录判断")));
        researchRaw(section,"分析结果与模型版本",{output:analysis.output,metadata:analysis.metadata}); body.append(section);
      });
    }
  } catch(error) {if(request===researchState.detailRequest)body.replaceChildren(el("p","negative",error.message));}
}

const strategyStances = {bullish:"有条件看多",neutral:"中性",bearish:"有条件看空"};
const strategyFields = {business:"业务与竞争位置",facts:"事实（附出处）",fundamentals:"基本面驱动（推断）",valuation:"估值与比较基准（推断）",expected_baseline:"预期基准及出处",macro:"宏观传导（推断）",strategic_fit:"CVC 战略协同（推断）",risks:"风险与反证缺口",review:"复盘：实际变化与判断偏差"};

function renderCompanyLandscape() {
  const data=researchState.landscape;
  if (!data) return;
  registerCompanyBriefs(data);
  if (!researchEl("company-entity").dataset.ready) {
    [["company-entity",data.entities],["company-layer",data.layers]].forEach(([id,options])=>{
      Object.entries(options).forEach(([value,label])=>{const option=el("option","",label);option.value=value;researchEl(id).append(option);});
    });
    const summary=researchEl("company-summary");
    Object.entries(data.entities).forEach(([entity,label])=>summary.append(researchCell(String(data.companies.filter(company=>company.entity===entity).length),label)));
    researchEl("company-entity").dataset.ready="true";
  }
  const search=researchEl("company-search").value.trim().toLocaleLowerCase(),layer=researchEl("company-layer").value,entity=researchEl("company-entity").value;
  const companies=data.companies.filter(company=>(layer==="all" || company.layers.includes(layer)) && (entity==="all" || company.entity===entity)
    && `${company.name} ${company.alias} ${company.ticker} ${company.business}`.toLocaleLowerCase().includes(search));
  researchEl("company-count").textContent=`${companies.length} / ${data.companies.length} 家`;
  if (!companies.some(company=>company.id===researchState.companyId)) researchState.companyId=companies[0]?.id || null;
  const list=researchEl("company-list"),signature=JSON.stringify(companies);
  if (list.dataset.signature!==signature) {
    list.dataset.signature=signature;list.replaceChildren();
    companies.forEach(company=>{
      const button=el("button","company-row");button.type="button";button.dataset.companyId=company.id;
      button.setAttribute("aria-controls","company-detail");
      const identity=el("div");identity.append(el("strong","",company.name),el("small","",`${data.entities[company.entity]}${company.ticker ? ` · ${company.ticker}` : ""}`),el("small","",company.layers.map(key=>data.layers[key]).join(" / ")));
      identity.append(instrumentBriefNode({target:`COMPANY:${company.id}`}));
      button.append(identity);
      button.addEventListener("click",()=>{researchState.navigationEdited=true;researchState.companyId=company.id;renderCompanyLandscape();});list.append(button);
    });
    if (!companies.length) list.append(el("p","quiet-note","没有匹配的公司或机构"));
  }
  list.querySelectorAll("button").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.companyId===researchState.companyId)));
  const company=companies.find(row=>row.id===researchState.companyId),detail=researchEl("company-detail"),detailSignature=JSON.stringify(company || null);
  if (detail.dataset.signature===detailSignature) return;
  const sameCompany=detail.dataset.companyId===company?.id;
  const expanded=new Set(Array.from(detail.querySelectorAll(".company-module[open]"),node=>node.dataset.module));
  detail.dataset.companyId=company?.id || "";
  detail.dataset.signature=detailSignature;detail.replaceChildren();
  if (!company) {const title=el("h2","","暂无匹配研究");title.id="company-detail-title";detail.append(title);return;}
  function module(id, label, basis) {
    const section=el("details","company-module"),summary=el("summary"),body=el("div","company-module-body");
    section.dataset.module=id;section.open=sameCompany ? expanded.has(id) : id==="facts";
    summary.append(el("span","company-module-title",label),el("small","company-module-basis",basis));
    section.append(summary,body);detail.append(section);return body;
  }
  const header=el("header","company-detail-heading"),title=el("h2","",company.name);title.id="company-detail-title";
  header.append(title,el("p","quiet-note",`${data.entities[company.entity]}${company.ticker ? ` · 美股 ${company.ticker}` : " · 未关联公开股票代码"} · ${company.layers.map(key=>data.layers[key]).join(" / ")}`));
  const actions=el("div","research-actions");
  [["建立研究",()=>openCompanyResearch(company)],["相关研究",()=>openCompanyStrategies(company)],["资料版本",()=>openResearchDetail("inputs",company.input_version_id)]].forEach(([label,action])=>{const button=el("button","secondary-button",label);button.type="button";button.addEventListener("click",action);actions.append(button);});
  header.append(actions);detail.append(header);
  module("facts","业务事实","官网陈述").append(el("p","company-business",company.business));
  const financials = company.financials;
  if (company.ticker) {
    const section = module("financials", "公开财报与估值", "年度数据 · 非 TTM");
    if (financials && Object.keys(financials.metrics || {}).length) {
      section.append(el("p", "quiet-note", `${financials.source} · 采集 ${researchDate(financials.collected_at)}${financials.cached ? " · 保留缓存" : ""}`));
      const metrics = el("div", "company-fields");
      Object.values(financials.metrics).forEach(metric => researchField(metrics, metric.label,
        `${researchNumber(metric.value, 2)} ${metric.unit} · 报告期 ${metric.start} 至 ${metric.end} · 申报 ${metric.filed}${metric.growth_pct == null ? "" : ` · 同比 ${researchNumber(metric.growth_pct, 2)}%`}`));
      const valuation = financials.valuation || {};
      researchField(metrics, "最近财年市盈率（非 TTM）", `${researchNumber(valuation.annual_pe)} · 价格时点 ${researchDate(valuation.price_as_of)} · EPS报告期 ${valuation.eps_as_of || "缺失"}`);
      section.append(metrics, el("p", "quiet-note", valuation.error || valuation.note || "估值数据尚未齐全"));
      researchLink(section, "SEC 原始申报", financials.source_url);
      if (financials.input_version_id) {
        const version = el("button", "text-button", "财报数据版本");version.type="button";
        version.addEventListener("click",()=>openResearchDetail("inputs",financials.input_version_id));section.append(version);
      }
    } else section.append(el("p", "quiet-note", financials?.error || financials?.note || "后台正在采集公开财报；未取得的数据不填零。"));
    if (financials?.error) section.append(el("p", "negative", `保留旧数据 · ${financials.error}`));
  }
  const analysis=module("framework","经营与投资框架","研究推断");
  const fields=el("div","company-fields");
  Object.entries({monetization:"收入与价值来源",fundamentals:"基本面驱动",moat:"竞争位置与护城河",valuation:"估值或尽调基准",macro:"宏观传导",strategic_fit:"CVC 协同",metrics:"下一步验证指标",risks:"风险与反证缺口"}).forEach(([key,label])=>researchField(fields,label,company[key]));analysis.append(fields);
  const scenarioModule=module("scenarios","条件情景","成立 / 失效条件");
  const scenarios=el("div","company-scenarios"),stances=company.entity==="listed" ? strategyStances : {bullish:"积极情景",neutral:"观察情景",bearish:"谨慎情景"};
  Object.entries(stances).forEach(([stance,label])=>{
    const section=el("section",`company-scenario company-scenario-${stance}`);section.append(el("h4","",label));
    Object.entries({thesis:"推断",conditions:"成立条件",invalidation:"失效条件"}).forEach(([key,title])=>researchField(section,title,company.scenarios[stance][key]));scenarios.append(section);
  });scenarioModule.append(el("p","quiet-note","条件情景不代表当前股价判断。"),scenarios,el("p","research-notice","待补最新财务或项目数据、可靠预期基准与独立反证。官网核验日期不是财报日期；没有计算上涨概率，不生成交易指令。"));
  const sources=el("div","company-sources");company.sources.forEach(source=>researchLink(sources,`${source.title} · ${source.published_at ? `发布 ${source.published_at}` : "发布日期未注明"}`,source.url));
  sources.append(el("p","quiet-note",`官网核验 ${data.reviewed_at} · 资料 v${company.version} · 此版本留存 ${researchDate(company.observed_at)}`),el("p","quiet-note",data.method));
  module("sources","来源与版本",`${company.sources.length} 个来源 · v${company.version}`).append(sources);
}

function openCompanyStrategies(company) {
  researchEl("strategy-search").value=company.ticker ? `US:${company.ticker}` : `COMPANY:${company.id}`;
  researchEl("strategy-stance").value="all";
  researchState.cardOffset=0;
  selectCompanyResearch("cards");
  researchEl("strategy-search").focus();
}

function selectCompanyResearch(tab) {
  if (!companyResearchTabs.includes(tab)) return;
  researchState.navigationEdited=true;
  researchState.tab=tab;
  setView("research");
}

function openCompanyResearch(company) {
  if (!researchEl("strategy-editor").hidden && !window.confirm("已有未保存的研究草稿，是否用这家公司的资料替换？")) return;
  openStrategyEditor({
    name:company.name,target:company.ticker ? `US:${company.ticker}` : `COMPANY:${company.id}`,kind:company.entity==="listed" ? "stock" : "cvc",stance:"neutral",
    data_as_of:researchState.landscape.reviewed_at,horizon:"未来 12 个月（经营/合作验证，非股价预测）",
    business:`${company.business}\n竞争位置（推断）：${company.moat}`,facts:company.business,
    fundamentals:`${company.fundamentals}\n收入/价值来源（推断）：${company.monetization}\n待验证指标：${company.metrics}`,
    valuation:company.valuation,macro:company.macro,strategic_fit:company.strategic_fit,
    risks:`${company.risks}\n尚未补充最新财务/项目数据、预期基准及独立反证。`,scenarios:company.scenarios,
    evidence:company.sources.map(source=>({role:"support",url:source.url,as_of:source.published_at || "",excerpt:`官网业务陈述：${company.business}\n查阅日期 ${researchState.landscape.reviewed_at}（非发布日期）`}))
  });
}

function strategyField(parent,label,name,value="",type="textarea",options=null) {
  const wrapper=el("label","strategy-field"), control=el(options ? "select" : type==="textarea" ? "textarea" : "input");
  if (options) Object.entries(options).forEach(([key,title])=>{const option=el("option","",title); option.value=key;control.append(option);});
  else if(type!=="textarea") control.type=type;
  control.name=name; control.value=value; control.maxLength=type==="textarea" ? 10000 : 120;
  wrapper.append(el("span","",label),control);parent.append(wrapper);return control;
}

function addStrategyEvidence(row={}, container=researchEl("strategy-evidence")) {
  const entry=el("div","strategy-evidence-row");
  strategyField(entry,"类型","role",row.role || "support","select",{support:"证据",counter:"反证"});
  const url=strategyField(entry,"原文链接","url",row.url,"url");url.maxLength=2048;url.required=true;
  strategyField(entry,"证据日期","as_of",row.as_of,"date");
  const excerpt=strategyField(entry,"事实摘录","excerpt",row.excerpt);excerpt.maxLength=4000;excerpt.required=true;
  const remove=el("button","icon-button","×");remove.type="button";remove.title="删除此条证据";remove.setAttribute("aria-label",remove.title);
  remove.addEventListener("click",()=>entry.remove());entry.append(remove);container.append(entry);
}

function openStrategyEditor(card={}, embedded=null) {
  if (!embedded) selectCompanyResearch("cards");
  const form=embedded?.form || researchEl("strategy-editor");form.replaceChildren();form.hidden=false;
  form._portfolioEditor=embedded;
  if (!embedded) researchEl("research-company-add").hidden=true;
  const heading=el("div","research-section-heading");heading.append(el("h2","",card.id ? `编辑研究 · v${card.version}` : "新建研究"));
  const cancel=el("button","secondary-button","取消");cancel.type="button";cancel.addEventListener("click",()=>{if(embedded)embedded.close();else{form.hidden=true;researchEl("strategy-new").focus();}});heading.append(cancel);form.append(heading);
  const identity=el("div","strategy-grid");form.append(identity);
  [["公司或标的","name"],["代码 / 公司标识","target"]].forEach(([label,key])=>{strategyField(identity,label,key,card[key],"text").required=true;});
  const brief=instrumentBriefNode(card);identity.append(brief);
  form.elements.target.addEventListener("input",()=>updateInstrumentBrief(brief,{target:form.elements.target.value}));
  strategyField(identity,"研究类型","kind",card.kind || "stock","select",{cvc:"CVC 公司研究",stock:"个人股票研究",asset:"资产专题"});
  strategyField(identity,"当前判断","stance",card.stance || "neutral","select",strategyStances);
  strategyField(identity,"资料截至","data_as_of",card.data_as_of,"date");strategyField(identity,"研究期限","horizon",card.horizon,"text");
  const fields=el("div","strategy-grid");form.append(fields);
  Object.entries(strategyFields).forEach(([key,label])=>strategyField(fields,label,key,card[key]));
  const scenarios=el("div","strategy-scenarios");form.append(scenarios);
  Object.entries(strategyStances).forEach(([stance,label])=>{
    const section=el("section");section.append(el("h3","",label));
    Object.entries({thesis:"情景推断",conditions:"成立条件",invalidation:"失效条件"}).forEach(([key,title])=>strategyField(section,title,`${stance}_${key}`,card.scenarios?.[stance]?.[key]));scenarios.append(section);
  });
  const evidenceHeading=el("div","research-section-heading"),add=el("button","secondary-button","添加证据 / 反证");add.type="button";
  evidenceHeading.append(el("h3","","原文证据"),add);const evidence=el("div");if(!embedded)evidence.id="strategy-evidence";form.append(evidenceHeading,evidence);
  add.addEventListener("click",()=>addStrategyEvidence({},evidence));
  (card.evidence || []).forEach(row=>addStrategyEvidence(row,evidence));
  const error=el("p","negative");if(!embedded)error.id="strategy-error";error.setAttribute("role","alert");const save=el("button","primary-button","保存研究版本");save.type="submit";form.append(error,save);
  form.dataset.id=card.id || "";form.dataset.version=card.version || 0;form.elements.name.focus();
}

async function saveStrategyCard(event) {
  event.preventDefault();const form=event.currentTarget,button=event.submitter;button.disabled=true;
  const body={id:form.dataset.id,base_version:Number(form.dataset.version),scenarios:{},evidence:[]};
  ["name","target","kind","stance","data_as_of","horizon",...Object.keys(strategyFields)].forEach(key=>body[key]=form.elements[key].value);
  Object.keys(strategyStances).forEach(stance=>{body.scenarios[stance]={};["thesis","conditions","invalidation"].forEach(key=>body.scenarios[stance][key]=form.elements[`${stance}_${key}`].value);});
  form.querySelectorAll(".strategy-evidence-row").forEach(row=>{const evidence={};row.querySelectorAll("input,select,textarea").forEach(input=>evidence[input.name]=input.value);body.evidence.push(evidence);});
  try {const saved=await api("/api/research/cards",{method:"POST",body});
    if(form._portfolioEditor){await form._portfolioEditor.saved(saved);return;}
    form.hidden=true;
    if (!body.id) {researchEl("strategy-search").value=body.target;researchEl("strategy-stance").value="all";researchState.cardOffset=0;}
    showToast("研究版本已保存","success");await refreshResearch();researchEl("strategy-new").focus();}
  catch(error){form.querySelector('[role="alert"]').textContent=error.message;}finally{button.disabled=false;}
}

function renderStrategyCards(data) {
  researchEl("strategy-count").textContent=`${data.total} 项研究`;
  data.items.forEach(card => {if (strategyComparison.has(card.id)) strategyComparison.set(card.id, card);});
  renderStrategyComparison();
  const container=researchEl("strategy-cards"), signature=JSON.stringify(data);
  if(container.dataset.signature===signature)return;
  const open=new Set(Array.from(container.querySelectorAll("details[open]")).map(node=>node.dataset.id));
  const focused=document.activeElement.closest?.("[data-card-action]");const focusId=focused?.dataset.cardAction;
  container.dataset.signature=signature;container.replaceChildren();
  if(!data.items.length){container.append(emptyState("暂无研究","尚未保存公司或资产研究"));return;}
  data.items.forEach(card=>{
    const item=el("details","strategy-card");item.dataset.id=card.id;item.open=open.has(card.id);
    const identity=el("div","strategy-identity");identity.append(el("strong","",card.name),instrumentBriefNode(card));
    const summary=el("summary");summary.append(identity,el("span","",card.target),el("span",card.stance==="bullish"?"positive":card.stance==="bearish"?"negative":"neutral",strategyStances[card.stance]),el("small","",`${card.assessment_status==="conditional"?"有条件研究":"证据不足"} · v${card.version} · 资料 ${card.data_as_of || "未注明"}`));item.append(summary);
    const content=el("div","strategy-card-body"),actions=el("div","research-actions");
    [["编辑 / 复盘",()=>openStrategyEditor(card)],["版本与原始记录",()=>openResearchDetail("inputs",card.input_version_id)]].forEach(([label,action],index)=>{const button=el("button","secondary-button",label);button.type="button";button.dataset.cardAction=`${card.id}:${index}`;button.addEventListener("click",action);actions.append(button);});content.append(actions);
    const compare = el("button", "secondary-button", "加入对比"); compare.type="button"; compare.dataset.compareId=card.id; compare.prepend(uiIcon("plus"));
    compare.addEventListener("click",()=>{
      if (strategyComparison.has(card.id)) strategyComparison.delete(card.id);
      else if (strategyComparison.size < 4) {strategyComparison.set(card.id,card);researchEl("strategy-comparison").open=true;}
      else {showToast("最多对比 4 项研究，请先移除一项", "error");return;}
      renderStrategyComparison();
    });actions.prepend(compare);
    content.append(el("p","quiet-note",`期限 ${card.horizon || "未注明"} · 保存 ${researchDate(card.observed_at)} · 预期基准${card.baseline_status==="missing"?"缺失，不能认定超预期":"由研究者提供，尚未核验"}`));
    const fields=el("div","strategy-grid");Object.entries(strategyFields).forEach(([key,label])=>researchField(fields,label,card[key] || "未填写"));content.append(fields);
    const scenarios=el("div","strategy-scenarios");Object.entries(strategyStances).forEach(([stance,label])=>{const section=el("section");section.append(el("h3","",label));Object.entries({thesis:"情景推断",conditions:"成立条件",invalidation:"失效条件"}).forEach(([key,title])=>researchField(section,title,card.scenarios[stance][key] || "未填写"));scenarios.append(section);});content.append(scenarios);
    card.evidence.forEach(evidence=>{const row=el("div","strategy-evidence");row.append(el("strong","",`${evidence.role==="counter"?"反证":"证据"} · ${evidence.as_of || "日期未注明"}`),el("p","",evidence.excerpt));researchLink(row,"打开原文",evidence.url);content.append(row);});
    if(!card.evidence.some(row=>row.role==="counter"))content.append(el("p","quiet-note","反证尚未记录"));item.append(content);container.append(item);
  });
  renderStrategyComparison();
  if(focusId)Array.from(container.querySelectorAll("[data-card-action]")).find(node=>node.dataset.cardAction===focusId)?.focus();
}

function renderStrategyComparison() {
  const cards=[...strategyComparison.values()], selected=researchEl("strategy-comparison-selected"), container=researchEl("strategy-comparison-table");
  const picker=researchEl("strategy-compare-select"), available=(researchState.cards?.items || []).filter(card=>!strategyComparison.has(card.id));
  const pickerSignature=JSON.stringify(available.map(card=>[card.id,card.name,card.target]));
  if(picker.dataset.signature!==pickerSignature){
    const previous=picker.value;picker.dataset.signature=pickerSignature;
    picker.replaceChildren(...available.map(card=>{const option=el("option","",`${card.name} · ${card.target}`);option.value=card.id;return option;}));
    if(available.some(card=>card.id===previous))picker.value=previous;
  }
  picker.disabled=!available.length;researchEl("strategy-compare-add").disabled=!available.length || cards.length>=4;
  researchEl("strategy-comparison-count").textContent=`${cards.length} / 4`;
  const signature=JSON.stringify(cards);
  document.querySelectorAll("[data-compare-id]").forEach(button=>{
    const active=strategyComparison.has(button.dataset.compareId);
    button.setAttribute("aria-pressed",String(active));button.replaceChildren(uiIcon(active?"minus":"plus"),document.createTextNode(active?"移出对比":"加入对比"));
  });
  if(container.dataset.signature===signature)return;
  container.dataset.signature=signature;selected.replaceChildren();container.replaceChildren();
  cards.forEach(card=>{
    const row=el("span","research-selection-item",card.name),remove=el("button","icon-button");remove.type="button";
    remove.title=`移出对比 ${card.name}`;remove.setAttribute("aria-label",remove.title);remove.append(uiIcon("x"));
    remove.addEventListener("click",()=>{strategyComparison.delete(card.id);renderStrategyComparison();researchEl("strategy-comparison").querySelector("summary").focus();});row.append(remove);selected.append(row);
  });
  if(cards.length<2){container.append(el("p","quiet-note","选择已有研究后加入对比；至少 2 项、最多 4 项。"));return;}
  const {table,body}=etfTable(["研究维度",...cards.map(card=>`${card.name} · ${card.target} · v${card.version}`)]);
  for(const [key,label] of [["data_as_of","资料截至"],["stance","有条件判断"],["horizon","研究期限"],...Object.entries(strategyFields)]){
    const row=el("tr"),heading=el("th","",label);heading.scope="row";row.append(heading);
    cards.forEach(card=>row.append(el("td","",key==="stance"?strategyStances[card.stance]:card[key] || "未填写")));body.append(row);
  }
  const evidence=el("tr");const heading=el("th","","证据与反证");heading.scope="row";evidence.append(heading);
  cards.forEach(card=>{const cell=el("td");(card.evidence || []).forEach(row=>researchLink(cell,`${row.role==="counter"?"反证":"证据"} · ${row.as_of || "日期未注明"}`,row.url));if(!card.evidence?.length)cell.textContent="未填写";evidence.append(cell);});body.append(evidence);container.append(table);
}

function openResearchCompanyAdd() {
  if (!researchEl("strategy-editor").hidden && !confirm("放弃尚未保存的研究编辑？")) return;
  researchEl("strategy-editor").hidden=true;
  const form=researchEl("research-company-add");form.hidden=false;
  researchEl("research-company-add-status").textContent="";form.elements.symbol.focus();
}

async function addResearchCompany(event) {
  event.preventDefault();const form=event.currentTarget,button=event.submitter;
  const market=form.elements.market.value,symbol=form.elements.symbol.value.trim().toUpperCase();
  button.disabled=true;researchEl("research-company-add-status").textContent="正在核对证券正式名称…";
  try {
    const identity=await api(`/api/securities/resolve?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`);
    if(form.hidden || form.elements.market.value!==market || form.elements.symbol.value.trim().toUpperCase()!==symbol)return;
    if(!identity.name || identity.market!==market || !identity.symbol)throw Error("来源未返回可核验的证券名称");
    openStrategyEditor({name:identity.name,target:`${identity.market}:${identity.symbol}`,kind:"stock",stance:"neutral"});
    const editor=researchEl("strategy-editor");editor.elements.name.readOnly=true;editor.elements.target.readOnly=true;
    editor.elements.name.title="按证券代码识别的正式名称，不使用自定义别名";
  } catch(error) {researchEl("research-company-add-status").textContent=`无法建立研究：${error.message}。请核对市场和代码后重试。`;}
  finally {button.disabled=false;}
}

async function addEtfSelection(event) {
  event.preventDefault();const input=researchEl("etf-add-symbol"),status=researchEl("etf-add-status");
  status.hidden=true;status.textContent="";
  try {
    const added=parseEtfSelection(input.value);
    if(added.length!==1)throw Error("每次添加一个 ETF 代码");
    // Include an unfinished bulk edit instead of silently discarding it.
    const current=parseEtfSelection(researchEl("etf-symbols").value);
    if(current.includes(added[0]))throw Error(`${added[0]} 已在对比列表中`);
    const symbols=parseEtfSelection([...current,...added].join(","));
    researchEl("etf-symbols").value=symbols.join(", ");input.value="";
    await changeEtfSelection();input.focus();
  } catch(error) {status.textContent=error.message;status.hidden=false;}
}

const etfCatalog = {
  IGV: {name:"iShares Expanded Tech-Software Sector ETF", theme:"广义软件", scope:"北美软件，兼有部分互动媒体与服务", weighting:"修正市值加权", fee:0.38, feeDate:"2026-10-06 官网核验", declared:"106 个股票 / 111 条项目 · 2026-10-02（用户说明，待导入核验）", source:"https://www.ishares.com/us/products/239771/ishares-north-american-techsoftware-etf", methodology:"https://www.spglobal.com/spdji/en/indices/equity/sp-north-american-expanded-technology-software-index-custom/", focus:"软件行业整体收入、企业 IT 预算与龙头现金流。市值加权并不等于每家公司风险相同。", condition:"收入与现金流改善，估值没有完全透支增长；需逐项核对财报。", invalidation:"IT 支出放缓、龙头增长或现金流走弱，或估值压缩抵消盈利增长。"},
  XSW: {name:"State Street SPDR S&P Software & Services ETF", theme:"软件与服务等权", scope:"应用 / 系统软件、互动娱乐、IT 咨询等", weighting:"修正等权；再平衡后会漂移", fee:0.35, feeDate:"2026-10-06 官网核验", declared:"131 个股票 / 133 条项目 · 2026-10-05（用户说明，待导入核验）", source:"https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-software-services-etf-xsw", focus:"软件行情能否从少数龙头扩散到更多公司。等权不等于低波动，也不等于每天权重相同。", condition:"行业改善具有广度，中小公司利润与融资环境也改善。", invalidation:"上涨仅集中于少数巨头，或较小公司的盈利、融资能力恶化。"},
  WCLD: {name:"WisdomTree Cloud Computing Fund", theme:"云软件", scope:"美国上市、以云软件与服务为主的企业（含 ADR）", weighting:"BVP Nasdaq Emerging Cloud Index 等权", fee:0.45, feeDate:"2026-10-05 官网资料", declared:"65 个股票 / 67 条项目 · 2026-10-05（用户说明，待导入核验）", source:"https://www.wisdomtree.com/us/products/megatrends/wcld", focus:"云收入、续费、净收入留存与自由现金流。聚焦云软件，不等于包揽所有 AI 或云基础设施龙头。", condition:"续费和增长改善，同时现金流质量提高，估值与增长相匹配。", invalidation:"增长减速、客户缩减订阅，或价格竞争与 AI 替代削弱单位经济。"},
  BUG: {name:"Global X Cybersecurity ETF", theme:"网络安全", scope:"网络安全企业；包含非美国上市证券", weighting:"持仓非等权；市值加权约束见官方 2023 摘要", fee:0.50, feeDate:"2026-10-05 官网资料", source:"https://www.globalxetfs.com/funds/bug", methodology:"https://assets.globalxetfs.com/funds/documents/bug/Index-Methodology-Summary.pdf", focus:"安全预算、续费、平台整合和竞争。安全需求长期存在，不代表成分公司每个季度都能增长。", condition:"订单与续费改善，竞争没有显著侵蚀利润，主要持仓估值合理。", invalidation:"大客户削减预算、竞争压价，或产品事故与集中持仓拖累经营。"},
};
const etfSymbols = Object.keys(etfCatalog);
const etfTypes = {stock:"股票",cash:"现金 / 货币基金",future:"期货",other:"其他"};

function parseEtfSelection(value) {
  const symbols=[...new Set(String(value).toUpperCase().trim().split(/[\s,，、;；]+/).filter(Boolean))];
  if (!symbols.length || symbols.length>6 || symbols.some(symbol=>! /^[A-Z0-9][A-Z0-9.\-]{0,14}$/.test(symbol))) throw Error("请输入 1 至 6 个 ETF 代码，以逗号或空格分隔。");
  return symbols;
}

function etfMeta(symbol, market) {
  return etfCatalog[symbol] || {name:market?.meta?.longName || symbol,theme:"自选 ETF",scope:"投资范围待发行商资料核验",weighting:"待核验",fee:null,feeDate:"待核验",
    source:`https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}/`,focus:"核对基金跟踪目标、费用、持仓结构及是否使用杠杆；名称相近不代表风险相同。",
    condition:"需结合跟踪目标、盈利与估值证据判断；价格趋势不能代替基本面。",invalidation:"跟踪目标改变、持仓集中或衍生品敞口增加，需重新核验。"};
}

function etfMarketAnalysis(market, symbol) {
  if (!market || market.meta?.symbol?.toUpperCase()!==symbol || market.meta?.instrumentType!=="ETF") return null;
  const values=market.indicators?.adjclose?.[0]?.adjclose || market.indicators?.quote?.[0]?.close || [];
  const rows=(market.timestamp || []).map((time,i)=>({time,close:values[i]})).filter(row=>Number.isFinite(row.time)&&Number.isFinite(row.close)&&row.close>0).sort((a,b)=>a.time-b.time);
  if (!rows.length) return null;
  const closes=rows.map(row=>row.close),last=closes[closes.length-1],mean=n=>closes.length>=n?closes.slice(-n).reduce((a,b)=>a+b,0)/n:null;
  let peak=closes[0],drawdown=0;
  for(const value of closes){peak=Math.max(peak,value);drawdown=Math.min(drawdown,(value/peak-1)*100);}
  const ma20=mean(20),ma60=mean(60),stance=ma20===null||ma60===null?"样本不足":last>ma20&&ma20>ma60?"趋势偏多":last<ma20&&ma20<ma60?"趋势偏空":"趋势中性";
  return {name:market.meta.longName||symbol,as_of:new Date(rows[rows.length-1].time*1000).toISOString(),samples:closes.length,
    change20:closes.length>20?(last/closes[closes.length-21]-1)*100:null,change60:closes.length>60?(last/closes[closes.length-61]-1)*100:null,drawdown,stance,
    basis:market.indicators?.adjclose?.[0] ? "来源复权收盘价" : "未复权收盘价；分红和拆股可能影响比较"};
}

function etfAnalysis(fund, market, symbol) {
  const stats=etfStatistics(fund),price=etfMarketAnalysis(market,symbol),facts=[];
  if(stats){
    facts.push(`持仓 ${fund.as_of}：${stats.stocks} 条股票项目，前十大正权重股票占 ${stats.top10.toFixed(2)}%。`);
    if(stats.largest)facts.push(`最大股票 ${stats.largest.symbol} 占 ${stats.largest.weight.toFixed(2)}%。`);
    if(stats.other || !stats.stocks)facts.push(`非股票净权重 ${stats.other.toFixed(2)}%；股票交集不能衡量债券、期货或杠杆风险。`);
  }else facts.push("完整持仓缺失：不估算集中度，也不把缺失成分当作未持有。");
  if(price)facts.push(`${price.samples} 个日线样本，样本内最大回撤 ${price.drawdown.toFixed(2)}%；${price.basis}，不是未来亏损上限。`);
  return {facts,stance:price?.stance || "行情待核验",inference:stats ? (stats.top10>=50?"按前十大正权重股票超过 50% 的提示阈值，少数公司对组合影响较大。":"前十大集中度未超过 50% 提示阈值，但不能据此认定低风险或充分分散。") : "缺少持仓，暂不能判断行业暴露与分散效果。"};
}

function etfWeight(value) {
  return value == null ? "--" : Number(value).toLocaleString("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:6})+"%";
}

function etfStockWeights(fund) {
  const weights = new Map();
  for (const row of fund?.holdings || []) {
    if (row.type !== "stock" || !row.key || !(row.weight > 0)) continue;
    weights.set(row.key,(weights.get(row.key) || 0)+row.weight);
  }
  return weights;
}

function etfStatistics(fund) {
  if (!fund) return null;
  const rows=fund.holdings, stocks=rows.filter(row=>row.type==="stock");
  const grouped=new Map();
  for (const [index,row] of stocks.entries()) {
    if (row.weight <= 0) continue;
    const key=row.key || `unmatched:${index}`, previous=grouped.get(key);
    grouped.set(key,{name:row.name,symbol:row.symbol,weight:(previous?.weight || 0)+row.weight});
  }
  const ordered=Array.from(grouped.values()).sort((a,b)=>b.weight-a.weight);
  return {stocks:stocks.length,items:rows.length,top10:ordered.slice(0,10).reduce((sum,row)=>sum+row.weight,0),largest:ordered[0],
    total:rows.reduce((sum,row)=>sum+row.weight,0),other:rows.filter(row=>row.type!=="stock").reduce((sum,row)=>sum+row.weight,0),unmatched:stocks.filter(row=>!row.key).length};
}

function etfOverlap(first, second) {
  if (!first || !second) return null;
  const a=etfStockWeights(first),b=etfStockWeights(second);let weight=0,count=0;
  for (const [key,value] of a) if (b.has(key)) {weight+=Math.min(value,b.get(key));count++;}
  return {weight,count};
}

function etfCompareRows(funds, {symbols=etfSymbols, filter="shared", search=""}={}) {
  const rows=new Map(),query=search.trim().toLocaleLowerCase();
  for (const symbol of Object.keys(funds)) for (const holding of funds[symbol]?.holdings || []) {
    if (holding.type!=="stock" || !holding.key) continue;
    const row=rows.get(holding.key) || {key:holding.key,name:holding.name,symbol:holding.symbol,weights:{}};
    row.weights[symbol]=(row.weights[symbol] || 0)+holding.weight;rows.set(holding.key,row);
  }
  return Array.from(rows.values()).filter(row=>{
    const count=symbols.filter(symbol=>Object.hasOwn(row.weights,symbol)).length;
    return count>0 && (filter==="all" || (filter==="shared" ? count>=2 : count===1)) && `${instrumentName({target:row.key,name:row.name})} ${row.name} ${row.symbol} ${row.key}`.toLocaleLowerCase().includes(query);
  }).sort((a,b)=>Math.max(...Object.values(b.weights))-Math.max(...Object.values(a.weights)) || a.key.localeCompare(b.key));
}

function etfHoldingName(row) {
  return row.type === "stock" && row.key ? instrumentName({target:row.key,name:row.name}) : row.name;
}

function etfFilteredHoldings(fund, {search="",type="all",sort="weight-desc"}={}) {
  const query=search.trim().toLocaleLowerCase();
  return (fund?.holdings || []).filter(row=>(type==="all" || (type==="stock" ? row.type==="stock" : row.type!=="stock")) && `${etfHoldingName(row)} ${row.name} ${row.symbol}`.toLocaleLowerCase().includes(query))
    .slice().sort((a,b)=>sort==="source" ? a.source_row-b.source_row : sort==="name" ? etfHoldingName(a).localeCompare(etfHoldingName(b)) : (sort==="weight-asc" ? a.weight-b.weight : b.weight-a.weight) || a.source_row-b.source_row);
}

function etfTable(headers) {
  const table=el("table","etf-table"),head=el("thead"),row=el("tr");
  table.style.minWidth=`${Math.max(560,headers.length*160)}px`;
  for (const header of headers) {const cell=el("th");cell.scope="col";cell.append(typeof header==="string" ? el("span","",header) : header);row.append(cell);}
  head.append(row);const body=el("tbody");table.append(head,body);return {table,body};
}

function etfChoose(symbol) {
  researchState.etfSelectionEdited=true;
  researchState.etfSelected=symbol;renderEtfResearch(true);
}

async function changeEtfSelection() {
  researchState.etfSelectionEdited=true;
  clearTimeout(researchState.etfTimer);
  const input=researchEl("etf-symbols");
  try {
    const symbols=parseEtfSelection(input.value);input.setAttribute("aria-invalid","false");
    researchState.etfSymbols=symbols;researchState.etfPair=null;
    if(!symbols.includes(researchState.etfSelected))researchState.etfSelected=symbols[0];
    if(typeof saveStartupPreferences==="function")saveStartupPreferences();
    renderEtfResearch(true);await loadEtfSelection();
  } catch(error) {input.setAttribute("aria-invalid","true");researchEl("etf-load-status").textContent=error.message;}
}

async function loadEtfSelection() {
  if(researchEl("etf-symbols").getAttribute?.("aria-invalid")==="true")return;
  const generation=++researchState.etfLoad,symbols=researchState.etfSymbols.slice();
  const status=researchEl("etf-load-status");status.textContent="正在读取公开行情与持仓…";
  const queue=symbols.slice();let completed=0;
  const worker=async()=>{while(queue.length && generation===researchState.etfLoad){
    const symbol=queue.shift();
    try {
      const result=await api(`/api/research/etf?symbol=${encodeURIComponent(symbol)}`);
      if(generation!==researchState.etfLoad)return;
      if(!result.market && researchState.etfRemote[symbol]?.market){result.market=researchState.etfRemote[symbol].market;result.warnings.push("保留上次行情样本，尚未更新；以样本时间为准。");}
      researchState.etfRemote[symbol]=result;
      if(typeof rememberStartupResponse==="function")rememberStartupResponse(`/api/research/etf?symbol=${encodeURIComponent(symbol)}`,result);
      if(result.fund){
        researchState.etf ||= {payload:{funds:{}}};
        const old=researchState.etf.payload.funds[symbol];
        if(!old || result.fund.as_of>=old.as_of)researchState.etf.payload.funds[symbol]=result.fund;
      }
    }catch(error){
      if(generation!==researchState.etfLoad)return;
      researchState.etfRemote[symbol]={...researchState.etfRemote[symbol],warnings:[`加载失败，保留上次资料：${error.message}`]};
    }
    completed++;status.textContent=`已检查 ${completed} / ${symbols.length} · 各 ETF 的资料时间与缺失项见下方`;
    renderEtfResearch(true);
  }};
  await Promise.all([worker(),worker()]);
}

function renderEtfResearch(force=false) {
  const etfSymbols=researchState.etfSymbols,remote=researchState.etfRemote;
  const metaFor=symbol=>etfMeta(symbol,remote[symbol]?.market);
  const snapshot=researchState.etf, funds=snapshot?.payload?.funds || {}, panel=researchEl("research-etf-panel");
  const signature=JSON.stringify([snapshot,etfSymbols,remote,researchState.etfSelected,researchState.etfPair]);
  if (!force && panel.dataset.signature===signature) return;
  panel.dataset.signature=signature;
  const focused=document.activeElement?.dataset?.etfFocus;
  const available=etfSymbols.filter(symbol=>funds[symbol]);
  researchEl("etf-coverage").textContent=`${available.length} / ${etfSymbols.length} 已有持仓`;
  if(document.activeElement!==researchEl("etf-symbols") && researchEl("etf-symbols").getAttribute("aria-invalid")!=="true")researchEl("etf-symbols").value=etfSymbols.join(", ");
  const importSelect=researchEl("etf-import-symbol"),previousImport=importSelect.value;
  importSelect.replaceChildren(...etfSymbols.map(symbol=>{const option=el("option","",symbol);option.value=symbol;return option;}));
  if(etfSymbols.includes(previousImport))importSelect.value=previousImport;
  const headers=["比较维度",...etfSymbols.map(symbol=>{
    const node=el("div","etf-column-heading"),button=el("button","text-button",symbol);button.type="button";button.dataset.etfFocus=`head:${symbol}`;
    button.setAttribute("aria-label",`展开 ${symbol} 完整持仓`);button.setAttribute("aria-pressed",String(researchState.etfSelected===symbol));button.addEventListener("click",()=>etfChoose(symbol));
    node.append(button,el("strong","",metaFor(symbol).theme),el("small","",metaFor(symbol).name));return node;
  })];
  const summary=etfTable(headers),stats=Object.fromEntries(etfSymbols.map(symbol=>[symbol,etfStatistics(funds[symbol])]));
  const metrics=[
    ["持仓日期",symbol=>funds[symbol]?.as_of || "待导入"],
    ["股票 / 全部项目",symbol=>stats[symbol] ? `${stats[symbol].stocks} / ${stats[symbol].items}` : "未取得"],
    ["行情样本截至",symbol=>researchDate(etfMarketAnalysis(remote[symbol]?.market,symbol)?.as_of)],
    ["近 20 交易日价格变化",symbol=>researchNumber(etfMarketAnalysis(remote[symbol]?.market,symbol)?.change20,2,"%")],
    ["近 60 交易日价格变化",symbol=>researchNumber(etfMarketAnalysis(remote[symbol]?.market,symbol)?.change60,2,"%")],
    ["行情计算口径",symbol=>etfMarketAnalysis(remote[symbol]?.market,symbol)?.basis || "待核验"],
    ["趋势判断（非预测）",symbol=>etfAnalysis(funds[symbol],remote[symbol]?.market,symbol).stance],
    ["前十大正权重股票",symbol=>stats[symbol] ? `${researchNumber(stats[symbol].top10)}%` : "--"],
    ["最大股票权重",symbol=>stats[symbol]?.largest ? `${stats[symbol].largest.symbol} · ${researchNumber(stats[symbol].largest.weight)}%` : "--"],
    ["非股票净权重",symbol=>stats[symbol] ? `${researchNumber(stats[symbol].other)}%` : "--"],
    ["全部项目权重合计",symbol=>stats[symbol] ? `${researchNumber(stats[symbol].total)}%` : "--"],
    ["持仓结构提示（推断）",symbol=>etfAnalysis(funds[symbol],remote[symbol]?.market,symbol).inference],
    ["投资范围",symbol=>metaFor(symbol).scope],
    ["权重方式",symbol=>metaFor(symbol).weighting],
    ["年费用率",symbol=>metaFor(symbol).fee==null?"待核验":`${researchNumber(metaFor(symbol).fee)}% · ${metaFor(symbol).feeDate}`],
  ];
  for (const [label,value] of metrics) {
    const row=el("tr"),heading=el("th","",label);heading.scope="row";row.append(heading);
    for (const symbol of etfSymbols) {
      const cell=el("td","",value(symbol));cell.classList.toggle("etf-selected-column",symbol===researchState.etfSelected);
      if (label==="前十大正权重股票" && stats[symbol]) {
        const bar=el("div","etf-concentration-bar"),fill=el("span",`etf-color-${symbol.toLowerCase()}`);fill.style.width=`${Math.max(0,Math.min(100,stats[symbol].top10))}%`;
        bar.setAttribute("aria-hidden","true");bar.append(fill);cell.append(bar);
      }
      row.append(cell);
    }
    summary.body.append(row);
  }
  const sourceRow=el("tr"),sourceHeading=el("th","","来源与状态");sourceHeading.scope="row";sourceRow.append(sourceHeading);
  for (const symbol of etfSymbols) {const cell=el("td"),meta=metaFor(symbol);researchLink(cell,etfCatalog[symbol]?"基金官网":"行情来源",meta.source);if(funds[symbol]?.source_url)researchLink(cell,"持仓原文",funds[symbol].source_url);for(const warning of remote[symbol]?.warnings||[])cell.append(el("p","quiet-note",warning));sourceRow.append(cell);}
  summary.body.append(sourceRow);researchEl("etf-comparison").replaceChildren(summary.table);
  const matrix=etfTable(["ETF",...etfSymbols]);
  for (const first of etfSymbols) {
    const row=el("tr"),heading=el("th","",first);heading.scope="row";row.append(heading);
    for (const second of etfSymbols) {
      const cell=el("td"),overlap=first===second ? null : etfOverlap(funds[first],funds[second]);
      if (!overlap) cell.textContent=first===second?"自身":"待导入";
      else {
        const button=el("button","etf-overlap-button");button.type="button";button.dataset.etfFocus=`pair:${first}:${second}`;
        button.append(el("strong","",`${researchNumber(overlap.weight)} 点`),el("small","",`${overlap.count} 个股票`));
        button.title=`${first} ${funds[first].as_of} / ${second} ${funds[second].as_of}`;
        button.setAttribute("aria-label",`查看 ${first} 与 ${second} 共同持仓，${overlap.count} 个股票，加权交集 ${researchNumber(overlap.weight)} 个百分点`);
        button.setAttribute("aria-pressed",String(researchState.etfPair?.includes(first) && researchState.etfPair?.includes(second)));
        button.addEventListener("click",()=>{researchState.etfSelectionEdited=true;researchState.etfPair=[first,second];researchEl("etf-overlap-filter").value="shared";renderEtfResearch(true);});cell.append(button);
      }
      row.append(cell);
    }
    matrix.body.append(row);
  }
  researchEl("etf-overlap-matrix").replaceChildren(matrix.table);
  const filter=researchEl("etf-overlap-filter").value, symbols=researchState.etfPair || etfSymbols;
  const compareRows=etfCompareRows(funds,{symbols,filter,search:researchEl("etf-overlap-search").value});
  const partial=available.length<etfSymbols.length;
  researchEl("etf-pair-reset").hidden=!researchState.etfPair;
  researchEl("etf-overlap-count").textContent=`${researchState.etfPair ? symbols.join(" × ") : `${etfSymbols.length} 只对照`} · ${compareRows.length} 条${partial ? " · 数据未齐，仅比较已有持仓" : ""}${filter==="unique" && partial ? "，不能据此认定所选组合中的独有持仓" : ""}`;
  const comparison=etfTable(["公司 / 代码",...etfSymbols]);
  for (const item of compareRows) {
    const row=el("tr"),identity=el("td");identity.append(el("strong","",instrumentName({target:item.key,name:item.name})),el("small","etf-code",item.key),instrumentBriefNode({target:item.key}));row.append(identity);
    for (const symbol of etfSymbols) row.append(el("td",funds[symbol] ? "etf-weight" : "etf-missing",!funds[symbol] ? "待导入" : Object.hasOwn(item.weights,symbol) ? `${researchNumber(item.weights[symbol])}%` : "未持有"));
    comparison.body.append(row);
  }
  researchEl("etf-overlap-list").replaceChildren(compareRows.length ? comparison.table : el("p","etf-empty",available.length<2 ? "至少导入两只 ETF 后才能比较重叠。" : "当前筛选没有匹配的股票。"));
  const tabs=researchEl("etf-fund-tabs");tabs.replaceChildren();
  for (const symbol of etfSymbols) {const button=el("button","",symbol);button.type="button";button.dataset.etfFocus=`fund:${symbol}`;button.classList.toggle("active",symbol===researchState.etfSelected);button.setAttribute("aria-pressed",String(symbol===researchState.etfSelected));button.addEventListener("click",()=>etfChoose(symbol));tabs.append(button);}
  renderEtfHoldings(funds);
  if (focused) Array.from(panel.querySelectorAll("[data-etf-focus]")).find(node=>node.dataset.etfFocus===focused)?.focus();
}

function renderEtfHoldings(funds) {
  const symbol=researchState.etfSelected,fund=funds[symbol],market=researchState.etfRemote[symbol]?.market,meta=etfMeta(symbol,market),container=researchEl("etf-holdings-list"),source=researchEl("etf-selected-source");
  source.replaceChildren(el("strong","",`${symbol} · ${meta.name}`),instrumentBriefNode(`US:${symbol}`));
  if (fund) {
    const stats=etfStatistics(fund);
    source.append(el("span","",`持仓 ${fund.as_of} · ${fund.provenance} · ${fund.filename} · 文件 SHA256 ${(fund.source_sha256 || "").slice(0,12)}`));
    if(stats.unmatched)source.append(el("span","negative",`${stats.unmatched} 个股票代码未识别，未纳入重叠计算`));
    if(fund.source_url)researchLink(source,"原始持仓文件",fund.source_url);
    for(const url of fund.source_urls || [])researchLink(source,"文件记录的来源",url);
    const inputId=researchState.etfRemote[symbol]?.input_id || researchState.etf.id;
    if(inputId){const button=el("button","text-button","冻结数据与版本");button.type="button";button.addEventListener("click",()=>openResearchDetail("inputs",inputId));source.append(button);}
  } else source.append(el("span","",meta.declared || "尚未取得完整持仓"));
  researchLink(source,etfCatalog[symbol]?"基金官网":"行情来源",meta.source);
  const rows=etfFilteredHoldings(fund,{search:researchEl("etf-holding-search").value,type:researchEl("etf-holding-type").value,sort:researchEl("etf-holding-sort").value});
  researchEl("etf-holding-count").textContent=fund ? `${rows.length} / ${fund.holdings.length} 条项目 · 权重沿用原文` : "待导入完整文件";
  researchEl("etf-holdings-export").disabled=!fund;
  if (!fund || !rows.length) container.replaceChildren(el("p","etf-empty",fund ? "当前筛选没有匹配的持仓项目。" : "尚未导入：集中度、权重和重叠数据不作估算。"));
  else {
    const table=etfTable(["公司 / 持仓项目","代码","类型","净资产权重","原文行号"]);
    for (const item of rows) {
      const row=el("tr"),name=el("td");name.append(el("strong","",etfHoldingName(item)));if(item.type==="stock")name.append(instrumentBriefNode({target:item.key}));
      row.append(name,el("td","etf-code",item.symbol || "--"),el("td","",etfTypes[item.type] || item.type),el("td",`etf-weight${item.weight<0 ? " negative" : ""}`,etfWeight(item.weight)),el("td","etf-weight",String(item.source_row)));
      table.body.append(row);
    }
    container.replaceChildren(table.table);
  }
  const analysis=etfAnalysis(fund,market,symbol),focus=researchEl("etf-research-focus");focus.replaceChildren(el("h3","",`${symbol} · 自动分析`));
  for(const fact of analysis.facts)focus.append(el("p","",`事实：${fact}`));
  focus.append(el("p","",`推断：${analysis.inference}`),el("p","",meta.focus));
  const conditions=el("div","etf-conditions");for(const [label,value] of [["有条件看多（推断）",meta.condition],["失效 / 反证线索",meta.invalidation]]){const section=el("div");section.append(el("strong","",label),el("p","",value));conditions.append(section);}focus.append(conditions);
}

async function importEtfFile(event) {
  event.preventDefault();const file=researchEl("etf-import-file").files[0],button=event.submitter,status=researchEl("etf-import-status");
  if (!file) return;
  if (file.size>1000000) {status.textContent="文件超过 1 MB，未导入。";return;}
  button.disabled=true;status.textContent="正在校验持仓文件…";
  try {
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(Error("无法读取文件"));reader.readAsDataURL(file);});
    const result=await api("/api/research/etf/import",{method:"POST",body:{filename:file.name,data,symbol:researchEl("etf-import-symbol").value,as_of:researchEl("etf-import-date").value}});
    status.textContent=`已导入 ${result.imported.join("、")} · 快照 v${result.version}`;await refreshResearch();
  } catch(error) {status.textContent=`导入失败：${error.message}${/404|不存在|Not Found/i.test(error.message) ? "；新版导入接口需重新启动 STock。" : ""}`;}
  finally{button.disabled=false;}
}

function exportEtfHoldings() {
  const symbol=researchState.etfSelected,fund=researchState.etf?.payload?.funds?.[symbol];if(!fund)return;
  const quote=value=>`"${String(value ?? "").replaceAll('"','""')}"`;
  const lines=[["ETF","持仓日期","公司","代码","类型","净资产权重(%)","原文权重","原文行号"],...fund.holdings.map(row=>[symbol,fund.as_of,etfHoldingName(row),row.symbol,etfTypes[row.type],row.weight,row.raw_weight,row.source_row])];
  const url=URL.createObjectURL(new Blob(["\ufeff",lines.map(row=>row.map(quote).join(",")).join("\r\n")],{type:"text/csv;charset=utf-8"}));
  const link=el("a");link.href=url;link.download=`${symbol}_holdings_${fund.as_of}.csv`;link.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}

document.addEventListener("DOMContentLoaded",()=>{
  researchEl("etf-add-form").addEventListener("submit",addEtfSelection);
  researchEl("etf-selection-form").addEventListener("submit",event=>{event.preventDefault();changeEtfSelection();});
  researchEl("etf-symbols").addEventListener("input",event=>{
    researchState.etfSelectionEdited=true;
    ++researchState.etfLoad;clearTimeout(researchState.etfTimer);
    if(!event.isComposing)researchState.etfTimer=setTimeout(changeEtfSelection,800);
  });
  const etfKind=el("option","","ETF 持仓快照");etfKind.value="etf_holdings";researchEl("research-kind").append(etfKind);
  researchEl("etf-import-form").addEventListener("submit",importEtfFile);
  researchEl("etf-holdings-export").addEventListener("click",exportEtfHoldings);
  ["etf-holding-search","etf-overlap-search"].forEach(id=>researchEl(id).addEventListener("input",()=>{researchState.etfSelectionEdited=true;renderEtfResearch(true);}));
  ["etf-holding-type","etf-holding-sort","etf-overlap-filter"].forEach(id=>researchEl(id).addEventListener("change",()=>{researchState.etfSelectionEdited=true;renderEtfResearch(true);}));
  researchEl("etf-pair-reset").addEventListener("click",()=>{researchState.etfSelectionEdited=true;researchState.etfPair=null;renderEtfResearch(true);});
  const financialKind=el("option","","公开年度财报");financialKind.value="fundamentals";researchEl("research-kind").append(financialKind);
  const cardKind=el("option","","公司与策略研究");cardKind.value="research_card";researchEl("research-kind").append(cardKind);
  const valuationKind=el("option","","估值研究假设");valuationKind.value="valuation_model";researchEl("research-kind").append(valuationKind);
  researchEl("strategy-new").addEventListener("click",openResearchCompanyAdd);
  researchEl("strategy-compare-add").addEventListener("click",()=>{
    const card=researchState.cards?.items.find(item=>item.id===researchEl("strategy-compare-select").value);
    if(card && strategyComparison.size<4){strategyComparison.set(card.id,card);renderStrategyComparison();}
  });
  researchEl("research-company-add").addEventListener("submit",addResearchCompany);
  researchEl("research-company-private").addEventListener("click",()=>openStrategyEditor({kind:"cvc"}));
  researchEl("research-company-cancel").addEventListener("click",()=>{researchEl("research-company-add").hidden=true;researchEl("strategy-new").focus();});
  researchEl("strategy-editor").addEventListener("submit",saveStrategyCard);
  researchEl("company-search").addEventListener("input",()=>{researchState.navigationEdited=true;renderCompanyLandscape();});
  ["company-layer","company-entity"].forEach(id=>researchEl(id).addEventListener("change",()=>{researchState.navigationEdited=true;renderCompanyLandscape();}));
  researchEl("company-reset").addEventListener("click",()=>{
    researchState.navigationEdited=true;researchEl("company-search").value="";researchEl("company-layer").value="all";researchEl("company-entity").value="all";renderCompanyLandscape();researchEl("company-search").focus();
  });
  researchEl("company-list").addEventListener("keydown",event=>{
    const buttons=Array.from(event.currentTarget.querySelectorAll("button")),index=buttons.indexOf(event.target);
    if (index<0 || !["ArrowDown","ArrowUp","Home","End"].includes(event.key)) return;
    event.preventDefault();const next=event.key==="Home" ? 0 : event.key==="End" ? buttons.length-1 : Math.max(0,Math.min(buttons.length-1,index+(event.key==="ArrowDown" ? 1 : -1)));
    buttons[next].focus();buttons[next].click();
  });
  ["strategy-search","strategy-stance"].forEach(id=>researchEl(id).addEventListener("change",()=>{researchState.navigationEdited=true;researchState.cardOffset=0;refreshResearch();}));
  researchEl("strategy-search").addEventListener("input",()=>{researchState.navigationEdited=true;});
  const companyTabs=Array.from(document.querySelectorAll("#company-research-views button"));
  companyTabs.forEach((button,index)=>{
    button.addEventListener("click",()=>selectCompanyResearch(button.dataset.tab));
    button.addEventListener("keydown",event=>{
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
      event.preventDefault();
      const next=event.key==="Home"?0:event.key==="End"?companyTabs.length-1:(index+(event.key==="ArrowRight"?1:-1)+companyTabs.length)%companyTabs.length;
      companyTabs[next].focus();companyTabs[next].click();
    });
  });
  document.querySelectorAll("#research-tabs button, #market-nav [data-tab], #backtest-tabs [data-tab]").forEach(button=>button.addEventListener("click",()=>{
    researchState.navigationEdited=true;
    researchState.tab=button.dataset.tab;
    setView("research");
  }));
  const evidenceTabs=[...document.querySelectorAll("#research-evidence-views button")];
  evidenceTabs.forEach((button,index)=>{
    button.addEventListener("click",()=>{researchState.navigationEdited=true;researchState.tab=button.dataset.tab;setView("research");});
    button.addEventListener("keydown",event=>{
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
      event.preventDefault();const next=event.key==="Home"?0:event.key==="End"?evidenceTabs.length-1:1-index;
      evidenceTabs[next].focus();evidenceTabs[next].click();
    });
  });
  ["research-asset","research-horizon","research-status","research-kind"].forEach(id=>researchEl(id).addEventListener("change",()=>{briefAfterTitle("research-asset",{asset:researchEl("research-asset").value});researchState.offset=0;researchState.inputOffset=0;refreshResearch();}));
  researchEl("research-refresh").addEventListener("click",()=>refreshResearch());
  researchEl("research-cohort").addEventListener("change",renderResearchGroup);
  [["research-prev",-50],["research-next",50]].forEach(([id,step])=>researchEl(id).addEventListener("click",()=>{const key=researchState.tab==="cards"?"cardOffset":researchState.tab==="inputs"?"inputOffset":"offset"; researchState[key]=Math.max(0,researchState[key]+step);refreshResearch();}));
  researchEl("research-detail-dialog").addEventListener("close",()=>{researchState.detailRequest++;});
  researchEl("research-settings-button").addEventListener("click",()=>{
    if (!researchState.data) return; const settings=researchState.data.settings, form=researchEl("research-settings-form");
    form.elements.enabled.checked=settings.enabled; form.elements.latency_seconds.value=settings.latency_seconds;
    ["gold","btc"].forEach(asset=>{form.elements[`${asset}_fee`].value=settings.costs[asset.toUpperCase()].fee_bps/100;form.elements[`${asset}_slip`].value=settings.costs[asset.toUpperCase()].slippage_bps/100;});
    researchEl("research-settings-error").textContent="";researchEl("research-settings-dialog").showModal();
  });
  researchEl("research-settings-form").addEventListener("submit",async event=>{
    event.preventDefault();const form=event.currentTarget, button=event.submitter || form.querySelector('[type="submit"]');button.disabled=true;
    try {const body={enabled:form.elements.enabled.checked,latency_seconds:Number(form.elements.latency_seconds.value),costs:{}};
      ["gold","btc"].forEach(asset=>body.costs[asset.toUpperCase()]={fee_bps:Number(form.elements[`${asset}_fee`].value)*100,slippage_bps:Number(form.elements[`${asset}_slip`].value)*100});
      await api("/api/research/settings",{method:"POST",body});researchEl("research-settings-dialog").close();showToast("新方案已保存，历史记录保持不变","success");await refreshResearch();
    } catch(error){researchEl("research-settings-error").textContent=error.message;} finally{button.disabled=false;}
  });
  renderResearch();
  window.setInterval(()=>{if(state.currentView==="research" && document.visibilityState==="visible")refreshResearch({silent:true});},5000);
});
