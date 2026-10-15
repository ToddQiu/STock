from __future__ import annotations

import base64
import os
import shutil
import subprocess


class WindowsSystemNotifier:
    def __init__(self, app_name: str = "STock") -> None:
        self.app_name = app_name
        self._powershell = shutil.which("powershell.exe") if os.name == "nt" else None

    @property
    def available(self) -> bool:
        return bool(self._powershell)

    def notify(self, title: str, body: str) -> tuple[bool, str | None]:
        if not self._powershell:
            return False, "Windows 系统通知不可用"

        safe_title = _powershell_literal(_single_line(title, 64))
        safe_body = _powershell_literal(_single_line(body, 240))
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notice = New-Object System.Windows.Forms.NotifyIcon
try {{
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Text = {_powershell_literal(self.app_name)}
    $notice.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notice.BalloonTipTitle = {safe_title}
    $notice.BalloonTipText = {safe_body}
    $notice.Visible = $true
    $notice.ShowBalloonTip(15000)
    Start-Sleep -Milliseconds 16000
}} finally {{
    $notice.Dispose()
}}
""".strip()
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(
                [
                    self._powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Sta",
                    "-EncodedCommand",
                    encoded,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError as exc:
            return False, f"系统通知启动失败: {exc}"
        return True, None


def _single_line(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
