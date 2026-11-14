import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quant_news_monitor.portfolio_monitor import PortfolioMonitor, build_portfolio_analysis


class PortfolioAnalysisTests(unittest.TestCase):
    def test_historical_fund_nav_is_not_todays_profit(self) -> None:
        position = {"market": "FUND", "symbol": "110022", "name": "Fund", "quantity": 100,
                    "average_cost": 1, "currency": "CNY"}
        quote = {"symbol": "110022", "price": 1.2, "previous_close": 1.1, "change_pct": 9.09,
                 "nav_date": "2026-09-08", "status": "live"}
        snapshot = {"quotes": [quote], "fx_rates": {"CNY": 1}}
        now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        result = build_portfolio_analysis([position], snapshot, [], [], now=now)
        row = result["positions"][0]
        self.assertAlmostEqual(row["latest_nav_pnl_cny"], 10)
        self.assertIsNone(row["daily_pnl_cny"])
        self.assertIsNone(result["summary"]["daily_pnl_cny"])
        self.assertEqual(result["summary"]["daily_pnl_count"], 0)
        self.assertFalse(result["summary"]["daily_pnl_complete"])
        for nav_date in ("2026-09-10", "2026-09-11", "", "invalid"):
            with self.subTest(nav_date=nav_date):
                quote["nav_date"] = nav_date
                row = build_portfolio_analysis([position], snapshot, [], [], now=now)["positions"][0]
                if nav_date == "2026-09-10":
                    self.assertAlmostEqual(row["daily_pnl_cny"], 10)
                else:
                    self.assertIsNone(row["daily_pnl_cny"])

    def test_stale_quotes_do_not_supply_daily_profit_or_trend_in_any_market(self) -> None:
        for market in ("CN", "HK", "KR", "US", "FUND"):
            with self.subTest(market=market):
                position = {"market": market, "symbol": "TEST", "name": "Test", "quantity": 10,
                            "average_cost": 10, "currency": "CNY"}
                snapshot = {"quotes": [{"symbol": "TEST", "price": 11, "previous_close": 10,
                                         "change_pct": 10, "status": "stale"}], "fx_rates": {"CNY": 1}}
                result = build_portfolio_analysis([position], snapshot, [], [])
                row = result["positions"][0]
                self.assertEqual(row["market_value_cny"], 110)
                self.assertIsNone(row["daily_pnl_cny"])
                self.assertEqual(row["signal_score"], 0)
                self.assertFalse(row["signal_available"])
                self.assertEqual(row["analysis"]["direction"], "待确认")
                self.assertEqual(result["summary"]["stale_valued_count"], 1)

    def test_daily_profit_coverage_is_separate_from_valuation_coverage(self) -> None:
        positions = [{"market": "CN", "symbol": "STOCK", "name": "Stock", "quantity": 10,
                      "average_cost": 10, "currency": "CNY"},
                     {"market": "FUND", "symbol": "110022", "name": "Fund", "quantity": 100,
                      "average_cost": 1, "currency": "CNY"}]
        snapshot = {"quotes": [{"symbol": "STOCK", "price": 11, "previous_close": 10, "status": "live", "quote_time": "2026-09-10T00:00:00+00:00"},
                                {"symbol": "110022", "price": 1.2, "previous_close": 1.1,
                                 "nav_date": "2026-09-08", "status": "live"}], "fx_rates": {"CNY": 1}}
        result = build_portfolio_analysis(positions, snapshot, [], [],
                                          now=datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.assertTrue(result["summary"]["valuation_complete"])
        self.assertFalse(result["summary"]["daily_pnl_complete"])
        self.assertEqual(result["summary"]["daily_pnl_count"], 1)
        self.assertEqual(result["summary"]["daily_pnl_cny"], 10)

    def test_delayed_daily_profit_requires_latest_session_and_valid_conversion(self) -> None:
        position = {"market": "CN", "symbol": "601169.SS", "quantity": 10,
                    "average_cost": 10, "currency": "CNY"}
        quote = {"symbol": position["symbol"], "price": 11, "previous_close": 10,
                 "currency": "CNY", "status": "delayed"}
        cases = [
            ("intraday", "2026-11-09T05:30:00", "2026-11-09T05:16:00", {}, "CNY", 10, 0),
            ("old intraday", "2026-11-09T05:30:00", "2026-11-09T05:09:00", {}, "CNY", None, 0),
            ("current close", "2026-11-09T08:00:00", "2026-11-09T07:27:00", {}, "CNY", 10, 0),
            ("weekend close", "2026-11-08T07:00:00", "2026-11-06T07:00:00", {}, "CNY", 10, 1),
            ("older close", "2026-11-08T07:00:00", "2026-11-05T07:00:00", {}, "CNY", None, 0),
            ("previous day during open", "2026-11-09T05:30:00", "2026-11-06T07:00:00", {}, "CNY", None, 0),
            ("restored", "2026-11-09T08:00:00", "2026-11-09T07:00:00", {"restored_from_cache": True}, "CNY", None, 0),
            ("invalid FX", "2026-11-09T08:00:00", "2026-11-09T07:00:00", {"fx_valid": False}, "HKD", None, 0),
        ]
        for label, current, quoted, flags, base, expected, prior in cases:
            with self.subTest(label=label):
                stamp = quoted + "+00:00"
                snapshot = {"status": "live", "quotes": [{**quote, "quote_time": stamp}],
                            "fx_rates": {"CNY": 1, "HKD": .9}, **flags}
                result = build_portfolio_analysis([position], snapshot, [], [],
                    now=datetime.fromisoformat(current + "+00:00"), account={"base_currency": base})
                summary = result["summary"]
                self.assertEqual(summary["daily_pnl_base"], expected)
                self.assertEqual(summary["daily_pnl_pct"], 10 if expected is not None else None)
                self.assertEqual(summary["daily_pnl_complete"], expected is not None)
                self.assertEqual(summary["daily_pnl_delayed_count"], int(expected is not None))
                self.assertEqual(summary["daily_pnl_prior_session_count"], prior)
                self.assertEqual(summary["daily_pnl_quote_time"], stamp if expected is not None else None)
                self.assertFalse(summary["valuation_current"])
                self.assertEqual(result["positions"][0]["quote_status"], "delayed")

    def test_daily_percent_weights_same_covered_positions_by_previous_value(self):
        positions = [{"market": "CN", "symbol": symbol, "quantity": 10,
                      "average_cost": 10, "currency": "CNY"} for symbol in ("601169.SS", "000543.SZ", "600000.SS")]
        quotes = [{"symbol": symbol, "price": price, "previous_close": previous,
                   "currency": "CNY", "status": status, "quote_time": "2026-11-09T05:20:00+00:00"}
                  for symbol, price, previous, status in (("601169.SS", 11, 10, "delayed"),
                    ("000543.SZ", 18, 20, "delayed"), ("600000.SS", 90, 100, "stale"))]
        summary = build_portfolio_analysis(positions, {"status": "live", "quotes": quotes, "fx_rates": {"CNY": 1}},
            [], [], now=datetime.fromisoformat("2026-11-09T05:30:00+00:00"), account={"base_currency": "CNY"})["summary"]
        self.assertEqual(summary["daily_pnl_base"], -10)
        self.assertEqual(summary["daily_pnl_count"], 2)
        self.assertFalse(summary["daily_pnl_complete"])
        self.assertAlmostEqual(summary["daily_pnl_pct"], -10 / 300 * 100)

    def test_analysis_calculates_value_profit_weight_and_signal(self) -> None:
        positions = [
            {
                "market": "US",
                "symbol": "MU",
                "name": "Micron Technology",
                "quantity": 10,
                "average_cost": 100,
                "currency": "USD",
            }
        ]
        snapshot = {
            "status": "live",
            "updated_at": "2026-09-04T00:00:00+00:00",
            "interval_seconds": 20,
            "fx_rates": {"CNY": 1.0, "USD": 7.2},
            "quotes": [
                {
                    "symbol": "MU",
                    "price": 120,
                    "previous_close": 115,
                    "change_pct": 4.3478,
                    "status": "live",
                    "source_url": "https://finance.yahoo.com/quote/MU/",
                }
            ],
            "errors": [],
        }
        news = [{"market": "US", "symbol": "MU", "score": 0.5}]
        watchlist = [
            {
                "market": "US",
                "symbol": "MU",
                "trump_factor": {"score": 0.2},
            }
        ]

        result = build_portfolio_analysis(positions, snapshot, news, watchlist)
        item = result["positions"][0]
        summary = result["summary"]

        self.assertEqual(item["market_value"], 1200)
        self.assertEqual(item["pnl"], 200)
        self.assertEqual(item["market_value_cny"], 8640)
        self.assertEqual(item["pnl_cny"], 1440)
        self.assertEqual(item["weight_pct"], 100)
        self.assertEqual(item["analysis"]["direction"], "偏多")
        self.assertEqual(summary["total_market_value_cny"], 8640)
        self.assertEqual(summary["total_cost_cny"], 7200)
        self.assertEqual(summary["total_pnl_cny"], 1440)
        self.assertEqual(summary["total_pnl_pct"], 20)

    def test_missing_quote_is_excluded_from_profit_not_reported_as_loss(self) -> None:
        positions = [
            {
                "market": "CN",
                "symbol": "600000.SS",
                "name": "浦发银行",
                "quantity": 100,
                "average_cost": 10,
                "currency": "CNY",
            }
        ]
        result = build_portfolio_analysis(
            positions,
            {"fx_rates": {"CNY": 1.0}, "quotes": [], "errors": ["no quote"]},
            [],
            [],
        )

        self.assertEqual(result["summary"]["total_cost_cny"], 1000)
        self.assertEqual(result["summary"]["priced_cost_cny"], 0)
        self.assertIsNone(result["summary"]["total_pnl_cny"])
        self.assertIsNone(result["summary"]["total_pnl_pct"])
        self.assertFalse(result["summary"]["valuation_complete"])
        self.assertIsNone(result["positions"][0]["pnl"])

    def test_fund_position_uses_confirmed_nav_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "portfolio": {
                            "positions": [
                                {
                                    "market": "FUND",
                                    "symbol": "110022",
                                    "name": "易方达消费行业股票",
                                    "quantity": 1000,
                                    "average_cost": 2.8,
                                    "currency": "CNY",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            monitor = PortfolioMonitor(str(path))
            quote = {
                "symbol": "110022",
                "price": 2.962,
                "previous_close": 2.922,
                "change_pct": 1.3689,
                "quote_time": "2026-09-04T23:00:00+08:00",
                "nav_date": "2026-09-04",
                "price_kind": "confirmed_nav",
                "price_label": "已确认净值",
                "source": "天天基金",
                "source_url": "https://fund.eastmoney.com/110022.html",
            }
            with patch(
                "quant_news_monitor.portfolio_monitor.fetch_china_fund_quote",
                return_value=quote,
            ) as fund_quote, patch(
                "quant_news_monitor.portfolio_monitor._fetch_yahoo_quote"
            ) as yahoo_quote:
                snapshot = monitor.refresh()

        fund_quote.assert_called_once_with("110022", 10)
        yahoo_quote.assert_not_called()
        self.assertEqual(snapshot["status"], "live")
        self.assertEqual(snapshot["quotes"][0]["price_kind"], "confirmed_nav")
        self.assertEqual(snapshot["quotes"][0]["source"], "天天基金")


if __name__ == "__main__":
    unittest.main()
