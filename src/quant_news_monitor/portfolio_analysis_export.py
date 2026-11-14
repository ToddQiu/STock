"""Serialize the existing client research model into an Excel snapshot."""
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from .portfolio_export import PERCENT, STATUS_LABELS, _date, _number, _percent, _sheet, _source_date, _time
from .security_master import display_name


MAX_HOLDINGS = 60
MAX_PAIRS = MAX_HOLDINGS * (MAX_HOLDINGS - 1) // 2


def _object(value):
    if not isinstance(value, dict):
        raise ValueError('组合分析数据格式无效')
    return value


def _list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('组合分析数据格式无效或超出范围')
    return value


def _rows(data, key, limit):
    return [_object(row) for row in _list(data.get(key, []), limit)]


def _text(value):
    return value[:4000] if isinstance(value, str) and value else None


def _ratio(value):
    value = _number(value)
    return value if value is not None and -1 <= value <= 1 else None


def _table(book, title, identifier, headers, rows, *, widths=None, percentages=(), freeze='A2', height=26):
    sheet = _sheet(book, title, headers, [dict(zip(headers, row)) for row in rows],
                   freeze=freeze, table_name=identifier, widths=widths or {}, height=height, notes={}, metadata=[])
    for column in percentages:
        for row in range(2, len(rows) + 2):
            sheet.cell(row, column).number_format = PERCENT
    return sheet


def _pairs(data, key, names):
    rows = _rows(data, key, MAX_PAIRS)
    for row in rows:
        a, b = row.get('a'), row.get('b')
        if not isinstance(a, str) or not isinstance(b, str) or a == b or a not in names or b not in names:
            raise ValueError('相关性配对与当前组合不一致，请刷新组合后再导出')
    return rows


