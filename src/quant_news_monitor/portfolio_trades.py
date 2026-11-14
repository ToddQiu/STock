"""Append-only, per-account trade snapshots; original-currency amounts never revalue."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from .portfolio_workspace import normalize_rationale


MONEY_STEP = Decimal("0.00000001")
QUANTITY_STEP = Decimal("0.000001")


def template_metadata(values, trade_date):
    """Optional statement fields; never rewrite executed quantities or monetary results."""
    from .portfolio_records import text
    result = {key: text(values.get(key, ''), label, limit) for key, label, limit in (
        ('account_name', '账户名称', 120), ('quantity_calculation', '交易数量计算', 2000),
        ('period_return_basis', '当期收益率口径', 2000))}
    when = values.get('trade_time', '')
    try:
        if not isinstance(when, str) or when and (len(when) not in (5, 8) or time.fromisoformat(when).tzinfo is not None):
            raise ValueError()
        if when:
            time.fromisoformat(when)
    except ValueError:
        raise ValueError('交易时间须为 HH:MM 或 HH:MM:SS') from None
    result['trade_time'] = when
    opened = values.get('opened_date', '')
    try:
        if not isinstance(opened, str) or opened and (date.fromisoformat(opened).isoformat() != opened or opened > trade_date[:10]):
            raise ValueError()
    except ValueError:
        raise ValueError('建仓日期无效，或晚于成交日期') from None
    result['opened_date'] = opened
    value = values.get('period_return_pct')
    if isinstance(value, bool):
        raise ValueError('当期总收益率须为有效数字')
    if value not in ('', None):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError('当期总收益率须为有效数字') from None
        import math
        if not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError('当期总收益率数值无效')
        if not result['period_return_basis']:
            raise ValueError('填写当期总收益率时，请同时说明起止日期与计算口径')
    else:
        value = None
    result['period_return_pct'] = value
    return result


def request_metadata(values, normalized):
    request_id = values.get("request_id")
    if request_id is None:
        return {}
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80 or any(ord(c) < 32 for c in request_id):
        raise ValueError("成交请求编号须为1至80个字符")
    normalized = dict(normalized)
    if normalized.get("origin") != "complete_fees":
        normalized["trade_date"] = values.get("trade_date")
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"request_id": request_id, "request_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def previous_request(account, metadata):
    if not metadata:
        return None
    previous = next((row for row in account.get("trades", []) if row.get("request_id") == metadata["request_id"]), None)
    if previous and previous.get("request_sha256") != metadata["request_sha256"]:
        raise ValueError("同一成交请求编号的内容已改变，请重新打开表单")
    return previous


def number(value, label, *, positive=False):
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise ValueError(f"{label}须为有效数字")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}须为有效数字") from None
    if not result.is_finite() or result < 0 or positive and result <= 0 or result > Decimal("1e12"):
        raise ValueError(f"{label}须为{'正数' if positive else '非负数'}，且不超过1万亿")
    return result


def amount(value):
    with localcontext() as context:
        context.prec = 50
        return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def trade_inputs(values, *, manual=False):
    quantity = number(values.get("quantity"), "成交数量", positive=True).quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    if quantity <= 0:
        raise ValueError("成交数量不能小于0.000001")
    execution_price = number(values.get("execution_price"), "成交价格", positive=True)
    # Omitted legacy fees are unknown; an explicit zero confirms a zero charge.
    fee = number(values["fee"], "本次交易费用") if "fee" in values else None
    extra = number(values.get("extra_buy_fee", 0), "未计入成本的买入费用")
    confirmed = values.get("cost_fees_confirmed", False)
    if not isinstance(confirmed, bool):
        raise ValueError("成本费用确认须为布尔值")
    if manual and (fee is None or not confirmed):
        raise ValueError("历史卖出须填写费用，并确认成本及未计入成本的买入费用")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    text = values.get("trade_date", today.isoformat() if not manual else "")
    try:
        if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text or date.fromisoformat(text) > today:
            raise ValueError()
    except ValueError:
        raise ValueError("成交日期须为有效日期，且不能晚于今天") from None
    note = values.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("成交备注须为不超过2000字的文字")
    return quantity, execution_price, fee, extra, confirmed, text, note.strip()


def record(*, position, action, quantity, execution_price, cost_amount, fee, extra_buy_fee,
           fee_complete, trade_date, note, closed=False, origin="adjust", rationale=None):
    gross = quantity * execution_price
    net = gross - cost_amount - fee - extra_buy_fee if action == "sell" and fee_complete else None
    return {"id": uuid.uuid4().hex, "action": action, "origin": origin,
            "trade_date": trade_date, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "market": position["market"], "symbol": position["symbol"],
            "name": position.get("name") or position["symbol"], "currency": position["currency"],
            "quantity": float(quantity), "average_cost": position["average_cost"],
            "execution_price": float(execution_price), "gross_amount": amount(gross),
            "cost_amount": amount(cost_amount) if action == "sell" else None,
            "fee": amount(fee) if fee is not None else None, "extra_buy_fee": amount(extra_buy_fee),
            "net_pnl": amount(net) if net is not None else None,
            "fee_complete": fee_complete, "closed": closed, "note": note,
            "cost_basis": "已记录均价成本；本次未入成本买费另扣，已含费用不重复扣除",
            **({"cycle_id": position["cycle_id"]} if position.get("cycle_id") else {}),
            **{key: (position.get('plan') or {})[key] for key in ('account_name', 'opened_date') if (position.get('plan') or {}).get(key)},
            **(rationale or {})}


def manual_record(values):
    from .security_master import CURRENCIES, normalize
    action = values.get("action", "sell")
    if action not in ("buy", "sell"):
        raise ValueError("请选择补录买入或卖出")
    market, symbol = normalize(values.get("market", ""), values.get("symbol", ""))
    currency = values.get("currency", CURRENCIES[market])
    if currency != CURRENCIES[market]:
        raise ValueError(f"该标的成交及成本须统一使用原币{CURRENCIES[market]}")
    name = values.get("name") or symbol
    if not isinstance(name, str) or len(name) > 120:
        raise ValueError("标的名称须为120字以内的文字")
    quantity, price, fee, extra, confirmed, when, note = trade_inputs(values, manual=action == "sell")
    if not values.get("trade_date") or fee is None:
        raise ValueError("历史成交须填写实际成交日期及费用，无费用请填0")
    rationale = normalize_rationale(values)
    cost = number(values.get("average_cost"), "对应平均成本", positive=True) if action == "sell" else (quantity * price + fee) / quantity
    closed = values.get("closed", False)
    opening = values.get("opening", False)
    if not isinstance(closed, bool) or not isinstance(opening, bool) or (closed and action != "sell") or (opening and action != "buy"):
        raise ValueError("建仓须为买入成交，清仓须为卖出成交")
    if action == "buy" and extra:
        raise ValueError("本次买入只填写本次费用，不填写卖出分摊费用")
    metadata = template_metadata(values, when) if "opened_date" in values else {}
    if opening:
        if metadata.get("opened_date") and metadata["opened_date"] != when:
            raise ValueError("建仓日期须与实际建仓成交日期一致")
        metadata["opened_date"] = when
    result = record(position={"market": market, "symbol": symbol, "name": name.strip(), "currency": currency,
                            "average_cost": float(cost)}, action=action, quantity=quantity,
                  execution_price=price, cost_amount=quantity * cost, fee=fee, extra_buy_fee=extra,
                  fee_complete=action == "buy" or confirmed, trade_date=when, note=note, closed=closed, origin="manual",
                  rationale=rationale)
    result.update(metadata)
    if opening:
        result["opening"] = True
    # Keep the legacy sell request fingerprint for retries saved by earlier versions.
    request = {"origin": "manual", "market": market, "symbol": symbol,
        "name": name.strip(), "currency": currency, "quantity": str(quantity.normalize()),
        "execution_price": str(price.normalize()), "average_cost": str(cost.normalize()),
        "fee": str(fee.normalize()), "extra_buy_fee": str(extra.normalize()),
        "cost_fees_confirmed": confirmed, "closed": closed, "note": note, **rationale}
    if action == "buy":
        request.update(action=action, opening=opening)
    request.update(metadata)
    result.update(request_metadata(values, request))
    return result


def _day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def trade_details(row):
    action = row["action"]
    opening = row.get("origin") == "opening" or row.get("opening") is True
    closed = action == "sell" and row.get("closed") is True
    fee, gross = row.get("fee"), row.get("gross_amount")
    cash_change = None
    basis = row.get("cost_amount")
    if fee is not None and gross is not None and action in {"buy", "sell"}:
        movement = Decimal(str(gross))
        if action == "buy":
            movement = -movement
        cash_change = amount(movement - Decimal(str(fee)))
        if action == "buy":
            basis = -cash_change
    opened = _day(row.get("opened_date"))
    when = _day(row.get("trade_date"))
    return {**row, "trade_type": "建仓" if opening else "清仓" if closed else "买入" if action == "buy" else "卖出",
            "cash_change": cash_change, "basis_amount": basis,
            "closed_date": when.isoformat() if closed and when else None,
            "holding_days": (when - opened).days if when and opened and when >= opened else None}


def _dividend_trade(row):
    tax, fee = row.get("tax"), row.get("fee")
    deductions = amount(Decimal(str(tax)) + Decimal(str(fee))) if tax is not None and fee is not None else None
    return {**row, "action": "dividend", "trade_type": "分红", "trade_date": row["payment_date"],
            "recorded_at": row.get("created_at"), "fee": deductions, "fee_complete": deductions is not None,
            "cash_change": row.get("net_amount"), "origin": "dividend"}


def _cycle_return(rows, close):
    opens = [row for row in rows if row["trade_type"] == "建仓"]
    closes = [row for row in rows if row["trade_type"] == "清仓"]
    if len(opens) != 1 or len(closes) != 1:
        return {}, "需要同一轮的建仓与清仓记录"
    opening = opens[0]
    first, last = _day(opening.get("trade_date")), _day(close.get("trade_date"))
    if not first or not last or any(not _day(row.get("trade_date")) or not first <= _day(row["trade_date"]) <= last for row in rows):
        return {}, "需要核对整轮成交日期"
    if any(row["currency"] != close["currency"] or not row.get("fee_complete") or row.get("fee") is None for row in rows):
        return {}, "需要补齐同币种成交和费用"

    # Entered dates determine the sequence. An explicitly marked opening comes
    # first and a final liquidation comes last when both fall on the same day.
    ordered = sorted(rows, key=lambda row: (_day(row["trade_date"]), row["trade_type"] != "建仓",
                   row["trade_type"] == "清仓", row.get("trade_time") or row.get("recorded_at") or ""))
    balance = bought = allocated = extra = net = Decimal(0)
    for row in ordered:
        quantity = Decimal(str(row["quantity"]))
        if row["action"] == "buy":
            balance += quantity
            bought += Decimal(str(row["gross_amount"])) + Decimal(str(row["fee"]))
        else:
            balance -= quantity
            if row.get("cost_amount") is None or row.get("net_pnl") is None:
                return {}, "需要补齐卖出对应成本和已实现净盈亏"
            allocated += Decimal(str(row["cost_amount"]))
            extra += Decimal(str(row.get("extra_buy_fee") or 0))
            net += Decimal(str(row["net_pnl"]))
        if balance < 0 or balance == 0 and row is not close:
            return {}, "需要核对整轮买卖数量与清仓标志"
    if balance != 0:
        return {}, "需要补齐整轮买卖数量"
    # Individual allocations and executions are stored rounded to eight decimals.
    if bought <= 0 or abs(bought - allocated) > max(Decimal("0.01"), MONEY_STEP * len(rows)):
        return {}, "累计买入成本与卖出对应成本不一致"
    total_cost = bought + extra
    return {"closed_return_pct": amount(net / total_cost * 100), "closed_buy_cost": amount(total_cost),
            "closed_net_pnl": amount(net), "opened_date": first.isoformat(), "holding_days": (last - first).days}, None


def _mark_closed_returns(rows):
    groups = {}
    for row in rows:
        if row.get("cycle_id"):
            key = (row["market"], row["symbol"], row["cycle_id"])
        elif row.get("opened_date"):
            key = (row["market"], row["symbol"], row["currency"], row.get("account_name") or "", row["opened_date"])
        else:
            key = (row["id"],)
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        for row in group:
            if row["trade_type"] != "清仓":
                continue
            values, error = _cycle_return(group, row)
            if not error and not row.get("cycle_id"):
                # An undated same-security backfill inside the cycle must be
                # assigned first; otherwise an apparently balanced subset can mislead.
                first, last = _day(values["opened_date"]), _day(row["trade_date"])
                if any(not other.get("cycle_id") and not other.get("opened_date") and
                       (other["market"], other["symbol"], other["currency"]) == (row["market"], row["symbol"], row["currency"]) and
                       _day(other.get("trade_date")) and first <= _day(other["trade_date"]) <= last for other in rows):
                    values, error = {}, "同一期间有未填写建仓日期的成交"
            row.update(closed_return_pct=None, closed_return_status=error)
            row.update(values)


def summarize(records, convert, *, dividends=()):
    """Do not present a partial fee/FX subtotal as a complete realized net result."""
    rows, by_currency = [], {}
    sell_count = complete_count = incomplete_count = 0
    known_net, known_fees = [], []
    sold_costs = []
    sold_cost_complete = True
    fx_complete = fees_complete = True
    for original in records:
        row = trade_details(original)
        currency = row["currency"]
        group = by_currency.setdefault(currency, {"currency": currency, "net_pnl": 0., "fees": 0.,
                                                  "sell_count": 0, "complete_count": 0, "incomplete_count": 0})
        for key in ("gross_amount", "cost_amount", "fee", "extra_buy_fee", "net_pnl"):
            row[key + "_base"] = convert(row.get(key), currency)
        fee = row.get("fee")
        if fee is None:
            fees_complete = False
        total_fee = (fee or 0) + row.get("extra_buy_fee", 0)
        group["fees"] += total_fee
        converted = convert(total_fee, currency)
        if converted is None:
            fx_complete = False
        else:
            known_fees.append(converted)
        if row["action"] == "sell":
            sell_count += 1; group["sell_count"] += 1
            cost, extra = row["cost_amount_base"], row["extra_buy_fee_base"]
            if cost is None or extra is None:
                sold_cost_complete = False
            else:
                sold_costs.append(cost + extra)
            net = row.get("net_pnl")
            if net is None or not row.get("fee_complete"):
                incomplete_count += 1; group["incomplete_count"] += 1
            else:
                complete_count += 1; group["complete_count"] += 1; group["net_pnl"] += net
                converted = row["net_pnl_base"]
                if converted is None:
                    fx_complete = False
                else:
                    known_net.append(converted)
        rows.append(row)
    _mark_closed_returns(rows)
    net_total = round(sum(known_net), 8)
    sold_cost = round(sum(sold_costs), 8) if sold_cost_complete else None
    realized_pct = net_total / sold_cost * 100 if sold_cost and sold_cost > 0 and incomplete_count == 0 and fx_complete else None
    for group in by_currency.values():
        group["net_pnl"] = round(group["net_pnl"], 8)
        group["fees"] = round(group["fees"], 8)
        group["known_net_pnl"] = group["net_pnl"]
        if group["incomplete_count"]:
            group["net_pnl"] = None
    dividends = [_dividend_trade(row) for row in dividends if row.get("status") == "received"]
    return {"records": rows + dividends, "summary": {
        "record_count": len(rows), "sell_count": sell_count, "complete_count": complete_count,
        "dividend_count": len(dividends),
        "incomplete_count": incomplete_count, "fx_complete": fx_complete, "fees_complete": fees_complete,
        "realized_net_base": net_total if incomplete_count == 0 and fx_complete else None,
        "realized_cost_base": sold_cost, "realized_return_pct": realized_pct,
        "known_realized_net_base": net_total, "fees_base": round(sum(known_fees), 8) if fees_complete and fx_complete else None,
        "known_fees_base": round(sum(known_fees), 8), "by_currency": list(by_currency.values()),
        "conversion_note": "原币成交记录固定；外币汇总按当前参考汇率折算，不含汇兑损益。",
        "history_note": "仅汇总已记录成交；旧版未保存的清仓历史须按真实成交补录。"}}
