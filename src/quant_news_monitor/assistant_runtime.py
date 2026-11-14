"""Lazy, loopback-only llama.cpp runner. No cloud fallbacks or tool execution."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

MODEL = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
RUNTIME = "b11850"


def _windows_job(process):
    # Windows closes this handle even on a forced app exit, preventing orphan models.
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                    ("flags", w.DWORD), ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [ctypes.c_void_p, w.LPCWSTR], w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, w.HANDLE(int(process._handle))):
        if handle:
            kernel.CloseHandle(handle)
        raise OSError("Cannot isolate model process")
    return lambda: kernel.CloseHandle(handle)


class LocalModel:
    def __init__(self, home: Path, idle_seconds=180):
        self.home, self.idle_seconds = home, idle_seconds
        self._process = self._job_close = self._timer = None
        self._lock = threading.RLock()
        self._opener = build_opener(ProxyHandler({}))
        self._url, self._key = "", ""
        self._closed = False

    @property
    def installed(self):
        return os.name == "nt" and (self.home / RUNTIME / "llama-server.exe").is_file() and (self.home / MODEL).is_file() and (self.home / "manifest.json").is_file()

    @property
    def loaded(self):
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _request(self, path, data=None, timeout=2):
        req = Request(self._url + path, data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._key})
        with self._opener.open(req, timeout=timeout) as response:
            return json.loads(response.read(100_000))

    def _start(self, cancel):
        with self._lock:
            if self._closed or cancel.is_set():
                raise InterruptedError("Cancelled")
            if self._timer:
                self._timer.cancel()
            if self.loaded:
                return
            if not self.installed:
                raise FileNotFoundError("Local model not installed")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            self._url, self._key = f"http://127.0.0.1:{port}", secrets.token_urlsafe(32)
            args = [str(self.home / RUNTIME / "llama-server.exe"), "-m", str(self.home / MODEL),
                    "--host", "127.0.0.1", "--port", str(port), "--api-key", self._key,
                    "-c", "4096", "-t", "2", "-tb", "2", "-np", "1", "-ngl", "0",
                    "--no-webui", "--no-webui-mcp-proxy", "--no-warmup", "--log-disable"]
            env = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")}
            self._process = subprocess.Popen(args, cwd=self.home / RUNTIME, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            try:
                self._job_close = _windows_job(self._process)
            except OSError:
                self.stop()
                raise
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cancel.wait(.25):
                raise InterruptedError("Cancelled")
            if not self.loaded:
                raise OSError("Model process stopped")
            try:
                if self._request("/health").get("status") == "ok":
                    return
            except (URLError, OSError, ValueError):
                pass
        raise TimeoutError("Model load timed out")

    def generate(self, system, prompt, cancel, schema=None):
        try:
            self._start(cancel)
            if cancel.is_set():
                raise InterruptedError("Cancelled")
            data = {
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "temperature": 0, "max_tokens": 180, "stream": False, "seed": 1,
                "cache_prompt": False}
            if schema:
                data["response_format"] = {"type": "json_schema", "json_schema": {"name": "evidence", "strict": True, "schema": schema}}
            result = self._request("/v1/chat/completions", data, timeout=90)
            choice = result["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Model returned empty text")
            return answer.strip()[:6000], choice.get("finish_reason") == "length"
        except Exception:
            self.stop()
            raise
        finally:
            with self._lock:
                if self.loaded:
                    self._timer = threading.Timer(self.idle_seconds, self.stop)
                    self._timer.daemon = True
                    self._timer.start()

    def stop(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            process, close = self._process, self._job_close
            self._process = self._job_close = None
            if close:
                close()
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def shutdown(self):
        with self._lock:
            self._closed = True
        self.stop()
