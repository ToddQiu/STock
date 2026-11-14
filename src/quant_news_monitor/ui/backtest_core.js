(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.STockBacktest=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='stock-daily-v1',DAY=86400000;
  const strategies={buy_hold:'买入持有',sma:'双均线',breakout:'收盘区间突破',frozen:'冻结研究判断'};
  const defaults={target:'US:AAPL',benchmark:'US:SPY',strategy:'buy_hold',capital:100000,allocation:100,fee_bps:5,slippage_bps:5,sell_tax_bps:0,fast:10,slow:30,breakout:20,exit_window:10,stop_loss:0,hold_days:14,price_mode:'adjusted',liquidate:true,start:'',end:''};
  function fail(message){throw new Error(message);}
  function number(value,label,min,max,integer=false){if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))fail(label+'参数无效');return value;}
  function day(value){if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(Date.parse(value))||new Date(value).toISOString().slice(0,10)!==value)fail('日期格式无效');return value;}
  function timestamp(value){if(typeof value!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value)))fail('记录时间须包含有效时区');return Date.parse(value);}
  function target(value){if(typeof value!=='string'||!/^(US|HK|CN|KR):[A-Z0-9^][A-Z0-9.^=-]{0,24}$/.test(value))fail('标的须为市场:代码，例如 US:AAPL');return value;}
  function options(input){
    const o={...defaults,...input};target(o.target);if(o.benchmark)target(o.benchmark);
    if(!Object.prototype.hasOwnProperty.call(strategies,o.strategy))fail('未知策略');
    for(const [key,min,max,integer] of [['capital',1,1e12],['allocation',1,100],['fee_bps',0,1000],['slippage_bps',0,1000],['sell_tax_bps',0,1000],['fast',2,250,true],['slow',3,500,true],['breakout',2,250,true],['exit_window',2,250,true],['stop_loss',0,90],['hold_days',1,365,true]])number(o[key],key,min,max,integer);
    if(o.strategy==='sma'&&o.fast>=o.slow)fail('短均线必须小于长均线');
    if(o.price_mode!=='adjusted'&&o.price_mode!=='raw')fail('价格口径无效');
    if(typeof o.liquidate!=='boolean')fail('期末结算参数无效');
    if(o.start)day(o.start);if(o.end)day(o.end);if(o.start&&o.end&&o.start>o.end)fail('开始日期不能晚于结束日期');return o;
  }
  function series(history,o,cutoff){
    if(!history||!Array.isArray(history.points)||history.points.length<3||history.points.length>2000)fail('需要3至2000条完整日线');
    if(!['USD','CNY','HKD','KRW'].includes(history.currency))fail('历史行情币种未核验');
    if(typeof history.source!=='string'||!history.source.trim())fail('历史行情缺少来源');
    const seen=new Set(),rows=history.points.map(row=>{
      const date=day(String(row.time||row.date||'').slice(0,10));
      if(seen.has(date))fail('重复日线：'+date);seen.add(date);
      if(date>=cutoff)fail('包含未完成或未来日线：'+date);
      const close=number(row.close,'收盘价',1e-12,1e12),price=o.price_mode==='adjusted'?number(row.adjusted_close,'复权收盘价（缺失时请明确选择未复权）',1e-12,1e12):close;
      return {date,time:Date.parse(date),price,close};
    }).sort((a,b)=>a.time-b.time);
    return rows;
  }
  function recordsFor(snapshot,o){
    const records=snapshot.records||[];if(!Array.isArray(records)||records.length>200)fail('研究记录不能超过200条');
    return records.filter(r=>r.target===o.target).map(r=>{
      const created=timestamp(r.created_at);if(![1,0,-1].includes(r.direction))fail('冻结判断缺少有效时间或方向');
      return {created,direction:r.direction,id:String(r.id||''),expires:created+o.hold_days*DAY};
    }).sort((a,b)=>a.created-b.created);
  }
  function simulate(all,o,records){
    const selected=all.filter(r=>(!o.start||r.date>=o.start)&&(!o.end||r.date<=o.end));
    if(selected.length<3)fail('所选区间至少需要3个完整交易日');
    const begin=all.indexOf(selected[0]),last=all.indexOf(selected[selected.length-1]);
    let cash=o.capital,units=0,pending=null,entry=null,peak=o.capital,maxDrawdown=0,fees=0,slippage=0,exposed=0;
    const curve=[],fills=[],closed=[],fee=o.fee_bps/10000,slip=o.slippage_bps/10000,tax=o.sell_tax_bps/10000;
    const mean=(i,n)=>all.slice(i-n+1,i+1).reduce((s,r)=>s+r.price,0)/n;
    const trade=(side,row,signal,reason)=>{
      const price=row.price*(side==='buy'?1+slip:1-slip);
      let amount,charge,slipCost,pnl=null;
      if(side==='buy'){
        const budget=cash*o.allocation/100;units=budget/(price*(1+fee));amount=units;charge=amount*price*fee;slipCost=amount*(price-row.price);
        cash-=amount*price+charge;entry={cost:budget,date:row.date,price};
      }else{
        amount=units;charge=amount*price*(fee+tax);slipCost=amount*(row.price-price);
        const proceeds=amount*price-charge;cash+=proceeds;pnl=proceeds-entry.cost;closed.push(pnl);units=0;entry=null;
      }
      fees+=charge;slippage+=slipCost;
      if(cash < -1e-6||!Number.isFinite(cash)||!Number.isFinite(units))fail('资金记账异常，已停止回测');
      if(Math.abs(cash)<1e-8)cash=0;
      fills.push({date:row.date,signal_date:signal,side,price,units:amount,fee:charge,slippage:slipCost,pnl,reason});
    };
    for(let i=begin;i<=last;i++){
      const row=all[i];if(units>0)exposed++;
      let stopped=false;
      if(pending){
        if(pending.side==='sell'&&units>0){trade('sell',row,pending.date,pending.reason);stopped=pending.reason==='收盘停损';}
        else if(pending.side==='buy'&&!units&&!(o.liquidate&&i===last))trade('buy',row,pending.date,pending.reason);
        pending=null;
      }
      if(i===last&&o.liquidate&&units>0)trade('sell',row,null,'预设期末结算');
      const equity=cash+units*row.price;peak=Math.max(peak,equity);const drawdown=(equity/peak-1)*100;maxDrawdown=Math.min(maxDrawdown,drawdown);
      curve.push({date:row.date,equity,cash,units,drawdown});
      if(i===last)continue;
      let long=false,reason=strategies[o.strategy],ready=true;
      if(o.strategy==='buy_hold')long=true;
      if(o.strategy==='sma'){ready=i+1>=o.slow;if(ready)long=mean(i,o.fast)>mean(i,o.slow);}
      if(o.strategy==='breakout'){
        ready=i>=Math.max(o.breakout,o.exit_window);
        if(ready)long=units>0?row.price>=Math.min(...all.slice(i-o.exit_window,i).map(r=>r.price)):row.price>Math.max(...all.slice(i-o.breakout,i).map(r=>r.price));
      }
      if(o.strategy==='frozen'){
        // A dated daily close is not a publication timestamp: use only records known before that day began.
        const known=records.filter(r=>r.created<row.time).at(-1);
        long=!!known&&known.direction===1&&row.time<known.expires;reason='冻结判断'+(known?' '+known.id:'');
      }
      if(units>0&&o.stop_loss>0&&row.price<=entry.price*(1-o.stop_loss/100)){long=false;ready=true;reason='收盘停损';}
      if(ready&&!stopped&&long&&!units)pending={side:'buy',date:row.date,reason};
      if(ready&&!long&&units)pending={side:'sell',date:row.date,reason};
    }
    const final=curve.at(-1).equity,days=(selected.at(-1).time-selected[0].time)/DAY;
    const returns=curve.map((r,i)=>r.equity/(i?curve[i-1].equity:o.capital)-1);
    return {curve,fills,metrics:{capital:o.capital,final_equity:final,net_return:(final/o.capital-1)*100,max_drawdown:maxDrawdown,annual_return:days>=365?((final/o.capital)**(365.25/days)-1)*100:null,fees,slippage,closed_trades:closed.length,win_rate:closed.length?closed.filter(p=>p>0).length/closed.length*100:null,exposure:exposed/Math.max(1,selected.length-1)*100,open_units:units,cash,benchmark_return:null,excess:null},audit:{from:selected[0].date,to:selected.at(-1).date,samples:selected.length,warmup:begin,days},returns};
  }
  function run(snapshot){
    if(!snapshot||snapshot.engine_version!==VERSION)fail('回测内核版本不匹配，不能重解释旧记录');
    let bytes=0;for(const char of JSON.stringify(snapshot)){const cp=char.codePointAt(0);bytes+=cp<128?1:cp<2048?2:cp<65536?3:4;}if(bytes>500000)fail('快照超过500KB，请缩短区间或减少记录');
    timestamp(snapshot.created_at);
    if(Date.parse(snapshot.created_at)>Date.now()+300000)fail('快照时间来自未来');
    const o=options(snapshot.options),cutoff=new Date(snapshot.created_at).toISOString().slice(0,10),all=series(snapshot.history,o,cutoff);
    const records=o.strategy==='frozen'?recordsFor(snapshot,o):[];
    if(o.strategy==='frozen'&&!records.length)fail('该标的尚无冻结判断，不能用今天的观点补写过去');
    const result=simulate(all,o,records),warnings=[
      '单标的、只做多、无杠杆的日线研究模拟；信号在收盘后形成，下一条实际日线收盘成交，非实盘指令。',
      '采用分数研究单位，不含整手限制、最低佣金、流动性与停牌成交约束；费用和卖出税率是输入假设。',
      '只使用已完成的UTC日期日线；缺失交易日不补价。停损在下一交易日收盘执行，不保证止损价。',
      '当前选择的证券及参数可能存在幸存者偏差和过拟合；样本内收益不能作为未来上涨概率。',
      o.price_mode==='adjusted'?'来源复权序列用于合成研究单位，包含来源调整假设，未另计分红；不是历史实际成交价。':'未复权价格模式不计分红和拆股；跨公司行动的收益可能失真。',
      '历史行情是本次取得的来源版本，不保证是历史当时可见的版本；事件关联不证明因果或已充分定价。',
      '导入文件中的来源和冻结时间属于文件声明；保存快照不等于独立核验。'
    ];
    if(o.strategy==='frozen'&&!snapshot.attention_model)warnings.push('冻结时间早于信号日才可使用；看空与中性均空仓，不模拟卖空；记录到期按持有日数停止。');
    if(snapshot.attention_model)warnings.push('热度算法历史规则重建：借用冻结信号执行内核，但不是当时真实冻结的研究判断。模型 '+snapshot.attention_model.version+'；留出测试不能证明未来有效。');
    if(snapshot.history.retained_from_cache)warnings.push('本次联网未能确认新数据，使用已保存历史行情；结束日期不是当前最新交易日的保证。');
    if(result.metrics.annual_return===null)warnings.push('不足一年，不外推年化收益。');
    if(o.benchmark){
      try{
        const h=o.benchmark===o.target?snapshot.history:snapshot.benchmark_history;
        if(!h||h.currency!==snapshot.history.currency)fail('基准缺少行情或币种不同');
        const rows=series(h,o,cutoff),bo={...o,strategy:'buy_hold',allocation:100,stop_loss:0,start:result.audit.from,end:result.audit.to};
        const b=simulate(rows,bo,[]);
        if(b.curve.length!==result.curve.length||b.curve.some((r,i)=>r.date!==result.curve[i].date))fail('基准交易日未完全对齐');
        result.metrics.benchmark_return=b.metrics.net_return;result.metrics.excess=result.metrics.net_return-b.metrics.net_return;
        result.curve.forEach((r,i)=>r.benchmark=b.curve[i].equity);
      }catch(error){warnings.push('基准比较暂停：'+error.message);}
    }
    if(!result.fills.length)warnings.push('没有可成交信号；检查预热长度、冻结时间或参数，不代表策略已经验证。');
    return {...result,engine_version:VERSION,strategy_label:snapshot.attention_model?'热度规则重建 · '+snapshot.attention_model.version:strategies[o.strategy],options:o,currency:snapshot.history.currency,warnings,audit:{...result.audit,source:snapshot.history.source,observed_at:snapshot.history.observed_at||snapshot.created_at,price_mode:o.price_mode}};
  }
  function report(snapshot){
    const result=run(snapshot),{curve,fills,returns,metrics:m,options:o}=result;
    const daily=returns.slice(1),mean=daily.reduce((sum,v)=>sum+v,0)/daily.length;
    const variance=daily.reduce((sum,v)=>sum+(v-mean)**2,0)/Math.max(1,daily.length-1),sd=Math.sqrt(variance);
    const closed=fills.filter(r=>r.side==='sell'),wins=closed.filter(r=>r.pnl>0),losses=closed.filter(r=>r.pnl<0);
    const profit=wins.reduce((sum,r)=>sum+r.pnl,0),loss=-losses.reduce((sum,r)=>sum+r.pnl,0);
    let underwater=0,longest=0,streak=0,maxStreak=0;
    for(const row of curve){underwater=row.drawdown < -1e-9 ? underwater+1 : 0;longest=Math.max(longest,underwater);}
    for(const row of closed){streak=row.pnl<0?streak+1:0;maxStreak=Math.max(maxStreak,streak);}
    const risk={annual_volatility:daily.length>=60?sd*Math.sqrt(252)*100:null,sharpe:daily.length>=60&&sd>1e-12?mean/sd*Math.sqrt(252):null,
      profit_factor:loss>0?profit/loss:null,average_win:wins.length?profit/wins.length:null,average_loss:losses.length?-loss/losses.length:null,
      max_losing_streak:maxStreak,underwater_bars:longest,exposure:m.exposure,cost_pct:(m.fees+m.slippage)/m.capital*100};
    const months=[];let previous=m.capital,previousBenchmark=m.capital;
    for(const row of curve){
      const month=row.date.slice(0,7);let item=months.at(-1);
      if(!item||item.month!==month){item={month,from:row.date,to:row.date,start_equity:previous,start_benchmark:previousBenchmark,samples:0};months.push(item);}
      item.to=row.date;item.samples++;item.net_return=(row.equity/item.start_equity-1)*100;
      item.benchmark_return=row.benchmark==null?null:(row.benchmark/item.start_benchmark-1)*100;
      previous=row.equity;previousBenchmark=row.benchmark;
    }
    const notes=['波动率与夏普按每年252个交易日、无风险利率0估算；至少60个日收益，零波动不计算夏普。',
      '盈利因子为已平仓盈利总额 / 亏损绝对值；没有亏损时不报无穷大。未平仓收益不计胜率和盈利因子。',
      '月度表现仅覆盖所选区间内实际样本；回撤持续期按观测交易日计，不补缺失日线。'];
    if(curve.length<90)notes.push('不足90条日线，暂不进行前后区间对照。');
    if(m.closed_trades<10)notes.push('已平仓交易不足10笔，胜率和盈亏结构仅供描述，尚不足以验证策略稳定性。');
    if(!fills.length)notes.push('本区间没有成交；零收益不代表策略有效。');
    if(o.benchmark&&m.benchmark_return===null)notes.push('基准数据未通过对齐或币种校验，不能评价相对表现。');
    const stress=[0,5,10].map(extra=>{
      const row={extra_bps:extra,fee_bps:o.fee_bps+extra,slippage_bps:o.slippage_bps+extra};
      if(row.fee_bps>1000||row.slippage_bps>1000)return {...row,error:'超出内核成本上限',net_return:null,max_drawdown:null};
      const value=extra===0?result:run({...snapshot,options:{...o,fee_bps:row.fee_bps,slippage_bps:row.slippage_bps}});
      return {...row,net_return:value.metrics.net_return,max_drawdown:value.metrics.max_drawdown,closed_trades:value.metrics.closed_trades};
    });
    const periods=[];
    if(curve.length>=90){
      const split=Math.floor(curve.length*.7);
      for(const [label,start,end] of [['前70%',0,split-1],['后30%',split,curve.length-1]]){
        // Reuse earlier prices for indicator warmup, but restart cash and pending orders at each boundary.
        const value=run({...snapshot,options:{...o,start:curve[start].date,end:curve[end].date}});
        periods.push({label,from:value.audit.from,to:value.audit.to,samples:value.audit.samples,net_return:value.metrics.net_return,max_drawdown:value.metrics.max_drawdown,
          benchmark_return:value.metrics.benchmark_return,excess:value.metrics.excess,closed_trades:value.metrics.closed_trades});
      }
    }
    return {version:'stock-report-v1',result,risk,months,stress,periods,notes};
  }
  function history(data){
    const rows=data.points||data.candles||[],today=new Date().toISOString().slice(0,10);
    return {currency:data.currency||data.quote?.currency,source:data.source,source_url:data.source_url,observed_at:data.observed_at||new Date().toISOString(),retained_from_cache:!!data.retained_from_cache,points:rows.map(r=>({time:String(r.time||r.date||r.datetime||'').slice(0,10),close:r.close,adjusted_close:r.adjusted_close})).filter(r=>r.time<today)};
  }
  function snapshot(o,h,b,records=[]){return {engine_version:VERSION,created_at:new Date().toISOString(),options:options(o),history:h,benchmark_history:b||null,records:records.filter(r=>r.target===o.target).map(r=>({id:r.id,target:r.target,created_at:r.created_at,direction:r.direction}))};}
  function plot(result,width,height){
    const rows=result.curve,values=rows.flatMap(r=>[r.equity,...(r.benchmark==null?[]:[r.benchmark])]),low=Math.min(result.metrics.capital,...values),high=Math.max(result.metrics.capital,...values),span=high-low||Math.max(1,high*.01);
    return {low,high,points:rows.map((r,i)=>({...r,x:12+i*(width-24)/(rows.length-1),y:12+(high-r.equity)*(height-24)/span,by:r.benchmark==null?null:12+(high-r.benchmark)*(height-24)/span}))};
  }
  function csv(result){return '\uFEFF'+[['成交日期','信号日期','方向','价格','研究单位','手续费与税','滑点成本','已实现盈亏','原因'],...result.fills.map(r=>[r.date,r.signal_date||'',r.side,r.price,r.units,r.fee,r.slippage,r.pnl??'',r.reason])].map(row=>row.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')).join('\r\n');}
  return {VERSION,defaults,strategies,options,run,report,history,snapshot,plot,csv};
});