def build_analysis_workbook(account, payload, *, captured_at):
    """Export one already calculated analysis. Does not fetch, store or recompute market data."""
    stamp = _time(captured_at)
    year = payload.get('year')
    if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= stamp.year:
        raise ValueError('年份须在2000年至当前年之间')
    performance = _object(payload.get('performance'))
    risk = _object(payload.get('risk'))
    context = _object(payload.get('context', {}))
    if performance.get('year') != year:
        raise ValueError('统计年份已变更，请重新导出')
    holdings = {row.get('target'): row for row in _rows(payload, 'holdings', MAX_HOLDINGS) if isinstance(row.get('target'), str)}
    names = {f"{row['market']}:{row['symbol']}": display_name(row['market'], row['symbol'],
             holdings.get(f"{row['market']}:{row['symbol']}", {}).get('name'), row.get('name'))
             for row in account.get('positions', [])}
    if len(names) > MAX_HOLDINGS:
        raise ValueError(f'组合分析最多支持{MAX_HOLDINGS}个标的')
    targets = _rows(performance, 'targets', MAX_HOLDINGS)
    keys = [_text(row.get('target')) for row in targets]
    if len(keys) != len(names) or set(keys) != set(names):
        raise ValueError('分析标的与当前组合不一致，请刷新组合后再导出')
    matrix = _list(performance.get('matrix', []), MAX_HOLDINGS)
    if len(matrix) != len(keys) or any(len(_list(row, MAX_HOLDINGS)) != len(keys) for row in matrix):
        raise ValueError('相关性矩阵与持仓标的不一致')
    annual_pairs = _pairs(performance, 'pairs', names)
    rolling_pairs = _pairs(risk, 'correlations', names)
    risk_window = 120 if context.get('risk_window') == 120 else 60
    basis = '证券市值' if context.get('risk_basis') == 'invested' else '含现金的总资产'
    coverage = _object(risk.get('coverage', {}))
    observed = _time(context.get('observed_at'))
    issues = [_text(issue) for issue in _list(performance.get('issues', []), 200)]
    issues += [_text(issue) for issue in _list(context.get('annual_errors', []), 100)]
    issues += [_text(issue) for issue in _list(context.get('risk_errors', []), 100)]
    partial = performance.get('status') == 'partial'
    status = '年度行情不足，待更新' if observed else '所选年暂无年度行情，待更新'
    if performance.get('status') == 'available':
        status = '已计算年度模拟'
    elif partial:
        status = '仅取得年内共同有效区间，另列区间模拟'
    if context.get('annual_busy') is True:
        status += '，年度行情正在更新'

    name = account.get('name') or '默认组合'
    currency = account.get('base_currency') or 'CNY'
    book = Workbook()
    book.remove(book.active)
    book.properties.title = f'{name} · {year}年组合分析'
    book.properties.creator = 'STock'
    metrics = [
        ('组合名称', name, '当前选定组合'),
        ('统计年份', year, '当年只统计至最近有效的共同收盘日'),
        ('导出时间（北京时间）', stamp, '冻结本次导出所用的组合与资料'),
        ('持仓估值采集时间（北京时间）', _time(context.get('quote_as_of')), '用于估算当前配置比例的行情快照时间'),
        ('年度行情采集时间（北京时间）', observed, '仅采用所选年份的年度行情'),
        ('短窗研究采集时间（北京时间）', _time(context.get('risk_as_of')), '用于短窗相关性与配置诊断的研究资料时间'),
        ('年度分析状态', status, '缺失值留空，零保留为实际零值'),
        ('模拟收益率', None if partial else _percent(performance.get('annualReturn')), '选年前共同收盘配置至区间末；以当前权重模拟'),
        ('最大回撤', None if partial else _percent(performance.get('maxDrawdown')), '年度模拟净值从峰值至谷值的最大跌幅'),
        ('年化波动率', None if partial else _percent(performance.get('volatility')), '至少20个共同收益区间的样本标准差 × √252'),
        ('持仓平均相关系数', _ratio(performance.get('averageCorrelation')), '所选年份有效持仓对等权，剔除对角线及重复配对'),
        ('持仓相关系数中位数', _ratio(performance.get('medianCorrelation')), '所选年份有效持仓对的中位数'),
        ('高相关持仓对数', sum((_ratio(row.get('correlation')) or 0) > 0.7 for row in annual_pairs), '相关系数 > 0.7'),
        ('有效相关性配对', _number(performance.get('validPairs')), f'共 {len(keys) * (len(keys) - 1) // 2} 个可能配对'),
        (f'{risk_window}日窗口有效配对', sum(_ratio(row.get('correlation')) is not None for row in rolling_pairs), f'共 {len(keys) * (len(keys) - 1) // 2} 个可能配对'),
        ('共同收益区间数', _number(performance.get('samples')), '基于当前持仓的共同收盘日期'),
        ('模拟起始基准日', _date(performance.get('from')), '年内第一个共同有效收盘' if partial else '选年前最后一个共同有效收盘'),
        ('模拟结束日', _date(performance.get('to')), '所选年份内最后一个共同有效收盘'),
        ('最大回撤峰值日', _date(performance.get('peakDate')), ''),
        ('最大回撤谷值日', _date(performance.get('troughDate')), ''),
        ('净值恢复日', _date(performance.get('recoveryDate')), '未记录恢复日时留空'),
        ('模拟现金权重', _number(performance.get('cashWeight')), '现金及当前汇率固定'),
        ('估值覆盖标的数', _number(coverage.get('valued')), f'当前组合共 {len(keys)} 个标的'),
        (f'总资产（{currency}）', _number(risk.get('total')), '现金加证券市值，待确认的报价仍可能包含在快照中'),
        ('最大持仓占比', _percent(risk.get('max')), f'分母为{basis}'),
        ('前五大持仓占比', _percent(risk.get('top5')), f'分母为{basis}'),
        ('现金占总资产比例', _percent(risk.get('cash')), '分母为含现金的总资产'),
        ('行业覆盖率', _percent(coverage.get('sector')), '已估值证券中可按行业归类的市值占比'),
        (f'未归类行业市值（{currency}）', _number(coverage.get('unknownValue')), '缺资料及未穿透基金的已估值市值'),
        ('计算口径', None, _text(performance.get('method'))),
        ('收益性质', None, '模拟以当前配置权重买入并持有、固定汇率、不含交易费用。实际账户收益须另按成交与出入金核算。'),
        ('相关性口径', None, '所选年使用同起止日期复权收益的 Pearson 相关系数，每对至少20个区间。短窗独立统计，60日须至少20个、120日须至少40个区间。'),
    ]
    if partial:
        metrics[9:9] = [('共同有效区间模拟收益率', _percent(performance.get('annualReturn')), '按本报告列明的年内起止日计算'),
                        ('共同有效区间最大回撤', _percent(performance.get('maxDrawdown')), '仅适用于该共同区间'),
                        ('共同有效区间年化波动率', _percent(performance.get('volatility')), '至少20个共同收益区间')]
    metrics.extend(('资料说明', None, issue) for issue in issues if issue)
    sheet = _table(book, '组合分析', 'AnalysisSummary', ['指标', '数值', '口径与说明'], metrics,
                   widths={1: 38, 2: 30, 3: 92}, height=34)
    for index, (label, value, note) in enumerate(metrics, 2):
        if label in {'模拟收益率', '最大回撤', '年化波动率', '模拟现金权重', '最大持仓占比', '前五大持仓占比', '现金占总资产比例', '行业覆盖率'} or label.startswith('共同有效区间'):
            sheet.cell(index, 2).number_format = PERCENT
        elif isinstance(value, int):
            sheet.cell(index, 2).number_format = '0'
        if note and len(note) > 55:
            sheet.row_dimensions[index].height = min(100, 24 * (1 + len(note) // 42))

    sheet = _table(book, '年度相关性矩阵', 'AnnualCorrelationMatrix', ['证券代码', *keys],
                   [[target, *[_ratio(value) for value in row]] for target, row in zip(keys, matrix)],
                   freeze='B2', widths={1: 24})
    for row in sheet.iter_rows(min_row=2, max_row=len(keys) + 1, min_col=2):
        for cell in row:
            value = cell.value
            if value is not None:
                cell.number_format = '0.000'
                if value:
                    cell.fill = PatternFill('solid', fgColor='FBE8E9' if value > 0 else 'E4F3E9')

    pair_rows = []
    for period, entries in ((f'{year}年', annual_pairs), (f'{risk_window}日窗口', rolling_pairs)):
        for row in entries:
            coefficient = _ratio(row.get('correlation'))
            pair_rows.append([period, row['a'], names[row['a']], row['b'], names[row['b']], coefficient,
                              _number(row.get('samples')), _text(row.get('from')), _text(row.get('to')),
                              '高正相关' if coefficient is not None and coefficient > 0.7 else None,
                              _text(row.get('reason')) or ('缺少有效相关系数' if coefficient is None else None)])
    _table(book, '相关性配对', 'CorrelationPairs', ['分析区间', '代码A', '标的A', '代码B', '标的B', '相关系数', '对齐区间数', '起点', '终点', '关注', '缺失原因'],
           pair_rows, widths={8: 29, 9: 29, 11: 42}, freeze='C2')

    weights = {_text(row.get('target')): _number(row.get('weight')) for row in _rows(performance, 'weights', MAX_HOLDINGS)}
    _table(book, '年度权重与来源', 'AnnualAnalysisWeights', ['证券代码', '标的', '原币', f'快照市值（{currency}）', '初始模拟权重', '年度行情来源', '来源链接', '缺失原因'],
           [[key, names[key], _text(row.get('currency')), _number(row.get('value')), weights.get(key),
             _text(row.get('source')), _text(row.get('source_url')), _text(row.get('reason'))] for key, row in zip(keys, targets)],
           widths={7: 52, 8: 44}, percentages=(5,))
    _table(book, '模拟净值', 'SimulatedNav', ['共同收盘日', '模拟净值', '累计模拟收益率', '回撤'],
           [[_date(row.get('date')), _number(row.get('value')), _percent(row.get('change')), _percent(row.get('drawdown'))]
            for row in _rows(performance, 'path', 370)], percentages=(3, 4))
    allocation = []
    for key, label in (('sector', '行业'), ('market', '市场'), ('currency', '报价币种'), ('largest', '前五大持仓')):
        for row in _rows(risk, key, MAX_HOLDINGS):
            allocation.append([label, _text(names.get(row.get('target'), row.get('name')) if key == 'largest' else row.get('name')), _number(row.get('value')),
                               _percent(row.get('weight')), _number(row.get('missing')), basis])
    _table(book, '资产配置', 'AnalysisAllocation', ['分类维度', '名称', f'金额（{currency}）', '占比', '缺估值项数', '占比分母'],
           allocation, widths={2: 31}, percentages=(4,))

    detail = []
    for target in keys:
        row = holdings.get(target, {})
        quote_status = _text(row.get('quote_status'))
        detail.append([target, names[target], _text(row.get('type')), _text(row.get('currency')),
                       _number(row.get('quantity')), _number(row.get('price')), _number(row.get('value')),
                       _number(row.get('cost')), _number(row.get('pnl')), _percent(row.get('pnl_pct')),
                       _source_date(row.get('quote_time')), _text(row.get('quote_source')), STATUS_LABELS.get(quote_status, quote_status),
                       _text(row.get('quote_url')), _text(row.get('description')), _text(row.get('classification_source')),
                       _source_date(row.get('classification_as_of')), _text(row.get('classification_url')),
                       _text(row.get('rolling_source')), _date(row.get('rolling_to')), _time(row.get('rolling_observed_at')),
                       _text(row.get('notes'))])
    _table(book, '持仓与来源', 'AnalysisHoldings', ['证券代码', '标的', '类型', '原币', '数量', '原币现价', f'市值（{currency}）',
           f'成本（{currency}）', f'浮动盈亏（{currency}）', '浮盈亏率', '报价时间（北京时间）', '报价来源', '报价状态', '报价链接',
           '行业或投资方向', '分类来源', '分类资料时间', '分类链接', '短窗日线来源', '短窗最后收盘日', '短窗资料采集时间', '资料说明'],
           detail, widths={14: 40, 15: 32, 18: 40, 22: 48}, percentages=(10,), freeze='C2', height=46)
    _table(book, '估值汇率', 'AnalysisFx', ['货币对', '报价', '来源', '来源时间（北京时间）', '状态'],
           [[_text(row.get('pair')), _number(row.get('price')), _text(row.get('source')), _source_date(row.get('quote_time')),
             STATUS_LABELS.get(row.get('status'), _text(row.get('status')))] for row in _rows(payload, 'fx', 16)])

    exposure = _object(payload.get('exposure', {}))
    overlap_rows = []
    for key, label, limit in (('overlaps', '已知重复敞口', 2000), ('disclosed', '已披露底层敞口', MAX_HOLDINGS * 5)):
        for row in _rows(exposure, key, limit):
            origins = ' / '.join(dict.fromkeys(v for v in _list(row.get('origins', []), MAX_HOLDINGS) if isinstance(v, str)))
            overlap_rows.append([label, _text(row.get('name')), _text(origins), _number(row.get('value')), _percent(row.get('weight'))])
    for row in _rows(exposure, 'pairs', MAX_PAIRS):
        overlap = _percent(row.get('overlap'))
        overlap_rows.append(['基金配对', _text(row.get('a')), _text(row.get('b')), None, None, _number(row.get('count')), overlap,
                             None, '完整同口径底仓不足' if overlap is None else None])
    overlap_rows.extend(['基金报告期', _text(row.get('target')), None, None, None, None, None, _date(row.get('as_of')),
                         _text(row.get('source')), _text(row.get('source_url'))]
                        for row in _rows(exposure, 'dates', MAX_HOLDINGS))
    overlap_rows.extend(['资料说明', None, None, None, None, None, None, None, _text(note)]
                        for note in [exposure.get('note'), *_list(exposure.get('unknown', []), 2000)] if note)
    _table(book, 'ETF穿透与重叠', 'AnalysisEtf', ['类型', '标的', '来源持仓或对比基金', f'穿透市值（{currency}）', '穿透占比',
           '共同股票数', '核验重叠率', '披露持仓期末', '口径或缺失原因', '资料来源链接'], overlap_rows,
           widths={3: 43, 9: 67, 10: 44}, percentages=(5, 7), height=40)
    diagnosis = [['配置口径', _text(risk.get('note')), None, None]]
    diagnosis.extend(['集中度提醒', _text(row), None, None] for row in _list(risk.get('alerts', []), 130))
    diagnosis.extend([_text(row.get(key)) for key in ('title', 'fact', 'meaning', 'action')]
                     for row in _rows(payload, 'diagnosis', 30))
    _table(book, '组合诊断', 'AnalysisDiagnosis', ['主题', '依据', '解释', '检查重点'], diagnosis,
           widths={1: 34, 2: 62, 3: 64, 4: 64}, height=80)

    output = BytesIO()
    book.save(output)
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or '组合'
    return f'STock-{safe_name[:60]}-{year}年组合分析-{stamp:%Y%m%d-%H%M%S}.xlsx', output.getvalue()
