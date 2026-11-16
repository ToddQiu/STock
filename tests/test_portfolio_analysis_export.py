from datetime import datetime, timezone
from io import BytesIO
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import unquote
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch

from openpyxl import load_workbook
import pytest

from quant_news_monitor.portfolio_analysis_export import build_analysis_workbook
from quant_news_monitor.portfolio_analysis_pdf import build_analysis_pdf
from quant_news_monitor.portfolio_export import MIME_TYPE
from quant_news_monitor.portfolio_research import PortfolioResearch
from quant_news_monitor.webapp import DashboardServer


STAMP = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
BOOK = 'a' * 32
ACCOUNT = {'id': BOOK, 'name': '温州组合', 'base_currency': 'HKD', 'positions': [
    {'market': 'US', 'symbol': 'AAPL', 'name': '=Apple'},
    {'market': 'US', 'symbol': 'MSFT', 'name': 'Microsoft'},
]}


def packet():
    pair = {'a': 'US:AAPL', 'b': 'US:MSFT', 'correlation': .81, 'samples': 250,
            'from': '2024-12-31', 'to': '2025-12-31'}
    return {'portfolio_id': BOOK, 'year': 2025, 'performance': {
        'year': 2025, 'status': 'available', 'annualReturn': 10, 'maxDrawdown': -6, 'volatility': 12,
        'averageCorrelation': .81, 'medianCorrelation': .81, 'cashWeight': .1, 'validPairs': 1,
        'targets': [{'target': 'US:AAPL', 'currency': 'USD', 'value': 400, 'source': '数据来源'},
                    {'target': 'US:MSFT', 'currency': 'USD', 'value': 500, 'source': '数据来源'}],
        'weights': [{'target': 'US:AAPL', 'weight': .4}, {'target': 'US:MSFT', 'weight': .5}],
        'pairs': [pair], 'matrix': [[1, .81], [.81, 1]],
        'path': [{'date': '2024-12-31', 'value': 1, 'change': 0, 'drawdown': 0},
                 {'date': '2025-12-31', 'value': 1.1, 'change': 10, 'drawdown': 0}],
    }, 'risk': {'correlations': [{**pair, 'correlation': None, 'samples': 2, 'reason': '样本不足'}],
                'total': 1000, 'max': 50, 'top5': 90, 'cash': 10, 'coverage': {'valued': 2, 'sector': 100},
                'sector': [{'name': '技术', 'value': 900, 'weight': 90, 'missing': 0}]},
        'diagnosis': [{'title': '集中度分析', 'fact': '前五大持仓占比90%', 'meaning': '风险集中', 'action': '复核'}],
        'context': {'risk_window': 120, 'observed_at': STAMP.isoformat(), 'quote_as_of': STAMP.isoformat()}}


def workbook_values(book):
    return {row[0]: row[1] for row in book['组合分析'].iter_rows(min_row=2, values_only=True)}


def test_report_has_numeric_workbook_values_and_missing_pairs():
    name, data = build_analysis_workbook(ACCOUNT, packet(), captured_at=STAMP)
    assert name == 'STock-温州组合-2025年组合分析-20261109-160000.xlsx'
    book = load_workbook(BytesIO(data))
    summary = workbook_values(book)
    assert summary['统计年份'] == 2025
    assert summary['模拟收益率'] == .1
    assert summary['最大回撤'] == -.06
    assert summary['年化波动率'] == .12
    assert summary['持仓平均相关系数'] == .81
    assert summary['高相关持仓对数'] == 1
    assert summary['年度行情采集时间（北京时间）'] == datetime(2026, 11, 9, 16)
    assert book['年度相关性矩阵']['C2'].value == .81
    assert book['相关性配对']['J2'].value == '高正相关'
    assert book['相关性配对']['A3'].value == '120日窗口'
    assert book['相关性配对']['F3'].value is None
    assert book['相关性配对']['K3'].value == '样本不足'
    assert book['年度权重与来源']['B2'].value == '=Apple'
    assert book['年度权重与来源']['B2'].data_type == 's'
    assert book['年度权重与来源']['E2'].value == .4
    assert book['模拟净值']['C2'].value == 0
    assert book['资产配置']['D2'].value == .9
    assert book['组合诊断']['A3'].value == '集中度分析'


