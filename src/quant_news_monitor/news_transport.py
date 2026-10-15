from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_delay(headers, *, fallback: float = 300, now: float | None = None) -> float:
    now = time.time() if now is None else now
    delays = []
    value = (headers or {}).get("Retry-After")
    if value:
        try:
            delays.append(float(value))
        except (ValueError, TypeError):
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
                delays.append(when.timestamp()-now)
            except (ValueError, TypeError, OverflowError):
                pass
    reset = (headers or {}).get("x-rate-limit-reset")
    if reset:
        try: delays.append(float(reset)-now)
        except (ValueError, TypeError): pass
    valid = [delay for delay in delays if 0 <= delay < float("inf")]
    return max(1, *valid) if valid else fallback


class NewsHttpClient:
    """Only for public news: bounded cache, conditional requests and provider backoff."""

    def __init__(self, *, clock=time.monotonic, opener=None):
        self.clock, self.opener = clock, opener
        self._lock = threading.RLock()
        self._entries = {}
        self._hosts = {}

    def fetch(self, url: str, *, timeout: int, ttl: int = 15, headers=None, max_bytes: int = 2_000_000) -> bytes:
        with self._lock:
            if url not in self._entries:
                if len(self._entries) >= 256:
                    oldest = next((key for key,value in self._entries.items() if not value["busy"]), None)
                    if oldest is not None: self._entries.pop(oldest)
                self._entries[url] = {"lock":threading.Lock(),"busy":0,"payload":None,"checked":-float("inf"),"next":0,"failures":0,"etag":None,"modified":None}
            entry = self._entries[url]
            entry["busy"] += 1
        try:
            with entry["lock"]:
                now = self.clock()
                host = urllib.parse.urlsplit(url).hostname or "news"
                if entry["payload"] is not None and now-entry["checked"] < ttl:
                    return entry["payload"]
                with self._lock:
                    blocked_until = max(entry["next"], self._hosts.get(host,0))
                if now < blocked_until:
                    raise OSError(f"{host} 请求退避中，约 {int(blocked_until-now)+1} 秒后重试")
                request_headers = {"User-Agent":"QuantNewsMonitor/0.1 (+local research tool)",**(headers or {})}
                if entry["etag"]: request_headers["If-None-Match"] = entry["etag"]
                if entry["modified"]: request_headers["If-Modified-Since"] = entry["modified"]
                request = urllib.request.Request(url,headers=request_headers)
                try:
                    try:
                        with (self.opener or urllib.request.urlopen)(request,timeout=timeout) as response:
                            payload = response.read(max_bytes + 1)
                            if len(payload)>max_bytes: raise ValueError("来源响应过大")
                            entry.update(payload=payload,etag=response.headers.get("ETag"),modified=response.headers.get("Last-Modified"))
                    except urllib.error.HTTPError as exc:
                        if exc.code != 304 or entry["payload"] is None: raise
                    entry.update(checked=self.clock(),next=0,failures=0)
                    return entry["payload"]
                except (OSError,ValueError,HTTPException) as exc:
                    entry["failures"] += 1
                    delay = min(900,30*2**min(entry["failures"]-1,5))
                    if isinstance(exc,urllib.error.HTTPError):
                        if exc.code in {429,503}:
                            delay = retry_delay(exc.headers,fallback=max(300,delay))
                            with self._lock: self._hosts[host] = max(self._hosts.get(host,0),self.clock()+delay)
                        elif exc.code in {401,403}: delay = 900
                    entry["next"] = self.clock()+delay
                    raise OSError(f"{host} 暂不可用（{type(exc).__name__}），{int(delay)} 秒后重试") from exc
        finally:
            with self._lock: entry["busy"] -= 1


news_http = NewsHttpClient()
