import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quant_news_monitor.config import load_config


class EmptyWatchlistTests(unittest.TestCase):
    def test_new_install_loads_without_stocks_or_holdings(self):
        template = Path(__file__).resolve().parents[1] / 'desktop/default-config.json'
        raw = json.loads(template.read_text(encoding='utf-8'))
        raw['watchlist'] = []
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(raw), encoding='utf-8')
            self.assertEqual(load_config(path).watchlist, ())
            self.assertEqual(json.loads(path.read_text())['portfolio'], {'positions': [], 'principal_cny': 0})
            raw['watchlist'] = [{'market': 'US', 'name': 'Missing symbol'}]
            path.write_text(json.dumps(raw), encoding='utf-8')
            with self.assertRaises(KeyError):
                load_config(path)
