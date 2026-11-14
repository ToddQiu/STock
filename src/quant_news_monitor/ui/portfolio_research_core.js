(function(root) {
  'use strict';
  const DAY = 86400000;
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const mean = xs => xs.length ? xs.reduce((a,b)=>a+b,0)/xs.length : null;
  const date = x => typeof x === 'string' && Number.isFinite(Date.parse(x)) ? Date.parse(x) : null;
  const day = x => new Date(x).toISOString().slice(0,10);
  const key = r => r.target || r.market + ':' + r.symbol;
  const isFund = r => ['etf','etn','fund'].includes(String(r.asset_type||'').toLowerCase()) || r.market==='FUND';
  const sectorNames = {'Finance':'金融','Non-Energy Minerals':'非能源矿产','Communications':'通信','Technology Services':'科技服务','Electronic Technology':'电子科技','Health Technology':'医疗科技','Health Services':'医疗服务','Energy Minerals':'能源矿产','Utilities':'公用事业','Consumer Durables':'耐用消费品','Consumer Non-Durables':'非耐用消费品','Consumer Services':'消费服务','Retail Trade':'零售','Industrial Services':'工业服务','Producer Manufacturing':'工业制造','Process Industries':'加工工业','Transportation':'交通运输','Commercial Services':'商业服务','Distribution Services':'分销服务','Miscellaneous':'其他'};
  Object.assign(sectorNames,{'Technology':'科技','Financial Services':'金融服务','Communication Services':'通信服务','Consumer Cyclical':'可选消费','Consumer Defensive':'必需消费','Healthcare':'医疗保健','Health Care':'医疗保健','Basic Materials':'基础材料','Energy':'能源','Industrials':'工业','Real Estate':'房地产','Information Technology':'信息技术','Consumer Discretionary':'可选消费','Consumer Staples':'必需消费','Financials':'金融','Materials':'材料'});
  const industryNames={
    'Other Metals/Minerals':'其他金属与矿产','Investment Banks/Brokers':'投资银行与证券经纪','Investment Managers':'投资与资产管理','Specialty Telecommunications':'专用电信','Semiconductors':'半导体','Packaged Software':'软件产品','Internet Software/Services':'互联网软件与服务','Major Banks':'大型银行','Regional Banks':'区域银行','Electric Utilities':'电力公用事业','Investment Trusts/Mutual Funds':'投资信托／共同基金','Department Stores':'百货零售','Multi-Line Insurance':'综合保险','Pharmaceuticals: Major':'大型制药','Property/Casualty Insurance':'财产与意外保险','Water Utilities':'水务公用事业',
    'Advertising/Marketing Services':'广告与营销服务','Aerospace & Defense':'航空航天与国防','Agricultural Commodities/Milling':'农产品与谷物加工','Air Freight/Couriers':'航空货运与快递','Airlines':'航空运输','Alternative Power Generation':'新能源发电','Aluminum':'铝业','Apparel/Footwear':'服装与鞋类','Apparel/Footwear Retail':'服装与鞋类零售','Auto Parts: OEM':'汽车原厂零部件',
    'Beverages: Alcoholic':'酒类饮品','Beverages: Non-Alcoholic':'非酒精饮料','Biotechnology':'生物科技','Broadcasting':'广播电视','Building Products':'建筑产品','Cable/Satellite TV':'有线与卫星电视','Casinos/Gaming':'博彩娱乐','Chemicals: Agricultural':'农用化工','Chemicals: Major Diversified':'综合化工','Chemicals: Specialty':'特种化学品',
    'Computer Peripherals':'计算机外设','Computer Processing Hardware':'计算机硬件','Construction Materials':'建筑材料','Containers/Packaging':'容器与包装','Contract Drilling':'钻探承包服务','Data Processing Services':'数据处理服务','Discount Stores':'折扣零售','Drugstore Chains':'连锁药店','Electrical Products':'电气设备','Electronic Components':'电子元器件',
    'Electronic Equipment/Instruments':'电子设备与仪器','Electronic Production Equipment':'电子制造设备','Electronics Distributors':'电子产品分销','Electronics/Appliance Stores':'电子产品与家电零售','Electronics/Appliances':'电子产品与家用电器','Engineering & Construction':'工程与建筑','Environmental Services':'环境服务','Finance/Rental/Leasing':'金融与租赁','Financial Conglomerates':'综合金融','Financial Publishing/Services':'财经出版与信息服务',
    'Food Distributors':'食品分销','Food Retail':'食品零售','Food: Major Diversified':'综合食品','Food: Meat/Fish/Dairy':'肉类、水产与乳制品','Food: Specialty/Candy':'特色食品与糖果','Gas Distributors':'燃气供应','Home Furnishings':'家居用品','Home Improvement Chains':'家装连锁零售','Homebuilding':'住宅建设','Hospital/Nursing Management':'医院与护理机构管理',
    'Hotels/Resorts/Cruise lines':'酒店、度假村与邮轮','Household/Personal Care':'家居与个人护理用品','Industrial Conglomerates':'综合工业','Industrial Machinery':'工业机械','Industrial Specialties':'工业专用品','Information Technology Services':'信息技术服务','Insurance Brokers/Services':'保险经纪与服务','Integrated Oil':'综合石油','Internet Retail':'互联网零售','Life/Health Insurance':'人寿与健康保险',
    'Major Telecommunications':'综合电信','Managed Health Care':'管理式医疗','Marine Shipping':'海运','Media Conglomerates':'综合传媒','Medical Distributors':'医疗用品分销','Medical Specialties':'专用医疗器械','Medical/Nursing Services':'医疗与护理服务','Metal Fabrication':'金属加工','Miscellaneous Commercial Services':'其他商业服务','Motor Vehicles':'汽车制造',
    'Movies/Entertainment':'影视与娱乐','Oil & Gas Pipelines':'油气管道','Oil & Gas Production':'油气开采','Oil Refining/Marketing':'炼油与销售','Oilfield Services/Equipment':'油田服务与设备','Other Consumer Services':'其他消费服务','Other Transportation':'其他交通运输','Personnel Services':'人力资源服务','Pharmaceuticals: Generic':'仿制药','Pharmaceuticals: Other':'其他制药',
    'Precious Metals':'贵金属','Publishing: Newspapers':'报纸出版','Pulp & Paper':'纸浆与造纸','Railroads':'铁路运输','Real Estate Development':'房地产开发','Real Estate Investment Trusts':'房地产投资信托','Recreational Products':'休闲用品','Restaurants':'餐饮','Services to the Health Industry':'医疗行业服务','Specialty Insurance':'专业保险',
    'Specialty Stores':'专业零售','Steel':'钢铁','Telecommunications Equipment':'通信设备','Tobacco':'烟草','Tools & Hardware':'工具与五金','Trucking':'公路货运','Trucks/Construction/Farm Machinery':'卡车、工程与农业机械','Wholesale Distributors':'批发分销','Wireless Telecommunications':'无线通信',
    'Banks - Regional':'区域银行','Banks - Diversified':'综合银行','Utilities - Regulated Electric':'受监管电力','Utilities - Regulated Water':'受监管水务','Utilities - Regulated Gas':'受监管燃气','Utilities - Renewable':'可再生能源公用事业','Software - Application':'应用软件','Software - Infrastructure':'基础软件','Internet Content & Information':'互联网内容与信息','Semiconductor Equipment & Materials':'半导体设备与材料','Consumer Electronics':'消费电子','Auto Manufacturers':'汽车制造','Auto Parts':'汽车零部件','Asset Management':'资产管理','Capital Markets':'资本市场','Insurance - Diversified':'综合保险','Insurance - Life':'人寿保险','Insurance - Property & Casualty':'财产与意外保险','Insurance - Specialty':'专业保险','Insurance Brokers':'保险经纪','Credit Services':'信贷服务','Gold':'黄金','Copper':'铜业','Industrial Metals & Minerals':'工业金属与矿产','Specialty Chemicals':'特种化学品','Oil & Gas Integrated':'综合油气','Oil & Gas E&P':'油气勘探与开采','Oil & Gas Refining & Marketing':'油气炼化与销售','Oil & Gas Midstream':'油气储运','Drug Manufacturers - General':'综合制药','Drug Manufacturers - Specialty & Generic':'专科药与仿制药','Medical Devices':'医疗器械','Medical Instruments & Supplies':'医疗仪器与用品','Medical Care Facilities':'医疗机构','Diagnostics & Research':'诊断与研究','Telecom Services':'电信服务','Entertainment':'娱乐','Specialty Retail':'专业零售','Packaging & Containers':'包装与容器','Real Estate - Development':'房地产开发','Real Estate Services':'房地产服务'
  };
  function classificationName(value) { return industryNames[value] || sectorNames[value] || value; }
  function classificationRows(positions) {
    return positions.map(r=>{const fund=isFund(r),sector=classificationName(r.sector);
      return {target:key(r),name:r.name||r.symbol,type:({etf:'ETF',etn:'ETN',fund:'基金',stock:'股票'})[r.asset_type]||(fund?'基金':'股票'),
        description:fund?(r.investment_focus||'投资方向待核验'):[sector,classificationName(r.industry)].filter(Boolean).join(' / ')||'行业资料暂未取得',
        source:fund&&r.product_source||r.classification_source||'',source_url:fund&&r.product_source_url||r.classification_source_url||'',
        as_of:fund&&r.product_as_of||r.classification_as_of||'',note:r.classification_error|| (fund?'投资方向不等于底层行业权重':'')};});
  }
  function pearson(a,b,minimum=20) {
    if(a.length!==b.length||a.length<minimum||![...a,...b].every(finite))return null;
    const ma=mean(a),mb=mean(b);let ab=0,aa=0,bb=0;
    a.forEach((x,i)=>{ab+=(x-ma)*(b[i]-mb);aa+=(x-ma)**2;bb+=(b[i]-mb)**2;});
    return aa>0&&bb>0?Math.max(-1,Math.min(1,ab/Math.sqrt(aa*bb))):null;
  }
  function bars(rows,asOf=Date.now()) {
    const unique=new Map();
    (rows||[]).forEach(r=>{const at=date(r.time||r.datetime||r.date);
      if(at!==null&&at<=asOf&&finite(r.close)&&r.close>0)unique.set(day(at),{...r,time:new Date(at).toISOString(),at});});
    return [...unique.values()].sort((a,b)=>a.at-b.at);
  }
  function returns(rows,asOf) {
    const data=bars(rows,asOf),result=new Map();
    for(let i=1;i<data.length;i++) {
      const previous=data[i-1],current=data[i],change=current.close/previous.close-1;
      // Match both dates: a holiday gap must not be compared with a one-day return.
      if(current.at-previous.at<=7*DAY&&Math.abs(change)<0.5)
        result.set(day(previous.at)+'/'+day(current.at),change);
    }
    return result;
  }
  function correlations(histories,asOf=Date.now(),options={}) {
    const targets=Object.keys(histories),pairs=[];
    const series=new Map(targets.map(target=>{const history=researchHistory(histories[target],asOf);return [target,{adjusted:history.adjusted,returns:returns(history.points,asOf)}];}));
    for(let i=0;i<targets.length;i++)for(let j=i+1;j<targets.length;j++) {
      const window=options.window===120?120:60,minimum=window===120?40:20;
      const prepared=[series.get(targets[i]),series.get(targets[j])];
      const verified=prepared.every(h=>h.adjusted);
      const first=prepared[0].returns,second=prepared[1].returns;
      const dates=[...first.keys()].filter(d=>second.has(d)).sort().slice(-window);
      pairs.push({a:targets[i],b:targets[j],samples:dates.length,from:dates[0]||null,to:dates[dates.length-1]||null,
        correlation:options.strictAdjusted&&!verified?null:pearson(dates.map(d=>first.get(d)),dates.map(d=>second.get(d)),minimum),
        verified,window,reason:options.strictAdjusted&&!verified?'缺少来源复权序列':dates.length<minimum?'对齐样本不足':'',
        basis:(verified?'来源提供的复权收盘':'未核验复权的收盘')+'收益率；按交易日期对齐，跨市场并非同时刻价格；异常大幅变动区间不参与计算'});
    }
    return pairs;
  }
  function portfolio(positions,account={},histories={},identities=[],options={}) {
    identities=peerRows(identities);
    const rows=positions.map(row=>{const identity=identities.find(r=>r.target===key(row));return {...row,sector:isFund(row)?null:row.sector||identity?.sector,industry:isFund(row)?null:row.industry||identity?.industry,value:finite(row.value)?row.value:finite(row.market_value_base)?row.market_value_base:null};});
    const cash=finite(account.cash_base)?account.cash_base:0;
    const receivable=finite(account.receivable_base)?account.receivable_base:0;
    const payable=finite(account.payable_base)?account.payable_base:0;
    const known=rows.reduce((sum,row)=>sum+(row.value||0),0)+cash+receivable-payable;
    const complete=rows.every(r=>r.value!==null)&&account.cash_base!==null&&account.receivable_base!==null&&account.payable_base!==null;
    const holdings=new Map();
    for(const row of rows){const id=key(row),prior=holdings.get(id);if(prior)prior.value+=(row.value||0);else holdings.set(id,{...row,value:row.value||0});}
    const sorted=[...holdings.values()].sort((a,b)=>b.value-a.value);
    const denominator=options.basis==='invested'?known-cash-receivable+payable:known;
    const weight=value=>complete&&denominator>0?value/denominator*100:null;
    const allocation=field=>{
      const groups=new Map();rows.forEach(r=>{const label=field==='sector'&&isFund(r)?'基金/ETN（待穿透）':r[field]||'未分类';const current=groups.get(label)||{name:label,value:0,missing:0};
        if(r.value===null)current.missing++;else current.value+=r.value;groups.set(label,current);});
      return [...groups.values()].map(g=>({...g,weight:weight(g.value)})).sort((a,b)=>b.value-a.value);
    };
    const sectors=allocation('sector'),limit=finite(account.position_limit)?account.position_limit:35,sectorLimit=finite(account.sector_limit)?account.sector_limit:50;
    const alerts=[];
    sorted.forEach(r=>{if(weight(r.value)>limit)alerts.push((r.name||key(r))+' 占比 '+weight(r.value).toFixed(1)+'%，超过单仓上限 '+limit+'%');});
    sectors.forEach(r=>{if(!['未分类','基金/ETN（待穿透）'].includes(r.name)&&r.weight>sectorLimit)alerts.push(classificationName(r.name)+' 占比 '+r.weight.toFixed(1)+'%，超过行业上限 '+sectorLimit+'%');});
    const top5=weight(sorted.slice(0,5).reduce((s,r)=>s+r.value,0));
    const top5Limit=finite(account.top5_limit)?account.top5_limit:80;
    if(top5>top5Limit)alerts.push('前五大持仓占比 '+top5.toFixed(1)+'%，超过上限 '+top5Limit+'%');
    const valued=rows.filter(r=>r.value!==null),classified=valued.filter(r=>r.sector).reduce((s,r)=>s+Math.abs(r.value),0),gross=valued.reduce((s,r)=>s+Math.abs(r.value),0);
    return {complete,known,total:complete?known:null,missing:rows.filter(r=>r.value===null).length,
      max:weight(sorted[0]?.value||0),top5,cash:complete&&known>0?cash/known*100:null,largest:sorted.slice(0,5).map(r=>({...r,weight:weight(r.value)})),
      coverage:{valued:valued.length,total:rows.length,sector: gross>0?classified/gross*100:null,unknownValue:gross-classified},
      sector:sectors,market:allocation('market'),currency:allocation('currency'),alerts,correlations:correlations(histories,Date.now(),options),
      note:complete?(options.basis==='invested'?'仓位以证券市值为分母，现金仍按总资产展示。':'仓位以含现金的总资产为分母。')+'计价币种不等于公司收入敞口；缺少行业资料不代表低行业风险。':'估值或汇率不全：仅显示已知金额，暂停完整组合占比和超限判断。'};
  }
  function researchHistory(history={},asOf=Date.now()) {
    const points=bars(history?.points,asOf),adjusted=points.length>0&&points.every(r=>finite(r.adjusted_close)&&r.adjusted_close>0);
    return {...history,points:adjusted?points.map(r=>({...r,close:r.adjusted_close})):points,adjusted};
  }
  function portfolioPerformance(positions,account={},histories={},options={}) {
    const asOf=finite(options.asOf)?options.asOf:Date.now(),year=Number(options.year),currentYear=new Date(asOf).getUTCFullYear();
    const result={year,annualReturn:null,maxDrawdown:null,volatility:null,averageCorrelation:null,medianCorrelation:null,
      pairs:[],highPairs:[],validPairs:0,totalPairs:0,matrix:[],targets:[],path:[],weights:[],issues:[],samples:0,from:null,to:null,
      peakDate:null,troughDate:null,recoveryDate:null,cashWeight:null,status:'unavailable',annualization:252,
      method:'以当前证券市值与现金占比在所选年开始前一次配置，年内买入持有、不再平衡；现金和当前汇率固定，不含交易费用。采用来源复权收盘；这是持仓结构的历史模拟，不是实际账户收益。'};
    if(!Number.isInteger(year)||year<2000||year>currentYear){result.issues.push('年份须在2000年至当前年之间');return result;}
    const start=Date.UTC(year,0,1),end=Math.min(asOf,Date.UTC(year+1,0,1)-1),endDay=day(end),groups=new Map();
    for(const position of positions||[]) {
      const id=key(position),value=finite(position.market_value_base)?position.market_value_base:finite(position.value)?position.value:null;
      const existing=groups.get(id);
      if(existing){existing.value=value===null||existing.value===null?null:existing.value+value;if(existing.currency!==position.currency)existing.invalidCurrency=true;}
      else groups.set(id,{target:id,name:position.name||position.symbol||id,currency:position.currency,value});
    }
    result.targets=[...groups.values()];result.totalPairs=result.targets.length*(result.targets.length-1)/2;
    if(!result.targets.length){result.issues.push('当前没有证券持仓');return result;}
    const prepared=new Map();
    for(const row of result.targets) {
      const history=histories[row.target],source=history?.source;
      const points=bars(history?.points,end).filter(p=>p.at>=start-17*DAY&&p.at<=end);
      const adjusted=!!source&&points.length>0&&points.every(p=>finite(p.adjusted_close)&&p.adjusted_close>0);
      const currencyValid=!!row.currency&&row.currency===history?.currency&&!row.invalidCurrency;
      const valid=adjusted&&currencyValid,prices=new Map(points.map(p=>[day(p.at),p.adjusted_close]));
      const moves=new Map();
      if(valid)for(let i=1;i<points.length;i++){
        const previous=points[i-1],current=points[i];
        if(current.at>=start&&current.at-previous.at<=14*DAY)moves.set(day(previous.at)+'/'+day(current.at),current.adjusted_close/previous.adjusted_close-1);
      }
      const reason=!history?'尚未取得年度行情':!currencyValid?'报价币种与持仓不一致或缺失':!adjusted?'未取得完整来源复权序列':'';
      prepared.set(row.target,{points,prices,moves,valid,reason,source,source_url:history?.source_url});
      row.source=source||'';row.source_url=history?.source_url||'';row.reason=reason;
    }
    for(let i=0;i<result.targets.length;i++)for(let j=i+1;j<result.targets.length;j++) {
      const a=result.targets[i].target,b=result.targets[j].target,first=prepared.get(a),second=prepared.get(b);
      const dates=[...first.moves.keys()].filter(d=>second.moves.has(d)).sort();
      const coefficient=pearson(dates.map(d=>first.moves.get(d)),dates.map(d=>second.moves.get(d)),20);
      result.pairs.push({a,b,correlation:coefficient,samples:dates.length,from:dates[0]?.split('/')[0]||null,to:dates.at(-1)?.split('/')[1]||null,
        reason:first.reason||second.reason||(dates.length<20?'少于20个同起止日期收益区间':coefficient===null?'收益序列无波动，相关性未定义':'')});
    }
    const coefficients=result.pairs.map(p=>p.correlation).filter(finite).sort((a,b)=>a-b);
    result.validPairs=coefficients.length;result.averageCorrelation=mean(coefficients);
    result.medianCorrelation=coefficients.length?(coefficients[Math.floor((coefficients.length-1)/2)]+coefficients[Math.ceil((coefficients.length-1)/2)])/2:null;
    result.highPairs=result.pairs.filter(p=>finite(p.correlation)&&p.correlation>0.7).sort((a,b)=>b.correlation-a.correlation);
    result.matrix=result.targets.map(a=>result.targets.map(b=>a.target===b.target?pearson([...prepared.get(a.target).moves.values()],[...prepared.get(a.target).moves.values()],20):result.pairs.find(p=>p.a===a.target&&p.b===b.target||p.a===b.target&&p.b===a.target)?.correlation??null));
    if(!finite(account.cash_base)||account.cash_base<0)result.issues.push('现金或其换算汇率缺失，不能确定完整配置权重');
    if(account.payable_base===null)result.issues.push('应付账款换算汇率缺失');
    if(account.receivable_base===null)result.issues.push('应收账款换算汇率缺失');
    for(const row of result.targets){if(!finite(row.value)||row.value<=0)result.issues.push(row.name+'：当前市值缺失或非正');if(row.reason)result.issues.push(row.name+'：'+row.reason);}
    const total=result.targets.reduce((sum,row)=>sum+(row.value||0),0)+(account.cash_base||0)+(account.receivable_base||0)-(account.payable_base||0);
    if(result.issues.length||!finite(total)||total<=0)return result;
    result.cashWeight=account.cash_base/total;
    result.receivableWeight=(account.receivable_base||0)/total;
    result.payableWeight=(account.payable_base||0)/total;
    result.weights=result.targets.map(row=>({...row,weight:row.value/total}));
    let common=[...prepared.get(result.targets[0].target).prices.keys()];
    for(const row of result.targets.slice(1))common=common.filter(d=>prepared.get(row.target).prices.has(d));
    common.sort();
    const anchor=common.filter(d=>Date.parse(d)<start).at(-1);
    const partial=!anchor||start-Date.parse(anchor)>14*DAY;
    if(partial&&!options.allowPartial){result.issues.push('缺少选年前共同有效收盘，不能把年内首日或部分历史冒充全年收益');return result;}
    const inYear=common.filter(d=>Date.parse(d)>=start);
    const dates=partial?inYear:[anchor,...inYear],baseline=dates[0];
    if(dates.length<2){result.issues.push('所选年尚无共同有效收盘');return result;}
    result.from=baseline;result.to=dates.at(-1);
    if(Date.parse(endDay)-Date.parse(result.to)>14*DAY){result.issues.push('期末共同行情落后超过14天，请更新；暂停年度统计');return result;}
    if(dates.some((d,i)=>i>0&&Date.parse(d)-Date.parse(dates[i-1])>14*DAY)){result.issues.push('共同行情存在超过14天缺口，暂停年度统计');return result;}
    let peak=1,peakDate=baseline,worst=0,trough=baseline,worstPeak=baseline,recovery=null,drawdownPeak=1;
    result.path=dates.map(d=>{
      const value=result.cashWeight+result.receivableWeight-result.payableWeight+result.weights.reduce((sum,row)=>sum+row.weight*prepared.get(row.target).prices.get(d)/prepared.get(row.target).prices.get(baseline),0);
      if(value>=peak){peak=value;peakDate=d;}
      const drawdown=value/peak-1;
      if(drawdown<worst){worst=drawdown;trough=d;worstPeak=peakDate;drawdownPeak=peak;recovery=null;}
      else if(worst<0&&d>trough&&recovery===null&&value>=drawdownPeak)recovery=d;
      return {date:d,value,change:(value-1)*100,drawdown:drawdown*100};
    });
    if(result.path.some(p=>!finite(p.value)||p.value<=0)){result.path=[];result.issues.push('历史净值无效，暂停统计');return result;}
    const changes=result.path.slice(1).map((p,i)=>p.value/result.path[i].value-1),average=mean(changes);
    result.annualReturn=(result.path.at(-1).value-1)*100;result.maxDrawdown=worst*100;result.samples=changes.length;
    result.volatility=changes.length>=20?Math.sqrt(changes.reduce((sum,r)=>sum+(r-average)**2,0)/(changes.length-1)*252)*100:null;
    result.peakDate=worst<0?worstPeak:null;result.troughDate=worst<0?trough:null;result.recoveryDate=recovery;
    result.status=partial?'partial':'available';
    if(partial){
      result.issues.push('缺少选年前共同有效收盘，仅计算全部当前持仓都有复权收盘的年内共同区间。');
      result.method='在年内第一个共同有效收盘按当前证券市值与现金占比一次配置，买入持有、不再平衡。现金和当前汇率固定，不含交易费用。';
    }
    if(changes.length<20)result.issues.push('共同收益区间少于20个，暂不显示年化波动');
    if(account.payable_base)result.method+=' 应付账款按当前金额固定扣除，不作为现金支出。';
    if(account.receivable_base)result.method+=' 应收账款按当前金额固定，不计收益、不作为现金。';
    result.method+=' 共同收盘日期对齐，不补造缺价；波动率为共同区间收益样本标准差×√252，跨市场休市差异会影响该近似。相关性为同起止日期收益的Pearson系数，有效持仓对等权平均。';
    return result;
  }
  function retrospective(history,asOf=Date.now()) {
    const prepared=researchHistory(history,asOf),points=prepared.points,last=points[points.length-1],first=points[points.length-8];
    const volumes=points.slice(-7).map(r=>r.volume),previous=points.slice(-27,-7).map(r=>r.volume);
    const valid=xs=>xs.length&&xs.every(x=>finite(x)&&x>=0);
    const changes=[...returns(points,asOf).values()];const avg=mean(changes);
    return {return7:first&&last?(last.close/first.close-1)*100:null,from:first?.time,to:last?.time,
      volumeRatio:valid(volumes)&&previous.length>=10&&valid(previous)&&mean(previous)>0?mean(volumes)/mean(previous):null,
      volatility:changes.length>=20?Math.sqrt(changes.reduce((s,x)=>s+(x-avg)**2,0)/(changes.length-1)*252)*100:null,
      samples:points.length,source:history?.source||'',adjusted:prepared.adjusted,basis:'7 个已完成的交易日收益区间；波动率按252日年化；'+(prepared.adjusted?'使用来源提供的复权收盘':'未取得复权序列，仅为价格变化')};
  }
  function lookthrough(positions,funds={},account={}) {
    const exposures=new Map(),unknown=[],dates=[];
    let total=0,complete=true;
    function add(id,name,value,origin,type='stock'){
      const row=exposures.get(id)||{target:id,name,value:0,origins:[],type};row.value+=value;row.origins.push(origin);exposures.set(id,row);
    }
    for(const row of positions) {
      const value=finite(row.market_value_base)?row.market_value_base:row.value;
      if(!finite(value)){complete=false;unknown.push(key(row)+' 缺少市值');continue;}total+=value;
      const fund=funds[key(row)]||funds[row.symbol];
      if(!fund){add(key(row),row.name||key(row),value,key(row));if(isFund(row)||/etf|fund/i.test(row.type||''))unknown.push(key(row)+' 底层持仓未覆盖');continue;}
      dates.push({target:key(row),as_of:fund.as_of||fund.holdings_date||null,source:fund.source,source_url:fund.source_url});
      if(fund.partial)unknown.push(key(row)+' 仅取得公开重仓，底仓范围不完整');
      let sum=0;
      for(const holding of fund.holdings||[]) {
        if(!finite(holding.weight)){unknown.push(key(row)+' 持仓权重无效');continue;}sum+=holding.weight;
        const id=holding.key||holding.target||'UNRESOLVED:'+key(row)+':'+(holding.symbol||holding.name);
        add(id,holding.name||holding.symbol,value*holding.weight/100,key(row),holding.type||'stock');
      }
      if(Math.abs(100-sum)>0.01)unknown.push(key(row)+' 披露权重合计 '+sum.toFixed(2)+'%，未补齐或归一化');
    }
    const denominator=account.basis==='total'?finite(account.cash_base)&&account.receivable_base!==null&&account.payable_base!==null?total+account.cash_base+(account.receivable_base||0)-(account.payable_base||0):null:total;
    const rows=[...exposures.values()].map(r=>({...r,weight:complete&&denominator>0?r.value/denominator*100:null,overlap:new Set(r.origins).size>1})).sort((a,b)=>b.value-a.value);
    const names=[...new Set(positions.map(p=>funds[key(p)]?key(p):funds[p.symbol]?p.symbol:null).filter(Boolean))],pairs=[];
    for(let i=0;i<names.length;i++)for(let j=i+1;j<names.length;j++){
      const maps=[names[i],names[j]].map(id=>{const map=new Map();for(const h of funds[id].holdings||[]){const k=h.key||h.target;if(k&&finite(h.weight)&&h.weight>0&&(h.type||'stock')==='stock')map.set(k,(map.get(k)||0)+h.weight);}return map;});
      const shared=[...maps[0]].filter(([id])=>maps[1].has(id));
      const valid=[names[i],names[j]].every(id=>{const hs=funds[id].holdings||[];return !funds[id].partial&&hs.length&&hs.every(h=>finite(h.weight)&&h.weight>=0&&['stock','cash'].includes(h.type||'stock')&&((h.type||'stock')==='cash'||h.key||h.target))&&Math.abs(hs.reduce((s,h)=>s+h.weight,0)-100)<=0.05;});
      pairs.push({a:names[i],b:names[j],count:shared.length,overlap:valid?shared.reduce((s,[id,w])=>s+Math.min(w,maps[1].get(id)),0):null});
    }
    return {rows,overlaps:rows.filter(r=>r.overlap),pairs,unknown,dates,complete:complete&&!unknown.length,note:'按披露权重穿透，不与ETF外层重复相加；缺失权重不补齐。重叠率是共同股票较小权重之和，负权重/衍生品不作普通股票重叠率。'};
  }
  const eventKey=e=>JSON.stringify([e.target,e.event_id||'',e.event_id?'':e.title,e.event_id?'':e.source_url||'']);
  function latestEvents(events,asOf=Date.now()) {
    const latest=new Map();
    for(const e of events||[])if(date(e.observed_at)!==null&&date(e.observed_at)<=asOf){
      const id=eventKey(e),prior=latest.get(id);if(!prior||date(prior.observed_at)<=date(e.observed_at))latest.set(id,e);
    }
    return [...latest.values()];
  }
  function upcoming(events,target,asOf=Date.now(),days=14) {
    const start=Date.parse(day(asOf));
    return latestEvents(events,asOf).filter(e=>e.target===target&&date(e.date)!==null&&date(e.date)>=start&&date(e.date)<start+(days+1)*DAY)
      .sort((a,b)=>date(a.date)-date(b.date));
  }
  function rememberEvents(events,previous) {
    const old=new Map((previous||[]).map(e=>[eventKey(e),e]));
    return events.map(e=>{const p=old.get(eventKey(e));return {...e,first_observed_at:p&&p.date===e.date&&p.status===e.status?p.first_observed_at||p.observed_at:e.observed_at};});
  }
  function eventKind(e) {
    if(e.type==='news_lead')return 'lead';
    if(e.kind)return e.kind;
    return /财报|业绩|earnings/i.test(e.title||'')?'earnings':'event';
  }
  function eventConflict(event,events) {
    return !!event.date_conflict||eventKind(event)==='earnings'&&events.some(e=>e.target===event.target&&eventKind(e)==='earnings'&&Math.abs(date(e.date)-date(event.date))<=14*DAY&&(e.date_conflict||e.status==='cancelled'||e.date!==event.date));
  }
  function peerRows(rows) {
    const result=new Map();
    for(const row of [...rows].sort((a,b)=>(date(a.observed_at)||0)-(date(b.observed_at)||0)))result.set(row.target,{...result.get(row.target),...row});
    return [...result.values()];
  }
  function peers(target,rows) {
    rows=peerRows(rows);
    const own=rows.find(r=>r.target===target);
    if(!own||isFund(own))return [];
    return rows.filter(r=>r.target!==target&&!isFund(r)&&r.market===own.market&&own.industry&&r.industry===own.industry).map(row=>({
      ...row,comparison:peerProfile(own.industry).metrics.map(metric=>{
        const a=row[metric],b=own[metric];
        const periodRequired=['pb','ps','roe','net_margin','npl','revenue_growth','fcf_margin','retention','volatility'].includes(metric);
        const comparable=a&&b&&finite(a.value)&&finite(b.value)&&(metric!=='pe'||a.value>0&&b.value>0)&&a.basis===b.basis&&a.currency===b.currency&&(!periodRequired||a.period&&a.period===b.period)&&date(a.as_of)!==null&&date(b.as_of)!==null&&date(a.as_of)<=Date.now()&&date(b.as_of)<=Date.now()&&Math.abs(date(a.as_of)-date(b.as_of))<=92*DAY&&Date.now()-Math.min(date(a.as_of),date(b.as_of))<=180*DAY;
        return {metric,delta:comparable?a.value-b.value:null,reason:comparable?'同口径对比':'缺少可比资料或时点/口径不一致'};
      })}));
  }
  const dividendTerms=[
    {term:'每股股息',meaning:'每股获分配的股息金额。现金股息以币种/股计，不是百分比；本页未取得已核验的每股股息金额，不作反推。'},
    {term:'派息',meaning:'发放股息的行为；已宣告不等于已到账，除息日与派息到账日也不同。'},
    {term:'股息率',meaning:'年度每股现金股息 / 股价 × 100%。须区分过去12个月与预期口径；股价下跌也会推高股息率，不是总回报率。'},
    {term:'派息率（利润分配比例）',meaning:'同一期间、同一股份口径的每股股息 / 每股收益（EPS）× 100%，分母是盈利而非股价。盈利非正时不作正常比例解读，也不是越高越好。'}
  ];
  function dividendDisclosure(row,now=Date.now()) {
    function read(key){const m=row[key],at=date(m?.as_of);
      const valid=finite(m?.value)&&m.currency==='percent'&&!!m.basis&&at!==null&&at<=now;
      return {value:valid?m.value:null,text:valid?m.value.toFixed(2)+'%':m?'资料无效':'未取得',
        basis:valid?m.basis+'；'+(m.period?'期间 '+m.period:'期间末未取得')+'；快照 '+m.as_of.slice(0,10)+(now-at>14*DAY?'（超过14日）':''):'缺少有效数值、百分比单位、口径或快照日期'};
    }
    const yieldInfo=read('dividend_yield'),payout=read('payout_ratio'),m=row.payout_ratio,profit=row.net_income;
    // Only use a matching profit observation to flag loss-making periods; never reconstruct dividends.
    const samePeriod=m&&profit&&(m.period&&m.period===profit.period||!m.period&&!profit.period&&m.provider_field==='dividend_payout_ratio_ttm'&&profit.provider_field==='net_income_ttm')&&m.as_of===profit.as_of;
    let note='';
    if(yieldInfo.value<0){yieldInfo.text='不适用';yieldInfo.basis+='；来源负值，须核对分配口径';}
    if(payout.value!==null&&(payout.value<0||samePeriod&&finite(profit.value)&&profit.value<=0)){
      payout.text='不适用';note+=' 来源派息率 '+payout.value.toFixed(2)+'%；亏损、零盈利或负比例，须核对EPS，不能视为正常利润分配比例。';
    }else if(payout.value>100)note+=' 来源派息率超过100%，须核对盈利、特别股息与现金流，不直接判定未来减派。';
    else if(payout.value===0)note+=' 来源记录为0%，不等于历史从未派息或未来不会派息。';
    return {yieldInfo,payout,note};
  }
  function eventStudy(history,events,target,asOf=Date.now(),benchmark=null,posts=[]) {
    const past=retrospective(history,asOf),future=upcoming(events,target,asOf);
    const base=benchmark?retrospective(benchmark,asOf):null,aligned=base&&past.from===base.from&&past.to===base.to&&past.adjusted===base.adjusted;
    const excess7=aligned&&finite(past.return7)&&finite(base.return7)?past.return7-base.return7:null;
    const related=dedupePosts(posts,asOf).filter(p=>p.targets.includes(target)&&/^https:\/\//.test(p.url||''));
    const recent=related.filter(p=>date(p.published_at||p.as_of)>=date(past.from)&&date(p.published_at||p.as_of)<date(past.to)+DAY);
    const points=researchHistory(history,asOf).points.slice(-8),basePoints=researchHistory(benchmark,asOf).points;
    const priceStale=!past.to||asOf-date(past.to)>7*DAY||points.some((p,i)=>i>0&&p.at-points[i-1].at>7*DAY);
    const show=(v,suffix)=>finite(v)?v.toFixed(2)+suffix:'未取得';
    const displayed=new Map();
    for(const e of future){const id=eventKind(e)==='earnings'&&e.status!=='cancelled'?e.target+'|earnings|'+e.date:eventKey(e),prior=displayed.get(id);
      if(!prior||e.status==='confirmed'&&prior.status!=='confirmed'||e.status===prior.status&&date(e.observed_at)>date(prior.observed_at))displayed.set(id,e);
    }
    const links=[...displayed.values()].map(event=>{
      const sources=eventKind(event)==='earnings'&&event.status!=='cancelled'?future.filter(e=>e.target===event.target&&eventKind(e)==='earnings'&&e.date===event.date&&e.status!=='cancelled'):[event];
      const conflict=eventConflict(event,future),seen=date(event.first_observed_at)??date(event.observed_at);
      const sourceTime=date(event.data_as_of||event.observed_at),sourceStale=sourceTime===null||sourceTime>asOf||asOf-sourceTime>14*DAY;
      const evidence=related.filter(p=>{
        if(sources.some(e=>p.url===e.source_url||e.event_id&&(p.event_ids||[]).includes(e.event_id)))return true;
        // Date + event type may establish a lead only for a single attributed company.
        return p.targets.length===1&&eventKind(event)==='earnings'&&explicitDates((p.title||'')+' '+(p.summary||'')).includes(event.date)&&/财报|业绩|earnings/i.test((p.title||'')+' '+(p.summary||''));
      }).sort((a,b)=>date(a.published_at||a.as_of)-date(b.published_at||b.as_of));
      const timed=evidence.map(p=>({...p,source_url:p.url,published_at:p.published_at||p.as_of,
        timing:date(p.published_at||p.as_of)<date(past.from)?'价格窗口之前公开':date(p.published_at||p.as_of)<date(past.to)+DAY?'价格窗口内公开':'价格窗口之后公开',
        receipt:date(p.observed_at||p.collected_at)>=date(past.to)+DAY?'窗口结束后才采到，不能倒填历史信号':'保留原始采集时间'}));
      const gaps=[];
      if(past.return7===null)gaps.push('不足8根日线，无法形成7个收益区间');
      if(priceStale)gaps.push('日线超过7个自然日未更新、缺失或相邻记录间隔过长，暂停当前联动判断');
      if(!past.adjusted)gaps.push('未取得完整复权序列，除权除息可能影响价格变化');
      if(excess7===null)gaps.push('基准日期或复权口径未对齐，不能剔除市场共同涨跌');
      if(!finite(past.volumeRatio))gaps.push('前期成交量不足，不能验证放量');
      if(conflict)gaps.push('财报日期存在冲突或14日内多个候选日期，须核对财季与公司公告');
      if(sourceStale)gaps.push('事件来源快照超过14日、日期无效或来自未来，需重新核对');
      if(!timed.length)gaps.push('未匹配到带发布时间的直接事件新闻，相关公司新闻不等于事件证据');
      if(event.status!=='confirmed')gaps.push(event.status==='cancelled'?'事件已取消':event.type==='news_lead'?'仅新闻日期线索，不是已确认日程':'预计日期，尚需公司公告确认');
      if(!/^https:\/\//.test(event.source_url||''))gaps.push('缺少HTTPS事件原文');
      let status='待补证据',interpretation='目前不能将过去价格变化归因于这个事件。';
      if(event.status==='cancelled'){status='已取消';interpretation='原事件假设失效；不能继续作为未来催化。';}
      else if(conflict){status='日期待核对';interpretation='日程口径冲突，先核对公告，不按单一日期推进。';}
      else if(past.return7!==null&&!priceStale&&!sourceStale){
        if(excess7===null){status='仅价格观察';interpretation='有事件与价格变化，但没有同口径基准，暂不能区分公司预期与市场行情。';}
        else if(Math.abs(excess7)<1){status='相对变化不明显';interpretation='7日相对基准差值不足1个百分点，当前证据不足以识别事件特异性反应。';}
        else {status=excess7>0?'事件前相对走强':'事件前相对走弱';interpretation=(excess7>0?'价格相对基准走强，可检验预期改善或风险偏好抬升。':'价格相对基准走弱，可检验预期下修或风险溢价上升。')+'这只是时间上的共存，不代表事件导致涨跌。';}
      }
      const clock=t=>new Date(t).toISOString().slice(0,16).replace('T',' ')+' UTC';
      const chronology=timed.length?`匹配 ${timed.length} 条事件证据（非独立信源数）；最早可核验公开时间 ${clock(date(timed[0].published_at))}。`:'事件公告的最早公开时间尚未核验。';
      const receipt=`本系统首次观察到此日程 ${clock(seen)}；${seen>=date(past.to)+DAY?'在价格窗口结束后，不能用于该窗口的事前回测。':'采到时间不等于最早公开时间。'}`;
      const facts=`${target} 在 ${past.from?.slice(0,10)||'未知'} 至 ${past.to?.slice(0,10)||'未知'} 变化 ${show(past.return7,'%')}；相对基准 ${show(excess7,'个百分点')}；均量比 ${show(past.volumeRatio,'倍')}。${event.date}：${event.title}。`;
      const confirmation='先核对事件日期与最终公告；冻结同财季、同会计口径的预期值及来源，事件后核对实际值、指引与同期基准表现。';
      const invalidation='取消或延期使原时间假设失效；实际结果未达到冻结预期、预期下修，或同业同步波动，也会削弱事件解释。';
      const expected=finite(event.eps_forecast)?`来源EPS预期 ${event.eps_forecast}；${event.eps_basis||'财季及会计口径待核验'}；${event.eps_currency||'币种待核验'}。未完成同口径核对前，不评价超预期。`:'尚无已核验的数值预期基准，不能判断是否超预期或充分定价。';
      return {key:eventKey(event),event,sources,days:Math.round((date(event.date)-Date.parse(day(asOf)))/DAY),status,interpretation,facts,chronology,receipt,expected,gaps,evidence:timed.slice(0,12),evidenceCount:timed.length,confirmation,invalidation,
        canDraft:event.status!=='cancelled'&&!conflict&&!sourceStale&&!priceStale&&past.return7!==null&&/^https:\/\//.test(event.source_url||''),
        draft:{event_key:eventKey(event),event_date:event.date,direction:'0',note:[facts,interpretation,chronology,receipt,'待补：'+gaps.join('；')].join('\n'),expectation:expected+'\n来源：'+event.source_url,confirmation,invalidation}};
    });
    const path=points.map((p,i)=>{const b=basePoints.find(r=>day(r.at)===day(p.at)),b0=basePoints.find(r=>day(r.at)===day(points[0].at));return {date:day(p.at),close:p.close,change:i?(p.close/points[i-1].close-1)*100:null,cumulative:(p.close/points[0].close-1)*100,benchmark:past.adjusted===base?.adjusted&&b&&b0?(b.close/b0.close-1)*100:null};});
    return {past,events:future,benchmark:base,excess7,links,path,relatedCount:recent.length,
      window:`价格取最近7个已完成交易日的收益区间；未来事件按UTC日期含今天至第14日（${day(asOf)} 至 ${day(asOf+14*DAY)}）。日程日期不换算为公告时刻。`,
      method:'相对变化±1个百分点为描述性分组阈值，非显著性检验；均量比=近7根均量/此前20根均量（前期至少10根）。不计算已定价百分比或上涨概率。',
      hypothesis:past.return7===null?'历史日线不足，暂不判断提前反应':future.filter(e=>e.status!=='cancelled').length?
      '价格变动与未来事件同时存在；是否因预期提前交易，仍需行业基准、估值与后续结果验证。':'未取得未来14日相关事件；不代表没有事件。',
      confirmation:'核对事件最终公告、盈利预期变化与同行超额收益；事件落地后复盘实际结果。',
      invalidation:'事件延期或取消、预期下修，或涨幅主要由市场整体推动，均削弱该解释。',
      risk:'近期上涨、新闻热度或散户讨论量都不能单独证明已充分定价，也不意味着价格会持续上涨。'};
  }
  function explicitDates(text) {
    return [...String(text).matchAll(/(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?/g)].map(m=>`${m[1]}-${m[2].padStart(2,'0')}-${m[3].padStart(2,'0')}`).filter(d=>date(d)!==null&&day(date(d))===d);
  }
  function newsEventLeads(posts,target,now=Date.now()) {
    const result=[];
    for(const p of dedupePosts(posts,now)){
      if(p.targets.length!==1||p.targets[0]!==target||!/^https:\/\//.test(p.url||'')||now-date(p.published_at||p.as_of)>30*DAY)continue;
      const text=(p.title||'')+' '+(p.summary||''),sentences=text.split(/[。！？!?;；\n]/);
      for(const sentence of sentences){
        if(!/会议|峰会|财报|发布|政策|conference|summit|earnings|launch/i.test(sentence))continue;
        // Never invent a year or treat a historical/cancelled mention as a scheduled catalyst.
        if(/取消|延期|推迟|未定|否认|cancel|postpon|denied|not scheduled/i.test(sentence))continue;
        for(const d of explicitDates(sentence))if(d>=day(now)&&d<=day(now+14*DAY))result.push({target,title:p.title,date:d,status:'estimated',type:'news_lead',source:'新闻日期线索，待核对最终公告',source_url:p.url,published_at:p.published_at||p.as_of,observed_at:p.observed_at||p.collected_at,event_id:'news:'+p.url+'|'+d});
      }
    }
    return [...new Map(result.map(e=>[eventKey(e),e])).values()];
  }
  function eventDraft(study,key,existing={}) {
    const link=study.links.find(r=>r.key===key);if(!link?.canDraft)throw new Error('事件已失效、资料不足或日期冲突，请更新后核对');
    if(existing.event_key&&existing.event_key!==key)throw new Error('草稿已关联另一事件，请先保存或新建独立判断');
    const result={...link.draft};
    for(const field of ['note','expectation','confirmation','invalidation']){
      result[field]=existing[field]?.includes(link.draft[field])?existing[field]:[existing[field],link.draft[field]].filter(Boolean).join('\n');
      if(result[field].length>(field==='note'?4000:2000))throw new Error('草稿内容过长，请先精简；原草稿未修改');
    }
    if(existing.note)result.direction=String(existing.direction??0);
    return result;
  }
  function eventFollowup(record,events,now=Date.now()) {
    const frozen=record.inputs?.events||[],latest=latestEvents(events,now);
    return frozen.map(e=>{const current=latest.find(r=>eventKey(r)===eventKey(e));
      return {title:e.title,frozenDate:e.date,currentDate:current?.date||'',status:!current?'未采到后续日程，不能视为已取消':current.status==='cancelled'?'已取消：原事件假设失效':current.date!==e.date?'日程已变更：原窗口需复核':date(e.date)<Date.parse(day(now))?'日程已到：仍须核对实际公告及冻结预期':'等待事件；冻结记录不随日程更新而改写'};
    });
  }
  function dedupePosts(rows,asOf=Date.now()) {
    const seen=new Map();for(const row of rows||[]){
      const published=date(row.published_at||row.as_of),observed=date(row.observed_at||row.collected_at);
      if(published===null||observed===null||published>asOf||observed>asOf)continue;
      const fingerprint=String(row.text||row.title||row.excerpt||'').replace(/\s+/g,'').toLowerCase();
      const url=String(row.url||'').split('?')[0];const id=(row.platform||row.source||'')+'|'+(fingerprint||url);
      const targets=[...(row.targets||[]),...(row.assessments||[]).map(a=>a.target),row.target,row.market&&row.symbol?row.market+':'+row.symbol:null].filter(Boolean);
      const prior=seen.get(id);
      seen.set(id,{...(prior||row),targets:[...new Set([...(prior?.targets||[]),...targets])]});
    }return [...seen.values()];
  }
  const ATTENTION_VERSION='attention-v2';
  const attentionScope=r=>[r.source,r.coverage,r.cohort||'',r.method||'legacy'].join('|');
  function currentAttention(rows){if(!rows.length)return [];const scope=attentionScope(rows.at(-1));let i=rows.length-1;while(i>0&&attentionScope(rows[i-1])===scope)i--;return rows.slice(i);}
  function attention(rows,target,asOf=Date.now()) {
    const groups=new Map();
    for(const row of rows||[]) {
      if(row.target!==target||!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||!/(?:Z|[+-]\d{2}:\d{2})$/.test(row.observed_at)||date(row.observed_at)===null||date(row.observed_at)>asOf||date(row.date)===null||day(date(row.date))!==row.date||date(row.date)>asOf||
        !finite(row.count)||row.count<0||!finite(row.total)||row.total<=0||row.count>row.total||!row.source||!row.coverage)continue;
      const id=row.platform+'|'+row.date;const prior=groups.get(id);
      // Prefer a sealed bucket, then its earliest available version, not a future revision.
      if(!prior||(row.closed===true&&prior.closed!==true)||(row.closed===prior.closed&&date(row.observed_at)<date(prior.observed_at)))groups.set(id,{...row,share:row.count/row.total});
    }
    const series={};for(const row of [...groups.values()].sort((a,b)=>a.date.localeCompare(b.date))) {
      const list=series[row.platform]||(series[row.platform]=[]);
      const usable=r=>r.closed!==false&&r.complete!==false&&!r.truncated&&r.total>=20&&date(r.observed_at)<=date(r.date)+2*DAY;
      const prior=currentAttention([...list,row]).slice(0,-1);
      const baseline=prior.slice(-28).filter(r=>usable(r)&&date(r.observed_at)<=date(row.observed_at)&&date(r.date)>=date(row.date)-28*DAY).slice(-20);
      const average=mean(baseline.map(r=>r.share)),variance=average===null?null:mean(baseline.map(r=>(r.share-average)**2));
      const smoothed=average===null?row.share:(row.count+20*average)/(row.total+20);
      const sd=average===null?null:Math.sqrt(variance+Math.max(0.0001,average*(1-average))/(row.total+20));
      const ready=usable(row)&&baseline.length>=14,previous=baseline.at(-1);
      const oldMean=average,oldSd=baseline.length>=7?Math.sqrt(variance):null;
      list.push({...row,smoothed,baseline:baseline.length,quality:ready?'可作研究样本':row.closed===false?'日内累计，尚未封存':row.total<20?'样本少于20条':'覆盖或历史不足',
        z:ready&&sd>0?Math.max(-6,Math.min(6,(smoothed-average)/sd)):null,
        change:ready&&previous&&date(row.date)-date(previous.date)===DAY?smoothed-previous.smoothed:null,
        legacyZ:usable(row)&&oldSd>0?(row.share-oldMean)/oldSd:null});
    }
    return series;
  }
  function sampleAttention(posts,target,now=Date.now()) {
    const groups=new Map();for(const post of dedupePosts(posts,now)) {
      if(date(post.published_at||post.as_of)<now-DAY)continue;
      const platform=post.platform||'新闻',row=groups.get(platform)||{target,platform,date:day(now),count:0,total:0,source:'STock 已采集样本',source_url:post.url,coverage:'近24小时本机已采集去重样本，非全平台热度',observed_at:new Date(now).toISOString()};
      row.total++;if(post.targets.includes(target))row.count++;groups.set(platform,row);
    }return [...groups.values()];
  }
  function propagation(rows,target,from,to,asOf=Date.now()) {
    const series=Object.fromEntries(Object.entries(attention(rows,target,asOf)).map(([p,r])=>[p,currentAttention(r)])),a=series[from]||[],b=new Map((series[to]||[]).map(r=>[r.date,r]));
    const pairs=lag=>a.flatMap(r=>{const next=b.get(day(date(r.date)+lag*DAY));return finite(r.change)&&finite(next?.change)?[{date:r.date,x:r.change,y:next.change}]:[];});
    const end=a.length?date(a[a.length-1].date):0,begin=a.length?date(a[0].date):0,split=begin+(end-begin)*0.7;
    const candidates=[0,1,2,3,4,5,6,7].map(lag=>{const points=pairs(lag),train=points.filter(p=>date(p.date)+lag*DAY<split),test=points.filter(p=>date(p.date)>=split+7*DAY);
      return {lag,train:train.length,test:test.length,trainCorrelation:pearson(train.map(p=>p.x),train.map(p=>p.y)),testCorrelation:pearson(test.map(p=>p.x),test.map(p=>p.y),10)};});
    const eligible=candidates.filter(r=>r.lag>0&&r.train>=40&&r.trainCorrelation>0).sort((a,b)=>b.trainCorrelation-a.trainCorrelation||a.lag-b.lag);
    const coverage=platform=>new Set((series[platform]||[]).map(attentionScope));
    const stable=from!==to&&coverage(from).size===1&&coverage(to).size===1;
    const best=eligible[0]||null,byDate=new Map(a.map(r=>[r.date,r]));
    const controls=best?a.flatMap(r=>{const same=b.get(r.date),nextDate=day(date(r.date)+best.lag*DAY),next=b.get(nextDate),reverse=byDate.get(nextDate);
      return date(r.date)>=split+7*DAY&&[r.change,same?.change,next?.change,reverse?.change].every(finite)?[{x:r.change,y:next.change,same:same.change,reverse:reverse.change}]:[];}):[];
    const zeroCorrelation=pearson(controls.map(r=>r.x),controls.map(r=>r.same),20),reverseCorrelation=pearson(controls.map(r=>r.same),controls.map(r=>r.reverse),20);
    if(best){best.test=controls.length;best.testCorrelation=pearson(controls.map(r=>r.x),controls.map(r=>r.y),20);}
    const qualified=!!(stable&&best?.test>=20&&best.testCorrelation>0.2&&finite(zeroCorrelation)&&finite(reverseCorrelation)&&best.testCorrelation>Math.max(zeroCorrelation,reverseCorrelation)+0.1);
    return {version:ATTENTION_VERSION,from,to,candidates,best,qualified,stable,reverseCorrelation,zeroCorrelation,
      status:!stable?'平台未采到、口径变化或平台相同':!best?'样本不足或训练期没有正向领先':qualified?'留出方向一致，仍需后续复验':'未通过留出或同步/反向对照',
      note:'热度按样本占比收缩，至少20条/日、14个历史日；传播比较相邻日变化而非热度水平。前70%选1至7日滞后，隔离7日后检验，至少40训练/20留出样本。留出相关须>0.2并高于同步及反向对照0.1；这些是固定研究门槛，不是统计显著性、因果证明或上涨概率。'};
  }
  function attentionBacktest(rows,target,platform,history,B,costs={},now=Date.now()) {
    const available=currentAttention(attention(rows,target,now)[platform]||[]),series=available.filter(r=>finite(r.z)).slice(-180);
    const result={version:ATTENTION_VERSION,status:'待积累可用热度历史',runs:[],samples:series.length,
      note:'新旧算法在相同可用样本与同一留出区间比较。固定Z阈值1.5，只做多/空仓；信号有效7日，先采用入库后的完整UTC日期日线，再于下一根收盘模拟成交，含手续费与滑点。热度没有多空方向，升温不等于利好。此项为历史规则重建，不是当时冻结的实盘判断；重复查看留出结果再调参会造成过拟合。'};
    if(series.length<90||new Set(series.map(attentionScope)).size!==1){result.status='需至少90个同口径有效日样本；未取得的数据不补零';return result;}
    const split=date(series[Math.floor(series.length*.7)].date),test=series.filter(r=>date(r.date)>=split+7*DAY);
    if(test.length<20){result.status='隔离7日后留出不足20日';return result;}
    try {
      const h=history&&B.history(history),start=day(split+7*DAY);
      if(!h)throw Error('缺少历史日线');
      const days=h.points.filter(p=>p.time>=start&&date(p.time)<now);
      if(days.length<20)throw Error('留出区间不足20根完整日线');
      for(const [version,score] of [['share-z-v1','legacyZ'],[ATTENTION_VERSION,'z']]){
        const records=test.map(r=>({id:version+':'+r.date,target,created_at:r.observed_at,direction:r[score]>=1.5?1:0}));
        const snapshot=B.snapshot({target,benchmark:target,strategy:'frozen',start,hold_days:7,fee_bps:costs.fee_bps??5,slippage_bps:costs.slippage_bps??5,sell_tax_bps:costs.sell_tax_bps??0},h,h,records);
        snapshot.created_at=new Date(now).toISOString();
        snapshot.attention_model={version,origin:'historical_rule_reconstruction',platform,threshold:1.5,split:day(split),purge_days:7,
          rows:available.filter(r=>date(r.date)>=date(series[0].date)-28*DAY).slice(-220).map(r=>Object.fromEntries(['target','platform','date','count','total','source','source_url','coverage','cohort','method','observed_at','closed','complete','truncated'].filter(k=>r[k]!==undefined).map(k=>[k,r[k]])))};
        const simulation=B.run(snapshot);
        result.runs.push({version,snapshot,result:simulation});
      }
      result.status='留出区间费用后对照，未自动择优上线';result.start=start;
    }catch(error){result.runs=[];result.status='回测暂停：'+error.message;}
    return result;
  }
  function validateImport(value,now=Date.now()) {
    if(!value||!Array.isArray(value.events||[])||!Array.isArray(value.attention||[])||!Array.isArray(value.peers||[]))throw new Error('请选择研究资料 JSON（events / attention / peers 数组）');
    const result={events:[],attention:[],peers:[]};
    const safe=url=>typeof url==='string'&&/^https:\/\/[^\s/@]+(?:\/[^\s]*)?$/.test(url);
    for(const type of Object.keys(result)) {
      if((value[type]||[]).length>1000)throw new Error('单类资料最多1000条');
      result[type]=(value[type]||[]).map(row=>{
        if(!row||typeof row.target!=='string'||!/^(US|CN|HK|KR|FUND):[A-Z0-9.-]{1,24}$/.test(row.target)||!safe(row.source_url))throw new Error('每条资料须有市场:代码及 HTTPS 原文链接');
        const saved={...row,observed_at:new Date(now).toISOString(),first_observed_at:new Date(now).toISOString(),imported:true};
        if(type==='events'&&(!row.title||date(row.date)===null||!['confirmed','estimated','cancelled'].includes(row.status)))throw new Error('事件须含标题、日期及 confirmed/estimated/cancelled 状态');
        if(type==='attention'&&(!row.platform||!row.coverage||!row.source||date(row.date)===null||date(row.date)>now||!finite(row.count)||!finite(row.total)||row.count<0||row.total<=0||row.count>row.total))throw new Error('热度须注明采样覆盖、日期、提及数及总样本数；不能把未采到当作零');
        if(type==='peers'){
          if(row.market!==row.target.split(':')[0]||!row.name||!row.industry)throw new Error('同行须注明正式名称、市场及细分行业');
          for(const metric of ['pe','market_cap','dividend_yield','payout_ratio','leverage','volatility','net_income','pb','ps','net_margin','roe','npl','revenue_growth','fcf_margin','retention'])if(row[metric]&&(!finite(row[metric].value)||!row[metric].basis||!row[metric].currency||date(row[metric].as_of)===null||date(row[metric].as_of)>now))throw new Error('同行指标须填写数值、资料日期、币种及计算口径');
        }
        return saved;
      });
    }
    return result;
  }
  function attentionPrice(rows,target,platform,history,now=Date.now()) {
    const series=attention(rows,target,now)[platform]||[],prices=researchHistory(history,now).points;
    const cutoff=series.length?date(series[Math.floor(series.length*0.7)].date):0;
    return [1,3,7,14].map(lag=>{
      const samples=series.flatMap(row=>{
        const entry=prices.find(p=>p.at>=date(row.date)+DAY&&p.at>=date(row.observed_at)),exit=entry&&prices.find(p=>p.at>=entry.at+lag*DAY);
        if(!finite(row.z)||!entry||entry.at>date(row.date)+4*DAY||!exit||exit.at>entry.at+(lag+4)*DAY)return [];
        return [{x:row.z,y:exit.close/entry.close-1,at:entry.at,end:exit.at}];
      });
      const train=samples.filter(r=>r.end<cutoff),test=samples.filter(r=>r.at>=cutoff);
      return {lag,train:train.length,test:test.length,trainCorrelation:pearson(train.map(r=>r.x),train.map(r=>r.y)),testCorrelation:pearson(test.map(r=>r.x),test.map(r=>r.y),10)};
    });
  }
  function freeze(target,history,events,options={},now=Date.now()) {
    const past=retrospective(history,now);let future=upcoming(events,target,now).filter(r=>r.status!=='cancelled'&&/^https:\/\//.test(r.source_url||''));
    if(options.event_key){
      const study=eventStudy(history,events,target,now,null,options.posts||[]),link=study.links.find(r=>r.sources.some(e=>eventKey(e)===options.event_key));
      if(!link?.canDraft)throw new Error('关联事件已失效、过期或日期冲突，请重新核对');
      if(options.event_date&&link.event.date!==options.event_date)throw new Error('事件日期已变更，请重新生成研究草稿');
      future=future.filter(r=>eventKey(r)===options.event_key);
    }
    if(past.return7===null||!future.length)throw new Error('需要至少8根日线和未来14日带来源、观察时间的事件');
    const direction=Number(options.direction);
    if(![-1,0,1].includes(direction))throw new Error('请选择看多、中性或看空');
    const fee=Number(options.fee_bps||0),slip=Number(options.slippage_bps||0);
    if(![fee,slip].every(x=>finite(x)&&x>=0&&x<=1000))throw new Error('费用和滑点须在0至1000基点之间');
    const v2=options.method==='event-forward-v2',extra={};
    if(v2){
      for(const key of ['author','expectation','confirmation','invalidation']){const value=options[key];if(typeof value!=='string'||!value.trim()||value.length>(key==='author'?60:2000))throw new Error('请完整填写作者、预期基准、成立和失效条件');extra[key]=value.trim();}
      if(!String(options.note||'').trim())throw new Error('请填写判断依据');
      for(const key of ['tax_bps','borrow_bps']){const value=Number(options[key]||0);if(!finite(value)||value<0||value>10000)throw new Error('税费和年化借券费用须为0至10000基点');extra[key]=value;}
      if(!/^(US|CN|HK|KR):[A-Z0-9.-]{1,24}$/.test(options.benchmark||''))throw new Error('请选择有效比较基准');
      extra.benchmark=options.benchmark;extra.parent_id=String(options.parent_id||'');
    }
    return {target,created_at:new Date(now).toISOString(),due_at:new Date(now+14*DAY).toISOString(),direction,
      fee_bps:fee,slippage_bps:slip,event_key:options.event_key||'',event_date:options.event_date||'',inputs:{past,events:future,event_candidates:upcoming(events,target,now).slice(0,30),posts:dedupePosts(options.posts||[],now).filter(p=>p.targets.includes(target)).slice(0,40)},note:String(options.note||'').slice(0,4000),...extra,method:v2?'event-forward-v2':'event-forward-v1'};
  }
  function backtest(records,histories,now=Date.now()) {
    const rows=records.map(record=>{
      const created=date(record.created_at),due=date(record.due_at),history=researchHistory(histories[record.target],now),data=history.points;
      if(created===null||due===null||due<created+DAY)return {...record,status:'invalid'};
      // Entry is the next observed daily close, not a price already known at signal time.
      const entry=data.find(p=>p.at>created),exit=data.find(p=>p.at>=due&&entry&&p.at>entry.at);
      if(!entry||!exit)return {...record,status:now<due+7*DAY?'pending':'missing'};
      if(entry.at>created+7*DAY||exit.at>due+7*DAY)return {...record,status:'missing'};
      const move=(exit.close/entry.close-1)*100,gross=record.direction*move,days=(exit.at-entry.at)/DAY;
      const costs=record.direction===0?0:2*((Number(record.fee_bps)||0)+(Number(record.slippage_bps)||0))/100+(Number(record.tax_bps)||0)/100+(record.direction===-1?(Number(record.borrow_bps)||0)/100*days/365:0);
      const base=researchHistory(histories[record.benchmark],now),start=base.points.find(p=>day(p.at)===day(entry.at)),end=base.points.find(p=>day(p.at)===day(exit.at));
      const benchmark=start&&end&&base.adjusted===history.adjusted?(end.close/start.close-1)*100:null;
      const path=data.filter(p=>p.at>=entry.at&&p.at<=exit.at).map(p=>record.direction*(p.close/entry.close-1)*100);
      const neutralV2=record.method==='event-forward-v2'&&record.direction===0;
      return {...record,status:'evaluated',entry_at:entry.time,exit_at:exit.time,gross,net:neutralV2?null:gross-costs,move,costs,benchmark,excess:benchmark===null?null:move-benchmark,
        adverse:record.direction===0?null:Math.min(0,...path),adjusted:history.adjusted,review:neutralV2?'中性判断只记录事实，不计模拟胜率':history.adjusted?'来源复权序列，非成交记录':'未核验复权，仅价格观察'};
    }).sort((a,b)=>date(a.created_at)-date(b.created_at));
    const evaluated=rows.filter(r=>r.status==='evaluated'&&finite(r.net)),split=rows.length?date(rows[Math.floor(rows.length*0.7)].created_at):0;
    const train=evaluated.filter(r=>date(r.exit_at)<split),test=evaluated.filter(r=>date(r.created_at)>=split);
    const stats=xs=>({samples:xs.length,mean:mean(xs.map(r=>r.net)),hit:xs.length?xs.filter(r=>r.net>0).length/xs.length:null});
    return {rows,train:stats(train),test:stats(test),note:'冻结后下一根日线收盘入场、14日后第一根日线收盘观察；含双边费用/滑点近似扣减，非可成交承诺。时间分割并剔除跨界样本；重叠记录非独立样本，不报告胜率显著性或上涨概率。'};
  }
  function peerProfile(industry='') {
    if(/broker|investment bank|investment manager|证券|券商|资产管理/i.test(industry))return {name:'券商与资产管理',weights:{pe:20,pb:10,roe:20,revenue_growth:20,dividend_yield:10,volatility:20},metrics:['pe','pb','roe','revenue_growth','dividend_yield','volatility','market_cap'],note:'券商与资管不套用商业银行不良贷款模型；仍须核对客户资产、净流入、交易收入及资本约束。'};
    if(/bank|银行/i.test(industry))return {name:'银行',weights:{pb:20,pe:10,roe:20,npl:20,dividend_yield:10,volatility:20},metrics:['pb','roe','npl','pe','dividend_yield','volatility','market_cap'],note:'PB、ROE与资产质量须同报告期；还须核对净息差、拨备和资本充足率。低PE/PB不自动表示便宜。'};
    if(/software|软件|cloud|云服务/i.test(industry))return {name:'软件与云服务',weights:{ps:15,pe:10,revenue_growth:25,fcf_margin:20,net_margin:10,leverage:10,volatility:10},metrics:['ps','pe','revenue_growth','fcf_margin','net_margin','retention','leverage','volatility','market_cap'],note:'增长、现金流率、留存须同口径；亏损企业不按负PE排名，PS也不能脱离利润率解读。留存未取得时不推算。'};
    if(/mining|minerals|metals|矿|金属|oil|gas|石油|天然气/i.test(industry))return {name:'资源与周期企业',weights:{pe:15,pb:15,revenue_growth:15,net_margin:20,leverage:15,volatility:20},metrics:['pe','pb','revenue_growth','net_margin','leverage','volatility','dividend_yield','market_cap'],note:'周期高点的低PE不代表便宜；资源储量、品位、单位成本及商品价格敏感性尚须核验，同业分类不等于同一矿种。'};
    return {name:'通用企业',weights:{pe:15,pb:10,revenue_growth:20,net_margin:20,leverage:15,volatility:15,dividend_yield:5},metrics:['pe','pb','revenue_growth','net_margin','dividend_yield','leverage','volatility','market_cap'],note:'先核对主营、增长与财务期间；同一行业代码不保证业务可比。'};
  }
  const PEER_VERSION='STock Peer 1.0';
  // Floors prevent tiny denominators and percentage-point changes dominating the model.
  const PEER_METRICS={
    pe:['PE','估值',-1,10],pb:['PB','估值',-1,1],ps:['PS','估值',-1,2],
    roe:['ROE','经营质量',1,10],npl:['不良贷款率','经营质量',-1,1],
    net_margin:['净利率','经营质量',1,10],fcf_margin:['自由现金流率','经营质量',1,10],
    revenue_growth:['收入增长率','成长',1,10],leverage:['债务/权益','风险',-1,1],
    volatility:['年化波动率','风险',-1,20],dividend_yield:['股息率','股息收益',1,3]
  };
  function peerEvidence(own,row,metric,now) {
    const a=row[metric],b=own[metric],fail=reason=>({valid:false,reason,reliability:0});
    if(!a||!b||!finite(a.value)||!finite(b.value))return fail('缺少双方有效数值');
    if(!a.basis||a.basis!==b.basis||!a.currency||a.currency!==b.currency||a.currency==='unverified')return fail('指标口径或单位不一致');
    if(['pe','pb','ps'].includes(metric)&&(a.value<=0||b.value<=0))return fail('负值或零估值倍数不参与便宜程度排名');
    if(['leverage','npl','volatility','dividend_yield'].includes(metric)&&(a.value<0||b.value<0))return fail('负权益或异常负值，不能解释为低风险');
    if(metric==='roe'&&[own.pb,row.pb].some(p=>finite(p?.value)&&p.value<=0))return fail('净资产非正，ROE不能作为经营质量优势');
    const dates=[date(a.as_of),date(b.as_of)];
    if(dates.some(t=>t===null||t>now||now-t>14*DAY)||Math.abs(dates[0]-dates[1])>7*DAY)return fail('快照超过14日、相差超过7日或来自未来');
    if(a.period||b.period){
      if(!a.period||a.period!==b.period)return fail('报告期或收益区间不一致');
      return {valid:true,reliability:1,reason:'相同期间、口径与单位'};
    }
    if(metric==='dividend_yield')return {valid:true,reliability:1,reason:'同口径当前股息率，非未来分红承诺'};
    if(metric==='volatility'||metric==='npl')return fail('缺少可核验的同一期间');
    if(a.date_kind==='provider_snapshot'&&b.date_kind==='provider_snapshot'&&a.provider_field&&a.provider_field===b.provider_field&&row.source===own.source)
      return {valid:true,reliability:0.65,reason:'同来源最新财务快照；报告期末未取得，仅作折扣初筛'};
    return fail('缺少报告期或可追溯的来源字段');
  }
  function peerAnalysis(target,rows,now=Date.now()) {
    rows=peerRows(rows.filter(r=>date(r.observed_at)!==null&&date(r.observed_at)<=now));
    const own=rows.find(r=>r.target===target),profile=peerProfile(own?.industry),weights=profile.weights;
    const result={version:PEER_VERSION,profile,asOf:new Date(now).toISOString(),candidates:[],excluded:[],commonCoverage:0,commonMetrics:[],ranked:0,
      method:'相对研究分以当前标的50为基准；同一候选组使用共同指标，缺失权重不重新分配。高于50仅表示该规则下相对占优，不是上涨概率、收益预测或买入指令。',
      formula:'50 + 0.5 × Σ[原始权重 × 证据系数 × 方向化差异]；差异=(候选−当前)/max(|候选|,|当前|,尺度下限)，限幅±1。最新财务快照系数0.65，同期证据1。',
      universe:'仅同市场、同细分行业、同报价币种的股票；公开初筛取市值前16家并保留当前标的，另含已缓存同业，非全市场最优排名。',
      note:'先更新研究数据以取得同行指标。',stability:'尚未形成可比较的排名。'};
    if(!own||own.asset_type!=='stock'||!own.industry){result.note=isFund(own||{})?'ETF、ETN与基金不进入公司同行模型；应使用底层持仓分析。':'当前标的普通股身份或细分行业未取得，暂停排名。';return result;}
    for(const row of rows.filter(r=>r.target!==target)){
      let excluded='';
      if(isFund(row))excluded='基金/ETN不作为公司同行';
      else if(row.asset_type!=='stock')excluded='普通股身份尚未核验';
      else if(row.market!==own.market)excluded='不同上市市场';
      else if(row.industry!==own.industry)excluded='细分行业不同或未知';
      else if(!row.currency||!own.currency||row.currency!==own.currency)excluded='报价币种未核验或不一致';
      else if(!/^https:\/\//.test(row.source_url||'')||!/^https:\/\//.test(own.source_url||''))excluded='缺少可追溯来源';
      if(excluded){result.excluded.push({target:row.target,name:row.name,reason:excluded});continue;}
      const evidence=Object.entries(weights).map(([metric,weight])=>{
        const [label,dimension,direction,floor]=PEER_METRICS[metric],check=peerEvidence(own,row,metric,now);
        const unit=check.valid?Math.max(-1,Math.min(1,direction*(row[metric].value-own[metric].value)/Math.max(Math.abs(row[metric].value),Math.abs(own[metric].value),floor))):null;
        return {metric,label,dimension,weight,direction,floor,...check,unit,current:own[metric]?.value??null,candidate:row[metric]?.value??null};
      });
      const valid=evidence.filter(e=>e.valid),coverage=valid.reduce((s,e)=>s+e.weight,0),dims=new Set(valid.map(e=>e.dimension));
      const eligible=coverage>=60&&dims.size>=3&&dims.has('估值')&&dims.has('经营质量');
      result.candidates.push({...row,evidence,coverage,eligible,score:null,rank:null,dimensions:[],interval:null,
        reason:eligible?'待统一比较指标':'证据不足：须覆盖至少60%权重、3个维度，且同时包含估值与经营质量'});
    }
    const eligible=result.candidates.filter(r=>r.eligible),keys=Object.keys(weights);
    // At most seven scored metrics: exhaustively choose coverage, never choose on score.
    let cohort={metrics:[],members:[],weight:0};
    for(let mask=1;mask<2**keys.length;mask++){
      const metrics=keys.filter((_,i)=>mask&(1<<i)),weight=metrics.reduce((s,k)=>s+weights[k],0),dimensions=new Set(metrics.map(k=>PEER_METRICS[k][1]));
      if(weight<60||dimensions.size<3||!dimensions.has('估值')||!dimensions.has('经营质量'))continue;
      const members=eligible.filter(r=>metrics.every(k=>r.evidence.find(e=>e.metric===k).valid));
      if(members.length>cohort.members.length||members.length===cohort.members.length&&weight>cohort.weight)cohort={metrics,members,weight};
    }
    const common=cohort.members.length?cohort.metrics:[],selected=new Set(cohort.members.map(r=>r.target));
    const commonWeight=common.reduce((s,k)=>s+weights[k],0),dims=new Set(common.map(k=>PEER_METRICS[k][1]));
    result.commonCoverage=commonWeight;result.commonMetrics=common.map(k=>PEER_METRICS[k][0]);
    const canRank=commonWeight>=60&&dims.size>=3&&dims.has('估值')&&dims.has('经营质量');
    for(const row of result.candidates){
      const evidence=row.evidence.map(e=>({...e,used:canRank&&selected.has(row.target)&&common.includes(e.metric),contribution:canRank&&selected.has(row.target)&&common.includes(e.metric)?0.5*e.weight*e.reliability*e.unit:null}));
      row.evidence=evidence;
      if(!canRank||!selected.has(row.target)){if(row.eligible)row.reason='未覆盖最大可比组的统一指标，保留双边差异，不与该组混排。';continue;}
      row.score=50+evidence.reduce((s,e)=>s+(e.contribution??0),0);
      const effective=evidence.filter(e=>e.used).reduce((s,e)=>s+e.weight*e.reliability,0),uncertainty=(100-effective)/2;
      row.interval=[Math.max(0,row.score-uncertainty),Math.min(100,row.score+uncertainty)];
      row.effectiveCoverage=effective;row.snapshotOnly=evidence.some(e=>e.used&&e.reliability<1);
      row.reason=row.snapshotOnly?'快照初筛，尚未完成同报告期核验':'同期证据初筛，仍须验证主营与持续性';
      const positive=evidence.filter(e=>e.contribution>0).sort((a,b)=>b.contribution-a.contribution),negative=evidence.filter(e=>e.contribution<0).sort((a,b)=>a.contribution-b.contribution);
      row.drivers=`主要加分：${positive.slice(0,2).map(e=>e.label).join('、')||'未发现'}；主要减分：${negative.slice(0,2).map(e=>e.label).join('、')||'当前样本未发现'}。`;
      row.dimensions=[...new Set(evidence.map(e=>e.dimension))].map(name=>({name,contribution:evidence.filter(e=>e.dimension===name&&e.used).reduce((s,e)=>s+e.contribution,0),used:evidence.some(e=>e.dimension===name&&e.used)}));
    }
    result.candidates.sort((a,b)=>(a.score===null)-(b.score===null)||(b.score??0)-(a.score??0)||b.coverage-a.coverage||a.target.localeCompare(b.target));
    const ranked=result.candidates.filter(r=>r.score!==null);result.ranked=ranked.length;
    for(let i=0;i<ranked.length;i++)ranked[i].rank=i&&Math.abs(ranked[i].score-ranked[i-1].score)<1e-9?ranked[i-1].rank:i+1;
    if(ranked.length>1){
      const first=ranked[0];const sensitive=common.filter(k=>ranked.slice(1).some(r=>r.score-r.evidence.find(e=>e.metric===k).contribution>first.score-first.evidence.find(e=>e.metric===k).contribution+1e-9));
      result.stability=sensitive.length?'排序敏感：去掉 '+sensitive.map(k=>PEER_METRICS[k][0]).join('、')+' 后首位改变。':'逐项剔除共同指标，首位未改变；不代表未来收益稳定。';
      if(ranked[0].interval[0]<=ranked[1].interval[1])result.stability+=' 首两位资料缺口区间重叠，不能断言领先。';
    }else if(ranked.length===1)result.stability='仅1个符合条件的候选，只作双边比较，不代表行业第一。';
    result.note=`${result.candidates.length} 家同业候选，${result.ranked} 家可排序；共同指标权重 ${commonWeight}%。按覆盖人数最多、其次权重最高选择可比组，不按得分挑公司。资料缺口区间是未覆盖权重的最坏/最好边界，不是统计置信区间。`;
    return result;
  }
  const numberText=(x,d=2)=>finite(x)?x.toLocaleString('zh-CN',{maximumFractionDigits:d}):'未取得';
  const percentText=x=>finite(x)?numberText(x)+'%':'未取得';
  function portfolioReport(positions,account,histories={},identities=[],funds={},options={}) {
    // Narrative and limits always use total assets, even when a table shows invested weights.
    const model=portfolio(positions,account,histories,peerRows(identities),{...options,basis:'total',strictAdjusted:true});
    const exposure=lookthrough(positions,funds,{basis:'total',cash_base:account.cash_base,receivable_base:account.receivable_base,payable_base:account.payable_base});
    const sections=[],add=(title,fact,meaning,action)=>sections.push({title,fact,meaning,action});
    const currency=account.base_currency||'CNY',unique=new Set(positions.map(key)).size;
    const stale=positions.filter(p=>p.quote_error||p.identity_warning||['stale','error','missing'].includes(p.quote_status||p.status));
    add('先确认这份诊断能覆盖什么',`当前 ${unique} 个不同标的，${model.coverage.valued}/${model.coverage.total} 条持仓可估值；${stale.length} 条记录存在身份或行情警告。已知资产金额 ${numberText(model.known)} ${currency}。`,model.complete?'金额齐全不代表全部是最新价格。下文仓位及压力测算统一以含现金总资产为分母，避免混用证券仓位。':'缺少市值或现金汇率，不能把已知部分当成完整组合；比例、超限和压力结果暂停。','先核对报价时间、休市收盘价、成本币种与汇率，再解释损益；旧报价保留不等于实时成交价。');
    if(!positions.length){add('尚未形成证券组合','当前没有持仓。','没有持仓数据不代表已经完成风险评估。','录入持仓与现金后再生成集中度、相关性和同行研究。');return {model,sections,exposure};}
    const first=model.largest[0];
    add('收益最依赖哪些持仓',`最大持仓 ${first?.name||first?.symbol||'未知'}，占总资产 ${percentText(model.max)}；前五大合计 ${percentText(model.top5)}。设置上限分别为 ${account.position_limit??35}%、${account.top5_limit??80}%。`,model.complete?(model.alerts.length?`当前触发 ${model.alerts.length} 项集中度规则。持有股票数量多，不代表每个标的对损益的贡献均衡。`:'当前已知集中度未触发设置上限；这不说明估值合理或不会亏损。'):'估值不完整，无法确认最大仓位或是否超限。','优先复核大仓位的基本面、估值与失效条件；调仓应比较税费、流动性和替代标的，不机械按告警买卖。');
    const sector=model.sector.find(s=>!['未分类','基金/ETN（待穿透）'].includes(s.name));
    add('行业是否形成同一个风险押注',`已估值证券行业覆盖 ${percentText(model.coverage.sector)}；未分类金额 ${numberText(model.coverage.unknownValue)} ${currency}。${sector?`最大已知行业 ${classificationName(sector.name)} 占总资产 ${percentText(sector.weight)}。`:'尚无已核验行业分类。'}`,'行业相同的公司可能同时受利率、需求、监管或商品价格影响。外层行业统计不是ETF穿透行业，未知部分不能视作已分散。','同行比较用于挑选行业内更合适的公司；如果行业已经超限，增持同业通常会加重风险，而不是分散风险。');
    const foreign=model.currency.filter(c=>c.name!=='未分类'&&c.name!==currency),foreignWeight=foreign.reduce((s,c)=>s+(c.weight||0),0);
    add('现金与汇率会怎样改变结果',`现金占总资产 ${percentText(model.cash)}；${model.complete?`已分类的非${currency}计价证券占 ${percentText(foreignWeight)}`:'非基准币种占比暂不可算'}。`,'现金降低证券方向暴露，但也可能带来机会成本。报价币种只是估值换算敞口，不等于公司的实际收入、成本或对冲敞口。','先确定流动性需求及基准币种；比较同业时保持价格、盈利和股息的币种及股份类别一致，不能直接混比A股与H股。');
    const pairs=model.correlations.filter(r=>finite(r.correlation)),high=pairs.filter(r=>r.correlation>=0.7).sort((a,b)=>b.correlation-a.correlation),largest=high[0];
    add('相关性：分散是否真实存在',`${model.correlations.length} 对持仓中，${pairs.length} 对满足复权及样本要求；${high.length} 对历史相关系数不低于0.70。${largest?`其中 ${largest.a} / ${largest.b} 为 ${numberText(largest.correlation,3)}，${largest.samples} 个对齐区间。`:''}`,'0.70只作为检查阈值，不是未来同步上涨概率。高正相关提示可能重复承担同一风险；低相关或负相关也可能在危机中变化。','先检查高权重且高相关的组合，再核对共同业务驱动。没有足够样本的配对标记未知，不能当成相关性为零。');
    add('ETF 是否重复持有同一家公司',`已知底层重复敞口 ${exposure.overlaps.length} 项；${exposure.unknown.length} 项底仓或权重覆盖提示。${exposure.overlaps[0]?`最大已知重复标的 ${exposure.overlaps[0].name}，穿透占比 ${percentText(exposure.overlaps[0].weight)}。`:''}`,'直持股票与ETF底仓可能重复，不能把ETF外层市值和全部底仓再次相加。不同披露日也会影响比较。','优先核对最新官方底仓与披露日；仅在完整同口径底仓下解读重叠率，缺失部分不补齐到100%。');
    const valid=model.complete&&model.total>0&&finite(account.cash_base)&&account.cash_base>=0&&positions.every(p=>finite(p.value??p.market_value_base)&&(p.value??p.market_value_base)>=0);
    add('压力测试：假设发生，不是预测',valid?`假设最大持仓单独下跌20%，其他证券、现金和汇率不变，总资产机械变化约 -${numberText(model.max*0.2)}%；假设全部证券同时下跌10%，约 -${numberText((1-(account.cash_base+(account.receivable_base||0)-(account.payable_base||0))/model.total)*10)}%。`:'金额不全、净资产非正或存在负仓位，暂停简单多头压力测算。','这里只做仓位乘冲击幅度的敏感性分析，未模拟跳空、衍生品、税费、相关性变化与汇率联动，也不是最大可能亏损或VaR。','用自己的可承受损失检查仓位是否匹配目标；若机械冲击已经难以接受，应先研究风险预算，而不是依赖更乐观的价格预测。');
    return {model,sections,exposure};
  }
  function researchMetrics(rows,histories={},now=Date.now()) {
    return peerRows(rows).map(row=>{
      const prepared=researchHistory(histories[row.target],now),points=prepared.points.slice(-61),changes=[...returns(points,now).values()];
      const avg=mean(changes),valid=prepared.adjusted&&changes.length===60;
      const last=points[points.length-1];
      const volatility=valid?{value:Math.sqrt(changes.reduce((s,x)=>s+(x-avg)**2,0)/59*252)*100,basis:'60个复权收盘收益区间，252日年化(%)',currency:'percent',as_of:last.time,period:points[0].time.slice(0,10)+'/'+last.time.slice(0,10)}:null;
      return {...row,volatility:row.volatility||volatility};
    });
  }
  function peerInsights(target,rows,positions=[],account={}) {
    rows=peerRows(rows);const own=rows.find(r=>r.target===target),profile=peerProfile(own?.industry);
    const label={pb:'PB',ps:'PS',net_margin:'净利率',roe:'ROE',npl:'不良贷款率',pe:'PE',dividend_yield:'股息率',leverage:'债务/权益',volatility:'年化波动率',revenue_growth:'收入增长率',fcf_margin:'自由现金流率',retention:'留存率',market_cap:'市值'};
    const lower=new Set(['pb','ps','pe','npl','leverage','volatility']),p=portfolio(positions,account,{},rows),sector=p.sector.find(r=>r.name===own?.sector);
    return peers(target,rows).map(row=>{
      const advantages=[],tradeoffs=[],unknown=[],context=[];
      for(const c of row.comparison){const name=label[c.metric];if(c.delta===null){unknown.push(name+'：'+c.reason);continue;}
        const a=row[c.metric],b=own[c.metric],detail=`${name} ${numberText(a.value)} 对 ${numberText(b.value)} ${a.currency==='percent'?'%':a.currency==='ratio'?'倍':a.currency}（候选 / 当前持仓；${a.as_of.slice(0,10)} / ${b.as_of.slice(0,10)}）`;
        if(c.metric==='market_cap'){context.push(detail+'。规模不是估值便宜、质量更好或更易成交的证明。');continue;}
        if(c.delta===0){context.push(detail+'，该项未形成差异。');continue;}
        (lower.has(c.metric)?c.delta<0:c.delta>0)?advantages.push(detail):tradeoffs.push(detail);
      }
      const status=advantages.length?(tradeoffs.length?'有相对优势，也有明确取舍':'值得进一步比较，尚不足以替换持仓'):'证据不足以支持优先替换';
      const held=positions.some(p=>key(p)===row.target);
      const impact=`${held?'当前组合已经持有该候选；继续增加会提高其仓位。':'该候选尚未在当前组合中。'}${p.complete&&finite(account.cash_base)&&p.coverage.unknownValue===0&&sector&&finite(sector.weight)?`相关行业目前占总资产 ${percentText(sector.weight)}，上限 ${account.sector_limit??50}%。`: '相关行业分类、现金或组合估值资料不全，暂停完整行业占比判断。'}同币种同业等金额替换不改变行业总敞口；用现金增持则会提高行业敞口。`;
      return {...row,status,advantages,tradeoffs,unknown,context,impact,conclusion:advantages.length?`建议把 ${row.name||row.target} 纳入 ${own.name||target} 的替代研究清单，先验证相对优势是否可持续，而不是直接买入。`:'暂不生成买入或替换结论，先补齐可比资料。',checks:profile.name==='银行'?'银行还要比较净息差、贷款结构、不良认定、拨备覆盖率、资本充足率及分红约束；高股息率可能来自股价下跌。':'核对增长持续性、现金流、负债、会计口径与主营重合度；便宜可能对应更差的盈利前景。',note:'股息率以股价为分母；派息率以同期间、同股份口径EPS为分母，不作越高越好的评分。两者缺失不互推，已宣告不等于已到账。低历史波动不等于低永久损失风险。'};
    });
  }
  function earningsOutlook(events,target,history={},now=Date.now()) {
    const future=upcoming((events||[]).map(e=>({...e,event_id:e.event_id||e.target+'|'+e.title+'|'+e.source_url})),target,now,90).filter(e=>e.status!=='cancelled'&&(e.kind==='earnings'||/财报|earnings|业绩/i.test(e.title||''))&&/^https:\/\//.test(e.source_url||''));
    const dates=new Set(future.map(e=>e.date)),prices=bars(history.points,now),last=prices[prices.length-1];
    return {price:last?.close??null,priceDate:last?.time||null,currency:history.currency||'',priceSource:history.source||'未取得',priceSourceUrl:history.source_url||'',
      events:future.map(e=>({...e,forecastText:finite(e.eps_forecast)?`每股盈利预测 ${numberText(e.eps_forecast,4)}；${e.eps_basis||'预测期间/口径未核验'}；${e.eps_currency||'币种未核验'}。来源预测，不是公司实际盈利。`:'当前未取得有效的每股盈利预测。',statusText:e.status==='confirmed'?'来源标记已确认，仍须复核公司公告':'来源预计，未视为公司已确认',asOf:e.data_as_of||e.observed_at})),
      note:(dates.size>1?'来源日期存在多个候选，可能是预测冲突或不同报告期，应逐条核对。':'')+'财报日期不等于盈利所属期间。缺少可比预期与实际结果时，不称超预期；单季EPS不自动乘四。',
      next:future[0]||null};
  }
  function valuationScenario(outlook,input={}) {
    const parse=x=>typeof x==='number'?x:typeof x==='string'&&x.trim()!==''?Number(x):NaN;
    const eps=parse(input.annual_eps),multiple=parse(input.multiple),price=outlook.price;
    const period=String(input.period||'').trim(),basis=String(input.basis||'').trim();
    if(!finite(price)||price<=0)throw new Error('缺少有效的未复权收盘价，暂不能反推');
    if(!['USD','CNY','HKD','KRW'].includes(outlook.currency))throw new Error('报价币种未核验，不能仅按上市市场推定盈利币种');
    if(!finite(eps)||eps<=0||!finite(multiple)||multiple<=0||multiple>1000)throw new Error('年度EPS须为正数，假设PE须大于0且不超过1000；亏损企业不使用此模型');
    if(!period||period.length>80||!basis||basis.length>500)throw new Error('请填写全年盈利期间及假设依据；单季预测不能代替全年EPS');
    if(/quarter|季度|单季|Q[1-4]/i.test(period))throw new Error('此模型只接受年度盈利假设，不能使用季度EPS');
    const required=price/multiple,impliedPE=price/eps,value=eps*multiple;
    const scenarios=[0.9,1,1.1].flatMap(e=>[0.8,1,1.2].map(m=>({eps:eps*e,multiple:multiple*m,value:eps*e*multiple*m,change:(eps*e*multiple*m/price-1)*100})));
    if(![required,impliedPE,value,(required/eps-1)*100,...scenarios.flatMap(r=>Object.values(r))].every(finite))throw new Error('输入数值超出可计算范围，请核对单位和数量级');
    const note=`研究假设，不是目标价或上涨概率。${period} 年度EPS假设 ${numberText(eps,4)} ${outlook.currency}/股，假设PE ${numberText(multiple)}倍；依据：${basis}。参考未复权收盘 ${numberText(price,4)} ${outlook.currency}（${outlook.priceDate}，${outlook.priceSource}）。在该PE下，当前价格需年度EPS ${numberText(required,4)}；较输入盈利假设 ${numberText((required/eps-1)*100)}%。输入EPS对应当前PE ${numberText(impliedPE)}倍，条件估值 ${numberText(value,4)} ${outlook.currency}。差值不是相对上一财年增长率，也不能证明已充分定价。`;
    return {required,impliedPE,value,gap:(required/eps-1)*100,change:(value/price-1)*100,scenarios,note,input:{annual_eps:eps,multiple,period,basis},price,priceDate:outlook.priceDate,currency:outlook.currency};
  }
  function consensusSnapshot(payload,target,observedAt) {
    const data=payload?.data,annual=data?.yearlyForecast;
    if(!/^US:[A-Z][A-Z0-9.-]*$/.test(target)||String(data?.symbol||'').toUpperCase()!==target.slice(3)||!Array.isArray(annual?.rows)||!annual.rows.length||annual.rows.length>8||annual.rows.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw new Error('未取得匹配标的的年度一致预期，保留旧快照');
    if(!annual.rows.some(r=>/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) 20\d{2}$/.test(r.fiscalEnd||'')&&(finite(r.consensusEPSForecast)||typeof r.consensusEPSForecast==='string'&&/^-?\d+(?:\.\d+)?$/.test(r.consensusEPSForecast.trim())&&finite(Number(r.consensusEPSForecast)))))throw new Error('年度一致预期没有有效期间与EPS，保留旧快照');
    return {target,annual,observed_at:observedAt,source:'Nasdaq',source_url:'https://www.nasdaq.com/market-activity/stocks/'+target.slice(3).toLowerCase()+'/earnings'};
  }
  function consensusModel(target,snapshot,outlook={},options={},now=Date.now()) {
    const result={rows:[],selected:null,grid:[],forwardPE:null,dispersion:null,reason:'尚未取得年度一致预期；更新研究数据后自动载入。',note:'',source:snapshot?.source||'',source_url:snapshot?.source_url||'',observed_at:snapshot?.observed_at||'',sourceAsOf:snapshot?.annual?.asOf||'来源未注明',unitNote:'来源未单列预测币种、GAAP/调整后及股份换算口径；不是公司指引，也不是已实现盈利。'};
    if(!target.startsWith('US:')){result.reason='自动年度一致预期目前覆盖美股；其他市场保留手动情景，不套用美股数据。';return result;}
    if(!snapshot||snapshot.target!==target||!Array.isArray(snapshot.annual?.rows))return result;
    const number=x=>finite(x)?x:typeof x==='string'&&/^-?\d+(?:\.\d+)?$/.test(x.trim())?Number(x):null;
    const count=x=>{const n=number(x);return Number.isInteger(n)&&n>=0?n:null;};
    const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    for(const raw of snapshot.annual.rows.slice(0,8)){
      if(!raw||typeof raw!=='object')continue;
      const match=/^([A-Z][a-z]{2}) (20\d{2})$/.exec(raw.fiscalEnd||''),month=match?months.indexOf(match[1]):-1;
      if(month<0)continue;
      const year=Number(match[2]),period=year+'-'+String(month+1).padStart(2,'0'),eps=number(raw.consensusEPSForecast),low=number(raw.lowEPSForecast),high=number(raw.highEPSForecast);
      const samples=count(raw.noOfEstimates),rangeValid=finite(eps)&&finite(low)&&finite(high)&&low<=eps&&eps<=high;
      result.rows.push({period,label:'财年截至 '+period,periodEnd:Date.UTC(year,month+1,1)-1,eps,low,high,samples,up:count(raw.up),down:count(raw.down),rangeValid});
    }
    result.rows.sort((a,b)=>a.period.localeCompare(b.period));
    const duplicate=new Set(result.rows.map(r=>r.period)).size!==result.rows.length;
    const selected=options.period?result.rows.find(r=>r.period===options.period):result.rows.find(r=>r.periodEnd>=now)||result.rows[result.rows.length-1];result.selected=selected||null;
    if(duplicate){result.reason='来源包含重复财年，暂停反推以免混合样本。';return result;}
    if(!selected){result.reason='未取得有效年度期间，或所选财年已不在新快照中。';return result;}
    if(finite(selected.eps)&&selected.eps!==0&&selected.rangeValid)result.dispersion=(selected.high-selected.low)/Math.abs(selected.eps)*100;
    const at=date(snapshot.observed_at),priceAt=date(outlook.priceDate);
    if(at===null||at>now||now-at>7*DAY){result.reason='一致预期快照超过7日或时间无效；保留展示，暂停自动反推。';return result;}
    const sourceAt=date(snapshot.annual.asOf);
    if(sourceAt!==null&&(sourceAt>now||now-sourceAt>7*DAY)){result.reason='来源注明的更新时间过期或来自未来；重新采集不能把旧预期变新。';return result;}
    if(!finite(selected.eps)||selected.samples===null||selected.samples<2){result.reason='有效预期少于2份或均值缺失，不能作为多样本一致预期基准。';return result;}
    if(selected.periodEnd<now){result.reason='该财年已经结束，保留来源预测展示，不作未来年度反推。';return result;}
    if(selected.eps<=0){result.reason='一致预期EPS为零或负数，不使用正PE反推模型。';return result;}
    if(!finite(outlook.price)||outlook.price<=0||priceAt===null||priceAt>now||now-priceAt>7*DAY){result.reason='未取得7日内有效未复权收盘价，暂停自动反推。';return result;}
    if(!['USD','CNY','HKD','KRW'].includes(outlook.currency)||options.confirmedUnit!==true){result.reason='先核对并确认预测EPS与报价币种、每股/每ADS口径一致；不自动假定美元。';return result;}
    const multiples=[10,15,20,25,30,40];
    result.forwardPE=outlook.price/selected.eps;
    result.grid=multiples.map(multiple=>({multiple,required:outlook.price/multiple,gap:(outlook.price/multiple/selected.eps-1)*100,value:selected.eps*multiple,change:(selected.eps*multiple/outlook.price-1)*100,low:selected.rangeValid&&selected.low>0?selected.low*multiple:null,high:selected.rangeValid&&selected.high>0?selected.high*multiple:null}));
    if(!finite(result.forwardPE)||result.grid.some(r=>Object.values(r).some(v=>v!==null&&!finite(v)))){result.grid=[];result.forwardPE=null;result.reason='数值超出计算范围，请核对来源单位。';return result;}
    result.reason='按已确认的相同币种和股份口径计算；PE为当前股价相对所选财年EPS的前瞻倍数假设，不是未来期末PE或分析师PE预测。'+(selected.rangeValid&&selected.low<=0?' 来源低端EPS非正，不计算其正PE估值。':'');
    result.note=`${target} ${selected.label}，Nasdaq年度一致预期EPS ${numberText(selected.eps,4)}，样本 ${selected.samples} 份；${selected.rangeValid?'来源最低/最高 '+numberText(selected.low,4)+' / '+numberText(selected.high,4):'来源高低区间不完整或无效'}。近4周上调/下调次数 ${selected.up??'未取得'} / ${selected.down??'未取得'}。采集于 ${snapshot.observed_at}，来源更新日 ${result.sourceAsOf}；${snapshot.source_url}。用户确认与报价 ${outlook.currency} 及上市股份口径一致，会计调整仍以来源EPS*口径为准。参考未复权收盘 ${numberText(outlook.price,4)}（${outlook.priceDate}），隐含预测PE ${numberText(result.forwardPE)}倍。PE 10/15/20/25/30/40是独立假设，反推EPS=股价/假设PE，预期差=反推EPS/一致预期EPS-1。高低值不是置信区间，预期差不能证明已充分定价，更不是上涨概率；不含股息、税费、汇率或成交成本。`;
    result.note+=' 所用PE是当前前瞻PE，不是未来期末PE；未做独立现金流折现，不能把结果当成未来可兑现价格。';
    return result;
  }
  const api={finite,pearson,bars,returns,portfolio,correlations,portfolioPerformance,lookthrough,retrospective,researchHistory,eventStudy,eventKey,rememberEvents,newsEventLeads,eventDraft,eventFollowup,peers,peerRows,peerProfile,peerAnalysis,dividendTerms,dividendDisclosure,dedupePosts,attention,sampleAttention,propagation,attentionPrice,attentionBacktest,ATTENTION_VERSION,validateImport,freeze,backtest,portfolioReport,researchMetrics,peerInsights,earningsOutlook,valuationScenario,consensusSnapshot,consensusModel,classificationRows,classificationName};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.STockPortfolioResearch=api;
})(typeof globalThis!=='undefined'?globalThis:this);
