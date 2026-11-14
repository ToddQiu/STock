"""Editorial sections backed by the evidence frozen with a global briefing."""
from calendar import month_name
from datetime import date, datetime, timedelta
from html import unescape
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .briefing import _number, _time
from .briefing_narrative import _evidence
from .market_sessions import MARKETS


ZONE = ZoneInfo('Asia/Shanghai')
GEO = re.compile(r'地缘|制裁|关税|關稅|停火|战争|戰爭|外交|出口管制|sanctions?|tariffs?|ceasefire|geopolitic|export controls?', re.I)
INDUSTRY = re.compile(r'产业|行业|供应链|產業|行業|供應鏈|产能|订单|半导体|芯片|能源|sector|industry|supply chain|capacity|semiconductors?', re.I)
FINANCING = re.compile(r'IPO|首次公开发行|首次公開發行|再融资|再融資|配售|增发|增發|可转债|可轉債|initial public offering|secondary offering|rights issue|share placement', re.I)
ANNOUNCEMENT = re.compile(r'公告|披露|业绩报告|業績報告|财报|財報|监管问询|監管問詢|filing|files (?:its|a|form)|earnings release', re.I)
SIGNALS = {'bullish': '利好', 'bearish': '利空', 'neutral': '中性'}


def fetch_week_calendar(cutoff):
    """Use the Fed's dated public monthly calendar for policy-related events."""
    local = cutoff.astimezone(ZONE)
    through = (local + timedelta(days=6-local.weekday())).replace(hour=23, minute=59, second=59)
    eastern = ZoneInfo('America/New_York')
    months = {(stamp.astimezone(eastern).year, stamp.astimezone(eastern).month) for stamp in (cutoff, through)}
    rows = []
    for year, month in sorted(months):
        url = f'https://www.federalreserve.gov/newsevents/{year}-{month_name[month].lower()}.htm'
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=6) as response:
            raw = response.read(500_001)
        if len(raw) > 500_000:
            raise ValueError('美联储日历响应过大')
        pattern = r'<div class="col-xs-2">\s*<p>([^<]+)</p>\s*</div>\s*<div class="col-xs-7">\s*<p>([^<]+)</p>(.*?)</div>\s*<div class="col-xs-3">\s*<p>(\d{1,2})</p>'
        for clock, title, detail, day in re.findall(pattern, raw.decode('utf-8-sig'), re.S):
            title = unescape(title).strip()
            if not re.search(r'FOMC|Beige Book|Speech|Testimony|Press Conference', title, re.I):
                continue
            try:
                parsed = datetime.strptime(re.sub(r'[.\s]', '', clock).upper(), '%I:%M%p')
                stamp = datetime(year, month, int(day), parsed.hour, parsed.minute, tzinfo=eastern)
            except ValueError:
                continue
            if not cutoff < stamp <= through:
                continue
            topic = re.search(r'<p class=["\']calendar__title["\']>\s*<em>(.*?)</em>', detail, re.S)
            text = stamp.astimezone(ZONE).strftime('%Y-%m-%d %H:%M 北京时间')
            if topic:
                text += ' · ' + unescape(re.sub(r'<[^>]+>', '', topic[1])).strip()[:150]
            rows.append({'title': title, 'detail': text, 'refs': [], 'as_of': stamp.isoformat(), 'source': '美联储官方日历', 'url': url})
    return sorted(rows, key=lambda item: item['as_of'])


def index_commentary(front):
    rows = [row for row in front['markets'] if _number(row.get('change_pct')) is not None and row.get('session_date') and row.get('ref')]
    if not rows:
        return [{'text': '有效涨跌数据不足，本期暂无法比较指数方向或板块强弱。', 'refs': []}]
    rising = sum(row['change_pct'] > 0 for row in rows)
    falling = sum(row['change_pct'] < 0 for row in rows)
    result = [{'text': f'表内 {len(rows)} 只可比较指数中，{rising} 只上涨，{falling} 只下跌，{len(rows)-rising-falling} 只持平。各地收盘时点不同，方向统计仅覆盖表内样本。',
               'refs': [row['ref'] for row in rows]}]
    for market in ('US', 'CN'):
        local = [row for row in rows if row['market'] == market]
        if len(local) < 2 or len({row['session_date'] for row in local}) != 1:
            continue
        strongest, weakest = max(local, key=lambda r: r['change_pct']), min(local, key=lambda r: r['change_pct'])
        if strongest['change_pct'] == weakest['change_pct']:
            continue
        spread = strongest['change_pct'] - weakest['change_pct']
        result.append({'text': f"{strongest['session_date']} {MARKETS[market]['name']}收盘，{strongest['label']}（{strongest['change_pct']:+.2f}%）比{weakest['label']}（{weakest['change_pct']:+.2f}%）强 {spread:.2f} 个百分点，反映所列指数间的相对强弱。",
                       'refs': [strongest['ref'], weakest['ref']]})
    return result


def _news_row(item, explanation=''):
    source = item.get('source') or '来源待核对'
    excerpt = item.get('excerpt', '').strip().removesuffix(source).strip()
    title = item['title'].removesuffix(' - ' + source)
    return {'title': title, 'detail': explanation or (excerpt[:160] if excerpt != title else ''),
            'refs': [item['ref']], 'url': item.get('url'), 'source': source, 'as_of': item.get('published_at')}


