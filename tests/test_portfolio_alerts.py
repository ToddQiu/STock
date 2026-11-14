import copy
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from quant_news_monitor.config_store import ConfigStore
from quant_news_monitor.portfolio_alerts import evaluate_risk, normalize_risk_settings, quote_usable
from quant_news_monitor.portfolio_monitor import PortfolioMonitor, build_portfolio_analysis
from quant_news_monitor.startup_cache import SnapshotStore


NOW = datetime(2026, 10, 16, 14, tzinfo=timezone.utc)


class PortfolioRiskTests(unittest.TestCase):
    def setUp(self):
        self.positions = [{"market": "US", "symbol": "TEST", "name": "Test", "quantity": 10,
                           "average_cost": 100, "currency": "USD", "created_at": "fixture",
                           "risk_settings": normalize_risk_settings({"stop_price": 80, "weight_max_pct": 70, "weight_min_pct": 20})},
                          {"market": "CN", "symbol": "600000.SS", "name": "Other", "quantity": 100,
                           "average_cost": 10, "currency": "CNY"}]
        self.snapshot = {"status": "live", "fx_rates": {"USD": 1, "CNY": 1}, "fx_valid": True,
                         "quotes": [{"symbol": "TEST", "price": 80, "quote_time": NOW.isoformat(), "status": "live"},
                                    {"symbol": "600000.SS", "price": 10, "quote_time": NOW.isoformat(), "status": "live"}]}

    def evaluate(self, previous=None, now=NOW):
        analysis = build_portfolio_analysis(self.positions, self.snapshot, [], [], now=now)
        return evaluate_risk(analysis, self.snapshot, previous or {}, now)

    def test_validation_and_disable(self):
        self.assertIsNone(normalize_risk_settings({})["stop_price"])
        for value in (True, False, "nan", "inf", -1, 0, 1e13, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_risk_settings({"stop_price": value})
        for values in ({"weight_max_pct": 101}, {"weight_min_pct": -1}, {"enabled": "yes"},
                       {"weight_min_pct": 50, "weight_max_pct": 50}, None, []):
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_risk_settings(values)
        self.positions[0]["risk_settings"]["enabled"] = False
        state, events = self.evaluate()
        self.assertEqual(state["states"], {})
        self.assertEqual(events, [])

    def test_inclusive_stop_and_weight_limits(self):
        _, events = self.evaluate()
        self.assertEqual([e["kind"] for e in events], ["stop_price"])
        self.snapshot["quotes"][0]["price"] = 100
        self.positions[0]["risk_settings"]["weight_max_pct"] = 50
        _, events = self.evaluate()
        self.assertEqual([e["kind"] for e in events], ["weight_max_pct"])
        self.positions[0]["risk_settings"]["weight_max_pct"] = None
        self.positions[0]["risk_settings"]["weight_min_pct"] = 50
        self.assertEqual(self.evaluate()[1][0]["kind"], "weight_min_pct")

    def test_fixed_take_profit_uses_original_price_and_rejects_a_second_profit_rule(self):
        with self.assertRaises(ValueError):
            normalize_risk_settings({'take_profit_price': 115, 'take_profit_pct': 15})
        self.positions[0]['average_cost'] = 200
        self.positions[0]['risk_settings'] = normalize_risk_settings({'take_profit_price': 115})
        self.snapshot['quotes'][0]['price'] = 114.99
        self.assertFalse(self.evaluate()[1])
        self.snapshot['quotes'][0]['price'] = 115
        _, events = self.evaluate()
        self.assertEqual([event['kind'] for event in events], ['take_profit_price'])
        self.assertIn('≥ 115 USD', events[0]['reason_summary'])

    def test_fresh_reference_quotes_trigger_price_rules_without_claiming_realtime(self):
        quote = self.snapshot['quotes'][0]
        quote.update(status='delayed', source='公开参考', quote_time=(NOW-timedelta(seconds=3)).isoformat())
        for rule, threshold, price in [('stop_price', 80, 80), ('take_profit_price', 110, 110),
                                       ('stop_loss_pct', 5, 95), ('take_profit_pct', 10, 110)]:
            with self.subTest(rule=rule):
                self.positions[0]['risk_settings'] = normalize_risk_settings({rule: threshold})
                quote['price'] = price
                state, events = self.evaluate()
                self.assertEqual([e['kind'] for e in events], [rule])
                self.assertIn('参考报价', events[0]['reason_summary'])
                self.assertIn('UTC+8', events[0]['reason_summary'])
                self.assertEqual(self.evaluate(state)[1], [])
        self.assertFalse(quote_usable(quote, NOW))  # Other valuation callers retain their contract.
        for values in ({'quote_time': (NOW-timedelta(minutes=21)).isoformat()},
                       {'quote_time': None}, {'quote_time': (NOW+timedelta(minutes=6)).isoformat()},
                       {'error': 'failed'}, {'status': 'stale'}):
            original = dict(quote)
            quote.update(values)
            self.assertEqual(self.evaluate()[1], [])
            quote.clear(); quote.update(original)
        self.snapshot['restored_from_cache'] = True
        self.assertEqual(self.evaluate()[1], [])

    def test_percentage_validation(self):
        for kind in ("take_profit_pct", "stop_loss_pct"):
            self.assertIsNone(normalize_risk_settings({kind: ""})[kind])
            self.assertEqual(normalize_risk_settings({kind: "5.5"})[kind], 5.5)
            for value in (True, False, "nan", "inf", -5, 0, 0.0000001, [], {}):
                with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                    normalize_risk_settings({kind: value})
        self.assertEqual(normalize_risk_settings({"take_profit_pct": 250})["take_profit_pct"], 250)
        self.assertEqual(normalize_risk_settings({"stop_loss_pct": 100})["stop_loss_pct"], 100)
        for values in ({"stop_loss_pct": 100.01}, {"stop_loss_pct": 5, "stop_price": 80}):
            with self.assertRaises(ValueError):
                normalize_risk_settings(values)

    def test_percentages_use_cost_not_daily_change_and_include_exact_boundaries(self):
        self.positions[0]["average_cost"] = 1.03
        self.positions[0]["risk_settings"] = normalize_risk_settings({"take_profit_pct": 10, "stop_loss_pct": 5})
        quote = self.snapshot["quotes"][0]
        quote.update(previous_close=9, change_pct=-90)
        for price, kind, value in ((1.133, "take_profit_pct", 10), (0.9785, "stop_loss_pct", -5)):
            quote["price"] = price
            _, events = self.evaluate()
            self.assertEqual([e["kind"] for e in events], [kind])
            self.assertAlmostEqual(events[0]["value"], value)
            self.assertIn("较平均成本收益", events[0]["reason_summary"])
            self.assertIn("≤ -5" if value < 0 else "≥ 10", events[0]["reason_summary"])
        for price in (1.1329, 0.9786, 1.03):
            quote["price"] = price
            self.assertEqual(self.evaluate()[1], [])

    def test_percentage_crossings_recover_and_respect_cooldown(self):
        for kind, threshold, hit, near, recovered in (
                ("take_profit_pct", 10, 110, 109.9, 109),
                ("stop_loss_pct", 5, 95, 95.1, 96)):
            with self.subTest(kind=kind):
                self.positions[0]["risk_settings"] = normalize_risk_settings({kind: threshold})
                quote = self.snapshot["quotes"][0]
                quote["price"] = hit
                state, events = self.evaluate()
                self.assertEqual(len(events), 1)
                self.assertEqual(self.evaluate(state)[1], [])
                quote["price"] = near
                state, _ = self.evaluate(state)
                self.assertEqual(state["states"][f"US:TEST:{kind}"]["status"], "recovering")
                quote["price"] = recovered
                state, _ = self.evaluate(state)
                self.assertFalse(state["states"][f"US:TEST:{kind}"]["active"])
                quote["price"] = hit
                state, events = self.evaluate(state, NOW + timedelta(seconds=60))
                self.assertEqual(events, [])
                self.assertTrue(state["states"][f"US:TEST:{kind}"]["pending_alert"])
                self.assertEqual(len(self.evaluate(state, NOW + timedelta(seconds=301))[1]), 1)

    def test_percentage_rules_pause_on_bad_cost_or_quote_but_do_not_need_fx(self):
        self.positions[0]["risk_settings"] = normalize_risk_settings({"take_profit_pct": 10, "stop_loss_pct": 5})
        for cost in (None, 0, -1, float("nan"), float("inf"), True):
            with self.subTest(cost=cost):
                self.positions[0]["average_cost"] = cost
                state, events = self.evaluate()
                self.assertEqual(events, [])
                self.assertEqual(len(state["states"]), 2)
                self.assertTrue(all(s["status"] == "paused" and "成本" in s["reason"] for s in state["states"].values()))
        self.positions[0]["average_cost"] = 100
        self.snapshot["fx_valid"] = False
        self.snapshot["quotes"].pop()
        state, events = self.evaluate()
        self.assertEqual([e["kind"] for e in events], ["stop_loss_pct"])
        self.snapshot["quotes"][0]["status"] = "stale"
        paused, events = self.evaluate(state)
        self.assertEqual(events, [])
        self.assertTrue(paused["states"]["US:TEST:stop_loss_pct"]["active"])
        self.assertEqual(paused["states"]["US:TEST:stop_loss_pct"]["status"], "paused")
        self.snapshot["quotes"][0]["status"] = "live"
        self.snapshot["restored_from_cache"] = True
        self.assertEqual(self.evaluate()[1], [])

    def test_percentage_rules_persist_and_follow_average_cost_after_buy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            store = ConfigStore(path)
            saved = store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"take_profit_pct": 20, "stop_loss_pct": 10}})
            self.assertIsNone(saved["risk_settings"]["stop_price"])
            self.positions = store.read()["portfolio"]["positions"]
            self.snapshot["quotes"][0]["price"] = 90
            state, events = self.evaluate()
            self.assertEqual(events[0]["kind"], "stop_loss_pct")
            store.adjust_position({"market": "US", "symbol": "TEST", "action": "buy", "quantity": 10, "execution_price": 80})
            self.positions = ConfigStore(path).read()["portfolio"]["positions"]
            self.assertEqual(self.positions[0]["average_cost"], 90)
            self.assertEqual(self.positions[0]["risk_settings"], saved["risk_settings"])
            state, events = self.evaluate(state)
            self.assertEqual(events, [])
            self.assertEqual(state["states"]["US:TEST:stop_loss_pct"]["status"], "watching")
            self.snapshot["quotes"][0]["price"] = 81
            self.assertEqual(self.evaluate(state)[1][0]["kind"], "stop_loss_pct")

    def test_confirmed_fund_nav_supports_percentage_take_profit(self):
        self.positions[0].update(market="FUND", symbol="000001", average_cost=1)
        self.positions[0]["risk_settings"] = normalize_risk_settings({"take_profit_pct": 20})
        self.snapshot["quotes"][0].update(symbol="000001", price=1.2, quote_time=(NOW-timedelta(days=2)).isoformat())
        _, events = self.evaluate()
        self.assertEqual([e["kind"] for e in events], ["take_profit_pct"])
        self.assertEqual(events[0]["quote_time"], self.snapshot["quotes"][0]["quote_time"])

    def test_no_duplicate_hysteresis_cooldown_and_rearm(self):
        state, events = self.evaluate()
        self.assertEqual(len(events), 1)
        self.assertEqual(self.evaluate(state)[1], [])
        self.snapshot["quotes"][0]["price"] = 80.1
        state, _ = self.evaluate(state)
        self.assertEqual(state["states"]["US:TEST:stop_price"]["status"], "recovering")
        self.snapshot["quotes"][0]["price"] = 79
        self.assertEqual(self.evaluate(state)[1], [])
        self.snapshot["quotes"][0]["price"] = 81
        state, _ = self.evaluate(state)
        self.assertFalse(state["states"]["US:TEST:stop_price"]["active"])
        self.snapshot["quotes"][0]["price"] = 79
        state, events = self.evaluate(state, NOW + timedelta(seconds=60))
        self.assertEqual(events, [])
        self.assertTrue(state["states"]["US:TEST:stop_price"]["pending_alert"])
        state, events = self.evaluate(state, NOW + timedelta(seconds=301))
        self.assertEqual(len(events), 1)
        self.assertEqual(len(state["events"]), 2)

    def test_stale_missing_and_cached_inputs_never_trigger_or_rearm(self):
        active, _ = self.evaluate()
        for field, value in (("status", "stale"), ("price", None), ("price", 0), ("price", float("nan")),
                             ("quote_time", None), ("quote_time", (NOW-timedelta(days=30)).isoformat())):
            with self.subTest(field=field, value=value):
                snapshot = copy.deepcopy(self.snapshot)
                self.snapshot["quotes"][0][field] = value
                state, events = self.evaluate(active)
                self.assertEqual(events, [])
                self.assertEqual(state["states"]["US:TEST:stop_price"]["status"], "paused")
                self.assertTrue(state["states"]["US:TEST:stop_price"]["active"])
                self.snapshot = snapshot
        self.snapshot["restored_from_cache"] = True
        self.assertEqual(self.evaluate()[1], [])

    def test_incomplete_valuation_or_fx_pauses_weight_but_not_stop(self):
        self.positions[0]["risk_settings"]["weight_max_pct"] = 30
        for mode in ("missing", "stale", "fx"):
            with self.subTest(mode=mode):
                snapshot = copy.deepcopy(self.snapshot)
                if mode == "missing":
                    self.snapshot["quotes"].pop()
                elif mode == "stale":
                    self.snapshot["quotes"][1]["status"] = "stale"
                else:
                    self.snapshot["fx_valid"] = False
                state, events = self.evaluate()
                self.assertEqual([e["kind"] for e in events], ["stop_price"])
                self.assertEqual(state["states"]["US:TEST:weight_max_pct"]["status"], "paused")
                self.snapshot = snapshot

    def test_closing_price_and_confirmed_nav_keep_source_time(self):
        quote = self.snapshot["quotes"][0]
        quote.update(status="closed", quote_time=(NOW-timedelta(days=2)).isoformat())
        state, events = self.evaluate()
        self.assertEqual(events[0]["quote_time"], quote["quote_time"])
        self.assertEqual(self.evaluate(state)[1], [])
        quote["status"] = "live"
        self.assertFalse(quote_usable(quote, NOW))
        self.assertTrue(quote_usable(quote, NOW, fund=True))
        quote["quote_time"] = (NOW+timedelta(hours=1)).isoformat()
        self.assertFalse(quote_usable(quote, NOW, fund=True))

    def test_deleted_holdings_prune_states_but_keep_bounded_history(self):
        state, _ = self.evaluate()
        self.positions = []
        state["events"] *= 110
        result, events = self.evaluate(state)
        self.assertEqual(result["states"], {})
        self.assertEqual(len(result["events"]), 100)
        self.assertEqual(events, [])

    def test_persistence_adjustment_and_normal_restart_do_not_replay(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            store = ConfigStore(path)
            before = store.read()
            with self.assertRaises(ValueError):
                store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"stop_price": "NaN"}})
            self.assertEqual(before, store.read())
            result = store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"stop_price": 80}})
            revision = result["risk_revision"]
            self.assertEqual(store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"stop_price": 80}})["risk_revision"], revision)
            store.adjust_position({"market": "US", "symbol": "TEST", "action": "buy", "quantity": 1, "execution_price": 90})
            self.assertEqual(store.read()["portfolio"]["positions"][0]["risk_settings"]["stop_price"], 80)
            snapshots = SnapshotStore(root / "test.db")
            monitor = PortfolioMonitor(str(path), now=lambda: NOW, snapshot_store=snapshots)
            monitor._notifier = Mock(available=True)
            monitor._notifier.notify.return_value = (True, None)
            monitor._save(self.snapshot)
            monitor._notifier.notify.assert_called_once()
            restarted = PortfolioMonitor(str(path), now=lambda: NOW, snapshot_store=snapshots)
            restarted._notifier = Mock(available=True)
            restarted._notifier.notify.return_value = (True, None)
            restarted._save(self.snapshot)
            restarted._notifier.notify.assert_not_called()
            self.assertEqual(len(restarted.snapshot()["risk"]["events"]), 1)
            store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"stop_price": 80, "enabled": False}})
            store.set_position_risk({"market": "US", "symbol": "TEST", "risk_settings": {"stop_price": 80}})
            restarted._save(self.snapshot)
            restarted._notifier.notify.assert_called_once()

    def test_notification_failure_does_not_drop_quote_or_event(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            monitor = PortfolioMonitor(str(path), now=lambda: NOW, snapshot_store=SnapshotStore(root / "test.db"))
            monitor._notifier = Mock(available=True)
            monitor._notifier.notify.side_effect = OSError("failed")
            monitor._save(self.snapshot)
            self.assertEqual(monitor.snapshot()["status"], "live")
            self.assertEqual(monitor.snapshot()["risk"]["events"][0]["notification"], "failed")
            self.assertTrue(monitor.snapshot()["risk"]["notification_error"])

    def test_notification_failure_is_visible_per_portfolio_and_retries_after_cooldown(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'portfolios': [{'id': 'test', 'name': '测试组合',
                                                       'positions': self.positions}]}), encoding='utf-8')
            clock = [NOW]
            monitor = PortfolioMonitor(str(path), now=lambda: clock[0])
            monitor._notifier = Mock(available=True)
            monitor._notifier.notify.return_value = (False, '系统通知脚本失败')
            monitor._save(self.snapshot)
            self.assertEqual(monitor.snapshot('test')['risk']['notification_error'], '系统通知脚本失败')
            monitor._save(self.snapshot)
            monitor._notifier.notify.assert_called_once()
            self.assertTrue(monitor.snapshot('test')['risk']['notification_error'])
            clock[0] += timedelta(seconds=301)
            monitor._notifier.notify.return_value = (True, None)
            monitor._save(self.snapshot)
            self.assertEqual(monitor._notifier.notify.call_count, 2)
            self.assertIsNone(monitor.snapshot('test')['risk']['notification_error'])
            monitor._save(self.snapshot)
            self.assertEqual(monitor._notifier.notify.call_count, 2)

    def test_notification_submission_does_not_block_dashboard_snapshot(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'portfolio': {'positions': self.positions}}), encoding='utf-8')
            monitor = PortfolioMonitor(str(path), now=lambda: NOW)
            entered, release, read = threading.Event(), threading.Event(), threading.Event()
            def notify(*args):
                entered.set()
                release.wait(2)
                return True, None
            monitor._notifier = Mock(available=True, notify=notify)
            worker = threading.Thread(target=monitor._save, args=(self.snapshot,))
            worker.start()
            reader = threading.Thread(target=lambda: (monitor.snapshot(), read.set()))
            try:
                self.assertTrue(entered.wait(1))
                reader.start()
                self.assertTrue(read.wait(1), 'Windows notification held the dashboard lock')
            finally:
                release.set()
                worker.join(2)
                if reader.ident is not None:
                    reader.join(2)

    def test_storage_failure_and_hosted_mode_never_send_native_notification(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            for allowed in (True, False):
                monitor = PortfolioMonitor(str(path), now=lambda: NOW, snapshot_store=SnapshotStore(root / f"test-{allowed}.db"), system_notifications_allowed=allowed)
                monitor._notifier = Mock(available=True)
                with patch.object(monitor._snapshot_store, "save", return_value=not allowed):
                    monitor._save(self.snapshot)
                monitor._notifier.notify.assert_not_called()
                self.assertEqual(len(monitor.snapshot()["risk"]["events"]), 1)
                self.assertEqual(bool(monitor.snapshot()["risk"]["storage_error"]), allowed)
                self.assertEqual(monitor.snapshot()["risk"]["system_notifications_available"], allowed)

    def test_non_us_market_close_retains_last_quote_without_accepting_open_staleness(self):
        quote = {"symbol": "600000.SS", "status": "live", "price": 10,
                 "quote_time": NOW.replace(hour=7).isoformat()}
        self.assertTrue(quote_usable(quote, NOW, market="CN"))
        quote["quote_time"] = NOW.replace(hour=1).isoformat()
        self.assertFalse(quote_usable(quote, NOW.replace(hour=2), market="CN"))

    def test_configuration_read_failure_does_not_kill_refresh(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            monitor = PortfolioMonitor(str(path), now=lambda: NOW)
            with patch.object(monitor.config_store, "read", side_effect=OSError("temporary error")):
                result = monitor.refresh()
            self.assertEqual(result["status"], "error")
            self.assertTrue(monitor.snapshot()["risk"]["storage_error"])
            self.assertFalse(monitor.snapshot()["refresh_in_progress"])

    def test_real_refresh_pipeline_batches_notifications_and_checks_fx(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.positions[0]["risk_settings"]["weight_max_pct"] = 30
            path.write_text(json.dumps({"portfolio": {"positions": self.positions}}), encoding="utf-8")
            monitor = PortfolioMonitor(str(path), now=lambda: NOW)
            monitor._notifier = Mock(available=True)
            monitor._notifier.notify.return_value = (True, None)
            quotes = {q["symbol"]: q for q in self.snapshot["quotes"]}
            quotes["CNY=X"] = {"symbol": "CNY=X", "price": 1, "quote_time": NOW.isoformat(), "status": "live"}
            with patch("quant_news_monitor.portfolio_monitor._fetch_yahoo_quote", side_effect=lambda symbol, timeout: quotes[symbol]), \
                 patch("quant_news_monitor.portfolio_monitor._fetch_position_quote", side_effect=lambda symbol, timeout: quotes[symbol]):
                monitor.refresh()
                self.assertEqual(len(monitor.snapshot()["risk"]["events"]), 2)
                monitor._notifier.notify.assert_called_once()
                monitor.refresh()
                monitor._notifier.notify.assert_called_once()
                quotes["CNY=X"]["quote_time"] = (NOW-timedelta(days=3)).isoformat()
                monitor.refresh()
                self.assertFalse(monitor.snapshot()["fx_valid"])
                self.assertEqual(monitor.snapshot()["risk"]["states"]["US:TEST:weight_max_pct"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
