from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant_news_monitor.portfolio_research import PortfolioResearch
from quant_news_monitor.research_ledger import ResearchLedger
from quant_news_monitor.webapp import AppState


NOW = datetime(2026, 11, 15, 4, tzinfo=timezone.utc)


def make_app(tmp_path):
    books = [{'id': str(i) * 32, 'name': name, 'positions': [{'market': 'US', 'symbol': symbol}]}
             for i, (name, symbol) in enumerate([('成都', 'AAPL'), ('温州', 'MSFT'), ('Todd', 'MS')], 1)]
    app = SimpleNamespace(research_ledger=ResearchLedger(tmp_path / 'research.db'),
                          config_store=SimpleNamespace(read=lambda: {'portfolios': books}), _portfolio_researches={})
    app.get_portfolio_research = lambda identifier: app._portfolio_researches[identifier]
    app._portfolio_researches = {b['id']: PortfolioResearch(app, b['id']) for b in books}
    return app, books


def test_new_books_start_without_ai_and_recover_after_failure(tmp_path):
    app, books = make_app(tmp_path)
    for book in books:
        research = app.get_portfolio_research(book['id'])
        with patch.object(research, 'refresh_performance', return_value=True) as refresh:
            assert research.ensure_analysis(now=NOW)
            refresh.assert_called_once_with(2026)
        research.performance = {'year': 2026, 'targets': ['US:' + book['positions'][0]['symbol']],
                                'observed_at': NOW.isoformat(), 'histories': {}, 'errors': ['源限流']}
        with patch.object(research, 'refresh_performance', return_value=True) as refresh:
            assert not research.ensure_analysis(now=NOW + timedelta(minutes=1))
            assert research.ensure_analysis(now=NOW + timedelta(minutes=6))
            refresh.assert_called_once()


def test_new_target_or_year_refreshes_but_quote_quantity_and_cash_changes_do_not_refetch(tmp_path):
    app, books = make_app(tmp_path)
    research = app.get_portfolio_research(books[0]['id'])
    research.performance = {'year': 2026, 'targets': ['US:AAPL'], 'observed_at': NOW.isoformat(),
                            'histories': {'US:AAPL': {'points': [1]}}, 'errors': []}
    with patch.object(research, 'refresh_performance', return_value=True) as refresh:
        books[0]['positions'][0]['quantity'] = 50
        books[0]['cash'] = 200
        assert not research.ensure_analysis(now=NOW + timedelta(minutes=1))
        assert research.ensure_analysis(now=NOW + timedelta(minutes=31))
        refresh.reset_mock()
        books[0]['positions'].append({'market': 'US', 'symbol': 'NVDA'})
        assert research.ensure_analysis(now=NOW + timedelta(minutes=1))
        refresh.assert_called_once_with(2026)
        research.performance_busy = True
        assert not research.ensure_analysis(2025, now=NOW)


def test_scheduler_covers_all_books_even_when_news_ai_is_off(tmp_path):
    app, books = make_app(tmp_path)
    for research in app._portfolio_researches.values():
        research.ensure_analysis = Mock(return_value=False)
    AppState.refresh_portfolio_analyses(app)
    for research in app._portfolio_researches.values():
        research.ensure_analysis.assert_called_once()


def test_collector_persists_rolling_inputs_and_preserves_last_good_history_on_failure(tmp_path):
    app, books = make_app(tmp_path)
    research = app.get_portfolio_research(books[2]['id'])
    history = {'currency': 'USD', 'source': 'fixture adjusted', 'observed_at': NOW.isoformat(),
               'points': [{'time': f'2026-11-{i:02}', 'close': i+100, 'adjusted_close': i+100} for i in range(1, 14)]}
    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', return_value=history):
        research._collect_performance(2026)
    saved = research.snapshot()
    assert saved['histories']['US:MS'] == history
    assert saved['performance']['targets'] == ['US:MS']
    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', side_effect=OSError('offline')):
        research._collect_performance(2026)
    restored = PortfolioResearch(app, books[2]['id']).snapshot()
    assert restored['performance']['histories']['US:MS'] == history
    assert restored['histories']['US:MS']['observed_at'] == NOW.isoformat()
    assert restored['performance']['errors']
    assert 'US:MS' not in app.get_portfolio_research(books[0]['id']).snapshot()['histories']


def test_two_views_of_different_years_reuse_their_own_history(tmp_path):
    app, books = make_app(tmp_path)
    research = app.get_portfolio_research(books[0]['id'])
    def history(target, year, **kwargs):
        return {'currency': 'USD', 'source': 'fixture', 'points': [
            {'time': f'{year}-11-01', 'close': year, 'adjusted_close': year}]}
    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', side_effect=history):
        research._collect_performance(2025)
        research._collect_performance(2026)
    now = datetime.fromisoformat(research.performance['observed_at']) + timedelta(minutes=1)
    with patch.object(research, 'refresh_performance') as refresh:
        for year in [2025, 2026, 2025]:
            assert not research.ensure_analysis(year, now=now)
            assert research.snapshot(year)['performance']['histories']['US:AAPL']['points'][0]['close'] == year
        refresh.assert_not_called()
    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', side_effect=OSError('offline')):
        research._collect_performance(2025)
    assert research.snapshot(2025)['performance']['histories']['US:AAPL']['points'][0]['close'] == 2025
    assert research.snapshot(2026)['performance']['histories']['US:AAPL']['points'][0]['close'] == 2026


def test_fund_disclosure_failure_keeps_dated_cache_then_recovers_without_overriding_imports(tmp_path):
    app, books = make_app(tmp_path)
    books[0]['positions'] = [{'market': 'FUND', 'symbol': '000001'}]
    research = app.get_portfolio_research(books[0]['id'])
    target = 'FUND:000001'
    history = {'currency': 'CNY', 'points': [{'time': '2026-11-13', 'close': 1, 'adjusted_close': 1}]}
    old = {'holdings': [{'key': 'CN:600519.SS', 'weight': 10}], 'as_of': '2026-06-30', 'source': 'fixture'}
    fresh = {**old, 'as_of': '2026-09-30'}
    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', return_value=history), \
            patch('quant_news_monitor.portfolio_research._export_fund_disclosure',
                  side_effect=[({}, old), ({}, {}), ({}, fresh)]) as disclosure:
        research._collect_performance(2026)
        research._collect_performance(2026)
        failed = research.snapshot()
        assert failed['funds'][target] == old
        assert any(target in error and '沿用' in error for error in failed['analysis_errors'])
        research._collect_performance(2026)
        recovered = research.snapshot()
        assert recovered['funds'][target] == fresh
        assert not recovered['analysis_errors']
        assert disclosure.call_count == 3
        with patch('quant_news_monitor.etf_research.latest_holdings',
                   return_value={'payload': {'funds': {target: old}}}):
            research._collect_performance(2026)
        assert research.snapshot()['funds'][target] == old
        assert disclosure.call_count == 3
