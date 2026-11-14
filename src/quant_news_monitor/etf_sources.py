"""Public ETF reads. Never turn missing holdings into an empty portfolio."""

import json
from datetime import datetime, timezone
from urllib.parse import quote

from .etf_research import etf_symbol, latest_holdings, parse_import, save_holdings, _SAVE_LOCK
from .news_transport import NewsHttpClient


_http = NewsHttpClient()
ISHARES = {
    "SOXX": "239705/ishares-phlx-semiconductor-etf",
    "IGV": "239771/ishares-expanded-tech-software-sector-etf",
    "IVV": "239726/ishares-core-sp-500-etf",
    "IWM": "239710/ishares-russell-2000-etf",
}
SPDR = {"SPY", "XSW", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "XBI", "XSD", "KRE"}


def holdings_source(symbol):
    if symbol in ISHARES:
        return f"https://www.ishares.com/us/products/{ISHARES[symbol]}/latest-holdings.csv", ".csv"
    if symbol in SPDR:
        return f"https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{symbol.lower()}.xlsx", ".xlsx"
    return None


def load_etf(ledger, symbol):
    symbol = etf_symbol(symbol)
    snapshot = latest_holdings(ledger)
    result = {"symbol": symbol, "fund": snapshot["payload"]["funds"].get(symbol), "input_id": snapshot.get("id"),
              "market": None, "warnings": [], "checked_at": datetime.now(timezone.utc).isoformat()}
    # Only the ticker leaves the device; no holdings quantities or personal research.
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?interval=1d&range=3mo"
        payload = json.loads(_http.fetch(url, timeout=8, ttl=900))
        charts = (payload.get("chart") or {}).get("result") or []
        market = charts[0] if charts else None
        meta = (market or {}).get("meta") or {}
        if meta.get("symbol", "").upper() != symbol or meta.get("instrumentType") != "ETF":
            result["warnings"].append("行情来源未确认该代码为 ETF；不套用股票行情，请核对代码。")
        else:
            result["market"] = market
    except (OSError, ValueError, TypeError, KeyError):
        result["warnings"].append("公开行情暂未取得，请稍后刷新；已有持仓仍可分析。")

    source = holdings_source(symbol)
    if source:
        url, extension = source
        try:
            raw = _http.fetch(url, timeout=10, ttl=21600, max_bytes=1_000_000)
            fund = parse_import(raw, symbol + extension, symbol=symbol)[symbol]
            fund.update(source_url=url, provenance="发行商公开持仓文件；权重未归一化", coverage="full")
            with _SAVE_LOCK:
                old = latest_holdings(ledger)["payload"]["funds"].get(symbol)
                # A delayed issuer response must not roll back a newer manual import.
                if not old or fund["as_of"] >= old["as_of"]:
                    if not old or old.get("source_sha256") != fund["source_sha256"]:
                        save_holdings(ledger, {symbol: fund})
                    result["fund"] = old if old and old.get("source_sha256") == fund["source_sha256"] else fund
                else:
                    result["fund"] = old
                    result["warnings"].append("官网文件日期较旧，保留较新的本地持仓。")
                result["input_id"] = latest_holdings(ledger).get("id")
        except (OSError, ValueError, KeyError, TypeError):
            result["warnings"].append("发行商持仓暂未取得；保留已有快照，可导入官方 Excel / CSV。")
    else:
        result["warnings"].append("该 ETF 尚未接通自动完整持仓来源；可导入官方 Excel / CSV，随后自动计算对比。")
    return result
