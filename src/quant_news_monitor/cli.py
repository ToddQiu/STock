from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from .analyzers import select_analyzer
from .config import load_config
from .monitor import NewsMonitor, RunSummary
from .runtime import build_sources
from .storage import NewsStore


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股/港股/美股新闻事件监测器")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="运行一次后退出（默认）")
    mode.add_argument("--watch", action="store_true", help="持续循环监测")
    parser.add_argument("--offline", action="store_true", help="仅读取本地 inbox，不访问网络")
    parser.add_argument(
        "--analyzer",
        choices=("auto", "rules", "openai"),
        help="临时覆盖配置中的分析方式",
    )
    parser.add_argument("--interval-minutes", type=int, help="临时覆盖轮询间隔")
    parser.add_argument("--interval-seconds", type=int, help="临时覆盖新闻检测间隔（秒）")
    return parser


def _print_summary(summary: RunSummary) -> None:
    print(
        f"抓取 {summary.fetched} 条，新入库 {summary.inserted} 条，"
        f"完成分析 {summary.analyzed} 条，新增告警 {len(summary.alerts)} 条"
    )
    for alert in summary.alerts:
        label = "利多" if alert.polarity == "bullish" else "利空"
        level = "高" if alert.level == "high" else "中"
        print(f"[{label}/{level}] {alert.market}:{alert.symbol} {alert.score:+.2f} {alert.title}")
        print(f"  {alert.summary}")
    for error in summary.errors:
        print(f"[警告] {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        analyzer, analyzer_message = select_analyzer(
            args.analyzer or config.analysis.mode,
            config.analysis.model,
            watchlist=config.watchlist,
        )
        store = NewsStore(config.storage.database)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 2

    monitor = NewsMonitor(config, build_sources(config), analyzer, store)
    interval = max(1, args.interval_seconds or (args.interval_minutes * 60 if args.interval_minutes else config.news_interval_seconds))
    print(analyzer_message)
    print(f"监测股票数: {len(config.watchlist)}，告警阈值: ±{config.alert_threshold:.2f}")

    try:
        while True:
            print(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] 开始检测")
            _print_summary(monitor.run_once(offline=args.offline))
            if not args.watch:
                return 0
            print(f"下一次检测将在 {interval} 秒后进行，按 Ctrl+C 停止。")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n监测已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
