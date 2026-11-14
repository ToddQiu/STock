"""Public company reporting periods and mainland ETF disclosures for analysis exports."""

import csv
import io
import json
import math
import re
from datetime import date, datetime
from html import unescape
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_DATA = 'https://datacenter-web.eastmoney.com/api/data/v1/get?'
_FUND = 'https://fundf10.eastmoney.com/'
_HKEX = 'https://www1.hkexnews.hk'
_STATEMENT_ITEMS = {
    '收入': ('营业收入', '经营收入总额', '营业总收入', '主营收入', '保险服务收入'),
    '归母净利': ('归属于母公司股东净利润', '归属于普通股股东净利润',
                 '本公司拥有人应占溢利', '股东应占溢利', '本公司股东应占溢利'),
    '每股收益': ('基本每股收益-普通股', '基本每股盈利', '基本每股收益'),
}
_CURRENCIES = {'人民币': 'CNY', '港元': 'HKD', '港币': 'HKD', '美元': 'USD',
               'CNY': 'CNY', 'HKD': 'HKD', 'USD': 'USD'}


def _read(url, referer):
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': referer})
    with urlopen(request, timeout=7) as response:
        data = response.read(512_001)
    if len(data) > 512_000:
        raise ValueError('财务来源响应过大')
    return data.decode('utf-8-sig')


def _rows(report, code, date_field, limit):
    query = urlencode({'reportName': report, 'columns': 'ALL', 'filter': f'(SECURITY_CODE="{code}")',
                       'sortColumns': date_field, 'sortTypes': '-1', 'pageSize': limit, 'pageNumber': 1,
                       'source': 'WEB', 'client': 'WEB'})
    payload = json.loads(_read(_DATA + query, 'https://data.eastmoney.com/'))
    rows = (payload.get('result') or {}).get('data') or []
    return [row for row in rows if isinstance(row, dict) and row.get('SECURITY_CODE') == code]


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return ''


