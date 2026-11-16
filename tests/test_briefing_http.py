import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import unquote

from quant_news_monitor.webapp import AppState, DashboardServer


class BriefingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps({
            'auto_monitor_enabled': False, 'analysis': {'mode': 'rules'},
            'storage': {'database': 'data/monitor.db', 'alerts': 'data/alerts.jsonl'},
            'sources': {'google_news_rss': False, 'yahoo_finance_rss': False,
                        'inbox': 'data/inbox.jsonl', 'rss_feeds': []},
            'watchlist': [{'market': 'US', 'symbol': 'MU', 'name': 'Micron'}], 'portfolio': {'positions': []},
        }), encoding='utf-8')
        self.state = AppState(self.path, start_asset_monitor=False)
        self.server = DashboardServer(('127.0.0.1', 0), self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.state.shutdown()
        self.temp.cleanup()

    def request(self, path, payload=None):
        req = Request(f'http://127.0.0.1:{self.server.server_port}{path}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_settings_roundtrip_and_invalid_values_do_not_change_saved_settings(self):
        self.assertIsNone(self.state.briefing_monitor._scheduler)
        status, before = self.request('/api/briefings')
        self.assertEqual(status, 200)
        self.assertEqual({r['market'] for r in before['markets']}, {'CN', 'US', 'HK', 'KR'})
        status, settings = self.request('/api/briefings/settings', {
            'enabled': False, 'morning_minutes_before_open': 45, 'close_minutes_after_close': 20})
        self.assertEqual(status, 200)
        self.assertEqual(settings['morning_minutes_before_open'], 45)
        status, _ = self.request('/api/briefings/settings', {'enabled': 'false'})
        self.assertEqual(status, 400)
        self.assertEqual(self.request('/api/briefings')[1]['settings'], settings)

    def test_workbench_briefing_assets_are_served(self):
        base = f'http://127.0.0.1:{self.server.server_port}'
        with urlopen(base + '/', timeout=5) as response:
            page = response.read().decode('utf-8')
        for asset, marker, content_type in [
            ('/briefing.js', 'window.Briefings', 'javascript'),
            ('/briefing.css', '.briefing-panel', 'text/css'),
        ]:
            with self.subTest(asset=asset):
                self.assertIn(asset[1:], page)
                with urlopen(base + asset, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(content_type, response.headers['Content-Type'])
                    self.assertIn(marker, response.read().decode('utf-8'))

    def test_run_returns_job_without_blocking_and_report_lookup_is_wired(self):
        monitor = self.state.briefing_monitor
        with patch.object(monitor, 'trigger', return_value={'accepted': True, 'report_id': 'one', 'status': 'queued'}) as trigger:
            status, value = self.request('/api/briefings/run', {'market': 'HK', 'kind': 'morning'})
            self.assertEqual(status, 202)
            self.assertEqual(value['report_id'], 'one')
            trigger.assert_called_once_with('HK', 'morning', None, refresh=False)
        self.assertEqual(self.request('/api/briefings/report?id=missing')[0], 400)
        self.assertEqual(self.request('/api/briefings/run', {'market': 'BAD', 'kind': 'morning'})[0], 400)

    def test_annual_refresh_routes_to_the_requested_portfolio_research(self):
        research = self.state.get_portfolio_research('default')
        with patch.object(research, 'refresh_performance', return_value=True) as refresh:
            status, result = self.request('/api/portfolio/research', {'action': 'refresh_performance', 'year': 2025})
            self.assertEqual((status, result), (200, {'started': True}))
            refresh.assert_called_once_with(2025)

    def test_opening_analysis_selects_and_ensures_requested_year(self):
        research = self.state.get_portfolio_research('default')
        research.performance = {'year': 2026, 'histories': {}, 'errors': []}
        research.performances[2025] = {'year': 2025, 'histories': {'US:MU': {'points': []}}, 'errors': []}
        with patch.object(research, 'ensure_analysis', return_value=False) as ensure:
            status, result = self.request('/api/portfolio/research?year=2025')
            self.assertEqual(status, 200)
            self.assertEqual(result['performance']['year'], 2025)
            self.assertIn('US:MU', result['performance']['histories'])
            ensure.assert_called_once_with(2025)
            ensure.reset_mock()
            self.assertEqual(self.request('/api/portfolio/research')[1]['performance']['year'], 2026)
            ensure.assert_not_called()
        self.assertEqual(self.request('/api/portfolio/research?year=invalid')[0], 400)
        self.assertEqual(self.request('/api/portfolio/research?year=1999')[0], 400)

    def test_pdf_export_returns_async_job_and_reports_invalid_requests(self):
        monitor = self.state.briefing_monitor
        with patch.object(monitor, 'export_pdf', return_value={'accepted': True, 'report_id': 'one', 'status': 'queued'}) as export:
            status, value = self.request('/api/briefings/export', {'id': 'one'})
        self.assertEqual(status, 202)
        self.assertEqual(value['status'], 'queued')
        export.assert_called_once_with('one')
        with patch.object(monitor, 'export_pdf', side_effect=KeyError('missing')):
            self.assertEqual(self.request('/api/briefings/export', {'id': 'missing'})[0], 404)
        with patch.object(monitor, 'export_pdf', side_effect=ValueError('报告尚未完成')):
            self.assertEqual(self.request('/api/briefings/export', {'id': 'one'})[0], 400)

    def test_pdf_view_and_download_serve_frozen_bytes_with_chinese_filename(self):
        path = Path(self.temp.name) / '美股_2026-11-06_早报.pdf'
        content = b'%PDF-1.4\nHTTP fixture bytes\n%%EOF'
        path.write_bytes(content)
        base = f'http://127.0.0.1:{self.server.server_port}/api/briefings/pdf?id=one'
        for suffix, disposition in [('', 'inline'), ('&download=1', 'attachment')]:
            with self.subTest(disposition=disposition), patch.object(self.state.briefing_monitor, 'pdf_file', return_value=path) as lookup:
                with urlopen(base + suffix, timeout=5) as response:
                    self.assertEqual(response.read(), content)
                    self.assertEqual(response.headers['Content-Type'], 'application/pdf')
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertTrue(response.headers['Content-Disposition'].startswith(disposition + ';'))
                    self.assertIn(path.name, unquote(response.headers['Content-Disposition']))
                lookup.assert_called_once_with('one')
        with patch.object(self.state.briefing_monitor, 'pdf_file', side_effect=FileNotFoundError):
            self.assertEqual(self.request('/api/briefings/pdf?id=one')[0], 404)

    def test_briefing_context_preserves_missing_source_state_and_does_not_change_auto_switch(self):
        with patch.object(self.state, 'trigger_scan') as scan, patch.object(self.state.earnings_calendar, 'query', return_value={'items': [], 'runtime': {'stale': True}}):
            result = self.state.briefing_context()
        scan.assert_called_once()
        self.assertFalse(self.state.auto_enabled)
        self.assertEqual(result['news'], [])
        self.assertTrue(any('财报' in error for error in result['errors']))
        self.assertIsNotNone(result['collected_at'])
