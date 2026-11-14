from __future__ import annotations

import json
import http.client
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .fund_data import fetch_china_fund_history
from .us_sessions import chart_session_at, us_quote_fields
from .news_transport import retry_delay
from zoneinfo import ZoneInfo


YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SOURCE_NAME = "Yahoo Finance"
YAHOO_CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_PROVIDER_RETRY_UNTIL: dict[str, float] = {}
_PROVIDER_LOCK = threading.Lock()
CHART_MODES = {
    "intraday": {
        "1d": ("1m", "1d"),
        "5d": ("1m", "5d"),
    },
    "daily": {
        "1m": ("1d", "1mo"),
        "3m": ("1d", "3mo"),
        "6m": ("1d", "6mo"),
        "1y": ("1d", "1y"),
    },
    "weekly": {
        "6m": ("1wk", "6mo"),
        "1y": ("1wk", "1y"),
    },
}
DEFAULT_PERIODS = {"intraday": "1d", "daily": "3m", "weekly": "1y"}
FUND_PERIODS = {
    "daily": {"1m", "3m", "6m", "1y"},
    "weekly": {"6m", "1y"},
}
INTRADAY_INTERVALS = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60}
_STOCK_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,24}$")
ASSET_CHARTS = {
    "GOLD": ("GC=F", "COMEX 黄金期货参考；不是 XAU/USD 现货，未校准或拼接现货报价。"),
    "BTC": ("BTC-USD", "Yahoo BTC/USD 参考 K 线；与主报价的 Coinbase 单一交易所来源不同。"),
    "OIL": ("CL=F", "WTI 连续期货参考；不是现货原油，也不保证对应某个交割月份。"),
    "US5Y": ("^FVX", "Cboe 五年期美债收益率指标，单位 %；不是债券价格或 DGS5 日频序列。"),
}


def normalize_chart_interval(mode: str, interval: str | None) -> str | None:
    if interval is None:
        return "1m" if mode == "intraday" else None
    if mode != "intraday":
        raise ValueError("分钟周期仅适用于分时 K 线")
    if interval in {"1s", "30s"}:
        raise ValueError("当前未接入秒级行情源，暂不提供1秒或30秒K线")
    if interval not in INTRADAY_INTERVALS:
        raise ValueError("K线周期仅支持1、5、10、15、30或60分钟")
    return interval


def resample_intraday_chart(data: dict[str, Any], market: str, interval: str | None) -> dict[str, Any]:
    """Aggregate the cached source minutes, keeping gaps and session boundaries."""
    if interval is None or interval == "1m":
        return data
    if data.get("mode") != "intraday" or data.get("interval") != "1m":
        raise ValueError("只能从真实1分钟K线合并分钟周期")
    if data.get("kind") == "price_line":
        raise ValueError("CFD来源仅提供分时采样，没有可合并的分钟开高低收数据")
    minutes = INTRADAY_INTERVALS[interval]
    zone = ZoneInfo(data.get("exchange_timezone") or {
        "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "KR": "Asia/Seoul",
        "US": "America/New_York",
    }.get(market, "UTC"))
    buckets: dict[tuple, list[dict]] = {}
    for row in sorted(data.get("candles") or [], key=lambda row: row["timestamp"]):
        local = datetime.fromtimestamp(row["timestamp"], zone)
        minute = local.hour * 60 + local.minute
        # Align to the exchange opening; never merge lunch, pre/post or dates.
        if market in {"CN", "HK"}:
            opening = 780 if minute >= 780 else 570 if minute >= 570 else 540
        elif market == "US" and data.get("instrument_type") != "FUTURE":
            opening = 960 if minute >= 960 else 570 if minute >= 570 else 240 if minute >= 240 else 0
        else:
            opening = 540 if market == "KR" else 0
        start = opening + (minute - opening) // minutes * minutes
        stamp = int(local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0).timestamp())
        buckets.setdefault((local.date(), row.get("session"), stamp), []).append(row)
    candles = []
    for (_, _, stamp), rows in buckets.items():
        volumes = [row.get("volume") for row in rows]
        candles.append({
            **rows[0], "timestamp": stamp,
            "datetime": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            "open": rows[0]["open"], "close": rows[-1]["close"],
            "high": max(row["high"] for row in rows), "low": min(row["low"] for row in rows),
            "volume": sum(volumes) if all(value is not None for value in volumes) else None,
            "source_points": len(rows), "partial": len(rows) < minutes,
        })
    result = {**data, "interval": interval, "source_interval": "1m", "candles": candles,
              "stats": {**data.get("stats", {}), "points": len(candles)},
              "technical": build_technical_analysis(candles, mode="intraday", is_fund=False)}
    result["note"] = (
        f"每根K线为{minutes}分钟，由实际1分钟K线合并；按交易时段起点对齐，跨日和午休分开。"
        "缺失分钟不补造，末根或缺口所在K线可能不完整；图上时间为周期起点。原始数据说明："
        + data.get("note", "")
    )
    return result


def chart_session_status(market: str, symbol: str, data: dict, *, now: datetime | None = None) -> str:
    if not (data.get("stats") or {}).get("latest_timestamp") or market == "FUND":
        return "unknown"
    if market == "ASSET" and symbol == "BTC":
        return "continuous"
    zones = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "KR": "Asia/Seoul", "US": "America/New_York", "ASSET": "America/New_York"}
    if market not in zones:
        return "unknown"
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zones[market]))
    minute = local.hour * 60 + local.minute
    if (market == "ASSET" and symbol in {"GOLD", "OIL"}) or (market == "US" and symbol in {"NQ=F", "NQ.CFD"}):
        closed = (local.weekday() == 5 or local.weekday() == 4 and minute >= 17 * 60
                  or local.weekday() == 6 and minute < 18 * 60 or local.weekday() < 4 and 17 * 60 <= minute < 18 * 60)
        return "closed" if closed else "unknown"
    if local.weekday() >= 5:
        return "closed"
    windows = {"CN": ((555, 690), (780, 900)), "HK": ((540, 720), (780, 970)), "KR": ((540, 930),),
               "US": ((240, 1200),) if data.get("extended_hours") else ((570, 960),)}
    # Routine hours only: never assert an open/live market without a verified holiday calendar.
    if market in windows and not any(start <= minute < end for start, end in windows[market]):
        return "closed"
    return "unknown"