def _value(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _metric(label, value, currency, *, growth=None):
    if (value := _value(value)) is None:
        return ''
    if label != '每股收益' and abs(value) >= 1e8:
        number = f'{value / 1e8:,.2f} 亿 {currency}'
    else:
        number = f'{value:,.4g} {currency}'
    if (growth := _value(growth)) is not None:
        number += f'（来源同比 {growth:+.2f}%）'
    return f'{label} {number}'


def _evidence(identifier, source, url, as_of, label, *, fund=False):
    return {'id': identifier, 'kind': 'fundamental', 'source': source, 'url': url,
            'as_of': as_of, 'label': label, 'asset_class': 'fund' if fund else 'stock'}


def company_profile_evidence(market, symbol, today):
    if market == 'CN' and re.fullmatch(r'\d{6}\.(SS|SZ|BJ)', symbol):
        code, exchange = symbol.split('.')
        provider = ('SH' if exchange == 'SS' else exchange) + code
        url = 'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?' + urlencode({'code': provider})
        payload = json.loads(_read(url, 'https://emweb.securities.eastmoney.com/'))
        if payload.get('Code') != provider or payload.get('SecurityCode') != code:
            return None
        row, dates = payload.get('jbzl') or {}, payload.get('fxxg') or {}
        fields = {'company_name': row.get('gsmc'), 'business': '登记经营范围：' + row['jyfw'] if row.get('jyfw') else None, 'description': row.get('gsjj'),
                  'founded_date': dates.get('clrq'), 'listing_date': dates.get('ssrq'), 'industry': row.get('sshy'),
                  'location': row.get('bgdz'), 'website': row.get('gswz')}
    elif market in {'HK', 'US'}:
        if market == 'HK' and not re.fullmatch(r'\d{4,5}\.HK', symbol):
            return None
        if market == 'US' and not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', symbol):
            return None
        code = symbol.split('.')[0].zfill(5) if market == 'HK' else symbol
        rows = _rows(f'RPT_{market}F10_INFO_ORGPROFILE', code, 'SECURITY_CODE', 1)
        if not rows or market == 'HK' and rows[0].get('SECUCODE') != f'{code}.HK':
            return None
        row = rows[0]
        url = (f'https://emweb.securities.eastmoney.com/PC_HKF10/CompanyProfile/index?type=web&code={code}'
               if market == 'HK' else f'https://quote.eastmoney.com/us/{code}.html')
        fields = {'company_name': row.get('ORG_NAME'), 'business': row.get('MAIN_BUSINESS') or row.get('ORG_PROFILE'),
                  'description': row.get('ORG_PROFILE'), 'founded_date': row.get('FOUND_DATE'),
                  'listing_date': row.get('LISTING_DATE'), 'industry': row.get('BELONG_INDUSTRY'),
                  'location': row.get('ADDRESS'), 'website': row.get('ORG_WEB')}
    else:
        return None
    fields = {key: _plain(str(value))[:1200] for key, value in fields.items()
              if value is not None and str(value).strip() not in {'', '--', '---'}}
    for key in ('founded_date', 'listing_date'):
        day = _day(fields.get(key))
        if day and day <= today:
            fields[key] = day
        else:
            fields.pop(key, None)
    if not fields.get('company_name'):
        return None
    return {'id': 'P2', 'kind': 'profile', 'source': '东方财富 · 公司概况', 'url': url, 'as_of': today,
            'label': '公司公开基本资料（成立／登记日期为来源所列主体口径，非上市日期；资料更新日期未披露，本日采集）',
            'fields': fields, 'asset_class': 'stock'}


def _holdings_classification(positions, market):
    from .security_master import cached_identity, normalize
    sectors = {}
    for row in positions:
        if row.get('industry') and row.get('industry_source'):
            sectors[row['industry']] = sectors.get(row['industry'], 0) + row['weight']
            continue
        try:
            holding_market = market
            if market == 'CN' and len(row.get('symbol', '')) in {4, 5}:
                holding_market = 'HK'
            identity = cached_identity(*normalize(holding_market, row.get('symbol', ''))) or {}
        except ValueError:
            identity = {}
        if industry := identity.get('industry'):
            row.update(industry=industry, industry_source=identity.get('source'),
                       industry_source_url=identity.get('source_url'), industry_as_of=identity.get('as_of'))
            sectors[industry] = sectors.get(industry, 0) + row['weight']
    return {'sectors': [{'name': name, 'weight': weight} for name, weight in sorted(sectors.items(), key=lambda item: -item[1])],
            'classification_coverage_pct': sum(sectors.values()),
            'classification_note': '行业按可核实公开分类汇总，仅覆盖已取得分类的底仓；未归一化为全基金占比'}


def foreign_fund_evidence(symbol, today):
    from .etf_sources import holdings_source, _http
    from .etf_research import parse_import
    source = holdings_source(symbol)
    if not source:
        return []
    url, suffix = source
    raw = _http.fetch(url, timeout=7, ttl=0, max_bytes=1_000_000)
    industries = {}
    if suffix == '.csv':
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        header = next((i for i, row in enumerate(rows) if {'Ticker', 'Name', 'Sector'} <= set(row)), None)
        if header is not None:
            columns = rows[header]
            end = next((i for i in range(header + 1, len(rows)) if not rows[i]), len(rows))
            for row in rows[header + 1:end]:
                if len(row) > max(columns.index('Ticker'), columns.index('Sector')):
                    industries[row[columns.index('Ticker')]] = row[columns.index('Sector')]
            # Issuer legal footers are outside the dated holdings table.
            cleaned = io.StringIO()
            csv.writer(cleaned).writerows(rows[:end])
            raw = cleaned.getvalue().encode('utf-8')
    fund = parse_import(raw, symbol + suffix, symbol=symbol)[symbol]
    if not _day(fund['as_of']) or fund['as_of'] > today:
        return []
    positions = sorted(fund['holdings'], key=lambda row: -row['weight'])
    for row in positions:
        if industry := industries.get(row['symbol']):
            row.update(industry=industry, industry_source='ETF 发行商 · 官方持仓文件',
                       industry_source_url=url, industry_as_of=fund['as_of'])
    return [{**_evidence('F2', 'ETF 发行商 · 官方持仓文件', url, fund['as_of'],
                        f"发行商底层持仓（截至 {fund['as_of']}，{len(positions)} 项，按文件原始权重）", fund=True),
             'holdings': positions, 'coverage': 'issuer_holdings', 'coverage_pct': sum(row['weight'] for row in positions),
             **_holdings_classification(positions, 'US')}]


def company_evidence(market, symbol, today):
    if market == 'CN' and re.fullmatch(r'\d{6}\.(SS|SZ|BJ)', symbol):
        code, exchange = symbol.split('.')
        rows = _rows('RPT_LICO_FN_CPD', code, 'REPORTDATE', 8)
        qualified = []
        provider_symbol = f"{code}.{'SH' if exchange == 'SS' else exchange}"
        for row in rows:
            end, filed = _day(row.get('REPORTDATE')), _day(row.get('NOTICE_DATE'))
            if row.get('SECUCODE') == provider_symbol and end and filed and end <= filed <= today:
                qualified.append((end, filed, row))
        if not qualified:
            return None
        end, filed, row = max(qualified, key=lambda item: item[:2])
        metrics = [_metric('营收', row.get('TOTAL_OPERATE_INCOME'), 'CNY', growth=row.get('YSTZ')),
                   _metric('归母净利', row.get('PARENT_NETPROFIT'), 'CNY', growth=row.get('SJLTZ')),
                   _metric('每股收益', row.get('BASIC_EPS'), 'CNY/股')]
        if any(metrics):
            return _evidence('F1', '东方财富 · 业绩报表', f'https://data.eastmoney.com/notices/stock/{code}.html',
                             end, ' · '.join(filter(None, [f'报告期截至 {end}（累计）', f'披露 {filed}', *metrics])))
        return None
    if market == 'HK' and re.fullmatch(r'\d{4,5}\.HK', symbol):
        code = symbol.split('.')[0].zfill(5)
        report, url = 'RPT_HKF10_FN_INCOME', f'https://emweb.securities.eastmoney.com/PC_HKF10/NewFinancialAnalysis/index?type=web&code={code}'
    elif market == 'US' and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', symbol):
        code = symbol
        report, url = 'RPT_USF10_FN_INCOME', f'https://quote.eastmoney.com/us/{code}.html'
    else:
        return None
    rows = _rows(report, code, 'REPORT_DATE', 100)
    periods = {}
    for row in rows:
        if market == 'HK' and row.get('SECUCODE') != f'{code}.HK':
            continue
        end, start = _day(row.get('REPORT_DATE')), _day(row.get('START_DATE'))
        currency = _CURRENCIES.get(row.get('CURRENCY'))
        if currency and start and end and start < end <= today:
            periods.setdefault((end, start, currency), {})[row.get('ITEM_NAME')] = row
    for (end, start, currency), items in sorted(periods.items(), reverse=True):
        metrics = []
        for label, names in _STATEMENT_ITEMS.items():
            item = next((items[name] for name in names if name in items), {})
            metrics.append(_metric(label, item.get('AMOUNT'), currency + ('/股' if label == '每股收益' else ''),
                                   growth=item.get('YOY_RATIO')))
        if any(metrics):
            return _evidence('F1', '东方财富 · 利润表', url, end,
                             ' · '.join(filter(None, [f'来源报告期 {start} 至 {end}', '披露日期待核对', *metrics])))
    return None


def _plain(markup):
    return ' '.join(unescape(re.sub(r'<[^>]*>', '', markup)).split())


def hk_filing_evidence(symbol, today):
    if not re.fullmatch(r'\d{4,5}\.HK', symbol):
        return None
    code = symbol.split('.')[0].zfill(5)
    search = _HKEX + '/search/titlesearch.xhtml'
    prefix = urlencode({'lang': 'EN', 'type': 'A', 'name': code, 'market': 'SEHK', 'callback': 'stockLookup'})
    response = _read(_HKEX + '/search/prefix.do?' + prefix, search).strip()
    match = re.fullmatch(r'callback\((.*)\);?', response, re.S)
    if not match:
        return None
    stocks = json.loads(match.group(1)).get('stockInfo', [])
    stock_id = next((row.get('stockId') for row in stocks if row.get('code') == code), None)
    if not isinstance(stock_id, int):
        return None
    page = _read(search + '?' + urlencode({'category': 0, 'lang': 'EN', 'market': 'SEHK', 'stockId': stock_id}), search)
    filings = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S):
        if not re.search(r'\b(?:Quarterly|Interim|Annual|Final) (?:Results|Report)\b', row, re.I):
            continue
        security = re.search(r'Stock Code:\s*</span>\s*(\d{5})\s*</td>', row)
        release = re.search(r'Release Time:\s*</span>\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})', row)
        document = re.search(r'<a href="(/listedco/listconews/sehk/\d{4}/\d{4}/\d+\.pdf)"[^>]*>(.*?)</a>', row, re.S)
        if not security or security.group(1) != code or not release or not document:
            continue
        stamp = datetime.strptime(' '.join(release.groups()), '%d/%m/%Y %H:%M')
        if stamp.date().isoformat() <= today:
            filings.append((stamp, document.group(1), _plain(document.group(2))[:200]))
    if not filings:
        return None
    stamp, path, title = max(filings)
    return {**_evidence('F2', '港交所披露易 · 公告入口', _HKEX + path, stamp.date().isoformat(),
                        f'披露 {stamp:%Y-%m-%d %H:%M} 香港时间 · {title} · 正文数据待核对'),
            'asset_class': 'filing_index'}


