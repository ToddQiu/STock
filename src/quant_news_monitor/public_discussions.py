from __future__ import annotations

import json
import re
import http.client
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from defusedxml import ElementTree
from protego import Protego

from .news_transport import retry_delay


USER_AGENT = "QuantifyWebSamples/1.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/3.0/"
WEB_SOURCES = {
    "valuepickr": {"name": "ValuePickr", "domains": ("forum.valuepickr.com",), "mode": "web",
                  "url": "https://forum.valuepickr.com/posts.rss", "terms_url": "https://forum.valuepickr.com/tos"},
    "tradingqna": {"name": "Trading Q&A", "domains": ("tradingqna.com",), "mode": "web",
                  "url": "https://tradingqna.com/posts.rss", "terms_url": "https://tradingqna.com/tos"},
}
for _source in WEB_SOURCES.values():
    _source.update(scope="英语投资论坛，印度市场为主；不代表原四个平台", license="CC BY-NC-SA 3.0", license_url=LICENSE_URL)
WEB_SOURCES["hackernews"] = {
    "name": "Hacker News", "domains": ("news.ycombinator.com",), "mode": "web", "format": "search",
    "url": "https://hn.algolia.com/api/v1/search_by_date", "terms_url": "https://hn.algolia.com/api",
    "scope": "公开科技社区，投资主题广泛检索并补充股票池公司；不代表全网或原四个平台",
    "interval_seconds": 60,
}
HN_SEARCH_OPTIONS = {"typoTolerance": "false", "queryType": "prefixNone", "restrictSearchableAttributes": "comment_text"}


def parse_hackernews(payload: bytes, *, now: datetime) -> tuple[list[dict], int]:
    try:
        data = json.loads(payload)
        if not isinstance(data.get("hits"), list):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise WebSourceError("公开社区检索返回格式异常", stage="parse") from None
    posts, skipped = [], 0
    for hit in data["hits"][:100]:
        try:
            identity = str(hit["objectID"])
            published = datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00"))
            text = plain_text(hit.get("comment_text") or "", remove_quotes=True)
            if (not identity.isdigit() or published.tzinfo is None or published > now
                    or (now - published).total_seconds() > 86400 or not 1 <= len(text) <= 12000):
                raise ValueError()
            posts.append({"id": identity, "url": f"https://news.ycombinator.com/item?id={identity}",
                          "text": text, "author": str(hit.get("author") or ""),
                          "published_at": published.astimezone(timezone.utc).isoformat(),
                          "context": {"title": plain_text(hit.get("story_title") or "")[:300],
                                      "analysis_text": text, "feed_url": WEB_SOURCES["hackernews"]["url"],
                                      "adaptation": "官方公开检索；仅正文参与观点匹配，不使用检索词或话题标题"}})
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            skipped += 1
    return posts, skipped


def hackernews_queries(watches) -> list[str]:
    known = {"AAPL": "Apple", "GOOG": "Google", "GOOGL": "Google", "MU": "Micron",
             "005930.KS": "Samsung", "000660.KS": "Hynix", "NVDA": "Nvidia",
             "MSFT": "Microsoft", "TSLA": "Tesla", "AMZN": "Amazon", "META": "Meta"}
    queries = ["bullish", "bearish", "undervalued", "overvalued", "stock", "earnings", "shares", "valuation"]
    for watch in watches:
        name = known.get(watch.symbol)
        if not name:
            name = next((alias for alias in (watch.name, *watch.aliases)
                         if re.fullmatch(r"[A-Za-z][A-Za-z .&-]{2,59}", alias)), None)
        if name:
            queries.append(name.strip() + " stock")
    return list(dict.fromkeys(queries))[:24]


def public_source_interval(platform: str, default: int) -> int:
    return int(WEB_SOURCES[platform].get("interval_seconds", default))


