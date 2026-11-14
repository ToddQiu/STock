"""Private loopback service owned by the packaged desktop window."""
from __future__ import annotations

import argparse
import json
import sys
import threading

from quant_news_monitor.webapp import AppState, DashboardServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    state = AppState(args.config)
    try:
        server = DashboardServer(("127.0.0.1", args.port), state)
    except OSError:
        if not args.port:
            state.shutdown()
            raise
        server = DashboardServer(("127.0.0.1", 0), state)

    def parent_closed():
        # EOF also handles an unexpected exit of the desktop parent process.
        sys.stdin.buffer.read()
        server.shutdown()

    threading.Thread(target=parent_closed, daemon=True, name="desktop-lifetime").start()
    print(json.dumps({"type": "ready", "port": server.server_port}))
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        state.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
