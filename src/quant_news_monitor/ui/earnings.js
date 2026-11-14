"use strict";

const earningsState = {data:null, offset:0, request:0, pending:null, lastFetch:0, signature:"", error:"", collecting:false,
  selectedSymbol:"", financialPending:null, financialLastFetch:0, financialError:"", selectionEdited:false};
const earningsSessions = {before_open:"盘前", after_close:"盘后", unknown:"时段未公布"};
const earningsStatuses = {estimated:"预计", conflict:"来源日期分歧", unknown:"日期待公布", expired:"旧日期待更新"};

function earningsParams(paginated = true) {
  const params = new URLSearchParams({search:researchEl("earnings-search").value.trim(), days:researchEl("earnings-days").value,
    scope:researchEl("earnings-scope").value, sort:researchEl("earnings-sort").value,
    include_unknown:researchEl("earnings-unknown").checked ? "1" : "0"});
  if (paginated) params.set("offset", earningsState.offset);
  return params;
}

function earningsMoney(value) {
  if (value == null || !Number.isFinite(Number(value))) return "--";
  const amount = Number(value);
  return amount >= 100000000 ? `$${researchNumber(amount/100000000,2)} 亿` : `$${researchNumber(amount/10000,0)} 万`;
}

function earningsCell(main, secondary, className = "") {
  const cell=el("td",className);cell.append(el("span","",main));
  if(secondary)cell.append(el("small","",secondary));
  return cell;
}

