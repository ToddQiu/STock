"""Predeclared intraday hypotheses. Only completed bars at the signal time are used."""

from datetime import datetime
import math
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

from .short_term import SESSIONS, ZONES

STRATEGIES = {
    'vwap_reversion': {'name':'VWAP 回归','principle':'价格明显偏离当日成交量加权均价，且开始向均价回转时，检验回归假设。不是跌得多就买。',
        'rule':'当日完整分钟VWAP；偏离超过0.15%与1.5倍ATR占比中的较大值，最新收盘向VWAP靠拢。30分钟内只估计回归一半偏离。',
        'risk':'单边趋势可能持续远离VWAP；缺少开盘以来的价格或成交量时不计算。', 'cap_minutes':30},
    'opening_breakout': {'name':'开盘突破','principle':'先固定开盘区间，再看资金是否带量突破边界，检验早盘方向能否延续。',
        'rule':'开盘前15分钟高低点固定；20分钟量能预热后、开盘后120分钟内，收盘从区间内跨越边界0.1ATR且量比至少1.5。',
        'risk':'震荡盘容易反复穿越；集合竞价不混入分钟量，不把后续全天高低点用作开盘区间。', 'cap_minutes':30},
    'trend_pullback': {'name':'趋势回踩','principle':'已有趋势中等待回撤靠近短均线，再等价格重新转向，而不是追逐刚拉起的价格。',
        'rule':'61根连续分钟；EMA20/60同向，前5分钟触及EMA20且未明显破坏EMA60；最新收盘重回EMA20并越过前根高/低点，量比至少1。',
        'risk':'回踩可能变成趋势反转；参考失效位是回踩极值，不自动执行止损。', 'cap_minutes':30},
    'squeeze_breakout': {'name':'波动收缩突破','principle':'先确认波动压缩，再确认放量突破；收缩本身没有上涨或下跌方向。',
        'rule':'前一根20分钟布林带宽位于此前41个带宽的低25%区域；最新收盘突破此前20根高/低及布林带，量比至少1.5。',
        'risk':'小波动不保证随后大涨；必须等待方向确认，低流动性也可能制造假收缩。', 'cap_minutes':30},
    'relative_strength': {'name':'相对强弱','principle':'比较标的与同市场基准在相同20分钟内的涨跌，区分主动强势与跟随大盘。不是RSI。',
        'rule':'21个分钟时间戳逐一对齐；超额对数收益至少±0.2%，且标的本身涨跌方向及相对EMA20位置一致。20分钟内估计延续一半超额收益，幅度限制在约2ATR内。',
        'risk':'跑赢大盘仍可能亏钱；基准不同结果可能不同，缺少同步分钟时不能代用旧报价。', 'cap_minutes':20},
    'false_breakout': {'name':'假突破反转','principle':'价格突破既有区间后又收回区间内，检验突破失败后的反向移动。',
        'rule':'先固定20分钟区间，随后3分钟出现收盘突破，最新收盘带量收回区间且方向反转；量比至少1。上下两边都突破时不判断。',
        'risk':'刚收回区间也可能再次突破；参考失效位为这次突破的极值，不是保证有效的止损位。', 'cap_minutes':30},
}
DEFAULT_BENCHMARKS = {'US':'SPY','CN':'000300.SS','HK':'^HSI','KR':'^KS11'}


def _continuous(rows, count):
    if len(rows)<count: raise ValueError(f'需要{count}根连续完整分钟K线')
    recent=rows[-count:]
    if any(b['timestamp']-a['timestamp']!=60 or b['date']!=a['date'] for a,b in zip(recent,recent[1:])):
        raise ValueError('计算窗口跨日、午休或存在分钟缺口')
    return recent


def _ema(values, period):
    value=values[0]
    result=[value]
    for price in values[1:]:
        value += 2/(period+1)*(price-value)
        result.append(value)
    return result


def _volume_ratio(rows):
    window=_continuous(rows,21)
    if any(r.get('volume') is None or r['volume']<0 for r in window):
        raise ValueError('最近21分钟成交量不完整')
    average=mean(r['volume'] for r in window[:-1])
    if average<=0: raise ValueError('此前20分钟无有效成交量')
    return window[-1]['volume']/average


