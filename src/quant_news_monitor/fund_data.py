from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any


FUND_SEARCH_ENDPOINT = (
    "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
)
FUND_NAV_ENDPOINT = "https://api.fund.eastmoney.com/f10/lsjz"
FUND_SOURCE_NAME = "天天基金"
_FUND_CODE_PATTERN = re.compile(r"^\d{6}$")


def search_china_funds(
    query: str,
    *,
    timeout_seconds: int = 10,
    limit: int = 12,
) -> list[dict[str, Any]]:
    keyword = str(query or "").strip()
    if len(keyword) < 2:
        raise ValueError("请输入至少 2 个字符或完整基金代码")
    params = urllib.parse.urlencode({"m": 1, "key": keyword})
    payload = _request_json(
        f"{FUND_SEARCH_ENDPOINT}?{params}",
        timeout_seconds=timeout_seconds,
        referer="https://fund.eastmoney.com/data/fundsearch.html",
    )
    if int(payload.get("ErrCode") or 0) != 0:
        raise RuntimeError(str(payload.get("ErrMsg") or "基金检索服务返回错误"))

    results: list[dict[str, Any]] = []
    for item in payload.get("Datas") or []:
        if not isinstance(item, dict):
            continue
        base = item.get("FundBaseInfo") or {}
        code = str(base.get("FCODE") or item.get("CODE") or "").strip()
        name = str(base.get("SHORTNAME") or item.get("NAME") or "").strip()
        if not _FUND_CODE_PATTERN.fullmatch(code) or not name:
            continue
        results.append(
            {
                "code": code,
                "name": name,
                "fund_type": str(base.get("FTYPE") or "公募基金"),
                "fund_company": str(base.get("JJGS") or ""),
                "latest_nav": _finite_number(base.get("DWJZ")),
                "nav_date": str(base.get("FSRQ") or ""),
                "purchasable": str(base.get("ISBUY") or "") == "1",
                "source": FUND_SOURCE_NAME,
                "source_url": f"https://fund.eastmoney.com/{code}.html",
            }
        )
        if len(results) >= max(1, min(int(limit), 30)):
            break
    return results


