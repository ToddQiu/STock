from __future__ import annotations

import copy
import math
import threading
from pathlib import Path

from .config_store import ConfigStore
from .models import iso_utc, utc_now
from .research_ledger import ResearchLedger, engine_fingerprint, HORIZONS


DEFAULT_SETTINGS = {"enabled": True, "latency_seconds": 1,
                    "costs": {asset:{"fee_bps":5.,"slippage_bps":2.} for asset in ("GOLD","BTC")}}


class ForwardMonitor:
    def __init__(self, config_path: str | Path, ledger: ResearchLedger, snapshot_provider, *, now=utc_now):
        self.config = ConfigStore(config_path)
        self.ledger, self.snapshot_provider, self.now = ledger, snapshot_provider, now
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.last_tick = None
        self.error = None
        self.code_sha256 = engine_fingerprint("asset_monitor.py","trump_monitor.py","analyzers.py","forward_monitor.py","research_ledger.py","sources.py","news_transport.py")

    def settings(self):
        defaults = copy.deepcopy(DEFAULT_SETTINGS)
        saved = self.config.read().get("forward_validation") or {}
        defaults.update({k:v for k,v in saved.items() if k != "costs"})
        for asset, values in (saved.get("costs") or {}).items():
            if asset in defaults["costs"]: defaults["costs"][asset].update(values)
        return defaults

    def configure(self, payload):
        if not isinstance(payload,dict): raise ValueError("参数格式不正确")
        with self._lock:
            settings = self.settings()
            if "enabled" in payload:
                if not isinstance(payload["enabled"],bool): raise ValueError("记录开关必须为布尔值")
                settings["enabled"] = payload["enabled"]
            if "latency_seconds" in payload:
                try: value = float(payload["latency_seconds"])
                except (ValueError,TypeError): raise ValueError("执行等待时间须为有效数字") from None
                if not math.isfinite(value) or not 1 <= value <= 60: raise ValueError("执行等待时间须在 1 至 60 秒之间")
                settings["latency_seconds"] = value
            if "costs" in payload:
                if not isinstance(payload["costs"],dict): raise ValueError("费用参数格式不正确")
                for asset, values in payload["costs"].items():
                    if asset not in {"GOLD","BTC"} or not isinstance(values,dict): raise ValueError("费用标的不正确")
                    for key,value in values.items():
                        if key not in {"fee_bps","slippage_bps"}: raise ValueError("费用项目不正确")
                        try: number = float(value)
                        except (ValueError,TypeError): raise ValueError("费用须为有效数字") from None
                        if not math.isfinite(number) or not 0 <= number <= 500: raise ValueError("单边费用/滑点须在 0 至 500 基点之间")
                        settings["costs"][asset][key] = number
            self.config.set_value("forward_validation",settings)

    def start(self):
        self._thread = threading.Thread(target=self._loop,name="forward-validation",daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=3)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.step()
                self.error = None
            except Exception as exc:
                self.error = f"预测记录暂时不可用：{type(exc).__name__}"
            self._stop.wait(1)

    def step(self):
        snapshot = self.snapshot_provider()
        now = self.now()
        self.ledger.advance(snapshot.get("assets",[]),now=now)
        self.last_tick = iso_utc(now)
        settings = self.settings()
        if not settings["enabled"]: return
        active = self.ledger.active_slots(now=now)
        inputs = snapshot.get("research_inputs") or {}
        markets = inputs.get("markets") or {}
        analysis_settings = self.config.read().get("analysis") or {}
        engine = {"version":"asset-forward-v1", "code_sha256":self.code_sha256,
                  "analysis_mode":analysis_settings.get("mode","auto"), "requested_model":analysis_settings.get("model"),
                  "collection":{"news_interval_seconds":(snapshot.get("runtime") or {}).get("news_interval_seconds"),
                                "max_news_per_asset":(snapshot.get("runtime") or {}).get("news_max_per_asset")},
                  "calibrated":False}
        for signal in snapshot.get("probabilities",[]):
            asset, horizon = signal.get("asset"),signal.get("horizon")
            if asset not in {"GOLD","BTC"} or horizon not in HORIZONS or (asset,horizon) in active: continue
            quote = markets.get(asset)
            if not quote: continue
            policy = {**settings["costs"][asset], "latency_seconds":settings["latency_seconds"],
                      "entry_grace_seconds":120,"exit_grace_seconds":180,"quote_max_age_seconds":120,
                      "long_threshold":.58,"short_threshold":.42,"overlap":"one_open_per_asset_horizon",
                      "fees":"per_side_notional","evaluation_event":"exit_price_above_frozen_reference",
                      "funding_and_borrowing_included":False}
            context = {"market":quote,"bars":inputs.get("bars",{}).get(asset,[]),
                       "news":[n for n in inputs.get("news",[]) if n.get("asset")==asset],
                       "trump_posts":(snapshot.get("trump") or {}).get("posts",[]),
                       "base_signals":[r for r in inputs.get("base_signals",[]) if r.get("asset")==asset]}
            actual_engine = {**engine,"news_analyzers":sorted({n.get("analyzer", "unknown") for n in context["news"]}),
                             "returned_models":sorted({str((n.get("model_metadata") or {}).get("returned_model")) for n in context["news"] if (n.get("model_metadata") or {}).get("returned_model")})}
            self.ledger.create_forecast(signal,quote,context,policy=policy,engine=actual_engine,now=now)

    def snapshot(self, *, asset="all", horizon="all", status="all", offset=0):
        if asset not in {"all","GOLD","BTC"} or horizon not in {"all",*HORIZONS} or status not in {"all","pending","evaluated","missing"}:
            raise ValueError("不支持的记录筛选条件")
        if offset < 0: raise ValueError("分页位置不能为负数")
        records,total = self.ledger.forecasts(asset=asset,horizon=horizon,status=status,offset=offset,limit=50)
        return {**self.ledger.statistics(asset=asset,horizon=horizon),"records":records,"total":total,"offset":offset,"page_size":50,
                "settings":self.settings(),"runtime":{"last_tick":self.last_tick,"error":self.error},
                "method":"同一标的同一周期不重叠记录；预测与费用参数锁定后不改写。收益为单次模拟收益均值，不是账户累计收益。"}