def fetch_portfolio_chart(
    market: str, symbol: str, period: str | None = None, *, mode: str = "daily",
    timeout_seconds: int = 12,
) -> dict[str, Any]:
    if str(market).strip().upper() == "US" and str(symbol).strip().upper() == "NQ.CFD":
        if mode != "intraday" or str(period or "1d").strip().lower() != "1d":
            raise ValueError("纳指CFD参考当前仅提供最近交易时段分时采样，未接入同口径日K、周K或5日图")
        return _fetch_sina_nq_cfd_line(min(timeout_seconds, 6))
    if str(market).strip().upper() == "KR" and str(symbol).strip().upper() == "^KS11" and mode == "intraday" and (period or "1d") == "1d":
        return _fetch_naver_chart("KR", "^KS11", "1d", mode, min(timeout_seconds, 6))
    if str(market).strip().upper() == "CN" and str(symbol).strip().upper() == "000688.SS" and mode == "intraday":
        normalized_period = str(period or "1d").strip().lower()
        if normalized_period not in CHART_MODES["intraday"]:
            raise ValueError("该图表视图不支持所选周期")
        # Yahoo's STAR50 minute feed trails the exchange by about 15 minutes.
        # Use the existing code-validated mainland feed, including real OHLC.
        result = _fetch_backup_chart("CN", "000688.SS", normalized_period, mode, min(timeout_seconds, 6))
        result.update(symbol="000688.SS", instrument_type="INDEX", unit="points", currency=None,
                      refresh_seconds=20)
        result["stats"]["baseline_label"] = "较区间起点"
        return result
    try:
        primary_timeout = timeout_seconds if str(market).strip().upper() == "FUND" else min(timeout_seconds, 3 if str(symbol).upper() == "BTC" else 6)
        return _fetch_primary_chart(market, symbol, period, mode=mode, timeout_seconds=primary_timeout)
    except ChartDataUnavailable as primary:
        if str(market).upper() == "FUND":
            raise
        normalized_mode = _normalize_mode(mode)
        normalized_period = str(period or DEFAULT_PERIODS[normalized_mode]).lower()
        try:
            result = _fetch_backup_chart(str(market).strip().upper(), str(symbol).strip().upper(), normalized_period,
                                         normalized_mode, min(timeout_seconds, 6 if str(symbol).upper() == "BTC" else 3))
        except (ChartDataUnavailable, RuntimeError) as backup:
            if str(market).strip().upper() == "CN" and normalized_mode == "intraday":
                result = _fetch_sina_cn_minutes(str(symbol).strip().upper(), normalized_period, min(timeout_seconds, 3))
            elif str(market).strip().upper() == "US" and normalized_mode == "intraday":
                result = _fetch_sina_us_minutes(str(symbol).strip().upper(), normalized_period, min(timeout_seconds, 3))
            elif str(market).strip().upper() == "KR" or str(market).strip().upper() == "US" and normalized_mode != "intraday":
                try:
                    result = _fetch_naver_chart(str(market).strip().upper(), str(symbol).strip().upper(),
                                               normalized_period, normalized_mode, min(timeout_seconds, 3))
                except (ChartDataUnavailable, RuntimeError) as naver:
                    raise ChartDataUnavailable(f"公开行情源暂时不可用：{naver}", retry_after_seconds=60) from naver
            else:
                raise ChartDataUnavailable(
                    f"主行情源及备用源暂时不可用：{backup}",
                    retry_after_seconds=min(primary.retry_after_seconds, getattr(backup, "retry_after_seconds", 60)),
                ) from backup
        result["note"] = "主行情源暂不可用，已切换备用源。" + result["note"]
        return result


def _fetch_primary_chart(
    market: str,
    symbol: str,
    period: str | None = None,
    *,
    mode: str = "daily",
    timeout_seconds: int = 12,
) -> dict[str, Any]:
    normalized_market = str(market or "").strip().upper()
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_mode = _normalize_mode(mode)
    normalized_period = str(
        period or DEFAULT_PERIODS[normalized_mode]
    ).strip().lower()

    if normalized_market == "FUND":
        if normalized_mode == "intraday":
            raise ValueError("公募基金没有盘中分时成交行情")
        if normalized_period not in FUND_PERIODS[normalized_mode]:
            raise ValueError("基金该视图不支持所选周期")
        result = fetch_china_fund_history(
            normalized_symbol,
            normalized_period,
            timeout_seconds=timeout_seconds,
        )
        if normalized_mode == "weekly":
            result = _resample_fund_weekly(result)
        result["mode"] = normalized_mode
        result["technical"] = build_technical_analysis(
            _fund_points_as_candles(result.get("points") or []),
            mode=normalized_mode,
            is_fund=True,
        )
        result["note"] = (
            "基金按周展示每周最后一个确认净值，不是交易所周 K。"
            if normalized_mode == "weekly"
            else "基金不提供盘中开高低收；这里展示历史确认单位净值，并非股票 K 线。"
        )
        return result

    if normalized_market == "ASSET":
        if normalized_symbol not in ASSET_CHARTS:
            raise ValueError("暂不支持该资产的 K 线")
        if normalized_symbol == "BTC" and normalized_mode == "intraday":
            if normalized_period != "1d":
                raise ValueError("BTC 分钟 K 仅提供最近最多 300 根，不是完整 5 日行情")
            return fetch_btc_minute_candles(timeout_seconds=timeout_seconds)
        ticker, note = ASSET_CHARTS[normalized_symbol]
        result = fetch_stock_candles(
            ticker, normalized_period, mode=normalized_mode,
            timeout_seconds=timeout_seconds, extended_hours=False,
        )
        result["note"] = note + " " + result["note"]
        result["instrument_note"] = note
        if normalized_symbol == "US5Y":
            result["currency"] = "%"
        return result

    if normalized_market not in {"CN", "HK", "US", "KR"}:
        raise ValueError("暂不支持该市场的历史行情")
    if not _STOCK_SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("股票代码格式无效")
    return fetch_stock_candles(
        normalized_symbol,
        normalized_period,
        mode=normalized_mode,
        timeout_seconds=timeout_seconds,
        extended_hours=normalized_market == "US",
    )


