"""Editable research fields corresponding to the portfolio workbook template."""
from datetime import date
import math

TEXT_FIELDS = {
    'pool_status': ('股票池状态', 80), 'rating': ('个股评级', 80),
    'sector': ('行业', 120), 'industry': ('二级行业/分类', 120),
    'asset_class': ('资产分类', 120), 'themes': ('概念题材', 500),
    'usage': ('标的用法', 500), 'research_tracking': ('调研追踪', 2000),
    'meeting_notes': ('例会讨论', 2000), 'committee_notes': ('投委会讨论', 2000),
    'business': ('标的概况', 2000), 'pe_analysis': ('PE 分析', 2000),
    'valuation_range': ('当前区间', 500),
}
NUMBER_FIELDS = {'reference_price': '参考现价', 'target_price': '目标价', 'current_pe': '当前 PE'}
DATE_FIELDS = {'added_date': '添加日期', 'price_as_of': '参考价日期', 'pe_as_of': 'PE资料日期'}


def watch_quote(snapshot, item, *, now=None):
    """Expose the cached quote in the security's own currency."""
    from .security_master import CURRENCIES

    market, symbol = item['market'], item['symbol']
    currency = CURRENCIES.get(market)
    candidates = [row for row in snapshot.get('quotes', []) if str(row.get('symbol', '')).upper() == symbol
                  and (not row.get('market') or str(row['market']).upper() == market)]
    exact = [row for row in candidates if row.get('market')]
    quote = exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else {}
    currency_match = not quote.get('currency') or quote['currency'] == currency
    price = quote.get('price')
    valid = isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0
    status = (quote.get('status') or 'waiting') if currency_match else 'error'
    source_stale = snapshot.get('restored_from_cache') or snapshot.get('status') in {'stale', 'error', 'waiting'}
    if valid and currency_match and now and status in {'live', 'closed'} and not source_stale:
        from .portfolio_alerts import quote_usable
        source_stale = not quote_usable(quote, now, market=market)
    error = quote.get('quote_error') if currency_match else '报价币种与证券币种不符'
    if valid and currency_match and source_stale:
        status = 'stale'
        error = error or '已采集报价，时间或来源待重新确认'
    return {'currency': currency, 'price': price if valid and currency_match else None,
            'quote_time': quote.get('quote_time'), 'quote_status': status, 'quote_source': quote.get('source'),
            'quote_session': quote.get('quote_session'), 'market_session': quote.get('market_session'),
            'quote_error': error,
            'source_note': quote.get('source_note'), 'source_url': quote.get('source_url'),
            'change_pct': quote.get('change_pct') if currency_match else None}


def normalize_profile(values):
    if not isinstance(values, dict):
        raise ValueError('白名单资料须为对象')
    result = {}
    for key, (label, limit) in TEXT_FIELDS.items():
        if key not in values:
            continue
        value = values[key]
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'{label}须为不超过{limit}字的文字')
        result[key] = value.strip()
    for key, label in NUMBER_FIELDS.items():
        if key not in values:
            continue
        value = values[key]
        if value in (None, ''):
            result[key] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'{label}须为有效数字') from None
        if isinstance(value, bool) or not math.isfinite(number) or abs(number) > 1e12 or key != 'current_pe' and number <= 0:
            raise ValueError(f'{label}数值无效')
        result[key] = number
    for key, label in DATE_FIELDS.items():
        if key not in values:
            continue
        value = values[key]
        try:
            if not isinstance(value, str) or value and date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ValueError(f'{label}须为有效日期') from None
        result[key] = value
    return result
