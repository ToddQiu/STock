(function () {
  "use strict";

  const jobs = new Map();
  const number = value => value == null || value === "" || typeof value === "boolean" ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  const pct = value => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  const snapshotKey = item => JSON.stringify([item.market, item.symbol, item.detected_at, item.quote_time, item.price, item.change_pct, item.relative_volume, item.movement_type]);
  const targetKey = item => `${item.market}:${String(item.symbol).toUpperCase()}`;

  function assessment(item) {
    const change = number(item.change_pct), volume = item.live_extended ? null : number(item.relative_volume);
    const up = change !== null && change > 0;
    const fund = /ETF|ETN|基金|交易所交易|倍做|倍反向/i.test(`${item.name || ""} ${item.asset_type || ""}`);
    const fact = [change === null ? "涨跌幅缺失，暂不判断方向" : `${item.session_label || "本次扫描"} ${pct(change)}`,
      volume === null ? "缺少可比量比" : `成交量为10日均量 ${volume.toFixed(1)} 倍`].join("；") + "。";
    let reverse;
    if (change === null || change === 0) reverse = "先核对报价时点与比较基准，再判断是否存在有效异动。";
    else if (fund) reverse = "先拆解底层指数的变动、跟踪方向与杠杆倍数、净值及溢折价；反向基金可能与指数反向变化，不能直接套用公司的盈利解释。";
    else if (Math.abs(change) >= 30) reverse = `幅度达 ${Math.abs(change).toFixed(2)}%，先排查拆并股、复牌及价格基准，再核对是否有足以改变盈利或估值的事件。放量不能单独证明资金净流入或逼空。`;
    else if (volume !== null && volume >= 2) reverse = `价${up ? "升" : "跌"}量增，需区分公司事件与板块共振：若同业平稳，应重点核对公司消息；若同业同步，则检验行业或宏观因素。`;
    else reverse = `${up ? "上涨" : "下跌"}${volume !== null && volume < 1 ? "但成交量低于10日均量" : "尚缺少强量能佐证"}，需优先检查板块联动、流动性与报价时效，不能仅凭价格确认基本面变化。`;
    const checks = [
      ["事件链", "核对公告、新闻原文及发布时间：事件是否发生在异动之前，是否明确涉及该标的，影响的是收入、利润、现金流还是估值。"],
      ["竞争解释", fund ? "对比底层指数、同类基金和净值；若指数变化不足以解释价格，继续核对溢折价、汇率和流动性。" : "对比行业与大盘同一时段表现；再检查公司行动、成交结构及消息是否已被提前交易。缺少数据时保留多种解释。"],
      ["反证", item.reason_details?.relation === "opposed" ? "现有消息的方向与价格相反，不能作为充分解释；需寻找抵消因素或调整原假设。" : "若消息发生在异动之后、内容已属旧闻，或同业并不同步，需降低相应解释的可信度。"],
      ["后续验证", `下一交易时段检查${up ? "涨幅能否保留" : "跌势是否延续"}、量能与同业表现；一周内跟进公告中的业务指标。价格延续只验证走势，不能独立证明原因。`],
    ];
    return {fact, reverse, checks};
  }

  function sameSnapshot(item, response) {
    const other = response?.movement;
    if (!other) return false;
    const normalized = value => String(value || "").toUpperCase().replace(/\.(SS|SZ|HK|KS|KQ)$/, "").replace(/^0+(?=\d)/, "");
    return response.market === item.market && normalized(response.symbol) === normalized(item.symbol)
      && ["price", "change_pct", "relative_volume"].every(key => number(other[key]) === number(item[key]))
      && String(other.detected_at || "") === String(item.detected_at || "");
  }

  function paragraph(label, value, className = "") {
    const p = el("p", className);
    p.append(el("strong", "mi-label", `${label} `), document.createTextNode(value));
    return p;
  }

  function renderAnalysis(parent, result) {
    const analysis = result.analysis;
    if (analysis.method === "evidence_check") parent.append(paragraph("证据不足", "仅形成核查路径，未生成 AI 归因结论。"));
    parent.append(paragraph("核实结论", analysis.conclusion));
    const details = el("details", "mi-deep-details");
    details.append(el("summary", "", "推理、反证与来源"));
    (analysis.facts || []).forEach(text => details.append(paragraph("事实", text)));
    (analysis.hypotheses || []).forEach(hypothesis => {
      const section = el("section", "mi-hypothesis");
      section.append(el("h4", "", `待验证 · ${hypothesis.cause}`), paragraph("传导逻辑", hypothesis.mechanism));
      if (hypothesis.evidence?.length) section.append(paragraph("支持线索", hypothesis.evidence.join("；")));
      section.append(paragraph("反证／缺口", hypothesis.against), paragraph("如何验证", hypothesis.verify), paragraph("何时推翻", hypothesis.invalidate));
      details.append(section);
    });
    const sources = el("ol", "mi-sources");
    (analysis.sources || []).forEach(source => {
      const safeLink = /^https?:\/\//i.test(source.url || "");
      if (!safeLink && source.scope !== "scanner_snapshot") return;
      const li = el("li"), link = el(safeLink ? "a" : "span", "", `[${source.id}] ${source.title}`);
      if (safeLink) { link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
      const scope = source.scope === "title_only" ? "仅标题" : source.scope === "title_summary" ? "标题与摘要" : "行情快照";
      li.append(link, el("small", "", [source.source, source.published_at ? formatDate(source.published_at) : "发布时间待核实", scope,
        source.timing === "after_detection" ? "晚于异动采样" : ""].filter(Boolean).join(" · ")));
      sources.append(li);
    });
    details.append(sources, el("small", "mi-asof", `分析时间 ${formatDate(analysis.as_of || result.as_of)} · 对应本次异动快照`));
    parent.append(details);
  }

  function paint(node, item, entry) {
    const content = node.querySelector(".mi-ai-content"), button = node.querySelector(".mi-run"), status = node.querySelector(".mi-status");
    content.replaceChildren();
    const busy = entry?.status === "running";
    button.disabled = busy;
    button.textContent = busy ? "核实中…" : entry?.status === "ready" ? "重新核实" : "深入核实（AI）";
    const changed = entry && entry.key !== snapshotKey(item);
    const snapshotNote = changed ? `对应 ${formatDate(entry.item.detected_at)} 采样价 ${entry.item.price}；当前行情已变化。` : "";
    status.textContent = busy ? `${snapshotNote}正在采集消息并检查原因` : entry?.error || "";
    status.classList.toggle("mi-error", entry?.status === "error");
    if (entry?.status === "ready" && entry.result?.analysis) {
      if (changed) content.append(paragraph("较早快照", snapshotNote));
      renderAnalysis(content, entry.result);
    }
    node.querySelector(".mi-local").hidden = entry?.status === "ready";
  }

  function update(entry) {
    document.querySelectorAll(".movement-investigation").forEach(node => {
      if (node.dataset.target === targetKey(entry.item)) paint(node, node.movementItem, entry);
    });
  }

  async function run(item) {
    const key = snapshotKey(item);
    if (jobs.get(targetKey(item))?.status === "running") return;
    const entry = {key, item, status:"running", result:null, error:null};
    jobs.set(targetKey(item), entry); update(entry);
    try {
      const response = await api("/api/movements/investigation", {method:"POST", body:{market:item.market, symbol:item.symbol, force:true}});
      accept(entry, response);
    } catch (error) { entry.status = "error"; entry.error = error.message; update(entry); }
  }

  function accept(entry, response) {
    if (response.status === "error" && !response.movement?.detected_at) {
      entry.status = "error"; entry.error = response.error || "核实记录不可用，请重试。";
    } else if (!sameSnapshot(entry.item, response)) {
      entry.status = "error"; entry.error = "行情快照已变化，请刷新异动列表后重新核实。";
    } else {
      entry.result = response; entry.status = response.status;
      entry.error = response.error || (response.status === "idle" ? "本次核实已失效，请重新核实。" : "");
    }
    update(entry);
    if (entry.status === "running") window.setTimeout(async () => {
      try {
        const response = await api(`/api/movements/investigation?market=${encodeURIComponent(entry.item.market)}&symbol=${encodeURIComponent(entry.item.symbol)}&movement_signature=${encodeURIComponent(entry.result?.movement_signature || "")}`);
        accept(entry, response);
      } catch (error) { entry.status = "error"; entry.error = error.message; update(entry); }
    }, 2500);
  }

  function append(parent, item) {
    const node = el("div", "movement-investigation"), local = el("div", "mi-local");
    node.dataset.snapshot = snapshotKey(item);
    node.dataset.target = targetKey(item);
    node.movementItem = item;
    const reasoning = assessment(item);
    local.append(paragraph("量价", reasoning.fact), paragraph("倒推 · 待验证", reasoning.reverse));
    const details = el("details", "mi-checks");
    details.append(el("summary", "", "验证路径与反证"));
    reasoning.checks.forEach(([label, text]) => details.append(paragraph(label, text)));
    local.append(details);
    const content = el("div", "mi-ai-content"), actions = el("div", "mi-actions");
    const button = el("button", "mi-run", "深入核实（AI）");
    button.type = "button";
    button.setAttribute("aria-label", `深入核实 ${item.name || item.symbol} 的异动原因`);
    button.addEventListener("click", () => run(item));
    const status = el("span", "mi-status"); status.setAttribute("role", "status");
    actions.append(button, status);
    node.append(local, content, actions);
    node.addEventListener("click", event => event.stopPropagation());
    node.addEventListener("keydown", event => event.stopPropagation());
    parent.append(node);
    paint(node, item, jobs.get(node.dataset.target));
  }

  window.MovementInvestigation = {append, assessment, sameSnapshot};
})();