class WebSourceError(RuntimeError):
    def __init__(self, message: str, retry_seconds: int = 900, status: str = "error", *, stage: str = "request"):
        super().__init__(message)
        self.retry_seconds = retry_seconds
        self.status = status
        self.stage = stage


class NoWebRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WebSourceError("来源发生重定向，已暂停；需要重新核验订阅地址", 3600, "blocked")


class PlainText(HTMLParser):
    def __init__(self, *, remove_quotes: bool):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.remove_quotes = remove_quotes

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        excluded = tag in {"script", "style", "iframe", "noscript", "svg", "form"}
        excluded |= self.remove_quotes and (tag == "blockquote" or "quote" in (attrs.get("class") or "").split())
        if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr", "source"}:
            self.stack.append((tag, excluded or any(hidden for _, hidden in self.stack)))
        if tag in {"br", "p", "div", "li", "blockquote", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break
        if tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, text):
        if not any(hidden for _, hidden in self.stack):
            self.parts.append(text)

    def text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def plain_text(value: str, *, remove_quotes: bool = False) -> str:
    parser = PlainText(remove_quotes=remove_quotes)
    parser.feed(value)
    return parser.text()


def parse_discussion_feed(payload: bytes, platform: str, *, now: datetime) -> tuple[list[dict], int]:
    if platform not in WEB_SOURCES:
        raise ValueError("不支持的网页讨论来源")
    try:
        root = ElementTree.fromstring(payload)
    except Exception:
        raise WebSourceError("订阅返回格式异常，可能需要登录或人工验证；未作为帖子入库", 3600, "blocked") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise WebSourceError("来源没有返回有效的公开讨论订阅，已暂停", 3600, "blocked")
    posts, skipped = [], 0
    for item in root.findall("./channel/item")[:100]:
        try:
            url = (item.findtext("link") or "").strip()
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname not in WEB_SOURCES[platform]["domains"]
                    or parsed.username or parsed.password or parsed.port not in {None, 443} or not parsed.path.startswith("/t/")):
                raise ValueError("帖子链接不属于此讨论源")
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None or published > now or (now-published).total_seconds() > 15*86400:
                raise ValueError("缺少可信的近期发布时间")
            html = item.findtext("description") or ""
            text = plain_text(html)
            if not text or len(text) > 12000:
                raise ValueError("帖子为空或过长")
            posts.append({"id": item.findtext("guid") or url, "url": url, "text": text,
                          "author": item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "",
                          "published_at": published.astimezone(timezone.utc).isoformat(),
                          "context": {"title": plain_text(item.findtext("title") or "")[:300],
                                      "analysis_text": plain_text(html, remove_quotes=True), "raw_html": html,
                                      "feed_url": WEB_SOURCES[platform]["url"], "license_url": LICENSE_URL,
                                      "license": "CC BY-NC-SA 3.0", "adaptation": "正文转为纯文本；观点分析剔除引用内容"}})
        except (TypeError, ValueError, OverflowError):
            skipped += 1
    if skipped and not posts:
        raise WebSourceError(f"收到 {skipped} 条记录，但缺少有效正文、时间或原帖链接，未入库")
    return posts, skipped


