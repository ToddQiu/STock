import io
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from quant_news_monitor.adr_monitor import _fetch_yahoo_quote
from quant_news_monitor.portfolio_monitor import build_portfolio_analysis, PortfolioMonitor
from quant_news_monitor.us_sessions import us_quote_fields, build_extended_monitor, overnight_period
from quant_news_monitor.yahoo_session_quotes import YahooSessionQuotes


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def chart():
    ts = lambda clock: at('2026-11-13T' + clock + '-05:00').timestamp()
    return {'meta': {'symbol': 'TEST', 'currency': 'USD', 'instrumentType': 'ETF',
                    'exchangeTimezoneName': 'America/New_York', 'regularMarketPrice': 110,
                    'regularMarketTime': ts('16:00:00'), 'previousClose': 100,
                    'currentTradingPeriod': {
                        'pre': {'start': ts('04:00:00'), 'end': ts('09:30:00')},
                        'regular': {'start': ts('09:30:00'), 'end': ts('16:00:00')},
                        'post': {'start': ts('16:00:00'), 'end': ts('20:00:00')}}},
            'timestamp': [ts('19:59:00')], 'indicators': {'quote': [{'close': [111]}]}}


def snapshot(stamp='2026-11-15T21:59:30-05:00', price=115.5):
    return {'symbol': 'TEST', 'currency': 'USD', 'quoteType': 'ETF',
            'exchangeTimezoneName': 'America/New_York', 'marketState': 'OVERNIGHT',
            'regularMarketPrice': 110, 'regularMarketTime': at('2026-11-13T16:00:00-05:00').timestamp(),
            'regularMarketPreviousClose': 100, 'overnightMarketPrice': price,
            'overnightMarketTime': at(stamp).timestamp(), 'exchangeDataDelayedBy': 15}


def test_overnight_updates_valuation_and_alerts_without_changing_close_cycle():
    now = at('2026-11-15T22:00:00-05:00')
    quote = us_quote_fields(chart(), now=now, latest=snapshot())
    assert (quote['price'], quote['quote_session'], quote['market_session'], quote['status']) == (115.5, 'overnight', 'overnight', 'live')
    assert quote['price_label'] == '夜盘价'
    assert quote['quote_time'] == at('2026-11-15T21:59:30-05:00').isoformat()
    assert quote['extended_change_pct'] == pytest.approx(5)
    analysis = build_portfolio_analysis([dict(market='US', symbol='TEST', currency='USD', quantity=10, average_cost=90)],
        {'quotes': [{'symbol': 'TEST', **quote}], 'fx_rates': {'USD': 7}, 'status': 'live'}, [], [],
        now=now, account={'base_currency': 'USD', 'cash_mode': 'manual', 'cash_balances': {'USD': 0}})
    row = analysis['positions'][0]
    assert row['market_value_base'] == 1155
    assert row['pnl_base'] == 255
    assert row['daily_pnl_base'] == 100  # Friday's regular close cycle, not Sunday's overnight change.
    assert build_extended_monitor(analysis['positions'], now=now)['events'][0]['session'] == 'overnight'


@pytest.mark.parametrize('now,start,end', [
    ('2026-07-12T22:00:00-04:00', '2026-07-13T00:00:00+00:00', '2026-07-13T08:00:00+00:00'),
    ('2026-11-15T22:00:00-05:00', '2026-11-16T01:00:00+00:00', '2026-11-16T09:00:00+00:00'),
    ('2026-11-16T03:59:59-05:00', '2026-11-16T01:00:00+00:00', '2026-11-16T09:00:00+00:00'),
])
def test_overnight_window_follows_dst_and_midnight(now, start, end):
    assert overnight_period(at(now).timestamp()) == {'start': at(start).timestamp(), 'end': at(end).timestamp()}


@pytest.mark.parametrize('now', ['2026-11-13T22:00:00-05:00', '2026-11-14T22:00:00-05:00',
                                '2026-11-25T22:00:00-05:00', '2026-11-16T04:00:00-05:00'])
def test_weekends_holidays_and_premarket_are_not_overnight(now):
    assert overnight_period(at(now).timestamp()) == {}


@pytest.mark.parametrize('change', [
    {'overnightMarketTime': None, 'fulldayPrice': 999},
    {'overnightMarketTime': at('2026-11-13T19:58:00-05:00').timestamp()},
    {'overnightMarketTime': at('2026-11-16T22:00:00-05:00').timestamp()},
    {'overnightMarketPrice': float('nan')}, {'overnightMarketPrice': True},
    {'currency': 'HKD'}, {'symbol': 'OTHER'},
])
def test_missing_stale_future_and_unmatched_quotes_never_become_live_overnight(change):
    quote = us_quote_fields(chart(), now=at('2026-11-15T22:00:00-05:00'), latest={**snapshot(), **change})
    assert quote['price'] == 111
    assert quote['status'] == 'stale'
    assert quote['market_session'] == 'overnight'
    assert '夜盘' in quote['quote_error']


