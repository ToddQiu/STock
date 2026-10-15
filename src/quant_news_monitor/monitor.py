from __future__ import annotations

from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from .analyzers import Analyzer, RuleAnalyzer
from .config import AppConfig
from .models import Alert, NewsItem, utc_now
from .sources import NewsSource
from .stock_mentions import matches_stock
from .storage import NewsStore, append_alert
from .research_ledger import ResearchLedger, RecordingAnalyzer


@dataclass(frozen=True)
class RunSummary:
    fetched: int
    inserted: int
    analyzed: int
    alerts: tuple[Alert, ...]
    errors: tuple[str, ...]


class NewsMonitor:
    def __init__(
        self,
        config: AppConfig,
        sources: list[NewsSource],
        analyzer: Analyzer,
        store: NewsStore,
    ):
        self.config = config
        self.sources = sources
        if isinstance(analyzer, RuleAnalyzer):
            analyzer.watchlist = config.watchlist
        self.ledger = ResearchLedger(config.storage.database.parent / "research.db")
        self.analyzer = RecordingAnalyzer(analyzer,self.ledger)
        self.store = store

    def run_once(self, *, offline: bool = False) -> RunSummary:
        errors: list[str] = []
        collected: list[NewsItem] = []

        sources = [source for source in self.sources if not (offline and source.is_network)]
        with ThreadPoolExecutor(max_workers=min(3,max(1,len(sources)))) as executor:
            futures = {executor.submit(source.collect,self.config.watchlist,self.config.max_items_per_asset):source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    collected.extend(future.result())
                    for warning in getattr(source,"errors",()):
                        errors.append(f"数据源 {source.name} 部分失败: {warning}")
                except Exception as exc:
                    errors.append(f"数据源 {source.name} 失败: {type(exc).__name__}: {exc}")

        now = utc_now()
        cutoff = now - timedelta(hours=self.config.lookback_hours)
        watches = {(w.market, w.symbol): w for w in self.config.watchlist}
        def eligible(item: NewsItem) -> bool:
            if not offline and (item.source == "demo" or item.title.startswith("[演示数据]")):
                return False
            watch = watches.get((item.market, item.symbol))
            return bool(watch and cutoff <= item.published_at <= now + timedelta(minutes=5)
                        and (item.source in {"manual", "local-inbox"} or matches_stock(f"{item.title} {item.summary}", watch)))
        recent = [item for item in collected if eligible(item)]
        for item in recent:
            self.ledger.observe_news(item)
        inserted = self.store.add_items(recent)

        analyzed = 0
        alerts: list[Alert] = []
        analyzer_id = getattr(self.analyzer, "analysis_id", self.analyzer.name)
        for item in self.store.pending_items(analyzer=analyzer_id, eligible=eligible):
            try:
                watch = watches[(item.market, item.symbol)]
                item = replace(item, target_name=watch.name, target_aliases=watch.aliases)
                analysis = self.analyzer.analyze(item)
                self.store.save_analysis(item, analysis, utc_now())
                analyzed += 1
            except Exception as exc:
                self.store.save_error(item, f"{type(exc).__name__}: {exc}")
                errors.append(f"分析 {item.symbol} / {item.title[:40]} 失败: {exc}")
                continue

            self.store.reconcile_alert(item.fingerprint, self.config.alert_threshold)
            if abs(analysis.score) < self.config.alert_threshold:
                continue

            alert = Alert(
                fingerprint=item.fingerprint,
                created_at=utc_now(),
                market=item.market,
                symbol=item.symbol,
                title=item.title,
                url=item.url,
                score=analysis.score,
                polarity="bullish" if analysis.score > 0 else "bearish",
                level="high" if abs(analysis.score) >= 0.65 else "medium",
                event_type=analysis.event_type,
                horizon=analysis.horizon,
                summary=analysis.summary,
                risk_flags=analysis.risk_flags,
            )
            if self.store.create_alert(alert):
                try:
                    append_alert(self.config.storage.alerts, alert)
                except OSError as exc:
                    errors.append(f"写入告警文件失败: {exc}")
                alerts.append(alert)

        return RunSummary(
            fetched=len(collected),
            inserted=inserted,
            analyzed=analyzed,
            alerts=tuple(alerts),
            errors=tuple(errors),
        )