class PublicDiscussionClient:
    """Fixed public RSS and official search endpoints; no login or link traversal."""

    def __init__(self, path: Path, *, opener=None, clock=time.time):
        self.path, self.clock = path, clock
        self.opener = opener
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS web_cache (url TEXT PRIMARY KEY, payload BLOB, metadata TEXT NOT NULL)")
        finally:
            db.close()
        self.allowed_urls = {source["url"] for source in WEB_SOURCES.values()}
        self.allowed_urls |= {self._robots_url(source["url"]) for source in WEB_SOURCES.values()}

    @staticmethod
    def _robots_url(url):
        parsed = urllib.parse.urlsplit(url)
        return f"https://{parsed.hostname}/robots.txt"

    @staticmethod
    def _fallback_ssl_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.options |= ssl.OP_NO_TICKET
        context.set_ciphers("DEFAULT")
        return context

    def _read(self, url):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            row = db.execute("SELECT payload, metadata FROM web_cache WHERE url=?", (url,)).fetchone()
            return (row[0], json.loads(row[1])) if row else (None, {})
        finally:
            db.close()

    def _open(self, request, *, timeout: int, context: ssl.SSLContext | None = None, ignore_proxy: bool = False):
        if self.opener is not None:
            return self.opener(request, timeout)
        if context is None or str(request.full_url).startswith("http://"):
            opener = [NoWebRedirect()]
            if ignore_proxy:
                opener.append(urllib.request.ProxyHandler({}))
            return urllib.request.build_opener(*opener).open(request, timeout=timeout)
        opener = [NoWebRedirect(), urllib.request.HTTPSHandler(context=context)]
        if ignore_proxy:
            opener.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*opener).open(request, timeout=timeout)

    def _save(self, url, payload, metadata):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO web_cache VALUES (?,?,?)", (url, payload, json.dumps(metadata)))
                if url.startswith(WEB_SOURCES["hackernews"]["url"] + "?"):
                    prefix = WEB_SOURCES["hackernews"]["url"] + "?%"
                    db.execute("DELETE FROM web_cache WHERE url LIKE ? AND rowid NOT IN "
                               "(SELECT rowid FROM web_cache WHERE url LIKE ? ORDER BY rowid DESC LIMIT 256)", (prefix, prefix))
        finally:
            db.close()

    def _download(self, request):
        # TLS EOF can occur at either handshake or body read. Keep certificate verification on.
        for attempt, timeout in enumerate((10, 5, 5)):
            try:
                with self._open(request, timeout=timeout,
                                context=self._fallback_ssl_context() if attempt else None,
                                ignore_proxy=attempt == 2) as response:
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        raise WebSourceError("订阅响应超过 2 MB，已停止读取", stage="parse")
                    return body, response.headers
            except urllib.error.HTTPError:
                raise
            except OSError as exc:
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                if attempt == 2 or (attempt == 0 and not isinstance(cause, ssl.SSLEOFError)):
                    raise

    def next_request_at(self, platform: str, interval: int) -> datetime | None:
        _, meta = self._read(WEB_SOURCES[platform]["url"])
        until = max(meta.get("retry_at", 0), max(meta.get("checked_at", 0), meta.get("attempted_at", 0))+max(interval, meta.get("min_interval", 0)))
        return datetime.fromtimestamp(until, timezone.utc) if until > self.clock() else None

    def _request(self, url, *, ttl):
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        hn_search = (f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == WEB_SOURCES["hackernews"]["url"]
                     and set(query) == {"query", "tags", "hitsPerPage", "numericFilters", *HN_SEARCH_OPTIONS}
                     and all(query.get(key) == [value] for key, value in HN_SEARCH_OPTIONS.items())
                     and query["tags"] == ["comment"] and query["hitsPerPage"] == ["100"]
                     and len(query["query"]) == 1 and 1 <= len(query["query"][0]) <= 80
                     and len(query["numericFilters"]) == 1
                     and re.fullmatch(r"created_at_i>\d{1,12}", query["numericFilters"][0]))
        if url not in self.allowed_urls and not hn_search:
            raise WebSourceError("此版本只连接已核验的公开讨论地址", 3600, "blocked")
        payload, meta = self._read(url)
        now = self.clock()
        if meta.get("retry_at", 0) > now:
            raise WebSourceError(meta.get("error") or "来源正在等待重试", int(meta["retry_at"]-now)+1,
                                 meta.get("status", "error"), stage=meta.get("failure_stage", "request"))
        if payload is not None and now-meta.get("checked_at", 0) < ttl:
            return payload, meta
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/xml, text/plain"}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("modified"):
            headers["If-Modified-Since"] = meta["modified"]
        try:
            # Read current OS proxy settings when each request starts, including after a network switch.
            request = urllib.request.Request(url, headers=headers)
            try:
                body, response_headers = self._download(request)
                meta = {"checked_at": self.clock(), "retry_at": 0, "status": "connected", "error": None,
                        "etag": response_headers.get("ETag"), "modified": response_headers.get("Last-Modified")}
            except urllib.error.HTTPError as exc:
                try:
                    if exc.code != 304 or payload is None:
                        if exc.code in {401, 403, 407}:
                            raise WebSourceError(f"访问受限（HTTP {exc.code}），已暂停；不尝试登录或绕过验证",
                                                 86400, "blocked", stage="access") from None
                        limited = exc.code in {429, 503}
                        delay = retry_delay(exc.headers, fallback=900) if limited else 900
                        raise WebSourceError(f"来源请求失败（HTTP {exc.code}），已按等待时间暂停",
                                             max(300, int(delay)), stage="rate_limit" if limited else "request") from None
                    body = payload
                    meta.update(checked_at=self.clock(), retry_at=0, status="connected", error=None,
                                failure_stage=None, network_failures=0)
                finally:
                    exc.close()
            self._save(url, body, meta)
            return body, meta
        except (WebSourceError, OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, WebSourceError):
                error = exc
            else:
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                phase = "访问规则" if url.endswith("/robots.txt") else "讨论订阅"
                failures = min(4, int(meta.get("network_failures", 0))+1)
                meta["network_failures"] = failures
                error = WebSourceError(f"{phase}网络读取失败（{type(cause).__name__}），请检查网络或系统代理；稍后重试",
                                       min(1800, 300*2**(failures-1)), stage="access_rules" if url.endswith("/robots.txt") else "network")
            meta.update(retry_at=self.clock()+error.retry_seconds, error=str(error), status=error.status, failure_stage=error.stage)
            self._save(url, payload, meta)
            raise error from None

    def _fetch_hackernews(self, url, meta, queries, interval, now):
        queries = list(dict.fromkeys(queries or hackernews_queries(())))[:24]
        cursor = int(meta.get("query_cursor", 0)) % len(queries)
        ordered = queries[cursor:] + queries[:cursor]
        waiting = {q: state for q, state in meta.get("query_waiting", {}).items()
                   if q in queries and state["retry_at"] > self.clock()}
        collected, skipped, completed, attempted = {}, 0, 0, 0
        errors, source_retry_at, latest_failed_at = [], 0, None
        deadline = time.monotonic() + 30
        for offset, query in enumerate(ordered):
            if time.monotonic() >= deadline:
                break
            meta["query_cursor"] = (cursor + offset + 1) % len(queries)
            if query in waiting:
                continue
            params = urllib.parse.urlencode({"query": query, "tags": "comment", "hitsPerPage": 100,
                                           "numericFilters": f"created_at_i>{int(now.timestamp()) - 86400}",
                                           **HN_SEARCH_OPTIONS})
            attempted += 1
            try:
                body, _ = self._request(f"{url}?{params}", ttl=60)
                posts, invalid = parse_hackernews(body, now=datetime.fromtimestamp(self.clock(), timezone.utc))
                collected.update((p["id"], p) for p in posts)
                skipped += invalid
                completed += 1
            except WebSourceError as error:
                latest_failed_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
                waiting[query] = {"retry_at": self.clock() + error.retry_seconds, "error": str(error), "stage": error.stage,
                                  "failed_at": latest_failed_at}
                errors.append(error)
                if error.status == "blocked" or error.stage == "rate_limit":
                    source_retry_at = waiting[query]["retry_at"]
                    break
        pending = len(queries) - completed
        latest_error = errors[-1] if errors else None
        status = "connected" if not pending else "partial" if completed else "error"
        if latest_error and latest_error.status == "blocked":
            status = "blocked"
        error_text = str(latest_error) if latest_error else next((state["error"] for state in waiting.values()), None)
        stage = latest_error.stage if latest_error else next((state["stage"] for state in waiting.values()), None)
        failed_at = latest_failed_at if latest_error else next((state.get("failed_at") for state in waiting.values()), None)
        if waiting and len(waiting) == len(queries):
            source_retry_at = max(source_retry_at, min(state["retry_at"] for state in waiting.values()))
        meta.update(checked_at=self.clock(), retry_at=source_retry_at, query_waiting=waiting,
                    status=status, error=error_text, failure_stage=stage)
        self._save(url, None, meta)
        return {"posts": list(collected.values()), "skipped": skipped, "error": error_text, "status": status,
                "diagnostics": {"failure_stage": stage, "latest_error": error_text, "latest_error_at": failed_at, "query_total": len(queries),
                                "query_completed": completed, "query_attempted": attempted, "query_pending": pending,
                                "query_retry_at": datetime.fromtimestamp(min(s["retry_at"] for s in waiting.values()), timezone.utc).isoformat() if waiting else None},
                "checked_at": datetime.fromtimestamp(meta["checked_at"], timezone.utc).isoformat(),
                "next_request_at": datetime.fromtimestamp(max(source_retry_at, meta["checked_at"] + max(60, interval)), timezone.utc)}

    def fetch(self, platform: str, *, interval: int = 300, now: datetime | None = None, queries: list[str] | None = None) -> dict:
        if platform not in WEB_SOURCES:
            raise ValueError("不支持的网页讨论来源")
        now = now or datetime.now(timezone.utc)
        url = WEB_SOURCES[platform]["url"]
        with self._lock:
            allowed = self.next_request_at(platform, interval)
            if allowed:
                payload, meta = self._read(url)
                raise WebSourceError(meta.get("error") or "网页源处于采样等待期", max(1, int(allowed.timestamp()-self.clock())+1), "blocked" if meta.get("status") == "blocked" else "error")
            payload, meta = self._read(url)
            meta["attempted_at"] = self.clock()
            self._save(url, payload, meta)
            try:
                if platform == "hackernews":
                    return self._fetch_hackernews(url, meta, queries, interval, now)
                robots_url = self._robots_url(url)
                robots_body, robots_meta = self._request(robots_url, ttl=3600)
                if b"<html" in robots_body.lower() or b"<!doctype" in robots_body.lower():
                    raise WebSourceError("无法确认来源的自动访问规则，已暂停", 3600, "blocked")
                try:
                    rules = Protego.parse(robots_body.decode("utf-8-sig", errors="strict"))
                except UnicodeError:
                    raise WebSourceError("来源的自动访问规则无法读取，已暂停", 3600, "blocked") from None
                if not rules.can_fetch(url, USER_AGENT):
                    raise WebSourceError("来源 robots.txt 不允许读取此订阅，已暂停", 3600, "blocked")
                crawl_delay = float(rules.crawl_delay(USER_AGENT) or 0)
                delay = robots_meta["checked_at"]+crawl_delay-self.clock()
                if delay > 0:
                    raise WebSourceError("正在遵守来源的访问间隔", max(1, int(delay)+1))
                payload, meta = self._request(url, ttl=0)
                posts, skipped = parse_discussion_feed(payload, platform, now=now)
                meta["min_interval"] = crawl_delay
                self._save(url, payload, meta)
                return {"posts": posts, "skipped": skipped, "checked_at": datetime.fromtimestamp(meta["checked_at"], timezone.utc).isoformat(),
                        "next_request_at": datetime.fromtimestamp(meta["checked_at"]+max(interval, crawl_delay), timezone.utc)}
            except WebSourceError as exc:
                payload, meta = self._read(url)
                meta.update(retry_at=self.clock()+exc.retry_seconds, error=str(exc), status=exc.status, failure_stage=exc.stage)
                self._save(url, payload, meta)
                raise
