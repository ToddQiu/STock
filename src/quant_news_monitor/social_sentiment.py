from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .config_store import ConfigStore
from .models import WatchItem, iso_utc, utc_now
from .stock_mentions import social_stock_text, social_aliases, GENERIC_ASSET_NAMES
from .research_ledger import ResearchLedger, engine_fingerprint
from .public_discussions import WEB_SOURCES, PublicDiscussionClient, WebSourceError, hackernews_queries, public_source_interval
from .social_discovery import SocialDiscoveryCatalog
from .social_opinions import analyze_opinion
from functools import lru_cache
from .trump_monitor import XCredentialStore, _unprotect_windows, build_trump_factor


PLATFORMS = {
    "xiaohongshu": {"name": "小红书", "domains": ("xiaohongshu.com", "xhslink.com"), "mode": "feed"},
    "douyin": {"name": "抖音", "domains": ("douyin.com", "iesdouyin.com"), "mode": "feed"},
    "x": {"name": "X", "domains": ("x.com", "twitter.com"), "mode": "api"},
    "threads": {"name": "Threads", "domains": ("threads.net", "threads.com"), "mode": "api"},
    **WEB_SOURCES,
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS social_posts (
 platform TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, url TEXT NOT NULL,
 author TEXT NOT NULL, published_at TEXT NOT NULL, collected_at TEXT NOT NULL,
 metrics TEXT NOT NULL, origin TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(platform,id)
);
CREATE INDEX IF NOT EXISTS social_time ON social_posts(published_at);
CREATE TABLE IF NOT EXISTS social_runs (
 platform TEXT NOT NULL, checked_at TEXT NOT NULL, status TEXT NOT NULL,
 message TEXT NOT NULL, window_start TEXT, window_end TEXT, coverage TEXT NOT NULL,
 query_signature TEXT NOT NULL DEFAULT '', diagnostics TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(platform,checked_at)
);
"""


def strict_time(value: Any) -> datetime:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        raise ValueError("请提供有效的帖子发布时间") from None
    if parsed.tzinfo is None:
        raise ValueError("发布时间必须带时区")
    return parsed.astimezone(timezone.utc)


@lru_cache(maxsize=8192)
def directional_opinion(text: str) -> dict | None:
    analysis = analyze_opinion(text)
    return analysis if analysis["label"] in {"看多", "看空"} and analysis["score"] else None


def normalize_post(raw: dict, platform: str, *, origin: str = "import", now: datetime | None = None) -> dict:
    now = now or utc_now()
    if platform not in PLATFORMS or not isinstance(raw, dict):
        raise ValueError("不支持的社交平台或帖子格式")
    text = str(raw.get("text") or raw.get("title") or "").strip()
    if not text or len(text) > 12000:
        raise ValueError("帖子文本不能为空且不能超过 12000 字")
    published = strict_time(raw.get("published_at") or raw.get("created_at") or raw.get("timestamp"))
    if published > now + timedelta(minutes=5):
        raise ValueError("帖子发布时间在未来")
    url = str(raw.get("url") or raw.get("permalink") or "").strip()
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or not any(host == d or host.endswith("."+d) for d in PLATFORMS[platform]["domains"]):
        raise ValueError("原帖链接必须属于所选平台，且使用 HTTPS")
    identity = str(raw.get("id") or hashlib.sha256((host + parsed.path).encode()).hexdigest())
    raw_metrics = raw.get("metrics", raw.get("public_metrics", {}))
    if not isinstance(raw_metrics, dict):
        raise ValueError("互动量必须是一个数据对象")
    metrics = {}
    for key, value in raw_metrics.items():
        if key in {"like_count", "reply_count", "repost_count", "retweet_count", "quote_count", "view_count"}:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError("互动量必须是非负数") from None
            if not math.isfinite(number) or number < 0:
                raise ValueError("互动量必须是非负数")
            metrics[key] = min(int(number), 10**12)
    return {"platform": platform, "id": identity[:200], "text": text, "url": url,
            "author": str(raw.get("author") or raw.get("username") or raw.get("author_id") or "")[:200],
            "published_at": iso_utc(published), "collected_at": iso_utc(now), "metrics": metrics, "origin": origin,
            "context": dict(raw.get("context") or {}) if origin == "web" else {}}


def opinion_text(post: dict) -> str:
    text = post.get("context", {}).get("analysis_text", post["text"]) if post["origin"] == "web" else post["text"]
    return _opinion_body(post["platform"], text)


@lru_cache(maxsize=4096)
def _opinion_body(platform: str, text: str) -> str:
    if platform == "hackernews":
        # Product purchases, phone calls and market share are not securities opinions.
        finance = r"\b(?:stock price|stock market|share price|stocks?|shareholders?|investors?|dividends?|earnings|valuation|portfolio|market cap|revenue|profits?|bullish|bearish|undervalued|overvalued)\b|\$[A-Z]{1,5}\b"
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
        return "\n".join(part.group() for part in re.finditer(r"[^\n.!?]+[.!?]?", body) if re.search(finance, part.group(), re.I))
    return text


def post_link_key(post: dict) -> str:
    parsed = urllib.parse.urlsplit(post["url"])
    if post["platform"] in WEB_SOURCES:
        # Forum replies share a topic path; their post fragments identify different authors' replies.
        if re.fullmatch(r"post_\d+", parsed.fragment):
            return parsed.path.rstrip("/")+"#"+parsed.fragment
        return parsed.path.rstrip("/")+"?"+parsed.query+"#"+parsed.fragment
    return parsed.path.rstrip("/")


class SocialStore:
    def __init__(self, path: Path, ledger: ResearchLedger | None = None):
        self.path = path
        self.ledger = ledger
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.executescript(SCHEMA)
            if "context" not in {r[1] for r in db.execute("PRAGMA table_info(social_posts)")}:
                db.execute("ALTER TABLE social_posts ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
            if "query_signature" not in {r[1] for r in db.execute("PRAGMA table_info(social_runs)")}:
                db.execute("ALTER TABLE social_runs ADD COLUMN query_signature TEXT NOT NULL DEFAULT ''")
            if "diagnostics" not in {r[1] for r in db.execute("PRAGMA table_info(social_runs)")}:
                db.execute("ALTER TABLE social_runs ADD COLUMN diagnostics TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_posts(self, posts: list[dict]) -> int:
        added = 0
        if self.ledger:
            for post in posts:
                self.ledger.observe("social",f"{post['platform']}:{post['id']}",
                                    {k:v for k,v in post.items() if k != "collected_at"},published_at=post["published_at"])
        with self._session() as db:
            for p in posts:
                exists = db.execute("SELECT 1 FROM social_posts WHERE platform=? AND id=?", (p["platform"], p["id"])).fetchone()
                db.execute("""INSERT INTO social_posts (platform,id,text,url,author,published_at,collected_at,metrics,origin,context)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform,id) DO UPDATE SET text=excluded.text, url=excluded.url,
                    author=excluded.author, metrics=excluded.metrics, published_at=excluded.published_at,
                    collected_at=excluded.collected_at, origin=excluded.origin, context=excluded.context""",
                    (p["platform"], p["id"], p["text"], p["url"], p["author"], p["published_at"], p["collected_at"], json.dumps(p["metrics"]), p["origin"], json.dumps(p.get("context") or {})))
                added += not bool(exists)
        return added

    def save_run(self, run: dict) -> None:
        with self._session() as db:
            db.execute("INSERT OR REPLACE INTO social_runs (platform,checked_at,status,message,window_start,window_end,coverage,query_signature,diagnostics) VALUES (?,?,?,?,?,?,?,?,?)",
                       (run["platform"], run["checked_at"], run["status"], run["message"], run.get("window_start"), run.get("window_end"), json.dumps(run.get("coverage") or []), run.get("query_signature") or "", json.dumps(run.get("diagnostics") or {})))
            cutoff = iso_utc(utc_now()-timedelta(days=15))
            db.execute("DELETE FROM social_posts WHERE published_at < ?", (cutoff,))
            db.execute("DELETE FROM social_runs WHERE checked_at < ?", (cutoff,))

    def read(self) -> tuple[list[dict], list[dict]]:
        with self._session() as db:
            db.row_factory = sqlite3.Row
            posts = [dict(r) for r in db.execute("SELECT * FROM social_posts ORDER BY published_at DESC")]
            runs = [dict(r) for r in db.execute("SELECT * FROM social_runs ORDER BY checked_at DESC")]
        for p in posts:
            p["metrics"] = json.loads(p["metrics"])
            p["context"] = json.loads(p["context"])
        for r in runs:
            r["coverage"] = json.loads(r["coverage"])
            r["diagnostics"] = json.loads(r["diagnostics"])
        return posts, runs


class SocialTokenStore(XCredentialStore):
    def __init__(self, path: Path, environment: str):
        super().__init__(path)
        self.environment = environment

    def source(self) -> str:
        return "environment" if os.environ.get(self.environment, "").strip() else "windows_dpapi" if self.path.exists() else "none"

    def load(self) -> str:
        token = os.environ.get(self.environment, "").strip()
        if not token and self.path.exists():
            token = _unprotect_windows(self.path.read_bytes()).decode("utf-8")
        return re.sub(r"^Bearer\s+", "", token, flags=re.I).strip()


class SourceError(RuntimeError):
    def __init__(self, message: str, retry_seconds: int = 300):
        super().__init__(message)
        self.retry_seconds = retry_seconds


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError("数据源发生重定向，请更新连接地址")


def get_json(url: str, token: str = "") -> dict:
    headers = {"User-Agent": "QuantifySocial/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url, headers=headers), timeout=12) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise SourceError("数据源响应超过 2 MB")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise SourceError("数据源返回格式不正确")
        if result.get("error") or result.get("errors"):
            raise SourceError("平台返回错误，请检查授权范围与余额", 900)
        return result
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            try:
                retry = max(300, min(86400, int(exc.headers.get("Retry-After") or 900)))
            except ValueError:
                retry = 900
            raise SourceError("平台限流，已暂停请求并等待重试", retry) from None
        raise SourceError(f"连接失败（HTTP {exc.code}），请检查授权与接口权限", 900) from None
    except (OSError, ValueError) as exc:
        raise SourceError("网络连接或数据解析失败，稍后重试") from None


def matching_signature(watches, keywords):
    values = [(w.market, w.symbol, social_aliases(w, keywords.get(f"{w.market}:{w.symbol}", ()))) for w in watches]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def fetch_x(watchlist: tuple[WatchItem, ...], token: str, now: datetime, *, stock_keywords: dict | None = None) -> tuple[list[dict], list[str]]:
    groups: list[tuple[list[str], list[str]]] = []
    terms, keys = [], []
    for watch in watchlist:
        key = f"{watch.market}:{watch.symbol}"
        names = social_aliases(watch, (stock_keywords or {}).get(key, ()))
        aliases = [re.sub(r'[^\w\s.-]', '', name) for name in names]
        full_terms = [f'${watch.symbol}'] + [f'"{name}"' for name in aliases if name]
        chosen = []
        for candidate in full_terms:
            if len(" OR ".join(chosen + [candidate])) > 380:
                break
            chosen.append(candidate)
        term = "(" + " OR ".join(chosen) + ")"
        if len(" OR ".join(terms + [term])) > 450 and terms:
            groups.append((terms, keys))
            terms, keys = [], []
        terms.append(term)
        if len(chosen) == len(full_terms) and all(a == b for a, b in zip(names, aliases)):
            keys.append(key)
    if terms:
        groups.append((terms, keys))
    posts, coverage = [], []
    for terms, keys in groups:
        params = {"query": "(" + " OR ".join(terms) + ") -is:retweet", "max_results": 100,
                  "start_time": (now-timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end_time": (now-timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "tweet.fields": "created_at,author_id,public_metrics", "sort_order": "recency"}
        for _ in range(2):
            data = get_json("https://api.x.com/2/tweets/search/recent?"+urllib.parse.urlencode(params), token)
            for row in data.get("data") or []:
                posts.append(normalize_post({**row, "url": f'https://x.com/i/status/{row["id"]}'}, "x", origin="api", now=now))
            next_token = (data.get("meta") or {}).get("next_token")
            if not next_token:
                coverage.extend(keys)
                break
            params["next_token"] = next_token
    return posts, coverage


def fetch_threads(watchlist: tuple[WatchItem, ...], token: str, now: datetime) -> tuple[list[dict], list[str]]:
    posts = []
    for watch in watchlist:
        params = {"q": watch.name, "search_type": "RECENT", "limit": 50,
                  "fields": "id,text,timestamp,permalink,username"}
        data = get_json("https://graph.threads.net/v1.0/keyword_search?"+urllib.parse.urlencode(params), token)
        for row in data.get("data") or []:
            if row.get("text"):
                posts.append(normalize_post(row, "threads", origin="api", now=now))
    # Keyword search is sampled; an empty result cannot establish zero platform discussion.
    return posts, []


def opinion_signal_summary(posts: list[dict], now: datetime, hours: int) -> dict:
    accounts = Counter((p["platform"], p["author"]) for p in posts if p["author"])
    platforms = {p["platform"] for p in posts}
    count = len(posts)
    concentration = max(accounts.values(), default=0)/count if count else None
    recent = [p for p in posts if strict_time(p["published_at"]) > now-timedelta(hours=1)]
    previous = [p for p in posts if now-timedelta(hours=2) < strict_time(p["published_at"]) <= now-timedelta(hours=1)]
    recent_accounts = {(p["platform"], p["author"]) for p in recent if p["author"]}
    burst = hours >= 2 and len(previous) >= 3 and len(recent) >= 6 and len(recent) >= len(previous)*2 and len(recent_accounts) >= 3
    if count >= 10 and len(accounts) >= 5 and len(platforms) >= 2 and concentration <= .4:
        level, label = 2, "跨来源表态"
    elif count >= 5 and len(accounts) >= 3 and concentration <= .5:
        level, label = 1, "多账号表态"
    else:
        level, label = 0, "零星线索" if count else "无有效观点"
    risks = []
    if count and len(accounts) < 3:
        risks.append("可识别账号不足 3 个")
    if count and len(platforms) == 1:
        risks.append("仅单一来源")
    if concentration is not None and concentration > .5:
        risks.append("观点集中于单个账号")
    if count and sum(accounts.values()) < count:
        risks.append("部分帖子缺少作者信息")
    if len(platforms) > 1:
        risks.append("跨平台账号未验证为独立个人")
    reasons = {"bullish": [], "bearish": []}
    seen = set()
    for post in sorted(posts, key=lambda p: p["published_at"], reverse=True):
        side = "bullish" if post["analysis"]["score"] > 0 else "bearish"
        for signal in post["analysis"].get("signals", []):
            key = (side, signal["excerpt"])
            if key in seen or len(reasons[side]) >= 3:
                continue
            seen.add(key)
            reasons[side].append({**signal, "url": post["url"], "author": post["author"],
                                  "platform": post["platform"], "published_at": post["published_at"]})
    return {"level": level, "label": label, "risks": risks, "reasons": reasons,
            "author_concentration_pct": round(concentration*100, 1) if concentration is not None else None,
            "recent_hour": len(recent), "previous_hour": len(previous) if hours >= 2 else None,
            "hour_change_pct": round((len(recent)/len(previous)-1)*100, 1) if hours >= 2 and len(previous) >= 3 else None,
            "burst": burst, "burst_label": "样本升温" if burst else "基线不足" if len(previous) < 3 else "未见明显升温"}


def build_social_dashboard(posts: list[dict], runs: list[dict], watches: tuple[WatchItem, ...], *, hours: int = 24,
                           platform: str = "all", now: datetime | None = None, stock_keywords: dict | None = None) -> dict:
    now = now or utc_now()
    cutoff, previous_cutoff = now-timedelta(hours=hours), now-timedelta(hours=hours*2)
    selected_platforms = list(PLATFORMS) if platform == "all" else [platform]
    rows = []
    all_evidence = {}
    all_post_ids = set()
    for watch in watches:
        key = f"{watch.market}:{watch.symbol}"
        matched, previous = [], []
        seen = set()
        seen_links = set()
        discussion_seen, discussion_links = set(), set()
        discussion_count = 0
        duplicate_count = 0
        for post in posts:
            when = strict_time(post["published_at"])
            if post["platform"] not in selected_platforms or not previous_cutoff <= when <= now:
                continue
            text = social_stock_text(opinion_text(post), watch, additional=(stock_keywords or {}).get(key, ()))
            if not text:
                continue
            signature = (post["platform"], when >= cutoff, re.sub(r"\s+|https?://\S+", "", post["text"].lower()))
            link_key = (post["platform"], when >= cutoff, post_link_key(post))
            # Keep raw discussion coverage for cold-stock checks even when opinions are hidden.
            if when >= cutoff and signature not in discussion_seen and link_key not in discussion_links:
                discussion_count += 1
                discussion_seen.add(signature)
                discussion_links.add(link_key)
            analysis = directional_opinion(text)
            if analysis is None:
                continue
            if signature in seen or link_key in seen_links:
                duplicate_count += when >= cutoff
                continue
            seen.add(signature)
            seen_links.add(link_key)
            analyzed = {**post, "analysis": analysis, "stock_key": key}
            (matched if when >= cutoff else previous).append(analyzed)
        counts = Counter(p["platform"] for p in matched)
        authors = {(p["platform"], p["author"]) for p in matched if p["author"]}
        covered = []
        for source in selected_platforms:
            latest_run = next((r for r in runs if r["platform"] == source), {})
            if latest_run.get("status") != "connected":
                continue
            for run in runs:
                if run["platform"] != source or run["status"] != "connected" or key not in run["coverage"]:
                    continue
                if not run.get("window_start") or not run.get("window_end"):
                    continue
                if strict_time(run["window_start"]) <= cutoff and strict_time(run["window_end"]) >= now-timedelta(minutes=15):
                    covered.append(source)
                    break
        author_weights = defaultdict(float)
        weighted, denominator = 0., 0.
        for p in matched:
            score = p["analysis"]["score"]
            if not score:
                continue
            author = (p["platform"], p["author"] or "__unknown__")
            weight = min(1., max(0., 3.-author_weights[author])) * .5**((now-strict_time(p["published_at"])).total_seconds()/43200)
            author_weights[author] += 1
            weighted += score * weight
            denominator += weight
        bullish = sum(p["analysis"]["score"] > 0 for p in matched)
        bearish = sum(p["analysis"]["score"] < 0 for p in matched)
        evidence = sorted(matched, key=lambda p:p["published_at"], reverse=True)[:30]
        for p in evidence:
            all_evidence.setdefault((p["platform"], p["id"]), p)
        count = len(matched)
        signal = opinion_signal_summary(matched, now, hours)
        effective_count = sum(min(3, value) for value in author_weights.values())
        all_post_ids.update((p["platform"], p["id"]) for p in matched)
        score = round(weighted/denominator*100, 1) if denominator else None
        rows.append({"key": key, "market": watch.market, "symbol": watch.symbol, "name": watch.name,
                     "mentions": count, "authors": len(authors), "platform_counts": dict(counts), "covered_platforms": covered,
                     "discussion_count": discussion_count,
                     "signal": signal,
                     "score": score, "bullish": bullish, "bearish": bearish, "neutral": count-bullish-bearish,
                     "previous_mentions": len(previous), "change_pct": round((count/len(previous)-1)*100,1) if previous else None,
                     "change_label": "样本环比" if previous else "基线不足", "duplicates": duplicate_count,
                     "evidence": evidence, "latest_at": evidence[0]["published_at"] if evidence else None,
                     "raw_heat": math.log1p(effective_count)*.7 + math.log1p(len(authors))*.3})
    peak = max((r["raw_heat"] for r in rows), default=0)
    for row in rows:
        raw_heat = row.pop("raw_heat")
        row["heat"] = round(raw_heat/peak*100,1) if peak and (row["mentions"] or row["covered_platforms"]) else None
        if row["mentions"] == 0:
            row["attention"] = "样本冷清" if row["covered_platforms"] else "覆盖不足"
        else:
            row["attention"] = row["signal"]["label"]
        row["quality"] = row["signal"]["label"] if row["mentions"] else "已检索" if row["covered_platforms"] else "未知"
        row["conclusion"] = (
            f"{row['mentions']} 条明确观点、{row['authors']} 个可识别账号；看多 {row['bullish']} / 看空 {row['bearish']}。"
            if row["mentions"] else "当前样本暂无明确看多或看空观点。" if row["discussion_count"] else "已完成部分来源检索，所采集样本内暂无讨论。" if row["covered_platforms"] else "缺少有效检索覆盖，暂不能判断是否冷门。")
    rows.sort(key=lambda r:(r["heat"] or 0,r["mentions"],r["authors"]), reverse=True)
    hot = [r for r in rows if r["mentions"]]
    cold = sorted([r for r in rows if r["covered_platforms"] and r["discussion_count"] <= 2
                   and (r["mentions"] or not r["discussion_count"])], key=lambda r:r["discussion_count"])
    return {"generated_at": iso_utc(now), "hours": hours, "platform": platform, "opinion_scope": "directional", "stocks": rows,
            "hot": [r["key"] for r in hot[:10]], "cold": [r["key"] for r in cold[:10]],
            "summary": {"stock_count": len(rows), "discussed_count": len(hot), "cold_count": len(cold),
                        "post_count": len(all_post_ids),
                        "unknown_count": sum(r["attention"] == "覆盖不足" for r in rows)},
            "posts": sorted(all_evidence.values(), key=lambda p:p["published_at"], reverse=True)[:100],
            "method": "明确观点规则 v3；热度与情绪限制同账号重复贡献。样本级别和升温仅描述已采集观点，不是涨跌概率；不同账号未必是独立个人。"}


class SocialSentimentMonitor:
    def __init__(self, config_path: str | Path, ledger: ResearchLedger | None = None, *, trump_snapshot=None):
        self.trump_snapshot = trump_snapshot
        self.config = ConfigStore(config_path)
        root = Path(config_path).resolve().parent
        self.ledger = ledger or ResearchLedger(root / "data" / "research.db")
        self.store = SocialStore(root / "data" / "social.db",self.ledger)
        self._opinion_version = engine_fingerprint("social_sentiment.py","social_opinions.py","stock_mentions.py","public_discussions.py")
        self.tokens = {key: SocialTokenStore(root / "data" / ("x_api_token.bin" if key == "x" else f"social_{key}_token.bin"),
                                           "X_BEARER_TOKEN" if key == "x" else f"{key.upper()}_ACCESS_TOKEN") for key in PLATFORMS}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._worker = None
        self._refreshing = False
        self._next_allowed: dict[str, datetime] = {}
        self._last_error = None
        self.web_client = PublicDiscussionClient(root / "data" / "social_web_cache.db")
        self.discovery_catalog = SocialDiscoveryCatalog(root)

    def settings(self) -> dict:
        return {"enabled":True,"interval_seconds":60,"sources":{},"web_sources":[],"web_interval_seconds":300,"stock_keywords":{},
                **(self.config.read().get("social_sentiment") or {})}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="social-sentiment", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in (self._thread, self._worker):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)

    def _configured(self, platform: str, settings: dict) -> bool:
        if platform in WEB_SOURCES:
            return platform in settings.get("web_sources", [])
        source = (settings.get("sources") or {}).get(platform) or {}
        return bool(source.get("feed_url") or
                    (platform in {"x", "threads"} and self.tokens[platform].source() != "none"))

    def _allowed_at(self, platform: str, settings: dict) -> datetime:
        allowed = self._next_allowed.get(platform, datetime.min.replace(tzinfo=timezone.utc))
        if platform in WEB_SOURCES:
            cached = self.web_client.next_request_at(platform, public_source_interval(platform, settings.get("web_interval_seconds", 300)))
            if cached:
                allowed = max(allowed, cached)
        return allowed

    def trigger_refresh(self, platform: str | None = None, *, manual: bool = False) -> bool:
        if platform is not None and (not isinstance(platform, str) or platform not in PLATFORMS):
            raise ValueError("不支持的平台")
        with self._lock:
            if self._refreshing or self._stop.is_set():
                return False
            settings = self.settings()
            selected = [key for key in PLATFORMS if (platform is None or key == platform) and self._configured(key, settings)]
            if not selected:
                if manual:
                    raise ValueError("尚未接入可采集的数据源，请先启用网页采集，或配置平台授权连接")
                return False
            now = utc_now()
            if all(self._allowed_at(key, settings) > now for key in selected):
                if manual:
                    raise ValueError("请求间隔或限流等待期尚未结束，请按连接状态中的时间重试")
                return False
            self._refreshing = True
            self._worker = threading.Thread(target=self._refresh, args=(platform,), name="social-refresh", daemon=True)
            self._worker.start()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = 60
            try:
                settings = self.settings()
                if settings.get("enabled", True):
                    self.trigger_refresh()
                    if settings.get("web_sources"):
                        self.discovery_catalog.refresh()
                    now = utc_now()
                    due = [(self._allowed_at(key, settings)-now).total_seconds()
                           for key in PLATFORMS if self._configured(key, settings)]
                    interval = max(1, min(60, min(due))) if due else 60
                    if self._refreshing:
                        interval = 60
            except Exception:
                self._last_error = "采集调度异常，正在等待重试"
            self._wake.wait(interval)
            self._wake.clear()

    def configure(self, payload: dict) -> None:
        with self._lock:
            self._configure(payload)

    def _configure(self, payload: dict) -> None:
        settings = self.settings()
        if "stock_keywords" in payload:
            updates = payload["stock_keywords"]
            keys = {f"{w.market}:{w.symbol}" for w in load_config(self.config.path).watchlist if w.market != "FUND"}
            if not isinstance(updates, dict) or any(key not in keys for key in updates):
                raise ValueError("匹配词必须属于当前股票池中的股票")
            normalized = {}
            for key, words in updates.items():
                if not isinstance(words, list) or len(words) > 20 or any(not isinstance(word, str) or not 2 <= len(word.strip()) <= 60 for word in words):
                    raise ValueError("每只股票最多 20 个匹配词，每个词 2 至 60 字")
                if any(word.strip().upper() in GENERIC_ASSET_NAMES for word in words):
                    raise ValueError("请使用具体公司或证券名称，不使用黄金、BTC 等泛资产名称")
                normalized[key] = list(dict.fromkeys(word.strip() for word in words))
            settings["stock_keywords"] = {**settings.get("stock_keywords", {}), **normalized}
        if "web_interval_seconds" in payload:
            interval = int(payload["web_interval_seconds"])
            if interval < 300 or interval > 3600:
                raise ValueError("网页采样间隔须在 5 到 60 分钟之间")
            settings["web_interval_seconds"] = interval
        if "web_sources" in payload:
            sources = payload["web_sources"]
            if not isinstance(sources, list) or not all(isinstance(key, str) and key in WEB_SOURCES for key in sources):
                raise ValueError("请选择已核验的公开讨论来源")
            previous = set(settings.get("web_sources") or [])
            settings["web_sources"] = sorted(set(sources))
            for key in previous.symmetric_difference(sources):
                settings.setdefault("sources", {}).setdefault(key, {})["changed_at"] = iso_utc(utc_now())
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise ValueError("自动采集开关须为布尔值")
            settings["enabled"] = payload["enabled"]
        if "interval_seconds" in payload:
            interval = int(payload["interval_seconds"])
            if interval < 60 or interval > 3600:
                raise ValueError("采集间隔须在 60 到 3600 秒之间")
            settings["interval_seconds"] = interval
        platform = payload.get("platform")
        if platform:
            if not isinstance(platform, str) or platform in WEB_SOURCES:
                raise ValueError("网页来源使用独立采集设置，不接收授权令牌或自定义地址")
            if platform not in PLATFORMS:
                raise ValueError("不支持的平台")
            source = settings.setdefault("sources", {}).setdefault(platform, {})
            changed = False
            if "feed_url" in payload:
                url = str(payload["feed_url"]).strip()
                parsed = urllib.parse.urlsplit(url)
                if url and (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password):
                    raise ValueError("数据源地址须为 HTTPS，密钥请填在授权令牌栏")
                changed = url != (source.get("feed_url") or "")
                source["feed_url"] = url
            credentials = self.tokens[platform]
            if payload.get("clear_token") or str(payload.get("token") or "").strip():
                if credentials.source() == "environment":
                    raise ValueError("此令牌来自启动环境，须在环境中修改")
                if payload.get("clear_token"):
                    credentials.clear()
                else:
                    credentials.save(str(payload["token"]))
                changed = True
            if changed:
                source["changed_at"] = iso_utc(utc_now())
                self._next_allowed.pop(platform, None)
        self.config.set_value("social_sentiment", settings)
        self._wake.set()

    def import_posts(self, payload: dict) -> dict:
        raw_posts = payload.get("posts")
        if not isinstance(raw_posts, list) or not 1 <= len(raw_posts) <= 200:
            raise ValueError("每次导入 1 至 200 条帖子")
        if not all(isinstance(p, dict) for p in raw_posts):
            raise ValueError("每条帖子必须是一个数据对象")
        normalized = [normalize_post(p, str(p.get("platform") or payload.get("platform") or "")) for p in raw_posts]
        inserted = self.store.save_posts(normalized)
        self._record_opinions(normalized)
        return {"inserted": inserted, "received": len(normalized)}

    def _record_opinions(self, posts: list[dict]) -> None:
        watches = tuple(w for w in load_config(self.config.path).watchlist if w.market != "FUND")
        keywords = self.settings().get("stock_keywords", {})
        for post in posts:
            version = self.ledger.observe("social",f"{post['platform']}:{post['id']}",
                                          {k:v for k,v in post.items() if k != "collected_at"},published_at=post["published_at"])
            for watch in watches:
                key = f"{watch.market}:{watch.symbol}"
                text = social_stock_text(opinion_text(post),watch, additional=keywords.get(key, ()))
                if not text: continue
                started = utc_now()
                analysis = analyze_opinion(text)
                self.ledger.record_analysis(version["id"],f"{watch.market}:{watch.symbol}",analysis,
                    started_at=started,completed_at=utc_now(),engine="social-rules-v1",
                    metadata={"code_sha256":self._opinion_version,"matched_text":text,"matching_keywords":social_aliases(watch, keywords.get(key, ())),"evaluation_role":"observed_sentiment_not_timed_forecast"},
                    dedupe_key=f"social:{version['id']}:{watch.market}:{watch.symbol}:{self._opinion_version}:{hashlib.sha256(text.encode()).hexdigest()}")

    def _refresh(self, selected_platform: str | None = None) -> None:
        try:
            self._last_error = None
            watches = tuple(w for w in load_config(self.config.path).watchlist if w.market != "FUND")
            settings = self.settings()
            for platform, definition in PLATFORMS.items():
                if self._stop.is_set():
                    break
                if selected_platform is not None and platform != selected_platform:
                    continue
                if not self._configured(platform, settings):
                    continue
                now = utc_now()
                if self._allowed_at(platform, settings) > now:
                    continue
                source = (settings.get("sources") or {}).get(platform) or {}
                url = source.get("feed_url") or ""
                run = {"platform": platform, "checked_at": iso_utc(now), "status": "not_configured", "message": "等待授权数据源", "coverage": [], "diagnostics": {}}
                stage = "request"
                try:
                    token = self.tokens[platform].load() if platform not in WEB_SOURCES else ""
                    web_batch = None
                    if platform in WEB_SOURCES:
                        options = {"queries": hackernews_queries(watches)} if platform == "hackernews" else {}
                        web_batch = self.web_client.fetch(platform, interval=public_source_interval(platform, settings.get("web_interval_seconds", 300)), now=now, **options)
                        run["diagnostics"].update(web_batch.get("diagnostics") or {})
                        stage = "parse"
                        posts = [normalize_post(p, platform, origin="web", now=utc_now()) for p in web_batch["posts"]]
                        coverage = []
                    elif url:
                        payload = get_json(url, token)
                        stage = "parse"
                        raw = payload.get("posts")
                        if not isinstance(raw, list):
                            raise SourceError("数据源缺少 posts 列表")
                        posts = [normalize_post(p, platform, origin="feed", now=now) for p in raw[:2000]]
                        coverage = []
                    elif token and platform == "x":
                        posts, coverage = fetch_x(watches, token, now, stock_keywords=settings.get("stock_keywords", {}))
                        run["query_signature"] = matching_signature(watches, settings.get("stock_keywords", {}))
                    elif token and platform == "threads":
                        posts, coverage = fetch_threads(watches, token, now)
                    else:
                        continue
                    # Discard a response from a connection replaced while its request was in flight.
                    current_source = (self.settings().get("sources") or {}).get(platform) or {}
                    if current_source.get("changed_at") != source.get("changed_at"):
                        continue
                    stage = "storage"
                    inserted = self.store.save_posts(posts)
                    run["diagnostics"].update(received_count=len(posts), inserted_count=inserted)
                    stage = "analysis"
                    self._record_opinions(posts)
                    self._next_allowed[platform] = web_batch["next_request_at"] if web_batch else utc_now() + timedelta(seconds=settings.get("interval_seconds", 60))
                    run.update(status="connected", message=f"读取 {len(posts)} 条，新增 {inserted} 条",
                               window_start=iso_utc(now-timedelta(hours=24)), window_end=iso_utc(now-timedelta(seconds=15)), coverage=coverage)
                    if web_batch:
                        run.update(checked_at=web_batch["checked_at"], window_start=None, window_end=None,
                                   status=web_batch.get("status", "connected"),
                                   message=f"公开社区读取 {len(posts)} 条，新增 {inserted} 条；跳过 {web_batch['skipped']} 条无效记录")
                        if run["status"] == "partial":
                            run["message"] = f"已读取 {len(posts)} 条，新增 {inserted} 条；剩余查询自动续采"
                except Exception as exc:
                    current_source = (self.settings().get("sources") or {}).get(platform) or {}
                    if current_source.get("changed_at") != source.get("changed_at"):
                        continue
                    run.update(status=getattr(exc, "status", "error"), message=str(exc) if isinstance(exc, (SourceError, WebSourceError, ValueError)) else "采集失败，请检查数据源连接")
                    run["diagnostics"].update(failure_stage=getattr(exc, "stage", stage), latest_error=run["message"])
                    self._next_allowed[platform] = utc_now() + timedelta(seconds=getattr(exc, "retry_seconds", 300))
                self.store.save_run(run)
        except Exception:
            self._last_error = "采集任务未完成，请检查本机配置和数据存储；后台将自动重试"
        finally:
            with self._lock:
                self._refreshing = False
            self._wake.set()

    def snapshot(self, *, hours: int = 24, platform: str = "all", sample_scope: str = "all", sample_query: str = "") -> dict:
        if hours not in {1, 24, 168} or platform not in {*PLATFORMS, "all"}:
            raise ValueError("不支持的时间窗口或平台")
        if sample_scope not in {"all", "matched", "unmatched"} or not isinstance(sample_query, str) or len(sample_query) > 100:
            raise ValueError("不支持的样本筛选，搜索内容最多 100 字")
        posts, runs = self.store.read()
        all_watches = load_config(self.config.path).watchlist
        watches = tuple(w for w in all_watches if w.market != "FUND")
        settings = self.settings()
        keywords = settings.get("stock_keywords", {})
        now = utc_now()
        trump = self._trump_snapshot(all_watches, keywords, now)
        trump_ids = {str(post.get("id")) for post in trump["posts"]}
        ordinary_posts = [post for post in posts if not (post["platform"] == "x" and
                          (str(post.get("author") or "").lstrip("@").casefold() == "realdonaldtrump"
                           or str(post.get("id")) in trump_ids))]
        excluded_policy_count = len(posts) - len(ordinary_posts)
        posts = ordinary_posts
        cutoff = now-timedelta(hours=hours)
        sources, active_runs = [], []
        for key, definition in PLATFORMS.items():
            source = ((settings.get("sources") or {}).get(key) or {})
            url = source.get("feed_url") or ""
            configured = self._configured(key, settings)
            current_runs = [r for r in runs if r["platform"] == key and r["status"] != "not_configured"
                            and configured and (not source.get("changed_at") or
                                                strict_time(r["checked_at"]) > strict_time(source["changed_at"]))]
            current_signature = matching_signature(watches, keywords)
            active_runs.extend({**r, "coverage": r["coverage"] if r.get("query_signature") == current_signature else []} for r in current_runs)
            latest = next(iter(current_runs), {})
            success = next((r for r in current_runs if r["status"] == "connected"), {})
            received = next((r for r in current_runs if r.get("diagnostics", {}).get("received_count", 0) > 0), {})
            status = latest.get("status", "waiting") if configured else "not_configured"
            last = latest.get("checked_at")
            interval = public_source_interval(key, settings.get("web_interval_seconds", 300)) if key in WEB_SOURCES else settings.get("interval_seconds", 60)
            if status in {"connected", "partial"} and last and (now-strict_time(last)).total_seconds() > max(900, interval*3):
                status = "stale"
            waiting_message = "授权数据源地址未配置" if key in {"xiaohongshu", "douyin"} else "平台授权令牌或数据源地址未配置"
            if key in WEB_SOURCES:
                waiting_message = "网页采集未启用"
            allowed = self._allowed_at(key, settings)
            next_request = iso_utc(allowed) if configured and allowed and allowed > now else None
            source_posts = [p for p in posts if p["platform"] == key and cutoff <= strict_time(p["published_at"]) <= now]
            health_runs = [r for r in current_runs if now-timedelta(hours=24) <= strict_time(r["checked_at"]) <= now]
            successes = sum(r["status"] == "connected" for r in health_runs)
            partials = sum(r["status"] == "partial" for r in health_runs)
            latest_failure = next((r for r in health_runs if r.get("diagnostics", {}).get("latest_error") or r["status"] in {"error", "blocked"}), {})
            failures = 0
            for recent in current_runs:
                if recent["status"] in {"connected", "partial"}:
                    break
                failures += 1
            sources.append({"key": key, "name": definition["name"], "mode": "feed" if url else definition["mode"],
                                      "configured": configured, "status": status,
                                      "message": (latest.get("message") or "连接已保存，尚未验证") if configured else waiting_message,
                                      "checked_at": last, "post_count": sum(p["platform"] == key for p in posts),
                                      "last_success_at": success.get("checked_at"), "window_post_count": len(source_posts),
                                      "last_received_at": received.get("checked_at"), "diagnostics": latest.get("diagnostics", {}),
                                      "credential_source": self.tokens[key].source() if key not in WEB_SOURCES else "not_required", "feed_url": url,
                                      "public_url": definition.get("url"), "scope": definition.get("scope"),
                                      "format": definition.get("format", "rss"),
                                      "terms_url": definition.get("terms_url"), "license": definition.get("license"), "license_url": definition.get("license_url"),
                                      "interval_seconds": interval,
                                      "health": {"attempts_24h": len(health_runs), "successes_24h": successes,
                                                 "partial_count_24h": partials,
                                                 "failure_count_24h": sum(r["status"] in {"error", "blocked"} or bool(r.get("diagnostics", {}).get("latest_error")) for r in health_runs),
                                                 "latest_error": latest_failure.get("diagnostics", {}).get("latest_error") or latest_failure.get("message"),
                                                 "latest_error_at": latest_failure.get("diagnostics", {}).get("latest_error_at") or latest_failure.get("checked_at"),
                                                 "failure_stage": latest_failure.get("diagnostics", {}).get("failure_stage"),
                                                 "success_rate": round(100*successes/len(health_runs), 1) if health_runs else None,
                                                 "consecutive_failures": failures,
                                                 "matched_count": sum(any(directional_opinion(social_stock_text(opinion_text(p), w, additional=keywords.get(f"{w.market}:{w.symbol}", ()))) for w in watches) for p in source_posts),
                                                 "latest_published_at": max((p["published_at"] for p in source_posts), default=None)},
                                      "next_request_at": next_request, "retry_at": next_request if status in {"error", "blocked"} else None})
        active_runs.sort(key=lambda r: r["checked_at"], reverse=True)
        result = build_social_dashboard(posts, active_runs, watches, hours=hours, platform=platform, now=now, stock_keywords=keywords)
        discovery_posts = [p for p in posts if (platform == "all" or p["platform"] == platform)
                           and now-timedelta(hours=hours*2) <= strict_time(p["published_at"]) <= now]
        watched = {(w.market, w.symbol) for w in watches}
        candidates = tuple(w for w in self.discovery_catalog.candidates([opinion_text(p) for p in discovery_posts])
                           if (w.market, w.symbol) not in watched)
        discovery = build_social_dashboard(discovery_posts, [], candidates, hours=hours, platform=platform, now=now)
        discovery["stocks"] = [row for row in discovery["stocks"] if row["mentions"]]
        discovery["summary"]["stock_count"] = len(discovery["stocks"])
        discovery["summary"]["unknown_count"] = sum(row["previous_mentions"] == 0 for row in discovery["stocks"])
        discovery["catalog"] = self.discovery_catalog.status()
        discovery["method"] = "股票池之外的明确观点候选；同账号贡献设上限，提供原文依据与样本质量。规则判断非大模型，账号未必相互独立，情绪与升温不是涨跌概率。"
        result["discovery"] = discovery
        result["trump"] = trump
        factors = {(item["market"], item["symbol"]): item for item in trump["factors"]}
        for stock in result["stocks"]:
            stock["trump_factor"] = factors.get((stock["market"], stock["symbol"]))
        result["summary"]["policy_posts_excluded"] = excluded_policy_count
        result["keyword_settings"] = [{"key": f"{w.market}:{w.symbol}", "name": w.name, "symbol": w.symbol,
                                       "base": list(social_aliases(w)), "custom": keywords.get(f"{w.market}:{w.symbol}", [])} for w in watches]
        result["sources"] = sources
        selected_sources = [s for s in sources if platform == "all" or s["key"] == platform]
        window_posts = [p for p in posts if (platform == "all" or p["platform"] == platform)
                        and cutoff <= strict_time(p["published_at"]) <= now]
        result["summary"].update(collected_count=len(window_posts), imported_count=sum(p["origin"] == "import" for p in window_posts))
        web_posts = [p for p in window_posts if p["origin"] == "web" and p["platform"] in WEB_SOURCES]
        result["summary"]["web_sample_count"] = len(web_posts)
        samples = []
        for post in web_posts:
            opinions = {}
            for watch in (*watches, *candidates):
                key = f"{watch.market}:{watch.symbol}"
                analysis = directional_opinion(social_stock_text(opinion_text(post), watch, additional=keywords.get(key, ())))
                if analysis:
                    opinions[key] = analysis
            if opinions:
                samples.append({**post, "stock_opinions": opinions,
                                "matched_stocks": [f"{w.market}:{w.symbol}" for w in watches if f"{w.market}:{w.symbol}" in opinions]})
        result["summary"]["web_directional_count"] = len(samples)
        result["summary"]["web_matched_count"] = sum(bool(p["matched_stocks"]) for p in samples)
        query = sample_query.strip().casefold()
        selected_samples = [p for p in samples if (sample_scope == "all" or bool(p["matched_stocks"]) == (sample_scope == "matched"))
                            and (not query or query in " ".join((p["text"], p.get("context", {}).get("title", ""), *p["stock_opinions"])).casefold())]
        result["web_samples"] = selected_samples[:30]
        result["sample_selection"] = {"scope": sample_scope, "query": sample_query, "total": len(selected_samples)}
        configured_count = sum(s["configured"] for s in sources)
        connected_count = sum(s["status"] == "connected" for s in sources)
        partial_count = sum(s["status"] == "partial" for s in sources)
        enabled = settings.get("enabled", True)
        if not configured_count:
            collection_state = "not_configured"
        elif self._refreshing:
            collection_state = "collecting"
        elif self._last_error:
            collection_state = "error"
        elif not enabled:
            collection_state = "paused"
        elif connected_count or partial_count:
            collection_state = "connected" if connected_count == configured_count else "partial"
        elif any(s["status"] in {"error", "blocked"} for s in sources):
            collection_state = "error"
        elif any(s["status"] == "stale" for s in sources):
            collection_state = "stale"
        else:
            collection_state = "waiting"
        result["runtime"] = {"refresh_in_progress": self._refreshing, "enabled": enabled,
                             "interval_seconds": settings.get("interval_seconds", 60), "state": collection_state,
                             "configured_count": configured_count, "connected_count": connected_count,
                             "partial_count": partial_count,
                             "web_sources": settings.get("web_sources", []), "web_interval_seconds": settings.get("web_interval_seconds", 300),
                             "web_connected_count": sum(s["mode"] == "web" and s["status"] == "connected" for s in sources),
                             "web_partial_count": sum(s["mode"] == "web" and s["status"] == "partial" for s in sources),
                             "web_source_count": len(WEB_SOURCES),
                             "platform_connected_count": sum(s["mode"] != "web" and s["status"] == "connected" for s in sources),
                             "last_error": self._last_error,
                             "last_attempt_at": max((s["checked_at"] for s in sources if s["checked_at"]), default=None),
                             "last_success_at": max((s["last_success_at"] for s in sources if s["last_success_at"]), default=None),
                             "can_refresh": not self._refreshing and any(s["configured"] and not s["next_request_at"] for s in selected_sources)}
        return result

    def _trump_snapshot(self, watches, keywords, now):
        try:
            snapshot = self.trump_snapshot() if self.trump_snapshot else {"posts": [], "runtime": {"configured": False, "connected": False, "status": "not_configured"}}
        except Exception:
            snapshot = {"posts": [], "runtime": {"configured": False, "connected": False, "status": "error", "message": "Trump 状态读取失败，不影响普通讨论统计"}}
        posts, seen = [], set()
        for post in snapshot.get("posts") or []:
            try:
                timestamp = strict_time(post.get("created_at"))
            except ValueError:
                continue
            key = str(post.get("id") or post.get("url") or "")
            if key and key not in seen and now-timedelta(hours=72) <= timestamp <= now:
                posts.append(post)
                seen.add(key)
        posts.sort(key=lambda item: item["created_at"], reverse=True)
        factors = []
        for watch in watches:
            factor = build_trump_factor(posts, market=watch.market, symbol=watch.symbol, name=watch.name,
                                        aliases=social_aliases(watch, keywords.get(f"{watch.market}:{watch.symbol}", ())), now=now)
            if not factor["post_count"]:
                factor = {**factor, "score": None, "direction": "unavailable", "reason": "暂无相关 Trump 帖文，不代表中性"}
            factors.append({"market": watch.market, "symbol": watch.symbol, "name": watch.name, **factor})
        global_factor = build_trump_factor(posts, market="US", symbol="SPY", name="US stocks", now=now)
        if not global_factor["post_count"]:
            global_factor = {**global_factor, "score": None, "direction": "unavailable"}
        return {"posts": posts, "factors": factors, "global_factor": global_factor,
                "runtime": snapshot.get("runtime") or {}, "window_hours": 72, "independent": True}
