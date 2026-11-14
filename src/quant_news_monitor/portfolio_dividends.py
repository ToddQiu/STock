"""Cash distributions are separate from original position costs and price P&L."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID
from . import portfolio_cash


def money(value, label, currency):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label}须为有效金额") from None
    if not number.is_finite() or not 0 <= number <= Decimal("1000000000000"):
        raise ValueError(f"{label}须在 0 到 1 万亿之间")
    unit = Decimal("1") if currency == "KRW" else Decimal("0.01")
    return number.quantize(unit, rounding=ROUND_HALF_UP)


def normalize(payload, account, old=None):
    try:
        record_id = str(UUID(str(payload.get("id", ""))))
    except ValueError:
        raise ValueError("股息记录编号无效") from None
    if record_id != payload.get("id"):
        raise ValueError("股息记录编号须使用标准 UUID 格式")
    key = (payload.get("market"), payload.get("symbol"))
    security = next((r for r in account.get("positions", []) + account.get("dividends", [])
                     if (r.get("market"), r.get("symbol")) == key), None)
    if not security or old and key != (old["market"], old["symbol"]):
        raise ValueError("请选择本组合的持仓；已有记录不能更换标的")
    currency = payload.get("currency")
    status = payload.get("status")
    action = payload.get("cash_action")
    if currency not in {"CNY", "USD", "HKD", "KRW"}:
        raise ValueError("股息币种无效")
    if status not in {"expected", "received"} or action not in {"record_only", "credit"}:
        raise ValueError("股息状态或现金处理方式无效")
    if status == "expected" and action != "record_only":
        raise ValueError("待派发股息不能计入现金")
    dates = {}
    for key in ("payment_date", "ex_date"):
        value = payload.get(key) or ""
        try:
            dates[key] = date.fromisoformat(value).isoformat() if value else ""
        except (ValueError, TypeError):
            raise ValueError("请填写有效日期") from None
    if not dates["payment_date"]:
        raise ValueError("请填写派发 / 到账日期")
    if status == "received" and dates["payment_date"] > datetime.now(timezone(timedelta(hours=8))).date().isoformat():
        raise ValueError("未来股息请登记为待派发")
    gross = money(payload.get("gross_amount"), "税前股息", currency)
    if gross <= 0:
        raise ValueError("税前股息须大于 0")
    deductions = []
    for key, label in (("tax", "扣税"), ("fee", "费用")):
        value = payload.get(key)
        deductions.append(None if status == "expected" and value in (None, "") else money(value, label, currency))
    tax, fee = deductions
    if sum((v for v in deductions if v is not None), Decimal(0)) > gross:
        raise ValueError("扣税与费用合计不能超过税前股息")
    net = gross - tax - fee if tax is not None and fee is not None else None
    now = datetime.now(timezone.utc).isoformat()
    return {"id": record_id, "market": security["market"], "symbol": security["symbol"],
            "name": security.get("name", security["symbol"]), "currency": currency,
            "status": status, "cash_action": action, **dates,
            "gross_amount": float(gross), "tax": float(tax) if tax is not None else None,
            "fee": float(fee) if fee is not None else None, "net_amount": float(net) if net is not None else None,
            "note": str(payload.get("note") or "").strip()[:300],
            "created_at": old["created_at"] if old else now, "updated_at": now}


def reconcile_cash(account, old, new):
    # Estimated cash already includes received dividends from the ledger.
    if portfolio_cash.mode(account) == "auto":
        return
    # Reverse only cash this ledger actually credited, then apply the replacement.
    def credit(row):
        return row if row and row["status"] == "received" and row["cash_action"] == "credit" else None
    previous, replacement = credit(old), credit(new)
    if "cash_balances" in account:
        balances = portfolio_cash.balances(account)
        currencies = {row["currency"] for row in (previous, replacement) if row}
        for currency in currencies:
            delta = sum((Decimal(str(row["net_amount"])) * sign for row, sign in
                         ((previous, -1), (replacement, 1)) if row and row["currency"] == currency), Decimal(0))
            balance = money(balances.get(currency, 0), "组合现金", currency) + delta
            if balance < 0:
                raise ValueError("撤回已入账股息后现金不足，请先核对组合现金")
            balances[currency] = float(money(balance, "组合现金", currency))
        account["cash_balances"] = balances
        return
    currency = account.get("cash_currency", "CNY")
    if any(row and row["currency"] != currency for row in (previous, replacement)):
        raise ValueError("到账币种与组合现金币种不同，不能自动增减现金；请仅登记并单独核对换汇")
    delta = Decimal(str(replacement["net_amount"])) if replacement else Decimal(0)
    delta -= Decimal(str(previous["net_amount"])) if previous else Decimal(0)
    if delta:
        balance = money(account.get("cash", 0), "组合现金", currency) + delta
        if balance < 0:
            raise ValueError("撤回已入账股息后现金不足，请先核对组合现金")
        account["cash"] = float(money(balance, "组合现金", currency))


def summarize(records, convert):
    received = [r for r in records if r["status"] == "received"]
    expected = [r for r in records if r["status"] == "expected"]

    def total(rows, field):
        values = [convert(r[field], r["currency"]) for r in rows]
        return sum(values) if values and all(v is not None for v in values) else None

    return {"record_count": len(records), "received_count": len(received), "expected_count": len(expected),
            "received_net_base": total(received, "net_amount"),
            "expected_gross_base": total(expected, "gross_amount")}
