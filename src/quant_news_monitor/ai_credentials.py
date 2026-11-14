"""OpenAI credentials scoped to one local STock installation/user."""
from __future__ import annotations

import os
from pathlib import Path
import threading


class OpenAICredentials:
    _lock = threading.RLock()

    def __init__(self, config_path):
        self.path = Path(config_path).resolve().parent / ".credentials" / "openai.dpapi"

    def source(self):
        if os.environ.get("OPENAI_API_KEY", "").strip():
            return "environment"
        return "windows_dpapi" if self.path.is_file() else "none"

    def load(self):
        environment = os.environ.get("OPENAI_API_KEY", "").strip()
        if environment:
            return environment
        with self._lock:
            if not self.path.is_file():
                return ""
            from .trump_monitor import _unprotect_windows
            return _unprotect_windows(self.path.read_bytes()).decode("utf-8")

    def available_key(self):
        try:
            return self.load() or None
        except (OSError, RuntimeError, ValueError, UnicodeError):
            # A migrated Windows-user secret must not prevent the app opening.
            return None

    def status(self):
        source = self.source()
        configured = bool(self.available_key())
        return {"configured": configured, "source": source,
                "error": "本机密钥无法读取，请在当前 Windows 账户重新配置" if source != "none" and not configured else None}

    def save(self, api_key):
        if self.source() == "environment":
            raise ValueError("当前密钥由 OPENAI_API_KEY 环境变量提供，请先在启动环境中移除后再保存本机密钥")
        if not isinstance(api_key, str):
            raise ValueError("请输入有效的 OpenAI API Key")
        value = api_key.strip()
        if not 20 <= len(value) <= 4096 or any(char.isspace() or ord(char) < 33 for char in value):
            raise ValueError("API Key 格式无效，请完整粘贴密钥，不要包含空格或换行")
        from .trump_monitor import _protect_windows
        with self._lock:
            encrypted = _protect_windows(value.encode("utf-8"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(encrypted)
            os.replace(temporary, self.path)

    def clear(self):
        if self.source() == "environment":
            raise ValueError("当前密钥由 OPENAI_API_KEY 环境变量提供，请在启动环境中移除后重开软件")
        with self._lock:
            self.path.unlink(missing_ok=True)