def fund_evidence(symbol, today):
    code = symbol.split('.')[0]
    if not re.fullmatch(r'\d{6}', code):
        return []
    profile = _FUND + f'jbgk_{code}.html'
    html = _read(profile, _FUND)
    def field(label):
        match = re.search(r'<th[^>]*>\s*' + re.escape(label) + r'\s*</th>\s*<td[^>]*>(.*?)(?:</td>|<th)', html, re.S)
        value = _plain(match.group(1)) if match else ''
        return value[:120] if value and not value.startswith('---') else ''
    if not field('基金代码').startswith(code):
        return []
    size = re.search(r'净资产规模[：:]\s*<span>\s*([\d.,]+\s*[亿万]?元)\s*（截止至：(\d{4}-\d{2}-\d{2})）', html)
    as_of = _day(size.group(2)) if size else ''
    values = [(key, field(key)) for key in ('跟踪标的', '业绩比较基准', '管理费率', '托管费率',
                                          '基金全称', '基金管理人', '成立日期/规模')]
    if as_of and as_of <= today:
        values.append(('净资产规模', f'{size.group(1)}（{as_of}）'))
    else:
        as_of = ''
    label = ' · '.join(f'{key} {value}' for key, value in values if value)
    focus = values[0][1] or values[1][1]
    result = [{**_evidence('F1', '天天基金 · 产品概况', profile, as_of, label, fund=True),
               'investment_focus': focus, 'fields': {key: value for key, value in values if value}}] if label else []
    holdings = _FUND + f'ccmx_{code}.html'
    try:
        html = _read(_FUND + 'FundArchivesDatas.aspx?' + urlencode({'type': 'jjcc', 'code': code, 'topline': 10}), _FUND)
    except (OSError, ValueError):
        return result
    blocks = re.findall(r'截止至：\s*<font[^>]*>(\d{4}-\d{2}-\d{2})</font>.*?<tbody>(.*?)</tbody>', html, re.S)
    for end, table in sorted(blocks, reverse=True):
        if not _day(end) or end > today:
            continue
        entries, positions = [], []
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)[:10]:
            cells = [_plain(value) for value in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
            if (len(cells) >= 7 and cells[2] and re.fullmatch(r'\d+(?:\.\d+)?%', cells[6])
                    and 0 <= float(cells[6][:-1]) <= 100):
                entries.append(f'{cells[2][:50]} {cells[6]}')
                positions.append({'symbol': cells[1] if re.fullmatch(r'\d{4,6}', cells[1]) else '',
                                  'name': cells[2][:50], 'weight': float(cells[6][:-1])})
        if entries:
            result.append({**_evidence('F2', '天天基金 · 基金定期披露', holdings, end,
                                      f'披露重仓（报告期末 {end}，占基金净值） · ' + '、'.join(entries), fund=True),
                           'holdings': positions, 'coverage': 'disclosed_top_holdings',
                           'coverage_pct': sum(row['weight'] for row in positions),
                           **_holdings_classification(positions, 'CN')})
            break
    return result
