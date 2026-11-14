"""Fixed local candidates and chronological, same-origin comparisons."""

import math
from statistics import mean

from .research_ledger import paper_return
from .short_term_signals import STRATEGIES, signals, signal

MODEL_VERSION = 'minute-candidates-2'
MODELS = {'flat': '价格不变基准', 'trend20': '20分钟趋势',
          'trend60': '60分钟趋势', 'revert60': '60分钟均值回归',
          **{key:value['name'] for key,value in STRATEGIES.items()}}
HORIZONS = (5, 15, 30, 60)
DEFAULT_MODEL = 'trend20'


def predict_log(rows, horizon, model, *, market='US', benchmark=None, decision=None):
    if model not in MODELS:
        raise ValueError('未知预测模型')
    if model in STRATEGIES:
        decision=decision or signal(rows,model,market=market,benchmark=benchmark)
        if not decision['available']: raise ValueError(decision['reason'])
        return decision['log_move']*min(horizon/decision['cap_minutes'],1)
    window = 60 if model in ('trend60', 'revert60') else 20
    if len(rows) < window + 1:
        raise ValueError(f'{MODELS[model]}需要{window+1}根连续分钟K线')
    recent = rows[-window-1:]
    if any(b['timestamp']-a['timestamp'] != 60 or b['date'] != a['date'] for a,b in zip(recent,recent[1:])):
        raise ValueError('模型窗口存在断档、午休或跨日')
    if model == 'flat':
        return 0.
    if model == 'revert60':
        return .5 * math.log(mean(r['close'] for r in recent[-60:]) / recent[-1]['close']) * min(horizon / 30, 1)
    weight = .25 if model == 'trend60' else .5
    return weight * math.log(recent[-1]['close']/recent[0]['close']) / window * min(horizon, window)


def samples(rows, horizon, policy, *, market='US', benchmark=None):
    result, run, next_origin, day_start = [], 0, 0, 0
    by_stamp={r['timestamp']:r for r in benchmark or []}
    for i,row in enumerate(rows):
        if i and row['date']!=rows[i-1]['date']: day_start=i
        run = run + 1 if i and row['timestamp']-rows[i-1]['timestamp']==60 and row['date']==rows[i-1]['date'] else 1
        if run < 21 or i < next_origin or i+horizon >= len(rows):
            continue
        end = rows[i+horizon]
        if end['timestamp']-row['timestamp'] != horizon*60 or end['date'] != row['date']:
            continue
        window=rows[max(day_start,i-60):i+horizon+1]
        if any(abs(math.log(b['close']/a['close']))>math.log(1.5) for a,b in zip(window,window[1:])):
            continue
        prefix=rows[day_start:i+1]
        reference=[by_stamp[r['timestamp']] for r in prefix[-21:] if r['timestamp'] in by_stamp]
        decisions=signals(prefix,market=market,benchmark=reference)
        predictions={}
        for m in MODELS:
            try: predictions[m]=math.expm1(predict_log(prefix,horizon,m,market=market,benchmark=reference,decision=decisions.get(m)))*100
            except ValueError: predictions[m]=None
        actual = (end['close']/row['close']-1)*100
        # Entry is next minute's open, never the close used to generate the signal.
        threshold = 2*(policy['fee_bps']+policy['slippage_bps'])/100 + .1
        returns = {m: paper_return(int(p is not None and p>threshold),rows[i+1]['open'],end['close'],policy)['net_return_pct']
                   for m,p in predictions.items()}
        result.append({'timestamp':row['timestamp'],'date':row['date'],'target_timestamp':end['timestamp'],
                       'actual':actual,'predictions':predictions,'returns':returns,
                       'trades':{m:int(p is not None and p>threshold) for m,p in predictions.items()}})
        next_origin = i+horizon+1
    return result


def metrics(records, model):
    if not records:
        return {'count':0,'days':0,'mae':None,'direction_hit':None,'net_return_pct':0.,'max_drawdown_pct':0.,'trades':0}
    direction = lambda v: 1 if v>.1 else -1 if v<-.1 else 0
    equity, peak, drawdown = 1., 1., 0.
    for r in records:
        equity *= 1+r['returns'][model]/100
        peak = max(peak,equity)
        drawdown = max(drawdown,(1-equity/peak)*100)
    return {'count':len(records),'days':len({r['date'] for r in records}),
            'mae':mean(abs(r['predictions'][model]-r['actual']) for r in records),
            'direction_hit':mean(direction(r['predictions'][model])==direction(r['actual']) for r in records),
            'net_return_pct':(equity-1)*100,'max_drawdown_pct':drawdown,
            'trades':sum(r['trades'][model] for r in records)}


def comparison(rows, horizon, policy, *, watermark=0, current=DEFAULT_MODEL, market='US', benchmark=None):
    records = samples(rows,horizon,policy,market=market,benchmark=benchmark)
    days = sorted({r['date'] for r in records})
    split = max(1,int(len(days)*.7))
    test_start = days[split] if split<len(days) else '9999'
    train = [r for r in records if r['date']<test_start]
    test = [r for r in records if r['date']>=test_start and r['timestamp']>watermark]
    # Each candidate is compared with incumbent and baseline on its exact valid
    # origins. Missing volume/reference data is not a fabricated zero forecast.
    scores={}
    for model in MODELS:
        paired=lambda rs:[r for r in rs if all(r['predictions'].get(m) is not None for m in (model,current,'flat'))]
        training,testing=paired(train),paired(test)
        scores[model]={'train':metrics(training,model),'test':metrics(testing,model),'all':metrics(paired(records),model),
                       'train_baseline':metrics(training,'flat'),'test_baseline':metrics(testing,'flat'),
                       'test_current':metrics(testing,current),'unavailable':len(records)-len(paired(records))}
    def rank(model):
        score=scores[model]
        base=score['train_baseline']['mae']
        return score['train']['mae']/base if base and score['train']['count']>=30 and score['train']['days']>=5 else math.inf
    candidate=min(MODELS,key=rank)
    trial=scores[candidate]['test']
    sufficient=math.isfinite(rank(candidate)) and trial['count']>=12 and trial['days']>=3
    reason = '等待至少5个训练交易日、3个未使用的验证交易日及足够非重叠样本'
    accepted = False
    if sufficient:
        incumbent,baseline=scores[candidate]['test_current'],scores[candidate]['test_baseline']
        beats_errors = all(other['mae']>0 and trial['mae']<=other['mae']*.95 for other in (incumbent,baseline))
        accepted = (candidate!=current and beats_errors and trial['trades']>=5
                    and trial['net_return_pct']>max(0,incumbent['net_return_pct'])
                    and trial['max_drawdown_pct']<=incumbent['max_drawdown_pct']+.25)
        reason = '验证误差改善至少5%，模拟收益和回撤门槛通过' if accepted else '未同时通过误差、交易样本、费用后收益及回撤门槛，保留当前模型'
    return {'horizon':horizon,'scores':scores,'candidate':candidate,'current':current,'accepted':accepted,
            'comparison_mode':'paired_valid_origins','origin_count':len(records),
            'sufficient':sufficient,'reason':reason,'train_days':sorted({r['date'] for r in train}),
            'test_days':sorted({r['date'] for r in test}),
            'consumed_until':max((r['target_timestamp'] for r in test),default=watermark) if sufficient else watermark}
