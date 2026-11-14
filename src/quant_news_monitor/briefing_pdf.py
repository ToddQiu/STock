"""Offline PDF rendering of saved briefings and instrument analyses."""
from __future__ import annotations

import math
import os
import re
import threading
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle


_RENDER_LOCK = threading.Lock()  # ReportLab font subsetting uses shared font objects.
_INK = colors.HexColor("#263448")
_MUTED = colors.HexColor("#657187")
_BLUE = colors.HexColor("#285bb3")
_UP = "#bd3546"
_DOWN = "#178166"
_REFERENCE = re.compile(r"\[([QNE]\d+)\]")
SIGNALS = {"bullish": "利多", "bearish": "利空", "neutral": "中性", "mixed": "多空分歧", "unknown": "待核实"}
STATUSES = {"ready": "完整", "partial": "部分可用", "unavailable": "不可用", "checked": "已核对", "missing_quote": "缺少有效收盘行情"}
ROLES = {"index": "本市场指数", "overseas": "外盘参考", "holding": "持仓", "whitelist": "白名单"}
MARKET_NAMES = {"CN": "A股", "US": "美股", "HK": "港股", "KR": "韩股"}
CHECKPOINTS = (("intraday", "首个后续交易日盘中"), ("next_day", "首个后续交易日收盘"), ("week", "一周后（第5个交易日）"))
CHECKPOINT_LABELS = {"pending": "待到期", "unavailable": "缺少数据", "aligned": "方向一致",
                     "opposed": "方向相反", "flat": "表现持平", "observed": "已观察"}


def _font_paths():
    # Use fonts licensed on the user's computer. Never copy Windows fonts into
    # the application package. TTFont embeds only the glyphs used by this PDF.
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    return [windows / name for name in ("simkai.ttf", "msyh.ttc", "simhei.ttf", "malgun.ttf")] + [
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
    ]


def _fonts():
    result = []
    for index, path in enumerate(_font_paths()):
        if not path.is_file():
            continue
        name = f"STockBriefing{index}"
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                candidate = TTFont(name, str(path))
                # OS/2 embedding flags: restricted, no subsetting, or bitmap only.
                # None of these can be satisfied by our subset-outline PDFs.
                flags = int.from_bytes(candidate.face.get_table("OS/2")[8:10], "big")
                if flags & 0x0302:
                    continue
                pdfmetrics.registerFont(candidate)
            font = pdfmetrics.getFont(name)
            result.append((name, font.face.charToGlyph))
        except Exception:
            continue
    if not result or not any(ord("报") in cmap for _, cmap in result):
        raise ValueError("未找到可嵌入的中文字体；请安装 Windows 中文字体（楷体、微软雅黑或黑体）后重试")
    # The first usable Chinese font is the document font; other local fonts
    # cover foreign names such as Hangul without replacing Chinese typography.
    result.sort(key=lambda row: ord("报") not in row[1])
    return result


