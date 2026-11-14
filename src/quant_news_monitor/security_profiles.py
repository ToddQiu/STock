"""Public classification metadata, refreshed independently of prices and research."""
from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .security_master import CURRENCIES, cached_identity, normalize, preferred_name, remember_profiles

REGIONS = {'US': 'america', 'CN': 'china', 'HK': 'hongkong', 'KR': 'korea'}
COLUMNS = ['name', 'description', 'sector', 'industry', 'type', 'currency']


def fetch_profiles(market, symbols):
    names = [s.split('.')[0] if market != 'US' else s for s in symbols]
    if market == 'HK':
        names = [str(int(s)) for s in names]
    body = {'filter': [{'left': 'name', 'operation': 'in_range', 'right': names}],
            'columns': COLUMNS, 'range': [0, len(symbols) * 4],
            'options': {'lang': 'zh' if market == 'CN' else 'en'}}
    request = Request(f'https://scanner.tradingview.com/{REGIONS[market]}/scan',
                      data=json.dumps(body).encode(), headers={'User-Agent': 'Mozilla/5.0',
                      'Content-Type': 'application/json', 'Origin': 'https://www.tradingview.com'})
    with urlopen(request, timeout=8) as response:
        raw = response.read(200001)
    if len(raw) > 200000:
        raise ValueError('分类响应过大')
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('分类来源格式无效')
    now = datetime.now(timezone.utc).isoformat()
    hk_names = {}
    if market == 'HK' and payload['data']:
        from .market_scanner import _hk_company_names
        hk_names = _hk_company_names(int(datetime.now(timezone.utc).timestamp()) // 86400, 8)
    matches = {}
    for row in payload['data']:
        if not isinstance(row, dict) or len(row.get('d', [])) != len(COLUMNS):
            continue
        exchange, sep, code = str(row.get('s', '')).partition(':')
        allowed = {'US': {'NASDAQ', 'NYSE', 'AMEX', 'CBOE'}, 'CN': {'SSE', 'SZSE', 'BSE'}, 'HK': {'HKEX'}, 'KR': {'KRX'}}[market]
        fields = dict(zip(COLUMNS, row['d']))
        if not sep or exchange not in allowed or fields['name'] != code or fields['currency'] != CURRENCIES[market]:
            continue
        suffix = {'SSE': '.SS', 'SZSE': '.SZ', 'BSE': '.BJ'}.get(exchange, '')
        try:
            _, symbol = normalize(market, code + suffix)
        except ValueError:
            continue
        if market == 'KR':
            expected = [s for s in symbols if s.split('.')[0] == code]
            if len(expected) == 1:
                symbol = expected[0]
        if symbol not in symbols or not isinstance(fields['description'], str) or not fields['description'].strip():
            continue
        known = cached_identity(market, symbol) or {}
        if known.get('exchange') and known['exchange'] != exchange:
            continue
        kind = fields['type']
        if kind not in {'stock', 'dr', 'fund'}:
            continue
        fund = kind == 'fund'
        asset_type = (known.get('asset_type') if known.get('asset_type') in {'etf', 'etn', 'fund'} else
                      'etn' if 'ETN' in fields['description'] else 'etf' if 'ETF' in fields['description'] else 'fund') if fund else 'stock'
        url = 'https://www.tradingview.com/symbols/' + row['s'].replace(':', '-') + '/'
        name = preferred_name(market, hk_names.get(str(int(code))) if market == 'HK' else None,
                              fields['description'], known.get('name'))
        profile = dict(market=market, symbol=symbol, name=name, currency=fields['currency'],
                       exchange=exchange, asset_type=asset_type, source='TradingView 公开目录', source_url=url,
                       as_of=now, classification_as_of=now, classification_source='TradingView 公开目录',
                       classification_source_url=url, classification_error='')
        for field in ('sector', 'industry'):
            value = fields[field]
            profile[field] = value.strip() if not fund and isinstance(value, str) and value.strip() else None
        if not fund and not (profile['sector'] and profile['industry']):
            continue
        matches.setdefault(symbol, []).append(profile)
    return {market + ':' + symbol: rows[0] for symbol, rows in matches.items() if len(rows) == 1}


class ProfileCollector:
    def __init__(self, store=None):
        self.store = store
        self.items = (store.load('security_profiles') or {}).get('items', {}) if store else {}
        if not isinstance(self.items, dict):
            self.items = {}
        self.items = {k: v for k, v in self.items.items() if isinstance(v, dict) and v.get('name')}
        remember_profiles(self.items)
        self.lock = threading.RLock()
        self.thread = None
        self.attempts = {}
        self.closed = False

    def shutdown(self):
        with self.lock:
            self.closed = True

    def request(self, positions):
        now = datetime.now(timezone.utc).timestamp()
        with self.lock:
            if self.closed or self.thread and self.thread.is_alive():
                return
            due = {}
            for row in positions:
                try:
                    market, symbol = normalize(row['market'], row['symbol'])
                except (KeyError, ValueError):
                    continue
                if market not in REGIONS:
                    continue
                key = market + ':' + symbol
                saved = self.items.get(key, {})
                try:
                    stamp = datetime.fromisoformat(saved.get('classification_as_of', '')).timestamp()
                except (ValueError, TypeError):
                    stamp = 0
                interval = 3600 if saved.get('classification_error') or not stamp else 86400
                last = max(stamp, self.attempts.get(key, 0))
                if now - last >= interval:
                    due[key] = (market, symbol)
                    self.attempts[key] = now
            if due:
                self.thread = threading.Thread(target=self._collect, args=(due,), name='security-profiles', daemon=True)
                self.thread.start()

    def _collect(self, due):
        for market in REGIONS:
            keys = {key: symbol for key, (m, symbol) in due.items() if m == market}
            if not keys:
                continue
            # Bound public requests without blocking the quote worker.
            pairs = list(keys.items())
            for offset in range(0, len(pairs), 50):
                batch = dict(pairs[offset:offset+50])
                try:
                    found = fetch_profiles(market, list(batch.values()))
                    error = '来源未返回可核验分类，保留已有资料'
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    found, error = {}, '分类来源暂不可用，保留已有资料：' + type(exc).__name__
                with self.lock:
                    if self.closed:
                        return
                    for key, symbol in batch.items():
                        old = self.items.get(key) or cached_identity(market, symbol) or {}
                        if key in found:
                            self.items[key] = {**old, **found[key]}
                        elif old:
                            self.items[key] = {**old, 'classification_error': error}
                    remember_profiles(self.items)
                    if self.store:
                        self.store.save('security_profiles', {'items': copy.deepcopy(self.items)}, force=True)