def test_missing_year_preserves_coverage_without_inventing_metrics():
    payload = packet()
    payload['context'] = {}
    payload['performance'].update(status='unavailable', annualReturn=None, maxDrawdown=None, volatility=None,
                                  averageCorrelation=None, matrix=[[None, None], [None, None]], path=[],
                                  weights=[], issues=['所选年尚未取得有效复权行情'])
    _, data = build_analysis_workbook(ACCOUNT, payload, captured_at=STAMP)
    book = load_workbook(BytesIO(data))
    summary = workbook_values(book)
    assert '暂无' in summary['年度分析状态']
    assert summary['模拟收益率'] is None
    assert summary['估值覆盖标的数'] == 2
    assert book['年度相关性矩阵']['C2'].value is None
    assert any(row[2] == '所选年尚未取得有效复权行情' for row in book['组合分析'].values)


@pytest.mark.parametrize('market,symbol,english,chinese', [
    ('CN', '601169.SS', 'Bank of Beijing', '北京银行'),
    ('HK', '0700.HK', 'Tencent Holdings', '腾讯控股'),
])
def test_export_uses_market_language_names_without_changing_account(market, symbol, english, chinese):
    target = market + ':' + symbol
    payload = json.loads(json.dumps(packet()).replace('US:AAPL', target))
    payload['holdings'] = [{'target': target, 'name': chinese}, {'target': 'US:MSFT', 'name': '微软'}]
    payload['risk']['largest'] = [{'target': target, 'name': english, 'value': 400, 'weight': 40}]
    account = {**ACCOUNT, 'positions': [{'market': market, 'symbol': symbol, 'name': english},
                                      {'market': 'US', 'symbol': 'MSFT', 'name': 'Microsoft'}]}
    _, data = build_analysis_workbook(account, payload, captured_at=STAMP)
    book = load_workbook(BytesIO(data))
    assert book['年度权重与来源']['B2'].value == chinese
    assert book['年度权重与来源']['B3'].value == 'Microsoft'
    assert book['相关性配对']['C2'].value == chinese
    assert book['持仓与来源']['B2'].value == chinese
    assert any(row[1] == chinese for row in book['资产配置'].iter_rows(min_row=2, values_only=True))
    assert account['positions'][0]['name'] == english


def test_export_collects_selected_year_and_latest_rolling_inputs():
    ledger = Mock(inputs=Mock(return_value={'items': []}))
    account = {**ACCOUNT, 'positions': [*ACCOUNT['positions'], {'market': 'CN', 'symbol': '515000.SS', 'asset_type': 'stock'}]}
    state = SimpleNamespace(config_store=Mock(read=Mock(return_value={'portfolios': [account]})), research_ledger=ledger)
    research = PortfolioResearch(state, BOOK)

    def history(target, year, *, now, recent=False):
        if not recent and target == 'US:MSFT':
            raise OSError('行情源暂不可用')
        return {'points': [{'time': '2025-12-31', 'close': 1, 'adjusted_close': 1}],
                'source': '复权行情', 'currency': 'USD', 'year': year}

    with patch('quant_news_monitor.portfolio_research.fetch_performance_history', side_effect=history), \
         patch('quant_news_monitor.portfolio_research._export_fund_disclosure', return_value=(
             {'investment_focus': '科技龙头指数'}, {'holdings': [{'name': '披露标的'}], 'partial': True})), \
         patch('quant_news_monitor.etf_research.latest_holdings', return_value={'payload': {'funds': {}}}):
        snapshot = research.prepare_analysis_export(2025)
    assert snapshot['portfolio_id'] == BOOK
    assert snapshot['performance']['year'] == 2025
    assert set(snapshot['performance']['histories']) == {'US:AAPL', 'CN:515000.SS'}
    assert 'US:MSFT' in snapshot['performance']['errors'][0]
    assert set(snapshot['histories']) == {'US:AAPL', 'US:MSFT', 'CN:515000.SS'}
    assert snapshot['funds']['CN:515000.SS']['partial']
    ledger.observe.assert_not_called()


def test_report_exports_detail_and_separates_partial_interval_from_annual_return():
    payload = packet()
    payload['performance'].update(status='partial', to='2025-12-31')
    payload['performance']['from'] = '2025-02-01'
    payload['holdings'] = [{'target': 'US:AAPL', 'price': 100, 'value': 400, 'description': '消费电子',
                            'quote_source': '交易所报价', 'quote_time': STAMP.isoformat()}]
    payload['fx'] = [{'pair': 'USD_HKD', 'price': 7.8, 'source': '外汇源', 'status': 'live'}]
    payload['exposure'] = {'overlaps': [{'name': '共同标的', 'value': 50, 'weight': 5, 'origins': ['US:AAPL']}],
                           'unknown': ['底仓仅部分披露']}
    _, data = build_analysis_workbook(ACCOUNT, payload, captured_at=STAMP)
    book = load_workbook(BytesIO(data))
    summary = workbook_values(book)
    assert summary['模拟收益率'] is None
    assert summary['共同有效区间模拟收益率'] == .1
    assert any('消费电子' in row for row in book['持仓与来源'].values)
    assert any(7.8 in row for row in book['估值汇率'].values)
    assert any('最新报价' in row for row in book['估值汇率'].values)
    assert any('底仓仅部分披露' in row for row in book['ETF穿透与重叠'].values)


