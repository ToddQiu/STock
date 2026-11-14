"""Small, auditable short-horizon research calculations; no trade execution."""

from datetime import datetime, timedelta, timezone
import math
from statistics import mean, stdev
from zoneinfo import ZoneInfo


VERSION = "short-term-1.7"
_UNSET_DURATION = object()
HORIZONS = (5, 10, 20)
TREND_WINDOW = 20
CALIBRATION_MIN = 60
CALIBRATION_MAX = 252
ZONES = {"US": "America/New_York", "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "KR": "Asia/Seoul"}
SESSIONS = {"US": ((570, 960),), "CN": ((570, 690), (780, 900)),
            "HK": ((570, 720), (780, 960)), "KR": ((540, 930),)}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quantile(values, p):
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    left = int(position)
    return ordered[left] + (ordered[min(left + 1, len(ordered) - 1)] - ordered[left]) * (position - left)


def _pct(log_return):
    return math.expm1(log_return) * 100


def _direction(value):
    return 1 if value > 0.1 else -1 if value < -0.1 else 0


def _session(moment, market):
    if moment.weekday() >= 5:
        return None
    minute = moment.hour * 60 + moment.minute
    return next((i for i, (start, end) in enumerate(SESSIONS[market]) if start <= minute < end), None)


def _candles(chart, market, now):
    if market not in ZONES:
        raise ValueError("短线研判目前支持美股、A股、港股、韩股及其场内ETF/指数")
    if chart.get("interval") != "1m":
        raise ValueError("短线研判只使用1分钟K线，不能用其他周期替代")
    zone = ZoneInfo(ZONES[market])
    rows, stamps = [], set()
    excluded = 0
    for row in sorted(chart.get("candles", []), key=lambda r: r.get("timestamp", 0)):
        if not all(_number(row.get(k)) and row[k] > 0 for k in ("timestamp", "open", "high", "low", "close")):
            raise ValueError("行情包含无效价格或时间，请更新来源后重试")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"], row["low"]):
            raise ValueError("行情高低价不一致")
        moment = datetime.fromtimestamp(row["timestamp"], zone)
        day = moment.date().isoformat()
        # Minute stamps denote bucket START; exclude unclosed and extended-hours bars.
        if (row["timestamp"] + 60 > now.timestamp() or _session(moment, market) is None
                or row.get("session", "regular") != "regular"):
            excluded += 1
            continue
        if row["timestamp"] in stamps:
            raise ValueError("行情含重复时间，无法确定唯一研究样本")
        stamps.add(row["timestamp"])
        volume = row.get("volume")
        rows.append({**row, "date": day, "volume": volume if _number(volume) and volume >= 0 else None})
    return rows, excluded


def _runs(rows):
    """Contiguous minute counts keep lunch, overnight and missing bars out of returns."""
    runs = []
    for i, row in enumerate(rows):
        continuous = i > 0 and row['timestamp'] - rows[i-1]['timestamp'] == 60 and row['date'] == rows[i-1]['date']
        runs.append(runs[-1] + 1 if continuous else 1)
    return runs


def _forecast(logs, origin, horizon):
    return 0.5 * mean(logs[origin - TREND_WINDOW + 1:origin + 1]) * horizon


def _metrics(records):
    if not records:
        return {"count": 0}
    mae = mean(abs(r["actual"] - r["point"]) for r in records)
    baseline = mean(abs(r["actual"]) for r in records)
    result = {"count": len(records), "from": records[0]["date"], "to": records[-1]["date"],
              "mae": mae, "baseline_mae": baseline, "skill": 1 - mae / baseline if baseline > 1e-12 else None,
              "direction_hit": mean(_direction(r["point"]) == _direction(r["actual"]) for r in records),
              "up_frequency": mean(_direction(r["actual"]) == 1 for r in records),
              "majority_hit": mean(r["past_direction"] == _direction(r["actual"]) for r in records)}
    for level, alpha in ((50, 0.5), (80, 0.2)):
        for prefix in ("", "baseline_"):
            bounds = [(r[f"{prefix}low{level}"], r[f"{prefix}high{level}"], r["actual"]) for r in records]
            result[f"{prefix}coverage{level}"] = mean(low <= actual <= high for low, high, actual in bounds)
            result[f"{prefix}width{level}"] = mean(high - low for low, high, _ in bounds)
            result[f"{prefix}interval_score{level}"] = mean(
                high - low + 2 / alpha * (max(low - actual, 0) + max(actual - high, 0))
                for low, high, actual in bounds)
    return result


def forecast_minutes(rows):
    if len(rows) < TREND_WINDOW + CALIBRATION_MIN + max(HORIZONS):
        return {"available": False, "reason": "至少需要100根完整分钟K线，才能校准全部期限"}
    prices = [r["close"] for r in rows]
    logs = [0.0] + [math.log(b / a) for a, b in zip(prices, prices[1:])]
    runs = _runs(rows)
    last = len(rows) - 1
    path, evaluations = [], []
    for horizon in range(1, max(HORIZONS) + 1):
        forecasts = {j: _forecast(logs, j, horizon) for j in range(TREND_WINDOW, len(rows)) if runs[j] > TREND_WINDOW}
        outcomes = {j: math.log(prices[j + horizon] / prices[j]) for j in forecasts
                    if j + horizon < len(rows) and runs[j + horizon] >= TREND_WINDOW + horizon + 1}
        records = []
        latest = None
        for origin in range(TREND_WINDOW + horizon + CALIBRATION_MIN - 1, len(rows)):
            if origin not in forecasts or (horizon not in HORIZONS and origin != last):
                continue
            # j+h <= origin: calibration labels must already have matured at issuance.
            indices = [j for j in outcomes if j + horizon <= origin][-CALIBRATION_MAX:]
            if len(indices) < CALIBRATION_MIN:
                continue
            actuals = [outcomes[j] for j in indices]
            errors = [outcomes[j] - forecasts[j] for j in indices]
            point = forecasts[origin]
            record = {"date": datetime.fromtimestamp(rows[origin]['timestamp'], timezone.utc).isoformat(),
                      "timestamp": rows[origin]['timestamp'], "horizon": horizon, "point": _pct(point),
                      "calibration_count": len(errors)}
            for level, lower, upper in ((50, .25, .75), (80, .1, .9)):
                record[f"low{level}"] = _pct(point + quantile(errors, lower))
                record[f"high{level}"] = _pct(point + quantile(errors, upper))
                record[f"baseline_low{level}"] = _pct(quantile(actuals, lower))
                record[f"baseline_high{level}"] = _pct(quantile(actuals, upper))
            if origin == last:
                latest = record
            if origin in outcomes:
                signs = [_direction(_pct(v)) for v in actuals]
                past_direction = max((0, -1, 1), key=signs.count)
                records.append({**record, "actual": _pct(outcomes[origin]), "past_direction": past_direction,
                                "matures": datetime.fromtimestamp(rows[origin + horizon]['timestamp'], timezone.utc).isoformat()})
        if latest:
            path.append(latest)
        if horizon in HORIZONS:
            metrics = _metrics(records)
            nonoverlap = _metrics(records[::horizon])
            blocks = [_metrics(records[len(records) * i // 3:len(records) * (i + 1) // 3]) for i in range(3)]
            evidence = "样本不足" if nonoverlap["count"] < 30 else (
                "历史误差较低，尚未前向验证" if metrics["skill"] is not None and metrics["skill"] > 0 else "未优于零收益基准")
            evaluations.append({"horizon": horizon, "metrics": metrics, "nonoverlap": nonoverlap,
                                "blocks": blocks, "evidence": evidence, "records": records})
    if not any(e['metrics']['count'] for e in evaluations):
        return {"available": False, "reason": "连续分钟样本不足；断档、午休和跨日不拼接计算收益"}
    return {"available": True, "basis": "同一连续时段的分钟价格收益", "path": path,
            "evaluations": evaluations,
            "latest_close": rows[-1]["close"], "sample_count": len(rows), "start": rows[0]["date"], "as_of": rows[-1]["date"],
            "settings": {"trend_window": TREND_WINDOW, "shrinkage": 0.5, "calibration_min": CALIBRATION_MIN,
                         "calibration_max": CALIBRATION_MAX, "direction_deadband_pct": 0.1}}


def volume_minutes(rows, market, now, retained=False):
    history, events, segment = [], [], []
    previous = None
    count, last_state = 0, None
    zone = ZoneInfo(ZONES[market])
    for row in rows:
        moment = datetime.fromtimestamp(row["timestamp"], zone)
        if previous is None or row["timestamp"] - previous["timestamp"] != 60 or row["date"] != previous["date"]:
            segment, count, last_state = [], 0, None
        volume_ok = _number(row.get("volume")) and row["volume"] > 0
        item = {**row,
                "local_time": moment.strftime("%m/%d %H:%M"), "state": "预热中", "count": 0,
                "change_pct": None, "ratio": None, "average_volume": None, "level": "", "event": ""}
        if not volume_ok:
            item["state"] = "成交量不可用"
            segment, count, last_state = [], 0, None
        elif len(segment) >= 20:
            prior = segment[-20:]
            average = mean(r["volume"] for r in prior)
            ratio = row["volume"] / average
            change = (row["close"] / prior[-1]["close"] - 1) * 100
            direction = "上涨" if change > .01 else "下跌" if change < -.01 else "窄幅"
            label = ("放量" if ratio >= 1.5 else "缩量" if ratio <= .7 else "正常量") + direction
            count = count + 1 if label == last_state else 1
            if label not in ("放量上涨", "缩量下跌"):
                count = 0
            high, low = max(r["high"] for r in prior), min(r["low"] for r in prior)
            level = "收盘突破前20根高点" if row["close"] > high else "收盘跌破前20根低点" if row["close"] < low else "区间内"
            item.update(state=label, count=count, ratio=ratio, change_pct=change, average_volume=average,
                        resistance=high, support=low, level=level)
            if count == 5:
                item["event"] = "连续5根" + label
                events.append(item.copy())
            last_state = label
        if volume_ok:
            segment.append(row)
        history.append(item)
        previous = row
    latest = history[-1] if history else None
    age = max(0, now.timestamp() - latest["timestamp"] - 60) if latest else None
    local_now = now.astimezone(zone)
    status = "暂无完整分钟" if not latest else "缓存历史" if retained else (
        "最近交易时段" if _session(local_now, market) is None else "行情滞后" if age > 180 else "最近完整分钟（延迟未核实）")
    return {"available": bool(latest), "latest": latest, "history": history, "events": events,
            "status": status, "age_seconds": age, "timezone": ZONES[market],
            "settings": {"window": 20, "expand": 1.5, "shrink": .7, "deadband_pct": .01, "consecutive": 5}}


def validate_forecast_hours(hours):
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 720:
        raise ValueError("预测时长须为1至720之间的整数小时")
    return hours


def forecast_duration_minutes(*, hours=_UNSET_DURATION, minutes=_UNSET_DURATION):
    if minutes is not _UNSET_DURATION:
        if hours is not _UNSET_DURATION:
            raise ValueError("预测时长只能选择分钟或小时其中一种")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 43200:
            raise ValueError("预测时长须为1至43200之间的整数分钟")
        return minutes
    return validate_forecast_hours(24 if hours is _UNSET_DURATION else hours) * 60


def forecast_duration_label(minutes):
    hours, remainder = divmod(minutes, 60)
    return (f"{hours}小时" if hours else "") + (f"{remainder}分钟" if remainder else "")


def project_price(rows, market, now, hours=_UNSET_DURATION, model_id='trend20', *, minutes=_UNSET_DURATION, benchmark=None, allow_fallback=True):
    """Minute-based conditional path; future OHLC and volume remain unknown."""
    duration_minutes = forecast_duration_minutes(hours=hours, minutes=minutes)
    hours = duration_minutes / 60
    duration_label = forecast_duration_label(duration_minutes)
    if not rows or _runs(rows)[-1] < 21:
        return {"available": False, "reason": "最近时段至少需要21根连续完整的1分钟K线；午休、跨日或断档后重新累计"}
    zone = ZoneInfo(ZONES[market])
    age = (now.astimezone(zone).date() - datetime.fromisoformat(rows[-1]['date']).date()).days
    if age > 14:
        return {"available": False, "reason": f"最近分钟行情为{rows[-1]['date']}，已超过14天，不能作为当前预测起点"}
    recent = rows[-21:]
    prices = [r['close'] for r in recent]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    if any(abs(v) > math.log(1.5) for v in returns):
        return {"available": False, "reason": "连续分钟价格存在超过50%比例的跳变，请核对行情后生成"}
    drift, volatility = 0.5 * mean(returns), stdev(returns)
    from .short_term_models import MODELS, predict_log
    from .short_term_signals import STRATEGIES, signal
    requested_model = model_id
    decision=None
    fallback_reason=None
    try:
        if model_id in STRATEGIES:
            decision=signal(rows,model_id,market=market,benchmark=benchmark)
            local_last=datetime.fromtimestamp(rows[-1]['timestamp'],zone)
            local_now=now.astimezone(zone)
            if local_now.date()!=local_last.date() or _session(local_now,market)!=_session(local_last,market) or now.timestamp()-rows[-1]['timestamp']-60>180:
                raise ValueError('日内策略只用于信号当日的当前交易时段；休市或行情滞后时可查看历史信号，不沿用到下次开盘')
        predict_log(rows, 1, model_id,market=market,benchmark=benchmark,decision=decision)
    except ValueError as exc:
        if not allow_fallback: return {'available':False,'reason':str(exc),'strategy_decision':decision}
        fallback_reason=str(exc)
        model_id = 'trend20'
        decision=None
    trend_minutes = STRATEGIES[model_id]['cap_minutes'] if model_id in STRATEGIES else 60 if model_id == 'trend60' else 30 if model_id == 'revert60' else 20
    anchor = rows[-1]['close']
    local_now = now.astimezone(zone)
    starts_next_session = _session(local_now, market) is None
    start = now.timestamp()
    if starts_next_session:
        openings = []
        for offset in range(8):
            day = local_now.date() + timedelta(days=offset)
            if day.weekday() < 5:
                midnight = datetime.combine(day, datetime.min.time(), zone)
                openings.extend((midnight + timedelta(minutes=opening)).timestamp() for opening, _ in SESSIONS[market])
        start = min(stamp for stamp in openings if stamp > now.timestamp())
    end = start + duration_minutes * 60
    # shortcut: use the existing routine-session clock. Holiday/half-day verification
    # needs an exchange calendar; disclose this instead of calling these confirmed sessions.
    sessions = []
    day = datetime.fromtimestamp(start, zone).date()
    while day <= datetime.fromtimestamp(end, zone).date():
        if day.weekday() < 5:
            midnight = datetime.combine(day, datetime.min.time(), zone)
            for opening, closing in SESSIONS[market]:
                left = (midnight + timedelta(minutes=opening)).timestamp()
                right = (midnight + timedelta(minutes=closing)).timestamp()
                if right > start and left < end:
                    sessions.append((max(start, left), min(end, right)))
        day += timedelta(days=1)
    stamps = {start, end, *(v for session in sessions for v in session)}
    stamps.update(start + minute * 60 for minute in range(1440, duration_minutes, 1440))
    # Keep the short trend visible even when the selected wall-clock horizon is days.
    remaining = trend_minutes
    for left, right in sessions:
        for minute in range(1, min(remaining, int((right-left)/60)) + 1):
            stamps.add(left + minute * 60)
        remaining -= min(remaining, int((right-left)/60))
        if remaining <= 0:
            break
    step = max(3600, duration_minutes * 60 / 120)
    stamp = start + step
    while stamp < end:
        stamps.add(stamp)
        stamp += step
    minutes_per_day = sum(b - a for a, b in SESSIONS[market])
    path = []
    for stamp in sorted(stamps):
        minutes = sum(max(0, min(stamp, b) - a) for a, b in sessions) / 60
        t = minutes / minutes_per_day
        spread = 2 * volatility * math.sqrt(minutes)
        effective_minutes=min(minutes,(sessions[0][1]-sessions[0][0])/60) if model_id in STRATEGIES else minutes
        point = predict_log(rows, effective_minutes, model_id,market=market,benchmark=benchmark,decision=decision)
        path.append({'timestamp': stamp, 'price': anchor * math.exp(point),
                     'low': anchor * math.exp(point - spread), 'high': anchor * math.exp(point + spread),
                     'trading_days': t, 'trading_minutes': minutes,
                     'session_active': any(a < stamp <= b for a, b in sessions)})
    final = path[-1]
    change = (final['price'] / anchor - 1) * 100
    total_minutes = round(final['trading_days'] * minutes_per_day, 2)
    effective_horizon = min(trend_minutes, final['trading_minutes'])
    if model_id in STRATEGIES:
        effective_horizon = min(effective_horizon, (sessions[0][1] - sessions[0][0]) / 60)
    horizon_point = next(p for p in path if p['trading_minutes'] >= effective_horizon - 1e-8)
    projection_basis = {
        'model_horizon_minutes': trend_minutes, 'effective_horizon_minutes': effective_horizon,
        'horizon_timestamp': horizon_point['timestamp'], 'horizon_price': horizon_point['price'],
        'horizon_change_pct': (horizon_point['price'] / anchor - 1) * 100,
        'volatility_sample_count': len(returns), 'minute_std_pct': volatility * 100,
        'band_method': 'sqrt_time_log_returns', 'band_sigma': 2,
        'band_formula': '中线价格 × exp(±2 × 分钟对数收益标准差 × √累计交易分钟数)',
        'calibrated': False, 'coverage': None,
        'display_hours': hours, 'display_minutes': duration_minutes, 'trading_minutes': total_minutes,
        'full_lower_pct': (final['low'] / anchor - 1) * 100,
        'full_upper_pct': (final['high'] / anchor - 1) * 100,
        'market_closed': starts_next_session,
    }
    direction = '偏上行' if change > .1 else '偏下行' if change < -.1 else '方向不明显'
    window_label = f"下次常规开盘起{duration_label}" if starts_next_session else f"未来{duration_label}"
    summary = (f"前{effective_horizon:g}个交易分钟的技术估计{direction}，"
               f"参考价{horizon_point['price']:.2f}（{projection_basis['horizon_change_pct']:+.2f}%）；"
               f"{window_label}为展示窗口，约含{total_minutes:g}分钟交易。")
    five, twenty = (prices[-1] / prices[-6] - 1) * 100, (prices[-1] / prices[-21] - 1) * 100
    ma20 = mean(prices[-20:])
    deviation = (prices[-1] / ma20 - 1) * 100
    support = min(r['low'] for r in recent[-20:])
    resistance = max(r['high'] for r in recent[-20:])
    volumes = [r.get('volume') for r in recent[-21:]]
    volume_ratio = volumes[-1] / mean(volumes[:-1]) if all(_number(v) and v > 0 for v in volumes) else None
    reasons = [
        {'title': '分钟趋势', 'detail': f"近5分钟{five:+.2f}%，近20分钟{twenty:+.2f}%；20分钟平均对数收益减半后约{_pct(drift):+.3f}%/分钟，只延续前20个交易分钟，之后不继续放大趋势。"},
        {'title': '波动带为何变宽', 'detail': f"最近{len(returns)}个分钟对数收益的标准差为{volatility*100:.3f}%；上下界=中线×exp(±2×该标准差×√累计交易分钟数)。展示窗口累计{total_minutes:g}分钟，所以中线停止外推后，参考带仍会扩大。阴影不是预计的上下震荡，也不是已校准的95%概率区间。"},
        {'title': '价格位置', 'detail': f"最新分钟收盘较20分钟均价{deviation:+.2f}%；最近20根分钟K线低/高为{support:.2f} / {resistance:.2f}。"},
        {'title': '分钟成交量', 'detail': f"最新完整分钟成交量为此前20分钟均量的{volume_ratio:.2f}倍；仅作量能背景，未直接加权改变预测。" if volume_ratio is not None else "来源成交量不完整，本次不将量能作为预测依据。"},
    ]
    if model_id != 'trend20':
        reasons[0] = {'title': MODELS[model_id], 'detail': f'使用固定候选模型 {model_id}；趋势估计最多延续{trend_minutes}个交易分钟。是否通过验证，以自动研究中的对应记录为准。'}
    if decision:
        reasons[0]={'title':MODELS[model_id],'detail':decision['reason']+'。'+decision['rule']}
        reasons[-1]={'title':'策略证据','detail':'；'.join(f'{k}：{v:.4f}' if isinstance(v,(int,float)) else f'{k}：{v}' for k,v in decision['facts'].items())}
    warnings = ["使用最近连续分钟数据；多日路径不是已验证的多日预测，未来K线和成交量未知。",
                f"趋势最多延续前{trend_minutes}个交易分钟；之后中线持平但波动范围继续扩大，不表示价格将停止变动。",
                "按常规交易时段排除周末和午休；节假日、临时停市与半日市尚未核验。",
                "参考带不是保证覆盖的概率区间；仅由最近20个分钟收益外推，尚未验证对应期限的覆盖率。价格线未纳入未来新闻、财报意外、开盘跳空及分红除权。"]
    if starts_next_session:
        warnings.insert(0, "当前不在常规交易时段，预测起点顺延至下一个常规开盘；以最近分钟收盘为条件锚点，不代表已知下次开盘价。")
    if age > 3:
        warnings.append(f"最新分钟行情距当前{age}个自然日，请核对停牌或行情滞后。")
    if requested_model != model_id:
        warnings.append(f'所选模型暂不可用：{fallback_reason}；本次回退至20分钟趋势，不计作原模型预测。')
    return {'available': True, 'hours': hours, 'duration_minutes': duration_minutes, 'duration_label': duration_label, 'basis': 'minute',
            'strategy_decision':decision,
            'model_id': model_id, 'model_name': MODELS[model_id], 'requested_model': requested_model,
            'starts_next_session': starts_next_session,
            'start_timestamp': start, 'end_timestamp': end, 'as_of': datetime.fromtimestamp(rows[-1]['timestamp'], zone).strftime('%m/%d %H:%M'),
            'anchor_price': anchor, 'path': path, 'trading_minutes': total_minutes, 'change_pct': change,
            'summary': summary, 'reasons': reasons, 'warnings': warnings, 'projection_basis': projection_basis,
            'settings': {'window': None if model_id in STRATEGIES else 60 if model_id in ('trend60','revert60') else 20, 'interval': '1m', 'trend_minutes': trend_minutes, 'shrinkage': None if model_id in STRATEGIES else .25 if model_id == 'trend60' else .5, 'band_sigma': 2,
                         'calendar': 'routine_hours_only', 'start_rule': 'now_or_next_routine_open'},
            'sample_count': len(returns), 'support': support, 'resistance': resistance}


def analyze_short_term(chart, market, symbol, mode, now=None, *, hours=_UNSET_DURATION, minutes=_UNSET_DURATION, model_id='trend20', benchmark=None, allow_fallback=True):
    if mode not in ("daily", "intraday", "forecast"):
        raise ValueError("研判模式须为forecast、daily或intraday")
    if mode == 'forecast':
        duration_minutes = forecast_duration_minutes(hours=hours, minutes=minutes)
    now = now or datetime.now(timezone.utc)
    rows, excluded = _candles(chart, market, now)
    result = project_price(rows, market, now, minutes=duration_minutes, model_id=model_id,benchmark=benchmark,allow_fallback=allow_fallback) if mode == 'forecast' else forecast_minutes(rows) if mode == "daily" else volume_minutes(rows, market, now, chart.get("retained_from_cache", False))
    if mode != 'intraday':
        result['history'] = rows
    result['interval'] = '1m'
    return {"version": VERSION, "market": market, "symbol": symbol, "mode": mode, "result": result,
            "generated_at": now.isoformat(), "currency": chart.get('currency', ''), "source": chart.get("source"), "source_url": chart.get("source_url"),
            "retained_from_cache": bool(chart.get("retained_from_cache")), "source_note": chart.get("note", ""),
            "excluded_bars": excluded, "timezone": ZONES[market], "input_bars": rows}
