"""Point-in-time news and annual-report context, separate from price estimates."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

from .models import canonical_news_url, news_event_key


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _news(market, symbol, now, snapshot, last_price_at):
    cutoff = now - timedelta(hours=72)
    if last_price_at:
        cutoff = max(now - timedelta(days=7), min(cutoff, last_price_at))
    items, seen = [], set()
    invalid_times = 0
    for row in sorted(snapshot.get('news') or [],
                      key=lambda r: _time(r.get('published_at')) or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        if row.get('market') != market or str(row.get('symbol', '')).upper() != symbol:
            continue
        times = [_time(row.get(key)) for key in ('published_at', 'discovered_at', 'analyzed_at')]
        if any(value and value > now for value in times):
            continue
        published = times[0]
        if not published:
            invalid_times += 1
            continue
        if published < cutoff:
            continue
        key = row.get('event_key') or news_event_key(market, symbol, str(row.get('title', '')),
            str(row.get('news_summary', '')), str(row.get('published_at', '')))
        key = key or canonical_news_url(str(row.get('url', ''))) or str(row.get('title', '')).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        metadata = row.get('analysis_metadata') or {}
        status = row.get('evidence_status') or metadata.get('evidence_status')
        score = row.get('score')
        explanation = str(row.get('impact_reason') or row.get('analysis_summary') or '').strip()
        eligible = (all(times) and status == 'supported' and metadata.get('direction_eligible') is not False
                    and row.get('event_type') not in (None, 'other') and bool(explanation)
                    and _number(score) and abs(score) > .04)
        direction = (1 if score > 0 else -1) if eligible else 0
        reason = explanation or '当前证据不足以判断影响方向。'
        if not all(times):
            reason = '采集或分析时间缺失，未纳入消息方向。' + str(reason)
        elif row.get('event_type') in (None, 'other'):
            reason = '未识别出可归属的基本面事件，未纳入消息方向。' + str(reason)
        elif not eligible:
            reason = '方向证据不足或存在冲突，未纳入消息方向。' + str(reason)
        items.append({key: row.get(key) for key in ('title', 'url', 'source', 'published_at', 'discovered_at', 'analyzed_at', 'event_type')}
                     | {'impact_reason': reason, 'direction': direction, 'evidence_status': status,
                        'after_price': bool(last_price_at and published > last_price_at)})
    counts = {'total': len(items), 'bullish': sum(r['direction'] > 0 for r in items),
              'bearish': sum(r['direction'] < 0 for r in items), 'neutral': sum(r['direction'] == 0 for r in items)}
    bias = 0 if counts['bullish'] and counts['bearish'] else 1 if counts['bullish'] else -1 if counts['bearish'] else None
    error = snapshot.get('news_error')
    if items:
        status = 'partial' if error else 'available'
        tone = '利多与利空并存' if bias == 0 else '消息偏多' if bias == 1 else '消息偏空' if bias == -1 else '暂无可用方向证据'
        summary = f"已采集{len(items)}条去重消息，{tone}；仅用于事件研判。"
        if error:
            summary += '部分来源更新失败，覆盖可能不全。'
    elif error:
        status, summary = 'unavailable', '新闻来源暂不可用，不能据此判断没有消息。'
    elif invalid_times:
        status, summary = 'insufficient_evidence', '已采集消息缺少有效发布时间，不能作为当前方向依据。'
    elif not snapshot.get('news_covered'):
        status, summary = 'not_covered', '该标的未在新闻采集范围内，暂无可用缓存；不代表没有新闻。'
    else:
        status, summary = 'no_recent_news', '当前窗口未找到该标的已采集消息；不代表市场没有新消息。'
    return {'status': status, 'summary': summary, 'bias': bias, 'items': items, 'counts': counts,
            'window_hours': round((now - cutoff).total_seconds() / 3600, 1), 'error': error,
            'coverage': '已采集的该标的新闻，非全网实时检索',
            'runtime': deepcopy(snapshot.get('news_runtime') or {})}


def _fundamentals(market, symbol, now, snapshot):
    row = snapshot.get('fundamentals') or {}
    empty = {'metrics': {}, 'source': None, 'source_url': None, 'collected_at': None, 'data_as_of': None}
    if market != 'US':
        return {**empty, 'status': 'unsupported', 'summary': '当前年度财报来源尚未覆盖该市场。'}
    if snapshot.get('fundamentals_error'):
        return {**empty, 'status': 'unavailable', 'summary': '年度财报缓存读取失败，未加入本次研判。'}
    if str(row.get('symbol', '')).upper() != symbol:
        return {**empty, 'status': 'not_collected', 'summary': '尚无该标的年度财报缓存。'}
    collected = _time(row.get('collected_at'))
    if not collected or collected > now:
        status = 'unsupported' if row.get('status') == 'unsupported' else 'not_collected'
        return {**empty, 'status': status, 'summary': row.get('note') or '尚无本次生成时点之前采集的有效年度财报。'}
    metrics = {}
    for name, metric in (row.get('metrics') or {}).items():
        try:
            filed = datetime.fromisoformat(str(metric.get('filed'))).date()
            end = datetime.fromisoformat(str(metric.get('end'))).date()
        except (ValueError, TypeError):
            continue
        if filed <= now.date() and end <= now.date() and _number(metric.get('value')):
            metrics[name] = deepcopy(metric)
    if not metrics:
        return {**empty, 'status': 'unsupported' if row.get('status') == 'unsupported' else 'not_collected',
                'summary': '未取得可用年度财报指标，不补填推测数据。'}
    stale = row.get('status') == 'stale' or bool(row.get('error')) or (now - collected).total_seconds() >= 21600
    return {'status': 'stale' if stale else 'available', 'metrics': metrics,
            'source': row.get('source'), 'source_url': row.get('source_url'),
            'collected_at': row.get('collected_at'), 'data_as_of': max(r['end'] for r in metrics.values()),
            'summary': ('沿用已缓存年度财报。' if stale else '使用已采集年度财报。')
                       + '年度经营背景不等于短线涨跌信号；未据此判断超预期。'}


def build_context(market, symbol, now, result, snapshot=None):
    snapshot = snapshot or {}
    stamps = [r.get('timestamp') for r in result.get('history', []) if _number(r.get('timestamp'))]
    last_price_at = datetime.fromtimestamp(max(stamps) + 60, timezone.utc) if stamps else None
    news = _news(market, symbol, now, snapshot, last_price_at)
    fundamentals = _fundamentals(market, symbol, now, snapshot)
    path, anchor = result.get('path') or [], result.get('anchor_price')
    change = (path[-1]['price'] / anchor - 1) * 100 if (path and _number(anchor) and anchor > 0
              and _number(path[-1].get('price'))) else None
    technical = None if change is None else 1 if change > .1 else -1 if change < -.1 else 0
    news_direction = news['bias']
    if technical in (-1, 1) and news_direction in (-1, 1):
        label = '技术与消息同向' if technical == news_direction else '技术与消息分歧'
        detail = ('已有消息与技术方向一致，仍需开盘后的价格和成交量确认。' if technical == news_direction else
                  '消息方向与技术路径相反，应观察开盘跳空及量价反应，原技术路径依据不足以覆盖新事件。')
    elif news_direction == 0:
        label, detail = '消息多空并存', '已采集事件方向存在分歧，保留双方依据，等待开盘量价确认。'
    elif news_direction in (-1, 1):
        label = '消息偏多，待价格确认' if news_direction > 0 else '消息偏空，待价格确认'
        detail = '消息已纳入休市及盘中研判；技术方向尚不明确，需观察开盘后的实际反应。'
    else:
        label, detail = '消息证据不足', '本次无法用已采集新闻确认或否定技术方向，缺消息不等于中性。'
    detail += '新闻用于定性判断和开盘观察；新闻到价格的数值映射尚未校准，未调整价格线或波动带。'
    return {'as_of': now.isoformat(), 'price_adjusted': False, 'news': news, 'fundamentals': fundamentals,
            'assessment': {'label': label, 'detail': detail, 'technical_direction': technical, 'news_direction': news_direction}}
