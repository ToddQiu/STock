import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from quant_news_monitor.us_sessions import build_extended_monitor, session_at, us_quote_fields
from quant_news_monitor.portfolio_monitor import build_portfolio_analysis
from quant_news_monitor.portfolio_chart import fetch_stock_candles, _vwap_series


def moment(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def payload(day="2026-09-08", offset="-04:00"):
    def ts(clock):
        return moment(f"{day}T{clock}{offset}").timestamp()
    return {"meta": {"exchangeTimezoneName": "America/New_York", "instrumentType": "EQUITY",
                     "regularMarketPrice": 100, "regularMarketTime": ts("04:00:00")-12*3600,
                     "previousClose": 95, "currentTradingPeriod": {
                         "pre": {"start": ts("04:00:00"), "end": ts("09:30:00")},
                         "regular": {"start": ts("09:30:00"), "end": ts("16:00:00")},
                         "post": {"start": ts("16:00:00"), "end": ts("20:00:00")}}},
            "timestamp": [ts("08:29:00")], "indicators": {"quote": [{"close": [103]}]}}


class USSessionTests(unittest.TestCase):
    def test_premarket_uses_latest_regular_close_not_older_previous_close(self):
        result = payload()
        quote = us_quote_fields(result, now=moment("2026-09-08T08:30:00-04:00"))
        self.assertEqual(quote["price"], 103)
        self.assertEqual(quote["previous_close"], 100)
        self.assertAlmostEqual(quote["change_pct"], 3)
        self.assertEqual(quote["quote_session"], "pre")
        self.assertEqual(quote["status"], "live")

    def test_postmarket_keeps_daily_and_extended_change_distinct(self):
        result = payload()
        result["meta"].update(regularMarketPrice=110, regularMarketTime=moment("2026-09-08T16:00:00-04:00").timestamp(), previousClose=100)
        result["timestamp"] = [moment("2026-09-08T16:29:00-04:00").timestamp()]
        result["indicators"]["quote"][0]["close"] = [115.5]
        quote = us_quote_fields(result, now=moment("2026-09-08T16:30:00-04:00"))
        self.assertEqual(quote["quote_session"], "post")
        self.assertAlmostEqual(quote["extended_change_pct"], 5)
        self.assertAlmostEqual(quote["change_pct"], 15.5)
        self.assertEqual(quote["previous_close"], 100)

    def test_regular_open_never_calls_a_premarket_bar_live(self):
        quote = us_quote_fields(payload(), now=moment("2026-09-08T09:30:00-04:00"))
        self.assertEqual(quote["market_session"], "regular")
        self.assertEqual(quote["status"], "stale")

    def test_future_null_and_nan_bars_are_not_prices(self):
        result = payload()
        result["timestamp"] *= 3
        result["timestamp"].append(moment("2026-09-08T10:00:00-04:00").timestamp())
        result["indicators"]["quote"][0]["close"] = [None, float("nan"), 103, 900]
        quote = us_quote_fields(result, now=moment("2026-09-08T08:30:00-04:00"))
        self.assertEqual(quote["price"], 103)

    def test_missing_extended_quote_and_old_periods_do_not_claim_live(self):
        result = payload()
        result["timestamp"] = []
        quote = us_quote_fields(result, now=moment("2026-09-08T08:30:00-04:00"))
        self.assertEqual(quote["price"], 100)
        self.assertEqual(quote["status"], "stale")
        self.assertIsNone(quote["previous_close"])
        self.assertIsNone(quote["change_pct"])
        old = us_quote_fields(result, now=moment("2026-09-12T08:30:00-04:00"))
        self.assertEqual(old["status"], "closed")

    def test_dst_and_short_session_follow_provider_boundaries(self):
        winter = payload("2026-11-27", "-05:00")
        periods = winter["meta"]["currentTradingPeriod"]
        close = moment("2026-11-27T13:00:00-05:00").timestamp()
        periods["regular"]["end"] = close
        periods["post"]["start"] = close
        self.assertEqual(session_at(periods, close), "post")
        self.assertEqual(session_at(periods, moment("2026-11-27T09:29:00-05:00").timestamp()), "pre")

    def test_non_us_instruments_are_unchanged(self):
        for field, value in (("exchangeTimezoneName", "Asia/Seoul"), ("instrumentType", "CURRENCY")):
            result = payload()
            result["meta"][field] = value
            self.assertEqual(us_quote_fields(result), {})

    def test_winter_afterhours_candles_keep_new_york_date_for_vwap(self):
        result = payload("2026-11-25", "-05:00")
        result["timestamp"] = [moment("2026-11-25T18:59:00-05:00").timestamp(), moment("2026-11-25T19:01:00-05:00").timestamp()]
        result["indicators"]["quote"][0] = {"open": [100, 102], "high": [100, 102], "low": [100, 102], "close": [100, 102], "volume": [10, 10]}
        with patch("quant_news_monitor.portfolio_chart._request_json", return_value={"chart": {"result": [result]}}), \
             patch("quant_news_monitor.portfolio_chart.time.time", return_value=moment("2026-11-25T19:02:00-05:00").timestamp()):
            chart = fetch_stock_candles("MU", "1d", mode="intraday")
        self.assertEqual([bar["date"] for bar in chart["candles"]], ["2026-11-25", "2026-11-25"])
        self.assertEqual([bar["session"] for bar in chart["candles"]], ["post", "post"])
        overlay = next(item for item in chart["technical"]["overlays"] if item["key"] == "vwap")
        self.assertEqual(overlay["values"][-1], 101)

    def test_intraday_vwap_resets_at_next_exchange_date(self):
        values = _vwap_series([{"date": "2026-11-25", "close": 100, "volume": 10}, {"date": "2026-11-27", "close": 120, "volume": 10}])
        self.assertEqual(values, [100, 120])

    def test_only_fresh_extended_holding_quotes_trigger(self):
        now = moment("2026-09-08T08:30:00-04:00")
        quote = us_quote_fields(payload(), now=now)
        item = {"market": "US", "symbol": "MU", "name": "Micron", **quote, "quote_status": quote["status"]}
        result = build_extended_monitor([item], now=now)
        self.assertEqual(result["triggered_count"], 1)
        self.assertIsNone(result["events"][0]["relative_volume"])
        self.assertEqual(result["events"][0]["confidence"], 0)
        self.assertEqual(result["events"][0]["session"], "pre")
        for update in ({"quote_status": "stale"}, {"extended_change_pct": 1.9}, {"quote_session": "post"}, {"market": "KR"}):
            self.assertEqual(build_extended_monitor([{**item, **update}], now=now)["triggered_count"], 0)
        self.assertEqual(build_extended_monitor([item], now=now+timedelta(minutes=4))["triggered_count"], 0)

    def test_portfolio_values_use_premarket_price_without_inventing_close_cycle_profit(self):
        quote = us_quote_fields(payload(), now=moment("2026-09-08T08:30:00-04:00"))
        positions = [{"market": "US", "symbol": "MU", "name": "Micron", "currency": "USD", "quantity": 10, "average_cost": 90}]
        result = build_portfolio_analysis(positions, {"quotes": [{"symbol": "MU", **quote}], "fx_rates": {"USD": 7}}, [], [])
        row = result["positions"][0]
        self.assertEqual(row["market_value_cny"], 7210)
        self.assertIsNone(row["daily_pnl_cny"])
        self.assertEqual(row["market_session"], "pre")


if __name__ == "__main__":
    unittest.main()