def _is_filing(item):
    try:
        host = (urlsplit(item.get('url') or '').hostname or '').lower()
    except ValueError:
        return False
    return any(host == domain or host.endswith('.'+domain) for domain in ('sec.gov', 'hkexnews.hk', 'cninfo.com.cn', 'sse.com.cn', 'szse.cn', 'dart.fss.or.kr'))


def _week_rows(report, cutoff):
    start = cutoff.astimezone(ZONE).date()
    end = start + timedelta(days=6-start.weekday())
    upcoming = []
    for item in report.get('calendar_events', []):
        stamp = _time(item.get('as_of'))
        if stamp and cutoff < stamp and start <= stamp.astimezone(ZONE).date() <= end:
            upcoming.append((stamp.astimezone(ZONE).date(), item))
    for item in report.get('earnings', []):
        try:
            day = date.fromisoformat(item.get('earnings_date') or '')
        except (TypeError, ValueError):
            continue
        if not start <= day <= end or not item.get('name'):
            continue
        sources = item.get('sources') or []
        source = next((row for row in sources if row.get('url')), {})
        market = MARKETS.get(item.get('market') or 'US', {}).get('name', '')
        certainty = '已确认日期' if item.get('date_status') == 'confirmed' and not item.get('date_conflict') else '预计日期，待发行人确认'
        upcoming.append((day, {'title': f"{item['name']}财报", 'detail': f'{day} {market}交易日 · {certainty}',
                               'refs': [], 'source': source.get('name'), 'url': source.get('url')}))
    return [row for _, row in sorted(upcoming, key=lambda entry: entry[0])[:10]]


def build_sections(report, sessions):
    """Keep the same section order when individual sources have no usable data."""
    narrative = report['narrative']
    hypotheses = narrative.get('hypotheses') or []
    _, _, news, _ = _evidence(report)
    cutoff = _time(report['window']['end'])
    sections = []

    def add(title, rows, empty, summary=''):
        sections.append({'title': title, 'summary': summary or ('' if rows else empty), 'rows': rows})

    focus = [{'title': row['sector'], 'detail': f"{SIGNALS[row['signal']]} · {row['event']}。验证条件为{row['validation']}",
              'refs': row['refs']} for row in hypotheses]
    risks = [{'title': row.get('condition') or '风险与验证条件',
              'detail': '。'.join(text for text in (row.get('confirms'), row.get('weakens')) if text), 'refs': row.get('refs') or []}
             for row in narrative.get('watchpoints', [])]
    overview = narrative.get('overview') or report.get('factual_summary') or ''
    if report['kind'] == 'morning':
        by_event = {ref: row for row in hypotheses for ref in row['refs'] if ref.startswith('N')}
        important = []
        for item in news[:5]:
            hypothesis = by_event.get(item['ref'])
            detail = f"{hypothesis['sector']} · {hypothesis['impact']}" if hypothesis else ''
            important.append(_news_row(item, detail))
        add('重要新闻与行业影响', important, '本期未取得可核对的重要新闻。')
        add('地缘政治', [_news_row(item) for item in news if GEO.search(item['title'])][:4], '本期采集未覆盖可核对的地缘事件。')
        add('产业／行业动态', [_news_row(item) for item in news if INDUSTRY.search(item['title']) or item.get('event_type') in {'order', 'capacity', 'product'}][:4], '本期未取得可核对的产业动态。')
        add('IPO／再融资', [_news_row(item) for item in news if FINANCING.search(item['title'])][:4], '本期未取得可核对的 IPO 或再融资消息。')
        filings = [_news_row(item, '可查阅披露原文。' if _is_filing(item) else '报道提及公告，内容和日期以发行人披露为准。')
                   for item in news if _is_filing(item) or ANNOUNCEMENT.search(item['title'])]
        add('上市公司公告', filings[:5], '本期未取得可核对的上市公司公告或披露线索。')
        add('本周重要事件前瞻', _week_rows(report, cutoff), '本周余下日期内暂未取得可核对的日程。')
        add('今日重点关注板块', focus, '当前证据尚不足以给出具体板块判断。')
        add('综合盘前判断与风险提示', risks, '本期暂无可复核的验证条件。', overview)
    else:
        add('值得关注的板块', focus, '当前证据尚不足以给出具体板块判断。')
        intraday = []
        for item in news:
            pair, stamp = sessions.get(item.get('market')), _time(item.get('published_at'))
            if pair and stamp and pair[0].open <= stamp <= pair[0].close:
                intraday.append(_news_row(item))
        for event in report.get('events', []):
            pair, stamp = sessions.get(event.get('market')), _time(event.get('detected_at'))
            if not pair or not stamp or not pair[0].open <= stamp <= pair[0].close:
                continue
            change = _number(event.get('change_pct'))
            if change is not None:
                intraday.append({'title': f"{event.get('name') or event.get('symbol') or '标的'}盘中异动",
                                 'detail': f"{pair[0].date}交易时段监测到 {change:+.2f}%，原因仍待核对。",
                                 'refs': [], 'source': 'STock 盘中异动监测', 'as_of': event.get('detected_at'), 'url': event.get('evidence_url')})
        add('盘中重要新闻与异动', sorted(intraday, key=lambda item: item.get('as_of') or '', reverse=True)[:8],
            '本期未取得能匹配已完成交易时段的盘中样本。')
        add('明日展望与风险提示', risks, '本期暂无可复核的验证条件。', overview)
    return sections
