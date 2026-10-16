import base64
import json
import secrets
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quant_news_monitor.hosted import (
    HostedServer, HostedState, create_access_key, initialize_workspace, read_access_key, validate_origin,
)

ROOT = Path(__file__).resolve().parents[1]


class HostedInitializationTests(unittest.TestCase):
    def test_origin_is_explicit_https_without_credentials_or_paths(self):
        self.assertEqual(validate_origin("https://stock.example.com"), "https://stock.example.com")
        for value in ("", "http://stock.example.com", "https://localhost", "https://127.0.0.1",
                      "https://u:p@stock.example.com", "https://stock.example.com/",
                      "https://stock.example.com/a", "https://stock.example.com?x=1",
                      "https://stock.example.com:8080", "https://bad_host.example"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_origin(value)

    def test_workspace_never_reuses_personal_data_and_preserves_hosted_data(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = ROOT / "desktop/default-config.json"
            if not template.exists():
                template = ROOT / "config.example.json"
            personal = root / "personal"
            personal.mkdir()
            (personal / "config.json").write_text('{"private":"do not publish"}')
            with self.assertRaises(ValueError):
                initialize_workspace(personal, template)
            hosted = root / "hosted"
            config = initialize_workspace(hosted, template)
            raw = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(raw["portfolio"], {"positions": [], "principal_cny": 0})
            raw["portfolio"]["principal_cny"] = 123
            config.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(initialize_workspace(hosted, template), config)
            self.assertEqual(json.loads(config.read_text())["portfolio"]["principal_cny"], 123)
            config.unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                initialize_workspace(hosted, template)

    def test_access_key_is_random_and_never_replaced(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "access-key.txt"
            create_access_key(path)
            first = read_access_key(path)
            self.assertEqual(len(first), 43)
            with self.assertRaises(FileExistsError):
                create_access_key(path)
            self.assertEqual(read_access_key(path), first)
            path.write_text("password")
            with self.assertRaises(ValueError):
                read_access_key(path)


class HostedRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        config = {
            "auto_monitor_enabled": False, "news_translation_enabled": False,
            "fundamentals_enabled": False,
            "analysis": {"mode": "rules"},
            "storage": {"database": "data/monitor.db", "alerts": "data/alerts.jsonl"},
            "sources": {"google_news_rss": False, "yahoo_finance_rss": False,
                        "bing_news_rss": False, "inbox": "data/inbox.jsonl", "rss_feeds": []},
            "portfolio": {"positions": [], "principal_cny": 0},
            "watchlist": [{"market": "US", "symbol": "MU", "name": "Micron", "query": "Micron"}],
        }
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        self.state = HostedState(path, start_asset_monitor=False)
        self.key = secrets.token_urlsafe(32)
        self.auth = "Basic " + base64.b64encode(("stock:" + self.key).encode()).decode()
        self.server = HostedServer(("127.0.0.1", 0), self.state, "https://stock.example.com", self.key)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.state.shutdown()
        self.temporary.cleanup()

    def request(self, path, method="GET", payload=None, overrides=None):
        headers = {"Host": "stock.example.com", "Authorization": self.auth}
        if method != "GET":
            headers.update({"Origin": "https://stock.example.com", "Content-Type": "application/json"})
        headers.update(overrides or {})
        headers = {key: value for key, value in headers.items() if value is not None}
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_every_page_api_and_export_requires_authentication(self):
        for path in ("/", "/app.js", "/api/dashboard", "/api/health", "/api/assets",
                     "/api/research/export", "/api/research/earnings/export", "/config.json"):
            with self.subTest(path=path):
                status, headers, body = self.request(path, overrides={"Authorization": None})
                self.assertEqual(status, 401)
                self.assertIn("Basic", headers["WWW-Authenticate"])
                self.assertEqual(body, b"")
        for auth in ("Bearer token", "Basic !!!", "Basic c3RvY2s6d3Jvbmc="):
            self.assertEqual(self.request("/api/dashboard", overrides={"Authorization": auth})[0], 401)

    def test_cross_origin_and_missing_origin_writes_are_rejected_before_mutation(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                        {"Origin": "null"}, {"Origin": None}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("/api/portfolio/principal", "POST", {"principal_cny": 999}, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.state.config_store.read()["portfolio"]["principal_cny"], 0)

    def test_all_original_modules_and_scripts_are_served(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        for marker in (b'data-view-target="portfolio"', b'data-view-target="signals"',
                       b'data-view-target="social"', b'data-view-target="assets"',
                       b'data-view-target="indices"', b'data-view-target="settings"',
                       b'data-tab="etf"', b'data-tab="earnings"', b'data-tab="fundamentals"'):
            self.assertIn(marker, body)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        for path in ("/app.js", "/startup_memory.js", "/research.js", "/earnings.js", "/social.js",
                     "/workbench.js", "/indices.js", "/annotations.js", "/styles.css", "/icons.svg"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        for path in ("/api/dashboard", "/api/news", "/api/social", "/api/assets", "/api/indices",
                     "/api/features", "/api/fundamentals", "/api/research", "/api/research/inputs",
                     "/api/research/cards", "/api/research/companies", "/api/research/earnings",
                     "/api/research/earnings/annotations"):
            with self.subTest(path=path):
                status, _, data = self.request(path)
                self.assertEqual(status, 200, data.decode())
                self.assertIsInstance(json.loads(data), dict)

    def test_writes_reach_real_portfolio_and_research_not_a_static_mock(self):
        with patch.object(self.state.portfolio_monitor, "trigger_refresh"):
            status, _, data = self.request("/api/portfolio/positions", "POST", {
                "market": "US", "symbol": "MU", "name": "Micron", "quantity": 2, "average_cost": 100})
            self.assertEqual(status, 201, data.decode())
            status, _, data = self.request("/api/portfolio/principal", "POST", {"principal_cny": 1000})
            self.assertEqual(status, 200, data.decode())
            saved = self.state.config_store.read()["portfolio"]
            self.assertEqual(saved["positions"][0]["quantity"], 2)
            self.assertEqual(saved["principal_cny"], 1000)
            self.assertEqual(self.request("/api/portfolio/positions/US/MU", "DELETE", {})[0], 200)
            self.assertEqual(self.state.config_store.read()["portfolio"]["positions"], [])
        self.assertEqual(self.request("/api/research/export")[0], 200)
        self.assertTrue(self.state.research_ledger.path.exists())

    def test_private_files_and_traversal_are_never_served(self):
        for path in ("/config.json", "/../config.json", "/data/monitor.db", "/secrets/access-key.txt"):
            self.assertEqual(self.request(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
