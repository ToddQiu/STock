"""Holding-specific notes and read-only research summaries for the portfolio view."""
from __future__ import annotations

import math
from datetime import date, timedelta, timezone


def normalize_rationale(value, keys=("entry_analysis", "buy_reason", "exit_analysis", "sell_reason")):
    labels = {"entry_analysis": "买入分析", "buy_reason": "买入原因",
              "exit_analysis": "卖出分析", "sell_reason": "卖出原因"}
    result = {}
    for key in keys:
        if key not in value:
            continue
        text = value[key]
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError(f"{labels[key]}须为不超过2000字的文字")
        if key.endswith("reason") and not text.strip():
            raise ValueError(f"请填写{labels[key]}")
        result[key] = text.strip()
    return result


def normalize_plan(value):
    if not isinstance(value, dict):
        raise ValueError("持仓资料须为对象")
    result = normalize_rationale(value, ("entry_analysis", "buy_reason"))
    for key, limit in (("sector", 120), ("usage", 500), ("valuation_basis", 2000),
                       ("account_name", 120), ("strategy", 500), ("rating", 80),
                       ("business", 2000), ("risks", 2000), ("asset_class", 120)):
        if key in {"account_name", "strategy", "rating", "business", "risks", "asset_class"} and key not in value:
            continue
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError(f"{key}须为不超过{limit}字的文字")
        result[key] = text.strip()
    for key in ("opened_date", "valuation_as_of"):
        text = value.get(key, "")
        try:
            if not isinstance(text, str) or text and (date.fromisoformat(text).isoformat() != text or date.fromisoformat(text) > date.today()):
                raise ValueError()
        except ValueError:
            raise ValueError("建仓日期和估值资料日期须为有效日期，且不能晚于今天") from None
        result[key] = text
    for key in ("valuation_low", "valuation_high"):
        raw = value.get(key)
        if raw in (None, ""):
            result[key] = None
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("估值上下限须为有效正数") from None
        if isinstance(raw, bool) or not math.isfinite(number) or not 0 < number <= 1e12:
            raise ValueError("估值上下限须为有效正数，且不超过1万亿")
        result[key] = number
    low, high = result["valuation_low"], result["valuation_high"]
    if (low is None) != (high is None) or low is not None and low >= high:
        raise ValueError("请同时填写估值上下限，且下限小于上限")
    if low is not None and (not result["valuation_basis"] or not result["valuation_as_of"]):
        raise ValueError("估值区间须注明依据和资料日期")
    return result


def holding_metadata(position, portfolio_id, now):
    opened = (position.get("plan") or {}).get("opened_date")
    day = now.astimezone(timezone(timedelta(hours=8))).date()
    try:
        duration = (day - date.fromisoformat(opened)).days
    except (TypeError, ValueError):
        duration = -1
    return {"holding_id": f"{portfolio_id}:{position['market']}:{position['symbol']}",
            "holding_status": "已建仓", "holding_days": duration if duration >= 0 else None}


def position_workspace(position, peers, cards):
    target = f"{position['market']}:{position['symbol']}"
    peer = next(iter(sorted((r for r in peers if str(r.get("target", "")).upper() == target),
                            key=lambda r: str(r.get("observed_at", "")), reverse=True)), {})
    plan = position.get("plan") or {}
    own_sector = position.get("industry") or position.get("sector") or position.get("investment_focus")
    sector = plan.get("sector") or own_sector or peer.get("industry") or peer.get("sector")
    source = "手动补录" if plan.get("sector") else (position.get("classification_source") or position.get("product_source")) if own_sector else peer.get("source")
    source_url = (position.get("classification_source_url") or position.get("product_source_url")) if own_sector else peer.get("source_url")
    pe = peer.get("pe") or {}
    pe_value = pe.get("value")
    if isinstance(pe_value, bool) or not isinstance(pe_value, (int, float)) or not math.isfinite(pe_value):
        pe = {}
    if position.get("asset_type") in {"fund", "etf", "etn", "leveraged_etf", "inverse_etf"} or position.get("market") == "FUND":
        pe = {"not_applicable": True}
    return {"sector": sector, "sector_source": source, "sector_source_url": source_url if not plan.get("sector") else None,
            "pe": pe, "pe_source": peer.get("source"), "pe_source_url": peer.get("source_url"),
            "cards": [r for r in cards if str(r.get("target", "")).strip().upper() == target]}
