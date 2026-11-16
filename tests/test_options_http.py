import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from quant_news_monitor.webapp import DashboardServer


class OptionsHTTPTests(unittest.TestCase):
    def test_workspace_assets_are_served_with_expected_content_types(self):
        server = DashboardServer(('127.0.0.1', 0), SimpleNamespace())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for asset, mime in [('options_core.js', 'text/javascript'),
                                ('options_learning.js', 'text/javascript'),
                                ('options.js', 'text/javascript'), ('options.css', 'text/css')]:
                with self.subTest(asset=asset):
                    try:
                        response = urlopen(f'http://127.0.0.1:{server.server_port}/{asset}', timeout=5)
                    except HTTPError as error:
                        response = error
                    with response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(mime, response.headers['Content-Type'])
                        self.assertGreater(len(response.read()), 100)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
