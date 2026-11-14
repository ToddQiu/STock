"""Local minute archive, immutable forecasts, and bounded automatic model selection."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from zoneinfo import ZoneInfo

from .models import utc_now
from .research_ledger import canonical, engine_fingerprint
from .short_term import _candles, _session, _UNSET_DURATION, SESSIONS, ZONES, analyze_short_term, forecast_duration_minutes
from .short_term_models import MODELS, MODEL_VERSION, HORIZONS, DEFAULT_MODEL, comparison, predict_log
from .short_term_signals import STRATEGIES, DEFAULT_BENCHMARKS, signals
from .short_term_context import build_context

SCHEMA = '''
CREATE TABLE IF NOT EXISTS targets(market TEXT,symbol TEXT,enabled INTEGER NOT NULL DEFAULT 1,
 last_pull REAL NOT NULL DEFAULT 0,last_eval REAL NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(market,symbol));
CREATE TABLE IF NOT EXISTS bars(market TEXT,symbol TEXT,stamp INTEGER,date TEXT,payload TEXT,
 PRIMARY KEY(market,symbol,stamp));
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS selections(market TEXT,symbol TEXT,horizon INTEGER,model TEXT,version INTEGER,
 watermark REAL NOT NULL DEFAULT 0,PRIMARY KEY(market,symbol,horizon));
CREATE TABLE IF NOT EXISTS iterations(id TEXT PRIMARY KEY,market TEXT,symbol TEXT,created REAL,data_asof REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS iterations_target ON iterations(market,symbol,created DESC);
CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY,market TEXT,symbol TEXT,issued REAL,due REAL,
 kind TEXT,identity TEXT UNIQUE,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS forecasts_target ON forecasts(market,symbol,issued DESC);
CREATE TABLE IF NOT EXISTS outcomes(forecast_id TEXT PRIMARY KEY,status TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY,market TEXT,symbol TEXT,horizon INTEGER,created REAL,
 previous TEXT,current TEXT,version INTEGER,reason TEXT,iteration_id TEXT);
'''


def target_key(market, symbol):
    market, symbol = str(market).strip().upper(), str(symbol).strip().upper()
    if market not in ZONES or not re.fullmatch(r'[A-Z0-9.^=_-]{1,24}',symbol):
        raise ValueError('请输入有效的市场和代码')
    return market,symbol


def expected_bar(stamp, market):
    """Latest complete regular minute at a checkpoint; never substitute an earlier gap."""
    zone = ZoneInfo(ZONES[market])
    day = datetime.fromtimestamp(stamp,zone).date()
    for offset in range(8):
        date = day-timedelta(days=offset)
        if date.weekday()>=5:
            continue
        midnight = datetime.combine(date,datetime.min.time(),zone)
        candidates=[]
        for opening,closing in SESSIONS[market]:
            left=(midnight+timedelta(minutes=opening)).timestamp()
            right=(midnight+timedelta(minutes=closing)).timestamp()
            bucket=math.floor((min(stamp,right)-60)/60)*60
            if bucket>=left:
                candidates.append(bucket)
        if candidates:
            return max(candidates)
    return None


class MinuteResearch:
    def __init__(self,path,chart_provider,*,now=utc_now,context_provider=None):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.chart_provider,self.now=chart_provider,now
        self.context_provider=context_provider
        self._stop=threading.Event()
        self._run_lock=threading.Lock()
        self._dispatch_lock=threading.Lock()
        self.error=None
        self._benchmark_errors={}
        self._thread=None
        self._job=None
        self.fingerprint=engine_fingerprint('short_term.py','short_term_models.py','short_term_signals.py','short_term_research.py','short_term_context.py')
        with self.db() as db:
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(canonical({'enabled':True,'auto_promote':True,'fee_bps':5.,'slippage_bps':2.}),))
            for table in ('forecasts','iterations','changes','outcomes'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS frozen_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'research record is immutable'); END")

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.db() as db:
            return json.loads(db.execute('SELECT payload FROM settings WHERE id=1').fetchone()[0])

    def configure(self,payload):
        settings=self.settings()
        previous=dict(settings)
        for key,value in payload.items():
            if key in ('enabled','auto_promote'):
                if not isinstance(value,bool): raise ValueError('开关须为布尔值')
            elif key in ('fee_bps','slippage_bps'):
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
                    raise ValueError('单边费用和滑点须在0至100基点之间')
            else:
                raise ValueError('未知研究设置')
            settings[key]=value
        if not self._run_lock.acquire(False): raise ValueError('研究运行中，请完成后更改设置')
        try:
            with self.db() as db:
                db.execute('UPDATE settings SET payload=? WHERE id=1',(canonical(settings),))
                if any(previous[k]!=settings[k] for k in ('fee_bps','slippage_bps')):
                    for row in db.execute('SELECT * FROM selections WHERE model!=?',(DEFAULT_MODEL,)).fetchall():
                        db.execute('INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,row['market'],row['symbol'],row['horizon'],self.now().timestamp(),row['model'],DEFAULT_MODEL,row['version']+1,'费用口径变更，恢复默认模型等待新样本验证',None))
                    db.execute('UPDATE selections SET version=version+1,model=? WHERE model!=?',(DEFAULT_MODEL,DEFAULT_MODEL))
        finally:
            self._run_lock.release()
        return settings

    def register(self,market,symbol,enabled=True):
        market,symbol=target_key(market,symbol)
        if not isinstance(enabled,bool): raise ValueError('采集开关须为布尔值')
        with self.db() as db:
            db.execute('INSERT INTO targets(market,symbol,enabled) VALUES(?,?,?) ON CONFLICT(market,symbol) DO UPDATE SET enabled=excluded.enabled',(market,symbol,int(enabled)))
        return market,symbol

    def ingest(self,market,symbol,chart,*,track=True):
        market,symbol=target_key(market,symbol)
        rows,_=_candles(chart,market,self.now())
        with self.db() as db:
            if track: db.execute('INSERT OR IGNORE INTO targets(market,symbol) VALUES(?,?)',(market,symbol))
            # A complete minute is first-observed and immutable; later source revisions
            # must not retroactively change a frozen forecast's training history.
            db.executemany('INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?)',[(market,symbol,int(r['timestamp']),r['date'],canonical({**r,'source':chart.get('source'),'received_at':self.now().isoformat()})) for r in rows])
        return rows

    def bars(self,market,symbol,*,date=None,through=None,latest_day=False):
        conditions,values=[],[market,symbol]
        if date is not None:
            conditions.append('date=?')
            values.append(date)
        if through is not None:
            conditions.append('stamp<=?')
            values.append(through)
        if latest_day:
            conditions.append('date=(SELECT date FROM bars WHERE market=? AND symbol=? ORDER BY stamp DESC LIMIT 1)')
            values.extend((market,symbol))
        fields='stamp,date,payload' if conditions else 'payload'
        query=f'SELECT {fields} FROM bars WHERE market=? AND symbol=? ORDER BY stamp DESC LIMIT 100000'
        if conditions:
            # Filter before JSON decoding, but after the original archive cap:
            # requesting an older day must not revive samples outside that cap.
            query=f'SELECT payload FROM ({query}) WHERE {" AND ".join(conditions)} ORDER BY stamp DESC'
        with self.db() as db:
            rows=db.execute(query,values).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def selection(self,market,symbol,horizon=30):
        with self.db() as db:
            row=db.execute('SELECT * FROM selections WHERE market=? AND symbol=? AND horizon=?',(market,symbol,horizon)).fetchone()
        return dict(row) if row else {'model':DEFAULT_MODEL,'version':0,'watermark':0}

    def reference(self,market,symbol,*,refresh=False,period='1d',date=None):
        reference=DEFAULT_BENCHMARKS[market]
        if reference==symbol: return [],{'market':market,'symbol':reference,'error':'标的就是基准，相对强弱不适用'}
        if refresh:
            try:
                chart=self.chart_provider(market,reference,'1d' if market=='KR' else period,'intraday')
                self.ingest(market,reference,chart,track=False)
                self._benchmark_errors[market]='基准更新失败，保留历史缓存' if chart.get('retained_from_cache') else None
            except (ValueError,RuntimeError,OSError) as exc:
                self._benchmark_errors[market]=str(exc)[:200]
        rows=self.bars(market,reference,date=date)
        latest=rows[-1] if rows else None
        if date is not None:
            # A historical target day can differ from the newest benchmark day.
            # Keep the existing global freshness/source metadata in the response.
            with self.db() as db:
                record=db.execute('SELECT payload FROM bars WHERE market=? AND symbol=? ORDER BY stamp DESC LIMIT 1',(market,reference)).fetchone()
            latest=json.loads(record[0]) if record else None
        return rows,{'market':market,'symbol':reference,'last':latest['timestamp'] if latest else None,
                     'error':self._benchmark_errors.get(market),'source':latest.get('source') if latest else None}

    def inspect_strategies(self,market,symbol,chart):
        market,symbol=target_key(market,symbol)
        self.ingest(market,symbol,chart)
        day=self.bars(market,symbol,latest_day=True)
        reference,info=self.reference(market,symbol,refresh=True,date=day[-1]['date'] if day else None)
        return {'market':market,'symbol':symbol,'mode':'strategies','generated_at':self.now().isoformat(),
                'model_version':MODEL_VERSION,'engine_sha256':self.fingerprint,
                'source':chart.get('source'),'retained_from_cache':bool(chart.get('retained_from_cache')),
                'benchmark':info,'benchmark_input_bars':[r for r in reference if day and day[-1]['timestamp']-20*60<=r['timestamp']<=day[-1]['timestamp']],
                'result':{'available':bool(day),'interval':'1m','history':day,
                    'signals':signals(day,market=market,benchmark=reference)}}

    def issue(self,market,symbol,chart,*,hours=_UNSET_DURATION,minutes=_UNSET_DURATION,model_id=None):
        market,symbol=target_key(market,symbol)
        duration_minutes=forecast_duration_minutes(hours=hours,minutes=minutes)
        if model_id not in (None,'auto') and model_id not in MODELS: raise ValueError('未知预测策略')
        fresh=self.ingest(market,symbol,chart)
        reference_date=None
        if fresh:
            # A short provider window can omit the opening bars needed for VWAP.
            latest=fresh[-1]
            reference_date=latest['date']
            chart={**chart,'candles':[r for r in self.bars(market,symbol,date=latest['date'],through=latest['timestamp'])
                if r['date']==latest['date'] and r['timestamp']<=latest['timestamp']]}
        selected=self.selection(market,symbol)
        chosen=selected['model'] if model_id in (None,'auto') else model_id
        reference,info=self.reference(market,symbol,refresh=chosen=='relative_strength',date=reference_date)
        now=self.now()
        data=analyze_short_term(chart,market,symbol,'forecast',now,minutes=duration_minutes,model_id=chosen,
                                benchmark=reference,allow_fallback=model_id in (None,'auto'))
        data['result']['context']=build_context(market,symbol,now,data['result'],self.context_snapshot(market,symbol,now))
        data['benchmark']=info
        stamps={r['timestamp'] for r in data['input_bars'][-21:]}
        data['benchmark_input_bars']=[r for r in reference if r['timestamp'] in stamps]
        if data['result']['available']:
            data['model_selection']={**selected,'selection_horizon_minutes':30,'manual_override':model_id not in (None,'auto'),
                'note':'当前模型来自30分钟验证；不代表本次所选时长的预测已通过验证'}
            data['forecast_id']=self.freeze(market,symbol,data,'manual',None)
        return data

    def context_snapshot(self,market,symbol,now):
        if self.context_provider is None:
            return {}
        try:
            return self.context_provider(market,symbol,now)
        except Exception as exc:
            # Context is optional evidence: a failed cache read must not cancel a price forecast.
            error=f'{type(exc).__name__}: {exc}'[:200]
            return {'news_error':error,'fundamentals_error':error}

    def freeze(self,market,symbol,data,kind,identity):
        result=data['result']
        payload={**data,'engine_sha256':self.fingerprint,'model_version':MODEL_VERSION}
        record_id=uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?,?,?,?,?)',
                       (record_id,market,symbol,self.now().timestamp(),result['end_timestamp'],kind,identity,canonical(payload)))
            if identity:
                record_id=db.execute('SELECT id FROM forecasts WHERE identity=?',(identity,)).fetchone()[0]
        return record_id

    def issue_automatic(self,market,symbol,rows,benchmark=None):
        if not rows: return
        latest=rows[-1]
        now=self.now()
        if not 0<=now.timestamp()-latest['timestamp']-60<=180: return
        zone=ZoneInfo(ZONES[market])
        local=datetime.fromtimestamp(latest['timestamp'],zone)
        day=[r for r in rows if r['date']==latest['date']]
        decisions=signals(day,market=market,benchmark=benchmark)
        context_snapshot=None
        for horizon in HORIZONS:
            end=latest['timestamp']+(horizon+1)*60
            last_local=datetime.fromtimestamp(end-60,zone)
            if local.date()!=last_local.date() or _session(local,market)!=_session(last_local,market): continue
            with self.db() as db:
                active=db.execute("SELECT 1 FROM forecasts WHERE market=? AND symbol=? AND kind=? AND due>? LIMIT 1",(market,symbol,f'auto-{horizon}',now.timestamp())).fetchone()
            if active: continue
            selected=self.selection(market,symbol,horizon)
            candidates={}
            for m in MODELS:
                try: candidates[m]=latest['close']*math.exp(predict_log(day,horizon,m,market=market,benchmark=benchmark,decision=decisions.get(m)))
                except ValueError: candidates[m]=None
            model=selected['model'] if candidates[selected['model']] is not None else DEFAULT_MODEL
            if candidates[model] is None: continue
            anchor=latest['close']
            data={'market':market,'symbol':symbol,'mode':'forecast','generated_at':now.isoformat(),
                'source':latest.get('source'),'model_selection':selected,
                'result':{'available':True,'interval':'1m','horizon_minutes':horizon,'hours':horizon/60,
                    'start_timestamp':latest['timestamp']+60,'end_timestamp':end,'anchor_price':anchor,
                    'history':rows[-61:], 'model_id':model,'candidate_prices':candidates,'strategy_signals':decisions,
                    'input_range':{'from':day[0]['timestamp'],'to':latest['timestamp']},
                    'benchmark':DEFAULT_BENCHMARKS[market],
                    'benchmark_input_bars':[r for r in benchmark or [] if latest['timestamp']-20*60<=r['timestamp']<=latest['timestamp']],
                    'path':[{'timestamp':latest['timestamp']+(minute+1)*60,
                             'price':anchor*math.exp(predict_log(day,minute,model,market=market,benchmark=benchmark,decision=decisions.get(model)))}
                            for minute in range(horizon+1)]}}
            if context_snapshot is None:
                context_snapshot=self.context_snapshot(market,symbol,now)
            data['result']['context']=build_context(market,symbol,now,data['result'],context_snapshot)
            self.freeze(market,symbol,data,f'auto-{horizon}',f'{market}:{symbol}:{latest["timestamp"]}:{horizon}')

    def actuals(self,payload,rows):
        by_stamp={r['timestamp']:r for r in rows}
        now=self.now().timestamp()
        points=[]
        for p in payload['result']['path']:
            if p['timestamp']>now: continue
            stamp=expected_bar(p['timestamp'],payload['market'])
            row=by_stamp.get(stamp)
            points.append({'timestamp':p['timestamp'],'bar_timestamp':stamp,
                           'price':row['close'] if row else None,'prediction':p['price']})
        return points

    def trajectory(self,payload,rows):
        result=payload['result']
        end=min(self.now().timestamp(),result['end_timestamp'])
        points=[]
        for row in rows:
            stamp=row['timestamp']+60
            if not result['start_timestamp']<=stamp<=end: continue
            if points and stamp-points[-1]['timestamp']>60:
                points.append({'timestamp':points[-1]['timestamp']+60,'price':None})
            points.append({'timestamp':stamp,'price':row['close']})
        return points

    def settle(self,market,symbol,rows):
        with self.db() as db:
            pending=db.execute('SELECT f.* FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id WHERE f.market=? AND f.symbol=? AND o.forecast_id IS NULL AND f.due<=?',(market,symbol,self.now().timestamp())).fetchall()
        for record in pending:
            payload=json.loads(record['payload'])
            actual=self.actuals(payload,rows)
            final=actual[-1] if actual else None
            if final and final['price'] is not None:
                status='evaluated'
            elif self.now().timestamp()-record['due']>2*86400:
                status='missing'
            else:
                continue
            outcome={'recorded_at':self.now().isoformat(),'actual':actual,'trajectory':self.trajectory(payload,rows),
                     'candidate_errors':{m:(p/final['price']-1)*100 if p is not None else None for m,p in payload['result'].get('candidate_prices',{}).items()} if status=='evaluated' else {},
                     'error_pct':(final['prediction']/final['price']-1)*100 if status=='evaluated' else None}
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO outcomes VALUES(?,?,?)',(record['id'],status,canonical(outcome)))

    def evaluate(self,market,symbol,rows,benchmark=None):
        if not rows: return None
        settings=self.settings()
        with self.db() as db:
            last=db.execute('SELECT data_asof,payload FROM iterations WHERE market=? AND symbol=? ORDER BY created DESC,rowid DESC LIMIT 1',(market,symbol)).fetchone()
        policy={k:settings[k] for k in ('fee_bps','slippage_bps')}
        benchmark_stamp=benchmark[-1]['timestamp'] if benchmark else None
        if last and last['data_asof']>=rows[-1]['timestamp'] and json.loads(last['payload'])['policy']==policy and json.loads(last['payload']).get('engine_sha256')==self.fingerprint and json.loads(last['payload']).get('benchmark_asof')==benchmark_stamp:
            return json.loads(last['payload'])
        comparisons=[]
        selections={h:self.selection(market,symbol,h) for h in HORIZONS}
        for horizon in HORIZONS:
            selected=selections[horizon]
            comparisons.append(comparison(rows,horizon,policy,watermark=selected['watermark'],current=selected['model'],market=market,benchmark=benchmark))
        run_id=uuid.uuid4().hex
        result={'id':run_id,'created_at':self.now().isoformat(),'model_version':MODEL_VERSION,'engine_sha256':self.fingerprint,
                'data_asof':rows[-1]['timestamp'],'benchmark':DEFAULT_BENCHMARKS[market],'benchmark_asof':benchmark_stamp,
                'policy':policy,'comparisons':comparisons,'auto_promote':settings['auto_promote']}
        with self.db() as db:
            db.execute('INSERT INTO iterations VALUES(?,?,?,?,?,?)',(run_id,market,symbol,self.now().timestamp(),rows[-1]['timestamp'],canonical(result)))
            for item in comparisons:
                selected=selections[item['horizon']]
                model,version=selected['model'],selected['version']
                if item['accepted'] and settings['auto_promote']:
                    model,version=item['candidate'],version+1
                    db.execute('INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex,market,symbol,item['horizon'],self.now().timestamp(),selected['model'],model,version,item['reason'],run_id))
                db.execute('INSERT INTO selections VALUES(?,?,?,?,?,?) ON CONFLICT(market,symbol,horizon) DO UPDATE SET model=excluded.model,version=excluded.version,watermark=excluded.watermark',
                    (market,symbol,item['horizon'],model,version,item['consumed_until']))
            db.execute('UPDATE targets SET last_eval=? WHERE market=? AND symbol=?',(self.now().timestamp(),market,symbol))
        return result

    def rollback(self,market,symbol,horizon):
        market,symbol=target_key(market,symbol)
        if horizon not in HORIZONS: raise ValueError('无效模型期限')
        if not self._run_lock.acquire(False): raise ValueError('研究任务运行中，请完成后回滚')
        try:
            with self.db() as db:
                change=db.execute('SELECT * FROM changes WHERE market=? AND symbol=? AND horizon=? ORDER BY created DESC,rowid DESC LIMIT 1',(market,symbol,horizon)).fetchone()
                if not change: raise ValueError('暂无可回滚的模型版本')
                if not change['iteration_id']: raise ValueError('当前已恢复模型，需新升级后才能再次回滚')
                selected=db.execute('SELECT * FROM selections WHERE market=? AND symbol=? AND horizon=?',(market,symbol,horizon)).fetchone()
                version=selected['version']+1
                db.execute('UPDATE selections SET model=?,version=? WHERE market=? AND symbol=? AND horizon=?',(change['previous'],version,market,symbol,horizon))
                db.execute('INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,market,symbol,horizon,self.now().timestamp(),selected['model'],change['previous'],version,'用户回滚',None))
        finally:
            self._run_lock.release()

    def record(self,record_id):
        with self.db() as db:
            row=db.execute('SELECT f.*,o.status,o.payload AS outcome FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id WHERE f.id=?',(record_id,)).fetchone()
        if not row: raise ValueError('预测记录不存在')
        data=json.loads(row['payload'])
        rows=self.bars(row['market'],row['symbol']) if not row['outcome'] else []
        outcome=json.loads(row['outcome']) if row['outcome'] else {'actual':self.actuals(data,rows),'trajectory':self.trajectory(data,rows)}
        return {'id':record_id,'status':row['status'] or 'pending','forecast':data,'outcome':outcome}

    def snapshot(self,market,symbol):
        market,symbol=target_key(market,symbol)
        rows=self.bars(market,symbol)
        with self.db() as db:
            target=db.execute('SELECT * FROM targets WHERE market=? AND symbol=?',(market,symbol)).fetchone()
            latest=db.execute('SELECT payload FROM iterations WHERE market=? AND symbol=? ORDER BY created DESC,rowid DESC LIMIT 1',(market,symbol)).fetchone()
            runs=db.execute('SELECT payload FROM iterations WHERE market=? AND symbol=? ORDER BY created DESC,rowid DESC LIMIT 10',(market,symbol)).fetchall()
            records=db.execute('SELECT f.id,f.issued,f.due,f.kind,json_extract(f.payload,\'$.result.model_id\') AS model,o.status,o.payload AS outcome FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id WHERE f.market=? AND f.symbol=? ORDER BY f.issued DESC,f.rowid DESC LIMIT 30',(market,symbol)).fetchall()
            changes=db.execute('SELECT * FROM changes WHERE market=? AND symbol=? ORDER BY created DESC,rowid DESC LIMIT 20',(market,symbol)).fetchall()
            targets=db.execute('SELECT market,symbol,enabled,error FROM targets ORDER BY market,symbol').fetchall()
        _,benchmark_info=self.reference(market,symbol)
        return {'settings':self.settings(),'models':MODELS,'strategies':STRATEGIES,'benchmark':benchmark_info,'horizons':HORIZONS,'busy':self._run_lock.locked(),'error':self.error,
            'target':dict(target) if target else None,'targets':[dict(r) for r in targets],
            'data':{'bars':len(rows),'days':len({r['date'] for r in rows}),'first':rows[0]['timestamp'] if rows else None,
                    'last':rows[-1]['timestamp'] if rows else None,
                    'gaps':sum(b['date']==a['date'] and b['timestamp']-a['timestamp']!=60 and _session(datetime.fromtimestamp(a['timestamp'],ZoneInfo(ZONES[market])),market)==_session(datetime.fromtimestamp(b['timestamp'],ZoneInfo(ZONES[market])),market) for a,b in zip(rows,rows[1:])),
                    'missing_volume':sum(r.get('volume') is None for r in rows)},
            'iteration':json.loads(latest[0]) if latest else None,
            'runs':[{'created_at':p['created_at'],'results':[{'horizon':c['horizon'],'reason':c['reason'],'accepted':c['accepted']} for c in p['comparisons']]} for p in (json.loads(r[0]) for r in runs)],
            'active':{str(h):self.selection(market,symbol,h) for h in HORIZONS},
            'records':[{**dict(r),'status':r['status'] or 'pending','outcome':json.loads(r['outcome']) if r['outcome'] else None} for r in records],
            'changes':[dict(r) for r in changes]}

    def cycle(self,*,force=False):
        if not self._run_lock.acquire(False): return False
        try:
            with self.db() as db:
                targets=[dict(r) for r in db.execute('SELECT * FROM targets WHERE enabled=1')]
            for item in targets:
                if self._stop.is_set(): break
                market,symbol=item['market'],item['symbol']
                local=self.now().astimezone(ZoneInfo(ZONES[market]))
                interval=60 if _session(local,market) is not None else 900
                if not force and self.now().timestamp()-item['last_pull']<interval: continue
                try:
                    new_day=not item['last_pull'] or datetime.fromtimestamp(item['last_pull'],ZoneInfo(ZONES[market])).date()<local.date()
                    period='5d' if new_day and not (market=='KR' and symbol=='^KS11') else '1d'
                    try:
                        chart=self.chart_provider(market,symbol,period,'intraday')
                    except (ValueError,RuntimeError,OSError):
                        if period=='1d': raise
                        chart=self.chart_provider(market,symbol,'1d','intraday')
                    self.ingest(market,symbol,chart)
                    rows=self.bars(market,symbol)
                    benchmark,_=self.reference(market,symbol,refresh=True,period=period)
                    self.settle(market,symbol,rows)
                    if force or self.now().timestamp()-item['last_eval']>=3600:
                        self.evaluate(market,symbol,rows,benchmark)
                    if not chart.get('retained_from_cache'):
                        self.issue_automatic(market,symbol,rows,benchmark)
                    error='来源更新失败，本轮为旧缓存' if chart.get('retained_from_cache') else None
                except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
                    error=str(exc)[:300]
                with self.db() as db:
                    db.execute('UPDATE targets SET last_pull=?,error=? WHERE market=? AND symbol=?',(self.now().timestamp(),error,market,symbol))
            return True
        finally:
            self._run_lock.release()

    def trigger(self):
        with self._dispatch_lock:
            if self._run_lock.locked() or self._job and self._job.is_alive(): return False
            self._job=threading.Thread(target=lambda:self._cycle_report(force=True),name='minute-research-now',daemon=True)
            self._job.start()
        return True

    def _cycle_report(self,**kwargs):
        try:
            self.cycle(**kwargs)
            self.error=None
        except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
            self.error=str(exc)[:300]

    def start(self):
        def loop():
            while not self._stop.wait(15):
                if self.settings()['enabled']:
                    self._cycle_report()
        self._thread=threading.Thread(target=loop,name='minute-research',daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        for thread in (self._thread,self._job):
            if thread: thread.join(timeout=3)
