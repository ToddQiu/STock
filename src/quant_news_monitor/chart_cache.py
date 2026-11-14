"""Coordination for chart requests; cached chart dictionaries stay unannotated."""

from concurrent.futures import Future
import threading


class ChartWork:
    def __init__(self):
        # Accessed while AppState._lock is held, never while doing network I/O.
        self.inflight: dict[tuple, Future] = {}
        self.snapshot_lock = threading.Lock()
        self.transform_lock = threading.Lock()
        self.transforms: dict[tuple, tuple[dict, dict]] = {}

    def resample(self, data, market, symbol, period, interval, build):
        key = (market, symbol, data.get("mode"), period, interval)
        with self.transform_lock:
            cached = self.transforms.get(key)
            # Each successful source refresh installs a new, private candle list.
            if cached and cached[0].get("candles") is data.get("candles"):
                return cached[1]
            source = {key: value for key, value in data.items() if key != "retained_from_cache"}
            result = build(source, market, interval)
            self.transforms.pop(key, None)
            self.transforms[key] = (source, result)
            # Five derived intervals for each of the sixteen source cache entries.
            while len(self.transforms) > 80:
                self.transforms.pop(next(iter(self.transforms)))
            return result
