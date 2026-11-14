"""Private, versioned investment plans. Workbook content is data, never executable."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile

KIND = "investment_plan"
MAX_BYTES = 1_000_000


def number(value, label, optional=False, low=0, high=1e12):
    if value is None or isinstance(value, str) and not value.strip():
        if optional:
            return None
        raise ValueError(f"请填写{label}")
    if isinstance(value, bool):
        raise ValueError(f"{label}格式无效")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}须为有效数字") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label}超出有效范围")
    return result


def day(value, optional=False):
    if value in (None, "") and optional:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError("日期无效") from None


def security(value):
    value = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d+)\.(SH|SZ|BJ|HK|KS|KQ)", value)
    if not match:
        raise ValueError(f"无法确定证券的上市市场：{value}")
    code, exchange = match.groups()
    market = "HK" if exchange == "HK" else "KR" if exchange in {"KS", "KQ"} else "CN"
    symbol = code.zfill(5 if market == "HK" else 6)
    return market, symbol, f"{market}:{symbol}"


def _text(value, limit=10000):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("文字字段格式或长度无效")
    return value.strip()


def validate_plan(value):
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("不是 STock 配置方案")
    p = copy.deepcopy(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(p.get("id", ""))) or not _text(p.get("name"), 120):
        raise ValueError("方案标识或名称无效")
    if p.get("currency") not in {"CNY", "USD", "HKD", "KRW"}:
        raise ValueError("方案币种无效")
    p["budget"] = number(p.get("budget"), "预算")
    p["cash_target"] = number(p.get("cash_target"), "现金预留")
    day(p.get("as_of"), True)
    for name, limit in (("targets", 500), ("research", 500), ("valuations", 1500), ("reviews", 1000), ("trades", 2000)):
        if not isinstance(p.get(name), list) or len(p[name]) > limit or any(not isinstance(row, dict) for row in p[name]):
            raise ValueError(f"{name}列表格式或数量无效")
    if not isinstance(p.get("funds"), dict) or len(p["funds"]) > 100:
        raise ValueError("ETF 快照格式无效")
    targets = {}
    for row in p["targets"]:
        target = f"{row.get('market')}:{row.get('symbol')}"
        if (target != row.get("key") or not re.fullmatch(r"(?:CN|US|HK|KR|FUND|ASSET):[A-Z0-9.^=_-]{1,24}", target)
                or target in targets or row.get("currency") != p["currency"] or not _text(row.get("name"), 120)):
            raise ValueError("目标标的、币种或唯一标识无效")
        if {"CN":"CNY", "FUND":"CNY", "US":"USD", "HK":"HKD", "KR":"KRW", "ASSET":"USD"}[row["market"]] != row["currency"]:
            raise ValueError("方案按单一交易币种记账，请为不同币种另建方案")
        row["amount"] = number(row.get("amount"), "目标金额")
        row["shock_pct"] = number(row.get("shock_pct"), "压力幅度", True, -100, 1000)
        row["reference_price"] = number(row.get("reference_price"), "参考价", True)
        day(row.get("reference_date"), True)
        targets[target] = row
    if sum(r["amount"] for r in targets.values()) + p["cash_target"] > p["budget"] + .01:
        raise ValueError("目标金额与现金预留合计超过预算")
    for key, fund in p["funds"].items():
        if not isinstance(fund, dict) or key != f"{fund.get('market')}:{fund.get('symbol')}":
            raise ValueError("ETF 标识无效")
        day(fund.get("as_of"))
        if not isinstance(fund.get("holdings"), list) or len(fund["holdings"]) > 2500:
            raise ValueError("ETF 持仓行数无效")
        for row in fund["holdings"]:
            if not isinstance(row, dict) or not _text(row.get("name"), 120):
                raise ValueError("ETF 成分缺失名称")
            if row.get("key") and row["key"] != f"{row.get('market')}:{row.get('symbol')}":
                raise ValueError("ETF 成分标识不一致")
            row["weight"] = number(row.get("weight"), "权重", low=-1000, high=1000)
    for row in p["valuations"]:
        if row.get("target") not in targets or row.get("scenario") not in {"bull", "base", "bear"}:
            raise ValueError("估值标的或情景无效")
        for field, low, high in (("eps", 0, 1e12), ("growth", -99.99, 1000), ("pe", .01, 10000), ("discount", 0, 1000), ("dividends", 0, 1e12)):
            row[field] = number(row.get(field), field, True, low, high)
        row["years"] = number(row.get("years"), "年数", low=1, high=50)
        day(row.get("as_of"), True)
    for row in p["research"]:
        if row.get("target") not in targets:
            raise ValueError("研究标的不在方案中")
        day(row.get("as_of"), True)
    for row in p["reviews"]:
        day(row.get("date"))
        if row.get("target") and row["target"] not in targets:
            raise ValueError("复盘标的不在方案中")
        if not _text(row.get("title"), 160) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(row.get("id", ""))):
            raise ValueError("复盘标题或标识无效")
    if len({r["id"] for r in p["reviews"]}) != len(p["reviews"]):
        raise ValueError("复盘标识重复")
    _validate_trades(p, targets)
    # Bound all nested text, reject non-finite JSON and executable/credential URLs.
    def walk(node):
        if isinstance(node, str):
            _text(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                if key == "source_url" and item and not re.fullmatch(r"https?://[A-Za-z0-9][A-Za-z0-9.-]+(?::\d+)?(?:[/?#][^\s\\]*)?", str(item)):
                    raise ValueError("来源链接须为公开 HTTP(S) 地址")
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, float) and not math.isfinite(node):
            raise ValueError("不能保存非有限数字")
    walk(p)
    if len(json.dumps(p, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("方案超过 1 MB，请拆分记录")
    return p


def _validate_trades(plan, targets):
    cash, lots, ids = {}, {}, set()
    for row in sorted(enumerate(plan["trades"]), key=lambda pair: (str(pair[1].get("date", "")), pair[0])):
        row = row[1]
        day(row.get("date"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(row.get("id", ""))) or row["id"] in ids:
            raise ValueError("流水标识无效或重复")
        ids.add(row["id"])
        account = _text(row.get("account"), 80)
        if not account or row.get("currency") not in {"CNY", "USD", "HKD", "KRW"}:
            raise ValueError("流水账户或币种无效")
        group = (account, row["currency"])
        cash.setdefault(group, 0)
        kind, fee = row.get("kind"), number(row.get("fee"), "费用")
        row["fee"] = fee
        if kind in {"deposit", "withdraw"}:
            amount = number(row.get("amount"), "资金金额", low=.000001)
            if fee:
                raise ValueError("出入金费用须为零")
            cash[group] += amount if kind == "deposit" else -amount
        elif kind in {"initial", "buy", "sell", "dividend"}:
            target = targets.get(row.get("target"))
            if not target or target["currency"] != row["currency"]:
                raise ValueError("流水标的或币种无效")
            lot = (*group, row["target"])
            lots.setdefault(lot, 0)
            if kind == "dividend":
                amount = number(row.get("amount"), "分红金额", low=.000001)
                if lots[lot] <= 0 or fee > amount:
                    raise ValueError("分红须有持仓且净金额非负")
                cash[group] += amount - fee
            else:
                quantity = number(row.get("quantity"), "数量", low=.000001)
                price = number(row.get("price"), "价格", low=.000001)
                if kind == "sell" and quantity > lots[lot] + 1e-8:
                    raise ValueError("卖出数量超过该账户持仓")
                lots[lot] = round(lots[lot] + (-quantity if kind == "sell" else quantity), 6)
                if kind != "initial":
                    cash[group] += (quantity * price if kind == "sell" else -quantity * price) - fee
        else:
            raise ValueError("流水类型无效")
        cash[group] = round(cash[group], 6)
        if cash[group] < -.000001:
            raise ValueError("现金不足，请先录入实际入金")


def _optional(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def parse_workbook(data, filename):
    from openpyxl import load_workbook
    if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("请选择 XLSX / XLSM 配置表或 STock 方案 JSON")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 20_000_000:
                raise ValueError("工作簿展开内容过大")
        book = load_workbook(io.BytesIO(data), data_only=True, read_only=True, keep_links=False)
    except (BadZipFile, OSError, KeyError):
        raise ValueError("无法读取工作簿") from None
    try:
        required = {"标的研究库", "A股配置方案", "ETF持仓穿透"}
        if not required <= set(book.sheetnames):
            raise ValueError("需要标的研究库、A股配置方案、ETF持仓穿透三个工作表")
        if len(book.sheetnames) > 30 or any(s.max_row > 10000 or s.max_column > 100 for s in book):
            raise ValueError("工作簿行列数量过多")
        # Materialize bounded sheets once; repeated read-only cell lookup reparses XML.
        sheets = {name: [tuple(row) + (None,) * max(0, 40-len(row)) for row in book[name].values]
                  for name in required | ({"基准估值模型"} & set(book.sheetnames))}
        def cell(sheet, row, col):
            values = sheets.get(sheet, [])
            return values[row-1][col-1] if row <= len(values) and col <= len(values[row-1]) else None
        digest = hashlib.sha256(data).hexdigest()
        p = dict(schema=1, id="excel-" + digest[:24], name="成都股票池配置方案", currency="CNY",
                 budget=number(cell("A股配置方案", 4, 4), "预算"), cash_target=number(cell("A股配置方案", 9, 7), "现金预留"),
                 as_of=day(cell("A股配置方案", 4, 7), True), archived=False,
                 source={"filename": Path(filename).name, "sha256": digest, "imported_at": datetime.now(timezone.utc).isoformat(),
                         "note": "用户工作簿；原始人工观点，未经重新核验。只读取数据，不执行公式、宏或联网查询。"},
                 targets=[], research=[], funds={}, valuations=[], reviews=[], trades=[])
        research = {}
        for row in sheets["标的研究库"][1:]:
            if len(row) < 20 or not row[1]:
                continue
            market, symbol, target = security(row[1])
            research[target] = row
            p["research"].append(dict(target=target, name=str(row[0] or symbol), business=str(row[16] or ""),
                thesis=str(row[10] or ""), risks="", original_rating=str(row[3] or ""), as_of=day(row[19], True),
                source_url="", review=""))
        for row in sheets["A股配置方案"][9:17]:
            market, symbol, target = security(row[4])
            source = research.get(target)
            p["targets"].append(dict(key=target, market=market, symbol=symbol, name=str(row[3]), currency="CNY",
                category=str(row[2] or ""), amount=number(row[6], "目标金额"), is_etf=bool(source and source[4] == "ETF"),
                reference_price=_optional(row[7]), reference_date="", shock_pct=None))
        rows = sheets["ETF持仓穿透"]
        start = next((i for i, r in enumerate(rows) if r[:3] == ("ETF代码", "ETF名称", "证券代码")), None)
        if start is None:
            raise ValueError("找不到 ETF 底层持仓明细")
        for index, row in enumerate(rows[start+1:], start+2):
            if not row[0]:
                continue
            market, symbol, target = security(row[0])
            hm, hs, hk = security(row[2])
            report_day = day(row[6])
            fund = p["funds"].setdefault(target, dict(market=market, symbol=symbol, name=str(row[1]), as_of=report_day, source_urls=[], holdings=[]))
            if fund["as_of"] != report_day:
                raise ValueError("同一 ETF 有多个报告期，请先拆分")
            if row[11] and str(row[11]).startswith(("https://", "http://")) and row[11] not in fund["source_urls"]:
                fund["source_urls"].append(str(row[11]))
            fund["holdings"].append(dict(key=hk, market=hm, symbol=hs, name=str(row[3]), weight=number(row[5], "占净值比例", low=-10, high=10)*100,
                                         type="stock", source_row=index))
        for row in sheets.get("基准估值模型", [])[21:23]:
            if not row[0]:
                continue
            target = security(row[0])[2]
            for scenario in ("bull", "base", "bear"):
                p["valuations"].append(dict(target=target, scenario=scenario, eps=_optional(row[5]),
                    growth=None if _optional(row[6]) is None else row[6]*100, pe=_optional(row[7]),
                    discount=None if _optional(row[8]) is None else row[8]*100, dividends=_optional(row[9]), years=_optional(row[10]) or 4,
                    conditions="", invalidation="", source_url="", as_of=""))
        warnings = ["仅导入配置与研究，不导入计划数量为真实持仓，也不覆盖个人本金。", "工作簿的收益断链、空目标价及计算缓存未导入。",
                    "ETF 权重保持报告原值；未披露余额单列，不凑足 100%。", "文件参考价未取得可确认日期，不用于实际收益计算。"]
        return validate_plan(p), warnings
    finally:
        book.close()


def parse_payload(payload):
    filename = _text(payload.get("filename"), 200)
    try:
        data = base64.b64decode(payload.get("data", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ValueError("文件编码无效") from None
    if not data or len(data) > MAX_BYTES:
        raise ValueError("文件须为 1 MB 以内")
    if Path(filename).suffix.lower() == ".json":
        try:
            value = json.loads(data.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("方案 JSON 无法读取") from None
        if not isinstance(value, dict) or value.get("format") != "stock-investment-plan":
            raise ValueError("请选择 STock 导出的配置方案")
        return validate_plan(value.get("plan")), ["仅导入本地方案，不覆盖个人资产。"]
    return parse_workbook(data, filename)


def plans(ledger, include_archived=False, portfolio_id="default"):
    kind = KIND if portfolio_id == "default" else KIND + ":" + portfolio_id
    with ledger.session() as db:
        rows = db.execute("SELECT v.* FROM input_versions v WHERE kind=? AND id=(SELECT MAX(x.id) FROM input_versions x WHERE x.kind=v.kind AND x.identity=v.identity) ORDER BY id DESC", (kind,)).fetchall()
    return [{"plan": json.loads(r["payload"]), "version": r["version"]} for r in rows if include_archived or not json.loads(r["payload"]).get("archived")]


def save(ledger, payload, portfolio_id="default"):
    plan = validate_plan(payload.get("plan"))
    version = payload.get("base_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError("方案版本无效")
    row = ledger.observe(KIND if portfolio_id == "default" else KIND + ":" + portfolio_id, plan["id"], plan, expected_version=version)
    return {"plan": plan, "version": row["version"]}


def import_plan(ledger, payload, portfolio_id="default"):
    plan, warnings = parse_payload(payload)
    existing = next((p for p in plans(ledger, True, portfolio_id) if p["plan"]["id"] == plan["id"]), None)
    if existing:
        return {**existing, "already_imported": True, "warnings": warnings}
    result = save(ledger, {"plan": plan, "base_version": 0}, portfolio_id)
    return {**result, "already_imported": False, "warnings": warnings}
