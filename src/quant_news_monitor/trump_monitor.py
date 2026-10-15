from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import canonical_news_url, iso_utc, news_event_key, parse_datetime, utc_now
from .config_store import ConfigStore
from .sources import PUBLIC_POLICY_FEEDS, _fetch_rss, _parse_feed_datetime
from .research_ledger import ResearchLedger
from .news_transport import retry_delay


TRUMP_USERNAME = "realDonaldTrump"
TRUMP_POLL_INTERVAL_SECONDS = 15
TRUMP_POST_LIMIT = 30
TRUMP_FACTOR_LOOKBACK_HOURS = 72

THEME_LABELS = {
    "tariff": "关税政策",
    "china": "中国政策",
    "rates": "利率/Fed",
    "crypto": "加密货币",
    "gold": "黄金",
    "energy": "能源",
    "healthcare": "医疗监管",
    "defense": "国防支出",
    "geopolitics": "地缘风险",
    "fiscal": "税收/监管",
    "technology": "科技产业",
    "government": "政府运行",
}

# A positive theme signal means escalation/support for the named policy. The
# coefficient converts that policy stance into a target-specific market effect.
THEME_TARGET_COEFFICIENTS: dict[str, dict[str, float]] = {
    "tariff": {
        "broad": -0.25,
        "china": -0.90,
        "technology": -0.40,
        "semiconductor": -0.58,
        "domestic_industry": 0.20,
        "gold": 0.22,
        "crypto": -0.10,
    },
    "china": {
        "broad": -0.20,
        "china": -0.88,
        "technology": -0.36,
        "semiconductor": -0.52,
        "defense": 0.24,
        "gold": 0.28,
    },
    "rates": {
        "broad": 0.32,
        "technology": 0.48,
        "semiconductor": 0.45,
        "finance": -0.24,
        "gold": 0.74,
        "crypto": 0.68,
    },
    "crypto": {"broad": 0.08, "technology": 0.12, "crypto": 1.00},
    "gold": {"broad": 0.03, "gold": 1.00},
    "energy": {
        "broad": 0.10,
        "energy": 0.72,
        "clean_energy": -0.38,
        "domestic_industry": 0.16,
    },
    "healthcare": {"broad": -0.06, "healthcare": -0.78},
    "defense": {"broad": 0.06, "defense": 0.82, "technology": 0.12},
    "geopolitics": {
        "broad": -0.46,
        "china": -0.26,
        "defense": 0.54,
        "gold": 0.72,
        "crypto": -0.16,
        "energy": 0.28,
    },
    "fiscal": {
        "broad": 0.42,
        "technology": 0.30,
        "semiconductor": 0.28,
        "finance": 0.52,
        "domestic_industry": 0.42,
    },
    "technology": {
        "broad": 0.20,
        "china": 0.30,
        "technology": 0.68,
        "semiconductor": 0.78,
    },
    "government": {"broad": -0.42, "finance": -0.32, "gold": 0.26},
}

TARGET_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gold": ("gold", "bullion", "gdx", "gdxj", "gdxu", "黄金", "黃金"),
    "crypto": (
        "bitcoin",
        "btc",
        "crypto",
        "coinbase",
        "ethereum",
        "ether",
        "ethu",
        "比特币",
        "比特幣",
        "以太坊",
    ),
    "semiconductor": (
        "semiconductor",
        "chip",
        "micron",
        "broadcom",
        "intel",
        "samsung electronics",
        "wolfspeed",
        "silicon carbide",
        "photonics",
        "optics",
        "美光",
        "三星电子",
        "三星電子",
        "英特尔",
        "英特爾",
        "芯片",
        "晶片",
        "光模块",
        "光模組",
    ),
    "technology": (
        "technology",
        "tech",
        "apple",
        "tencent",
        "broadcom",
        "artificial intelligence",
        "ai ",
        "mags",
        "aipo",
        "苹果",
        "騰訊",
        "腾讯",
        "人工智能",
    ),
    "healthcare": (
        "healthcare",
        "health insurance",
        "unitedhealth",
        "pharma",
        "drug",
        "医疗",
        "醫療",
        "医药",
        "醫藥",
    ),
    "energy": (
        "energy",
        "oil",
        "gas",
        "nuclear",
        "constellation energy",
        "power infrastructure",
        "能源",
        "石油",
        "核电",
        "核電",
    ),
    "clean_energy": ("renewable", "solar", "wind power", "clean energy", "新能源"),
    "finance": (
        "bank",
        "financial",
        "morgan stanley",
        "berkshire",
        "insurance",
        "银行",
        "銀行",
        "保险",
        "保險",
    ),
    "defense": (
        "defense",
        "military",
        "drone",
        "aerospace",
        "spacex",
        "国防",
        "國防",
        "军工",
        "軍工",
        "无人机",
        "無人機",
        "航天",
    ),
    "domestic_industry": (
        "manufacturing",
        "industrial",
        "factory",
        "steel",
        "制造",
        "製造",
        "工业",
        "工業",
    ),
}


class XApiError(RuntimeError):
    def __init__(self, message: str, retry_seconds: float = 60):
        super().__init__(message)
        self.retry_seconds = retry_seconds


class XCredentialStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def source(self) -> str:
        if os.environ.get("X_BEARER_TOKEN", "").strip():
            return "environment"
        if self.path.exists() and self.path.stat().st_size > 0:
            return "windows_dpapi"
        return "none"

    def load(self) -> str:
        environment_token = os.environ.get("X_BEARER_TOKEN", "").strip()
        if environment_token:
            return _normalize_token(environment_token)
        if not self.path.exists():
            return ""
        encrypted = self.path.read_bytes()
        if not encrypted:
            return ""
        return _normalize_token(_unprotect_windows(encrypted).decode("utf-8"))

    def save(self, token: str) -> None:
        normalized = _normalize_token(token)
        if len(normalized) < 20:
            raise ValueError("X API Bearer Token 格式过短")
        encrypted = _protect_windows(normalized.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class TrumpMonitor:
    def __init__(
        self,
        config_path: str | Path,
        *,
        poll_interval_seconds: int = TRUMP_POLL_INTERVAL_SECONDS,
        timeout_seconds: int = 15,
        credential_store: XCredentialStore | None = None,
        ledger: ResearchLedger | None = None,
        public_enabled: bool | None = None,
    ):
        root = Path(config_path).resolve().parent
        self.ledger = ledger
        settings = ConfigStore(config_path).read() if Path(config_path).exists() else {}
        self.public_enabled = bool(settings.get("trump_public_enabled", True)) if public_enabled is None else public_enabled
        self.public_interval_seconds = max(300, int(settings.get("trump_public_interval_seconds", 300)))
        self._public_sources: dict[str, dict[str, Any]] = {}
        self.poll_interval_seconds = max(15, poll_interval_seconds)
        self.timeout_seconds = timeout_seconds
        self.credentials = credential_store or XCredentialStore(
            root / "data" / "x_api_token.bin"
        )
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._refreshing = False
        self._force_refresh = True
        self._next_refresh_at = 0.0
        self._not_before = 0.0
        self._last_full_refresh_at = 0.0
        self._user_id = ""
        self._posts: dict[str, dict[str, Any]] = {}
        self._status = "not_configured"
        self._message = "尚未配置 X API Bearer Token"
        self._last_checked_at: str | None = None
        self._last_updated_at: str | None = None
        self._errors: list[str] = []
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset_at: str | None = None
        if self.public_enabled:
            self._status = "waiting"
            self._message = "公开政策 RSS 已启用，等待首次采集；无需 X Token，非实时流"
        if self.public_enabled and self.ledger:
            with self.ledger.session() as db:
                cached = db.execute("SELECT id,payload,observed_at FROM input_versions WHERE kind='trump' AND identity LIKE 'public:%' "
                                    "AND id IN (SELECT MAX(id) FROM input_versions WHERE kind='trump' GROUP BY identity) "
                                    "ORDER BY published_at DESC LIMIT ?", (TRUMP_POST_LIMIT,)).fetchall()
                schedules = db.execute("SELECT payload FROM input_versions WHERE kind='trump_public_schedule' "
                                       "AND id IN (SELECT MAX(id) FROM input_versions WHERE kind='trump_public_schedule' GROUP BY identity)").fetchall()
            for row in cached:
                post = json.loads(row["payload"])
                self._posts[post["id"]] = {**post, "input_version_id": row["id"], "collected_at": row["observed_at"], "cached": True}
            self._public_sources = {state["feed_url"]: state for row in schedules if (state := json.loads(row["payload"]))}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="trump-x-monitor",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def trigger_refresh(self) -> bool:
        with self._lock:
            if time.monotonic()<self._not_before: return False
            if self.public_enabled and (self._refreshing or time.monotonic() < self._next_refresh_at):
                return False
            self._force_refresh = True
            already_refreshing = self._refreshing
        self._wake_event.set()
        return not already_refreshing

    def save_token(self, token: str) -> None:
        if self.credentials.source() == "environment":
            raise ValueError("当前 Token 来自 X_BEARER_TOKEN 环境变量，请在启动环境中修改")
        self.credentials.save(token)
        if self.public_enabled:
            return
        with self._lock:
            self._user_id = ""
            self._status = "connecting"
            self._not_before = 0.0
            self._message = "正在验证 X API 连接"
            self._errors = []
        self.trigger_refresh()

    def clear_token(self) -> None:
        if self.credentials.source() == "environment":
            raise ValueError("当前 Token 来自 X_BEARER_TOKEN 环境变量，请在启动环境中清除")
        self.credentials.clear()
        if self.public_enabled:
            return
        with self._lock:
            self._user_id = ""
            self._posts = {}
            self._status = "not_configured"
            self._message = "尚未配置 X API Bearer Token"
            self._errors = []
            self._last_updated_at = None
            self._not_before = 0.0
        self.trigger_refresh()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            posts = sorted(
                (copy.deepcopy(item) for item in self._posts.values()),
                key=lambda item: str(item.get("created_at", "")),
                reverse=True,
            )[:TRUMP_POST_LIMIT]
            source = self.credentials.source()
            return {
                "posts": posts,
                "runtime": {
                    "username": TRUMP_USERNAME,
                    "configured": source != "none",
                    "credential_source": source,
                    "status": self._status,
                    "message": self._message,
                    "connected": self._status == "connected",
                    "refresh_in_progress": self._refreshing,
                    "poll_interval_seconds": self.public_interval_seconds if self.public_enabled else self.poll_interval_seconds,
                    "source_mode": "public_rss" if self.public_enabled else "x_api",
                    "public_enabled": self.public_enabled,
                    "public_sources": copy.deepcopy(list(self._public_sources.values())),
                    "real_time": False,
                    "freshness_note": "公开来源按发布节奏更新；本地检查不等于外部实时请求。",
                    "next_check_at": iso_utc(utc_now() + timedelta(seconds=max(0, self._next_refresh_at - time.monotonic()))),
                    "last_checked_at": self._last_checked_at,
                    "last_updated_at": self._last_updated_at,
                    "post_count": len(posts),
                    "errors": list(self._errors),
                    "rate_limit_remaining": self._rate_limit_remaining,
                    "rate_limit_reset_at": self._rate_limit_reset_at,
                },
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._refresh_once()
            except Exception as exc:
                with self._lock:
                    self._status = "error"
                    self._message = "Trump 动态刷新失败"
                    self._errors = [f"{type(exc).__name__}: {exc}"]
                    self._refreshing = False
                    self._next_refresh_at = time.monotonic() + self.poll_interval_seconds
            with self._lock:
                wait_seconds = max(1.0, self._next_refresh_at - time.monotonic())
            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()

    def _refresh_once(self) -> None:
        if self.public_enabled:
            self._refresh_public_once()
            return
        with self._lock:
            if self._refreshing:
                return
            now_monotonic = time.monotonic()
            if now_monotonic < self._not_before: return
            if (
                not self._force_refresh
                and self._next_refresh_at > now_monotonic
            ):
                return
            self._force_refresh = False
            self._refreshing = True

        checked_at = utc_now()
        next_delay = self.poll_interval_seconds
        try:
            token = self.credentials.load()
            if not token:
                with self._lock:
                    self._status = "not_configured"
                    self._message = "尚未配置 X API Bearer Token"
                    self._last_checked_at = iso_utc(checked_at)
                    self._errors = []
                return

            with self._lock:
                user_id = self._user_id
                full_refresh = (
                    time.monotonic() - self._last_full_refresh_at >= 10 * 60
                )
                since_id = "" if full_refresh else _latest_post_id(self._posts)
                self._status = "connecting"
                self._message = "正在读取 @realDonaldTrump"

            user_id, raw_posts, rate = _fetch_x_user_posts(
                token,
                user_id=user_id,
                since_id=since_id,
                timeout_seconds=self.timeout_seconds,
            )
            parsed = [_parse_x_post(item) for item in raw_posts]
            if self.ledger:
                for item in raw_posts:
                    self.ledger.observe("trump",str(item["id"]),item,published_at=item.get("created_at"))
            with self._lock:
                self._user_id = user_id
                changed = False
                for item in parsed:
                    changed = changed or self._posts.get(str(item["id"])) != item
                    self._posts[str(item["id"])] = item
                ordered = sorted(
                    self._posts.values(),
                    key=lambda item: str(item.get("created_at", "")),
                    reverse=True,
                )[:TRUMP_POST_LIMIT]
                self._posts = {str(item["id"]): item for item in ordered}
                self._status = "connected"
                self._message = "X API 已连接，正在高频监测"
                self._last_checked_at = iso_utc(checked_at)
                if changed:
                    self._last_updated_at = iso_utc(checked_at)
                if full_refresh:
                    self._last_full_refresh_at = time.monotonic()
                self._errors = []
                self._rate_limit_remaining = rate.get("remaining")
                self._rate_limit_reset_at = rate.get("reset_at")
                if rate.get("remaining")==0:
                    next_delay = max(self.poll_interval_seconds,retry_delay({"x-rate-limit-reset":str(parse_datetime(rate["reset_at"]).timestamp())})) if rate.get("reset_at") else 300
                    self._not_before = time.monotonic()+next_delay
        except Exception as exc:
            with self._lock:
                self._status = "error"
                self._message = _friendly_x_error(exc)
                self._last_checked_at = iso_utc(checked_at)
                self._errors = [f"{type(exc).__name__}: {exc}"]
                next_delay = max(self.poll_interval_seconds,getattr(exc,"retry_seconds",60))
                self._not_before = time.monotonic()+next_delay
        finally:
            with self._lock:
                self._refreshing = False
                self._next_refresh_at = time.monotonic() + next_delay

    def _refresh_public_once(self) -> None:
        with self._lock:
            if self._refreshing or time.monotonic() < self._next_refresh_at:
                return
            self._refreshing = True
        changed = False
        try:
            for publisher, feed_url in PUBLIC_POLICY_FEEDS:
                if self._stop_event.is_set():
                    break
                state = self._public_sources.get(feed_url, {})
                if state.get("next_check_at") and parse_datetime(state["next_check_at"]) > utc_now():
                    continue
                checked_at = iso_utc(utc_now())
                try:
                    rows = _fetch_rss(feed_url, publisher, self.timeout_seconds, cache_seconds=self.public_interval_seconds)
                    parsed = [post for row in rows if (post := _parse_public_post(row, feed_url))]
                    for post in parsed:
                        if self.ledger:
                            saved = self.ledger.observe("trump", post["id"], post, published_at=post["created_at"])
                            post = {**post, "input_version_id": saved["id"], "collected_at": saved["observed_at"]}
                        with self._lock:
                            previous = self._posts.get(post["id"], {})
                            changed = changed or previous.get("facts") != post["facts"]
                            self._posts[post["id"]] = post
                    state = {"publisher": publisher, "feed_url": feed_url, "last_checked_at": checked_at,
                             "last_success_at": checked_at, "failures": 0, "error": None, "matched_count": len(parsed)}
                    delay = self.public_interval_seconds
                except Exception as exc:
                    failures = state.get("failures", 0) + 1
                    delay = min(21600, self.public_interval_seconds * 2 ** min(failures - 1, 6))
                    provider_error = exc if isinstance(exc, urllib.error.HTTPError) else exc.__cause__
                    if isinstance(provider_error, urllib.error.HTTPError):
                        delay = max(delay, retry_delay(provider_error.headers, fallback=delay))
                        if provider_error.code in {401, 403}:
                            delay = max(86400, delay)
                        provider_error.close()
                    state = {**state, "publisher": publisher, "feed_url": feed_url, "last_checked_at": checked_at,
                             "failures": failures, "error": f"{type(exc).__name__}: {exc}"}
                state["next_check_at"] = iso_utc(utc_now() + timedelta(seconds=delay))
                with self._lock:
                    self._public_sources[feed_url] = state
                if self.ledger:
                    self.ledger.observe("trump_public_schedule", feed_url, state)
            with self._lock:
                ordered = sorted(self._posts.values(), key=lambda item: item["created_at"], reverse=True)[:TRUMP_POST_LIMIT]
                self._posts = {post["id"]: post for post in ordered}
                self._errors = [state["error"] for state in self._public_sources.values() if state.get("error")]
                success = any(state.get("last_success_at") and not state.get("error") for state in self._public_sources.values())
                self._status = "connected" if success else "error"
                self._message = ("公开媒体 RSS 按计划采集" if success else "公开媒体暂不可用，保留历史记录并退避")
                self._message += "；不是 Trump 原帖或实时流；市场影响是未校准的规则推断"
                self._last_checked_at = iso_utc(utc_now())
                if changed:
                    self._last_updated_at = self._last_checked_at
        finally:
            with self._lock:
                delays = [max(1, (parse_datetime(state["next_check_at"]) - utc_now()).total_seconds())
                          for state in self._public_sources.values() if state.get("next_check_at")]
                self._next_refresh_at = time.monotonic() + (min(delays) if delays else self.public_interval_seconds)
                self._refreshing = False


def _parse_public_post(raw: dict[str, Any], feed_url: str) -> dict[str, Any] | None:
    title, summary = str(raw.get("title") or ""), str(raw.get("summary") or "")
    text = f"{title}\n{summary}".strip()
    if not _contains_any(text, ("trump", "特朗普", "川普")):
        return None
    published = _parse_feed_datetime(raw.get("published_at"))
    age = (utc_now() - published).total_seconds()
    url = str(raw.get("url") or "")
    parsed_url = urllib.parse.urlsplit(url)
    if not 0 <= age <= TRUMP_FACTOR_LOOKBACK_HOURS * 3600 or parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        return None
    if parsed_url.hostname in {"news.google.com", "bing.com", "www.bing.com"}:
        return None
    analysis = analyze_trump_post(text)
    if not analysis["themes"]:
        return None
    # A media excerpt is evidence of a report, never a verified Trump quotation or enacted policy.
    facts = {"title": title, "summary": summary, "source": raw.get("source"), "url": url,
             "published_at": iso_utc(published), "feed_published_at": raw.get("published_at"), "feed_url": feed_url}
    ambiguous = _contains_any(text, ("denies", "denied", "rumor", "rumour", "could", "might", "if",
                                     "did not", "has not", "will not", "no plans", "may impose", "may raise",
                                     "否认", "传闻", "可能", "如果", "尚未"))
    analysis.update(kind="rule_inference", calibrated=False, evidence_status="reported_not_verified",
                    direction_eligible=not ambiguous,
                    limitation="仅对媒体标题和摘要做关键词推断，未核验原始政策全文，不是事实结论或收益概率。")
    if ambiguous:
        analysis.update(theme_signals={}, general_score=0, market_score=0, confidence=0,
                        summary="报道包含否认、传闻或条件措辞，仅保留证据，不计方向因子")
    return {"id": "public:" + hashlib.sha256(canonical_news_url(url).encode()).hexdigest(),
            "username": raw.get("source") or parsed_url.hostname, "source": raw.get("source"),
            "source_kind": "media_report", "text": text, "created_at": facts["published_at"],
            "published_at": facts["published_at"], "url": url, "facts": facts,
            "public_metrics": {}, "analysis": analysis}


def build_trump_factor(
    posts: list[dict[str, Any]],
    *,
    market: str,
    symbol: str,
    name: str,
    aliases: tuple[str, ...] | list[str] = (),
    sector: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    groups = _target_groups(market, symbol, name, aliases, sector)
    mention_terms = _mention_terms(symbol, name, aliases)
    contributions: list[dict[str, Any]] = []
    seen = set()

    for post in posts:
        created_at = _parse_feed_datetime(post.get("created_at"))
        age_hours = (now - created_at).total_seconds() / 3600.0
        if not 0 <= age_hours <= TRUMP_FACTOR_LOOKBACK_HOURS:
            continue
        facts = post.get("facts") or {}
        identity = news_event_key("US", "TRUMP", facts.get("title") or str(post.get("text", "")), "", iso_utc(created_at))
        if identity in seen:
            continue
        seen.add(identity)
        analysis = post.get("analysis") or analyze_trump_post(str(post.get("text", "")))
        if analysis.get("direction_eligible") is False:
            continue
        theme_signals = analysis.get("theme_signals") or {}
        direct_mention = any(
            _contains_term(str(post.get("text", "")), term) for term in mention_terms
        )
        effects: list[tuple[str, float, float]] = []
        for theme, raw_stance in theme_signals.items():
            stance = float(raw_stance or 0.0)
            coefficients = THEME_TARGET_COEFFICIENTS.get(str(theme), {})
            matching = [float(coefficients[group]) for group in groups if group in coefficients]
            if not matching or abs(stance) < 0.05:
                continue
            coefficient = max(matching, key=abs)
            effects.append((str(theme), stance * coefficient, abs(coefficient)))

        general_score = float(analysis.get("general_score") or 0.0)
        if direct_mention and abs(general_score) >= 0.05:
            effects.append(("direct", general_score * 0.92, 1.0))
        if not effects:
            continue

        effect = _clamp(
            sum(value for _, value, _ in effects) / max(1.0, math.sqrt(len(effects))),
            -1.0,
            1.0,
        )
        relevance = 1.0 if direct_mention else max(weight for _, _, weight in effects)
        recency = 0.5 ** (age_hours / 8.0)
        metrics = post.get("public_metrics") or {}
        engagement = sum(int(metrics.get(key) or 0) for key in metrics)
        engagement_weight = 1.0 + min(0.20, math.log10(engagement + 1.0) / 30.0)
        confidence = float(analysis.get("confidence") or 0.45)
        weight = recency * (0.45 + 0.55 * relevance) * confidence * engagement_weight
        contributions.append(
            {
                "score": effect,
                "weight": weight,
                "relevance": relevance,
                "confidence": confidence,
                "themes": [theme for theme, _, _ in effects if theme != "direct"],
                "post": post,
                "significance": abs(effect) * weight,
                "direct_mention": direct_mention,
            }
        )

    if not contributions:
        return _empty_factor()

    denominator = sum(item["weight"] for item in contributions)
    score = _clamp(
        sum(item["score"] * item["weight"] for item in contributions)
        / max(0.0001, denominator),
        -1.0,
        1.0,
    )
    lead = max(contributions, key=lambda item: item["significance"])
    themes = list(
        dict.fromkeys(
            theme
            for item in sorted(contributions, key=lambda value: value["significance"], reverse=True)
            for theme in item["themes"]
        )
    )[:3]
    relevance = max(float(item["relevance"]) for item in contributions)
    confidence = sum(item["confidence"] * item["weight"] for item in contributions) / max(
        0.0001, denominator
    )
    direction = "bullish" if score > 0.08 else "bearish" if score < -0.08 else "neutral"
    direction_copy = "偏多" if direction == "bullish" else "偏空" if direction == "bearish" else "中性"
    theme_copy = "、".join(THEME_LABELS.get(theme, theme) for theme in themes) or "直接提及"
    lead_post = lead["post"]
    return {
        "score": round(score, 4),
        "direction": direction,
        "relevance": round(relevance, 4),
        "confidence": round(_clamp(confidence, 0.0, 1.0), 4),
        "themes": themes,
        "theme_labels": [THEME_LABELS.get(theme, theme) for theme in themes],
        "post_count": len(contributions),
        "direct_mention": any(bool(item["direct_mention"]) for item in contributions),
        "reason": f"规则推断：{theme_copy}对该标的的近期综合影响{direction_copy}；非已验证市场影响",
        "score_kind": "uncalibrated_policy_signal",
        "evidence": [{"id": item["post"].get("id"), "url": item["post"].get("url"),
                      "published_at": item["post"].get("created_at"), "source_kind": item["post"].get("source_kind", "x_post"),
                      "facts": copy.deepcopy(item["post"].get("facts") or {"text": item["post"].get("text")}),
                      "inference": copy.deepcopy(item["post"].get("analysis"))} for item in contributions],
        "latest_post_id": str(lead_post.get("id", "")),
        "latest_post_url": str(lead_post.get("url", "")),
        "latest_post_at": str(lead_post.get("created_at", "")),
    }


def analyze_trump_post(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    signals: dict[str, float] = {}

    if _contains_any(normalized, ("tariff", "tariffs", "import duty", "import duties", "关税", "關稅", "进口税", "進口稅")):
        signals["tariff"] = _stance(
            normalized,
            ("increase", "impose", "additional", "raise", "加征", "加徵", "提高", "征收", "徵收"),
            ("remove", "lower", "reduce", "zero tariff", "trade deal", "撤销", "撤銷", "降低", "协议", "協議"),
            0.65,
        )
    if _contains_any(normalized, ("china", "chinese", "中国", "中國", "beijing", "北京")):
        signals["china"] = _stance(
            normalized,
            ("sanction", "ban", "restrict", "threat", "punish", "制裁", "禁令", "限制", "威胁", "威脅"),
            ("cooperate", "agreement", "deal", "talks", "合作", "协议", "協議", "会谈", "會談"),
            signals.get("tariff", 0.20),
        )
    if _contains_any(normalized, ("federal reserve", "the fed", "interest rate", "rates", "美联储", "聯儲", "利率")):
        signals["rates"] = _stance(
            normalized,
            ("cut", "lower", "reduce", "dovish", "降息", "降低"),
            ("raise", "higher", "hike", "hawkish", "加息", "提高"),
            0.0,
        )
    if _contains_any(normalized, ("bitcoin", "crypto", "digital asset", "btc", "比特币", "比特幣", "加密货币", "加密貨幣")):
        signals["crypto"] = _stance(
            normalized,
            ("support", "reserve", "embrace", "pro-crypto", "buy", "支持", "储备", "儲備", "购买", "購買"),
            ("ban", "crackdown", "fraud", "scam", "oppose", "禁令", "打击", "打擊", "欺诈", "詐騙"),
            _general_direction(normalized),
        )
    if _contains_any(normalized, ("gold", "bullion", "xau", "黄金", "黃金", "金价", "金價")):
        signals["gold"] = _stance(
            normalized,
            ("buy", "reserve", "strong", "rise", "购买", "購買", "储备", "儲備", "上涨", "上漲"),
            ("sell", "weak", "fall", "出售", "抛售", "下跌"),
            _general_direction(normalized),
        )
    if _contains_any(normalized, ("oil", "gas", "energy", "drill", "nuclear", "石油", "天然气", "天然氣", "能源", "钻探", "鑽探", "核电", "核電")):
        signals["energy"] = _stance(
            normalized,
            ("drill", "produce", "expand", "deregulate", "approve", "增产", "增產", "扩产", "擴產", "放松监管", "批准"),
            ("ban", "restrict", "phase out", "禁止", "限制", "淘汰"),
            0.25,
        )
    if _contains_any(normalized, ("healthcare", "drug price", "pharma", "insurer", "medicare", "医疗", "醫療", "药价", "藥價", "保险公司", "保險公司")):
        signals["healthcare"] = _stance(
            normalized,
            ("lower prices", "cut prices", "investigate", "most favored nation", "降价", "降價", "调查", "調查", "最惠国", "最惠國"),
            ("deregulate", "reduce oversight", "放松监管", "減少監管"),
            0.30,
        )
    if _contains_any(normalized, ("defense", "military", "army", "navy", "missile", "国防", "國防", "军队", "軍隊", "导弹", "導彈")):
        signals["defense"] = _stance(
            normalized,
            ("increase spending", "build", "expand", "strong military", "增加军费", "增加軍費", "扩军", "擴軍"),
            ("cut spending", "reduce forces", "削减军费", "削減軍費", "裁军", "裁軍"),
            0.35,
        )
    if _contains_any(normalized, ("war", "attack", "strike", "conflict", "sanction", "ceasefire", "peace", "战争", "戰爭", "袭击", "襲擊", "冲突", "衝突", "制裁", "停火", "和平")):
        signals["geopolitics"] = _stance(
            normalized,
            ("war", "attack", "strike", "escalate", "sanction", "战争", "戰爭", "袭击", "襲擊", "升级", "升級", "制裁"),
            ("peace", "ceasefire", "de-escalate", "agreement", "和平", "停火", "降温", "降溫", "协议", "協議"),
            0.35,
        )
    if _contains_any(normalized, ("tax cut", "tax hike", "deregulation", "regulation", "stimulus", "减税", "減稅", "加税", "加稅", "监管", "監管", "刺激")):
        signals["fiscal"] = _stance(
            normalized,
            ("tax cut", "deregulation", "stimulus", "减税", "減稅", "放松监管", "刺激"),
            ("tax hike", "more regulation", "加税", "加稅", "加强监管", "加強監管"),
            0.0,
        )
    if _contains_any(normalized, ("technology", "semiconductor", "chip", "artificial intelligence", " ai ", "科技", "半导体", "半導體", "芯片", "晶片", "人工智能")):
        signals["technology"] = _stance(
            normalized,
            ("invest", "support", "leadership", "manufacture", "投资", "投資", "支持", "领先", "領先", "制造", "製造"),
            ("export ban", "restrict", "block", "ban", "出口禁令", "限制", "封锁", "封鎖"),
            _general_direction(normalized) * 0.60,
        )
    if _contains_any(normalized, ("government shutdown", "debt default", "政府停摆", "政府停擺", "债务违约", "債務違約")):
        signals["government"] = _stance(
            normalized,
            ("shutdown", "default", "停摆", "停擺", "违约", "違約"),
            ("avoid", "reopen", "deal", "避免", "重开", "重開", "协议", "協議"),
            0.80,
        )

    general = _general_direction(normalized)
    broad_effects = [
        stance * THEME_TARGET_COEFFICIENTS.get(theme, {}).get("broad", 0.0)
        for theme, stance in signals.items()
    ]
    market_score = _clamp(
        (sum(broad_effects) / max(1.0, math.sqrt(len(broad_effects)))) + general * 0.20,
        -1.0,
        1.0,
    )
    themes = [theme for theme, stance in signals.items() if abs(stance) >= 0.05]
    confidence = _clamp(0.38 + len(themes) * 0.10 + (0.10 if abs(general) > 0 else 0.0), 0.35, 0.88)
    direction = "偏多" if market_score > 0.08 else "偏空" if market_score < -0.08 else "中性"
    theme_copy = "、".join(THEME_LABELS.get(theme, theme) for theme in themes) or "一般政治表态"
    return {
        "theme_signals": {key: round(value, 4) for key, value in signals.items()},
        "themes": themes,
        "theme_labels": [THEME_LABELS.get(theme, theme) for theme in themes],
        "general_score": round(general, 4),
        "market_score": round(market_score, 4),
        "confidence": round(confidence, 4),
        "summary": f"规则推断：识别到{theme_copy}，可能对整体风险偏好{direction}；未验证实际影响",
    }


def apply_trump_factor_to_asset_snapshot(
    snapshot: dict[str, Any],
    posts: list[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    contexts = {
        "GOLD": {"market": "GLOBAL", "symbol": "GOLD", "name": "Gold XAU USD"},
        "BTC": {"market": "GLOBAL", "symbol": "BTC", "name": "Bitcoin BTC crypto"},
    }
    factors = {
        key: build_trump_factor(posts, **context)
        for key, context in contexts.items()
    }
    horizon_weights = {"15m": 9.0, "1h": 7.0, "24h": 5.0}
    for row in result.get("probabilities", []):
        # Unsupported targets and unavailable quotes must not become synthetic 50/50 signals.
        if row.get("asset") not in contexts or row.get("available") is False:
            continue
        factor = factors.get(str(row.get("asset")), _empty_factor())
        base_up = float(row.get("up_probability") or 50.0)
        adjustment = float(factor["score"]) * horizon_weights.get(str(row.get("horizon")), 5.0)
        adjusted_up = round(_clamp(base_up + adjustment, 12.0, 88.0), 1)
        row["base_up_probability"] = base_up
        row["up_probability"] = adjusted_up
        row["down_probability"] = round(100.0 - adjusted_up, 1)
        row["trump_adjustment"] = round(adjusted_up - base_up, 1)
        row["trump_factor"] = copy.deepcopy(factor)
        row["signal"] = "偏多" if adjusted_up >= 58 else "偏空" if adjusted_up <= 42 else "震荡"
        drivers = list(row.get("drivers") or [])
        factor_copy = (
            f"Trump {float(factor['score']):+.2f} ({adjusted_up - base_up:+.1f}pp)"
            if abs(float(factor["score"])) >= 0.01
            else "Trump中性"
        )
        row["drivers"] = [*drivers, factor_copy]
    result["trump_factors"] = factors
    runtime = result.setdefault("runtime", {})
    runtime["trump_factor_included"] = True
    runtime["probability_method"] = (
        str(runtime.get("probability_method") or "多因子研究信号")
        + "；已加入 Trump 公开信息规则因子（非校准概率）"
    )
    return result


def _fetch_x_user_posts(
    token: str,
    *,
    user_id: str,
    since_id: str,
    timeout_seconds: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    resolved_user_id = user_id
    rate: dict[str, Any] = {}
    if not resolved_user_id:
        lookup_url = (
            "https://api.x.com/2/users/by/username/"
            + urllib.parse.quote(TRUMP_USERNAME, safe="")
        )
        lookup, rate = _x_json_request(lookup_url, token, timeout_seconds)
        user = lookup.get("data") if isinstance(lookup, dict) else None
        resolved_user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        if not resolved_user_id:
            raise XApiError("X API 未返回 @realDonaldTrump 的用户 ID")

    query: dict[str, str] = {
        "max_results": "10",
        "exclude": "retweets",
        "tweet.fields": "created_at,lang,public_metrics,referenced_tweets",
    }
    if since_id:
        query["since_id"] = since_id
    timeline_url = (
        f"https://api.x.com/2/users/{urllib.parse.quote(resolved_user_id, safe='')}/tweets?"
        + urllib.parse.urlencode(query)
    )
    payload, rate = _x_json_request(timeline_url, token, timeout_seconds)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    posts = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return resolved_user_id, posts, rate


def _x_json_request(
    url: str,
    token: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "QuantNewsMonitor/0.1 (+local research tool)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            headers = response.headers
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        detail = ""
        try:
            parsed = json.loads(body)
            detail = str(parsed.get("detail") or parsed.get("title") or "")
        except json.JSONDecodeError:
            detail = body.strip()[:180]
        delay = retry_delay(exc.headers) if exc.code in {429,503} else 900 if exc.code in {401,403} else 60
        raise XApiError(f"X API HTTP {exc.code}: {detail or exc.reason}",delay) from exc
    except urllib.error.URLError as exc:
        raise XApiError(f"X API 网络错误: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise XApiError("X API 返回格式异常")
    return payload, _rate_limit_from_headers(headers)


def _parse_x_post(item: dict[str, Any]) -> dict[str, Any]:
    post_id = str(item.get("id", "")).strip()
    if not post_id:
        raise ValueError("X 帖文缺少 ID")
    text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
    created_at = parse_datetime(str(item.get("created_at", "")), default=utc_now())
    raw_metrics = item.get("public_metrics") if isinstance(item.get("public_metrics"), dict) else {}
    metrics = {
        "repost_count": int(raw_metrics.get("retweet_count") or 0),
        "reply_count": int(raw_metrics.get("reply_count") or 0),
        "like_count": int(raw_metrics.get("like_count") or 0),
        "quote_count": int(raw_metrics.get("quote_count") or 0),
    }
    return {
        "id": post_id,
        "username": TRUMP_USERNAME,
        "text": text,
        "created_at": iso_utc(created_at),
        "url": f"https://x.com/{TRUMP_USERNAME}/status/{post_id}",
        "language": str(item.get("lang") or ""),
        "public_metrics": metrics,
        "analysis": analyze_trump_post(text),
    }


def _target_groups(
    market: str,
    symbol: str,
    name: str,
    aliases: tuple[str, ...] | list[str],
    sector: str,
) -> set[str]:
    text = " ".join((symbol, name, sector, *aliases)).casefold()
    groups = {"broad"}
    if market.upper() in {"CN", "HK"}:
        groups.add("china")
    for group, keywords in TARGET_GROUP_KEYWORDS.items():
        if any(keyword.casefold() in text for keyword in keywords):
            groups.add(group)
    return groups


def _mention_terms(
    symbol: str,
    name: str,
    aliases: tuple[str, ...] | list[str],
) -> list[str]:
    values = [name, *aliases]
    if len(symbol) >= 3 or any(character.isdigit() for character in symbol):
        values.append(symbol)
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if len(value.strip()) >= 3
        )
    )


def _contains_term(text: str, term: str) -> bool:
    normalized_text = text.casefold()
    normalized_term = term.casefold().strip()
    if not normalized_term:
        return False
    if normalized_term.isascii():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_text,
        ) is not None
    return normalized_term in normalized_text


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(_contains_term(text, pattern) for pattern in patterns)


def _stance(
    text: str,
    positive_patterns: tuple[str, ...],
    negative_patterns: tuple[str, ...],
    default: float,
) -> float:
    positive = sum(1 for pattern in positive_patterns if _contains_term(text, pattern))
    negative = sum(1 for pattern in negative_patterns if _contains_term(text, pattern))
    if positive == negative:
        return default
    return _clamp((positive - negative) * 0.55, -1.0, 1.0)


def _general_direction(text: str) -> float:
    positive = (
        "great", "strong", "win", "record", "boom", "success", "approve",
        "deal", "peace", "support", "buy", "上涨", "上漲", "利好", "成功",
        "强劲", "強勁", "协议", "協議", "和平", "支持",
    )
    negative = (
        "bad", "weak", "fail", "crash", "fraud", "disaster", "threat",
        "ban", "attack", "下跌", "利空", "失败", "失敗", "崩盘", "崩盤",
        "欺诈", "威胁", "威脅", "禁令", "袭击", "襲擊",
    )
    score = sum(1 for value in positive if _contains_term(text, value)) - sum(
        1 for value in negative if _contains_term(text, value)
    )
    return _clamp(score * 0.28, -1.0, 1.0)


def _empty_factor() -> dict[str, Any]:
    return {
        "score": 0.0,
        "direction": "neutral",
        "relevance": 0.0,
        "confidence": 0.0,
        "themes": [],
        "theme_labels": [],
        "post_count": 0,
        "direct_mention": False,
        "reason": "近 72 小时暂无可用的相关 Trump 公开信息，不代表政策中性",
        "score_kind": "uncalibrated_policy_signal",
        "evidence": [],
        "latest_post_id": "",
        "latest_post_url": "",
        "latest_post_at": "",
    }


def _latest_post_id(posts: dict[str, dict[str, Any]]) -> str:
    numeric = [int(value) for value in posts if value.isdigit()]
    return str(max(numeric)) if numeric else ""


def _rate_limit_from_headers(headers: Any) -> dict[str, Any]:
    remaining = headers.get("x-rate-limit-remaining") if headers else None
    reset = headers.get("x-rate-limit-reset") if headers else None
    reset_at = None
    if reset:
        try:
            reset_at = iso_utc(datetime.fromtimestamp(int(reset), tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            reset_at = None
    try:
        remaining_value = int(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining_value = None
    return {"remaining": remaining_value, "reset_at": reset_at}


def _friendly_x_error(exc: Exception) -> str:
    text = str(exc)
    if "HTTP 401" in text:
        return "X API 鉴权失败，请检查 Bearer Token"
    if "HTTP 402" in text:
        return "X API 额度不足，请在开发者后台充值"
    if "HTTP 403" in text:
        return "当前 X API 应用无权读取该时间线"
    if "HTTP 429" in text:
        return "X API 已达到限流，稍后自动重试"
    return "X API 连接失败，稍后自动重试"


def _normalize_token(token: str) -> str:
    value = token.strip()
    if value.casefold().startswith("bearer "):
        value = value[7:].strip()
    return value


def _protect_windows(data: bytes) -> bytes:
    return _windows_dpapi(data, protect=True)


def _unprotect_windows(data: bytes) -> bytes:
    return _windows_dpapi(data, protect=False)


def _windows_dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("安全保存 X API Token 目前仅支持 Windows")
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    source_buffer = ctypes.create_string_buffer(data)
    source_blob = DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)),
    )
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    flags = 0x1

    if protect:
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        success = crypt32.CryptProtectData(
            ctypes.byref(source_blob),
            "Quantify X API Token",
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        description = wintypes.LPWSTR()
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source_blob),
            ctypes.byref(description),
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
        if description:
            kernel32.LocalFree(description)

    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