def _opening_rows(day, market, count=None):
    zone=ZoneInfo(ZONES[market])
    midnight=datetime.combine(datetime.fromisoformat(day[-1]['date']).date(),datetime.min.time(),zone)
    end=day[-1]['timestamp']
    start=int(midnight.timestamp())
    expected=[start+m*60 for a,b in SESSIONS[market] for m in range(a,b) if start+m*60<=end]
    expected=expected[:count] if count else expected
    if count and len(expected)<count: raise ValueError(f'开盘区间尚未完成{count}分钟')
    by_stamp={r['timestamp']:r for r in day}
    if not expected or any(t not in by_stamp for t in expected): raise ValueError('缺少当日开盘起的完整分钟数据')
    return [by_stamp[t] for t in expected]


def signal(rows, model, *, market='US', benchmark=None):
    """Neutral is a valid no-trigger observation; unavailable is never scored as neutral."""
    if model not in STRATEGIES: raise ValueError('未知策略')
    result={'id':model,**STRATEGIES[model],'available':False,'direction':0,'log_move':None,
            'reason':'暂无分钟数据','facts':{},'invalidation':None}
    if not rows: return result
    try:
        count=61 if model in ('trend_pullback','squeeze_breakout') else 24 if model=='false_breakout' else 21
        recent=_continuous(rows,count)
        prices=[r['close'] for r in recent]
        last,previous=recent[-1],recent[-2]
        close=last['close']
        atr=mean(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(recent[-15:-1],recent[-14:]))
        if atr<=0: raise ValueError('分钟波幅为零，无法估算策略幅度')
        direction,move,invalid=0,0.,None
        facts={'ATR14':atr}
        ratio=_volume_ratio(recent) if model not in ('relative_strength','vwap_reversion') else None
        if ratio is not None: facts['量比']=ratio
        day=[r for r in rows if r['date']==last['date']]
        if model=='vwap_reversion':
            day=_opening_rows(day,market)
            if any(r.get('volume') is None or r['volume']<0 for r in day) or sum(r['volume'] for r in day)<=0:
                raise ValueError('当日成交量不完整，不能用局部均价冒充VWAP')
            vwap=lambda rs:sum((r['high']+r['low']+r['close'])/3*r['volume'] for r in rs)/sum(r['volume'] for r in rs)
            if sum(r['volume'] for r in day[:-1])<=0: raise ValueError('此前没有足够成交量')
            level,prior=vwap(day),vwap(day[:-1])
            gap=math.log(level/close)
            threshold=max(.0015,1.5*atr/close)
            if last['volume']>0 and abs(gap)>=threshold and abs(gap)<abs(math.log(prior/previous['close'])):
                if gap>0 and close>previous['close']: direction=1
                if gap<0 and close<previous['close']: direction=-1
            move=gap*.5 if direction else 0.
            facts.update({'VWAP':level,'偏离%':(close/level-1)*100,'触发偏离阈值%':threshold*100})
            invalid=min(r['low'] for r in recent[-5:]) if direction>0 else max(r['high'] for r in recent[-5:]) if direction<0 else None
        elif model=='opening_breakout':
            opening=_opening_rows(day,market,15)
            upper,lower=max(r['high'] for r in opening),min(r['low'] for r in opening)
            elapsed=(last['timestamp']-opening[0]['timestamp'])/60
            if 20<=elapsed<120 and ratio>=1.5:
                if previous['close']<=upper and close>upper+.1*atr: direction=1;invalid=upper
                if previous['close']>=lower and close<lower-.1*atr: direction=-1;invalid=lower
            move=direction*min(2*atr,max(atr,(upper-lower)*.5))/close
            facts.update({'开盘15分高':upper,'开盘15分低':lower,'距开盘分钟':elapsed})
        elif model=='trend_pullback':
            fast,slow=_ema(prices,20),_ema(prices,60)
            pull=recent[-6:-1]
            up=fast[-1]>slow[-1] and fast[-1]>fast[-6]
            down=fast[-1]<slow[-1] and fast[-1]<fast[-6]
            if ratio>=1:
                if up and any(r['low']<=f+.25*atr for r,f in zip(pull,fast[-6:-1])) and min(r['low'] for r in pull)>=slow[-2]-.5*atr and close>fast[-1]+.1*atr and close>previous['high']:
                    direction=1;invalid=min(r['low'] for r in pull)
                elif down and any(r['high']>=f-.25*atr for r,f in zip(pull,fast[-6:-1])) and max(r['high'] for r in pull)<=slow[-2]+.5*atr and close<fast[-1]-.1*atr and close<previous['low']:
                    direction=-1;invalid=max(r['high'] for r in pull)
            move=direction*1.5*atr/close
            facts.update({'EMA20':fast[-1],'EMA60':slow[-1]})
        elif model=='squeeze_breakout':
            # Every compression statistic ends before the breakout bar.
            widths=[4*pstdev(prices[i-19:i+1])/mean(prices[i-19:i+1]) for i in range(19,len(prices)-1)]
            cutoff=sorted(widths)[int((len(widths)-1)*.25)]
            base=recent[-21:-1]
            average,sd=mean(r['close'] for r in base),pstdev(r['close'] for r in base)
            upper=max(max(r['high'] for r in base),average+2*sd)
            lower=min(min(r['low'] for r in base),average-2*sd)
            if widths[-1]<=cutoff and ratio>=1.5:
                if close>upper+.1*atr: direction=1;invalid=upper
                if close<lower-.1*atr: direction=-1;invalid=lower
            move=direction*min(2*atr,max(1.5*atr,(upper-lower)*.5))/close
            facts.update({'前根带宽%':widths[-1]*100,'带宽低25%阈值':cutoff*100,'突破上界':upper,'突破下界':lower})
        elif model=='relative_strength':
            by_stamp={r['timestamp']:r for r in benchmark or []}
            aligned=[by_stamp.get(r['timestamp']) for r in recent[-21:]]
            if any(r is None for r in aligned): raise ValueError('基准缺少同时间戳的21根完整分钟，不能使用旧报价替代')
            asset=math.log(close/recent[-21]['close'])
            base=math.log(aligned[-1]['close']/aligned[0]['close'])
            relative=asset-base
            fast=_ema(prices,20)[-1]
            if relative>=.002 and asset>0 and close>fast: direction=1
            if relative<=-.002 and asset<0 and close<fast: direction=-1
            move=direction*min(abs(relative)*.5,2*atr/close)
            facts.update({'标的20分涨跌%':math.expm1(asset)*100,'基准20分涨跌%':math.expm1(base)*100,'超额对数收益%':relative*100})
            invalid=fast if direction else None
        else:
            base,attempt=recent[-24:-4],recent[-4:-1]
            upper,lower=max(r['high'] for r in base),min(r['low'] for r in base)
            broke_up=any(r['close']>upper+.1*atr for r in attempt)
            broke_down=any(r['close']<lower-.1*atr for r in attempt)
            if ratio>=1 and lower<close<upper and broke_up!=broke_down:
                if broke_up and close<upper-.1*atr and close<previous['close']:
                    direction=-1;invalid=max(r['high'] for r in attempt+[last])
                if broke_down and close>lower+.1*atr and close>previous['close']:
                    direction=1;invalid=min(r['low'] for r in attempt+[last])
            move=direction*1.5*atr/close
            facts.update({'原区间高':upper,'原区间低':lower,'此前向上突破':broke_up,'此前向下突破':broke_down})
        result.update(available=True,direction=direction,log_move=move,invalidation=invalid,facts=facts,
                      reason='偏多条件触发' if direction>0 else '偏空条件触发' if direction<0 else '条件未触发，保持观望')
    except ValueError as exc:
        result['reason']=str(exc)
    return result


def signals(rows, *, market='US', benchmark=None):
    return {m:signal(rows,m,market=market,benchmark=benchmark) for m in STRATEGIES}
