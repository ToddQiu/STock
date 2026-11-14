"""Original-currency cash balances, including legacy single-currency accounts."""

from decimal import Decimal, InvalidOperation


def balances(account):
    if "cash_balances" in account:
        return dict(account["cash_balances"])
    return {account.get("cash_currency", "CNY"): account["cash"]} if account.get("cash") is not None else {}


def mode(account):
    return account.get("cash_mode", "manual" if balances(account) else "auto")


def normalize_balances(value, *, label="库存现金"):
    if not isinstance(value, dict) or not value or not set(value) <= {"CNY", "HKD", "USD", "KRW"}:
        raise ValueError(f"请选择人民币、港币、美元、韩元中的至少一种{label}币种")
    result = {}
    for currency, raw in value.items():
        try:
            number = Decimal(str(raw))
        except InvalidOperation:
            raise ValueError(f"{currency} {label}须为有效金额") from None
        if not number.is_finite() or not 0 <= number <= 10**12:
            raise ValueError(f"{currency} {label}须在 0 到 1 万亿之间")
        if number != number.quantize(Decimal("0.01")):
            raise ValueError(f"{currency} {label}最多保留两位小数")
        result[currency] = float(number)
    return result
