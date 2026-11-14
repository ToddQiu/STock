"""Stable portfolio IDs; the original account stays in its existing config slot."""
from __future__ import annotations

import re
from typing import Any


DEFAULT_ID = "default"


def get_portfolio(data: dict[str, Any], portfolio_id: str = DEFAULT_ID) -> dict[str, Any]:
    if portfolio_id == DEFAULT_ID:
        if (data.get("portfolio") or {}).get("deleted"):
            raise ValueError("组合已删除，请返回组合列表刷新")
        if data.get("portfolio") is None:
            data["portfolio"] = {"base_currency": "CNY", "positions": []}
        if not isinstance(data["portfolio"], dict):
            raise ValueError("组合配置异常，请保留数据并检查备份")
        return data["portfolio"]
    if not isinstance(portfolio_id, str) or not re.fullmatch(r"[a-f0-9]{32}", portfolio_id):
        raise ValueError("组合编号无效")
    row = next((row for row in data.get("portfolios", []) if row["id"] == portfolio_id), None)
    if row is None:
        raise ValueError("组合不存在，请返回组合列表刷新")
    return row


def portfolios(data: dict[str, Any]) -> list[dict[str, Any]]:
    if (data.get("portfolio") or {}).get("deleted"):
        return list(data.get("portfolios", []))
    return [{**get_portfolio(data), "id": DEFAULT_ID, "name": get_portfolio(data).get("name") or "默认组合"},
            *data.get("portfolios", [])]


def all_positions(data: dict[str, Any]) -> list[dict[str, Any]]:
    # Quote collection is shared; the per-account rows remain separate for valuation and risk.
    rows = {}
    for account in portfolios(data):
        for row in account.get("positions", []):
            rows.setdefault((row["market"], row["symbol"]), row)
    return list(rows.values())


def validate_name(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60 or any(ord(c) < 32 for c in value):
        raise ValueError("组合名称须为1至60个字符，不能包含换行或控制字符")
    return value.strip()