def _number(value, *, percent=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "暂无"
    return f"{value:+.2f}%" if percent else f"{value:,.4f}".rstrip("0").rstrip(".")


def _font_markup(value, fonts, unsupported):
    value = "暂无" if value is None or value == "" else str(value)
    face = fonts[0][0]
    parts, current, buffer = [], face, []
    for char in value.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            name = current
        else:
            name = next((name for name, cmap in fonts if ord(char) in cmap), None)
            if name is None:
                if char.isspace():
                    char, name = " ", face
                else:
                    unsupported.add(ord(char))
                    char, name = f"[U+{ord(char):04X}]", face
        if name != current:
            parts.append(f'<font name="{current}">{escape("".join(buffer)).replace(chr(10), "<br/>")}</font>')
            buffer, current = [], name
        buffer.append(char)
    parts.append(f'<font name="{current}">{escape("".join(buffer)).replace(chr(10), "<br/>")}</font>')
    return "".join(parts)


def _linked(markup, url):
    try:
        valid = urlsplit(str(url or ""))
        safe = valid.scheme in {"http", "https"} and bool(valid.netloc)
    except ValueError:
        safe = False
    return f'<link href="{escape(str(url), quote=True)}" color="#285bb3">{markup}</link>' if safe else markup


def render_instrument_analysis_pdf(name, target, analysis, *, previous=False):
    return _render_analysis_pdf(name, [(name, target, analysis, previous)], portfolio=False)


def render_portfolio_analysis_pdf(name, entries, *, outcomes=None, generated_at=None, portfolio_analysis=None):
    return _render_analysis_pdf(name, entries, portfolio=True, outcomes=outcomes, generated_at=generated_at, portfolio_analysis=portfolio_analysis)


def _render_analysis_pdf(name, entries, *, portfolio, outcomes=None, generated_at=None, portfolio_analysis=None):
    output = BytesIO()
    with _RENDER_LOCK:
        fonts = _fonts()
        face = fonts[0][0]
        unsupported = set()
        body = ParagraphStyle("instrument-body", fontName=face, fontSize=11, leading=18, wordWrap="CJK",
                              textColor=_INK, spaceAfter=8)
        title = ParagraphStyle("instrument-title", parent=body, fontSize=23, leading=29, spaceAfter=8)
        heading = ParagraphStyle("instrument-heading", parent=body, fontSize=13, leading=19,
                                 textColor=_BLUE, spaceBefore=8, spaceAfter=4, keepWithNext=True)
        small = ParagraphStyle("instrument-source", parent=body, fontSize=8.5, leading=12,
                               textColor=_MUTED, spaceAfter=5)
        table_body = ParagraphStyle("instrument-table", parent=body, fontSize=10, leading=15, spaceAfter=0)
        refs = re.compile(r"[\[【［]([QFNPT]\d+)[\]】］]")

        def text(value):
            return _font_markup(value, fonts, unsupported)

        def timestamp(value):
            if not value or len(value) == 10:
                return value or "时间待核实"
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return stamp.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S 北京时间") if stamp.tzinfo else value
            except ValueError:
                return value

        def instrument_section(instrument, target, analysis, previous):
            inputs = analysis.get("data_inputs")
            evidence = {row["id"]: row for row in [*(inputs or []), *analysis.get("evidence", [])]}
            asset_type = str(analysis.get("asset_type") or "").lower()
            fund = asset_type in {"etf", "etn", "fund"}
            report_label = "ETF分析" if asset_type in {"etf", "etn"} else "基金分析" if fund else "个股分析"

            def para(value, style=body):
                markup = []
                for index, part in enumerate(refs.split(str(value))):
                    if index % 2 and part in evidence:
                        marker = _linked(text(f"[{part}]"), evidence[part].get("url"))
                        markup.append(f'<super size="7" rise="3">{marker}</super>')
                    elif part:
                        markup.append(text(f"[{part}]" if index % 2 else part))
                return Paragraph("".join(markup), style)

            def data_table(headers, rows, widths):
                cells = [[para(value, table_body) for value in row] for row in [headers, *rows]]
                result = Table(cells, colWidths=[width * mm for width in widths], hAlign="LEFT", repeatRows=1)
                result.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f8")),
                    ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#dfe5ed")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                return result

            def source_line(row):
                source = f"[{row['id']}] {row.get('source') or '来源待核实'}  ·  {timestamp(row.get('as_of'))}"
                return Paragraph(_linked(text(source), row.get("url")), small)

            metadata = [target, analysis.get("workflow"), analysis.get("model"),
                        timestamp(analysis.get("generated_at"))]
            story = [para("STock  /  " + report_label, small), para(instrument, title),
                     para("  ·  ".join(filter(None, metadata)), small)]
            if previous:
                story.append(para("上次已生成的分析", small))
            if analysis.get('custom_analysis'):
                story.extend([para('自定义 Skill 分析', heading), para(analysis['custom_analysis'])])
            rating = f"研究评级  {analysis.get('rating') or '待评级'}" if analysis.get("summary") else "本轮 AI 判断暂未生成"
            story.append(para(rating, heading))
            for key, label in (("summary", "综合判断"), ("rating_reason", "评级依据"),
                               ("risks", "风险与反证"), ("rating_condition", "复核条件"),
                               ("drivers", "驱动因素"), ("watch", "观察重点")):
                if analysis.get(key):
                    story.extend([para(label, heading), para(analysis[key])])
            listed = set()
            detailed = analysis.get("report_sections")
            profiles = [row for row in inputs or [] if row.get("fields") and
                        (row["kind"] == "profile" or fund and row["kind"] == "fundamental")]
            if detailed is not None or profiles:
                story.append(para("产品基本资料" if fund else "公司基本资料", heading))
                labels = {"company_name": "公司名称", "business": "主营业务", "description": "公司简介",
                          "founded_date": "成立／登记日期（来源口径）", "listing_date": "上市日期",
                          "industry": "行业", "sector": "板块", "location": "所在地", "website": "公司网站",
                          "name": "证券名称", "asset_type": "证券类型", "investment_focus": "投资方向",
                          "market": "市场", "symbol": "证券代码"}
                if not profiles:
                    story.append(para("本轮未取得可核验的基本资料。", small))
                for row in profiles:
                    fields = row["fields"]
                    details = [[labels.get(key, key), value if value not in (None, "") else "本轮未取得披露"]
                               for key, value in fields.items() if key not in {"business", "description"}]
                    if not fund and row["kind"] == "profile" and "company_name" in fields and "founded_date" not in fields:
                        details.append(["成立／登记日期（来源口径）", "本轮未取得披露"])
                    if details:
                        story.append(data_table(["项目", "资料"], details, [39, 135]))
                    for key in ("business", "description"):
                        if fields.get(key):
                            story.extend([para(labels[key], heading), para(fields[key])])
                    story.append(source_line(row))
                    listed.add(row["id"])
            if fund and (detailed is not None or any(row.get("holdings") for row in inputs or [])):
                story.append(para("底层持仓穿透", heading))
                holdings_sources = [row for row in inputs or [] if row.get("holdings")]
                if not holdings_sources:
                    story.append(para("本轮未取得可核验的底层持仓披露，无法确认成分权重及行业敞口。", small))
                for row in holdings_sources:
                    story.append(para(f"披露期／数据日期  {timestamp(row.get('as_of'))}", small))
                    holdings = sorted(row["holdings"], key=lambda position: position.get("weight") or 0, reverse=True)[:20]
                    positions = [[position.get("name") or "名称待核实", position.get("symbol") or "待核实",
                                  f"{position['weight']:.2f}%" if isinstance(position.get("weight"), (float, int)) else "待核实"]
                                 for position in holdings]
                    if len(row["holdings"]) > len(holdings):
                        shown = sum(position.get("weight") or 0 for position in holdings)
                        story.append(para(f"本轮采集 {len(row['holdings'])} 项，表内列示权重最高 {len(holdings)} 项，合计 {shown:.2f}%。", small))
                    story.append(data_table(["底层标的", "代码", "占基金净值"], positions, [89, 45, 40]))
                    coverage = row.get("coverage_pct")
                    if isinstance(coverage, (float, int)):
                        story.append(para(f"本轮采集持仓权重合计 {coverage:.2f}% · 披露持仓不代表当前实时持仓。", small))
                    else:
                        story.append(para("持仓覆盖比例待核实；披露持仓不代表当前实时持仓。", small))
                    sectors = row.get("sectors") or []
                    if sectors:
                        story.append(para("已核实行业分布", heading))
                        story.append(data_table(["行业", "占基金净值"], [
                            [sector.get("name") or "行业待核实", f"{sector['weight']:.2f}%"]
                            for sector in sectors if isinstance(sector.get("weight"), (float, int))], [134, 40]))
                        classified = row.get("classification_coverage_pct")
                        if isinstance(classified, (float, int)):
                            story.append(para(f"已分类持仓覆盖 {classified:.2f}%；其余部分未推断行业。", small))
                    else:
                        story.append(para("未取得完整行业权重；不能将已披露重仓视为全部行业敞口。", small))
                    story.append(source_line(row))
                    listed.add(row["id"])
            if detailed is not None:
                sections = (("fund_analysis", "产品与基金基本面"), ("holdings_analysis", "持仓穿透分析"),
                            ("industry_analysis", "行业与产业链分析"), ("macro_analysis", "宏观环境判断"),
                            ("technical_analysis", "技术面分析")) if fund else (
                            ("business_analysis", "主营业务与商业模式"), ("fundamental_analysis", "公司基本面分析"),
                            ("macro_analysis", "宏观环境判断"), ("micro_analysis", "微观环境与竞争格局"),
                            ("technical_analysis", "技术面分析"))
                for key, caption in sections:
                    story.extend([para(caption, heading), para(detailed.get(key) or "本轮证据不足，尚未形成可核验判断。")])
            if inputs is not None:
                story.append(para("基本面、技术面与行情", heading))
                for kind, caption in (("fundamental", "ETF / 基金基本面" if fund else "公司基本面"),
                                      ("technical", "技术面 · 已完成日线"), ("quote", "行情 / 净值")):
                    rows = [row for row in inputs if row["kind"] == kind]
                    if rows and all(row["id"] in listed for row in rows):
                        continue
                    story.append(para(caption, heading))
                    if not rows:
                        story.append(para("本轮未取得可核验数据", small))
                    elif all(row.get("asset_class") == "filing_index" for row in rows):
                        story.append(para("本轮仅取得公告入口，财务数据待核对", small))
                    for row in rows:
                        if row["id"] in listed:
                            continue
                        listed.add(row["id"])
                        story.append(para(row["label"]))
                        story.append(source_line(row))
            sources = [(identifier, row) for identifier, row in evidence.items() if identifier not in listed]
            if sources:
                story.append(para("引用来源", heading))
                for identifier, row in sources:
                    label = f"[{identifier}] {row.get('label') or '来源'}"
                    details = f"  ·  {row.get('source') or '来源待核实'}  ·  {timestamp(row.get('as_of'))}"
                    story.append(Paragraph(_linked(text(label), row.get("url")) + text(details), small))
            return story

        story = []
        asset_type = str((entries[0][2] or {}).get("asset_type") or "").lower() if entries else ""
        label = "全组合 AI 分析" if portfolio else "ETF分析" if asset_type in {"etf", "etn"} else "基金分析" if asset_type == "fund" else "个股分析"
        if portfolio:
            outcomes = outcomes or {}
            available = sum(analysis is not None and target not in outcomes for _, target, analysis, _ in entries)
            story.extend([Paragraph(text(f"STock  /  {label}"), small), Paragraph(text(name), title)])
            if generated_at:
                story.append(Paragraph(text(f"本轮生成  {generated_at:%Y-%m-%d %H:%M:%S} 北京时间"), small))
            story.append(Paragraph(text(f"已收录 {available} / {len(entries)} 个持仓标的的 AI 判断"), heading))
            if generated_at:
                counts = {kind: sum(bool(analysis and any(row["kind"] == kind and row.get("asset_class") != "filing_index"
                                                        for row in analysis.get("data_inputs", [])))
                                    for _, _, analysis, _ in entries)
                          for kind in ("fundamental", "technical", "quote")}
                coverage = f"附数据的标的  ·  基本面 {counts['fundamental']}  ·  技术面 {counts['technical']}  ·  行情 {counts['quote']}"
                story.append(Paragraph(text(coverage), small))
            rows = [["标的", "代码", "评级", "分析状态"]]
            rows.extend([instrument, target, (analysis.get("rating") or "待评级") if analysis and target not in outcomes else "—",
                         outcomes.get(target) or (("上次已生成" if previous else "已生成") if analysis else "尚无分析")]
                        for instrument, target, analysis, previous in entries)
            cells = [[Paragraph(text(value), small if index else body) for value in row]
                     for index, row in enumerate(rows)]
            table = Table(cells, colWidths=[57*mm, 46*mm, 24*mm, 34*mm], hAlign="LEFT", repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f8")),
                                       ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#dfe5ed")),
                                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story.append(table)
            if portfolio_analysis:
                story.extend([Paragraph(text('组合 Skill 综合研判'), heading), Paragraph(text(portfolio_analysis), body)])
        for instrument, target, analysis, previous in entries:
            if analysis is not None:
                if story:
                    story.append(PageBreak())
                story.extend(instrument_section(instrument, target, analysis, previous))
        SimpleDocTemplate(output, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                          topMargin=17*mm, bottomMargin=17*mm, title=f"{name} · {label}").build(story)
    return output.getvalue()


def write_briefing_pdf(record, destination):
    """Write a frozen ready/partial report, atomically; return the output Path."""
    if record.get("status") not in {"ready", "partial"} or not isinstance(record.get("report"), dict):
        raise ValueError("仅可导出已完成的早晚报")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".pdf.tmp")
    with _RENDER_LOCK:
        try:
            _write(record, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def _write(record, destination):
    report = record["report"]
    fonts = _fonts()
    face = fonts[0][0]
    unsupported = set()
    width = A4[0] - 36 * mm - 12
    zone = str(report.get("window", {}).get("timezone") or "UTC")
    try:
        tz = ZoneInfo(zone)
    except (KeyError, ValueError):
        tz, zone = ZoneInfo("UTC"), "UTC"
    styles = {
        "body": ParagraphStyle("body", fontName=face, fontSize=11, leading=18, textColor=_INK,
                               wordWrap="CJK", spaceAfter=7, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName=face, fontSize=8.5, leading=12.5, textColor=_MUTED,
                                wordWrap="CJK", spaceAfter=4),
        "title": ParagraphStyle("title", fontName=face, fontSize=24, leading=31, textColor=_INK, spaceAfter=8),
        "heading": ParagraphStyle("heading", fontName=face, fontSize=15, leading=22, textColor=_BLUE,
                                  spaceBefore=14, spaceAfter=8, keepWithNext=True),
        "frontHeading": ParagraphStyle("frontHeading", fontName=face, fontSize=15, leading=22, textColor=_BLUE,
                                       spaceBefore=11, spaceAfter=6, keepWithNext=True),
        "item": ParagraphStyle("item", fontName=face, fontSize=12, leading=18, textColor=_INK,
                               wordWrap="CJK", spaceBefore=8, spaceAfter=5, keepWithNext=True),
        "table": ParagraphStyle("table", fontName=face, fontSize=10, leading=13, textColor=_INK, wordWrap="CJK"),
        "headline": ParagraphStyle("headline", fontName=face, fontSize=11.5, leading=16, textColor=_INK,
                                   wordWrap="CJK", spaceBefore=4, spaceAfter=2, keepWithNext=True),
        "headlineSummary": ParagraphStyle("headlineSummary", fontName=face, fontSize=9.5, leading=13.5,
                                          textColor=_INK, wordWrap="CJK", spaceAfter=2, keepWithNext=True),
        "source": ParagraphStyle("source", fontName=face, fontSize=8.25, leading=12, textColor=_MUTED,
                                 wordWrap="CJK", spaceAfter=4),
        "sourceHeading": ParagraphStyle("sourceHeading", fontName=face, fontSize=11, leading=15, textColor=_BLUE,
                                        spaceBefore=11, spaceAfter=5, keepWithNext=True),
    }
    narrative = report.get("narrative") or None
    evidence = {row["id"]: row for row in narrative.get("evidence", [])} if narrative else {}

    def text(value):
        return _font_markup(value, fonts, unsupported)

    def para(value, style="body"):
        if not isinstance(value, str) or "[" not in value:
            return Paragraph(text(value), styles[style])
        parts = _REFERENCE.split(value)
        markup = "".join(
            citation(part) if index % 2 else text(part) if part else ""
            for index, part in enumerate(parts)
        )
        return Paragraph(markup, styles[style])

    def moment(value, *, compact=False):
        if not value:
            return "暂无"
        try:
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
        if compact:
            return (instant.astimezone(tz) if instant.tzinfo else instant).strftime("%m-%d %H:%M")
        return instant.astimezone(tz).isoformat(timespec="seconds") if instant.tzinfo else str(value)

    def citation(identifier, url=None):
        url = url or evidence.get(identifier, {}).get("url")
        marker = _linked(text(f"[{identifier}]"), url)
        return f'<super size="7" rise="3">{marker}</super>'

    def link(label, url):
        label = label or "来源"
        caption = "打开原始来源" if report.get("market") == "GLOBAL" else str(url)
        markup = text(caption)
        anchor = _linked(markup, url)
        return Paragraph(f'{text(label)}：{anchor if anchor != markup else "无可用原文链接"}', styles["small"])

    def change(value, *, percent=True, style="body"):
        display = _number(value, percent=percent)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value:
            if not percent:
                display = f"{value:+,.2f}"
            display = f'<font color="{_UP if value > 0 else _DOWN}">{text(display)}</font>'
        else:
            display = text(display)
        return Paragraph(display, styles[style])

    def table(headers, rows, proportions, *, compact=False):
        data = [[para(value, "small") for value in headers]]
        data.extend([[cell if isinstance(cell, Paragraph) else para(cell, "table" if compact else "body") for cell in row] for row in rows])
        padding = 4 if compact else 6
        item = Table(data, colWidths=[width * part for part in proportions], repeatRows=1, hAlign="LEFT", splitInRow=1)
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), padding), ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
            ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#dfe5ed")),
        ]))
        return item

    def references(ids, label="依据"):
        markers = [citation(identifier) for identifier in dict.fromkeys(ids or []) if identifier in evidence]
        return Paragraph(text(label) + " " + " ".join(markers), styles["small"]) if markers else None

    def add_refs(story, ids, label="依据"):
        line = references(ids, label)
        if line:
            story.append(line)

    def annotated(value, *, refs=(), url=None, details=(), style="table"):
        markup = _linked(text(value), url) if url else para(value, style).text
        for identifier in dict.fromkeys(refs or []):
            if identifier in evidence and f"[{identifier}]" not in str(value):
                markup += citation(identifier)
        for detail in details:
            if detail:
                markup += "<br/>" + text(detail)
        return Paragraph(markup, styles[style])

    def source_stamp(value):
        return str(value) if len(str(value or "")) == 10 else moment(value, compact=True) if value else ""

    def source_cell(row):
        markup = _linked(text(row.get("source") or "来源待核实"), row.get("source_url"))
        if row.get("flow_source") and (row["flow_source"], row.get("flow_source_url")) != (row.get("source"), row.get("source_url")):
            markup += "<br/>" + _linked(text(f"资金流 · {row['flow_source']}"), row.get("flow_source_url"))
        for note in (source_stamp(row.get("as_of")), row.get("basis"), row.get("note"), row.get("flow_note")):
            if note:
                markup += "<br/>" + text(note)
        return Paragraph(markup, styles["small"])

    def amount(value, unit, *, signed=False, delta=None):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return "暂无"
        factor = 100_000_000 if unit in {"CNY", "USD", "HKD", "KRW", "股", "手"} and abs(value) >= 100_000_000 else 1
        display = ("+" if signed and value > 0 else "") + _number(value / factor)
        display += f" {'亿' if factor > 1 else ''}{unit}" if unit else ""
        if isinstance(delta, (int, float)) and not isinstance(delta, bool) and math.isfinite(delta):
            display += f"\n较前日 {_number(delta, percent=True)}"
        if signed and value:
            return Paragraph(f'<font color="{_UP if value > 0 else _DOWN}">{text(display)}</font>', styles["table"])
        return display

    def add_market_detail(story, detail):
        activity = detail.get("activity") or []
        story.append(para("成交量与主力资金流向", "heading"))
        if activity:
            story.append(table(["市场 / 交易日", "成交量", "成交额", "主力净流入", "来源与覆盖"], [
                [f"{row.get('name') or MARKET_NAMES.get(row.get('market'), row.get('market'))}\n{row.get('session_date') or ''}",
                 amount(row.get("volume"), row.get("volume_unit"), delta=row.get("volume_change_pct")),
                 amount(row.get("turnover"), row.get("currency"), delta=row.get("turnover_change_pct")),
                 amount(row.get("main_net_flow"), row.get("currency"), signed=True), source_cell(row)] for row in activity
            ], [.19, .16, .16, .16, .33], compact=True))
        else:
            story.append(para("本期暂无可核验的成交与资金流数据。"))
        for row in detail.get("analysis") or []:
            story.append(annotated(row.get("text"), refs=row.get("refs"), details=(row.get("source"),), style="body"))

        story.append(para("板块涨跌排行", "heading"))
        sectors = detail.get("sectors") or []
        if sectors:
            story.append(table(["市场 / 交易日", "板块", "排名", "涨跌幅", "来源与范围"], [
                [f"{MARKET_NAMES.get(row.get('market'), row.get('market'))}\n{row.get('session_date') or ''}",
                 row.get("name"), row.get("rank"), change(row.get("change_pct"), style="table"), source_cell(row)]
                for row in sectors
            ], [.17, .23, .09, .15, .36], compact=True))
        else:
            story.append(para("本期暂无可核验的板块排名。"))

        story.append(para("今日龙头股", "heading"))
        leaders = detail.get("leaders") or []
        if leaders:
            story.append(table(["市场 / 交易日", "标的", "板块", "涨跌幅", "成交量", "来源与范围"], [
                [f"{MARKET_NAMES.get(row.get('market'), row.get('market'))}\n{row.get('session_date') or ''}",
                 f"{row.get('name') or row.get('symbol')}\n{row.get('symbol') or ''}", row.get("sector"),
                 change(row.get("change_pct"), style="table"), amount(row.get("volume"), row.get("volume_unit") or "股"),
                 source_cell(row)] for row in leaders
            ], [.17, .18, .14, .11, .13, .27], compact=True))
        else:
            story.append(para("本期暂无可核验的龙头股样本。"))
        for note in detail.get("limitations") or []:
            story.append(para(note, "small"))

    def add_narrative_review(story, review):
        story.append(para("前一日推理验证与一周回看", "heading"))
        if review.get("note"):
            story.append(para(review["note"], "small"))
        for item in review.get("items") or []:
            origin_kind = {"morning": "早报", "close": "晚报"}.get(item.get("origin_kind"), "报告")
            story.append(annotated(f"{item.get('origin_date') or '日期待核实'} {origin_kind} · {item.get('event') or '此前叙事'}",
                                   url=item.get("event_url"), style="item"))
            target = " ".join(dict.fromkeys(filter(None, (item.get("name"), item.get("target")))))
            profile = [item.get("sector"), target, "旧版未标方向" if item.get("signal") == "unrecorded" else SIGNALS.get(item.get("signal"))]
            if item.get("validation"):
                profile.append(f"验证条件 {item['validation']}")
            if any(profile):
                story.append(para(" · ".join(filter(None, profile)), "small"))
            if item.get("impact"):
                story.append(para(item["impact"]))
            baseline = item.get("baseline") or {}
            if baseline:
                story.append(annotated(f"冻结基准 {baseline.get('session_date') or ''} 收盘 {_number(baseline.get('price'))}",
                                       url=baseline.get("source_url"), details=(baseline.get("source"),), style="small"))
            checkpoints = []
            for key, title in CHECKPOINTS:
                row = item.get(key) or {}
                details = [row.get("reason"), " · ".join(filter(None, (row.get("source"), source_stamp(row.get("as_of")))))]
                reading = change(row.get("change_pct"), style="table")
                judgement = annotated(CHECKPOINT_LABELS.get(row.get("status"), "待核实"), url=row.get("source_url"), details=details)
                relative = row.get("relative_change_pct")
                if isinstance(relative, (int, float)) and not isinstance(relative, bool) and math.isfinite(relative):
                    extra = text(f"相对指数\n{relative:+.2f} 个百分点")
                    reading = Paragraph(f'{reading.text}<br/><font size="8.25">{extra}</font>', styles["table"])
                    comparison = f"{row.get('benchmark_name') or '市场基准'} {_number(row.get('benchmark_change_pct'), percent=True)}"
                    if row.get("benchmark_source"):
                        comparison += f" · {row['benchmark_source']}"
                    anchor = _linked(text(comparison), row.get("benchmark_source_url"))
                    judgement = Paragraph(f"{judgement.text}<br/>{anchor}", styles["table"])
                checkpoints.append([f"{title}\n{row.get('session_date') or ''}", reading, judgement])
            story.append(table(["验证阶段 / 交易日", "相对冻结基准涨跌", "判断与来源"], checkpoints, [.22, .22, .56], compact=True))
            for row in item.get("followup") or []:
                meta = " · ".join(filter(None, (row.get("source"), source_stamp(row.get("as_of")))))
                story.append(annotated(f"后续披露与报道 · {row.get('title') or '公开资料'}", url=row.get("url"),
                                       details=(meta,), style="small"))
            pricing = item.get("pricing") or {}
            if pricing.get("reason"):
                story.append(para(f"定价判断 · {pricing['reason']}", "small"))

    def add_sections(story, sections):
        for section in sections:
            story.append(para(section["title"], "heading"))
            if section.get("summary"):
                story.append(para(section["summary"]))
            for row in section.get("rows") or []:
                story.append(annotated(row.get("title"), refs=row.get("refs"), url=row.get("url"), style="item"))
                if row.get("detail"):
                    story.append(para(row["detail"]))
                meta = " · ".join(filter(None, (row.get("source"), source_stamp(row.get("as_of")))))
                if meta:
                    story.append(para(meta, "small"))

    def add_narrative(story):
        mode = "有限证据归纳" if narrative.get("mode") == "evidence_rules" else "叙事研判 · 证据有限" if narrative.get("status") == "limited" else "叙事研判"
        story.extend([para(narrative.get("headline") or "当前市场主线", "heading"),
                      para(f"{mode}。以下原因与传导为推断，需结合证据与后续表现验证。", "small"),
                      para(narrative.get("overview"))])
        if narrative.get("hypotheses"):
            story.append(para("叙事逻辑与验证条件", "heading"))
            story.append(table(["事件", "涉及板块", "信号", "影响说明", "观察标的 / 验证条件"], [
                [annotated(row.get("event"), refs=row.get("refs")), row.get("sector"),
                 {"bullish": "利好", "bearish": "利空", "neutral": "中性"}.get(row.get("signal"), "待核实"),
                 row.get("impact"), "\n".join(filter(None, (row.get("target"), row.get("validation"))))]
                for row in narrative["hypotheses"]
            ], [.20, .13, .09, .31, .27], compact=True))
        for index, row in enumerate(narrative.get("reasoning") or [], 1):
            story.append(para(f"{index}. {row.get('hypothesis') or '驱动假说'}", "item"))
            if row.get("phenomenon"):
                story.append(para(f"观察：{row['phenomenon']}"))
            if row.get("mechanism"):
                story.append(para(f"传导逻辑：{row['mechanism']}"))
            add_refs(story, row.get("supporting_refs"))
            if row.get("alternative"):
                story.append(para(f"另一种解释：{row['alternative']}"))
            add_refs(story, row.get("contrary_refs"), "反向证据")
            if row.get("validation"):
                story.append(para(f"如何验证：{row['validation']}"))
        if narrative.get("market_views"):
            story.append(para("四地市场：各自在交易什么", "heading"))
            for row in narrative["market_views"]:
                story.extend([para({"CN": "A股", "US": "美股", "HK": "港股", "KR": "韩股"}.get(row.get("market"), row.get("market")), "item"),
                              para(row.get("summary")), para(f"原因推演：{row.get('drivers', '')}"),
                              para(f"验证：{row.get('watch', '')}")])
                add_refs(story, row.get("refs"))
        if narrative.get("portfolio"):
            story.append(para("对持仓与白名单的影响", "heading"))
            for row in narrative["portfolio"]:
                story.append(para(row.get("target"), "item"))
                story.append(para(row.get("implication")))
                if row.get("watch"):
                    story.append(para(f"重点观察：{row['watch']}"))
                add_refs(story, row.get("refs"))
        if narrative.get("watchpoints"):
            story.append(para("今天验证什么" if report.get("kind") == "morning" else "下一交易日观察什么", "heading"))
            for row in narrative["watchpoints"]:
                story.append(para(row.get("condition"), "item"))
                if row.get("confirms"):
                    story.append(para(f"支持主线：{row['confirms']}"))
                if row.get("weakens"):
                    story.append(para(f"削弱主线：{row['weakens']}"))
                add_refs(story, row.get("refs"))
        if narrative.get("limitations"):
            story.append(para("本次推理的缺口", "heading"))
            for note in narrative["limitations"]:
                story.append(para(note, "small"))

    def excerpt(value, limit):
        clean = " ".join(str(value or "").split())
        return clean if len(clean) <= limit else clean[:limit] + "…"

    def add_front_page(story, front_page):
        story.append(para("重点事件", "frontHeading"))
        if not front_page.get("events"):
            story.append(para("本期暂无可核实的重点事件。"))
        for row in front_page.get("events") or []:
            title = text(excerpt(row.get("title"), 76))
            if row.get("ref"):
                title += citation(row["ref"], row.get("url"))
            story.append(Paragraph(title, styles["headline"]))
            if row.get("summary"):
                story.append(para(excerpt(row["summary"], 90), "headlineSummary"))
            source = text(row.get("source") or "来源待核实")
            published = moment(row.get("published_at"), compact=True)
            story.append(Paragraph(f"{_linked(source, row.get('url'))}  ·  {text(published)}", styles["source"]))

        story.append(para("主要市场收盘涨跌", "frontHeading"))
        market_rows = []
        for row in front_page.get("markets") or []:
            label = row.get("label") or row.get("market")
            if row.get("ref"):
                label = f"{label}[{row['ref']}]"
            market_rows.append([
                label, row.get("session_date"), _number(row.get("price")),
                change(row.get("change_pct"), style="table"),
                row.get("source") if row.get("status") == "ready" else " · ".join(filter(None, [
                    row.get("source") if row.get("price") is not None else None,
                    STATUSES.get(row.get("status"), "待核实")])),
            ])
        if market_rows:
            story.append(table(["指数", "收盘交易日", "收盘", "涨跌幅", "数据来源 / 状态"],
                               market_rows, [.29, .18, .17, .14, .22], compact=True))
        else:
            story.append(para("本期暂无可核实的指数收盘行情。"))
        if front_page.get("market_analysis"):
            story.append(para("指数走势解读", "sourceHeading"))
            for row in front_page["market_analysis"]:
                story.append(annotated(row.get("text"), refs=row.get("refs"), style="headlineSummary"))

        story.append(para("持仓组合涨跌", "frontHeading"))
        portfolio_rows = []
        for row in front_page.get("portfolios") or []:
            coverage = f"{row.get('covered', 0)}/{row.get('total', 0)}"
            if row.get("status") != "ready":
                coverage += f" · {STATUSES.get(row.get('status'), '待核实')}"
            portfolio_rows.append([
                row.get("name"), change(row.get("change_pct"), style="table"),
                change(row.get("daily_pnl"), percent=False, style="table"), row.get("currency"),
                coverage, moment(row.get("quote_time"), compact=True),
            ])
        if portfolio_rows:
            story.append(table(["组合", "较前收", "盈亏估算", "币种", "行情覆盖", "最早报价"],
                               portfolio_rows, [.18, .13, .19, .10, .21, .19], compact=True))
            story.append(para("组合涨跌按持仓相对前收估算，行情口径见附录。", "source"))
        else:
            story.append(para("本期暂无组合涨跌估算。"))

    identity = f"{'报告日' if report.get('market') == 'GLOBAL' else '交易日'} {report.get('session_date', '暂无')}   |   第{record.get('revision', 1)}版   |   {STATUSES.get(record['status'], record['status'])}"
    story = [para("STock  /  市场工作台", "small"), para(report.get("title"), "title")]
    metadata = [
        para(f"生成：{moment(report.get('generated_at'))}   时区：{zone}", "small"),
        para(f"信息窗口：{moment(report.get('window', {}).get('start'))} 至 {moment(report.get('window', {}).get('end'))}", "small"),
        para(f"报告编号：{record.get('id', '暂无')}。本 PDF 导出已存档内容，不重新采集或回填行情。", "small"),
    ]
    if report.get("late"):
        metadata.append(para("本份为实际生成时点的更新记录，不回填为盘前已知信息。" if report.get("market") == "GLOBAL" else "盘前时段已过：本份为补看记录，不计入盘前观点验证。", "small"))
    if report.get("markets"):
        metadata.append(table(["市场", "生成时所处时段", "所引收盘交易日"],
                              [[r["name"], r["phase"], r.get("quote_session_date") or "未确认"] for r in report["markets"]], [.2, .35, .45], compact=True))
    front_page = report.get("front_page")
    if front_page:
        story.append(para(f"{identity}   |   生成 {moment(report.get('generated_at'), compact=True)}", "small"))
        add_front_page(story, front_page)
        story.append(PageBreak())
    else:
        story.append(para(identity))
        story.extend(metadata)
    market_detail = report.get("market_detail")
    narrative_review = report.get("narrative_review")
    sections = report.get("sections") or []
    if market_detail:
        add_market_detail(story, market_detail)
    if narrative_review:
        add_narrative_review(story, narrative_review)
    if narrative:
        add_narrative(story)
    if sections:
        add_sections(story, sections)
    if narrative and not (market_detail or narrative_review or sections):
        story.append(PageBreak())
    if narrative or front_page or market_detail or narrative_review or sections:
        story.append(para("附录：行情、新闻与原始依据", "heading"))
    if front_page:
        story.extend(metadata)
        portfolio_notes = [para(f"{row.get('name')} · {row['note']}", "source")
                           for row in front_page.get("portfolios") or [] if row.get("note")]
        if portfolio_notes:
            story.append(para("组合行情口径", "sourceHeading"))
            story.extend(portfolio_notes)
    if report.get("market") == "GLOBAL":
        story.append(para("行情核对", "heading"))
        story.append(table(["市场 / 标的", "交易日", "收盘 / 币种", "涨跌幅"], [
            [f"{r.get('market', '')} / {r.get('name') or r.get('symbol', '')}", r.get("session_date"),
             f"{_number(r.get('close'))} {'点' if r.get('role') == 'index' else r.get('currency') or ''}", change(r.get("change_pct"))]
            for r in report.get("quotes") or []], [.32, .23, .25, .2]))
        for row in report.get("quotes") or []:
            if row.get("status") != "ready":
                story.append(para(f"{row.get('name') or row.get('symbol')}: {row.get('reason') or '有效收盘行情缺失'}", "small"))
        story.append(para("以下列出正文引用的资料；全部采集记录保存在工作台的本期原文中。", "small"))
        # Keep the reading PDF focused on its analysis; the frozen app report
        # retains the full collection for inspection without a 200-item printout.
        used = set()
        for key in ("reasoning", "portfolio", "watchpoints", "market_views", "hypotheses"):
            for row in (narrative or {}).get(key) or []:
                for field in ("supporting_refs", "contrary_refs", "refs"):
                    used.update(row.get(field) or [])
        used.update(_REFERENCE.findall(str(narrative or {})))
        used.update(_REFERENCE.findall(str(sections)))
        for section in sections:
            for row in section.get("rows") or []:
                used.update(row.get("refs") or [])
        for row in (front_page or {}).get("market_analysis") or []:
            used.update(row.get("refs") or [])
        for row in (market_detail or {}).get("analysis") or []:
            used.update(row.get("refs") or [])
        for key in ("events", "markets"):
            used.update(row.get("ref") for row in (front_page or {}).get(key) or [])
        evidence = {key: row for key, row in evidence.items() if key in used}
    else:
        story.extend([para("摘要", "heading"), para(report.get("summary"))])
        coverage = report.get("coverage") or {}
        story.append(para(f"覆盖标的 {coverage.get('target_total', '暂无')}；有效行情 {coverage.get('quote_available', '暂无')} / {coverage.get('quote_total', '暂无')}；窗口新闻 {coverage.get('news_total', '暂无')}。", "small"))

        story.append(para("标的分析与关注", "heading"))
        focus = report.get("focus") or []
        if focus:
            story.append(table(["标的 / 市场", "定位", "消息方向", "分析依据"], [
                [f"{row.get('name', '')}\n{row.get('symbol', '')} / {row.get('market', '')}", ROLES.get(row.get("role"), "暂无"),
                 SIGNALS.get(row.get("signal"), "待核实"), f"{row.get('reason') or '暂无'}\n相关新闻 {row.get('news_count', '暂无')} 条"] for row in focus
            ], [.23, .13, .15, .49]))
        else:
            story.append(para("暂无标的分析。"))

        story.append(para("已完成交易日行情", "heading"))
        quotes = report.get("quotes") or []
        for row in quotes:
            story.append(para(f"{row.get('name', '')}  {row.get('symbol', '')} / {row.get('market', '')}  ·  {ROLES.get(row.get('role'), '标的')}", "item"))
            story.append(table(["交易日", "收盘", "前收盘", "涨跌幅"], [[row.get("session_date"), _number(row.get("close")), _number(row.get("previous_close")), change(row.get("change_pct"))]], [.25, .25, .25, .25]))
            story.append(para(f"{STATUSES.get(row.get('status'), row.get('status', '暂无'))}；币种 {row.get('currency') or '暂无'}；数据时点 {moment(row.get('as_of'))}；源行情时点 {moment(row.get('source_quote_at'))}", "small"))
            if row.get("reason"):
                story.append(para(f"数据说明：{row['reason']}", "small"))
            if row.get("in_whitelist"):
                story.append(para("该持仓同时在白名单中。", "small"))
            story.append(link(row.get("source"), row.get("source_url")))
        if not quotes:
            story.append(para("暂无已核验行情，不以零值替代。"))

        for index, row in enumerate(report.get("news") or [], 1):
            story.append(KeepTogether(([para("新闻与分析证据", "heading")] if index == 1 else []) + [
                para(f"{index}. {row.get('title') or '未提供标题'}", "item"),
                para(f"{row.get('market', '暂无')} / {row.get('symbol') or '市场背景'}；{'外盘背景' if row.get('background') else '本市场'}；{'涉及关注标的' if row.get('related') else '背景参考'}；{SIGNALS.get(row.get('signal'), '待核实')}；评分 {_number(row.get('score'))}；事件 {row.get('event_type') or '暂无'}", "small"),
                para(row.get("analysis") or "尚无可核验的分析。"),
                para(f"发布 {moment(row.get('published_at'))}；收录 {moment(row.get('discovered_at'))}；分析 {moment(row.get('analyzed_at'))}", "small"),
                link(row.get("source"), row.get("url")),
            ]))
        if not report.get("news"):
            story.append(para("新闻与分析证据", "heading"))
            story.append(para("窗口内暂无可核验新闻，不等于没有市场事件。"))

        story.append(para("异动记录", "heading"))
        for row in report.get("events") or []:
            story.append(para(f"{row.get('name', '')} {row.get('symbol', '')} / {row.get('market', '')}  {moment(row.get('detected_at'))}", "item"))
            story.append(change(row.get("change_pct")))
            story.append(para(row.get("reason_summary") or "暂无可核验原因。"))
            story.append(para(f"证据发布：{moment(row.get('evidence_published_at'))}", "small"))
            story.append(link(row.get("evidence_source"), row.get("evidence_url")))
        if not report.get("events"):
            story.append(para("窗口内暂无已记录异动。"))

        story.append(para("财报日程", "heading"))
        for row in report.get("earnings") or []:
            story.append(para(f"{row.get('name', '')} {row.get('symbol', '')}  ·  {row.get('earnings_date') or '日期待核实'}", "item"))
            story.append(para(f"时段 {row.get('session') or '暂无'}；日期状态 {row.get('date_status') or '暂无'}；来源日期{'有冲突' if row.get('date_conflict') else '未标示冲突'}", "small"))
            if row.get("source") or row.get("source_url"):
                story.append(link(row.get("source"), row.get("source_url")))
            for source in row.get("sources") or []:
                story.append(link(f"{source.get('name') or '来源'}（{source.get('date') or '日期暂无'}）", source.get("url")))
        if not report.get("earnings"):
            story.append(para("暂无已接入的相关财报日程；不表示没有财报事件。"))

        review = report.get("review") or {}
        story.append(para("上一交易日观点核对" if report.get("kind") == "morning" else "当日盘前观点核对", "heading"))
        story.append(para(f"核对交易日 {review.get('session_date') or '暂无'}；{STATUSES.get(review.get('status'), '不可用')}；已核对 {review.get('checked', 0)}；方向一致 {review.get('matched', 0)}。", "small"))
        story.append(para(review.get("note") or "无有效冻结早报可核对。"))
        if review.get("items"):
            story.append(table(["标的", "盘前方向", "收盘涨跌幅", "核对结果"], [
                [f"{row.get('name', '')}\n{row.get('symbol', '')}", SIGNALS.get(row.get("signal"), "待核实"), change(row.get("change_pct")),
                 "方向一致" if row.get("matched") is True else "方向不一致" if row.get("matched") is False else "未核对"] for row in review["items"]
            ], [.31, .21, .23, .25]))

    if evidence:
        story.append(para("叙事引用索引", "sourceHeading"))
        for identifier, row in evidence.items():
            description = f"[{identifier}] {row.get('label') or row.get('symbol') or '来源'}"
            scope = " / ".join(str(row[key]) for key in ("market", "symbol", "kind") if row.get(key))
            if scope:
                description += f" · {scope}"
            description += f" · {moment(row.get('as_of'))}"
            story.append(Paragraph(_linked(text(description), row.get("url")), styles["source"]))
    story.append(para("数据来源与完整性", "sourceHeading"))
    for row in report.get("sources") or []:
        description = f"{row.get('name') or '来源'} · {STATUSES.get(row.get('status'), row.get('status', '暂无'))} · {moment(row.get('as_of'))}"
        story.append(para(f"{description}\n{row.get('note') or '暂无说明。'}", "source"))
    story.append(para("缺失数据与使用边界", "heading"))
    for index, note in enumerate(report.get("limitations") or [], 1):
        story.append(para(f"{index}. {note}"))
    if unsupported:
        story.append(para("当前本机字体不能显示的少数字符以 [U+十六进制编号] 保留。", "small"))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont(face, 8)
        canvas.setFillColor(_MUTED)
        canvas.drawString(18 * mm, 12 * mm, f"STock  |  {report.get('session_date', '')}  |  {report.get('market', '')}")
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"{doc.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(destination), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                                 topMargin=16 * mm, bottomMargin=20 * mm,
                                 title=str(report.get("title") or "STock 早晚报"), author="STock")
    document.build(story, onFirstPage=page, onLaterPages=page)
