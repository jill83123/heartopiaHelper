"""Windows 桌面通知(右下角的 Toast)與工作列圖示閃爍。Toast 不需要額外套件，透過 PowerShell 呼叫系統的通知功能。

道具用完、食物吃完這類需要使用者處理的狀況，光寫在日誌裡容易沒人看到，所以另外跳出通知。
「請勿打擾／專注輔助」開著時 Toast 會被吸收，所以同時讓程式在工作列的圖示閃爍，直到使用者點開視窗。
"""

import base64
import os
import subprocess
import threading
from xml.sax.saxutils import escape

import win32con
import win32gui
from tools.tools import APP_TITLE, findWindow

# 借用 Windows PowerShell 的 AppUserModelID，沒有登錄過的程式才能顯示通知
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _script(title, message):
    def quote(text):
        return escape(text).replace("'", "''")  # XML 跳脫後，再處理 PowerShell 單引號字串裡的單引號

    xml = f'<toast><visual><binding template="ToastGeneric"><text>{quote(title)}</text><text>{quote(message)}</text></binding></visual></toast>'
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]",
            "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]",
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
            f"$xml.LoadXml('{xml}')",
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)",
        ]
    )


def flashTaskbar():
    """讓本程式視窗在工作列的圖示閃爍，直到視窗被點到前景。找不到視窗(例如從終端機直接跑)就算了"""
    try:
        hwnd = findWindow(APP_TITLE)
        if hwnd:
            flags = win32con.FLASHW_ALL | win32con.FLASHW_TIMERNOFG  # 圖示與標題列一起閃，直到視窗回到前景
            win32gui.FlashWindowEx(hwnd, flags, 0, 0)
    except Exception:
        pass


def notify(title, message):
    """跳出桌面通知並讓工作列圖示閃爍，失敗(非 Windows、被系統關閉通知等)就算了，不影響主程式"""
    if os.name != "nt":
        return
    flashTaskbar()

    def run():
        try:
            encoded = base64.b64encode(_script(title, message).encode("utf-16-le")).decode("ascii")
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=20,
                capture_output=True,
            )
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
