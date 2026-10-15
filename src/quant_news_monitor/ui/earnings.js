"use strict";

const earningsState = {data:null, offset:0, request:0, pending:null, lastFetch:0, signature:"", error:"", collecting:false};
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
      ["预计发布日期（美东）","公司 / 主营业务","发布时段","报告期 / EPS 预期","最新成交额（估算）","30日均量折算","日期状态 / 来源"].forEach(text=>{const cell=el("th","",text);cell.scope="col";headers.append(cell);});
      head.append(headers);table.append(head,body);
      data.items.forEach(item=>{
        const row=el("tr"), countdown=item.days_until==null ? "" : item.days_until<0 ? "旧记录" : item.days_until===0 ? "美东今天" : `${item.days_until} 天后`;
        row.append(earningsCell(item.earnings_date || "--",countdown,"earnings-date"));
        const identity=el("td","earnings-identity"), label=el("div","earnings-company-label");
        label.append(el("strong","",item.symbol));
        if(item.held)label.append(el("span","earnings-flag","持仓"));
        else if(item.watched)label.append(el("span","earnings-flag","股票池"));
        identity.append(label,el("span","earnings-company-name",item.name),instrumentBriefNode(item));
        row.append(identity,earningsCell(earningsSessions[item.session] || earningsSessions.unknown));
        row.append(earningsCell(item.fiscal_quarter || "--",item.eps_forecast==null ? "EPS 预期未取得" : `EPS 预期 $${researchNumber(item.eps_forecast)}`));
        row.append(earningsCell(earningsMoney(item.dollar_volume),"最新价格 × 最新成交量","earnings-money"),
          earningsCell(earningsMoney(item.average_dollar_volume),"最新价格 × 30日均量","earnings-money"));
        const sources=earningsCell(earningsStatuses[item.date_status] || "预计","","earnings-sources");
        if(item.date_conflict)sources.classList.add("earnings-conflict");
        item.sources.forEach(source=>{
          if(!/^https:\/\/(www\.)?(tradingview\.com|nasdaq\.com)\//.test(source.url || ""))return;
          const link=el("a","",source.name);link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";
          link.title=`${source.name} · ${source.date || "日期未提供"}`;
          link.setAttribute("aria-label",`${item.symbol} ${link.title}`);
          sources.append(link);
          if(item.date_conflict)sources.append(el("small","",`${source.name}: ${source.date || "--"}`));
        });
        row.append(sources);body.append(row);
      });
      container.append(table);
    }
  }
  researchEl("earnings-page-label").textContent=data.total ? `${data.offset+1}–${Math.min(data.offset+data.limit,data.total)} / ${data.total} 家` : "0 家";
  researchEl("earnings-prev").disabled=data.offset===0 || Boolean(earningsState.pending);
  researchEl("earnings-next").disabled=data.offset+data.limit>=data.total || Boolean(earningsState.pending);
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
  const filter=()=>{earningsState.offset=0;refreshResearch();};
  researchEl("earnings-search").addEventListener("input",()=>{window.clearTimeout(timer);timer=window.setTimeout(filter,250);});
  ["earnings-days","earnings-scope","earnings-sort","earnings-unknown"].forEach(id=>researchEl(id).addEventListener("change",filter));
  researchEl("earnings-source-refresh").addEventListener("click",refreshEarningsSource);
  [["earnings-prev",-100],["earnings-next",100]].forEach(([id,step])=>researchEl(id).addEventListener("click",()=>{
    earningsState.offset=Math.max(0,earningsState.offset+step);refreshResearch();
  }));
});
