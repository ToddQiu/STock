"""Provider-owned security identity; user-entered labels never become company names."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

CURRENCIES = {"US": "USD", "CN": "CNY", "HK": "HKD", "KR": "KRW", "FUND": "CNY"}
_cache: dict[str, tuple[float, dict]] = {}
_profiles: dict[str, dict] = {}
_lock = threading.RLock()


def normalize(market: str, symbol: str) -> tuple[str, str]:
    market, symbol = str(market).strip().upper(), str(symbol).strip().upper()
    if market not in CURRENCIES:
        raise ValueError("请选择美股、港股、A股、韩股或中国基金")
    if market == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", symbol):
        raise ValueError("美股证券代码格式无效")
    if market in {"CN", "HK", "KR", "FUND"}:
        suffixes = {"CN": ("SS", "SZ", "BJ"), "HK": ("HK",), "KR": ("KS", "KQ"), "FUND": ()}[market]
        match = re.fullmatch(r"(\d{1,6})(?:\.([A-Z]{2}))?", symbol)
        if not match or (match[2] and match[2] not in suffixes) or not int(match[1]):
            raise ValueError("证券代码与市场不匹配")
        code, suffix = match[1], match[2]
        if market in {"CN", "FUND"} and len(code) != 6:
            raise ValueError("A股及基金代码须为六位")
        if market == "HK" and len(code) > 5:
            raise ValueError("港股代码至多五位")
        if market == "FUND":
            symbol = code
        else:
            code = str(int(code)).zfill(4 if market == "HK" else 6)
            suffix = suffix or ("HK" if market == "HK" else "KS" if market == "KR" else "SS" if code[0] in "569" else "BJ" if code[0] in "48" else "SZ")
            symbol = code + "." + suffix
    return market, symbol


def catalog() -> list[dict]:
    with _lock:
        if not hasattr(catalog, "rows"):
            catalog.rows = json.loads(Path(__file__).with_name("security_directory.json").read_text(encoding="utf-8"))
            products = json.loads(Path(__file__).with_name("security_products.json").read_text(encoding="utf-8"))
            keys = {(r['market'], r['symbol']) for r in products}
            catalog.rows = [r for r in catalog.rows if (r['market'], r['symbol']) not in keys] + products
        return catalog.rows


def cached_identity(market: str, symbol: str) -> dict | None:
    market, symbol = normalize(market, symbol)
    key = market + ":" + symbol
    with _lock:
        cached = _cache.get(key)
        base = cached[1] if cached and time.monotonic() - cached[0] < 86400 else next((row for row in catalog() if row["market"] == market and row["symbol"] == symbol), {})
        profile = _profiles.get(key, {})
        result = {**base, **profile}
        result['name'] = preferred_name(market, profile.get('name'), base.get('name'))
        return copy.deepcopy(result) if result.get('name') else None


def preferred_name(market: str, *names: Any) -> str:
    names = [name.strip() for name in names if isinstance(name, str) and name.strip()]
    if market in {'CN', 'HK', 'FUND'}:
        return next((name for name in names if re.search(r'[\u3400-\u9fff]', name) or re.fullmatch(r'[0-9]+ETF', name, re.IGNORECASE)), next(iter(names), ''))
    if market == 'US':
        return next((name for name in names if re.search(r'[A-Za-z]', name) and not re.search(r'[\u3400-\u9fff]', name)), next(iter(names), ''))
    return next(iter(names), '')


def display_name(market: str, symbol: str, *names: Any) -> str:
    try:
        market, symbol = normalize(market, symbol)
        identity = cached_identity(market, symbol) or {}
    except ValueError:
        identity = {}
    candidates = [name for name in (*names, identity.get('name'))
                  if isinstance(name, str) and name.strip().upper() not in {symbol, market + ':' + symbol}]
    if market == 'US':
        candidates = [name for name in candidates if re.search(r'[A-Za-z]', name) and not re.search(r'[\u3400-\u9fff]', name)]
    return preferred_name(market, *candidates) or symbol


def remember_profiles(rows: dict[str, dict]) -> None:
    with _lock:
        _profiles.update(copy.deepcopy(rows))


def resolve(market: str, symbol: str) -> dict[str, Any]:
    market, symbol = normalize(market, symbol)
    result = cached_identity(market, symbol)
    if result:
        return result
    if market == "FUND":
        from .fund_data import search_china_funds
        rows = search_china_funds(symbol)
        if isinstance(rows, dict):
            rows = rows.get("items", rows.get("results", []))
        result = next((row for row in rows if row.get("code") == symbol), None)
        if not result:
            raise ValueError("未找到该基金的公开登记名称")
        result = {**result, "source": "天天基金公开目录"}
    else:
        from .adr_monitor import _fetch_yahoo_quote
        if market in {"CN", "HK"}:
            from .public_quotes import fetch_tencent_quote
            quote = fetch_tencent_quote(market, symbol)
        else:
            try:
                quote = _fetch_yahoo_quote(symbol, 6)
            except (OSError, ValueError):
                if market != "US":
                    raise
                from .public_quotes import fetch_tencent_quote
                quote = fetch_tencent_quote(market, symbol)
        name = quote.get("name")
        if not name or name.upper() == symbol or quote.get("currency") != CURRENCIES[market]:
            raise ValueError("来源未能确认证券正式名称或报价币种，请检查代码后重试")
        result = {"name": name, "source": quote.get("source") or "Yahoo Finance", "source_url": quote.get("source_url") or f"https://finance.yahoo.com/quote/{symbol}/"}
    result = {**result, "market": market, "symbol": symbol, "currency": CURRENCIES[market]}
    with _lock:
        _cache[market + ":" + symbol] = (time.monotonic(), dict(result))
    return result