def test_newer_regular_minute_and_premarket_snapshot_are_not_ignored():
    data = chart()
    data['meta']['regularMarketTime'] = at('2026-11-13T10:00:00-05:00').timestamp()
    data['timestamp'] = [at('2026-11-13T10:29:00-05:00').timestamp()]
    data['indicators']['quote'][0]['close'] = [112]
    quote = us_quote_fields(data, now=at('2026-11-13T10:30:00-05:00'))
    assert quote['price'] == quote['regular_price'] == 112
    assert quote['status'] == 'live'
    latest = {**snapshot(), 'preMarketPrice': 118, 'preMarketTime': at('2026-11-16T04:00:30-05:00').timestamp(), 'exchangeDataDelayedBy': 0}
    quote = us_quote_fields(chart(), now=at('2026-11-16T04:01:00-05:00'), latest=latest)
    assert (quote['price'], quote['quote_session'], quote['status']) == (118, 'pre', 'live')


def test_delayed_source_is_not_live_and_does_not_trigger_extended_alert():
    now = at('2026-11-16T04:01:00-05:00')
    quote = us_quote_fields(chart(), now=now, latest={**snapshot(), 'preMarketPrice': 118,
        'preMarketTime': at('2026-11-16T04:00:30-05:00').timestamp(), 'exchangeDataDelayedBy': 15})
    assert quote['status'] == 'delayed'
    assert quote['quote_error'] == ''
    item = {'market': 'US', 'symbol': 'TEST', 'name': 'Test', **quote, 'quote_status': quote['status']}
    assert build_extended_monitor([item], now=now)['triggered_count'] == 0


def test_fetcher_connects_session_feed_and_preserves_chart_when_it_fails():
    now = at('2026-11-15T22:00:00-05:00')
    for response, expected in [(snapshot(), 115.5), (OSError('offline'), 111)]:
        with patch('quant_news_monitor.adr_monitor.urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'chart': {'result': [chart()]}}).encode())), \
             patch('quant_news_monitor.us_sessions.utc_now', return_value=now), \
             patch('quant_news_monitor.adr_monitor._US_SESSION_QUOTES.fetch', side_effect=response if isinstance(response, Exception) else None, return_value=response):
            quote = _fetch_yahoo_quote('TEST', 2)
        assert quote['price'] == expected


@pytest.mark.parametrize('symbol,actual,currency,accepted', [
    ('CNY=X', 'USDCNY=X', 'CNY', True), ('HKD=X', 'USDHKD=X', 'HKD', True),
    ('KRW=X', 'USDKRW=X', 'KRW', True), ('CNY=X', 'USDCNH=X', 'CNY', False),
    ('CNY=X', 'USDCNY=X', 'USD', False), ('TEST', 'OTHER', 'USD', False),
])
def test_fx_aliases_keep_quote_identity_checks(symbol, actual, currency, accepted):
    data = chart()
    data['meta'].update(symbol=actual, currency=currency, instrumentType='CURRENCY')
    with patch('quant_news_monitor.adr_monitor.urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'chart': {'result': [data]}}).encode())):
        if accepted:
            assert _fetch_yahoo_quote(symbol, 2)['symbol'] == symbol
        else:
            with pytest.raises(ValueError, match='主体'):
                _fetch_yahoo_quote(symbol, 2)


def test_public_session_reuses_cookie_and_rejects_wrong_symbol():
    client = YahooSessionQuotes()
    wire = lambda row: io.BytesIO(json.dumps({'quoteResponse': {'result': [row]}}).encode())
    client._opener = MagicMock()
    client._opener.open.side_effect = [HTTPError('https://fc.yahoo.com', 404, '', {}, None),
                                     io.BytesIO(b'test-session'), wire(snapshot()), wire({**snapshot(), 'symbol': 'OTHER'})]
    assert client.fetch('TEST', 2)['symbol'] == 'TEST'
    assert 'overnightPrice=true' in client._opener.open.call_args.args[0]
    with pytest.raises(ValueError, match='代码不符'):
        client.fetch('TEST', 2)
    assert client._opener.open.call_count == 4


def test_auth_failure_resets_session_without_exposing_token():
    client = YahooSessionQuotes()
    client._crumb = 'do-not-log'
    client._opener = MagicMock()
    client._opener.open.side_effect = HTTPError('https://query1.finance.yahoo.com/?crumb=do-not-log', 401, '', {}, None)
    with pytest.raises(OSError) as failure:
        client.fetch('TEST', 2)
    assert 'do-not-log' not in str(failure.value)
    assert client._crumb is None


def test_portfolio_worker_refreshes_after_close_without_a_trading_hours_gate():
    monitor = PortfolioMonitor.__new__(PortfolioMonitor)
    monitor._stop_event = MagicMock()
    monitor._stop_event.is_set.side_effect = [False, False, True]
    monitor._wake_event = MagicMock()
    monitor._profiles = MagicMock()
    monitor.config_store = MagicMock()
    monitor.config_store.read.return_value = {}
    monitor.refresh = MagicMock()
    monitor.interval_seconds = 20
    monitor._loop()
    assert monitor.refresh.call_count == 2
    assert monitor._wake_event.wait.call_args.args == (20,)
