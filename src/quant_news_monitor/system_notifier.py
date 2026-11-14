from __future__ import annotations

import base64
import os
import shutil
import subprocess
from queue import Empty, Queue
from threading import Thread


DISPLAY_TIMEOUT_SECONDS = 5
_DISPLAY_ACK = "STOCK_NOTIFICATION_SHOWN"


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
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$notice = $null
$context = $null
$timer = $null
try {{
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $context = New-Object System.Windows.Forms.ApplicationContext
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 16000
    $timer.add_Tick({{ $context.ExitThread() }})
    $notice = New-Object System.Windows.Forms.NotifyIcon
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Text = {_powershell_literal(self.app_name)}
    $notice.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notice.BalloonTipTitle = {safe_title}
    $notice.BalloonTipText = {safe_body}
    $notice.add_BalloonTipShown({{
        [Console]::Out.WriteLine('{_DISPLAY_ACK}')
        [Console]::Out.Flush()
    }})
    $notice.Visible = $true
    $timer.Start()
    $notice.ShowBalloonTip(15000)
    [System.Windows.Forms.Application]::Run($context)
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}} finally {{
    if ($null -ne $notice) {{ $notice.Dispose() }}
    if ($null -ne $timer) {{ $timer.Dispose() }}
    if ($null -ne $context) {{ $context.Dispose() }}
}}
""".strip()
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
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
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            return False, f"系统通知启动失败: {exc}"
        displayed: Queue[tuple[bool, str | None]] = Queue()
        Thread(target=_read_display_confirmation, args=(process, displayed), daemon=True).start()
        try:
            return displayed.get(timeout=DISPLAY_TIMEOUT_SECONDS)
        except Empty:
            try:
                process.kill()
            except OSError:
                pass
            return False, "系统通知显示超时，未收到 Windows 显示确认，请检查通知权限或勿扰设置"


def _read_display_confirmation(process: subprocess.Popen[str], displayed: Queue[tuple[bool, str | None]]) -> None:
    confirmed = False
    diagnostic = ""
    try:
        with process.stdout:
            for line in process.stdout:
                if line.strip() == _DISPLAY_ACK and not confirmed:
                    # The Shell's BalloonTipShown callback confirms display, not just submission.
                    displayed.put((True, None))
                    confirmed = True
                elif not confirmed:
                    diagnostic = (diagnostic + line)[:500]
        process.wait()
    except (OSError, ValueError) as exc:
        diagnostic = str(exc)
    if not confirmed:
        detail = _single_line(diagnostic, 240) or "通知进程结束但未确认显示"
        displayed.put((False, f"系统通知执行失败: {detail}"))


def _single_line(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
