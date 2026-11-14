"""A frozen portfolio review workbook, rebuilt from the user's column layout."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
import math
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .portfolio_monitor import build_portfolio_analysis
from .portfolio_records import estimate_quota
from .portfolio_workspace import holding_metadata, position_workspace
from .stock_profile import watch_quote
from .security_master import cached_identity, display_name


BEIJING = timezone(timedelta(hours=8))
MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HOLDING_HEADERS = ["持仓编号", "组合名称", "账户名称", "证券代码", "标的名称", "持仓状态", "个股策略", "个股评级",
    "持股数量", "现价", "止盈价格", "止损价格", "持仓成本", "持仓市值", "仓位占比", "仓位限制", "浮动盈亏", "浮动盈亏比",
    "建仓日期", "持仓天数", "清仓后总收益率", "行业", "资产类型", "标的概况", "配置理由", "主要风险"]
WHITELIST_HEADERS = ["标的名称", "证券代码", "股票池状态", "个股评级", "行业", "二级行业/分类", "资产分类", "概念题材",
    "现价", "目标价", "分析&行动", "标的用法", "调研追踪", "例会讨论", "投委会讨论", "标的概况", "添加日期", "PE 分析", "当前区间", "当前 PE"]
TRADE_HEADERS = ["交易编号", "组合名称", "账户名称", "交易日期", "交易时间", "证券代码", "标的名称", "交易类型",
    "交易价格", "交易数量", "交易资金", "税费", "资金变动", "实现盈亏", "建仓日期", "清仓日期", "持仓天数", "当期总收益率", "交易数量计算", "成本核算（中间值）"]
QUOTE_HEADERS = ["市场", "原币", "报价时间（北京时间）", "报价来源", "报价状态", "交易时段", "报价说明", "来源链接"]
ASSET_LABELS = {"stock": "股票", "fund": "基金", "etf": "ETF", "etn": "ETN", "leveraged_etf": "杠杆 ETF", "inverse_etf": "反向 ETF", "other": "其他"}
STATUS_LABELS = {"live": "最新报价", "ready": "已采集", "closed": "休市报价", "delayed": "延迟报价", "stale": "历史缓存/待确认", "waiting": "等待报价", "error": "报价失败", "partial": "部分可用", "empty": "无持仓", "reference": "模板/手工参考价（非实时）", "missing": "缺失", "nav": "净值", "manual": "手动", "unavailable": "不可用"}
SESSION_LABELS = {"pre": "盘前", "regular": "常规交易", "post": "盘后", "closed": "休市"}
NUMBER = '#,##0.00;[Green]-#,##0.00;0.00'
PRICE = '#,##0.00####'
PERCENT = '0.00%;[Green]-0.00%;0.00%'
SIGNED = {"浮动盈亏", "浮动盈亏比", "当日盈亏估算（计价币）", "浮动盈亏（计价币）", "实现盈亏", "实现盈亏（计价币）", "已实现净盈亏（计价币）", "已知部分净盈亏（计价币）", "资金变动", "涨跌幅"}
PERCENTS = {"仓位占比", "仓位限制", "浮动盈亏比", "清仓后总收益率", "当期总收益率", "涨跌幅"}
PRICES = {"现价", "止盈价格", "止损价格", "持仓成本", "目标价", "交易价格", "平均成本单价（原币）"}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _percent(value):
    value = _number(value)
    return value / 100 if value is not None else None


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _time(value):
    if not value:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None  # A timezone-less source time cannot be labelled Beijing time.
        return stamp.astimezone(BEIJING).replace(tzinfo=None)
    except ValueError:
        return None


def _source_date(value):
    return _date(value) if len(str(value or "")) == 10 else _time(value)


def _text_cell(cell, value):
    # Keep user notes and names literal, including leading '=' and Excel error strings.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    cell.value = text
    cell.data_type = "s"


def _set_cell(cell, value, header=""):
    if isinstance(value, str):
        _text_cell(cell, value)
    else:
        cell.value = value
    color = "245A8C"
    if header in SIGNED and _number(value) is not None:
        color = "C62828" if value > 0 else "18804B" if value < 0 else color
    cell.font = Font(name="楷体", size=14, color=color)
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    if isinstance(value, datetime):
        cell.number_format = "yyyy-mm-dd hh:mm:ss"
    elif isinstance(value, date):
        cell.number_format = "yyyy-mm-dd"
    elif isinstance(value, time):
        cell.number_format = "hh:mm:ss"
    elif _number(value) is not None:
        cell.number_format = "0.00000000" if header.startswith("参考汇率：") else PERCENT if header in PERCENTS else PRICE if header in PRICES or header in {"持股数量", "交易数量"} else NUMBER


def _sheet(workbook, title, headers, rows, *, freeze, table_name, widths, height, notes, metadata):
    sheet = workbook.create_sheet(title)
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = freeze
    sheet.row_dimensions[1].height = 58
    for index, header in enumerate(headers, 1):
        cell = sheet.cell(1, index)
        _text_cell(cell, header)
        gold = title == "组合持仓" and index <= 3 or title == "交易记录" and index in {1, 2, 3, 5}
        blue = title == "组合持仓" and index in {4, 7} or title == "标的白名单" and index not in {16, 17, 18} or title == "交易记录" and index in {4, 8, 9, 10, 14}
        cell.fill = PatternFill("solid", fgColor="F4D8A5" if gold else "203956" if blue else "63758A")
        cell.font = Font(name="楷体", size=14, bold=True, color="61451D" if gold else "FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(index, 23)
    for row_number, row in enumerate(rows, 2):
        sheet.row_dimensions[row_number].height = height
        for index, header in enumerate(headers, 1):
            cell = sheet.cell(row_number, index)
            _set_cell(cell, row.get(header), header)
            cell.fill = PatternFill("solid", fgColor="FFFFFF" if row_number % 2 == 0 else "F7FAFD")
    last = len(rows) + 1
    table = Table(displayName=table_name, ref=f"A1:{get_column_letter(len(headers))}{last}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False,
                                        showLastColumn=False, showRowStripes=True, showColumnStripes=False)
    sheet.add_table(table)
    sheet.auto_filter.ref = table.ref
    start = last + 3
    for offset, (label, value) in enumerate([*metadata, *(("字段：" + key, text) for key, text in notes.items())]):
        row = start + offset
        _set_cell(sheet.cell(row, 1), label)
        _set_cell(sheet.cell(row, 4), value, label)
        sheet.cell(row, 1).font = Font(name="楷体", size=14, bold=True, color="245A8C")
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        sheet.merge_cells(start_row=row, start_column=4, end_row=row, end_column=min(10, len(headers)))
        sheet.row_dimensions[row].height = max(30, 22 * math.ceil(len(value) / 70)) if isinstance(value, str) else 30
    sheet.print_options.horizontalCentered = False
    sheet.print_title_rows = "1:1"
    return sheet


def _quote_fields(row):
    status = row.get("quote_status") or "waiting"
    return {"市场": row.get("market"), "原币": row.get("currency"), "报价时间（北京时间）": _time(row.get("quote_time")),
            "报价来源": row.get("quote_source"), "报价状态": STATUS_LABELS.get(status, status),
            "交易时段": SESSION_LABELS.get(row.get("quote_session") or row.get("market_session"), row.get("quote_session") or row.get("market_session")),
            "报价说明": row.get("quote_error") or row.get("source_note"), "来源链接": row.get("source_url")}


def _valuation(row):
    plan = row.get("plan") or {}
    low, high = _number(plan.get("valuation_low")), _number(plan.get("valuation_high"))
    if low is None or high is None:
        return None
    band = f"{low:g}–{high:g} {plan.get('currency') or ''}".strip()
    if plan.get("currency") != row.get("currency"):
        return band + "；估值币种待核对"
    price = _number(row.get("price"))
    if price is None or row.get("quote_status") in {"stale", "error", "waiting"}:
        return band + "；现价待确认"
    return band + "；" + ("低于估值区间" if price < low else "高于估值区间" if price > high else "处于估值区间")


def build_workbook(account, snapshot, whitelist, *, portfolio_id, captured_at, peers=(), cards=()):
    """Consume detached inputs only. No fetching, refreshing, file or account writes."""
    portfolio = build_portfolio_analysis(account.get("positions", []), snapshot, [], [],
        now=captured_at, principal_cny=account.get("principal_cny"), account=account)
    name = account.get("name") or "默认组合"
    captured = _time(captured_at)
    base = portfolio["base_currency"]
    summary = portfolio["summary"]
    trades = portfolio["trades"]
    common = [("组合名称", name), ("组合编号", portfolio_id), ("导出时间（北京时间 UTC+08:00）", captured),
        ("行情采集完成时间（北京时间）", _time(snapshot.get("updated_at"))), ("计价币种", base),
        ("报价口径", "冻结导出时软件已有报价；各标的报价时间独立。现价可能为延迟、盘前盘后、休市或缓存价格，不等同于当日正式收盘价。"),
        ("缓存状态", "上次启动缓存，等待重新确认" if snapshot.get("restored_from_cache") else STATUS_LABELS.get(snapshot.get("status"), snapshot.get("status") or "等待报价")),
        ("缺失字段", "空白表示未录入、不适用或来源不可用；不以零代替。未定义或未核实的收益率不以其他指标替代。"),
        ("数字口径", "价格与持仓成本为原币单价；持仓市值和浮动盈亏为原币金额。附列另列计价币金额。仓位占比=该标的计价币市值/组合持仓市值，不含现金。"),
        ("汇率口径", "外币金额按本次快照参考汇率折算；已实现原币成交值不变，外币汇总不含汇兑损益。过期/缺失报价或汇率会标记不可用。")]
    for currency, rate in sorted(portfolio["fx_rates"].items()):
        common.append((f"参考汇率：1 {currency} 折 CNY", _number(rate)))
    for pair, details in sorted(portfolio["fx_details"].items()):
        common.append((f"汇率 {pair} 来源/时间/状态", " / ".join(str(v) for v in (
            details.get("source"), details.get("quote_time"), details.get("status"), details.get("error")) if v)))

    holding_rows = []
    positions = {}
    for row in portfolio["positions"]:
        target = f"{row['market']}:{row['symbol']}"
        positions[target] = row
        holding = holding_metadata(row, portfolio_id, captured_at)
        workspace = position_workspace(row, peers, cards)
        row["workspace"] = workspace
        plan, risk = row.get("plan") or {}, row.get("risk_settings") or {}
        card = next(iter(workspace["cards"]), {})
        cost, profit, stop = _number(row.get("average_cost")), _number(risk.get("take_profit_pct")), _number(risk.get("stop_loss_pct"))
        opened = _date(plan.get("opened_date"))
        holding_rows.append(dict(zip(HOLDING_HEADERS, [
            holding["holding_id"], name, plan.get("account_name"), row["symbol"], display_name(row['market'], row['symbol'], row.get('name')), holding["holding_status"], plan.get("strategy") or card.get("strategic_fit"), plan.get("rating"),
            row.get("quantity"), row.get("price"), cost * (1 + profit / 100) if cost is not None and profit is not None else _number(risk.get("take_profit_price")),
            cost * (1 - stop / 100) if cost is not None and stop is not None else _number(risk.get("stop_price")),
            cost, row.get("market_value"), _percent(row.get("weight_pct")),
            _percent(risk.get("weight_max_pct") if risk.get("weight_max_pct") is not None else portfolio["account"]["position_limit"]),
            row.get("pnl"), _percent(row.get("pnl_pct")), opened, holding["holding_days"],
            None, workspace.get("sector"), plan.get("asset_class") or ASSET_LABELS.get(row.get("asset_type"), row.get("asset_type")), plan.get("business") or card.get("business"),
            plan.get("buy_reason"), plan.get("risks") or card.get("risks")]), **{
            **_quote_fields(row), "计价币种": base, "持仓市值（计价币）": row.get("market_value_base"),
            "持仓总成本（原币）": row.get("cost_value"), "持仓总成本（计价币）": row.get("cost_value_base"),
            "浮动盈亏（计价币）": row.get("pnl_base"), "当日盈亏估算（计价币）": row.get("daily_pnl_base"),
            "估值可用性": "可用" if row.get("valuation_current") else "不可确认/数据不完整",
            "涨跌幅": _percent(row.get("change_pct")), "买入分析": plan.get("entry_analysis"), "标的用法": plan.get("usage"),
            "当前区间": _valuation(row), "估值依据": plan.get("valuation_basis"),
            "当前 PE": _number((workspace.get("pe") or {}).get("value")),
            "PE 资料时间": _source_date((workspace.get("pe") or {}).get("as_of")),
            "PE 来源": workspace.get("pe_source_url") or workspace.get("pe_source"),
            "导出时间（北京时间）": captured}))

    pending_targets = set()
    for item in account.get("pending_positions", []):
        target = f"{item['market']}:{item['symbol']}"
        pending_targets.add(target)
        source = item.get("whitelist_source") or {}
        profile = source.get("profile") or {}
        quote = watch_quote(snapshot, item, now=captured_at)
        if quote["price"] is None and profile.get("reference_price") is not None:
            quote.update(price=profile["reference_price"], quote_status="reference", quote_time=None, change_pct=None,
                         quote_source="手工/模板参考价", quote_error="非实时行情；资料日期：" + (profile.get("price_as_of") or "未提供"), source_url=None)
        holding_rows.append({
            "持仓编号": f"{portfolio_id}:{target}", "组合名称": name, "证券代码": item["symbol"],
            "标的名称": display_name(item['market'], item['symbol'], item.get('name')), "持仓状态": "待建仓", "个股评级": profile.get("rating"),
            "现价": quote["price"], "行业": profile.get("sector"), "资产类型": profile.get("asset_class"),
            "标的概况": profile.get("business"), "配置理由": source.get("reason"),
            **_quote_fields({**item, **quote}), "计价币种": base, "估值可用性": "待建仓",
            "涨跌幅": _percent(quote.get("change_pct")), "买入分析": source.get("analysis"),
            "标的用法": profile.get("usage"), "当前区间": profile.get("valuation_range"),
            "当前 PE": _number(profile.get("current_pe")), "PE 资料时间": _date(profile.get("pe_as_of")),
            "PE 来源": "手工/模板资料" if profile.get("current_pe") is not None else None,
            "导出时间（北京时间）": captured,
        })

    for row in trades["records"]:
        if row.get("trade_type") != "清仓":
            continue
        target = f"{row['market']}:{row['symbol']}"
        holding_rows.append({
            "持仓编号": f"{portfolio_id}:{target}:closed:{row['id']}", "组合名称": name,
            "账户名称": row.get("account_name"), "证券代码": row["symbol"], "标的名称": display_name(row['market'], row['symbol'], row.get('name')),
            "持仓状态": "已清仓", "建仓日期": _date(row.get("opened_date")), "持仓天数": row.get("holding_days"),
            "清仓后总收益率": _percent(row.get("closed_return_pct")), "配置理由": row.get("buy_reason"),
            "市场": row["market"], "原币": row["currency"], "计价币种": base, "估值可用性": "历史清仓",
            "买入分析": row.get("entry_analysis"), "清仓日期": _date(row.get("closed_date")),
            "清仓收益率状态": f"待补齐 · {row['closed_return_status']}" if row.get("closed_return_status") else "整轮成交已核对",
            "导出时间（北京时间）": captured,
        })

    white_rows = []
    for item in whitelist:
        target = f"{item['market']}:{item['symbol']}"
        row = positions.get(target)
        if row is None:
            identity = cached_identity(item["market"], item["symbol"]) or {}
            row = {**identity, **item, **watch_quote(snapshot, item, now=captured_at)}
        workspace = row.get("workspace") or position_workspace(row, peers, cards)
        plan, pe = row.get("plan") or {}, workspace.get("pe") or {}
        card = next(iter(workspace["cards"]), {})
        profile = item.get("profile") or {}
        row = dict(row)
        if row.get("price") is None and profile.get("reference_price") is not None:
            row.update(price=profile["reference_price"], quote_status="reference", quote_time=None,
                       quote_source="手工/模板参考价", quote_error="非实时行情；资料日期：" + (profile.get("price_as_of") or "未提供"), source_url=None)
        manual_pe = profile.get("current_pe")
        white_rows.append(dict(zip(WHITELIST_HEADERS, [
            display_name(item['market'], item['symbol'], item.get('name')), item["symbol"], profile.get("pool_status") or "白名单",
            profile.get("rating"), profile.get("sector") or workspace.get("sector"), profile.get("industry"),
            profile.get("asset_class") or ASSET_LABELS.get(row.get("asset_type"), row.get("asset_type")), profile.get("themes"),
            row.get("price"), profile.get("target_price"), item.get("analysis"), profile.get("usage") or plan.get("usage"),
            profile.get("research_tracking") or card.get("review"), profile.get("meeting_notes"), profile.get("committee_notes"),
            profile.get("business") or card.get("business"), _date(profile.get("added_date", item.get("created_at"))),
            profile.get("pe_analysis") or ("不适用" if pe.get("not_applicable") else pe.get("basis")),
            profile.get("valuation_range") or _valuation(row), manual_pe if manual_pe is not None else _number(pe.get("value"))]), **{
            **_quote_fields(row), "加入理由": item.get("reason"),
            "PE 资料时间": _date(profile.get("pe_as_of")) if manual_pe is not None else _source_date(pe.get("as_of")),
            "PE 来源": "手工/模板资料" if manual_pe is not None else workspace.get("pe_source_url") or workspace.get("pe_source"),
            "当前组合关联": "已持仓" if target in positions else "待建仓" if target in pending_targets else "未持仓", "导出时间（北京时间）": captured,
            "原参考价": profile.get("reference_price"), "参考价日期": _date(profile.get("price_as_of")),
            "系统登记时间": _time(item.get("created_at"))}))

    trade_rows = []
    for row in trades["records"]:
        fee, gross = _number(row.get("fee")), _number(row.get("gross_amount"))
        action = row.get("action")
        net = _number(row.get("net_pnl")) if action == "sell" and row.get("fee_complete") else None
        when = row.get("trade_date")
        trade_rows.append(dict(zip(TRADE_HEADERS, [
            row.get("id"), name, row.get('account_name'), _date(when), time.fromisoformat(row['trade_time']) if row.get('trade_time') else _time(when) if len(str(when or "")) > 10 else None,
            row.get("symbol"), display_name(row['market'], row['symbol'], row.get('name')), row.get("trade_type"),
            row.get("execution_price"), row.get("quantity"), gross, fee,
            row.get("cash_change"), net, _date(row.get('opened_date')), _date(row.get("closed_date")), row.get("holding_days"),
            _percent(row.get('period_return_pct')), row.get('quantity_calculation'), row.get("basis_amount")]), **{
            "市场": row.get("market"), "原币": row.get("currency"), "计价币种": base,
            "实现盈亏（计价币）": row.get("net_pnl_base") if net is not None else None,
            "费用完整性": "已确认" if row.get("fee_complete") else "待补充费用/成本确认",
            "未计入成本的买入费用（原币）": row.get("extra_buy_fee"), "平均成本单价（原币）": row.get("average_cost"),
            "买入分析": row.get("entry_analysis"), "买入理由": row.get("buy_reason"),
            "卖出分析": row.get("exit_analysis"), "卖出理由": row.get("sell_reason"), "备注": row.get("note"),
            "当期收益率口径": row.get('period_return_basis'),
            "清仓后总收益率": _percent(row.get("closed_return_pct")),
            "清仓收益率状态": f"待补齐 · {row['closed_return_status']}" if row.get("closed_return_status") else "整轮成交已核对" if row.get("trade_type") == "清仓" else None,
            "登记时间（北京时间）": _time(row.get("recorded_at")), "导出时间（北京时间）": captured}))

    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = f"{name} · 组合复盘快照"
    workbook.properties.creator = "STock"
    holding_extra = QUOTE_HEADERS + ["计价币种", "持仓市值（计价币）", "持仓总成本（原币）", "持仓总成本（计价币）", "浮动盈亏（计价币）", "当日盈亏估算（计价币）", "估值可用性", "涨跌幅", "买入分析", "标的用法", "当前区间", "估值依据", "当前 PE", "PE 资料时间", "PE 来源", "导出时间（北京时间）"]
    holding_extra += ["清仓日期", "清仓收益率状态"]
    holding_notes = {"持仓编号": "组合编号:市场:证券代码，作为本快照中的稳定关联键。", "持仓成本": "原币平均成本单价；已计入均价的买入费用不再重复扣除。", "仓位占比": "按计价币持仓市值计算，分母不含现金；缺报价或汇率时留空。", "仓位限制": "个股自设上限，未设置时沿用组合单一持仓上限。", "浮动盈亏": "持仓市值减持仓总成本，不是清仓后扣除全部费用的已实现净盈亏。", "清仓后总收益率": "当前持仓尚未清仓；不以浮动盈亏比代替。", "建仓日期": "仅用手动记录的真实建仓日期，不以软件登记日期代替。"}
    holding_notes["清仓后总收益率"] = "已清仓的整轮原币扣费净盈亏 ÷ 买入总成本；缺少完整成交时留空。历史清仓行不计入当前组合估值。"
    totals = [("组合市值（计价币）", summary["total_market_value_base"]), ("持仓成本总额（计价币）", summary["total_cost_base"]),
        ("浮动盈亏（计价币）", summary["total_pnl_base"]), ("现金（计价币）", summary["cash_base"]), ("应收账款（计价币）", summary.get("receivable_base", 0)), ("应付账款（计价币）", summary.get("payable_base", 0)), ("净资产（计价币）", summary["net_assets_base"]),
        ("估值完整性", "完整" if summary["base_valuation_complete"] else "不完整；可计算项为部分合计"),
        ("估值时效", "可用" if summary["valuation_current"] else "含待确认报价/汇率"),
        ("当日盈亏覆盖", f"{summary['daily_pnl_count']}/{summary['position_count']} 个持仓"),
        ("已实现净盈亏（计价币）", trades["summary"]["realized_net_base"]),
        ("已实现口径", trades["summary"]["history_note"]), ("分析口径", "买入分析、配置理由及公司研究来自已保存资料。本次导出不重新采集新闻、不生成新的评级或交易建议。")]
    totals += [(f"库存现金原币（{row['currency']}）", row["amount"]) for row in summary.get("cash_details", [])]
    _sheet(workbook, "组合持仓", HOLDING_HEADERS + holding_extra, holding_rows, freeze="F2", table_name="PortfolioHoldings",
        widths={**{i: 16.906 for i in range(1, 24)}, 2: 23.27, 3: 26.18, 4: 19.633, 5: 35.363, **{i: 73.727 for i in range(24, 27)}, 43: 52, 46: 52},
        height=87.5, notes=holding_notes, metadata=common + totals)
    _sheet(workbook, "标的白名单", WHITELIST_HEADERS + QUOTE_HEADERS + ["加入理由", "PE 资料时间", "PE 来源", "当前组合关联", "导出时间（北京时间）", "原参考价", "参考价日期", "系统登记时间"], white_rows,
        freeze="B2", table_name="PortfolioWhitelist", widths={**{i:16.906 for i in range(1, 21)}, 1:29.906, 11:52.906, 12:37.27, 13:30, 14:30, 15:30, 18:31.27, 29:52},
        height=110, notes={"分析&行动": "已保存的白名单分析，不是本次自动生成的交易建议。", "目标价": "已录入的独立目标价；不以估值上限或止盈价替代。", "当前 PE": "优先保留手工/模板资料，否则使用已采集的估值资料；来源与日期见附列，未取得时留空。"},
        metadata=common + [("白名单范围", "全局股票池白名单；当前组合关联仅按本次选定组合标记。现价优先采用已采集行情；仅有模板参考价时明确标为非实时，并保留原参考价和资料日期。")])
    _sheet(workbook, "交易记录", TRADE_HEADERS + ["市场", "原币", "计价币种", "实现盈亏（计价币）", "费用完整性", "未计入成本的买入费用（原币）", "平均成本单价（原币）", "买入分析", "买入理由", "卖出分析", "卖出理由", "备注", "登记时间（北京时间）", "导出时间（北京时间）", "当期收益率口径", "清仓后总收益率", "清仓收益率状态"], trade_rows,
        freeze="D2", table_name="PortfolioTrades", widths={**{i:16.906 for i in range(1, 20)}, 20:18, **{i:52 for i in range(28, 33)}}, height=70,
        notes={"实现盈亏": "仅卖出且费用成本确认完整时记录净盈亏；原币成交金额，已扣对应成本、本次费用与未入成本买费。", "税费": "本次已登记交易费用；未计入成本的买入费用见附列。未录入不视为0。", "资金变动": "按成交金额与本次费用估算的现金流，不表示已自动调整或核对账户现金。", "交易时间": "按账单当地时间录入；仅有日期时留空，登记时间不是实际成交时间。", "当期总收益率": "按成交模板资料手动录入，并在附列注明起止日期及口径；未录入时留空。", "成本核算（中间值）": "卖出数量对应的原币成本总额。"},
        metadata=common + [("已实现净盈亏（计价币）", trades["summary"]["realized_net_base"]),
            ("已知部分净盈亏（计价币）", trades["summary"]["known_realized_net_base"]), ("费用合计（计价币）", trades["summary"]["fees_base"]),
            ("待补齐卖出记录数", trades["summary"]["incomplete_count"]), ("汇率折算完整性", "完整" if trades["summary"]["fx_complete"] else "缺失汇率"),
            ("成交口径", trades["summary"]["history_note"] + "资金变动是按成交记录推算的现金流，账户现金余额仍以账户设置为准。")])
    meeting_headers = ["例会编号", "会议名称", "会议类型", "会议日期", "会议纪要", "会议结论", "调研讨论", "个股讨论"]
    meeting_rows = [dict(zip(meeting_headers, [m['id'], m.get('name'), m.get('kind'), _date(m.get('date')),
        m.get('minutes'), m.get('conclusion'), m.get('research'), m.get('stocks')])) for m in account.get('meetings', [])]
    _sheet(workbook, "例会管理", meeting_headers, meeting_rows,
        freeze="B2", table_name="PortfolioMeetings", widths=dict(enumerate([23, 41, 13, 17, 75, 37, 54, 56], 1)),
        height=80, notes={}, metadata=[("组合名称", name), ("记录口径", "当前组合已保存的例会记录；空白表示尚未录入。"), ("导出时间（北京时间 UTC+08:00）", captured)])
    execution = workbook.create_sheet("打新与执行")
    execution.sheet_view.showGridLines = False
    for index, width in enumerate([3, 14.363, 18.727, 18.727, 19.363, 15.633, 19.363, 39.363, 20], 1):
        execution.column_dimensions[get_column_letter(index)].width = width
    execution.column_dimensions['B'].width = 18.727
    execution.merge_cells('B2:H2')
    execution_data = account.get('execution') or {}
    for address, text in {"B2": "打新资格与执行安排", "B3": execution_data.get('notes') or "手动核实记录；未录入的权限和额度留空。", "B5": "打新资金上限", "B8": "沪市", "B9": "深市"}.items():
        _set_cell(execution[address], text)
    _set_cell(execution['C5'], execution_data.get('budget'))
    for row, market in ((8, 'SH'), (9, 'SZ')):
        values = (execution_data.get('markets') or {}).get(market) or {}
        for column, key in enumerate(('target_value', 'simulated_value', 'average20_value', 'permission', 'quota', 'status'), 3):
            _set_cell(execution.cell(row, column), values.get(key))
        estimate = execution.cell(row, 9)
        _set_cell(estimate, estimate_quota(values.get('average20_value')))
        estimate.number_format = '#,##0'
    _set_cell(execution['B11'], '组合 / 导出时间')
    _set_cell(execution['C11'], name)
    _set_cell(execution['E11'], captured)
    execution.merge_cells('E11:H11')
    execution.row_dimensions[11].height = 30
    _set_cell(execution['B12'], '记录口径')
    _set_cell(execution['C12'], '资金与市值单位：人民币元。G 列为已录入额度，历史导入值见执行说明。I 列按原模板从实际20日均市值推算，实际可申购额度以券商核实为准。')
    execution.merge_cells('C12:I12')
    execution.row_dimensions[12].height = 40
    execution.merge_cells("B3:H3")
    for index, label in enumerate(["市场", "目标普通股市值", "整手模拟市值", "实际20日均市值", "主板权限", "市值对应额度（股）", "当前状态", "模板推算额度（股）"], 2):
        _set_cell(execution.cell(7, index), label)
        execution.cell(7, index).fill = PatternFill("solid", fgColor="203956")
        execution.cell(7, index).font = Font(name="楷体", size=14, bold=True, color="FFFFFF")
    for row, height in {2: 34, 3: 38, 5: 25, 7: 58, 8: 46, 9: 46}.items():
        execution.row_dimensions[row].height = height
    workbook.move_sheet("标的白名单", offset=-1)
    workbook.active = workbook["组合持仓"]
    output = BytesIO()
    workbook.save(output)
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "组合"
    filename = f"STock-{safe_name[:60]}-复盘-{captured:%Y%m%d-%H%M%S}.xlsx"
    return filename, output.getvalue()
