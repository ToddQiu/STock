"""Single-flight background reads; mutations invalidate even in-flight results."""
from __future__ import annotations

import threading
import time


_EMPTY = object()


class ReadSnapshot:
    """Publish read-only inputs atomically; consumers build their own responses.

    The loader returns a new object graph on each refresh. Neither it nor readers
    may mutate a published value. Replacing the reference keeps in-flight readers
    consistent without copying the entire news archive for every request.
    """
    def __init__(self, loader, *, ttl=5, clock=time.monotonic):
        self.loader, self.ttl, self.clock = loader, ttl, clock
        self._lock = threading.Lock()
        self._value = _EMPTY
        self._loaded_at = self._retry_at = -float('inf')
        self._error = None
        self._generation = 0
        self._pending = None

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self._value = _EMPTY
            self._error = None
            self._retry_at = -float('inf')

    def _load(self, generation, done):
        try:
            value, error = self.loader(), None
        except Exception as exc:
            value, error = _EMPTY, exc
        with self._lock:
            if generation == self._generation:
                self._error = error
                self._retry_at = self.clock() + self.ttl
                if error is None:
                    self._value, self._loaded_at = value, self.clock()
            done.set()

    def get(self):
        while True:
            with self._lock:
                now = self.clock()
                stale = self._value is _EMPTY or now - self._loaded_at >= self.ttl
                running = self._pending is not None and not self._pending.is_set()
                if stale and not running and now >= self._retry_at:
                    self._pending = threading.Event()
                    threading.Thread(target=self._load, args=(self._generation, self._pending),
                                     name='news-read-snapshot', daemon=True).start()
                    running = True
                if self._value is not _EMPTY:
                    result = {'value': self._value, 'stale': stale, 'refreshing': running,
                              'error': str(self._error) if self._error else None}
                    break
                if not running and self._error is not None:
                    raise self._error
                pending = self._pending
            pending.wait()
        return result
