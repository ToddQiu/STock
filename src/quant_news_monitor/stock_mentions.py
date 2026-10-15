from __future__ import annotations

import re
from functools import lru_cache

from .models import NewsItem, WatchItem


# Common words need a cashtag or company name to avoid attributing ordinary prose to a ticker.
AMBIGUOUS_TICKERS = {"A", "I", "ON", "IT", "ALL", "NOW", "SO", "FOR", "ARE", "CAN", "OUT", "OPEN", "LOVE", "BIG", "FAST", "REAL", "GOOD", "TEST", "MP"}
GENERIC_ASSET_NAMES = {"GOLD", "SILVER", "OIL", "BITCOIN", "BTC", "黄金", "黃金", "白银", "白銀", "原油", "比特币", "比特幣"}
SOCIAL_COMPANY_NAMES = {
    ("US", "MU"): ("Micron", "Micron Technology", "美光"),
    ("US", "MP"): ("MP Materials",),
    ("US", "CEG"): ("Constellation Energy",),
    ("US", "BRK.B"): ("Berkshire Hathaway", "伯克希尔", "伯克希爾"),
    ("US", "AAPL"): ("Apple", "Apple Inc", "苹果", "蘋果"),
    ("US", "GOOG"): ("Alphabet", "谷歌"),
    ("US", "MS"): ("Morgan Stanley", "摩根士丹利"),
    ("US", "NVDA"): ("Nvidia", "英伟达", "輝達"),
    ("US", "MSFT"): ("Microsoft", "微软", "微軟"),
    ("KR", "005930.KS"): ("Samsung Electronics", "三星电子", "三星電子"),
    ("KR", "000660.KS"): ("SK Hynix", "海力士"),
}


def social_aliases(watch: WatchItem, additional=()) -> tuple[str, ...]:
    names = (watch.name, *watch.aliases, *SOCIAL_COMPANY_NAMES.get((watch.market, watch.symbol), ()), *additional)
    unique = {}
    for alias in names:
        alias = alias.strip()
        if alias and alias.upper() != watch.symbol.upper() and alias.upper() not in GENERIC_ASSET_NAMES:
            unique.setdefault(alias.casefold(), alias)
    return tuple(unique.values())


def contains_alias(text: str, alias: str) -> bool:
    alias = alias.strip()
    if not alias:
        return False
    if alias.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text, re.I) is not None
    return alias.casefold() in text.casefold()


def matches_stock(text: str, watch: WatchItem, *, social: bool = False, additional=()) -> bool:
    symbol = watch.symbol.upper()
    names = social_aliases(watch, additional) if social else (alias for alias in (watch.name, *watch.aliases, *SOCIAL_COMPANY_NAMES.get((watch.market, watch.symbol), ())) if alias.upper() != symbol)
    if any(contains_alias(text, alias) for alias in names):
        return True
    if re.fullmatch(r"[A-Z]{1,5}", symbol):
        if re.search(rf"\${re.escape(symbol)}(?![a-z0-9])", text, re.I):
            return True
        ambiguous = AMBIGUOUS_TICKERS | GENERIC_ASSET_NAMES if social else AMBIGUOUS_TICKERS
        return symbol not in ambiguous and re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text) is not None
    return contains_alias(text, symbol)


def social_stock_text(text: str, watch: WatchItem, *, additional=()) -> str:
    return _social_stock_text(text, watch, tuple(additional))


def news_clauses(item: NewsItem, watchlist: tuple[WatchItem, ...] = ()) -> list[dict]:
    names = SOCIAL_COMPANY_NAMES.get((item.market, item.symbol), ())
    watch = WatchItem(item.market, item.symbol, item.target_name or (names[0] if names else item.symbol),
                      "", item.target_aliases)
    other_watches = [WatchItem(market, symbol, aliases[0], "", aliases)
                     for (market, symbol), aliases in SOCIAL_COMPANY_NAMES.items()
                     if (market, symbol) != (item.market, item.symbol)]
    other_watches.extend(other for other in watchlist if (other.market, other.symbol) != (item.market, item.symbol))
    result = []
    full_text = f"{item.title} {item.summary}"
    target_context = matches_stock(full_text, watch) or bool(re.search(r"\bcompany\b|公司", full_text, re.I))
    owner = item.symbol if target_context and not any(matches_stock(full_text, other) for other in other_watches) else None
    for field, body in (("title", item.title), ("summary", item.summary)):
        # Split contrast and coordinated assertions so their owners and negation scopes cannot leak.
        for part in re.split(r"[;；。\n!?！？]|(?<!\d)[,，](?!\d)|\.(?=\s)|\b(?:but|while|whereas|however|and)\b|但是|然而|不过|不過|但|而", body, flags=re.I):
            clause = part.strip()
            if not clause:
                continue
            target = matches_stock(clause, watch)
            others = [other.symbol for other in other_watches if matches_stock(clause, other)]
            if target and others:
                owner, attribution = None, "ambiguous"
            elif target:
                owner, attribution = item.symbol, "explicit"
            elif others:
                owner, attribution = others[0], "other_company"
            elif owner == item.symbol:
                attribution = "continuation"
            elif owner is None and re.search(r"\b(?:the company|company|corporation)\b|本公司|该公司|該公司|公司", clause, re.I):
                owner, attribution = item.symbol, "source_bound_company"
            else:
                attribution = "missing"
            result.append({"text": clause, "field": field, "target": item.symbol,
                           "attribution": attribution, "belongs_to_target": owner == item.symbol,
                           "owner": owner})
    return result


@lru_cache(maxsize=32768)
def _social_stock_text(text: str, watch: WatchItem, additional: tuple[str, ...]) -> str:
    symbol = watch.symbol.split(".")[0]
    explicit_pattern = re.compile(rf"\${re.escape(symbol)}(?![A-Za-z0-9])", re.I)
    # Most sampled replies are unrelated; avoid sentence-by-sentence work for them.
    if not explicit_pattern.search(text) and not matches_stock(text, watch, social=True, additional=additional):
        return ""
    # Sentence-level attribution prevents one ticker's opinion being copied to every mention.
    parts = re.split(r"[\n;；,，]|(?<=[。！？!?])|\bbut\b|\s+&\s+|\.{1,}\s+", text, flags=re.I)
    financial = re.compile(r"股票|股价|股價|财报|財報|看多|看空|利多|利好|利空|低估|高估|抄底|持有|增持|减持|減持|仓|倉|买|買|卖|賣|涨|漲|跌|估值|盈利|营收|營收|分红|分紅|\b(?:stocks?|shares?|shareholders?|investors?|portfolio|holdings?|holding|held|accumulat(?:e|ed|ing|ion)|bullish|bearish|earnings|undervalued|overvalued|long|short|buy(?:ing)?|bought|sold|sell(?:ing)?|calls?|puts?|invest(?:s|ed|ing|ments?)?|dividends?|revenue|profits?|valuation)\b", re.I)
    selected = []
    for part in parts:
        explicit = explicit_pattern.search(part)
        # A commodity nickname does not establish discussion of a particular fund or security.
        if explicit or (matches_stock(part, watch, social=True, additional=additional) and financial.search(part)):
            selected.append(part.strip())
    return "。".join(selected)
