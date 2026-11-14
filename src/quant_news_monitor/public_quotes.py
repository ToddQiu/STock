"""Small public quote fallback. Provider time is retained, never replaced by fetch time."""
import math
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def tencent_code(market, symbol):
    if market == "US" and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", symbol):
        return "us" + symbol
    code = symbol.split(".")[0]
    if market == "HK" and code.isdigit():
        return "hk" + code.zfill(5)
    if market == "CN" and re.fullmatch(r"\d{6}\.(SS|SZ|BJ)", symbol):
        return {"SS": "sh", "SZ": "sz", "BJ": "bj"}[symbol[-2:]] + code
    raise ValueError("备用行情不支持该证券")


def parse_tencent(text, market, symbol):
    key = tencent_code(market, symbol)
    match = re.fullmatch(r'\s*v_' + re.escape(key) + r'="([^"\r\n]+)";\s*', text)
    if not match:
        raise ValueError("备用来源证券代码不符")
    parts = match[1].split("~")
    if len(parts) < 47 or not parts[1]:
        raise ValueError("备用来源字段不足")
    actual = parts[2].split(".")[0]
    requested = symbol.split(".")[0] if market != "US" else symbol
    if (actual.lstrip("0") if market != "US" else actual) != (requested.lstrip("0") if market != "US" else requested):
        raise ValueError("备用来源主体不符")
    def number(index):
        try:
            value = float(parts[index])
            return value if math.isfinite(value) else None
        except (ValueError, IndexError):
            return None
    price, previous = number(3), number(4)
    if not price or price <= 0:
        raise ValueError("备用来源价格无效")
    fmt, zone = ("%Y%m%d%H%M%S", "Asia/Shanghai") if market == "CN" else ("%Y/%m/%d %H:%M:%S", "Asia/Hong_Kong") if market == "HK" else ("%Y-%m-%d %H:%M:%S", "America/New_York")
    moment = datetime.strptime(parts[30], fmt).replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if (moment - now).total_seconds() > 60:
        raise ValueError("备用来源报价时间在未来")
    currency = {"US": "USD", "CN": "CNY", "HK": "HKD"}[market]
    high, low = number(33), number(34)
    return {"market": market, "symbol": symbol, "name": parts[46] if market == "US" else parts[1],
            "price": price, "previous_close": previous if previous and previous > 0 else None,
            "day_high": high if high and high >= price else None,
            "day_low": low if low and 0 < low <= price else None,
            "change_pct": (price / previous - 1) * 100 if previous and previous > 0 else None,
            "currency": currency, "quote_time": moment.isoformat(),
            "status": "stale" if (now - moment).total_seconds() > 7 * 86400 else "delayed",
            "source": "腾讯财经 · 公开参考", "source_url": "https://gu.qq.com/" + key,
            "source_note": "保留来源最后报价；不保证实时，不将收盘价标为实时价。"}


def fetch_tencent_quote(market, symbol, timeout=5):
    key = tencent_code(market, symbol)
    request = Request("https://qt.gtimg.cn/q=" + key, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(20001)
    if len(data) > 20000:
        raise ValueError("备用来源响应过大")
    return parse_tencent(data.decode("gbk"), market, symbol)
