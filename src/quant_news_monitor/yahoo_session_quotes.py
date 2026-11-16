"""Timestamped session quotes using Yahoo's anonymous public session."""
import http.cookiejar
import json
import threading
import urllib.error
import urllib.parse
import urllib.request


class YahooSessionQuotes:
    def __init__(self):
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._opener.addheaders = [('User-Agent', 'Mozilla/5.0 QuantNewsMonitor/0.1')]
        self._crumb = None
        self._lock = threading.Lock()

    def _session(self, timeout):
        with self._lock:
            if self._crumb is None:
                try:
                    with self._opener.open('https://fc.yahoo.com', timeout=timeout) as response:
                        response.read(1024)
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                with self._opener.open('https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=timeout) as response:
                    crumb = response.read(1025).decode('utf-8').strip()
                if not crumb or len(crumb) > 1024 or '<' in crumb or '\n' in crumb:
                    raise ValueError('分时段行情会话无效')
                self._crumb = crumb
            return self._crumb

    def fetch(self, symbol, timeout):
        crumb = self._session(timeout)
        url = 'https://query1.finance.yahoo.com/v7/finance/quote?' + urllib.parse.urlencode(
            {'symbols': symbol, 'crumb': crumb, 'overnightPrice': 'true'})
        try:
            with self._opener.open(url, timeout=timeout) as response:
                body = response.read(1_000_001)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                with self._lock:
                    if self._crumb == crumb:
                        self._crumb = None
            # Do not expose the session token through the HTTPError URL.
            raise OSError(f'分时段行情请求失败（HTTP {error.code}）') from None
        if len(body) > 1_000_000:
            raise ValueError('分时段行情响应过大')
        rows = (json.loads(body).get('quoteResponse') or {}).get('result') or []
        if len(rows) != 1 or str(rows[0].get('symbol', '')).upper() != symbol.upper():
            raise ValueError('分时段行情证券代码不符')
        row = rows[0]
        if row.get('quoteType') not in {'EQUITY', 'ETF'} or row.get('exchangeTimezoneName') != 'America/New_York':
            raise ValueError('分时段行情并非美股或 ETF')
        return row
