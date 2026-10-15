from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Callable

from .analyzers import Analyzer
from .models import MarketMovement, NewsItem, iso_utc, utc_now
from .news_transport import retry_delay


TRADINGVIEW_COLUMNS = (
    "name",
    "description",
    "close",
    "change",
    "volume",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "sector",
    "exchange",
    "currency",
)

MARKET_SPACING_SECONDS = 3.0
MARKET_RECOVERY_DELAY_SECONDS = 12.0
HK_NAMES_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_c.json"


@lru_cache(maxsize=1)
def _hk_company_names(day: int, timeout_seconds: int) -> dict[str, str]:
    # HKEX current names avoid the stale Chinese aliases in quote-provider locales.
    try:
        request = urllib.request.Request(HK_NAMES_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("港股名称目录响应过大")
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("港股名称目录格式无效")
        names = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code, name = str(row.get("c", "")), row.get("n")
            if re.fullmatch(r"[0-9]{1,5}", code) and int(code) > 0 and isinstance(name, str) and name.strip():
                names[str(int(code))] = name.strip()
        if not names:
            raise ValueError("港股名称目录为空")
        return names
    except (OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        logging.getLogger(__name__).warning("HK Chinese names unavailable; retaining source names: %s", exc)
        return {}


@dataclass(frozen=True)
class MarketRule:
    market: str
    scanner_market: str
    exchanges: tuple[str, ...]
    minimum_price: float
    minimum_volume: int
    minimum_market_cap: int
    price_change_threshold: float
    relative_volume_threshold: float
    volume_change_threshold: float
    language: str


MARKET_RULES = (
    MarketRule("CN", "china", ("SSE", "SZSE", "BSE"), 1.0, 500_000, 1_000_000_000, 5.0, 2.5, 2.0, "zh-cn"),
    MarketRule("HK", "hongkong", ("HKEX",), 0.10, 300_000, 200_000_000, 8.0, 3.0, 2.0, "zh-hk"),
    MarketRule("US", "america", ("NASDAQ", "NYSE", "AMEX"), 0.50, 300_000, 50_000_000, 8.0, 3.0, 2.0, "en-us"),
    MarketRule("KR", "korea", ("KRX",), 100.0, 50_000, 100_000_000_000, 3.0, 1.8, 1.5, "ko-kr"),
)


EVENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings", ("earnings", "results", "revenue", "profit", "财报", "业绩", "营收", "利润", "业绩快报")),
    ("guidance", ("guidance", "outlook", "forecast", "指引", "预告", "展望", "盈利预测")),
    ("m_and_a", ("acquire", "acquisition", "merger", "takeover", "buyout", "收购", "合并", "并购", "要约")),
    ("capital_action", ("offering", "placement", "buyback", "dividend", "share sale", "增发", "配股", "回购", "分红", "减持", "增持")),
    ("regulation", ("regulator", "investigation", "probe", "antitrust", "approval", "监管", "调查", "反垄断", "处罚", "获批")),
    ("legal", ("lawsuit", "litigation", "court", "settlement", "诉讼", "法院", "和解", "索赔")),
    ("order", ("contract", "order", "tender", "deal", "中标", "订单", "合同", "合作")),
    ("capacity", ("investment", "invests", "factory", "capacity", "capex", "plant", "投资", "扩产", "产能", "工厂", "资本开支")),
    ("management", ("ceo", "cfo", "chairman", "resigns", "appoints", "管理层", "董事长", "辞任", "任命")),
    ("analyst", ("upgrade", "downgrade", "price target", "rating", "initiates", "评级", "目标价", "上调评级", "下调评级")),
    ("product", ("launch", "product", "trial", "patent", "drug", "发布", "新品", "临床", "专利", "药物")),
)

POSITIVE_WORDS = (
    "beats", "beat estimates", "raises", "raised", "upgrade", "approval", "approved",
    "wins", "record", "buyback", "surges", "超预期", "上调", "获批", "中标", "回购",
    "扭亏", "创新高", "增长",
)
NEGATIVE_WORDS = (
    "misses", "missed", "cuts", "cut guidance", "downgrade", "investigation", "lawsuit",
    "offering", "recall", "default", "下调", "低于预期", "调查", "诉讼", "处罚", "召回",
    "违约", "亏损", "减持",
)


@dataclass(frozen=True)
class MovementCandidate:
    market: str
    symbol: str
    name: str
    exchange: str
    sector: str
    currency: str
    price: float
    change_pct: float
    volume: float
    relative_volume: float
    market_cap: float
    movement_type: str
    intensity_score: float


@dataclass(frozen=True)
class EvidenceArticle:
    title: str
    summary: str
    source: str
    url: str
    published_at: datetime
    category: str
    direction: int
    rank: float


@dataclass(frozen=True)
class MovementRunSummary:
    scan_id: str
    started_at: datetime
    finished_at: datetime
    movements: tuple[MarketMovement, ...]
    market_counts: dict[str, int]
    errors: tuple[str, ...]
    analyzer: str
    retry_after_seconds: float = 0


class MarketMovementScanner:
    def __init__(
        self,
        analyzer: Analyzer,
        *,
        timeout_seconds: int = 15,
        max_per_market: int = 12,
        focus_symbols: dict[str, set[str] | None] | None = None,
        now: Callable[[], datetime] = utc_now,
    ):
        self.analyzer = analyzer
        self.timeout_seconds = timeout_seconds
        self.max_per_market = max_per_market
        self.focus_symbols = (
            {
                market.upper(): None if symbols is None else {_normalized_market_symbol(symbol) for symbol in symbols}
                for market, symbols in focus_symbols.items()
            }
            if focus_symbols is not None
            else None
        )
        self.now = now

    def run(self) -> MovementRunSummary:
        started_at = self.now()
        scan_id = iso_utc(started_at)
        candidates: list[MovementCandidate] = []
        errors: list[str] = []

        failed_markets: list[tuple[MarketRule, Exception]] = []
        retry_after_seconds = 0
        active_rules = self._active_rules()
        for index, rule in enumerate(active_rules):
            if index:
                time.sleep(MARKET_SPACING_SECONDS)
            try:
                candidates.extend(self._scan_market(rule))
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 429, 503}:
                    retry_after_seconds = 86400 if exc.code in {401, 403} else retry_delay(exc.headers, fallback=900)
                    errors.append(f"{rule.market} 行情: HTTP {exc.code}，暂停 {int(retry_after_seconds)} 秒，不绕过访问限制")
                    exc.close()
                    break
                failed_markets.append((rule, exc))

        # TradingView occasionally drops a whole connection window. Retrying the
        # failed markets as a second pass prevents a transient outage from making
        # the automatic cycle wait until the next configured interval.
        if failed_markets:
            time.sleep(MARKET_RECOVERY_DELAY_SECONDS)
            for index, (rule, _) in enumerate(failed_markets):
                if index:
                    time.sleep(MARKET_SPACING_SECONDS)
                try:
                    candidates.extend(self._scan_market(rule))
                except Exception as exc:
                    errors.append(f"{rule.market} 行情: {type(exc).__name__}: {exc}")
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 429, 503}:
                        retry_after_seconds = 86400 if exc.code in {401, 403} else retry_delay(exc.headers, fallback=900)
                        exc.close()
                        break

        movements: list[MarketMovement] = []
        worker_count = min(4, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._explain_candidate, candidate, scan_id, started_at): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    movements.append(future.result())
                except Exception as exc:
                    errors.append(f"{candidate.market}:{candidate.symbol} 归因: {type(exc).__name__}: {exc}")
                    movements.append(self._without_evidence(candidate, scan_id, started_at))

        movements.sort(key=lambda item: item.intensity_score, reverse=True)
        market_counts = {
            rule.market: sum(1 for item in movements if item.market == rule.market)
            for rule in active_rules
        }
        return MovementRunSummary(
            scan_id=scan_id,
            started_at=started_at,
            finished_at=self.now(),
            movements=tuple(movements),
            market_counts=market_counts,
            errors=tuple(errors),
            analyzer=getattr(self.analyzer, "name", "unknown"),
            retry_after_seconds=retry_after_seconds,
        )

    def _scan_market(self, rule: MarketRule) -> list[MovementCandidate]:
        rows = dict(self._request_rows(rule, "change", "desc"))
        focused = self.focus_symbols.get(rule.market) if self.focus_symbols is not None else None

        selected: list[MovementCandidate] = []
        for scanner_symbol, data in rows.items():
            candidate = _candidate_from_row(rule, scanner_symbol, data)
            if candidate is not None and (
                focused is None or _normalized_market_symbol(candidate.symbol) in focused
            ):
                selected.append(candidate)
        selected.sort(key=lambda item: item.intensity_score, reverse=True)
        # A focused scan represents an explicit user-owned universe. Keep every
        # triggered holding instead of silently dropping lower-ranked anomalies.
        selected = selected if focused is not None else selected[: self.max_per_market]
        if rule.market == "HK" and selected:
            names = _hk_company_names(int(time.time() // 86400), self.timeout_seconds)
            selected = [replace(item, name=names.get(str(int(item.symbol)), item.name))
                        if re.fullmatch(r"[0-9]{1,5}", item.symbol) else item for item in selected]
        return selected

    def _request_rows(
        self,
        rule: MarketRule,
        sort_by: str,
        sort_order: str,
    ) -> list[tuple[str, list[object]]]:
        focused = self.focus_symbols.get(rule.market) if self.focus_symbols is not None else None
        filters: list[dict[str, object]] = [
            {"left": "active_symbol", "operation": "equal", "right": True},
            {"left": "exchange", "operation": "in_range", "right": list(rule.exchanges)},
            {"left": "close", "operation": "greater", "right": rule.minimum_price},
        ]
        if focused is None:
            filters.extend(
                [
                    {"left": "type", "operation": "equal", "right": "stock"},
                    {"left": "volume", "operation": "greater", "right": rule.minimum_volume},
                    {"left": "market_cap_basic", "operation": "greater", "right": rule.minimum_market_cap},
                ]
            )
        if rule.market == "CN":
            filters.append({"left": "currency", "operation": "equal", "right": "CNY"})
        tickers = (
            [
                f"{exchange}:{symbol}"
                for symbol in sorted(focused)
                for exchange in rule.exchanges
            ]
            if focused
            else []
        )
        payload = {
            "filter": filters,
            "options": {"lang": "en" if rule.market in {"US", "HK"} else "ko" if rule.market == "KR" else "zh"},
            "markets": [rule.scanner_market],
            "symbols": {"query": {"types": []}, "tickers": tickers},
            "columns": list(TRADINGVIEW_COLUMNS),
            "sort": {"sortBy": sort_by, "sortOrder": sort_order},
            "range": [0, 6000],
        }
        raw = b""
        last_error: Exception | None = None
        for attempt in range(5):
            request = urllib.request.Request(
                f"https://scanner.tradingview.com/{rule.scanner_market}/scan",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                    "Connection": "close",
                    "Accept": "application/json",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Origin": "https://www.tradingview.com",
                    "Referer": "https://www.tradingview.com/",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read(3_000_001)
                break
            except OSError as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 429, 503}:
                    raise
                last_error = exc
                if attempt < 4:
                    time.sleep(min(8.0, 1.5 * (2**attempt)))
        if not raw:
            raise last_error or RuntimeError("行情服务未返回数据")
        if len(raw) > 3_000_000:
            raise ValueError("行情响应过大")
        parsed = json.loads(raw)
        return [
            (str(row.get("s", "")), list(row.get("d", [])))
            for row in parsed.get("data", [])
            if isinstance(row, dict) and isinstance(row.get("d"), list)
        ]

    def _active_rules(self) -> tuple[MarketRule, ...]:
        if self.focus_symbols is None:
            return MARKET_RULES
        return tuple(rule for rule in MARKET_RULES if rule.market in self.focus_symbols)

    def _explain_candidate(
        self,
        candidate: MovementCandidate,
        scan_id: str,
        detected_at: datetime,
    ) -> MarketMovement:
        rule = next(rule for rule in MARKET_RULES if rule.market == candidate.market)
        articles = _fetch_bing_articles(candidate, rule.language, self.timeout_seconds, self.now())
        evidence = max(articles, key=lambda item: item.rank, default=None)
        if evidence is None:
            return self._without_evidence(candidate, scan_id, detected_at)

        item = NewsItem(
            market=candidate.market,
            symbol=candidate.symbol,
            title=evidence.title,
            summary=evidence.summary,
            source=evidence.source,
            url=evidence.url,
            published_at=evidence.published_at,
            discovered_at=detected_at,
            target_name=candidate.name,
        )
        analysis = self.analyzer.analyze(item)
        category = evidence.category if evidence.category != "other" else analysis.event_type
        fallback = _headline_direction(str(analysis.metadata.get("evidence_text", ""))) if analysis.metadata else evidence.direction
        analyzed_direction = _sign(analysis.direction) or (fallback if analysis.metadata.get("allow_direction_fallback", True) else 0)
        price_direction = _sign(candidate.change_pct)
        aligned = analyzed_direction == price_direction if analyzed_direction else None
        confidence = _reason_confidence(evidence, category, aligned, candidate.relative_volume, analysis.confidence)

        if category == "other" or evidence.rank < 5 or analysis.metadata.get("direction_eligible") is False:
            summary = (
                f"未发现近72小时内足以解释本次异动的明确公司级催化。"
                f"最新相关报道为“{evidence.title}”，更可能是板块、资金或技术面共同作用，等待公告确认。"
            )
            confidence = min(confidence, 0.35)
        else:
            relation = "方向一致" if aligned is True else "方向不一致" if aligned is False else "方向性尚不明确"
            summary = (
                f"最可能由{_event_label(category)}驱动：{evidence.title}。"
                f"该公开信息与本次价格异动{relation}，属于时序和内容匹配，不代表因果已经证实。"
            )
        return _movement_from_candidate(
            candidate,
            scan_id,
            detected_at,
            category=category,
            summary=summary,
            confidence=confidence,
            evidence=evidence,
            analyzer=analysis.analyzer,
        )

    def _without_evidence(
        self,
        candidate: MovementCandidate,
        scan_id: str,
        detected_at: datetime,
    ) -> MarketMovement:
        summary = (
            "近72小时未检索到可直接验证的公司级公开消息；"
            "当前更可能由板块联动、资金面、技术性交易或低流动性放大造成，需等待公告确认。"
        )
        return _movement_from_candidate(
            candidate,
            scan_id,
            detected_at,
            category="unconfirmed",
            summary=summary,
            confidence=0.20,
            evidence=None,
            analyzer=getattr(self.analyzer, "name", "unknown"),
        )


def _normalized_market_symbol(symbol: str) -> str:
    normalized = str(symbol).upper().rsplit(":", 1)[-1]
    for suffix in (".KS", ".KQ", ".HK", ".SS", ".SZ"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _candidate_from_row(
    rule: MarketRule,
    scanner_symbol: str,
    data: list[object],
) -> MovementCandidate | None:
    if len(data) < len(TRADINGVIEW_COLUMNS):
        return None
    symbol = str(data[0] or scanner_symbol.rsplit(":", 1)[-1]).upper()
    name = str(data[1] or symbol).strip()
    if rule.market == "CN" and (symbol.startswith("200") or symbol.startswith("900")):
        return None
    try:
        price = float(data[2])
        change_pct = float(data[3])
        volume = float(data[4])
        relative_volume = float(data[5] or 0.0)
        market_cap = float(data[6] or 0.0)
    except (TypeError, ValueError):
        return None
    price_trigger = abs(change_pct) >= rule.price_change_threshold
    volume_trigger = (
        abs(change_pct) >= rule.volume_change_threshold
        and relative_volume >= rule.relative_volume_threshold
    )
    if not price_trigger and not volume_trigger:
        return None
    movement_type = "price_volume" if price_trigger and volume_trigger else "price" if price_trigger else "volume"
    intensity = min(
        100.0,
        abs(change_pct) * 2.6
        + min(relative_volume, 20.0) * 2.2
        + (12.0 if movement_type == "price_volume" else 0.0),
    )
    return MovementCandidate(
        market=rule.market,
        symbol=symbol,
        name=name,
        exchange=str(data[8] or scanner_symbol.partition(":")[0]),
        sector=str(data[7] or ""),
        currency=str(data[9] or ""),
        price=price,
        change_pct=change_pct,
        volume=volume,
        relative_volume=relative_volume,
        market_cap=market_cap,
        movement_type=movement_type,
        intensity_score=round(intensity, 2),
    )


def _fetch_bing_articles(
    candidate: MovementCandidate,
    language: str,
    timeout_seconds: int,
    now: datetime,
) -> list[EvidenceArticle]:
    company = re.sub(r"\s+Class\s+[A-Z](?:\s+Shares?)?$", "", candidate.name, flags=re.IGNORECASE)
    query = f"{company} {candidate.symbol}"
    params = urllib.parse.urlencode({"q": query, "format": "rss", "setlang": language})
    request = urllib.request.Request(
        f"https://www.bing.com/news/search?{params}",
        headers={"User-Agent": "QuantNewsMonitor/0.1 (+local research tool)"},
    )
    raw = b""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(2_000_001)
            break
        except OSError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    if not raw:
        raise last_error or RuntimeError("新闻服务未返回数据")
    if len(raw) > 2_000_000:
        raise ValueError("新闻响应过大")
    root = ET.fromstring(raw)
    cutoff = now - timedelta(hours=72)
    articles: list[EvidenceArticle] = []
    for entry in root.findall(".//item")[:12]:
        title = _child_text(entry, "title")
        summary = _child_text(entry, "description")
        published_at = _feed_datetime(_child_text(entry, "pubdate"))
        if not title or published_at < cutoff:
            continue
        url = _direct_news_url(_child_text(entry, "link"))
        if not url or not _looks_like_article(url, title):
            continue
        source = _child_text(entry, "source") or _source_from_url(url)
        text = f"{title} {summary}".casefold()
        category = _classify_event(text)
        direction = _headline_direction(text)
        rank = _article_rank(candidate, title, summary, category, direction, published_at, now)
        articles.append(
            EvidenceArticle(title, summary, source, url, published_at, category, direction, rank)
        )
    return articles


def _article_rank(
    candidate: MovementCandidate,
    title: str,
    summary: str,
    category: str,
    direction: int,
    published_at: datetime,
    now: datetime,
) -> float:
    text = f"{title} {summary}".casefold()
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
    rank = 4.0 if age_hours <= 12 else 3.0 if age_hours <= 24 else 1.5
    if category != "other":
        rank += 5.0
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(candidate.symbol.casefold())}(?![A-Za-z0-9])", text):
        rank += 2.0
    name_tokens = [token for token in re.findall(r"[a-z0-9]{4,}", candidate.name.casefold()) if token not in {"limited", "company", "corporation", "holdings", "class"}]
    name_tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,}", candidate.name))
    if any(token in text for token in name_tokens[:4]):
        rank += 2.0
    price_direction = _sign(candidate.change_pct)
    if direction and direction == price_direction:
        rank += 2.0
    elif direction and direction != price_direction:
        rank -= 2.5
    return rank


def _reason_confidence(
    evidence: EvidenceArticle,
    category: str,
    aligned: bool | None,
    relative_volume: float,
    analyzer_confidence: float,
) -> float:
    value = 0.32
    if category != "other":
        value += 0.18
    if aligned is True:
        value += 0.14
    elif aligned is False:
        value -= 0.18
    if evidence.rank >= 10:
        value += 0.08
    if relative_volume >= 3:
        value += 0.04
    value += min(0.10, max(0.0, analyzer_confidence) * 0.10)
    return round(max(0.15, min(0.90, value)), 2)


def _movement_from_candidate(
    candidate: MovementCandidate,
    scan_id: str,
    detected_at: datetime,
    *,
    category: str,
    summary: str,
    confidence: float,
    evidence: EvidenceArticle | None,
    analyzer: str,
) -> MarketMovement:
    return MarketMovement(
        scan_id=scan_id,
        detected_at=detected_at,
        market=candidate.market,
        symbol=candidate.symbol,
        name=candidate.name,
        exchange=candidate.exchange,
        sector=candidate.sector,
        currency=candidate.currency,
        price=candidate.price,
        change_pct=candidate.change_pct,
        volume=candidate.volume,
        relative_volume=candidate.relative_volume,
        market_cap=candidate.market_cap,
        movement_type=candidate.movement_type,
        intensity_score=candidate.intensity_score,
        reason_category=category,
        reason_summary=summary,
        confidence=confidence,
        evidence_title=evidence.title if evidence else "",
        evidence_url=evidence.url if evidence else "",
        evidence_source=evidence.source if evidence else "",
        evidence_published_at=evidence.published_at if evidence else None,
        analyzer=analyzer,
    )


def _direct_news_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    direct = urllib.parse.parse_qs(parsed.query).get("url", [value])[0]
    try:
        direct_parsed = urllib.parse.urlsplit(direct)
    except ValueError:
        return ""
    if direct_parsed.scheme not in {"http", "https"} or not direct_parsed.hostname:
        return ""
    if direct_parsed.hostname.casefold().endswith("bing.com"):
        return ""
    return direct


def _looks_like_article(url: str, title: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.casefold().rstrip("/")
    reject_fragments = (
        "/quote/",
        "/quotes/",
        "/equities/",
        "/market-data/",
        "/historical-data",
        "/company-people",
        "/insider-sentiment",
        "/press-releases",
        "/financials",
        "/analysis",
        "/chart",
        "/profile",
    )
    if any(fragment in path for fragment in reject_fragments):
        return False
    generic_title_phrases = (
        "real time chart",
        "stock quote",
        "historical data",
        "company profile",
        "company people",
        "实时图表",
        "历史数据",
        "公司资料",
    )
    folded_title = title.casefold()
    return not any(phrase in folded_title for phrase in generic_title_phrases)


def _child_text(entry: ET.Element, local_name: str) -> str:
    for child in entry:
        if child.tag.rsplit("}", 1)[-1].casefold() == local_name.casefold():
            return (child.text or "").strip()
    return ""


def _feed_datetime(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_from_url(url: str) -> str:
    hostname = urllib.parse.urlsplit(url).hostname or "新闻来源"
    return hostname.removeprefix("www.")


def _classify_event(text: str) -> str:
    for category, patterns in EVENT_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return category
    return "other"


def _headline_direction(text: str) -> int:
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    return _sign(positive - negative)


def _event_label(category: str) -> str:
    return {
        "earnings": "财报业绩",
        "guidance": "业绩指引",
        "m_and_a": "并购重组",
        "capital_action": "资本运作",
        "regulation": "监管审批",
        "legal": "法律事项",
        "order": "订单合作",
        "capacity": "投资扩产",
        "management": "管理层变动",
        "analyst": "机构评级",
        "product": "产品研发",
    }.get(category, "公开消息")


def _sign(value: float | int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
