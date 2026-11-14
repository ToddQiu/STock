"use strict";

// Reports use their frozen inputs. Dashboard quotes never replace a report's close.
window.Briefings = (() => {
  const names = {CN:'A股',HK:'港股',US:'美股',KR:'韩股',GLOBAL:'综合'};
  const statuses = {queued:'等待生成',running:'生成中',ready:'已生成',partial:'部分数据缺失',failed:'生成失败'};
  const signals = {bullish:'偏多',bearish:'偏空',neutral:'中性',mixed:'多空分歧',unknown:'证据不足'};
  const sourceStatuses = {ready:'可用',partial:'部分缺失',unavailable:'暂不可用',failed:'失败',not_due:'待到期'};
  const pdfStatuses = {not_exported:'PDF未生成',queued:'PDF等待导出',running:'PDF导出中',ready:'PDF已就绪',failed:'PDF导出失败'};
  const checkpoints = [['intraday','首个后续交易日盘中'],['next_day','首个后续交易日收盘'],['week','一周后（第5个交易日）']];
  const checkpointLabels = {pending:'待到期',unavailable:'缺少数据',aligned:'方向一致',opposed:'方向相反',flat:'表现持平',observed:'已观察'};
  let snapshot = null, pending = false, saving = false, dirty = false, kind = 'morning', market = 'GLOBAL', selected = '', signature = '', lastChecked = 0, dialogReportId = '';
  let requestedReport = null, choiceVersion = 0, readingHistory = false;
  const details = new Map(), detailKeys = new Map(), detailRequests = new Map(), detailErrors = new Map(), pdfRequests = new Set(), pdfErrors = new Map();
  const q = id => document.getElementById(id);
  const node = (tag, className = '', text = '') => el(tag, className, text);
  const text = value => value == null || value === '' ? '—' : String(value);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits:4}) : '—';
  const pct = value => typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
  const direction = value => typeof value === 'number' && Number.isFinite(value) ? value > 0 ? 'positive' : value < 0 ? 'negative' : '' : '';
  const stamp = (value, zone = 'Asia/Shanghai') => {
    if (!value || !Number.isFinite(Date.parse(value))) return '待确认';
    return new Intl.DateTimeFormat('zh-CN', {timeZone:zone,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
  };
  function link(label, href) {
    let url; try { url = new URL(href); } catch { return node('span','',text(label)); }
    if (!['http:','https:'].includes(url.protocol)) return node('span','',text(label));
    const a = node('a','',text(label)); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  }
  function reference(id, href) {
    const marker = node('sup','briefing-citation');
    marker.append(href ? link(`[${id}]`,href) : document.createTextNode(`[${id}]`));
    return marker;
  }
  function cited(tag, className, value, refs) {
    const item = node(tag,className);
    for (const part of text(value).split(/(\[[QNE]\d+\])/g)) {
      if (/^\[[QNE]\d+\]$/.test(part)) {
        const id = part.slice(1,-1);
        item.append(reference(id,refs.get(id)?.url));
      } else item.append(document.createTextNode(part));
    }
    return item;
  }
  const evidenceById = value => new Map((value?.evidence || []).map(row => [row.id,row]));
  function annotated(tag, value, refs, ids, href) {
    const item = href ? node(tag) : cited(tag,'',value,refs);
    if (href) item.append(link(value,href));
    for (const id of new Set(ids || [])) {
      if (refs.has(id) && !String(value).includes(`[${id}]`)) item.append(reference(id,refs.get(id).url));
    }
    return item;
  }
  const sourceStamp = (value,zone) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value : value ? stamp(value,zone) : '';
  function renderFrontPage(body, page, zone, refs, {compact = false} = {}) {
    if (!page) return;
    const front = node('div','briefing-front');
    front.append(node('h3','','重大事件'));
    const events = compact ? (page.events || []).slice(0,1) : (page.events || []);
    if (!events.length) front.append(node('p','briefing-front-detail','本期暂无已核验的重大事件。'));
    for (const row of events) {
      const event = node('article','briefing-front-event'), title = node('h4');
      title.append(link(row.title,row.url));
      if (row.ref) title.append(reference(row.ref));
      event.append(title,node('p','briefing-front-detail',[row.source,row.published_at && stamp(row.published_at,zone)].filter(Boolean).join(' · ')));
      if (!compact && row.summary) event.append(cited('p','',row.summary,refs));
      front.append(event);
    }

    const columns = node('div','briefing-front-numbers');
    const groups = [
      ['主要市场收盘涨跌', page.markets || [], row => [!compact && Number.isFinite(row.price) && `收盘 ${number(row.price)}`,row.session_date,row.source].filter(Boolean).join(' · ') || row.reason || '待补收盘数据'],
      ['持仓组合涨跌', page.portfolios || [], row => {
        const amount = typeof row.daily_pnl === 'number' && Number.isFinite(row.daily_pnl)
          ? `${row.daily_pnl > 0 ? '+' : ''}${row.daily_pnl.toLocaleString('zh-CN',{maximumFractionDigits:2})} ${row.currency || ''}`.trim() : '';
        const coverage = Number.isFinite(row.total) ? `覆盖 ${row.covered ?? 0}/${row.total}` : '';
        return [amount,coverage,row.quote_time && stamp(row.quote_time,zone),row.status === 'partial' ? '部分估算' : '',!amount && !row.quote_time ? row.note || '待补行情' : ''].filter(Boolean).join(' · ');
      }],
    ];
    for (const [title,rows,detail] of groups) {
      const group = node('section','briefing-front-group');
      group.append(node('h3','',title));
      if (!rows.length) group.append(node('p','briefing-front-detail','本期暂无可用数据。'));
      const list = node('dl','briefing-front-list');
      for (const row of rows) {
        const item = node('div','briefing-front-number'), label = node('dt','',text(row.name || row.label));
        if (row.ref) label.append(reference(row.ref,refs.get(row.ref)?.url));
        item.append(label,node('dd',`briefing-front-pct ${direction(row.change_pct)}`,pct(row.change_pct)),node('dd','briefing-front-detail',detail(row)));
        list.append(item);
      }
      if (rows.length) group.append(list);
      if (!compact && title === '主要市场收盘涨跌' && page.market_analysis?.length) {
        const analysis = node('div','briefing-front-analysis');
        analysis.append(node('h4','','指数走势解读'));
        for (const row of page.market_analysis) analysis.append(annotated('p',row.text,refs,row.refs));
        group.append(analysis);
      }
      columns.append(group);
    }
    front.append(columns);
    body.append(front);
  }
  function table(headers, rows) {
    const wrap = node('div','briefing-report-table-wrap'), t = node('table','briefing-report-table');
    const head = node('thead'), tr = node('tr');
    headers.forEach(label => { const th = node('th','',label); th.scope = 'col'; tr.append(th); }); head.append(tr); t.append(head);
    const body = node('tbody');
    rows.forEach(values => { const row = node('tr'); values.forEach(value => { const cell = node('td'); cell.append(value && typeof value === 'object' ? value : node('span','',text(value))); row.append(cell); }); body.append(row); });
    t.append(body); wrap.append(t); return wrap;
  }
  function section(body, title, items, render) {
    body.append(node('h3','',title));
    if (!items?.length) { body.append(node('p','quiet-note','本期无符合窗口和来源要求的记录。')); return; }
    render(items);
  }
  function sourceCell(row, zone) {
    const cell = node('div','briefing-report-source');
    cell.append(link(row.source || '来源待核实',row.source_url));
    if (row.flow_source && (row.flow_source !== row.source || row.flow_source_url !== row.source_url)) {
      cell.append(node('br'),link(`资金流 · ${row.flow_source}`,row.flow_source_url));
    }
    for (const note of [sourceStamp(row.as_of,zone),row.basis,row.note,row.flow_note]) {
      if (note) cell.append(node('br'),node('span','',note));
    }
    return cell;
  }
  function amount(value, unit, delta, signed = false) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    const scaled = ['CNY','USD','HKD','KRW','股','手'].includes(unit) && Math.abs(value) >= 100000000;
    const caption = `${signed && value > 0 ? '+' : ''}${number(scaled ? value / 100000000 : value)}${unit ? ` ${scaled ? '亿' : ''}${unit}` : ''}`;
    if (signed) return node('span',direction(value),caption);
    if (typeof delta !== 'number' || !Number.isFinite(delta)) return caption;
    const cell = node('div','',caption);
    cell.append(node('br'),node('small',direction(delta),`较前日 ${pct(delta)}`));
    return cell;
  }
  function renderMarketDetail(body, detail, zone, refs) {
    const marketDate = row => [row.name || names[row.market] || row.market,row.session_date].filter(Boolean).join(' · ');
    section(body,'成交量与主力资金流向',detail.activity,rows => body.append(table(
      ['市场 / 交易日','成交量','成交额','主力净流入','来源与覆盖'],rows.map(row => [marketDate(row),
        amount(row.volume,row.volume_unit,row.volume_change_pct),amount(row.turnover,row.currency,row.turnover_change_pct),
        amount(row.main_net_flow,row.currency,null,true),sourceCell(row,zone)]))));
    for (const row of detail.analysis || []) {
      body.append(annotated('p',row.text,refs,row.refs));
      if (row.source) body.append(node('p','briefing-report-source',row.source));
    }
    section(body,'板块涨跌排行',detail.sectors,rows => body.append(table(
      ['市场 / 交易日','板块','排名','涨跌幅','来源与范围'],rows.map(row => [marketDate({...row,name:null}),row.name,row.rank,
        node('span',direction(row.change_pct),pct(row.change_pct)),sourceCell(row,zone)]))));
    section(body,'今日龙头股',detail.leaders,rows => body.append(table(
      ['市场 / 交易日','标的','板块','涨跌幅','成交量','来源与范围'],rows.map(row => [marketDate({...row,name:null}),
        [row.name,row.symbol].filter(Boolean).join(' · '),row.sector,node('span',direction(row.change_pct),pct(row.change_pct)),
        amount(row.volume,row.volume_unit || '股'),sourceCell(row,zone)]))));
    for (const note of detail.limitations || []) body.append(node('p','briefing-report-source',note));
  }
  function renderNarrativeReview(body, review, zone) {
    body.append(node('h3','','前一日推理验证与一周回看'));
    if (review.note) body.append(node('p','briefing-report-source',review.note));
    for (const item of review.items || []) {
      const article = node('article','briefing-narrative-chain'), title = node('h4');
      const origin = item.origin_kind === 'morning' ? '早报' : item.origin_kind === 'close' ? '晚报' : '报告';
      title.append(link(`${item.origin_date || '日期待核实'} ${origin} · ${item.event || '此前叙事'}`,item.event_url));
      article.append(title);
      const signal = item.signal === 'unrecorded' ? '旧版未标方向' : signals[item.signal];
      const profile = [item.sector,[...new Set([item.name,item.target].filter(Boolean))].join(' '),signal,item.validation && `验证条件 ${item.validation}`].filter(Boolean);
      if (profile.length) article.append(node('p','briefing-report-source',profile.join(' · ')));
      if (item.impact) article.append(node('p','',item.impact));
      if (item.baseline) {
        const baseline = item.baseline;
        const caption = ['冻结基准',baseline.session_date,`收盘 ${number(baseline.price)}`,baseline.source].filter(Boolean).join(' · ');
        const line = node('p','briefing-report-source');line.append(link(caption,baseline.source_url));article.append(line);
      }
      const rows = checkpoints.map(([key,label]) => {
        const row = item[key] || {}, judgement = node('div');
        judgement.append(node('strong','',checkpointLabels[row.status] || '待核实'));
        if (row.reason) judgement.append(node('p','briefing-report-source',row.reason));
        if (row.source || row.source_url || row.as_of) judgement.append(sourceCell(row,zone));
        const reading = node('div');
        reading.append(node('span',direction(row.change_pct),pct(row.change_pct)));
        if (Number.isFinite(row.relative_change_pct)) {
          const relative = row.relative_change_pct;
          reading.append(node('br'),node('small',direction(relative),`相对指数 ${relative >= 0 ? '+' : ''}${relative.toFixed(2)} 个百分点`));
          const comparison = [`${row.benchmark_name || '市场基准'} ${pct(row.benchmark_change_pct)}`,row.benchmark_source].filter(Boolean).join(' · ');
          judgement.append(node('br'),link(comparison,row.benchmark_source_url));
        }
        return [[label,row.session_date].filter(Boolean).join(' · '),reading,judgement];
      });
      article.append(table(['验证阶段 / 交易日','相对冻结基准涨跌','判断与来源'],rows));
      for (const row of item.followup || []) {
        const line = node('p','briefing-report-source');
        const caption = ['后续披露与报道',row.title,row.source,sourceStamp(row.as_of,zone)].filter(Boolean).join(' · ');
        line.append(link(caption,row.url));article.append(line);
      }
      if (item.pricing?.reason) article.append(node('p','briefing-report-source',`定价判断 · ${item.pricing.reason}`));
      body.append(article);
    }
  }
  function renderSections(body, sections, refs, zone) {
    for (const section of sections) {
      body.append(node('h3','',section.title));
      if (section.summary) body.append(cited('p','',section.summary,refs));
      for (const row of section.rows || []) {
        const item = node('article','briefing-news');
        item.append(annotated('h4',row.title,refs,row.refs,row.url));
        if (row.detail) item.append(cited('p','',row.detail,refs));
        const meta = [row.source,sourceStamp(row.as_of,zone)].filter(Boolean).join(' · ');
        if (meta) item.append(node('p','briefing-report-source',meta));
        body.append(item);
      }
    }
  }
  const narrativeMode = value => value.mode === 'evidence_rules' ? '有限证据归纳' : value.status === 'limited' ? '叙事研判 · 证据有限' : '叙事研判';
  function narrativeRefs(value, ids, zone, label = '依据') {
    const refs = evidenceById(value);
    const line = node('p','briefing-report-source briefing-narrative-refs');
    const matched = [...new Set(ids || [])].map(id => refs.get(id)).filter(Boolean);
    if (!matched.length) return line;
    line.append(node('span','',label + '：'));
    for (const row of matched) {
      const source = link(row.label || row.symbol || '来源',row.url);
      source.prepend(reference(row.id),' ');
      if (row.as_of) source.append(` · ${stamp(row.as_of,zone)}`);
      line.append(source);
    }
    return line;
  }
  function renderNarrative(body, value, zone, kind) {
    const refs = evidenceById(value);
    body.append(cited('h3','',value.headline || '当前市场主线',refs));
    body.append(node('p','briefing-report-source',`${narrativeMode(value)}；以下原因与传导为推断，需结合证据和后续表现验证。`));
    body.append(cited('p','briefing-narrative-overview',value.overview,refs));
    if (value.hypotheses?.length) {
      const labels = {bullish:'利好',bearish:'利空',neutral:'中性'};
      body.append(node('h3','','叙事逻辑与验证条件'),table(['事件','涉及板块','信号','影响说明','观察标的 / 验证条件'],value.hypotheses.map(row => [
        annotated('span',row.event,refs,row.refs),row.sector,
        node('span',row.signal === 'bullish' ? 'positive' : row.signal === 'bearish' ? 'negative' : '',labels[row.signal] || '待核实'),
        cited('span','',row.impact,refs),cited('span','',[row.target,row.validation].filter(Boolean).join(' · '),refs),
      ])));
    }
    for (const [index,row] of (value.reasoning || []).entries()) {
      const item = node('article','briefing-narrative-chain');
      item.append(cited('h4','',`${index+1}. ${row.hypothesis || '驱动假说'}`,refs));
      for (const [label,key] of [['观察','phenomenon'],['传导逻辑','mechanism']]) if (row[key]) item.append(cited('p','',`${label}：${row[key]}`,refs));
      item.append(narrativeRefs(value,row.supporting_refs,zone));
      if (row.alternative) item.append(cited('p','',`另一种解释：${row.alternative}`,refs));
      item.append(narrativeRefs(value,row.contrary_refs,zone,'反向证据'));
      if (row.validation) item.append(cited('p','',`如何验证：${row.validation}`,refs));
      body.append(item);
    }
    if(value.market_views?.length){
      body.append(node('h3','','四地市场：各自在交易什么'));
      for(const row of value.market_views){
        const item=node('article','briefing-narrative-chain');
        item.append(node('h4','',names[row.market] || row.market),cited('p','',row.summary,refs),cited('p','',`原因推演：${row.drivers}`,refs),cited('p','',`验证：${row.watch}`,refs));
        item.append(narrativeRefs(value,row.refs,zone));body.append(item);
      }
    }
    if (value.portfolio?.length) {
      body.append(node('h3','','对持仓与白名单的影响'));
      for (const row of value.portfolio) {
        const item=node('article','briefing-narrative-chain');
        item.append(cited('strong','',row.target,refs),cited('p','',row.implication,refs));
        if(row.watch)item.append(cited('p','',`重点观察：${row.watch}`,refs));
        item.append(narrativeRefs(value,row.refs,zone));body.append(item);
      }
    }
    if(value.watchpoints?.length){
      body.append(node('h3','',kind==='morning'?'今天验证什么':'下一交易日观察什么'));
      for(const row of value.watchpoints){
        const item=node('article','briefing-narrative-chain');item.append(cited('strong','',row.condition,refs));
        if(row.confirms)item.append(cited('p','',`支持主线：${row.confirms}`,refs));
        if(row.weakens)item.append(cited('p','',`削弱主线：${row.weakens}`,refs));
        item.append(narrativeRefs(value,row.refs,zone));body.append(item);
      }
    }
    if(value.limitations?.length){const list=node('ul','briefing-report-list briefing-quality');value.limitations.forEach(item=>list.append(cited('li','',item,refs)));body.append(list);}
  }
  const detailKey = record => JSON.stringify([record?.id,record?.status,record?.completed_at,record?.pdf]);
  const chosenMeta = (id = selected) => snapshot?.reports?.find(row=>row.id===id);
  function renderPreview(record, {loading = false, error = ''} = {}) {
    const body=q('briefing-preview'), evidence=q('briefing-evidence-body'), report=record?.report;
    const scroll=body.dataset.reportId===record?.id ? body.scrollTop : 0;
    body.dataset.reportId=record?.id || '';
    const pdfError=pdfErrors.get(record?.id) || record?.pdf?.error;
    body.replaceChildren();evidence.replaceChildren();
    if(error)body.append(node('p','briefing-error',`${error}${report?'；保留上次已加载的本期内容':''}`));
    if(!report){body.append(node('p','quiet-note',loading?'正在读取所选报告…':record?.error || (record?statuses[record.status] || '报告尚未生成':'选择早报或晚报，生成后在这里阅读四地市场重点。')));evidence.append(node('p','quiet-note','本期分析与来源尚未生成。'));if(pdfError)evidence.append(node('p','briefing-error',`PDF导出：${pdfError}`));return;}
    const zone=report.window?.timezone || 'Asia/Shanghai';
    const narrative=report.narrative, refs=evidenceById(narrative);
    renderFrontPage(body,report.front_page,zone,refs,{compact:true});
    body.append(node('p','briefing-preview-meta',`${report.session_date} · ${zone} · 数据窗口 ${stamp(report.window?.start,zone)} — ${stamp(report.window?.end,zone)}`));
    if(report.late)body.append(node('p','briefing-quality briefing-preview-meta',report.editorial_version===2?'更新版 · 从实际发布时间开始跟踪':'补生成：不计入盘前观点验证。'));
    if(narrative){
      body.append(cited('strong','briefing-preview-headline',narrative.headline || '当前市场主线',refs));
      body.append(node('p','briefing-preview-meta',`${narrativeMode(narrative)} · 原因与传导为待验证推断`));
      body.append(cited('p','briefing-preview-summary briefing-preview-narrative',narrative.overview,refs));
      const points=node('ol','briefing-preview-points');
      for(const row of (narrative.reasoning || []).slice(0,2)){
        const item=node('li','briefing-preview-point');item.append(cited('strong','',row.hypothesis,refs));
        if(row.phenomenon)item.append(cited('p','briefing-preview-analysis',`观察：${row.phenomenon}`,refs));
        if(row.mechanism)item.append(cited('p','briefing-preview-analysis',`逻辑：${row.mechanism}`,refs));
        item.append(narrativeRefs(narrative,row.supporting_refs,zone));points.append(item);
      }
      body.append(points);
      if(narrative.market_views?.length){
        const markets=node('div','briefing-preview-points');
        for(const row of narrative.market_views)markets.append(cited('p','briefing-preview-analysis',`${names[row.market]}：${row.summary}`,refs));
        body.append(markets);
      }
      if(narrative.watchpoints?.[0]?.condition)body.append(cited('p','briefing-preview-analysis',`接下来关注：${narrative.watchpoints[0].condition}`,refs));
      if(narrative.limitations?.length)body.append(cited('p','briefing-preview-meta',narrative.limitations[0],refs));
    }else{
    body.append(node('p','briefing-preview-summary',text(report.summary)));
    const list=node('ol','briefing-preview-points');
    const news=[...(report.news || [])].sort((a,b)=>Number(Boolean(b.related))-Number(Boolean(a.related)));
    const focus=(report.focus || []).filter(row=>row.reason);
    const pickedNews=news.slice(0,focus.length?2:3);
    for(const row of pickedNews){
      const item=node('li','briefing-preview-point');
      item.append(link(row.title,row.url),node('p','briefing-preview-analysis',row.analysis || row.analysis_summary || '影响尚待核实'));
      item.append(node('small','briefing-report-source',`${row.source || '来源待确认'} · ${stamp(row.published_at,zone)}${row.related?' · 持仓 / 白名单相关':''}`));list.append(item);
    }
    for(const row of focus.slice(0,3-pickedNews.length)){
      const item=node('li','briefing-preview-point');item.append(node('strong','',`${row.name || row.symbol} · ${signals[row.signal] || '证据不足'}`),node('p','briefing-preview-analysis',row.reason));list.append(item);
    }
    if(list.children.length)body.append(list);else body.append(node('p','quiet-note','窗口内暂无可核验的重点新闻或标的依据。'));
    }
    body.append(node('p','briefing-preview-meta',`生成于 ${stamp(report.generated_at,zone)} · 行情与新闻时点以各项来源为准`));
    if(loading)body.append(node('p','quiet-note','正在更新本期记录…'));
    body.scrollTop=scroll;
    if(report.focus?.length){const list=node('ul','briefing-evidence-list');for(const row of report.focus)list.append(node('li','',`${row.name || row.symbol} · ${signals[row.signal] || '证据不足'}：${row.reason || '依据待补充'}`));evidence.append(list);}
    if(report.review?.note)evidence.append(node('p','',report.review.note));
    if(narrative){
      if(narrative.limitations?.length)narrative.limitations.forEach(value=>evidence.append(node('p','briefing-quality',String(value))));
      evidence.append(narrativeRefs(narrative,(narrative.evidence || []).map(row=>row.id),zone,'叙事引用'));
    }
    for(const row of report.sources || [])evidence.append(node('p','briefing-report-source',`${row.name} · ${sourceStatuses[row.status] || row.status || '待确认'}${row.as_of?` · ${stamp(row.as_of,zone)}`:''}${row.note?` · ${row.note}`:''}`));
    if(report.limitations?.length){const list=node('ul','briefing-evidence-list');report.limitations.forEach(value=>list.append(node('li','',String(value))));evidence.append(list);}
    if(pdfError)evidence.append(node('p','briefing-error',`PDF导出：${pdfError}`));
  }
  function renderPdf(record) {
    const pdf=record?.pdf || {status:'not_exported'}, status=pdfRequests.has(record?.id)?'queued':pdfErrors.has(record?.id)?'failed':pdf.status;
    const busy=['queued','running'].includes(status),ready=status==='ready', completed=['ready','partial'].includes(record?.status);
    const button=q('briefing-pdf-export'),view=q('briefing-pdf-view'),download=q('briefing-pdf-download');
    q('briefing-pdf-status').textContent=record?pdfStatuses[status] || 'PDF状态待确认':'PDF随报告生成';
    q('briefing-pdf-status').title=ready?[pdf.filename,pdf.exported_at?`导出于 ${stamp(pdf.exported_at)}`:''].filter(Boolean).join(' · '):'';
    if(status==='failed'){const why=node('button','text-button','PDF失败 · 查看原因');why.type='button';why.addEventListener('click',()=>{q('briefing-evidence').open=true;});q('briefing-pdf-status').replaceChildren(why);}
    button.disabled=!record||!completed||busy;button.hidden=ready;button.textContent=busy?'导出中…':status==='failed'?'重试PDF':'生成PDF';
    view.hidden=download.hidden=!ready;
    if(ready){const url='/api/briefings/pdf?'+new URLSearchParams({id:record.id});view.href=url;view.target='_blank';view.rel='noopener noreferrer';download.href=url+'&download=1';download.download=pdf.filename || 'STock-report.pdf';}
    else{view.removeAttribute('href');download.removeAttribute('href');download.removeAttribute('download');}
  }
  function renderSelected({loading = false} = {}) {
    const cached=details.get(selected),meta=chosenMeta();
    const record=meta&&detailKeys.get(selected)!==detailKey(meta)?{...cached,...meta,report:cached?.report}:cached || meta;
    renderPreview(record,{loading,error:detailErrors.get(selected) || ''});renderPdf(record);
  }
  async function readReport(id, {force = false} = {}) {
    const key=detailKey(chosenMeta(id));
    if(!force&&details.has(id)&&detailKeys.get(id)===key)return details.get(id);
    if(detailRequests.has(id))return detailRequests.get(id);
    const request=(async()=>{
      try{
        const value=await api('/api/briefings/report?'+new URLSearchParams({id}));
        if(value?.id!==id)throw Error('报告身份不匹配，请重新读取');
        details.set(id,value);detailKeys.set(id,key);detailErrors.delete(id);
        while(details.size>12){const old=[...details.keys()].find(key=>key!==selected&&key!==dialogReportId);if(!old)break;details.delete(old);detailKeys.delete(old);detailErrors.delete(old);}
        if(selected===id)renderSelected();
        if(dialogReportId===id&&q('briefing-dialog')?.open)renderReport(value);
        return value;
      }catch(error){
        detailErrors.set(id,error.message);
        if(selected===id)renderSelected();
        if(dialogReportId===id&&q('briefing-dialog')?.open){const previous=details.get(id);if(previous)renderReport(previous);else q('briefing-report-body').replaceChildren();q('briefing-report-body').append(node('p','briefing-error',`${error.message}${previous?.report?'；保留上次已加载的本期内容':''}`));}
        return null;
      }finally{detailRequests.delete(id);}
    })();
    detailRequests.set(id,request);return request;
  }
  function renderReport(record) {
    const body = q('briefing-report-body'); body.replaceChildren();
    const report = record?.report;
    q('briefing-report-title').textContent = report?.title || `${names[record?.market] || ''} ${record?.kind === 'morning' ? '盘前早报' : '盘后晚报'}`;
    if (!report) { body.append(node('p',record?.status === 'failed' ? 'briefing-error' : 'quiet-note', record?.error || statuses[record?.status] || '报告尚未生成')); return; }
    const zone = report.window?.timezone || 'Asia/Shanghai';
    body.append(node('p','briefing-report-meta', `${report.session_date} · ${zone} · 窗口 ${stamp(report.window?.start,zone)} — ${stamp(report.window?.end,zone)} · 生成 ${stamp(report.generated_at,zone)}`));
    if (report.late) body.append(node('p','briefing-quality',report.editorial_version===2?'更新版按实际发布时间冻结判断，并从之后的交易时段开始验证。':'补生成记录：未在原定时段发布；盘后补写的早报不计入预测验证。'));
    const refs = evidenceById(report.narrative);
    renderFrontPage(body,report.front_page,zone,refs);
    if (report.market_detail) renderMarketDetail(body,report.market_detail,zone,refs);
    if (report.narrative_review) renderNarrativeReview(body,report.narrative_review,zone);
    if (report.narrative) renderNarrative(body,report.narrative,zone,report.kind);
    if (report.sections?.length) renderSections(body,report.sections,refs,zone);
    if (report.narrative || report.market_detail || report.narrative_review || report.sections?.length) body.append(node('h3','','附录：行情、新闻与原始依据'));
    if(report.markets?.length)body.append(table(['市场','当前时段','引用收盘日'],report.markets.map(row=>[row.name,row.phase,row.quote_session_date || row.note])));
    body.append(node('p','',text(report.summary)));
    section(body, report.kind === 'morning' || report.market==='GLOBAL' ? '各市场最近已完成交易日行情' : '本交易日收盘行情', report.quotes, rows => {
      body.append(table(['标的','收盘日期','收盘价','涨跌','来源 / 状态'], rows.map(row => {
        const source = node('div'); source.append(link(row.source || '来源待确认',row.source_url));
        if (row.reason) source.append(node('p','briefing-report-source',row.reason));
        const asOf = row.as_of ? ` · ${stamp(row.as_of,zone)}` : '';
        source.append(node('small','briefing-report-source',`${sourceStatuses[row.status] || row.status || ''}${asOf}`));
        return [`${row.name || row.symbol} · ${row.symbol}${row.role==='overseas'?' · 外盘参考':''}`,text(row.session_date),`${number(row.close)}${row.role==='index'?' 点':row.currency ? ` ${row.currency}` : ''}`,node('span',direction(row.change_pct),pct(row.change_pct)),source];
      })));
    });
    section(body,'窗口内新闻与公告',report.news,rows => rows.forEach(row => {
      const item = node('article','briefing-news');
      item.append(link(row.title,row.url),node('p','briefing-report-source',`${row.source || '来源待确认'} · ${stamp(row.published_at,zone)}${row.market ? ` · ${names[row.market] || row.market}` : ''}${row.background ? ' · 外盘背景' : ''}${row.related ? ' · 持仓 / 白名单相关' : ''}`));
      item.append(node('p', row.signal === 'bullish' ? 'positive' : row.signal === 'bearish' ? 'negative' : '', `${signals[row.signal] || '证据不足'} · ${row.analysis || row.analysis_summary || '暂无有依据的方向分析'}`)); body.append(item);
    }));
    section(body,'关注标的与依据',report.focus,rows => {
      body.append(table(['标的','消息方向','依据'], rows.map(row => [`${row.name || row.symbol} · ${row.symbol}`,node('span',row.signal === 'bullish' ? 'positive' : row.signal === 'bearish' ? 'negative' : '',signals[row.signal] || '证据不足'),row.reason])));
    });
    section(body,'异动与事件',report.events,rows => rows.forEach(row => {
      const item = node('article','briefing-news'); item.append(node('strong','',`${row.name || row.symbol || ''} ${row.title || row.event_type || ''}`));
      item.append(node('p','',row.reason || row.analysis || row.reason_summary || '异动原因待核实'));
      item.append(link(row.source || row.evidence_source || '查看原始来源',row.url || row.evidence_url),node('span','briefing-report-source',` · ${stamp(row.detected_at || row.published_at,zone)}`));body.append(item);
    }));
    section(body,'近期财报日程',report.earnings,rows => {
      body.append(table(['公司','预计日期','来源 / 状态'],rows.map(row => [`${row.name || row.symbol} · ${row.symbol}`,row.earnings_date || row.date,(row.sources || []).map(source=>source.name).filter(Boolean).join(' / ') || row.date_status || '待核实'])));
    });
    body.append(node('h3','',report.kind==='morning'?'上个交易日观点回顾':'当日盘前观点核对'));
    const review = report.review || {}; body.append(node('p','quiet-note',review.note || '暂无可核对的已冻结盘前判断。'));
    if(review.session_date)body.append(node('p','briefing-report-meta',`核对交易日：${review.session_date}`));
    if (review.items?.length) body.append(table(['标的','盘前消息方向','收盘涨跌','核对'],review.items.map(row => [`${row.name || row.symbol} · ${row.symbol}`,signals[row.signal] || '证据不足',node('span',direction(row.change_pct),pct(row.change_pct)),row.matched === true ? '方向一致' : row.matched === false ? '方向不一致' : '未计分'])));
    if(report.narrative?.evidence?.length){body.append(node('h3','','叙事引用索引'));body.append(narrativeRefs(report.narrative,report.narrative.evidence.map(row=>row.id),zone,'冻结来源'));}
    section(body,'来源与数据质量',report.sources,rows => {
      body.append(table(['来源','状态','数据截至 / 说明'],rows.map(row=>[row.name,sourceStatuses[row.status] || row.status,`${row.as_of ? stamp(row.as_of,zone)+' · ' : ''}${row.note || ''}`])));
    });
    if (report.limitations?.length) { const list=node('ul','briefing-report-list briefing-quality');report.limitations.forEach(value=>list.append(node('li','',String(value))));body.append(list); }
  }
  async function openReport(id = selected, {show = true} = {}) {
    if (!id) return;
    dialogReportId=id;
    if(show){const cached=details.get(id);if(cached)renderReport(cached);else{q('briefing-report-title').textContent='读取报告…';q('briefing-report-body').replaceChildren(node('p','quiet-note','正在读取本期全文…'));}if(!q('briefing-dialog').open)q('briefing-dialog').showModal();}
    await readReport(id,{force:!show});
  }
  function render() {
    if (!snapshot) return;
    const markets=[...(snapshot.markets || [])];
    q('briefing-kind').value=kind;
    const rows=(snapshot.reports || []).filter(row=>row.kind===kind).sort((a,b)=>Number(b.market==='GLOBAL')-Number(a.market==='GLOBAL') || String(b.created_at).localeCompare(String(a.created_at))), history=q('briefing-history');
    if(requestedReport?.market===market&&requestedReport.kind===kind&&rows.some(row=>row.id===requestedReport.id)){
      selected=requestedReport.id;requestedReport=null;
    }
    if(!readingHistory&&rows[0]?.market==='GLOBAL'&&['ready','partial'].includes(rows[0].status))selected=rows[0].id;
    if (!rows.some(row=>row.id===selected)) selected=rows[0]?.id || '';
    const key=JSON.stringify(rows.map(row=>[row.id,row.status,row.completed_at,row.pdf]));
    if(history.dataset.signature!==key){history.dataset.signature=key;history.replaceChildren();rows.forEach(row=>{const option=node('option','',`${row.session_date} · ${names[row.market] || row.market}${row.market!=='GLOBAL'?'旧版':''} · 第${row.revision || 1}版 · ${statuses[row.status] || row.status}`);option.value=row.id;history.append(option);});if(!rows.length){const option=node('option','','暂无历史记录');option.value='';history.append(option);}}
    history.value=selected;
    const chosen=rows.find(row=>row.id===selected), schedule=snapshot.schedule, running=rows.some(row=>row.market==='GLOBAL'&&['queued','running'].includes(row.status));
    q('briefing-run').disabled=running||saving;
    q('briefing-run').textContent=running?'生成中…':rows.some(row=>row.market==='GLOBAL')?'更新本期':'生成综合报';q('briefing-open').disabled=!chosen;
    q('briefing-latest-title').textContent=chosen?`${chosen.session_date} ${names[chosen.market]}${kind==='morning'?'早报':'晚报'} · 第${chosen.revision || 1}版 · ${statuses[chosen.status] || chosen.status}${chosen.late?' · 更新版':''}`:'一份报告覆盖A股、美股、港股和韩股';
    const next=kind==='morning'?schedule?.next_morning_at:schedule?.next_close_at;
    q('briefing-status').textContent=chosen?.error || (schedule?.calendar_status==='unavailable'?schedule.calendar_note : `${snapshot.settings?.enabled?'自动已开启':'自动已暂停'}${next?` · 下次 ${stamp(next)} 东八区`:schedule?.calendar_note?` · ${schedule.calendar_note}`:''}${chosen?.news_count!=null?` · ${chosen.news_count} 条新闻`:''}`);
    if(!dirty&&!saving){const form=q('briefing-settings-form');for(const [field,value] of Object.entries(snapshot.settings || {})){if(!form.elements[field])continue;if(['enabled','auto_export_pdf'].includes(field))form.elements[field].checked=Boolean(value);else form.elements[field].value=value;}}
    const schedules=q('briefing-schedule'), scheduleKey=JSON.stringify([snapshot.settings,markets]);
    if(schedules.dataset.signature!==scheduleKey){schedules.dataset.signature=scheduleKey;schedules.replaceChildren();markets.forEach(row=>schedules.append(node('p','',`${row.name || names[row.market]} · ${row.target_count || 0} 个标的 · ${row.timezone || ''} · ${row.calendar_note || (row.calendar_status==='ready'?'交易日历已核验':'交易日历读取中')}`)));}
    const loading=Boolean(selected)&&detailKeys.get(selected)!==detailKey(chosen);
    renderSelected({loading});
    if(selected)readReport(selected);
  }
  async function refresh({force = false} = {}) {
    if(pending || !q('briefing-heading') || (!force&&Date.now()-lastChecked<15000))return;
    pending=true;
    try {
      const data=await api('/api/briefings');lastChecked=Date.now();
      const changed=JSON.stringify(data);snapshot=data;
      if(changed!==signature){signature=changed;render();if(q('briefing-dialog')?.open&&dialogReportId)openReport(dialogReportId,{show:false});}
      else if(selected&&detailErrors.has(selected))readReport(selected,{force:true});
    }catch(error){q('briefing-status').textContent=`报表更新失败：${error.message}；保留已加载记录`;}
    finally{pending=false;}
  }
  async function generate() {
    if(!market||saving)return;const request={market,kind,version:choiceVersion};readingHistory=false;saving=true;render();
    try{const value=await api('/api/briefings/run',{method:'POST',body:{market:request.market,kind:request.kind,refresh:true}});if(choiceVersion===request.version&&value.report_id)requestedReport={market:request.market,kind:request.kind,id:value.report_id};await refresh({force:true});showToast(value.accepted?'正在采集四地市场并推演；新正文与PDF将保存为新版本':'本期正在生成，已选中任务');}
    catch(error){showToast(error.message,'error');}
    finally{saving=false;render();}
  }
  async function exportPdf() {
    const id=selected, record=details.get(id) || chosenMeta(id);
    if(!id||!['ready','partial'].includes(record?.status)||pdfRequests.has(id))return;
    pdfErrors.delete(id);pdfRequests.add(id);renderPdf(record);
    try{
      const value=await api('/api/briefings/export',{method:'POST',body:{id}});
      if(value.report_id && value.report_id!==id)throw Error('导出报告身份不匹配');
      if(!Object.hasOwn(pdfStatuses,value.status))throw Error('导出任务状态无效');
      details.set(id,{...(details.get(id) || record),pdf:{...(record.pdf || {}),status:value.status,error:null}});
      pdfRequests.delete(id);
      await readReport(id,{force:true});await refresh({force:true});
      showToast(value.status==='ready'?'PDF已就绪，可查看或另存':'PDF已开始导出，完成后可查看或另存');
    }catch(error){pdfRequests.delete(id);pdfErrors.set(id,error.message);if(selected===id)renderSelected();showToast(error.message,'error');}
    finally{pdfRequests.delete(id);if(selected===id)renderPdf(details.get(id) || chosenMeta(id));}
  }
  async function saveSettings(event) {
    event.preventDefault();if(saving)return;const form=q('briefing-settings-form');if(!form.reportValidity())return;
    saving=true;const button=form.querySelector('button[type="submit"]');button.disabled=true;
    try{await api('/api/briefings/settings',{method:'POST',body:{enabled:form.elements.enabled.checked,auto_export_pdf:form.elements.auto_export_pdf.checked,morning_time:form.elements.morning_time.value,close_time:form.elements.close_time.value}});dirty=false;await refresh({force:true});showToast('早晚报自动运行设置已保存');}
    catch(error){showToast(error.message,'error');}finally{saving=false;button.disabled=false;render();}
  }
  function init() {
    if(!q('briefing-heading')||q('briefing-heading').dataset.bound)return;q('briefing-heading').dataset.bound='1';
    const dialog=node('dialog','briefing-dialog');dialog.id='briefing-dialog';dialog.setAttribute('aria-labelledby','briefing-report-title');
    const heading=node('div','briefing-dialog-heading'), title=node('h2');title.id='briefing-report-title';
    const closeForm=node('form');closeForm.method='dialog';const close=node('button','secondary-button','关闭');close.type='submit';closeForm.append(close);heading.append(title,closeForm);
    const body=node('div');body.id='briefing-report-body';dialog.append(heading,body);document.body.append(dialog);dialog.addEventListener('close',()=>{dialogReportId='';});
    q('briefing-history').addEventListener('change',event=>{choiceVersion++;requestedReport=null;readingHistory=true;selected=event.target.value;render();});
    q('briefing-kind').addEventListener('change',event=>{if(!['morning','close'].includes(event.target.value))return;choiceVersion++;requestedReport=null;readingHistory=false;kind=event.target.value;selected='';render();});
    q('briefing-run').addEventListener('click',generate);q('briefing-open').addEventListener('click',()=>openReport());
    let openSkillEditor;
    q('briefing-skills').addEventListener('click', event => {
      openSkillEditor ||= window.createAnalysisSkillEditor({
        endpoint:'/api/briefing-skills', title:'早报 / 晚报 Skill',
        description:'已填入默认策略。上传 Markdown 或直接编辑，分别用于早报、晚报的手动与自动生成；留空恢复默认。',
        sections:[['morning','早报 Skill','隔夜市场、盘前叙事与验证条件'],['close','晚报 Skill','收盘复盘、叙事验证与明日展望']]
      });
      return openSkillEditor(event);
    });
    q('briefing-pdf-export').addEventListener('click',exportPdf);
    q('briefing-settings-form').addEventListener('input',()=>{dirty=true;});q('briefing-settings-form').addEventListener('submit',saveSettings);
    refresh({force:true});
    window.setInterval(()=>{if(document.visibilityState==='visible'&&(state.currentView==='overview'||dialog.open))refresh({force:true});},15000);
  }
  return {init,refresh,renderReport};
})();
