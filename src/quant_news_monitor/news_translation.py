from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path


MODEL_VERSION = "opus-mt-en-zh-q8-sentencepiece-checked-v3"


def numeric_facts(text: str) -> Counter:
    scales = {"trillion": "1e12", "billion": "1e9", "million": "1e6", "thousand": "1e3",
              "万亿": "1e12", "亿": "1e8", "億": "1e8", "万": "1e4", "萬": "1e4", "千": "1e3"}
    facts = Counter()
    for match in re.finditer(r"(\d+(?:[, ]\d{3})*(?:\.\d+)?)\s*(trillion|billion|million|thousand|万亿|亿|億|万|萬|千|%)?", text, re.I):
        value = Decimal(match[1].replace(",", "").replace(" ", "")) * Decimal(scales.get((match[2] or "").lower(), "1"))
        unit = "%" if match[2] == "%" else "number"
        before, after = text[max(0, match.start()-1):match.start()], text[match.end():match.end()+3]
        if before == "$" or after.startswith("美元"):
            unit = "USD"
        facts[(value, unit)] += 1
    return facts


def translation_is_safe(original: str, translated: str) -> bool:
    return (bool(re.search(r"[\u3400-\u9fff]", translated))
            and numeric_facts(original) == numeric_facts(translated)
            and not re.search(r"(.{3,16})\1{2,}", translated))


def needs_translation(text: str) -> bool:
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return bool(re.search(r"[A-Za-z]{3,}", text) and (not chinese or latin > chinese * 3))


def is_chinese_headline(title: str, source: str = "") -> bool:
    suffix = " - " + source
    if source and title.endswith(suffix):
        title = title[:-len(suffix)]
    chinese = len(re.findall(r"[\u3400-\u9fff]", title))
    return chinese >= 3 and chinese * 3 >= len(re.findall(r"[A-Za-z]", title))


