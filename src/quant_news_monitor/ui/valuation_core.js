(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.STockValuation=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='stock-valuation-v1';
  const methods={dcf:'现金流折现 DCF',ddm:'股息折现 DDM',multiples:'相对估值',reverse:'市场预期反推',return:'投资回报 / IRR',capital:'必要回报率 / WACC'};
  const fail=message=>{throw Error(message);};
  function number(value,label,min=-1e15,max=1e15){if(value==null||value===''||typeof value==='boolean'||!['number','string'].includes(typeof value)||typeof value==='string'&&!value.trim())fail('请填写'+label);const n=Number(value);if(!Number.isFinite(n)||n<min||n>max)fail(label+'超出有效范围');return n;}
  const positive=(v,label)=>number(v,label,Number.MIN_VALUE);
  const rate=(v,label,min=-99.99,max=1000)=>number(v,label,min,max)/100;
  function years(value){const n=number(value,'预测年数',1,30);if(!Number.isInteger(n))fail('预测年数须为整数');return n;}
  const choice=(v,values,label)=>values.includes(v)?v:fail(label+'无效');
  function solveIncreasing(fn,target,lo,hi){
    if(fn(lo)>target||fn(hi)<target)return null;
    for(let i=0;i<160;i++){const mid=(lo+hi)/2;if(fn(mid)>target)hi=mid;else lo=mid;}
    return (lo+hi)/2;
  }
  function irr(price,flows){
    positive(price,'买入价格');if(!Array.isArray(flows)||!flows.length||flows.length>30)fail('现金流须为1至30个等间隔年度');
    flows.forEach(v=>number(v,'未来每股现金流',0));
    if(flows.every(v=>v===0))return null;
    const pv=r=>flows.reduce((s,v,i)=>s+v/(1+r)**(i+1),0);
    let lo=-.999999999,hi=1;
    while(pv(hi)>price&&hi<1e8)hi*=2;
    if(pv(lo)<price||pv(hi)>price)return null;
    for(let i=0;i<180;i++){const mid=(lo+hi)/2;if(pv(mid)>price)lo=mid;else hi=mid;}
    return (lo+hi)/2;
  }
  function projection(base,g,n,discount,terminalGrowth,timing='current',explicit=null){
    if(discount<=terminalGrowth)fail('折现率必须严格大于永续增长率');
    let cash=base;
    const rows=Array.from({length:n},(_,i)=>{
      cash=explicit?explicit[i]:base*(1+g)**(timing==='next'?i:i+1);
      return {year:i+1,cash,discount_factor:1/(1+discount)**(i+1),pv:cash/(1+discount)**(i+1)};
    });
    if(cash<=0)fail('终年现金流必须为正，无法用负现金流进入永续增长');
    const terminal=cash*(1+terminalGrowth)/(discount-terminalGrowth),terminalPV=terminal/(1+discount)**n;
    return {rows,terminal,terminalPV,operating:rows.reduce((s,r)=>s+r.pv,0)+terminalPV};
  }
  function bridge(x){return {shares:positive(x.shares,'稀释后股数（百万股）'),debt:number(x.debt,'有息负债（百万）',0),cash:number(x.cash,'非经营现金（百万）',0),claims:number(x.claims,'其他优先索偿（百万）',0)};}
  function baseResult(){return {value:null,metrics:[],rows:[],warnings:[],formula:'',sensitivity:null};}
  function metric(r,label,value,unit='',help=''){r.metrics.push({label,value,unit,help});}
  function calculate(model,x,{sensitivity=true}={}){
    choice(model,Object.keys(methods),'估值方法');if(!x||typeof x!=='object'||Array.isArray(x))fail('估值输入无效');
    const r=baseResult(),price=x.price==null||x.price===''?null:positive(x.price,'参考股价');
    if(['dcf','ddm','reverse'].includes(model)){
      const n=years(x.years),d=rate(x.discount,'折现率',0,100),t=rate(x.terminal_growth,'永续增长率',-99,100);
      if(d<=t)fail('折现率必须严格大于永续增长率');
      const isDividend=model==='ddm',basis=isDividend?'fcfe':choice(x.basis,['fcff','fcfe'],'现金流口径');
      const b=isDividend?{shares:1,debt:0,cash:0,claims:0}:basis==='fcff'?bridge(x):{shares:positive(x.shares,'稀释后股数（百万股）'),debt:0,cash:0,claims:0};
      if(model==='dcf')choice(x.flow_mode,['growth','explicit'],'现金流预测方式');
      const base=model==='dcf'&&x.flow_mode==='explicit'?0:number(x.cashflow,isDividend?'基期每股股息':'基期现金流（百万）');
      if(model==='reverse'){
        if(price===null)fail('反推需要参考股价');if(base<=0)fail('现金流须为正才能识别隐含增长；亏损期请使用逐年现金流DCF');
        const timing=choice(x.cash_timing,['current','next'],'现金流时点'),target=price*b.shares+b.debt+b.claims-b.cash;
        if(target<=0)fail('反推对应价值必须为正，请核对净现金和股价');
        const g=timing==='next'?d-base/target:(d*target-base)/(target+base);
        const fn=growth=>projection(base,growth,n,d,t,timing).operating;
        const growth=timing==='next'&&n===1?null:solveIncreasing(fn,target,-.999,10);
        metric(r,'单阶段隐含永续增速',g*100,'%','仅在所选现金流与折现口径下成立，不是市场一致预期。');
        metric(r,'两阶段隐含前期增速',growth===null?null:growth*100,'%','前N年增长，之后转入设定的永续增速；求解范围为-99.9%至1000%。');
        metric(r,basis==='fcff'?'反推企业价值':'反推股权价值',target,'百万');
        metric(r,'现金流收益率',base/target*100,'%');
        if(growth===null)r.warnings.push(timing==='next'&&n===1?'下一期现金流已给定且只预测1年，前期增长无法识别。':'在设定增长求解范围内没有解，不补成0。');
        else {const p=projection(base,growth,n,d,t,timing);r.rows=p.rows;metric(r,'反算价值差',p.operating-target,'百万');}
        r.formula=timing==='next'?'已知CF1：g = r - CF1 / V；两阶段按CF1 × (1+g1)^(t-1)折现。':'已知CF0：g = (r × V - CF0) / (V + CF0)；两阶段按CF0 × (1+g1)^t折现。';
        r.warnings.push('沿用原工作簿的反推思路，但不把TTM现金流误当下一期现金流；不沿用“20倍以上就失效”等机械买卖判断。');
      }else{
        let explicit=null,g=0;
        if(!isDividend&&x.flow_mode==='explicit'){
          if(!Array.isArray(x.flows)||x.flows.length!==n)fail('逐年现金流数量必须与预测年数一致');
          explicit=x.flows.map(v=>number(v,'年度现金流（百万）'));
        }else{if(base<=0)fail('比例增长模式的基期现金流须为正；亏损转盈请改用逐年现金流');g=rate(x.growth,'预测增长率');}
        const p=projection(base,g,n,d,t,'current',explicit),equity=p.operating-b.debt-b.claims+b.cash;
        r.value=equity/b.shares;r.rows=p.rows;
        metric(r,'每股条件估值',r.value,'/股');
        if(!isDividend){metric(r,basis==='fcff'?'经营企业价值':'股权价值（FCFE）',p.operating,'百万');if(basis==='fcff')metric(r,'扣债加现金后股权价值',equity,'百万');}
        metric(r,'终值现值',p.terminalPV,isDividend?'/股':'百万');
        metric(r,'终值占模型价值',p.operating>0?p.terminalPV/p.operating*100:null,'%','分母为折现现金流与终值总额；前期现金流为负时可能超过100%。');
        if(p.operating>0&&p.terminalPV/p.operating>.75)r.warnings.push('超过75%的模型价值来自终值，对长期增长和折现率较敏感；该提示不构成投资评级。');
        if(equity<=0)r.warnings.push('股权剩余价值非正：这是模型残余值，不是可交易的负股价。请复核债务与经营假设。');
        r.formula=isDividend?'V = Σ DPS_t / (1+Ke)^t + DPS_N × (1+gT) / (Ke-gT) / (1+Ke)^N':basis==='fcff'?'EV = Σ FCFF_t / (1+WACC)^t + TV现值；股权价值 = EV - 有息负债 - 其他索偿 + 非经营现金；每股价值 = 股权价值 / 稀释股数':'股权价值 = Σ FCFE_t / (1+Ke)^t + TV现值；每股价值 = 股权价值 / 稀释股数；不再次扣净债务。';
        if(sensitivity)r.sensitivity={rates:[-2,-1,0,1,2].map(v=>x.discount*1+v),growths:[-1,-.5,0,.5,1].map(v=>x.terminal_growth*1+v),values:[]};
        if(r.sensitivity)r.sensitivity.values=r.sensitivity.rates.map(discount=>r.sensitivity.growths.map(terminal_growth=>{try{return calculate(model,{...x,discount,terminal_growth},{sensitivity:false}).value;}catch{return null;}}));
      }
      r.warnings.push(isDividend?'DDM适合可持续派息的股权，股息率不等于派息率；不派息公司不宜直接套用。':basis==='fcff'?'FCFF必须配WACC；经营现金流减资本开支不一定等于FCFF，请核对利息及税后调整。':'FCFE必须配股权成本Ke；已反映债务融资影响，不得重复扣债。');
    }else if(model==='multiples'){
      const kind=choice(x.multiple_type,['pe','pb','ps','ev_ebitda'],'倍数类型'),multiple=positive(x.multiple,'估值倍数'),measure=positive(x.measure,'盈利、净资产或收入指标');
      if(['pe','pb'].includes(kind))r.value=measure*multiple;
      else {const b=kind==='ev_ebitda'?bridge(x):{shares:positive(x.shares,'稀释后股数（百万股）'),debt:0,cash:0,claims:0};const amount=measure*multiple;r.value=(amount-b.debt-b.claims+b.cash)/b.shares;metric(r,kind==='ev_ebitda'?'企业价值':'股权价值',amount,'百万');}
      metric(r,'每股条件估值',r.value,'/股');metric(r,'采用倍数',multiple,'倍');
      r.formula={pe:'每股价值 = 同口径年度EPS × PE',pb:'每股价值 = 每股账面净资产 × PB',ps:'股权价值 = 年度收入 × P/S；每股价值 = 股权价值 / 稀释股数',ev_ebitda:'EV = 年度EBITDA × EV/EBITDA；每股价值 = (EV - 债务 - 其他索偿 + 非经营现金) / 股数'}[kind];
      r.warnings.push('同业倍数需统一财年/TTM/预期、会计口径、币种和股份类别；本模块不会把手填倍数称作一致预期。');
      if(r.value<=0)r.warnings.push('扣除债务后股权残余值非正，不解释为可交易的负股价。');
    }else if(model==='return'){
      if(price===null)fail('投资回报需要买入价格');
      const n=years(x.years),eps=positive(x.eps,'基期年度EPS'),g=rate(x.growth,'EPS增长率'),pe=positive(x.exit_pe,'退出PE'),d=rate(x.discount,'必要回报率',0,100),dividend=number(x.dividend,'每年每股现金股息',0);
      const terminalEPS=eps*(1+g)**n,terminal=terminalEPS*pe,flows=Array(n).fill(dividend);flows[n-1]+=terminal;
      const dividendPV=Array.from({length:n},(_,i)=>dividend/(1+d)**(i+1)).reduce((a,b)=>a+b,0);
      r.value=terminal/(1+d)**n+dividendPV;
      const internal=irr(price,flows),required=price>dividendPV?((price-dividendPV)*(1+d)**n/(eps*pe))**(1/n)-1:null;
      metric(r,'假设现金流的年化IRR',internal===null?null:internal*100,'%','假定每年末等间隔收款和终年卖出，不是保证收益。');
      metric(r,'必要回报率门槛',d*100,'%','手填的机会成本或股权成本，不是模型预测。');
      metric(r,'满足门槛所需EPS增速',required===null?null:required*100,'%');
      metric(r,'按门槛折现的每股价值',r.value,'/股');metric(r,'假设退出价格',terminal,'/股');
      r.rows=flows.map((cash,i)=>({year:i+1,cash,discount_factor:1/(1+d)**(i+1),pv:cash/(1+d)**(i+1)}));
      r.formula='P0 = Σ 股息_t / (1+IRR)^t + [EPS0 × (1+g)^N × 退出PE] / (1+IRR)^N；必要回报率用于折现门槛，IRR由价格反求。';
      r.warnings.push('固定年度股息、年末收款与退出PE均为假设；未扣个人税费、交易费用或汇率影响。');
      if(required===null)r.warnings.push('股息现值已覆盖买入价，正退出价值下的EPS增长反推不适用。');
    }else{
      const rf=rate(x.rf,'无风险利率',-10,100),erp=rate(x.erp,'股权风险溢价',0,100),beta=number(x.beta,'Beta',-5,10),kd=rate(x.kd,'税前债务成本',0,100),tax=rate(x.tax,'有效利息税盾税率',0,100),weight=rate(x.debt_weight,'债务市场价值权重',0,100);
      const ke=rf+beta*erp,wacc=ke*(1-weight)+kd*(1-tax)*weight;
      metric(r,'股权必要回报率 Ke',ke*100,'%','CAPM输出是要求回报，不是实际或预测回报。');metric(r,'加权资本成本 WACC',wacc*100,'%','FCFF折现率；权益与债务权重按市场价值，未计优先股等其他资本。');metric(r,'税后债务成本',kd*(1-tax)*100,'%');
      r.formula='Ke = Rf + Beta × ERP；WACC = Ke × E/(D+E) + Kd × (1-T) × D/(D+E)';
      r.warnings.push('期限与币种要匹配；税盾只有可抵扣时才成立，亏损公司可将税盾税率设为0。Beta和风险溢价是输入估计，不是自动验证的市场事实。');
      if(ke<=0||wacc<=0)r.warnings.push('计算出的资本成本非正，不能直接作为通常正增长永续模型的折现率。');
    }
    if(r.value!==null&&price!==null&&r.value>0){metric(r,'估值相对参考价差', (r.value/price-1)*100,'%','(估值/股价-1)，不是上涨概率。');metric(r,'相对估值的安全边际',(1-price/r.value)*100,'%','(估值-股价)/估值，分母与价格差不同；取决于假设可靠性。');}
    const values=[r.value,...r.metrics.map(m=>m.value),...r.rows.flatMap(row=>[row.cash,row.pv,row.discount_factor])];
    if(values.some(v=>v!==null&&!Number.isFinite(v)))fail('计算超出数值范围，请降低增长或年数');
    return {...r,model,engine:VERSION};
  }
  function snapshot(model,meta,inputs){
    calculate(model,inputs);
    if(!meta||!['USD','CNY','HKD','KRW'].includes(meta.currency))fail('请选择币种');
    for(const key of ['name','target','data_as_of','price_as_of','source_url','notes'])if(typeof(meta[key]??'')!=='string'||(meta[key]||'').length>(key==='notes'?4000:key==='source_url'?2048:120))fail('研究资料格式无效');
    if(!meta.name?.trim())fail('请填写研究名称');
    if(!/^(US|CN|HK|KR):[A-Z0-9.^=_-]{1,24}$/.test(meta.target||''))fail('请填写市场:代码，例如 US:AAPL');
    for(const key of ['data_as_of','price_as_of'])if(meta[key]&&(!/^\d{4}-\d{2}-\d{2}$/.test(meta[key])||!Number.isFinite(Date.parse(meta[key]))||new Date(meta[key]).toISOString().slice(0,10)!==meta[key]))fail('资料日期无效');
    if(meta.source_url){const u=new URL(meta.source_url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)fail('来源须为不含账号密码的HTTP(S)链接');}
    return JSON.parse(JSON.stringify({engine:VERSION,model,meta,inputs}));
  }
  return {VERSION,methods,calculate,irr,snapshot};
});
