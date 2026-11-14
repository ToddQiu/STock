"""Public, dated environment evidence for an instrument report."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from xml.etree.ElementTree import ParseError

from .briefing_global import QUERIES, fetch_market_news
from .models import iso_utc
from .sources import _fetch_rss, _google_locale, _parse_feed_datetime, _with_bing_url_publisher


_MACRO = re.compile(r'央行|利率|降息|加息|通胀|通膨|汇率|匯率|关税|關稅|经济|經濟|财政|財政|货币|貨幣|'
                    r'美联储|美聯儲|地缘|地緣|GDP|Federal Reserve|\bFed\b|inflation|interest rate|'
                    r'economy|economic|tariff|monetary|fiscal|Bank of Korea|금리|물가|경제|관세', re.I)


def environment_evidence(config, public, now):
    market = public.get('market')
    market = 'CN' if market == 'FUND' else market
    if market not in QUERIES:
        return []
    sources = config.sources
    google = bool(getattr(sources, 'google_news_rss', False))
    bing = bool(getattr(sources, 'bing_news_rss', False))
    if not google and not bing:
        return []
    fund = str(public.get('asset_type') or '').lower() in {'etf', 'fund', 'etn'} or public.get('market') == 'FUND'
    fields = ('investment_focus', 'industry', 'sector', 'name') if fund else ('industry', 'investment_focus', 'sector', 'name')
    focus = next((str(public.get(key) or '').strip()[:150] for key in fields if public.get(key)), '')
    query = f'"{focus.replace(chr(34), "")}" (行业 OR 供需 OR 竞争 OR demand OR supply OR competition) when:14d'

    def select(rows, kind):
        selected = {}
        for row in rows:
            url = str(row.get('url') or '')
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            published = row.get('published_at')
            if not isinstance(published, str) or not published.strip():
                continue
            stamp = _parse_feed_datetime(published)
            title, summary = str(row.get('title') or '').strip(), str(row.get('news_summary') or row.get('summary') or '').strip()
            if (not valid or not title or not row.get('source') or not now - timedelta(days=14) <= stamp <= now
                    or kind == 'macro' and not _MACRO.search(title + ' ' + summary)):
                continue
            selected.setdefault(url, {'kind': kind, 'label': title[:220], 'excerpt': summary[:600],
                                      'as_of': iso_utc(stamp), 'url': url, 'source': str(row['source'])[:80],
                                      'evidence_scope': '公开市场背景报道；标题/摘要，不等同于公司因果证据' if kind == 'macro'
                                      else '公开行业背景报道；标题/摘要，需核对与标的的业务关联'})
        return sorted(selected.values(), key=lambda row: row['as_of'], reverse=True)[:3]

    def collect(kind):
        if kind == 'industry' and not focus:
            return []
        if google:
            try:
                rows = fetch_market_news(market, now) if kind == 'macro' else _fetch_rss(
                    'https://news.google.com/rss/search?' + urlencode({'q': query, **_google_locale(market)}),
                    'Google News RSS', 6, cache_seconds=60)
                if selected := select(rows, kind):
                    return selected
            except (OSError, RuntimeError, ValueError, ParseError):
                pass
        if bing:
            try:
                search = QUERIES[market][0] if kind == 'macro' else query
                url = 'https://www.bing.com/news/search?' + urlencode({
                    'q': search, 'format': 'rss', 'setlang': _google_locale(market)['hl']})
                rows = _fetch_rss(url, 'Bing News RSS', 6, cache_seconds=60)
                return select([_with_bing_url_publisher(row, 'Bing News RSS') for row in rows], kind)
            except (OSError, RuntimeError, ValueError, ParseError):
                pass
        return []

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='instrument-environment') as pool:
        macro, industry = list(pool.map(collect, ('macro', 'industry')))
    seen, result = set(), []
    for row in [*macro, *industry]:
        if row['url'] not in seen:
            seen.add(row['url'])
            result.append({'id': f'N{20 + len(result)}', **row})
    return result
