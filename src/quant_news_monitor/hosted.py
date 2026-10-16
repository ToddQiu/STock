"""Invite-only full web application; separate from the desktop listener and data."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .webapp import AppState, DashboardHandler


def validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname)
            or parsed.hostname in {"localhost", "127.0.0.1"}):
        raise ValueError("Public origin must be an HTTPS hostname without a path or credentials")
    return "https://" + parsed.hostname


def initialize_workspace(root: Path, template: Path) -> Path:
    root = root.resolve()
    marker = root / ".stock-hosted-workspace"
    config = root / "config.json"
    if marker.is_file():
        if not config.is_file():
            raise ValueError("Hosted workspace is incomplete; restore its backup before starting")
        return config
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty directory; never expose the desktop workspace")
    raw = json.loads(template.read_text(encoding="utf-8"))
    if (raw.get("portfolio") != {"positions": [], "principal_cny": 0}
            or raw.get("analysis", {}).get("mode") != "rules"):
        raise ValueError("Hosted template must have empty holdings, zero principal and rules analysis")
    raw["storage"] = {"database": "data/monitor.db", "alerts": "data/alerts.jsonl"}
    raw["sources"]["inbox"] = "data/inbox.jsonl"
    raw["asset_system_notifications_enabled"] = False
    root.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    marker.write_text("1\n", encoding="ascii")
    return config


def create_access_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a restart from invalidating everyone's access.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secrets.token_urlsafe(32) + "\n")


def read_access_key(path: Path) -> str:
    key = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
        raise ValueError("Generate the access key with --create-key; no default password is allowed")
    return key


class HostedState(AppState):
    def set_asset_system_notifications(self, enabled: bool) -> bool:
        return super().set_asset_system_notifications(False)

    def test_asset_system_notification(self) -> tuple[bool, str | None]:
        return False, "系统弹窗仅适用于桌面客户端，网站异动记录在事件中心"

    def assets_snapshot(self, *, for_research: bool = False) -> dict:
        result = super().assets_snapshot(for_research=for_research)
        result["runtime"].update(system_notifications_available=False,
                                 system_notifications_enabled=False,
                                 system_notification_error="网站异动记录在事件中心；系统弹窗需桌面客户端")
        return result


class HostedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, state: AppState, origin: str, key: str):
        self.public_origin = validate_origin(origin)
        self.public_host = urlsplit(self.public_origin).netloc
        self.app_state = state
        self.credential_digest = hashlib.sha256(("stock:" + key).encode("ascii")).digest()
        self._slots = threading.BoundedSemaphore(32)
        super().__init__(address, HostedHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class HostedHandler(DashboardHandler):
    server: HostedServer
    server_version = "STock"
    sys_version = ""

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def end_headers(self):
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def _trusted_request(self) -> bool:
        # Do not trust forwarded host/origin headers. Only the configured HTTPS site is allowed.
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if (hosts != [self.server.public_host] or len(origins) > 1
                or (origins and origins != [self.server.public_origin])
                or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")
                or (self.command != "GET" and origins != [self.server.public_origin])):
            self._send_json({"error": "请求来源与网站地址不一致"}, status=HTTPStatus.FORBIDDEN)
            return False
        authorizations = self.headers.get_all("Authorization", [])
        credentials = b""
        if len(authorizations) == 1 and authorizations[0].startswith("Basic "):
            try:
                credentials = base64.b64decode(authorizations[0][6:], validate=True)
            except (ValueError, binascii.Error):
                pass
        if not hmac.compare_digest(hashlib.sha256(credentials).digest(), self.server.credential_digest):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="STock invited workspace", charset="UTF-8"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/var/lib/stock"))
    parser.add_argument("--template", type=Path, default=Path("/app/config.example.json"))
    parser.add_argument("--origin", default=os.environ.get("STOCK_PUBLIC_ORIGIN", ""))
    parser.add_argument("--key-file", type=Path, default=Path("/run/secrets/access_key"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--create-key", action="store_true")
    args = parser.parse_args(argv)
    if args.create_key:
        create_access_key(args.key_file)
        print("Access key created. Keep it private; the login username is stock.")
        return 0
    origin = validate_origin(args.origin)
    key = read_access_key(args.key_file)
    config = initialize_workspace(args.workspace, args.template)
    state = HostedState(config)
    try:
        server = HostedServer((args.host, args.port), state, origin, key)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        print(f"STock full application ready behind HTTPS proxy: {origin}", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()
    finally:
        state.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