function renderEarnings() {
  const data=earningsState.data, runtime=data?.runtime;
  const status=researchEl("earnings-status"), refresh=researchEl("earnings-source-refresh");
  refresh.disabled=earningsState.collecting || Boolean(runtime?.refresh_in_progress) || (runtime?.retry_after_seconds || 0)>0;
  refresh.title=runtime?.retry_after_seconds>0 ? `来源冷却中，约 ${runtime.retry_after_seconds} 秒后可检查` : "检查新的财报预告";
  refresh.setAttribute("aria-busy",String(earningsState.collecting || Boolean(runtime?.refresh_in_progress)));
  researchEl("earnings-table").setAttribute("aria-busy",String(Boolean(earningsState.pending)));
  status.classList.toggle("error",Boolean(earningsState.error || runtime?.error));
  status.textContent=earningsState.error || runtime?.error || (runtime?.refresh_in_progress ? "正在检查最新预告，当前保留已有目录…" : runtime?.warnings?.join("；") || (runtime?.stale ? "目录已过期，等待来源更新；下方保留上次结果。" : ""));
  researchEl("research-runtime").textContent=runtime?.collected_at ? `${runtime.stale ? "旧目录" : "目录采集"} ${researchDate(runtime.collected_at)}` : "等待财报目录";
  if(!data){
    researchEl("earnings-coverage").textContent="正在读取美股公司库…";
    if(!earningsState.signature)researchEl("earnings-table").replaceChildren(el("p","earnings-empty",earningsState.error ? "财报目录暂时不可用" : "正在读取财报预告…"));
    return;
  }
  const coverage=data.coverage;
  researchEl("earnings-coverage").textContent=`公司库 ${researchNumber(coverage.companies,0)} 家 · 热门及高成交额 ${researchNumber(coverage.liquid_popular,0)} 家 · 其中日期待更新 ${researchNumber(coverage.unknown_dates,0)} 家`;
  const signature=JSON.stringify([data.items, data.today, data.offset, data.total]);
  if(signature!==earningsState.signature){
    earningsState.signature=signature;
    const container=researchEl("earnings-table");container.replaceChildren();
    if(!data.items.length){
      container.append(el("p","earnings-empty",coverage.companies ? "当前条件下暂无财报预告" : "尚未取得公司目录，请检查来源状态"));
    } else {
      const table=el("table","earnings-table"), head=el("thead"), headers=el("tr"), body=el("tbody");
      ["预计公布（美东）","公司 / 预告详情"].forEach(text=>{const cell=el("th","",text);cell.scope="col";headers.append(cell);});
      head.append(headers);table.append(head,body);
      data.items.forEach(item=>{
        const row=el("tr"), countdown=item.days_until==null ? "" : item.days_until<0 ? "旧记录" : item.days_until===0 ? "美东今天" : `${item.days_until} 天后`;
        row.dataset.symbol=item.symbol;
        row.append(earningsCell(item.earnings_date || "--",countdown,"earnings-date"));
        const identity=el("td","earnings-identity"), label=el("button","earnings-company-label");
        label.type="button";label.dataset.earningsSymbol=item.symbol;
        label.setAttribute("aria-label",`查看 ${item.symbol} ${item.name} 已公布财报`);
        label.setAttribute("aria-controls","fundamental-records");
        label.addEventListener("click",()=>selectEarningsCompany(item.symbol));
        label.append(el("strong","",item.symbol));
        if(item.held)label.append(el("span","earnings-flag","持仓"));
        else if(item.watched)label.append(el("span","earnings-flag","股票池"));
        identity.append(label,el("span","earnings-company-name",item.name),instrumentBriefNode(item));
        identity.append(el("small","",`${earningsSessions[item.session] || earningsSessions.unknown} · ${earningsStatuses[item.date_status] || "预计"}`));
        const details=el("details","earnings-row-details");
        details.append(el("summary","","预期、成交额与来源"),
          el("p","",`报告期 ${item.fiscal_quarter || "--"} · ${item.eps_forecast==null ? "EPS 预期未取得" : `EPS 预期 $${researchNumber(item.eps_forecast)}`}`),
          el("p","earnings-money",`最新成交额 ${earningsMoney(item.dollar_volume)}（最新价格 × 最新成交量）`),
          el("p","earnings-money",`30日均量折算 ${earningsMoney(item.average_dollar_volume)}（最新价格 × 30日均量）`));
        const sources=el("div","earnings-sources");sources.append(el("span","",earningsStatuses[item.date_status] || "预计"));
        if(item.date_conflict)sources.classList.add("earnings-conflict");
        item.sources.forEach(source=>{
          if(!/^https:\/\/(www\.)?(tradingview\.com|nasdaq\.com)\//.test(source.url || ""))return;
          const link=el("a","",source.name);link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";
          link.title=`${source.name} · ${source.date || "日期未提供"}`;
          link.setAttribute("aria-label",`${item.symbol} ${link.title}`);
          sources.append(link);
          if(item.date_conflict)sources.append(el("small","",`${source.name}: ${source.date || "--"}`));
        });
        details.append(sources);identity.append(details);row.append(identity);body.append(row);
      });
      container.append(table);
    }
  }
  researchEl("earnings-page-label").textContent=data.total ? `${data.offset+1}–${Math.min(data.offset+data.limit,data.total)} / ${data.total} 家` : "0 家";
  researchEl("earnings-prev").disabled=data.offset===0 || Boolean(earningsState.pending);
  researchEl("earnings-next").disabled=data.offset+data.limit>=data.total || Boolean(earningsState.pending);
  renderCompanyFinancials();
}

function selectedCompanyFinancials(items, symbol) {
  return (items || []).find(item=>item.symbol===symbol) || null;
}

function selectEarningsCompany(symbol) {
  if(!/^[A-Z0-9][A-Z0-9.-]{0,19}$/.test(symbol))return;
  earningsState.selectedSymbol=symbol;earningsState.selectionEdited=true;
  researchState.navigationEdited=true;
  renderCompanyFinancials();
  if(typeof saveStartupPreferences==="function")saveStartupPreferences();
  if(window.innerWidth<1000)researchEl("research-fundamentals-panel").scrollIntoView({block:"start"});
}

function renderCompanyFinancials() {
  const data=researchState.fundamentals, items=data?.items || [], runtime=data?.runtime || {};
  const dates=earningsState.data?.items || [];
  if(!earningsState.selectedSymbol && (data || earningsState.financialError))earningsState.selectedSymbol=dates.find(row=>selectedCompanyFinancials(items,row.symbol))?.symbol || items[0]?.symbol || dates[0]?.symbol || "";
  const symbol=earningsState.selectedSymbol, report=selectedCompanyFinancials(items,symbol), upcoming=dates.find(item=>item.symbol===symbol);
  const picker=researchEl("fundamental-company"), choices=items.map(item=>[item.symbol,`${item.symbol}${item.company ? ` · ${item.company}` : ""}`]);
  if(symbol && !report)choices.unshift([symbol,`${symbol} · 未取得财报`]);
  const choicesSignature=JSON.stringify(choices);
  if(picker.choicesSignature!==choicesSignature){
    picker.choicesSignature=choicesSignature;picker.replaceChildren();
    for(const [value,label] of choices){const option=el("option","",label);option.value=value;picker.append(option);}
  }
  picker.value=symbol;picker.disabled=!choices.length;
  const counts=runtime.coverage || {};
  researchEl("fundamental-coverage").textContent=data ? `财报覆盖 ${counts.total ?? items.length} 家 · 已采集 ${counts.available ?? items.filter(row=>row.status==="available").length} · 待采集 ${counts.waiting || 0} · 未覆盖 ${counts.unsupported || 0}` : "尚未取得财报目录";
  const status=researchEl("fundamental-status");
  status.textContent=earningsState.financialError || runtime.error || (earningsState.financialPending ? "正在读取财报，保留上次结果…" : data ? `${runtime.enabled ? "财报采集已开启" : "财报采集已暂停"}${runtime.refresh_in_progress ? " · 采集中" : ""} · 已采集财报每 ${Math.round((runtime.interval_seconds || 21600)/3600)} 小时检查` : "等待读取公司财报");
  status.classList.toggle("error",Boolean(earningsState.financialError || runtime.error));
  for(const button of researchEl("earnings-table").querySelectorAll?.("[data-earnings-symbol]") || [])button.setAttribute("aria-pressed",String(button.dataset.earningsSymbol===symbol));
  for(const row of researchEl("earnings-table").querySelectorAll?.("tr[data-symbol]") || [])row.classList.toggle("selected",row.dataset.symbol===symbol);
  const container=researchEl("fundamental-records"), signature=JSON.stringify([symbol,report,upcoming,data!==null]);
  if(container.financialSignature===signature)return;
  container.financialSignature=signature;container.replaceChildren();
  if(!symbol){container.append(el("p","earnings-empty","选择日程中的公司，或等待财报目录加载。"));return;}
  container.append(el("h3","",`${symbol} · ${report?.company || upcoming?.name || "公司财报"}`),instrumentBriefNode({market:"US",symbol}));
  container.append(el("p","quiet-note",upcoming ? `公布日程 ${upcoming.earnings_date || "日期待公布"} · ${earningsSessions[upcoming.session] || earningsSessions.unknown} · ${earningsStatuses[upcoming.date_status] || "预计"}（美东，非实际业绩）` : "该公司不在当前筛选的日程结果中。"));
  if(!report){container.append(el("p","earnings-empty",`${symbol} 尚无已取得的财报数据；不会以其他公司数据或零值代替。`));return;}
  const labels={waiting:"待采集",available:"已采集",unsupported:"未覆盖",stale:"保留旧数据",error:"来源错误"};
  container.append(el("p","quiet-note",`${labels[report.status] || report.status || "参考快照"} · 年度报告期 ${report.data_as_of || "未取得"} · 采集 ${researchDate(report.collected_at)}`));
  const metrics=el("div","company-fields");
  Object.values(report.metrics || {}).forEach(metric=>researchField(metrics,metric.label,`${researchNumber(metric.value)} ${metric.unit || ""} · ${metric.start || "--"} 至 ${metric.end || "--"} · 申报 ${metric.filed || "--"}${metric.growth_pct==null?"":` · 同比 ${researchNumber(metric.growth_pct)}%`}`));
  if(!Object.keys(report.metrics || {}).length)metrics.append(el("p","quiet-note","财务指标尚未取得，不以零值代替。"));
  if(report.valuation)researchField(metrics,"最近财年市盈率（非 TTM）",`${researchNumber(report.valuation.annual_pe)} · 价格时点 ${researchDate(report.valuation.price_as_of)} · EPS报告期 ${report.valuation.eps_as_of || "--"}`);
  container.append(metrics,el("p","quiet-note",report.error || report.note || "年度申报数据，不能直接与日程中的季度 EPS 预期比较。"));
  if(report.valuation)container.append(el("p","quiet-note",report.valuation.error || report.valuation.note || ""));
  if(report.source_url)researchLink(container,"SEC 原始申报",report.source_url);
  if(report.input_version_id){const button=el("button","text-button","财报数据版本");button.type="button";button.addEventListener("click",()=>openResearchDetail("inputs",report.input_version_id));container.append(button);}
}

async function refreshCompanyFinancials({silent=false}={}) {
  if(earningsState.financialPending)return earningsState.financialPending;
  if(silent && Date.now()-earningsState.financialLastFetch<60000)return;
  earningsState.financialError="";
  const pending=api("/api/fundamentals").then(data=>{researchState.fundamentals=data;earningsState.financialLastFetch=Date.now();})
    .catch(error=>{earningsState.financialError=`公司财报读取失败：${error.message}。保留上次结果。`;})
    .finally(()=>{earningsState.financialPending=null;if(researchState.tab==="earnings")renderCompanyFinancials();});
  earningsState.financialPending=pending;renderCompanyFinancials();return pending;
}

async function refreshEarningsCenter(options={}) {
  await Promise.all([refreshEarnings(options),refreshCompanyFinancials(options)]);
}

async function refreshEarnings({silent=false}={}) {
  const query=earningsParams().toString();
  if(earningsState.pending===query)return;
  const cadence=earningsState.data?.runtime?.refresh_in_progress ? 5000 : 60000;
  if(silent && Date.now()-earningsState.lastFetch<cadence)return;
  const sequence=++earningsState.request;
  earningsState.pending=query;earningsState.error="";renderEarnings();
  try{
    const data=await api(`/api/research/earnings?${query}`);
    if(sequence!==earningsState.request || query!==earningsParams().toString())return;
    earningsState.data=data;earningsState.offset=data.offset;earningsState.lastFetch=Date.now();
    if (typeof refreshInstrumentEarnings === "function" && data.runtime.collected_at !== instrumentEarningsState.data?.runtime?.collected_at) refreshInstrumentEarnings();
  } catch(error){
    if(sequence===earningsState.request)earningsState.error=`财报目录读取失败：${error.message}。保留上次结果。`;
  } finally{
    if(sequence===earningsState.request){earningsState.pending=null;if(researchState.tab==="earnings")renderEarnings();}
  }
}

async function refreshEarningsSource() {
  if(earningsState.collecting)return;
  earningsState.collecting=true;earningsState.error="";renderEarnings();
  try{
    const result=await api("/api/research/earnings/refresh",{method:"POST",body:{}});
    if(!result.accepted)throw Error("来源正在采集或冷却中");
    await refreshEarnings();
  } catch(error){earningsState.error=`检查预告未启动：${error.message}`;}
  finally{earningsState.collecting=false;if(researchState.tab==="earnings")renderEarnings();}
}

document.addEventListener("DOMContentLoaded",()=>{
  let timer;
  const filter=()=>{earningsState.selectionEdited=true;researchState.navigationEdited=true;earningsState.offset=0;refreshResearch();};
  researchEl("fundamental-company").addEventListener("change",event=>selectEarningsCompany(event.target.value));
  researchEl("earnings-search").addEventListener("input",()=>{earningsState.selectionEdited=true;researchState.navigationEdited=true;window.clearTimeout(timer);timer=window.setTimeout(filter,250);});
  ["earnings-days","earnings-scope","earnings-sort","earnings-unknown"].forEach(id=>researchEl(id).addEventListener("change",filter));
  researchEl("earnings-source-refresh").addEventListener("click",refreshEarningsSource);
  [["earnings-prev",-100],["earnings-next",100]].forEach(([id,step])=>researchEl(id).addEventListener("click",()=>{
    earningsState.offset=Math.max(0,earningsState.offset+step);refreshResearch();
  }));
});