def _fetch_backup_chart(market: str, symbol: str, period: str, mode: str, timeout: int) -> dict[str, Any]:
    if market == "ASSET" and symbol == "BTC":
        interval = "1w" if mode == "weekly" else CHART_MODES[mode][period][0]
        params = urllib.parse.urlencode({"symbol": "BTCUSDT", "interval": interval, "limit": 300})
        payload = _request_json("https://data-api.binance.vision/api/v3/klines?" + params, timeout_seconds=timeout)
        if not isinstance(payload, list):
            raise RuntimeError("BTC 备用源数据格式异常")
        candles = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                continue
            stamp = _finite_number(row[0])
            candle = _public_candle(stamp / 1000 if stamp else None, row[1:6], "UTC")
            if candle:
                candles.append(candle)
        if mode != "intraday":
            cutoff = time.time() - {"1m": 35, "3m": 100, "6m": 190, "1y": 370}[period] * 86400
            candles = [row for row in candles if row["timestamp"] >= cutoff]
        else:
            candles = sorted({row["timestamp"]: row for row in candles}.values(), key=lambda row: row["timestamp"])[-300:]
        return _public_chart(candles, "BTCUSDT", period, mode, "USDT", "UTC", "Binance 公开市场数据",
            "https://www.binance.com/en/trade/BTC_USDT",
            "BTC/USDT 现货；不是 Coinbase BTC/USD，USDT 存在脱锚风险，不能视为美元报价。")

    assets = {
        "GOLD": ("101.GC00Y", "USD", "America/New_York", "COMEX 黄金连续期货参考；不是现货金，也不保证与 Yahoo GC=F 使用相同交割月份。"),
        "OIL": ("102.CL00Y", "USD", "America/New_York", "NYMEX WTI 连续期货参考；不是现货油，也不保证与 Yahoo CL=F 使用相同交割月份。"),
        "US5Y": ("171.US5Y", "%", "America/New_York", "东方财富五年期美债收益率参考，单位 %；不是 Cboe FVX 指数、DGS5 日频观测或债券价格。"),
    }
    if market == "ASSET":
        secid, currency, exchange_zone, description = assets[symbol]
    elif market in {"CN", "HK", "KR"}:
        code = symbol.split(".")[0]
        if not code.isdigit():
            raise RuntimeError("备用源暂不支持此证券代码")
        if market == "CN":
            secid, currency, exchange_zone = ("1." if symbol.endswith(".SS") else "0.") + code, "CNY", "Asia/Shanghai"
        elif market == "HK":
            secid, currency, exchange_zone = "116." + code.zfill(5), "HKD", "Asia/Hong_Kong"
        else:
            secid, currency, exchange_zone = "177." + code.zfill(6), "KRW", "Asia/Seoul"
        description = "东方财富公开参考行情；K 线不复权，除权除息可能造成价格跳变。"
    elif market == "US":
        secid, currency, exchange_zone = _us_listing(symbol, timeout)["QuoteID"], "USD", "America/New_York"
        description = "东方财富美股常规时段参考；备用分钟线不含盘前盘后。K 线不复权，除权除息可能造成价格跳变。"
    else:
        raise RuntimeError("此市场尚无可用的备用图表源")

    days = {"1m": 35, "3m": 100, "6m": 190, "1y": 370, "1d": 3, "5d": 10}[period]
    params = urllib.parse.urlencode({
        "secid": secid, "klt": {"intraday": 1, "daily": 101, "weekly": 102}[mode], "fqt": 0,
        "beg": 0 if mode == "intraday" else (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d"),
        "end": "20500101", "lmt": 300,
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56",
    }, safe=",")
    payload = _request_json("https://push2his.eastmoney.com/api/qt/stock/kline/get?" + params, timeout_seconds=timeout)
    data = payload.get("data")
    expected_market, expected_code = secid.split(".")
    if payload.get("rc") != 0 or not isinstance(data, dict):
        raise RuntimeError("备用源未返回该证券或周期的数据")
    if str(data.get("code")) != expected_code or str(data.get("market")) != expected_market:
        raise RuntimeError("备用行情主体与请求代码不符")
    candles = []
    for row in data.get("klines") or []:
        if not isinstance(row, str):
            continue
        fields = row.split(",")
        if len(fields) != 6:
            continue
        try:
            # The provider displays minute timestamps in Beijing time, including overseas markets.
            local = datetime.strptime(fields[0], "%Y-%m-%d %H:%M" if mode == "intraday" else "%Y-%m-%d")
            stamp = local.replace(tzinfo=ZoneInfo("Asia/Shanghai" if mode == "intraday" else exchange_zone)).timestamp()
        except ValueError:
            continue
        candle = _public_candle(stamp, [fields[1], fields[3], fields[4], fields[2], fields[5]], exchange_zone)
        if candle:
            candles.append(candle)
    candles.sort(key=lambda row: row["timestamp"])
    if mode == "intraday" and period == "1d" and candles:
        if market == "ASSET":
            candles = [row for row in candles if row["timestamp"] > candles[-1]["timestamp"] - 86400]
        else:
            candles = [row for row in candles if row["date"] == candles[-1]["date"]]
    return _public_chart(candles, expected_code, period, mode, currency, exchange_zone, "东方财富 · 公开参考",
                         "https://quote.eastmoney.com/unify/r/" + secid, description)


def _fetch_sina_nq_cfd_line(timeout: int) -> dict[str, Any]:
    # This is the HF minute-line endpoint used by Sina's official NQ CFD page/SDK.
    # It contains price samples, not minute OHLC. Do not manufacture candles.
    payload = _request_json(
        "https://stock2.finance.sina.com.cn/futures/api/openapi.php/GlobalFuturesService.getGlobalFuturesMinLine?symbol=NQ",
        timeout_seconds=timeout)
    result = payload.get("result") or {}
    rows = (result.get("data") or {}).get("minLine_1d")
    if (result.get("status") or {}).get("code") != 0 or not isinstance(rows, list) or not rows:
        raise ChartDataUnavailable("新浪暂未返回纳指CFD分时采样")
    header = rows[0]
    if not isinstance(header, list) or len(header) != 10 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(header[0])):
        raise ChartDataUnavailable("纳指CFD分时采样格式无效")
    previous = _finite_number(header[1])
    previous = previous if previous and previous > 0 else None
    samples = {}
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != (10 if index == 0 else 6):
            continue
        value = _finite_number(row[5] if index == 0 else row[1])
        try:
            moment = datetime.strptime(str(row[-1]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            continue
        stamp = int(moment.timestamp())
        if value is None or value <= 0 or stamp <= 0 or stamp > time.time() + 2:
            continue
        samples[stamp] = {"timestamp": stamp, "datetime": moment.astimezone(timezone.utc).isoformat(),
                          "date": moment.date().isoformat(), "close": value, "session": "reference"}
    points = sorted(samples.values(), key=lambda row: row["timestamp"])
    if not points:
        raise ChartDataUnavailable("纳指CFD没有有效时间戳的分时采样")
    latest = points[-1]["close"]
    baseline = previous or points[0]["close"]
    return {"kind": "price_line", "mode": "intraday", "symbol": "NQ.CFD", "period": "1d", "interval": "1m",
            "currency": None, "unit": "points", "instrument_type": "CFD", "exchange_timezone": "Asia/Shanghai",
            "extended_hours": False, "candles": points, "session_date": points[-1]["date"], "refresh_seconds": 20,
            "stats": {"latest": latest, "previous_close": previous, "change_pct": (latest / baseline - 1) * 100,
                      "baseline_label": "较CFD来源前收" if previous else "较区间起点", "points": len(points),
                      "latest_timestamp": points[-1]["timestamp"], "high": max(p["close"] for p in points),
                      "low": min(p["close"] for p in points)},
            "technical": {}, "source": "新浪财经", "source_url": "https://finance.sina.com.cn/futures/quotes/NQ.shtml",
            "note": "纳指CFD盘外参考，不是CME交易所期货。折线为来源提供的每分钟价格采样，非分钟K线；未补造开高低收或成交量。最高最低仅为采样点极值。报价与分时采样更新频率不同，以各自原始时间为准。"}


def _us_listing(symbol: str, timeout: int) -> dict:
    params = urllib.parse.urlencode({"input": symbol, "type": 14, "count": 20})
    listing = _request_json("https://searchapi.eastmoney.com/api/suggest/get?" + params, timeout_seconds=timeout)
    matches = [row for row in (listing.get("QuotationCodeTable") or {}).get("Data") or []
               if isinstance(row, dict) and row.get("Code") == symbol
               and re.fullmatch(r"(?:105|106|107)\." + re.escape(symbol), str(row.get("QuoteID") or ""))]
    if len({row["QuoteID"] for row in matches}) != 1:
        raise RuntimeError("备用源没有唯一匹配的美股证券，未按近似名称取数")
    return matches[0]


def _fetch_sina_cn_minutes(symbol: str, period: str, timeout: int) -> dict:
    code = symbol.split(".")[0]
    if not re.fullmatch(r"[0569]\d{5}|[123]\d{5}", code):
        raise ChartDataUnavailable("该 A 股代码尚无独立分钟备用源", retry_after_seconds=60)
    source_code = ("sh" if symbol.endswith(".SS") else "sz") + code
    params = urllib.parse.urlencode({"symbol": source_code, "scale": 1, "ma": "no", "datalen": 300})
    payload = _request_json("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?" + params,
                            timeout_seconds=timeout)
    candles = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            moment = datetime.strptime(str(row.get("day")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            continue
        candle = _public_candle(moment.timestamp(), [row.get(key) for key in ("open", "high", "low", "close", "volume")], "Asia/Shanghai")
        if candle:
            candles.append(candle)
    if candles and period == "1d":
        last_day = max(row["date"] for row in candles)
        candles = [row for row in candles if row["date"] == last_day]
    return _public_chart(candles, symbol, period, "intraday", "CNY", "Asia/Shanghai", "新浪财经 · 公开参考",
        "https://finance.sina.com.cn/realstock/company/" + source_code + "/nc.shtml",
        "新浪 A 股 1 分钟 OHLC，单次最多最近 300 根，不能保证完整交易日或五日历史。")


def _fetch_sina_us_minutes(symbol: str, period: str, timeout: int) -> dict:
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", symbol):
        raise ChartDataUnavailable("该美股代码尚无独立分钟备用源", retry_after_seconds=60)
    params = urllib.parse.urlencode({"symbol": symbol, "type": 1, "___qn": 3})
    payload = _request_json("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/stockChart/US_MinKService.getMinK?" + params,
                            timeout_seconds=timeout)
    candles = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            moment = datetime.strptime(str(row.get("d")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            continue
        if moment.second or moment.weekday() >= 5 or not "09:30" <= moment.strftime("%H:%M") <= "16:00":
            continue
        candle = _public_candle(moment.timestamp(), [row.get(key) for key in ("o", "h", "l", "c", "v")], "America/New_York")
        if candle:
            candles.append(candle)
    if candles and period == "1d":
        last_day = max(row["date"] for row in candles)
        candles = [row for row in candles if row["date"] == last_day]
    return _public_chart(candles, symbol, period, "intraday", "USD", "America/New_York", "新浪财经 · 美股公开参考",
        "https://stock.finance.sina.com.cn/usstock/quotes/" + symbol + ".html",
        "新浪美股常规时段 1 分钟 OHLC，不含盘前盘后；未确认复权口径，除权除息可能造成价格跳变。")


def _fetch_naver_chart(market: str, symbol: str, period: str, mode: str, timeout: int) -> dict:
    kospi = market == "KR" and symbol == "^KS11"
    if market == "KR":
        code, section, currency, zone = "KOSPI" if kospi else symbol.split(".")[0], "domestic", "KRW", "Asia/Seoul"
    else:
        exchange = _us_listing(symbol, timeout).get("JYS")
        suffix = {"NASDAQ": ".O", "NYSE": ".N", "AMEX": ".K"}.get(exchange)
        if not suffix:
            raise RuntimeError("没有确认该美股的交易所，未猜测代码")
        code, section, currency, zone = symbol + suffix, "foreign", "USD", "America/New_York"
    params = {"periodType": "day"} if mode == "intraday" else {
        "periodType": "month", "range": {"1m": 1, "3m": 3, "6m": 6, "1y": 12}[period]}
    if kospi and mode == "intraday" and period != "1d":
        raise ChartDataUnavailable("Naver 指数分时仅提供最近交易日，未用单日代替多日行情")
    path_kind = "index" if kospi else "item"
    payload = _request_json(f"https://api.stock.naver.com/chart/{section}/{path_kind}/{code}?" + urllib.parse.urlencode(params),
                            timeout_seconds=timeout)
    if payload.get("code") != code or payload.get("periodType") != params["periodType"]:
        raise RuntimeError("Naver 行情主体或周期不符")
    candles, previous_volume = [], None
    for row in payload.get("priceInfos") or []:
        if not isinstance(row, dict):
            continue
        try:
            moment = datetime.strptime(str(row.get("localDateTime" if mode == "intraday" else "localDate")),
                                       "%Y%m%d%H%M%S" if mode == "intraday" else "%Y%m%d").replace(tzinfo=ZoneInfo(zone))
        except ValueError:
            continue
        volume = _finite_number(row.get("accumulatedTradingVolume"))
        minute_volume = volume - previous_volume if volume is not None and previous_volume is not None and volume >= previous_volume else None
        previous_volume = volume
        opening, closing = ("09:30", "16:00") if market == "US" else ("09:00", "15:30")
        if mode == "intraday" and not opening <= moment.strftime("%H:%M") <= closing:
            continue
        candle = _public_candle(moment.timestamp(), [row.get("openPrice"), row.get("highPrice"), row.get("lowPrice"),
            row.get("currentPrice" if mode == "intraday" else "closePrice"), minute_volume if mode == "intraday" else volume], zone)
        if candle:
            candles.append(candle)
    if mode == "weekly":
        weeks = {}
        for row in sorted(candles, key=lambda row: row["timestamp"]):
            day = datetime.fromisoformat(row["date"]).date()
            key = day - timedelta(days=day.weekday())
            prior = weeks.get(key)
            weeks[key] = {**row, "open": prior["open"] if prior else row["open"],
                "high": max(prior["high"], row["high"]) if prior else row["high"],
                "low": min(prior["low"], row["low"]) if prior else row["low"],
                "volume": prior["volume"] + row["volume"] if prior and prior["volume"] is not None and row["volume"] is not None else None if prior else row["volume"]}
        candles = list(weeks.values())
    result = _public_chart(candles, symbol, period, mode, currency, zone, "Naver Finance · 公开参考",
        f"https://m.stock.naver.com/{section}/stock/{code}/total",
        "Naver 公开参考 K 线；非实时授权。" + ("周 K 由真实日 K 聚合，首尾周可能未完整。" if mode == "weekly" else ""))
    if kospi:
        result.update(source="Naver Finance", source_url="https://m.stock.naver.com/domestic/index/KOSPI/total",
                      session_date=result["candles"][-1]["date"], refresh_seconds=10 if mode == "intraday" else 60)
        result["instrument_note"] = "韩国综合指数点位；Naver 原始 OHLC。"
        result["note"] = "Naver 原始 K 线；时间按首尔交易日，分时每 10 秒检查更新；当前分钟尚未收完。"
        previous = _finite_number(payload.get("lastClosePrice"))
        if mode == "intraday" and previous and previous > 0:
            result["stats"].update(previous_close=previous, change_pct=(result["stats"]["latest"] / previous - 1) * 100)
    return result


def _public_candle(stamp: Any, values: list, exchange_zone: str) -> dict[str, Any] | None:
    stamp = _finite_number(stamp)
    opening, high, low, close, volume = [_finite_number(value) for value in values]
    if stamp is None or not 0 < stamp <= time.time() + 60 or None in (opening, high, low, close):
        return None
    if min(opening, high, low, close) <= 0 or high < max(opening, low, close) or low > min(opening, close):
        return None
    moment = datetime.fromtimestamp(stamp, timezone.utc)
    return {"timestamp": int(stamp), "datetime": moment.isoformat(),
            "date": moment.astimezone(ZoneInfo(exchange_zone)).date().isoformat(), "session": "regular",
            "open": opening, "high": high, "low": low, "close": close, "volume": max(0, volume) if volume is not None else None}


def _public_chart(candles: list[dict], symbol: str, period: str, mode: str, currency: str,
                  exchange_zone: str, source: str, source_url: str, description: str) -> dict[str, Any]:
    candles = sorted({row["timestamp"]: row for row in candles}.values(), key=lambda row: row["timestamp"])
    if not candles:
        raise RuntimeError("备用源暂无有效的开高低收数据，未补造行情")
    latest, baseline = candles[-1]["close"], candles[0]["close"]
    as_of = candles[-1]["datetime"] if mode == "intraday" else candles[-1]["date"]
    coverage = f"实际数据截至 {as_of}，共 {len(candles)} 根；不保证覆盖完整所选区间。"
    return {"kind": "intraday" if mode == "intraday" else "candlestick", "mode": mode, "symbol": symbol,
        "period": period, "interval": CHART_MODES[mode][period][0], "currency": currency,
        "exchange_timezone": exchange_zone, "extended_hours": False, "candles": candles,
        "source": source, "source_url": source_url, "instrument_note": description,
        "stats": {"latest": latest, "change_pct": (latest / baseline - 1) * 100,
            "high": max(row["high"] for row in candles), "low": min(row["low"] for row in candles),
            "points": len(candles), "previous_close": None, "latest_timestamp": candles[-1]["timestamp"]},
        "technical": build_technical_analysis(candles, mode=mode, is_fund=False), "refresh_seconds": 60,
        "note": description + " " + coverage + " 涨跌幅相对区间首根收盘价，不是今日涨跌幅；公开参考，不保证实时。"}


def fetch_btc_minute_candles(*, timeout_seconds: int = 12) -> dict[str, Any]:
    payload = _request_json(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60",
        timeout_seconds=timeout_seconds,
    )
    candles = {}
    for row in payload:
        if not isinstance(row, list) or len(row) != 6:
            continue
        stamp, low, high, opening, close, volume = [_finite_number(value) for value in row]
        if None in (stamp, low, high, opening, close) or min(low, high, opening, close) <= 0:
            continue
        if stamp <= 0 or stamp > time.time() + 60 or stamp % 60:
            continue
        if high < max(opening, low, close) or low > min(opening, close):
            continue
        moment = datetime.fromtimestamp(stamp, timezone.utc)
        candles[int(stamp)] = {
            "timestamp": int(stamp), "datetime": moment.isoformat(), "date": moment.date().isoformat(),
            "session": "regular", "open": opening, "high": high, "low": low, "close": close,
            "volume": max(0.0, volume or 0.0),
        }
    rows = sorted(candles.values(), key=lambda candle: candle["timestamp"])[-300:]
    if not rows:
        raise ChartDataUnavailable("BTC 暂无可绘制的分钟 K 线")
    return {
        "kind": "intraday", "mode": "intraday", "symbol": "BTC-USD", "period": "1d",
        "interval": "1m", "currency": "USD", "exchange_timezone": "UTC", "extended_hours": False,
        "candles": rows, "source": "Coinbase Exchange",
        "instrument_note": "Coinbase BTC/USD 现货成交桶；单一交易所美元报价。",
        "source_url": "https://www.coinbase.com/advanced-trade/spot/BTC-USD",
        "stats": {"latest": rows[-1]["close"], "change_pct": (rows[-1]["close"] / rows[0]["close"] - 1) * 100,
                  "high": max(row["high"] for row in rows), "low": min(row["low"] for row in rows),
                  "points": len(rows), "previous_close": None, "latest_timestamp": rows[-1]["timestamp"]},
        "technical": build_technical_analysis(rows, mode="intraday", is_fund=False),
        "note": "Coinbase BTC/USD 真实 1 分钟 K；最近最多 300 根，非完整一天。涨跌幅相对区间首根收盘价；缺失分钟不补造，当前分钟可能尚未收完。窗口每 60 秒检查更新，不保证实时。",
        "refresh_seconds": 60,
    }


def fetch_stock_candles(
    symbol: str,
    period: str | None = None,
    *,
    mode: str = "daily",
    timeout_seconds: int = 12,
    extended_hours: bool = True,
) -> dict[str, Any]:
    from .index_monitor import INDEX_CATALOG

    normalized_symbol = str(symbol or "").strip().upper()
    index_definition = next((item for item in INDEX_CATALOG if item["symbol"] == normalized_symbol), None)
    is_index = index_definition is not None
    extended_hours = extended_hours and not is_index
    normalized_mode = _normalize_mode(mode)
    normalized_period = str(
        period or DEFAULT_PERIODS[normalized_mode]
    ).strip().lower()
    if not _STOCK_SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("股票代码格式无效")
    if normalized_period not in CHART_MODES[normalized_mode]:
        raise ValueError("该图表视图不支持所选周期")

    interval, range_name = CHART_MODES[normalized_mode][normalized_period]
    encoded_symbol = urllib.parse.quote(normalized_symbol, safe="")
    used_history_window = False
    ranges = (range_name, "5d") if normalized_mode == "intraday" and normalized_period == "1d" else (range_name,)
    latest_us_session = is_index and index_definition["market"] == "US" and normalized_mode == "intraday" and normalized_period == "1d"
    if latest_us_session:
        # US closures can return timestamps with null prices for 1d. Select the last real session from 5d.
        ranges, used_history_window = ("5d",), True
    for requested_range in ranges:
        params = urllib.parse.urlencode({
            "interval": interval,
            "range": requested_range,
            "events": "div,splits",
            "includePrePost": "true" if normalized_mode == "intraday" and extended_hours else "false",
        })
        payload = _request_json(f"{YAHOO_CHART_ENDPOINT}/{encoded_symbol}?{params}", timeout_seconds=timeout_seconds)
        chart = payload.get("chart") or {}
        if chart.get("error"):
            description = (chart.get("error") or {}).get("description")
            raise ChartDataUnavailable(str(description or f"{normalized_symbol} 历史行情不可用"))
        result = (chart.get("result") or [None])[0]
        if not isinstance(result, dict):
            raise ChartDataUnavailable(f"{normalized_symbol} 暂无历史行情")
        meta = result.get("meta") or {}
        if is_index and (meta.get("symbol") != normalized_symbol or meta.get("instrumentType") != "INDEX"):
            raise RuntimeError("指数行情主体不符，未使用股票或 ETF 代替")
        if meta.get("symbol") and str(meta["symbol"]).upper() != normalized_symbol:
            raise RuntimeError("行情主体与请求代码不符")
        reference_type = {"SOXX": "ETF", "NQ=F": "FUTURE"}.get(normalized_symbol)
        if reference_type and (meta.get("symbol") != normalized_symbol or meta.get("instrumentType") != reference_type):
            raise RuntimeError("盘外参考行情主体或品种类型不符")
        if meta.get("dataGranularity") and meta["dataGranularity"] != interval:
            raise RuntimeError("来源返回的 K 线周期与请求不符")
        if (result.get("timestamp") or requested_range != "1d"
                or str(meta.get("symbol", "")).upper() != normalized_symbol or meta.get("dataGranularity") != interval):
            break
        # An empty one-day window can occur during a closure. A rate/access error never reaches this retry.
        used_history_window = True
    is_us_extended = (extended_hours and normalized_mode == "intraday"
                      and meta.get("instrumentType") in {"EQUITY", "ETF"}
                      and meta.get("exchangeTimezoneName") == "America/New_York")

    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    adjusted = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    volumes = quote.get("volume") or []
    candles: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        if index >= min(len(opens), len(highs), len(lows), len(closes)):
            break
        open_value = _finite_number(opens[index])
        high_value = _finite_number(highs[index])
        low_value = _finite_number(lows[index])
        close_value = _finite_number(closes[index])
        if None in {open_value, high_value, low_value, close_value}:
            continue
        if min(open_value, high_value, low_value, close_value) <= 0:
            continue
        if high_value < max(open_value, low_value, close_value) or low_value > min(open_value, close_value):
            continue
        stamp = _finite_number(timestamp)
        if stamp is None or stamp <= 0 or stamp > time.time() + 60:
            continue
        volume = _finite_number(volumes[index]) if index < len(volumes) else None
        moment = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        candles.append(
            {
                "timestamp": int(stamp),
                "datetime": moment.isoformat(),
                "date": moment.astimezone(ZoneInfo("America/New_York")).date().isoformat() if is_us_extended or latest_us_session else moment.date().isoformat(),
                "session": chart_session_at(meta, int(stamp)) if is_us_extended else "regular",
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "adjusted_close": _finite_number(adjusted[index]) if normalized_mode == "daily" and index < len(adjusted) else None,
                "volume": volume if volume is not None and volume >= 0 else None,
            }
        )
    if not candles:
        raise ChartDataUnavailable(f"{normalized_symbol} 暂无可绘制的历史行情")
    candles.sort(key=lambda item: item["timestamp"])
    if normalized_mode == "intraday":
        # Yahoo can append a last-trade update inside the final minute bucket.
        minutes: dict[int, dict[str, Any]] = {}
        for candle in candles:
            bucket = candle["timestamp"] // 60 * 60
            previous = minutes.get(bucket)
            minutes[bucket] = {
                **candle,
                "timestamp": bucket,
                "datetime": datetime.fromtimestamp(bucket, timezone.utc).isoformat(),
                "open": previous["open"] if previous else candle["open"],
                "high": max(previous["high"], candle["high"]) if previous else candle["high"],
                "low": min(previous["low"], candle["low"]) if previous else candle["low"],
                "volume": max((v for v in (previous["volume"] if previous else None, candle["volume"]) if v is not None), default=None),
            }
        candles = list(minutes.values())

    history_previous_close = None
    if used_history_window:
        if meta.get("instrumentType") == "FUTURE":
            candles = [row for row in candles if row["timestamp"] > candles[-1]["timestamp"] - 86400]
        else:
            last_day = candles[-1]["date"]
            if latest_us_session:
                prior = [row for row in candles if row["date"] < last_day]
                history_previous_close = prior[-1]["close"] if prior else None
                quote_stamp, official_close = _finite_number(meta.get("regularMarketTime")), _finite_number(meta.get("previousClose"))
                if quote_stamp and official_close and official_close > 0 and datetime.fromtimestamp(quote_stamp, ZoneInfo("America/New_York")).date().isoformat() == last_day:
                    history_previous_close = official_close
            candles = [row for row in candles if row["date"] == last_day]

    closes_in_period = [item["close"] for item in candles]
    highs_in_period = [item["high"] for item in candles]
    lows_in_period = [item["low"] for item in candles]
    meta = result.get("meta") or {}
    previous_close = _finite_number(
        (meta.get("previousClose") or meta.get("chartPreviousClose")) if normalized_symbol == "NQ=F"
        else (meta.get("chartPreviousClose") or meta.get("previousClose"))
    ) if not used_history_window else history_previous_close
    session_fields = us_quote_fields(result) if is_us_extended and normalized_period == "1d" and not used_history_window else {}
    if session_fields.get("previous_close"):
        previous_close = session_fields["previous_close"]
    baseline_label = "较来源前收" if previous_close else "较区间起点"
    if (normalized_symbol == "SOXX" and session_fields.get("quote_session") in {"pre", "post"}
            and session_fields.get("extended_change_pct") is not None and session_fields.get("regular_price")):
        previous_close = session_fields["regular_price"]
        baseline_label = "较常规收盘"
    baseline = (
        previous_close
        if normalized_mode == "intraday"
        and normalized_period == "1d"
        and previous_close
        else closes_in_period[0]
    )
    if normalized_mode != "intraday" or normalized_period != "1d":
        baseline_label = "较区间起点"
    latest_close = closes_in_period[-1]
    change_pct = (
        (latest_close / baseline - 1.0) * 100.0 if baseline and baseline > 0 else 0.0
    )
    # Daily candle timestamps denote the session's opening, not the latest trade.
    # Consumers such as close reviews need the provider's quote clock separately.
    quote_stamp = _finite_number(meta.get("regularMarketTime"))
    try:
        source_quote_at = datetime.fromtimestamp(quote_stamp, timezone.utc).isoformat() if quote_stamp is not None else None
    except (OverflowError, OSError, ValueError):
        source_quote_at = None
    return {
        "kind": "intraday" if normalized_mode == "intraday" else "candlestick",
        "mode": normalized_mode,
        "symbol": normalized_symbol,
        "period": normalized_period,
        "interval": interval,
        "currency": str(meta.get("currency") or ""),
        "instrument_type": str(meta.get("instrumentType") or ""),
        "unit": "points" if is_index or normalized_symbol == "NQ=F" else str(meta.get("currency") or ""),
        "exchange_timezone": str(meta.get("exchangeTimezoneName") or ""),
        "extended_hours": is_us_extended,
        "latest_session": candles[-1].get("session"),
        "session_date": candles[-1]["date"],
        "candles": candles,
        "stats": {
            "latest": latest_close,
            "change_pct": round(change_pct, 4),
            "high": max(highs_in_period),
            "low": min(lows_in_period),
            "points": len(candles),
            "previous_close": previous_close,
            "baseline_label": baseline_label,
            "latest_timestamp": candles[-1]["timestamp"],
        },
        "technical": build_technical_analysis(
            candles,
            mode=normalized_mode,
            is_fund=False,
        ),
        "source": YAHOO_SOURCE_NAME,
        "source_quote_at": source_quote_at,
        "source_url": f"https://finance.yahoo.com/quote/{encoded_symbol}/chart/",
        "note": ("纳指100期货参考，报价源约延迟10分钟；不等同于纳斯达克综合指数。" if normalized_symbol == "NQ=F" else "") + ("一日区间为空，按相同一分钟粒度回溯历史，仅展示最近有效交易日或期货最近 24 小时片段；涨跌幅相对本段首根收盘价，不是今日涨跌幅。" if used_history_window else "") + (
            "每根 K 线为 1 分钟，含美股盘前、常规及盘后；公开参考行情，不保证实时。窗口打开时每 20 秒检查更新。"
            if is_us_extended else "每根 K 线为 1 分钟，缺失分钟不补造；公开参考行情，不保证实时。窗口打开时每 20 秒检查更新。"
            if normalized_mode == "intraday"
            else "指数显示开、高、低、收点位；成交量按来源披露。" if is_index
            else "股票显示真实开、高、低、收与成交量；技术提示只基于量价指标。"
        ),
    }


def build_technical_analysis(
    candles: list[dict[str, Any]],
    *,
    mode: str,
    is_fund: bool,
) -> dict[str, Any]:
    if not candles:
        return _empty_technical_analysis(mode)
    closes = [float(item["close"]) for item in candles]
    highs = [float(item.get("high") or item["close"]) for item in candles]
    lows = [float(item.get("low") or item["close"]) for item in candles]
    volumes = [float(item.get("volume") or 0.0) for item in candles]
    latest = closes[-1]
    ma5_series = _sma_series(closes, 5)
    ma10_series = _sma_series(closes, 10)
    ma20_series = _sma_series(closes, 20)
    ma60_series = _sma_series(closes, 60)
    ma5 = ma5_series[-1]
    ma10 = ma10_series[-1]
    ma20 = ma20_series[-1]
    ma60 = ma60_series[-1]
    rsi_series = _rsi_series(closes, 14)
    rsi14 = rsi_series[-1]
    macd_series, signal_series, histogram_series = _macd_series(closes)
    macd, macd_signal, macd_histogram = macd_series[-1], signal_series[-1], histogram_series[-1]
    bollinger = _bollinger(closes, 20)
    volume_ratio = _volume_ratio(volumes)
    vwap_series = _vwap_series(candles) if mode == "intraday" and not is_fund else []
    vwap = vwap_series[-1] if vwap_series else None
    lookback = min(20, len(candles))
    support = min(lows[-lookback:])
    resistance = max(highs[-lookback:])
    range_span = max(resistance - support, abs(latest) * 0.001)
    range_position = (latest - support) / range_span

    score = 0
    trend_score = 0
    if ma20 is not None:
        trend_score += 18 if latest >= ma20 else -18
    if ma5 is not None and ma20 is not None:
        trend_score += 14 if ma5 >= ma20 else -14
    elif ma5 is not None:
        trend_score += 8 if latest >= ma5 else -8
    score += trend_score

    momentum_score = 0
    if rsi14 is not None:
        if rsi14 >= 55:
            momentum_score += 10
        elif rsi14 <= 45:
            momentum_score -= 10
    if macd_histogram is not None:
        momentum_score += 13 if macd_histogram >= 0 else -13
    score += momentum_score

    position_score = 0
    if bollinger:
        position_score += 8 if latest >= bollinger["middle"] else -8
    if range_position >= 0.7:
        position_score += 5
    elif range_position <= 0.3:
        position_score -= 5
    score += position_score

    volume_score = 0
    if vwap is not None:
        volume_score += 14 if latest >= vwap else -14
    if volume_ratio is not None and volume_ratio >= 1.2 and len(closes) >= 2:
        volume_score += 8 if closes[-1] >= closes[-2] else -8
    score += volume_score
    score = int(max(-100, min(100, round(score))))
    stance = "偏多" if score >= 20 else "偏空" if score <= -20 else "中性"
    confidence = (
        "低"
        if len(closes) < 20
        else "高"
        if len(closes) >= 60 and abs(score) >= 40
        else "中"
    )

    cross_note = ""
    if len(ma5_series) >= 2 and len(ma20_series) >= 2:
        previous_ma5 = ma5_series[-2]
        previous_ma20 = ma20_series[-2]
        if None not in {previous_ma5, previous_ma20, ma5, ma20}:
            if ma5 >= ma20 and previous_ma5 < previous_ma20:
                cross_note = "，刚形成短期金叉"
            elif ma5 < ma20 and previous_ma5 >= previous_ma20:
                cross_note = "，刚形成短期死叉"

    trend_tone = "positive" if trend_score > 0 else "negative" if trend_score < 0 else "neutral"
    if ma5 is not None and ma20 is not None:
        trend_text = (
            f"最新值位于 MA20 {'上方' if latest >= ma20 else '下方'}，"
            f"MA5 {'高于' if ma5 >= ma20 else '低于'} MA20{cross_note}。"
        )
    elif ma5 is not None:
        trend_text = f"最新值位于 MA5 {'上方' if latest >= ma5 else '下方'}，长周期样本仍不足。"
    else:
        trend_text = "当前样本不足 5 根，均线趋势暂不可判定。"

    momentum_tone = "positive" if momentum_score > 0 else "negative" if momentum_score < 0 else "neutral"
    momentum_parts = []
    if rsi14 is not None:
        zone = "偏热" if rsi14 >= 70 else "偏弱" if rsi14 <= 30 else "强势" if rsi14 >= 55 else "弱势" if rsi14 <= 45 else "中性"
        momentum_parts.append(f"RSI14 为 {rsi14:.1f}，处于{zone}区")
    if macd_histogram is not None:
        momentum_parts.append(f"MACD 柱{'为正' if macd_histogram >= 0 else '为负'}")
    momentum_text = "；".join(momentum_parts) + "。" if momentum_parts else "动量指标样本不足。"

    if is_fund:
        volume_tone = "neutral"
        volume_text = "基金确认净值不含盘中成交量，量能与 VWAP 信号不可用。"
    elif mode == "intraday" and vwap is not None:
        distance = (latest / vwap - 1.0) * 100.0
        volume_tone = "positive" if distance >= 0 else "negative"
        ratio_text = f"，当前量比约 {volume_ratio:.2f} 倍" if volume_ratio is not None else ""
        volume_text = f"价格位于 VWAP {'上方' if distance >= 0 else '下方'} {abs(distance):.2f}%{ratio_text}。"
    elif volume_ratio is not None:
        volume_tone = "positive" if volume_ratio >= 1.2 and closes[-1] >= closes[-2] else "negative" if volume_ratio >= 1.2 else "neutral"
        volume_text = f"最新一根成交量约为此前均量的 {volume_ratio:.2f} 倍，{'量能明显放大' if volume_ratio >= 1.2 else '量能未明显放大'}。"
    else:
        volume_tone = "neutral"
        volume_text = "可用成交量样本不足，暂不做量价确认。"

    position_tone = "positive" if range_position >= 0.65 else "negative" if range_position <= 0.35 else "neutral"
    position_zone = "接近区间高位" if range_position >= 0.8 else "接近区间低位" if range_position <= 0.2 else "位于区间中部"
    position_text = f"近 {lookback} 根支撑约 {support:.4f}，阻力约 {resistance:.4f}；当前{position_zone}。"

    risks = []
    if len(closes) < 26:
        risks.append("样本少于 26 根，MACD 与中期趋势置信度有限")
    if rsi14 is not None and rsi14 >= 70:
        risks.append("RSI 进入偏热区，追涨需防短线回撤")
    elif rsi14 is not None and rsi14 <= 30:
        risks.append("RSI 进入超卖区，但弱势可能继续延伸")
    if range_position >= 0.9:
        risks.append("价格贴近近期阻力，关注放量突破是否成立")
    elif range_position <= 0.1:
        risks.append("价格贴近近期支撑，需防有效跌破")
    if mode == "intraday":
        risks.append("分时信号噪声较高，应等待收盘级别确认")
    elif mode == "weekly":
        risks.append("周线信号更稳但更滞后，不适合精确择时")
    if is_fund:
        risks.append("基金净值通常滞后确认，不能代表盘中可成交价格")
    if not risks:
        risks.append("当前未出现极端指标，但技术信号仍可能被消息面快速逆转")

    mode_label = "分钟 K" if mode == "intraday" else "周线" if mode == "weekly" else "日线"
    agreement = "趋势与动量方向一致" if trend_score * momentum_score > 0 else "趋势与动量尚未完全一致"
    headline = f"{mode_label}技术面{stance}，{agreement}"
    indicators = [
        _indicator("MA5", ma5, "price", "positive" if ma5 is not None and latest >= ma5 else "negative"),
        _indicator("MA20", ma20, "price", "positive" if ma20 is not None and latest >= ma20 else "negative"),
        _indicator("RSI14", rsi14, "number", "negative" if rsi14 is not None and (rsi14 >= 70 or rsi14 <= 30) else "neutral"),
        _indicator("MACD 柱", macd_histogram, "signed", "positive" if macd_histogram is not None and macd_histogram >= 0 else "negative"),
    ]
    if mode == "intraday" and not is_fund:
        indicators.append(_indicator("VWAP", vwap, "price", "positive" if vwap is not None and latest >= vwap else "negative"))
    elif bollinger:
        indicators.append(_indicator("BOLL 中轨", bollinger["middle"], "price", "positive" if latest >= bollinger["middle"] else "negative"))
    if not is_fund:
        indicators.append(_indicator("量比", volume_ratio, "ratio", volume_tone))
    indicators = [item for item in indicators if item["value"] is not None]

    overlays = [
        {"key": "ma5", "label": "MA5", "values": _rounded_series(ma5_series)},
        {"key": "ma20", "label": "MA20", "values": _rounded_series(ma20_series)},
    ]
    if vwap_series:
        overlays.append(
            {"key": "vwap", "label": "VWAP", "values": _rounded_series(vwap_series)}
        )
    return {
        "stance": stance,
        "score": score,
        "confidence": confidence,
        "headline": headline,
        "summary": f"技术评分 {score:+d}/100，{agreement}。该结果只反映当前量价结构。",
        "signals": [
            {"label": "趋势", "tone": trend_tone, "text": trend_text},
            {"label": "动量", "tone": momentum_tone, "text": momentum_text},
            {"label": "量能", "tone": volume_tone, "text": volume_text},
            {"label": "位置", "tone": position_tone, "text": position_text},
            {"label": "风险", "tone": "warning", "text": "；".join(risks) + "。"},
        ],
        "indicators": indicators,
        "overlays": overlays,
        "oscillators": {"rsi14": _rounded_series(rsi_series), "dif": _rounded_series(macd_series),
                        "dea": _rounded_series(signal_series), "histogram": _rounded_series(histogram_series)},
        "levels": {
            "support": _rounded(support),
            "resistance": _rounded(resistance),
            "ma5": _rounded(ma5),
            "ma10": _rounded(ma10),
            "ma20": _rounded(ma20),
            "ma60": _rounded(ma60),
            "rsi14": _rounded(rsi14),
            "macd": _rounded(macd),
            "macd_signal": _rounded(macd_signal),
            "macd_histogram": _rounded(macd_histogram),
            "vwap": _rounded(vwap),
            "volume_ratio": _rounded(volume_ratio),
            "bollinger": {key: _rounded(value) for key, value in (bollinger or {}).items()},
        },
        "disclaimer": "技术指标用于观察趋势和风险，不构成买卖建议；请与基本面、消息面及仓位纪律交叉验证。",
    }


def _resample_fund_weekly(result: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    for point in result.get("points") or []:
        date = datetime.strptime(str(point["date"]), "%Y-%m-%d")
        year, week, _ = date.isocalendar()
        buckets[(year, week)] = point
    points = sorted(buckets.values(), key=lambda item: int(item["timestamp"]))
    if not points:
        raise RuntimeError("基金暂无可绘制的周净值")
    values = [float(item["nav"]) for item in points]
    first = values[0]
    latest = values[-1]
    result = dict(result)
    result["points"] = points
    result["interval"] = "1wk"
    result["stats"] = {
        "latest": latest,
        "change_pct": round((latest / first - 1.0) * 100.0, 4) if first > 0 else 0.0,
        "high": max(values),
        "low": min(values),
        "points": len(points),
        "latest_timestamp": points[-1]["timestamp"],
    }
    return result


def _fund_points_as_candles(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": item["timestamp"],
            "date": item["date"],
            "open": item["nav"],
            "high": item["nav"],
            "low": item["nav"],
            "close": item["nav"],
            "volume": 0.0,
        }
        for item in points
    ]


def _sma_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        result.append(running / period if index >= period - 1 else None)
    return result


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int) -> float | None:
    return _rsi_series(values, period)[-1] if values else None


def _rsi_series(values: list[float], period: int) -> list[float | None]:
    result = [None] * len(values)
    if len(values) <= period:
        return result
    gains = []
    losses = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    def value():
        return (100.0 if average_gain > 0 else 50.0) if average_loss == 0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    result[period] = value()
    for index, (gain, loss) in enumerate(zip(gains[period:], losses[period:]), period + 1):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = value()
    return result


def _macd_series(values: list[float]) -> tuple[list, list, list]:
    if len(values) < 26:
        return ([None] * len(values), [None] * len(values), [None] * len(values))
    fast = _ema_series(values, 12)
    slow = _ema_series(values, 26)
    line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    signal = _ema_series(line, 9)
    return tuple([None if i < 25 else value for i, value in enumerate(row)]
                 for row in (line, signal, [a - b for a, b in zip(line, signal)]))


def _bollinger(values: list[float], period: int) -> dict[str, float] | None:
    if len(values) < period:
        return None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((value - middle) ** 2 for value in window) / period
    deviation = math.sqrt(variance)
    return {
        "upper": middle + 2.0 * deviation,
        "middle": middle,
        "lower": middle - 2.0 * deviation,
    }


def _volume_ratio(volumes: list[float]) -> float | None:
    if len(volumes) < 6 or volumes[-1] <= 0:
        return None
    history = [value for value in volumes[max(0, len(volumes) - 21) : -1] if value > 0]
    if len(history) < 5:
        return None
    average = sum(history) / len(history)
    return volumes[-1] / average if average > 0 else None


def _vwap_series(candles: list[dict[str, Any]]) -> list[float | None]:
    result: list[float | None] = []
    cumulative_value = 0.0
    cumulative_volume = 0.0
    previous_date = None
    for item in candles:
        date = item.get("date")
        if date and date != previous_date:
            cumulative_value = cumulative_volume = 0.0
        previous_date = date or previous_date
        volume = float(item.get("volume") or 0.0)
        typical = (
            float(item.get("high") or item["close"])
            + float(item.get("low") or item["close"])
            + float(item["close"])
        ) / 3.0
        cumulative_value += typical * volume
        cumulative_volume += volume
        result.append(
            cumulative_value / cumulative_volume if cumulative_volume > 0 else None
        )
    return result


def _indicator(
    label: str,
    value: float | None,
    value_format: str,
    tone: str,
) -> dict[str, Any]:
    return {
        "label": label,
        "value": _rounded(value),
        "format": value_format,
        "tone": tone if value is not None else "neutral",
    }


def _empty_technical_analysis(mode: str) -> dict[str, Any]:
    return {
        "stance": "中性",
        "score": 0,
        "confidence": "低",
        "headline": "技术指标样本不足",
        "summary": "等待更多行情数据。",
        "signals": [],
        "indicators": [],
        "overlays": [],
        "levels": {},
        "disclaimer": "技术指标用于观察趋势和风险，不构成买卖建议。",
        "mode": mode,
    }


def _rounded(value: float | None) -> float | None:
    return round(value, 8) if value is not None and math.isfinite(value) else None


def _rounded_series(values: list[float | None]) -> list[float | None]:
    return [_rounded(value) for value in values]


def _normalize_mode(value: str) -> str:
    normalized = str(value or "daily").strip().lower()
    if normalized not in CHART_MODES:
        raise ValueError("图表类型仅支持 intraday、daily 或 weekly")
    return normalized


class ChartDataUnavailable(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: int = 20):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _request_json(url: str, *, timeout_seconds: int) -> dict[str, Any] | list:
    parts = urllib.parse.urlsplit(url)
    coinbase = parts.hostname == "api.exchange.coinbase.com" and parts.path == "/products/BTC-USD/candles"
    yahoo = parts.hostname in YAHOO_CHART_HOSTS and parts.path.startswith("/v8/finance/chart/")
    sina_us = (parts.hostname == "stock.finance.sina.com.cn"
               and parts.path == "/usstock/api/jsonp_v2.php/stockChart/US_MinKService.getMinK")
    if sina_us:
        params = urllib.parse.parse_qs(parts.query)
        if (set(params) != {"symbol", "type", "___qn"} or params.get("type") != ["1"] or params.get("___qn") != ["3"]
                or len(params.get("symbol", [])) != 1 or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", params["symbol"][0])):
            raise ValueError("美股分钟行情请求参数无效")
    backup = (parts.hostname, parts.path) in {
        ("push2his.eastmoney.com", "/api/qt/stock/kline/get"),
        ("searchapi.eastmoney.com", "/api/suggest/get"),
        ("data-api.binance.vision", "/api/v3/klines"),
        ("quotes.sina.cn", "/cn/api/json_v2.php/CN_MarketDataService.getKLineData"),
        ("polling.finance.naver.com", "/api/realtime/domestic/index/KOSPI"),
        ("api.stock.naver.com", "/chart/domestic/index/KOSPI"),
    } or (parts.hostname == "api.stock.naver.com" and re.fullmatch(r"/chart/(?:domestic|foreign)/item/[A-Z0-9.]{1,24}", parts.path))
    backup = backup or (parts.hostname == "stock2.finance.sina.com.cn"
                        and parts.path == "/futures/api/openapi.php/GlobalFuturesService.getGlobalFuturesMinLine"
                        and parts.query == "symbol=NQ")
    if (parts.scheme != "https" or not (coinbase or yahoo or backup or sina_us) or parts.username or parts.port):
        raise ValueError("历史行情请求地址无效")
    provider = "yahoo" if yahoo else parts.hostname
    with _PROVIDER_LOCK:
        wait = _PROVIDER_RETRY_UNTIL.get(provider, 0) - time.monotonic()
    if wait > 0:
        raise ChartDataUnavailable("行情源正在限流冷却，暂不重复请求。", retry_after_seconds=math.ceil(wait))
    timeout = max(0.1, float(timeout_seconds))
    deadline = time.monotonic() + min(24.0, timeout * 2)
    last_error: Exception | None = None
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Keep query parameters and TLS verification intact when changing Yahoo nodes.
        host = parts.hostname if not yahoo else YAHOO_CHART_HOSTS[(YAHOO_CHART_HOSTS.index(parts.hostname) + attempt) % 2]
        request = urllib.request.Request(
            urllib.parse.urlunsplit(parts._replace(netloc=host)),
            headers={"Accept": "application/json, text/plain, */*",
                     "User-Agent": "Mozilla/5.0 QuantNewsMonitor/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=min(timeout, remaining)) as response:
                raw = response.read(3_000_001)
            if len(raw) > 3_000_000:
                raise ValueError("历史行情响应过大")
            decoded = raw.decode("utf-8-sig")
            if sina_us:
                # Strip only the provider's known JSONP envelope; never execute response scripts.
                envelope = re.fullmatch(r"\s*(?:/\*<script>location.href='//sina.com';</script>\*/\s*)?stockChart\((.+)\);?\s*", decoded, re.DOTALL)
                if not envelope:
                    raise ChartDataUnavailable("美股分钟行情返回格式无效")
                decoded = envelope[1]
            payload = json.loads(decoded)
            if not isinstance(payload, list if coinbase or sina_us or parts.hostname in {"data-api.binance.vision", "quotes.sina.cn"} else dict):
                raise ChartDataUnavailable("历史行情返回格式无效")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {401, 403, 429} or (exc.code == 503 and (exc.headers or {}).get("Retry-After")):
                delay = int(retry_delay(exc.headers, fallback=300))
                with _PROVIDER_LOCK:
                    _PROVIDER_RETRY_UNTIL[provider] = time.monotonic() + delay
                message = "行情源请求受限" if exc.code in {401, 403, 429} else "行情源暂时繁忙"
                exc.close()
                raise ChartDataUnavailable(f"{message}，约 {delay} 秒后可重试。", retry_after_seconds=delay) from exc
            exc.close()
            if exc.code not in {500, 502, 503, 504}:
                raise ChartDataUnavailable(f"行情源暂未提供该标的或周期的数据（HTTP {exc.code}）。", retry_after_seconds=60) from exc
        except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = exc
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ChartDataUnavailable("行情源证书验证失败，已停止请求；请检查系统时间或网络证书配置。", retry_after_seconds=300) from exc
        if attempt < 2:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.25 * (attempt + 1), remaining))
    with _PROVIDER_LOCK:
        _PROVIDER_RETRY_UNTIL[provider] = time.monotonic() + 20
    raise ChartDataUnavailable("行情连接暂时中断，有限重试未恢复；分时图稍后会自动重试，日K/周K可稍后重新打开。") from last_error


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
