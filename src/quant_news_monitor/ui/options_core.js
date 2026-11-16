(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.STockOptions=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const VERSION='options-learning-v1', MULTIPLIER=100;
  const defaults={spot:60,strike:55,strike2:65,days:30,iv:.8,contracts:1,fee:.65,rate:.04};
  const strategyIds=['long_call','long_put','cash_put','covered_call','bull_call_spread','bear_put_spread','protective_put','long_straddle'];
  const names={long_call:'买入 Call',long_put:'买入 Put',cash_put:'现金担保卖 Put',covered_call:'备兑 Call',bull_call_spread:'牛市 Call 价差',bear_put_spread:'熊市 Put 价差',protective_put:'保护性 Put',long_straddle:'买入跨式'};
  const settlementPolicy='卖 Put 到期价内接货，备兑 Call 到期价内交股；其他合约按净内在价值现金结算。忽略提前指派、股息、滑点及股票交易费。';
  const fail=message=>{throw new Error(message);};
  function number(value,label,min,max,integer=false){if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))fail(label+'超出有效范围');return value;}
  function normal(x){const t=1/(1+.2316419*Math.abs(x)),d=.3989422804014327*Math.exp(-x*x/2),p=1-d*t*(.319381530+t*(-.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))));return x>=0?p:1-p;}
  const intrinsic=(type,spot,strike)=>Math.max(type==='call'?spot-strike:strike-spot,0);
  function price(input){
    const {type,spot,strike,days,iv,rate=.04}=input;
    if(type!=='call'&&type!=='put')fail('期权类型无效');
    number(spot,'标的价格',0,1e6);number(strike,'行权价',.01,1e6);number(days,'剩余天数',0,730,true);number(iv,'波动率',0,5);number(rate,'无风险利率',0,1);
    if(days===0)return intrinsic(type,spot,strike);
    const t=days/365,discounted=strike*Math.exp(-rate*t);
    if(spot===0)return type==='call'?0:discounted;
    if(iv===0)return Math.max(type==='call'?spot-discounted:discounted-spot,0);
    // shortcut: 欧式、无股息 Black-Scholes 理论值；接入真实美式期权报价后替换估值，保留到期账务。
    const width=iv*Math.sqrt(t),d1=(Math.log(spot/strike)+(rate+iv*iv/2)*t)/width,d2=d1-width;
    return Math.max(0,type==='call'?spot*normal(d1)-discounted*normal(d2):discounted*normal(-d2)-spot*normal(-d1));
  }
  function strategy(id,input={}){
    if(!strategyIds.includes(id))fail('未知期权策略');
    const p={...defaults,...input};
    number(p.spot,'标的价格',0,1e6);number(p.strike,'行权价',.01,1e6);number(p.days,'到期天数',1,730,true);number(p.iv,'波动率',0,5);number(p.rate,'无风险利率',0,1);number(p.contracts,'合约张数',1,100,true);number(p.fee,'每张手续费',0,100);
    const legs=[],add=(type,strike,sign)=>legs.push({type,strike,quantity:sign*p.contracts,premium:price({...p,type,strike})});
    if(['long_call','covered_call','bull_call_spread','long_straddle'].includes(id))add('call',p.strike,id==='covered_call'?-1:1);
    if(['long_put','cash_put','bear_put_spread','protective_put','long_straddle'].includes(id))add('put',p.strike,id==='cash_put'?-1:1);
    if(id==='bull_call_spread'||id==='bear_put_spread'){
      number(p.strike2,'第二行权价',.01,1e6);
      if(id==='bull_call_spread'&&p.strike2<=p.strike)fail('牛市 Call 价差的卖出行权价必须更高');
      if(id==='bear_put_spread'&&p.strike2>=p.strike)fail('熊市 Put 价差的卖出行权价必须更低');
      add(id==='bull_call_spread'?'call':'put',p.strike2,-1);
    }
    return {id,params:p,legs,shares:['covered_call','protective_put'].includes(id)?MULTIPLIER*p.contracts:0};
  }
  const openingFees=s=>s.legs.reduce((v,l)=>v+Math.abs(l.quantity)*s.params.fee,0);
  const premiumCash=s=>-s.legs.reduce((v,l)=>v+l.quantity*l.premium*MULTIPLIER,0);
  function expirationPnl(s,spot){number(spot,'到期标的价格',0,1e8);return s.shares*(spot-s.params.spot)+s.legs.reduce((v,l)=>v+l.quantity*MULTIPLIER*(intrinsic(l.type,spot,l.strike)-l.premium),0)-openingFees(s);}
  function analyze(id,input){
    const s=strategy(id,input),p=s.params,knots=[...new Set([0,...s.legs.map(l=>l.strike)])].sort((a,b)=>a-b),values=knots.map(x=>expirationPnl(s,x));
    const tail=s.shares+s.legs.filter(l=>l.type==='call').reduce((v,l)=>v+l.quantity*MULTIPLIER,0),last=knots[knots.length-1],end=values[values.length-1],breakevens=[];
    const add=x=>{if(x>=0&&!breakevens.some(v=>Math.abs(v-x)<1e-7))breakevens.push(x);};
    for(let i=0;i<knots.length;i++){
      if(Math.abs(values[i])<1e-8)add(knots[i]);
      if(i&&values[i]*values[i-1]<0)add(knots[i-1]-values[i-1]*(knots[i]-knots[i-1])/(values[i]-values[i-1]));
    }
    if(tail&&-end/tail>0)add(last-end/tail);
    const netPremium=premiumCash(s),fees=openingFees(s),capitalRequired=id==='cash_put'?p.strike*MULTIPLIER*p.contracts+fees:Math.max(0,s.shares*p.spot-netPremium+fees);
    const high=Math.max(p.spot*2,last*1.5,...breakevens.map(x=>x*1.2),1),xs=[...new Set([...Array.from({length:61},(_,i)=>high*i/60),...knots,...breakevens])].sort((a,b)=>a-b);
    return {strategy:s,curve:xs.map(spot=>({spot,pnl:expirationPnl(s,spot)})),maxProfit:tail>0?Infinity:Math.max(...values),maxLoss:tail<0?Infinity:Math.max(0,-Math.min(...values)),breakevens:breakevens.sort((a,b)=>a-b),capitalRequired,netPremium,fees};
  }
  function createAccount(capital){number(capital,'初始资金',1,1e10);return {initialCapital:capital,cash:capital,shares:0,stockCost:0,position:null,spot:defaults.spot,days:0,iv:defaults.iv,rate:defaults.rate,fees:0,realized:0,log:[]};}
  function mark(a){
    const s=a.position,optionValue=s?s.legs.reduce((v,l)=>v+l.quantity*MULTIPLIER*price({type:l.type,strike:l.strike,spot:a.spot,days:a.days,iv:a.iv,rate:a.rate}),0):0;
    const reserved=s&&s.id==='cash_put'?s.params.strike*MULTIPLIER*s.params.contracts:0,stockValue=a.shares*a.spot,equity=a.cash+stockValue+optionValue;
    return {cash:a.cash,reserved,available:a.cash-reserved,optionValue,stockValue,equity,pnl:equity-a.initialCapital,fees:a.fees,realized:a.realized};
  }
  function record(a,event,message){if(a.cash<-.000001||!Number.isFinite(a.cash))fail('现金不足，不能完成操作');if(Math.abs(a.cash)<1e-8)a.cash=0;const m=mark(a);a.log.push({event,message,cash:a.cash,equity:m.equity,pnl:m.pnl,spot:a.spot,days:a.days});return a;}
  function openTrade(account,id,input){
    if(account.position)fail('请先平仓或结算现有期权合约');
    const a=structuredClone(account),s=strategy(id,input),p=s.params,newShares=Math.max(0,s.shares-a.shares),fees=openingFees(s),premium=premiumCash(s);
    if(a.shares&&Math.abs(a.spot-p.spot)>1e-8)fail('已有股票须先更新当前标的价格，再开仓');
    const required=id==='cash_put'?p.strike*MULTIPLIER*p.contracts+fees:newShares*p.spot-premium+fees;
    if(a.cash+1e-8<required)fail('可用现金不足；卖 Put 须准备完整接货资金');
    a.cash-=newShares*p.spot;a.shares+=newShares;a.stockCost+=newShares*p.spot;
    a.cash+=premium-fees;a.fees+=fees;a.position=s;a.spot=p.spot;a.days=p.days;a.iv=p.iv;a.rate=p.rate;
    return record(a,'open',`开仓 ${names[id]}：${p.contracts} 张；${premium>=0?'收入':'支出'}权利金 ${Math.abs(premium).toFixed(2)}，手续费 ${fees.toFixed(2)}${newShares?`；同时买入 ${newShares} 股`:''}。`);
  }
  function advance(account,input){
    const a=structuredClone(account),{spot,days,iv}=input;
    number(spot,'标的价格',0,1e6);number(days,'剩余天数',0,730,true);number(iv,'波动率',0,5);
    if(days>a.days)fail('推进行情不能增加剩余天数；延长期限请使用展期');
    a.spot=spot;a.days=days;a.iv=iv;
    return record(a,'advance',`行情更新：标的 ${spot.toFixed(2)}，剩余 ${days} 天，波动率 ${(iv*100).toFixed(1)}%。`);
  }
  function closeTrade(account){
    if(!account.position)fail('没有可平仓的期权合约');
    const a=structuredClone(account),s=a.position,value=mark(a).optionValue,fees=openingFees(s);
    a.cash+=value-fees;a.fees+=fees;a.realized+=premiumCash(s)+value-openingFees(s)-fees;a.position=null;
    return record(a,'close',`平仓 ${names[s.id]}：${value>=0?'收入':'支出'} ${Math.abs(value).toFixed(2)}，手续费 ${fees.toFixed(2)}；股票继续持有。`);
  }
  function settle(account){
    if(!account.position)fail('没有待结算的期权合约');
    if(account.days!==0)fail('请先推进至到期日');
    const a=structuredClone(account),s=a.position,p=s.params,shares=p.contracts*MULTIPLIER;
    let message='';a.realized+=premiumCash(s)-openingFees(s);
    if(s.id==='cash_put'&&a.spot<p.strike){
      a.cash-=p.strike*shares;a.shares+=shares;a.stockCost+=p.strike*shares;message=`Put 到期价内：按 ${p.strike} 接货 ${shares} 股，支付 ${(p.strike*shares).toFixed(2)}。账面股票亏损仍保留。`;
    }else if(s.id==='covered_call'&&a.spot>p.strike){
      if(a.shares<shares)fail('备兑股票数量不足');
      const cost=a.stockCost*shares/a.shares;
      a.cash+=p.strike*shares;a.shares-=shares;a.stockCost-=cost;a.realized+=p.strike*shares-cost;message=`Call 到期价内：按 ${p.strike} 交付 ${shares} 股，上方涨幅不再属于这部分持仓。`;
    }else{
      const value=mark(a).optionValue;a.cash+=value;a.realized+=value;
      message=['cash_put','covered_call'].includes(s.id)?'期权到期无内在价值，合约解除；股票持仓保持。':`教学结算：按净内在价值 ${value.toFixed(2)} 现金结算期权，不模拟逐腿行权；股票持仓保持。`;
    }
    a.position=null;return record(a,'settle',message);
  }
  function rollTrade(account,input){
    const s=account.position;if(!s||!['cash_put','covered_call'].includes(s.id))fail('仅现金担保 Put 和备兑 Call 可在此展期');
    if(account.days===0)fail('已到期合约须先结算，不能展期');
    number(input.days,'新的到期天数',1,730,true);if(input.days<=account.days)fail('展期的新到期天数必须更长');
    const p={...s.params,spot:account.spot,strike:input.strike,days:input.days,iv:input.iv},closed=closeTrade(account),a=openTrade(closed,s.id,p);
    return record(a,'roll','展期 = 平掉旧合约 + 开立新合约；旧合约已实现盈亏继续计入账户。');
  }
  function sellShares(account){
    if(account.position)fail('请先平仓或结算期权合约，再卖出股票');
    if(!account.shares)fail('没有可卖出的股票');
    const a=structuredClone(account),shares=a.shares,proceeds=shares*a.spot;
    a.cash+=proceeds;a.realized+=proceeds-a.stockCost;a.shares=0;a.stockCost=0;
    return record(a,'sell_shares',`按 ${a.spot.toFixed(2)} 卖出剩余 ${shares} 股；教学模型未计股票交易费。`);
  }
  return {VERSION,MULTIPLIER,defaults,strategyIds,names,settlementPolicy,price,strategy,expirationPnl,analyze,createAccount,mark,openTrade,advance,closeTrade,settle,rollTrade,sellShares};
});
