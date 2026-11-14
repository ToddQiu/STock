import unittest
from datetime import datetime
from quant_news_monitor.portfolio_monitor import build_portfolio_analysis
from quant_news_monitor.us_sessions import us_quote_fields


def at(value):
    return datetime.fromisoformat(value)


class PortfolioDailySessionTests(unittest.TestCase):
    def calculate(self, now, close, *, previous=100, fx_status='delayed', error=None):
        quote = dict(symbol='TEST', currency='USD', price=120, previous_close=100,
                     quote_time=close, status='closed', quote_session='post', market_session='closed',
                     quote_error='休市，保留最近成交', error=error, regular_price=110,
                     regular_previous_close=previous, regular_quote_time=close)
        snapshot = dict(status='live', quotes=[quote], fx_rates={'USD':7},
                        fx_details={'USD_CNY':dict(price=7, status=fx_status, quote_time=close)})
        return build_portfolio_analysis([dict(market='US', symbol='TEST', currency='USD',
            quantity=10, average_cost=90)], snapshot, [], [], now=at(now), account={'base_currency':'CNY'})

    def test_close_cycle_follows_dst_weekend_and_early_close(self):
        cases = [
            ('summer', '2026-07-11T08:00:00+08:00', '2026-07-10T20:00:00+00:00', '2026-07-09T20:00:00+00:00'),
            ('winter', '2026-11-14T08:00:00+08:00', '2026-11-13T21:00:00+00:00', '2026-11-12T21:00:00+00:00'),
            ('weekend', '2026-11-15T12:00:00+08:00', '2026-11-13T21:00:00+00:00', '2026-11-12T21:00:00+00:00'),
            ('early close', '2026-11-28T08:00:00+08:00', '2026-11-27T18:00:00+00:00', '2026-11-25T21:00:00+00:00'),
        ]
        for name, now, close, start in cases:
            with self.subTest(name=name):
                result = self.calculate(now, close)
                summary, row = result['summary'], result['positions'][0]
                self.assertEqual(summary['daily_pnl_base'], 700)
                self.assertEqual(row['daily_pnl_cny'], 700)
                self.assertEqual(summary['daily_pnl_pct'], 10)
                self.assertAlmostEqual(row['daily_change_pct'], 10)
                self.assertEqual(summary['total_market_value_base'], 8400)  # Latest after-hours valuation is separate.
                self.assertEqual(summary['daily_pnl_periods'], [dict(market='US', start=start, end=close, closed=True)])
                self.assertEqual(summary['daily_pnl_fx_reference_count'], 1)
                self.assertFalse(row['valuation_current'])

    def test_winter_four_am_is_still_intraday_and_five_am_is_close(self):
        result = self.calculate('2026-11-14T04:00:00+08:00', '2026-11-13T20:00:00+00:00', fx_status='live')
        period = result['summary']['daily_pnl_periods'][0]
        self.assertFalse(period['closed'])
        self.assertEqual(period['end'], '2026-11-14T04:00:00+08:00')

    def test_missing_baseline_old_close_and_failed_fx_are_not_zero_returns(self):
        for kwargs in ({'previous':None}, {'fx_status':'stale'}, {'error':'network failed'}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.calculate('2026-11-14T12:00:00+08:00', '2026-11-13T21:00:00+00:00', **kwargs)['summary']['daily_pnl_base'])
        self.assertIsNone(self.calculate('2026-11-14T12:00:00+08:00', '2026-11-12T21:00:00+00:00')['summary']['daily_pnl_base'])

    def test_premarket_does_not_reinterpret_latest_close_as_its_own_previous_close(self):
        close = at('2026-11-13T21:00:00+00:00').timestamp()
        opening = at('2026-11-16T14:30:00+00:00').timestamp()
        payload = {'meta': {'exchangeTimezoneName':'America/New_York', 'instrumentType':'ETF',
            'regularMarketPrice':110, 'regularMarketTime':close, 'previousClose':110,
            'currentTradingPeriod':{'regular':{'start':opening,'end':opening+23400}}}}
        fields = us_quote_fields(payload, now=at('2026-11-16T13:00:00+00:00'))
        self.assertIsNone(fields['regular_previous_close'])

    def test_monitor_preserves_baseline_only_for_the_same_regular_quote(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from quant_news_monitor.portfolio_monitor import PortfolioMonitor
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'portfolio':{'positions':[dict(market='US',symbol='TEST',
                currency='USD',quantity=10,average_cost=90)]}}), encoding='utf8')
            monitor = PortfolioMonitor(str(path), system_notifications_allowed=False)
            old = dict(symbol='TEST',price=110,regular_price=110,regular_previous_close=100,
                       regular_quote_time='2026-11-13T21:00:00+00:00')
            monitor._snapshot['quotes'] = [old]
            new = {**old, 'regular_previous_close':None}
            with patch('quant_news_monitor.portfolio_monitor._fetch_position_quote', return_value=new), \
                 patch('quant_news_monitor.portfolio_monitor._fetch_yahoo_quote', return_value={'price':7}):
                self.assertEqual(monitor.refresh()['quotes'][0]['regular_previous_close'], 100)
                new['regular_previous_close'] = None
                new['regular_quote_time'] = '2026-11-16T14:30:00+00:00'
                self.assertIsNone(monitor.refresh()['quotes'][0]['regular_previous_close'])


if __name__ == '__main__':
    unittest.main()