class NewsTranslator:
    """Display-only translation; raw evidence and financial analysis stay unchanged."""

    def __init__(self, root: Path, *, translate=None, enabled=True):
        self.root = root
        self.path = root / "data" / "news_translations.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, text TEXT NOT NULL)")
            self._cache = dict(db.execute("SELECT key, text FROM translations"))
        finally:
            db.close()
        self._jobs = queue.PriorityQueue(maxsize=2000)
        self._pending = set()
        self._failed_until = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._responses = queue.Queue()
        self._translate = translate
        self._state = "waiting"
        self._error = None
        self.enabled = enabled
        if not enabled:
            self._state = "disabled"
        self._sequence = 0

    def start(self):
        if self.enabled and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name="news-chinese", daemon=True)
            self._thread.start()

    def set_enabled(self, enabled):
        with self._lock:
            if self.enabled == enabled:
                return
            self.enabled = enabled
            self._state = "waiting" if enabled else "disabled"
        self.start()

    def shutdown(self):
        self._stop.set()
        self._close_worker()
        if self._thread:
            self._thread.join(timeout=3)

    def status(self):
        with self._lock:
            return {"state": self._state, "enabled": self.enabled, "error": self._error,
                    "pending": len(self._pending), "cached": len(self._cache),
                    "engine": "本机英译中 · 机器翻译", "original_preserved": True}

    def lookup(self, text: str, *, priority=0):
        if not needs_translation(text):
            return text, "original"
        if not self.enabled:
            return None, "disabled"
        if len(text) > 1600:
            return None, "too_long"
        key = hashlib.sha256((MODEL_VERSION + text).encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                return self._cache[key], "translated"
            if self._failed_until.get(key, 0) > time.monotonic():
                return None, "unavailable"
            if key not in self._pending:
                try:
                    self._sequence += 1
                    self._jobs.put_nowait((priority, self._sequence, key, text))
                    self._pending.add(key)
                except queue.Full:
                    return None, "waiting"
        return None, "pending"

    def decorate(self, value):
        result = copy.deepcopy(value)

        def visit(item):
            if isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                for child in list(item.values()):
                    if isinstance(child, (list, dict)):
                        visit(child)
                for field in ("title", "summary", "news_summary", "evidence_title", "text"):
                    text = item.get(field)
                    if isinstance(text, str) and text:
                        translated, status = self.lookup(text, priority=0 if field == "title" else 10)
                        item[field + "_zh"] = translated
                        item[field + "_translation_status"] = status

        visit(result)
        return result

    def _close_worker(self):
        process, self._process = self._process, None
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def _read_response(self, timeout):
        deadline = time.monotonic() + timeout
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                response = self._responses.get(timeout=0.5)
            except queue.Empty:
                continue
            if response is None:
                raise RuntimeError("Translation worker exited")
            return response
        raise RuntimeError("Translation worker timed out or stopped")

    def _start_worker(self):
        import sentencepiece
        resources = Path(os.environ.get("STOCK_RESOURCE_ROOT", self.root))
        tokenizer = resources / "data" / "translation-models" / "marian-tokenizer"
        # Windows native tokenizer file APIs cannot reliably open Unicode paths.
        self._source_sp = sentencepiece.SentencePieceProcessor(model_proto=(tokenizer / "source.spm").read_bytes())
        self._target_sp = sentencepiece.SentencePieceProcessor(model_proto=(tokenizer / "target.spm").read_bytes())
        self._vocab = json.loads((tokenizer / "vocab.json").read_text(encoding="utf-8"))
        self._reverse_vocab = {value: key for key, value in self._vocab.items()}
        node = os.environ.get("STOCK_NODE_EXECUTABLE") or shutil.which("node")
        script = resources / "translation_runtime" / "worker.mjs"
        if not node or not script.exists():
            raise RuntimeError("Local translation runtime is unavailable")
        env = os.environ.copy()
        # Node does not otherwise inherit Windows system proxy settings.
        for protocol, proxy in urllib.request.getproxies().items():
            if protocol in {"http", "https"}:
                env.setdefault(protocol.upper() + "_PROXY", proxy)
        env["NODE_USE_ENV_PROXY"] = "1"
        self._responses = queue.Queue()
        with (self.root / "data" / "translation-worker.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([node, str(script)], cwd=self.root, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                       encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._process = process
        responses = self._responses

        def read_lines():
            try:
                for line in process.stdout:
                    try:
                        responses.put(json.loads(line))
                    except ValueError:
                        continue
            except (OSError, ValueError):
                pass
            finally:
                responses.put(None)

        threading.Thread(target=read_lines, name="translation-output", daemon=True).start()
        if not self._read_response(120).get("ready"):
            raise RuntimeError("Translation worker is not ready")

    def _run(self):
        retry_at = 0
        while not self._stop.is_set():
            if not self.enabled:
                self._state = "disabled"
                self._close_worker()
                self._stop.wait(0.5)
                continue
            if self._stop.wait(max(0, retry_at - time.monotonic())):
                break
            try:
                _, _, key, original = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._state = "translating"
                if self._translate:
                    translated = self._translate(original)
                else:
                    if not self._process:
                        self._state = "loading"
                        self._start_worker()
                    pieces = self._source_sp.encode(original, out_type=str)
                    input_ids = [self._vocab.get(piece, self._vocab["<unk>"]) for piece in pieces] + [self._vocab["</s>"]]
                    self._process.stdin.write(json.dumps({"id": key, "input_ids": input_ids}) + "\n")
                    self._process.stdin.flush()
                    response = self._read_response(120)
                    if response.get("id") != key:
                        raise RuntimeError("Translation response mismatch")
                    tokens = [self._reverse_vocab[token] for token in response.get("output_ids", [])
                              if token in self._reverse_vocab and self._reverse_vocab[token] not in {"</s>", "<pad>"}]
                    translated = self._target_sp.decode_pieces(tokens)
                if not isinstance(translated, str) or not translation_is_safe(original, translated):
                    self._failed_until[key] = time.monotonic() + 300
                    self._state = "ready"
                    self._error = "部分译文未通过数字或文本校验，保留原文；其他新闻继续翻译"
                    continue
                db = sqlite3.connect(self.path, timeout=10)
                try:
                    with db:
                        db.execute("INSERT OR REPLACE INTO translations VALUES (?,?)", (key, translated))
                finally:
                    db.close()
                with self._lock:
                    self._cache[key] = translated
                self._state = "ready"
                self._error = None
            except Exception as exc:
                self._state = "unavailable"
                self._error = f"{type(exc).__name__}: {exc}"
                self._failed_until[key] = time.monotonic() + 300
                self._close_worker()
                retry_at = time.monotonic() + 30
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._jobs.task_done()