def fetch_china_fund_quote(
    code: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    normalized = str(code or "").strip()
    if not _FUND_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("中国基金代码必须是 6 位数字")
    params = urllib.parse.urlencode(
        {"fundCode": normalized, "pageIndex": 1, "pageSize": 2}
    )
    payload = _request_json(
        f"{FUND_NAV_ENDPOINT}?{params}",
        timeout_seconds=timeout_seconds,
        referer=f"https://fundf10.eastmoney.com/jjjz_{normalized}.html",
    )
    if int(payload.get("ErrCode") or 0) != 0:
        raise RuntimeError(str(payload.get("ErrMsg") or "基金净值服务返回错误"))
    records = (payload.get("Data") or {}).get("LSJZList") or []
    valid = [
        item
        for item in records
        if isinstance(item, dict) and _finite_number(item.get("DWJZ")) is not None
    ]
    if not valid:
        raise RuntimeError("基金暂无可用单位净值")

    latest = valid[0]
    previous = valid[1] if len(valid) > 1 else None
    price = float(latest["DWJZ"])
    previous_close = _finite_number(previous.get("DWJZ")) if previous else None
    change_pct = (
        (price - previous_close) / previous_close * 100.0
        if previous_close and previous_close > 0
        else _finite_number(latest.get("JZZZL")) or 0.0
    )
    nav_date = str(latest.get("FSRQ") or "")
    quote_time = _nav_quote_time(nav_date)
    return {
        "symbol": normalized,
        "price": price,
        "previous_close": previous_close,
        "change_pct": change_pct,
        "quote_time": quote_time,
        "nav_date": nav_date,
        "price_kind": "confirmed_nav",
        "price_label": "已确认净值",
        "source": FUND_SOURCE_NAME,
        "source_url": f"https://fund.eastmoney.com/{normalized}.html",
    }


def fetch_china_fund_history(
    code: str,
    period: str = "3m",
    *,
    timeout_seconds: int = 12,
) -> dict[str, Any]:
    normalized = str(code or "").strip()
    normalized_period = str(period or "3m").strip().lower()
    page_counts = {"1m": 2, "3m": 4, "6m": 7, "1y": 14}
    period_days = {"1m": 31, "3m": 93, "6m": 186, "1y": 366}
    if not _FUND_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("中国基金代码必须是 6 位数字")
    if normalized_period not in page_counts:
        raise ValueError("图表周期仅支持 1m、3m、6m 或 1y")

    first_page = _fetch_fund_nav_page(normalized, 1, timeout_seconds)
    total_count = int(first_page.get("TotalCount") or 0)
    available_pages = math.ceil(total_count / 20) if total_count > 0 else page_counts[normalized_period]
    requested_pages = min(page_counts[normalized_period], available_pages)
    payloads = [first_page]
    if requested_pages > 1:
        with ThreadPoolExecutor(max_workers=min(6, requested_pages - 1)) as executor:
            futures = {
                executor.submit(
                    _fetch_fund_nav_page,
                    normalized,
                    page_index,
                    timeout_seconds,
                ): page_index
                for page_index in range(2, requested_pages + 1)
            }
            for future in as_completed(futures):
                payloads.append(future.result())

    points: list[dict[str, Any]] = []
    for payload in payloads:
        for item in (payload.get("Data") or {}).get("LSJZList") or []:
            if not isinstance(item, dict):
                continue
            nav = _finite_number(item.get("DWJZ"))
            date = str(item.get("FSRQ") or "").strip()
            try:
                timestamp = int(
                    datetime.strptime(date, "%Y-%m-%d")
                    .replace(tzinfo=timezone(timedelta(hours=8)))
                    .timestamp()
                )
            except ValueError:
                continue
            if nav is None or nav <= 0:
                continue
            points.append(
                {
                    "timestamp": timestamp,
                    "date": date,
                    "nav": nav,
                    "change_pct": _finite_number(item.get("JZZZL")),
                }
            )
    points_by_date = {item["date"]: item for item in points}
    points = sorted(points_by_date.values(), key=lambda item: item["timestamp"])
    if not points:
        raise RuntimeError("基金暂无可绘制的历史净值")
    latest_date = datetime.strptime(points[-1]["date"], "%Y-%m-%d")
    cutoff = latest_date - timedelta(days=period_days[normalized_period])
    points = [
        item
        for item in points
        if datetime.strptime(item["date"], "%Y-%m-%d") >= cutoff
    ]

    values = [item["nav"] for item in points]
    first_nav = values[0]
    latest_nav = values[-1]
    change_pct = (
        (latest_nav / first_nav - 1.0) * 100.0 if first_nav > 0 else 0.0
    )
    return {
        "kind": "nav",
        "symbol": normalized,
        "period": normalized_period,
        "interval": "trading_day",
        "currency": "CNY",
        "points": points,
        "stats": {
            "latest": latest_nav,
            "change_pct": round(change_pct, 4),
            "high": max(values),
            "low": min(values),
            "points": len(points),
        },
        "source": FUND_SOURCE_NAME,
        "source_url": f"https://fund.eastmoney.com/{normalized}.html",
        "note": "基金不提供盘中开高低收；这里展示历史确认单位净值，并非股票 K 线。",
    }


def _fetch_fund_nav_page(
    code: str,
    page_index: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {"fundCode": code, "pageIndex": page_index, "pageSize": 20}
    )
    payload = _request_json(
        f"{FUND_NAV_ENDPOINT}?{params}",
        timeout_seconds=timeout_seconds,
        referer=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
    )
    if int(payload.get("ErrCode") or 0) != 0:
        raise RuntimeError(str(payload.get("ErrMsg") or "基金净值服务返回错误"))
    return payload


def _request_json(
    url: str,
    *,
    timeout_seconds: int,
    referer: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("基金服务响应过大")
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("基金服务返回格式无效")
    return payload


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nav_quote_time(value: str) -> str | None:
    try:
        date = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    china_timezone = timezone(timedelta(hours=8))
    return date.replace(hour=23, tzinfo=china_timezone).isoformat()
