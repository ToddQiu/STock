"""Readable PDF of the same validated snapshot used by the analysis workbook."""
from datetime import date, datetime
from io import BytesIO

from reportlab.graphics.shapes import Drawing, Line, PolyLine, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .briefing_pdf import _RENDER_LOCK, _fonts, _font_markup, _linked, _INK, _BLUE, _UP, _DOWN
from .portfolio_analysis_export import analysis_workbook


def build_analysis_pdf(account, payload, *, captured_at):
    filename, book = analysis_workbook(account, payload, captured_at=captured_at)
    with _RENDER_LOCK:
        return filename[:-5] + '.pdf', _render(book)


def _render(book):
    fonts, unsupported = _fonts(), set()
    face, width = fonts[0][0], 174 * mm
    body = ParagraphStyle('analysis-body', fontName=face, fontSize=9, leading=14, wordWrap='CJK', textColor=_INK)
    small = ParagraphStyle('analysis-small', parent=body, fontSize=8, leading=12, textColor=colors.HexColor('#657187'))
    heading = ParagraphStyle('analysis-heading', parent=body, fontSize=13, leading=20, spaceBefore=14, spaceAfter=8, keepWithNext=True, textColor=_BLUE)
    title = ParagraphStyle('analysis-title', parent=body, fontSize=21, leading=30, spaceAfter=8)
    numeric = ParagraphStyle('analysis-numeric', parent=body, alignment=TA_RIGHT)
    story, output = [], BytesIO()

    def p(value, style=body, url=None):
        markup = _font_markup(value, fonts, unsupported)
        return Paragraph(_linked(markup, url) if url else markup, style)

    def fmt(value, percent=False, decimals=2):
        if value is None:
            return '未取得'
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M')
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, (float, int)):
            return f'{value * 100:.2f}%' if percent else f'{value:,.{decimals}f}'
        return str(value)

    def money(value, percent=False):
        style = ParagraphStyle('signed', parent=numeric, textColor=colors.HexColor(_UP if value and value > 0 else _DOWN) if value else _INK)
        return p(fmt(value, percent), style)

    def rows(sheet):
        return list(book[sheet].iter_rows(min_row=2, values_only=True))

    def table(headers, data, fractions):
        content = [[p(h, small) for h in headers]] + [[v if isinstance(v, Paragraph) else p(v) for v in row] for row in data]
        if not data:
            content.append([p('未取得可用数据')] + [p('') for _ in headers[1:]])
        grid = Table(content, colWidths=[width * f for f in fractions], repeatRows=1, hAlign='LEFT', splitInRow=1)
        grid.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EDF2F8')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, 0), .5, colors.HexColor('#CFD8E6')),
        ]))
        story.append(grid)

    summary_rows = rows('组合分析')
    summary = {r[0]: r[1] for r in summary_rows}
    notes = {r[0]: r[2] for r in summary_rows}
    cells = {r[0].value: r[1] for r in book['组合分析'].iter_rows(min_row=2)}
    name, year = summary['组合名称'], summary['统计年份']
    story += [p('持仓组合分析报告', title), p(f'{name}  |  {year} 年  |  导出 {fmt(summary["导出时间（北京时间）"])} 北京时间', small), Spacer(1, 10)]
    partial = '共同有效区间模拟收益率' in summary
    keys = ['共同有效区间模拟收益率' if partial else '模拟收益率', '共同有效区间最大回撤' if partial else '最大回撤',
            '共同有效区间年化波动率' if partial else '年化波动率', '持仓平均相关系数']
    table(keys, [[money(summary.get(k), '%' in cells[k].number_format) if i < 2 else p(fmt(summary.get(k), '%' in cells[k].number_format, 3 if i == 3 else 2), numeric) for i, k in enumerate(keys)]], [.25]*4)
    story += [Spacer(1, 8), p(summary['年度分析状态'], small), p(notes['收益性质'], small)]
    selected = [k for k in summary if k.startswith('总资产（')] + ['最大持仓占比', '前五大持仓占比', '现金占总资产比例', '行业覆盖率', '模拟起始基准日', '模拟结束日']
    table(['指标', '数值'], [[k, p(fmt(summary[k], '%' in cells[k].number_format), numeric)] for k in selected], [.58, .42])

    path = rows('模拟净值')
    valid = [(i, r[1]) for i, r in enumerate(path) if r[1] is not None]
    if len(valid) >= 2:
        story.append(p('模拟净值', heading))
        chart = Drawing(width, 130)
        lo, hi = min(v for _, v in valid), max(v for _, v in valid)
        padding = max((hi-lo)*.1, .01)
        lo, hi = lo-padding, hi+padding
        for value in (lo, (lo+hi)/2, hi):
            y = 22+(value-lo)/(hi-lo)*94
            chart.add(Line(40, y, width-5, y, strokeColor=colors.HexColor('#DFE5ED'), strokeWidth=.5))
            chart.add(String(0, y-3, f'{value:.3f}', fontName=face, fontSize=8, fillColor=_INK))
        # Keep missing observations as gaps rather than connecting across unavailable data.
        segment = []
        for i, row in enumerate(path):
            if row[1] is None:
                if len(segment) > 2: chart.add(PolyLine(segment, strokeColor=_BLUE, strokeWidth=1.5))
                segment = []
            else:
                segment.extend([40+i/(len(path)-1)*(width-45), 22+(row[1]-lo)/(hi-lo)*94])
        if len(segment) > 2: chart.add(PolyLine(segment, strokeColor=_BLUE, strokeWidth=1.5))
        chart.add(String(40, 5, fmt(path[0][0]), fontName=face, fontSize=8))
        chart.add(String(width-5, 5, fmt(path[-1][0]), fontName=face, fontSize=8, textAnchor='end'))
        story.append(chart)

    story.append(p('组合诊断', heading))
    for label, fact, meaning, action in rows('组合诊断'):
        if not any((fact, meaning, action)): continue
        story.append(p(label, ParagraphStyle('diagnosis', parent=body, spaceBefore=8, keepWithNext=True, textColor=_BLUE)))
        for text in (fact, meaning, action):
            if text: story.append(p(text))

    story.append(p('资产配置与持仓', heading))
    table(['分类', '名称', '金额', '占比', '分母'], [[a, b, p(fmt(c), numeric), p(fmt(d, True), numeric), f] for a, b, c, d, _, f in rows('资产配置')], [.12, .25, .22, .16, .25])
    story += [Spacer(1, 10), p('持仓金额使用组合计价币种；原币与报价时点见资料来源。', small)]
    table(['标的', '数量', '市值', '浮动盈亏', '盈亏率'], [[f'{r[1]}\n{r[0]}', p(fmt(r[4]), numeric), p(fmt(r[6]), numeric), money(r[8]), money(r[9], True)] for r in rows('持仓与来源')], [.30, .15, .20, .20, .15])

    story.append(p('相关性', heading))
    story.append(p(notes['相关性口径'], small))
    pairs = rows('相关性配对')
    periods = list(dict.fromkeys(r[0] for r in pairs))
    for period in periods:
        group = [r for r in pairs if r[0] == period]
        chosen = sorted(group, key=lambda r: (r[5] is None, -abs(r[5] or 0)))[:20]
        story.append(p(f'{period}：共 {len(group)} 对，展示 {len(chosen)} 对（按相关系数绝对值排序）', small))
        table(['标的配对', '系数', '样本区间数', '区间 / 缺项'], [[f'{r[2]} / {r[4]}', p(fmt(r[5], decimals=3), numeric), p(fmt(r[6], decimals=0), numeric), r[10] or f'{r[7] or "未取得"} 至 {r[8] or "未取得"}'] for r in chosen], [.36, .14, .18, .32])
    if not periods: story.append(p('未取得有效配对。', small))

    story.append(p('ETF 穿透与重复敞口', heading))
    exposure = rows('ETF穿透与重叠')
    for kind in ('已知重复敞口', '已披露底层敞口', '基金配对'):
        group = [r for r in exposure if r[0] == kind]
        if not group: continue
        chosen = sorted(group, key=lambda r: -(r[6 if kind == '基金配对' else 3] or 0))[:30]
        story.append(p(f'{kind}：共 {len(group)} 项，展示 {len(chosen)} 项', small))
        if kind == '基金配对':
            table(['基金配对', '共同股票数', '重叠率 / 缺项'], [[f'{r[1]} / {r[2]}', fmt(r[5], decimals=0), r[8] or fmt(r[6], True)] for r in chosen], [.5, .2, .3])
        else:
            table(['底层标的', '来源持仓', '穿透市值', '占比'], [[r[1], r[2], p(fmt(r[3]), numeric), p(fmt(r[4], True), numeric)] for r in chosen], [.27, .30, .25, .18])
    for r in exposure:
        if r[0] == '资料说明': story.append(p(r[8], small))
        elif r[0] == '基金报告期': story.append(p(f'{r[1]}  |  披露期末 {fmt(r[7])}  |  {r[8] or "来源未取得"}', small, r[9]))
    if not exposure: story.append(p('未取得可用底仓披露，不代表不存在重复敞口。', small))

    story.append(p('资料来源与计算口径', heading))
    story.append(p('完整相关性配对、逐日净值及底仓明细可另导出 Excel。', small))
    for label, value, note in summary_rows:
        if '采集时间' in label or label in ('资料说明', '计算口径'):
            story.append(p(f'{label}：{fmt(value) if value is not None else ""} {note or ""}', small))
    for r in rows('年度权重与来源'):
        story.append(p(f'{r[1]} ({r[0]})  |  年度行情 {r[5] or "来源未取得"}  |  初始模拟权重 {fmt(r[4], True)}' + (f'  |  {r[7]}' if r[7] else ''), small, r[6]))
    for r in rows('持仓与来源'):
        story.append(p(f'{r[1]}  |  {r[3] or "币种未取得"}  |  {r[11] or "报价来源未取得"}  |  {fmt(r[10])}  |  {r[12] or "状态未取得"}', small, r[13]))
        if r[14]: story.append(p(f'分类：{r[14]}  |  {r[15] or "来源未取得"}  |  {fmt(r[16])}', small, r[17]))
        if r[18]: story.append(p(f'短窗日线：{r[18]}  |  最后收盘 {fmt(r[19])}  |  采集 {fmt(r[20])}', small))
        if r[21]: story.append(p(r[21], small))
    if rows('估值汇率'):
        table(['汇率对', '报价', '来源', '时点 / 状态'], [[r[0], fmt(r[1], decimals=4), r[2], f'{fmt(r[3])} / {r[4] or "未取得"}'] for r in rows('估值汇率')], [.18, .17, .25, .40])
    if unsupported: story.append(p('字体缺失字符以 Unicode 编号显示。', small))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#CFD8E6'))
        canvas.line(18*mm, 15*mm, A4[0]-18*mm, 15*mm)
        canvas.setFont(face, 8)
        canvas.drawString(18*mm, 10*mm, 'STock | Portfolio Analysis')
        canvas.drawRightString(A4[0]-18*mm, 10*mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                           topMargin=16*mm, bottomMargin=21*mm, title=f'{name} · {year}年持仓组合分析报告', author='STock')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