@pytest.fixture
def server():
    store = Mock(read=Mock(return_value={'portfolios': [ACCOUNT]}))
    state = SimpleNamespace(config_store=store)
    server = DashboardServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server, store
    server.shutdown()
    server.server_close()
    worker.join(2)


@pytest.mark.parametrize('extension', ['xlsx', 'pdf'])
def test_binary_export_is_read_only_and_rejects_wrong_year_or_book(server, extension):
    http, store = server
    base = f'http://127.0.0.1:{http.server_port}'

    def send(body, query=''):
        request = Request(base + '/api/portfolio/analysis.' + extension + query, data=json.dumps(body).encode(),
                          headers={'Origin': base, 'Content-Type': 'application/json'}, method='POST')
        return urlopen(request, timeout=10)

    with send(packet()) as response:
        assert response.status == 200
        assert response.headers['Content-Type'] == (MIME_TYPE if extension == 'xlsx' else 'application/pdf')
        assert response.headers['Cache-Control'] == 'no-store'
        assert '温州组合' in unquote(response.headers['Content-Disposition'])
        content = response.read()
        if extension == 'xlsx':
            assert workbook_values(load_workbook(BytesIO(content)))['组合名称'] == '温州组合'
        else:
            assert content.startswith(b'%PDF-')
    store.read.assert_called_once_with()
    store.write.assert_not_called()
    for payload, query in [({**packet(), 'year': 2024}, ''), (packet(), '?portfolio_id=default'),
                           ({**packet(), 'performance': {**packet()['performance'], 'targets': []}}, '')]:
        with pytest.raises(HTTPError) as error:
            send(payload, query)
        assert error.value.code == 400
        error.value.close()


def test_pdf_preserves_partial_metrics_zeroes_missing_values_and_evidence(tmp_path):
    from pypdf import PdfReader
    payload = packet()
    payload['performance']['status'] = 'partial'
    payload['holdings'] = [{'target': 'US:AAPL', 'quantity': 2, 'value': 400, 'pnl': 0, 'pnl_pct': 0,
                            'description': '消费电子 <b>字面资料</b>', 'quote_source': '交易所',
                            'quote_url': 'https://example.com/quotes', 'quote_time': STAMP.isoformat()}]
    payload['exposure'] = {'overlaps': [{'name': '共同底仓', 'value': 50, 'weight': 5, 'origins': ['US:AAPL']}],
                           'dates': [{'target': 'US:AAPL', 'as_of': '2025-12-31', 'source': '基金披露',
                                      'source_url': 'javascript:alert(1)'}], 'unknown': ['仅前十大底仓，非完整穿透']}
    before = json.dumps(payload)
    filename, content = build_analysis_pdf(ACCOUNT, payload, captured_at=STAMP)
    assert filename == 'STock-温州组合-2025年组合分析-20261109-160000.pdf'
    assert json.dumps(payload) == before
    reader = PdfReader(BytesIO(content))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    for value in ['持仓组合分析报告', '温州组合', '共同有效区间模拟收益率', '10.00%', '-6.00%',
                  '0.810', '0.00%', '未取得', '样本不足', '共同底仓', '5.00%', '非完整穿透', '2025-12-31']:
        assert value in text
    assert '实际账户收益' in text
    links = [annotation.get_object().get('/A', {}).get('/URI', '') for page in reader.pages for annotation in page.get('/Annots', [])]
    assert 'https://example.com/quotes' in links
    assert not any(link.startswith('javascript:') for link in links)
    (tmp_path / filename).write_bytes(content)


def test_pdf_handles_missing_year_and_paginated_long_notes():
    from pypdf import PdfReader
    payload = packet()
    payload['performance'].update(status='unavailable', annualReturn=None, maxDrawdown=None, volatility=None,
                                  averageCorrelation=None, path=[])
    payload['diagnosis'] = [{'title': '长段分析', 'fact': '风险资料需要核对。' * 250, 'action': '末尾验证条件'} for _ in range(4)]
    _, content = build_analysis_pdf(ACCOUNT, payload, captured_at=STAMP)
    reader = PdfReader(BytesIO(content))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) > 3
    assert '未取得' in text and '末尾验证条件' in text
    assert '10.00%' not in text.split('年度行情不足')[0]  # Missing annual return must not reuse a previous value.
