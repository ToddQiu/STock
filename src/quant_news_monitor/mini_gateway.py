"""Private, read-only WeChat adapter. Run behind HTTPS, never expose desktop APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PREFIX = "/mini/v1/"
SOURCE_PATHS = {"overview": "/api/dashboard", "portfolio": "/api/dashboard",
                "market": "/api/assets", "research": "/api/research/cards"}
SESSION_SECONDS = 3600


class GatewayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url: str, *, local: bool = False) -> dict:
    opener = build_opener(ProxyHandler({}), NoRedirect()) if local else build_opener(NoRedirect())
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=8) as response:
        raw = response.read(6_000_001)
    if len(raw) > 6_000_000:
        raise ValueError("Response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def text(value, limit=2000):
    return value[:limit] if isinstance(value, str) else ""


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def pick(row, strings=(), numbers=(), flags=(), *, limit=2000):
    row = row if isinstance(row, dict) else {}
    return {**{key: text(row.get(key), limit) for key in strings},
            **{key: number(row.get(key)) for key in numbers},
            **{key: row.get(key) is True for key in flags}}


def rows(value, limit):
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def article_url(value):
    value = text(value)
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def news_rows(value):
    result = []
    for row in rows(value, 40):
        item = pick(row, ("market", "symbol", "asset", "source", "published_at", "analyzed_at", "impact_reason", "analysis_summary"))
        item.update(title=text(row.get("title_zh") or row.get("title"), 500), url=article_url(row.get("url")))
        direction = row.get("polarity") or row.get("direction")
        item["stance"] = direction if direction in {"bullish", "neutral", "bearish"} else "unknown"
        result.append(item)
    return result


def project(view: str, raw: dict) -> dict:
    """Allowlist at every level: no config, raw inputs, credentials or probability claims."""
    if view == "overview":
        return {"freshness": pick(raw.get("freshness"), ("news_published_at", "news_collected_at", "news_analyzed_at")),
                "stats": pick(raw.get("stats"), numbers=("news_count", "bullish", "bearish", "neutral")),
                "news": news_rows(raw.get("news")),
                "movements": [pick(row, ("market", "symbol", "name", "detected_at", "reason"), ("change_pct",))
                              for row in rows(raw.get("movements"), 20)]}
    if view == "portfolio":
        source = raw.get("portfolio") if isinstance(raw.get("portfolio"), dict) else {}
        return {"summary": pick(source.get("summary"), ("principal_status",),
                    ("principal_cny", "principal_return_pct", "principal_loss_pct", "total_market_value_cny",
                     "total_pnl_cny", "daily_pnl_cny", "position_count", "valued_count", "stale_valued_count", "daily_pnl_count"),
                    ("valuation_complete", "daily_pnl_complete")),
                "updated_at": text((source.get("runtime") or {}).get("updated_at")),
                "positions": [pick(row, ("market", "symbol", "name", "currency", "quote_time", "quote_status", "quote_source", "price_label"),
                    ("quantity", "average_cost", "price", "market_value_cny", "pnl_cny", "pnl_pct", "daily_pnl_cny"))
                    for row in rows(source.get("positions"), 200)]}
    if view == "market":
        return {"assets": [pick(row, ("key", "symbol", "name", "subtitle", "unit", "currency", "price_time", "quote_status", "quote_note", "source"),
                       ("price", "change_24h", "change_bps"), ("delayed",)) for row in rows(raw.get("assets"), 4)],
                "news": news_rows(raw.get("news"))}
    if view == "research":
        cards = []
        for row in rows(raw.get("items"), 50):
            card = pick(row, ("id", "name", "target", "kind", "stance", "business", "facts", "fundamentals", "valuation", "macro", "risks", "review", "horizon", "data_as_of", "observed_at", "assessment_status"), limit=10000)
            scenarios = row.get("scenarios") if isinstance(row.get("scenarios"), dict) else {}
            card["scenarios"] = {stance: pick(scenarios.get(stance), ("thesis", "conditions", "invalidation"), limit=10000)
                                 for stance in ("bullish", "neutral", "bearish")}
            card["evidence"] = [{"url": article_url(e.get("url")), **pick(e, ("excerpt", "role", "as_of"), limit=4000)}
                                for e in rows(row.get("evidence"), 50)]
            cards.append(card)
        return {"items": cards, "total": number(raw.get("total")), "offset": number(raw.get("offset"))}
    raise GatewayError(404, "接口不存在")


class Gateway:
    def __init__(self, *, upstream="http://127.0.0.1:8765", appid="", secret="", owner="", reader=read_json, clock=time.monotonic):
        parsed = urlsplit(upstream)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Upstream must be http://127.0.0.1:<port>")
        self.upstream, self.appid, self.secret, self.owner = upstream.rstrip("/"), appid, secret, owner
        self.reader, self.clock = reader, clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.login_attempts = []
        self.cache = {}

    @property
    def configured(self):
        return bool(re.fullmatch(r"wx[0-9a-fA-F]{16}", self.appid) and self.secret and self.owner)

    def login(self, code):
        if not self.configured:
            raise GatewayError(503, "微信登录尚未配置")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", code):
            raise GatewayError(400, "登录凭证格式不正确")
        now = self.clock()
        with self.lock:
            self.login_attempts = [at for at in self.login_attempts if now - at < 60]
            if len(self.login_attempts) >= 20:
                raise GatewayError(429, "登录过于频繁，请稍后重试")
            self.login_attempts.append(now)
        try:
            identity = self.reader("https://api.weixin.qq.com/sns/jscode2session?" + urlencode({
                "appid": self.appid, "secret": self.secret, "js_code": code, "grant_type": "authorization_code"}))
        except Exception:
            raise GatewayError(502, "微信登录暂时不可用") from None
        if identity.get("errcode") or not secrets.compare_digest(str(identity.get("openid") or ""), self.owner):
            raise GatewayError(403, "此微信账号没有访问权限")
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions = {key: until for key, until in self.sessions.items() if until > now}
            if len(self.sessions) >= 32:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[hashlib.sha256(token.encode()).digest()] = now + SESSION_SECONDS
        return {"token": token, "expires_in": SESSION_SECONDS, "read_only": True}

    def authorize(self, header):
        if not isinstance(header, str) or not re.fullmatch(r"Bearer [A-Za-z0-9_-]{40,64}", header):
            raise GatewayError(401, "请先登录")
        key = hashlib.sha256(header[7:].encode()).digest()
        with self.lock:
            until = self.sessions.get(key, 0)
        if until <= self.clock():
            raise GatewayError(401, "登录已过期，请重新登录")
        return key

    def logout(self, header):
        key = self.authorize(header)
        with self.lock:
            self.sessions.pop(key, None)
            self.cache.clear()

    def snapshot(self, view):
        if view not in SOURCE_PATHS:
            raise GatewayError(404, "接口不存在")
        path = SOURCE_PATHS[view]
        with self.lock:
            cached = self.cache.get(path)
            if not cached or self.clock() - cached[0] >= 15:
                try:
                    raw = self.reader(self.upstream + path, local=True)
                    self.cache[path] = (self.clock(), raw)
                except Exception:
                    # Never return an earlier server snapshot as a successful new fetch.
                    raise GatewayError(502, "研究数据服务暂时不可用，请稍后重试") from None
            else:
                raw = cached[1]
        result = {"schema_version": 1, "view": view, "read_only": True,
                "served_at": datetime.now(timezone.utc).isoformat(),
                "data_as_of": text(raw.get("generated_at")), "data": project(view, raw)}
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 350_000:
            raise GatewayError(503, "本批资料过大，请使用桌面版查看完整记录")
        return result


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "ResearchGateway"
    sys_version = ""

    def handle_api(self):
        try:
            if not self.path.startswith(PREFIX) or "?" in self.path:
                raise GatewayError(404, "接口不存在")
            route = self.path[len(PREFIX):]
            if self.command == "POST" and route == "login":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise GatewayError(415, "请使用 JSON 请求")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024:
                        raise ValueError()
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    raise GatewayError(400, "请求格式不正确") from None
                result = self.server.gateway.login(value.get("code"))
            else:
                header = self.headers.get("Authorization", "")
                self.server.gateway.authorize(header)
                if self.command == "GET" and route in SOURCE_PATHS:
                    result = self.server.gateway.snapshot(route)
                elif self.command == "DELETE" and route == "session":
                    self.server.gateway.logout(header)
                    result = {"ok": True}
                else:
                    raise GatewayError(405, "仅提供只读研究接口")
            self.reply(200, result)
        except GatewayError as error:
            self.reply(error.status, {"error": str(error)})
        except Exception:
            self.reply(500, {"error": "服务暂时不可用"})

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    do_GET = do_POST = do_DELETE = do_PUT = handle_api

    def log_message(self, format, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private WeChat read-only gateway; HTTPS reverse proxy required")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--upstream", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    gateway = Gateway(upstream=args.upstream, appid=os.getenv("WECHAT_APP_ID", ""),
                      secret=os.getenv("WECHAT_APP_SECRET", ""), owner=os.getenv("WECHAT_OWNER_OPENID", ""))
    if not gateway.configured:
        parser.error("Set WECHAT_APP_ID, WECHAT_APP_SECRET and WECHAT_OWNER_OPENID on the server only")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), GatewayHandler)
    server.daemon_threads = True
    server.gateway = gateway
    print(f"WeChat gateway listening on 127.0.0.1:{args.port}; private read-only mode")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
