"""檢查 GitHub Releases 有沒有新版本，並下載、替換、重新啟動。

流程: 下載 Release 的 zip -> 解壓到 exe 旁的 _update 資料夾 -> 啟動一個批次檔，
等本程式結束後用 robocopy 覆蓋舊檔(config.ini 不覆蓋) -> 重新啟動新版。
只有打包後的 exe 才能自動替換；從原始碼執行只會提示有新版。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from version import VERSION

REPO = "jill83123/heartopiaHelper"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# 只接受來自本儲存庫 Release 的檔案，避免被導到其他網址
ALLOWED_PREFIX = f"https://github.com/{REPO}/releases/download/"
HEADERS = {"User-Agent": "heartopiaHelper-updater", "Accept": "application/vnd.github+json"}
UPDATE_DIR_NAME = "_update"


def isFrozen():
    return hasattr(sys, "frozen")


def appDir():
    return Path(sys.executable).parent if isFrozen() else Path(__file__).resolve().parent.parent


def parseVersion(text):
    """'v1.2.0' -> (1, 2, 0)，'v1.1' -> (1, 1, 0)；無法解析時回傳空 tuple"""
    parts = [int(n) for n in re.findall(r"\d+", str(text).split("-")[0])]
    # 補成三段，讓 "1.1" 與 "1.1.0" 視為相同版本
    return tuple(parts + [0] * (3 - len(parts))) if parts else ()


def isNewer(latest, current):
    latestParts, currentParts = parseVersion(latest), parseVersion(current)
    return bool(latestParts) and latestParts > currentParts


def fetchLatest(timeout=10):
    """查詢最新 Release。回傳 dict，查不到(沒網路、沒有 Release、格式不符)回傳 None"""
    try:
        request = urllib.request.Request(API_URL, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except Exception:
        return None

    asset = next(
        (a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".zip") and a.get("browser_download_url", "").startswith(ALLOWED_PREFIX)),
        None,
    )
    return {
        "version": data.get("tag_name", ""),
        "notes": data.get("body") or "",
        "pageUrl": data.get("html_url", ""),
        "asset": asset,
    }


def isReleasePage(url):
    """只允許開啟本儲存庫的 Release 頁面"""
    return isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/releases")


def checkUpdate():
    """回傳給前端的檢查結果"""
    latest = fetchLatest()
    result = {"current": VERSION, "canAutoUpdate": isFrozen(), "hasUpdate": False, "checkFailed": latest is None}
    if latest and isNewer(latest["version"], VERSION):
        result.update(hasUpdate=True, latest=latest["version"], notes=latest["notes"], pageUrl=latest["pageUrl"], hasAsset=latest["asset"] is not None)
    return result


def cleanupLeftovers():
    """啟動時清掉上次更新留下的暫存資料夾"""
    shutil.rmtree(appDir() / UPDATE_DIR_NAME, ignore_errors=True)


def buildScript(src, dst, exe):
    """產生更新用的批次檔內容: 等程式結束 -> 覆蓋檔案(不動 config.ini) -> 重新啟動"""
    # 這個批次檔在沒有視窗、沒有主控台的狀態下執行，所以不能用 timeout(需要主控台)或管線(find 會卡在等輸入)。
    # 改成: 試著以寫入方式開啟 exe，程式還在執行時會被鎖住而失敗，等它結束就能開啟；等待用 ping 代替。
    # 一律指定系統目錄下的程式，避免 PATH 裡有同名工具而失效
    sys32 = "%SystemRoot%\\System32"
    return "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >nul",
            ":wait",
            f'(>>"{exe}" (call )) 2>nul',
            "if errorlevel 1 (",
            f'  "{sys32}\\ping.exe" -n 2 127.0.0.1 >nul',
            "  goto wait",
            ")",
            f'if exist "{dst}\\config.ini" copy /y "{dst}\\config.ini" "{dst}\\config.ini.keep" >nul',
            f'"{sys32}\\robocopy.exe" "{src}" "{dst}" /E /XF "{dst}\\config.ini" /R:5 /W:2 /NFL /NDL /NJH /NJS /NP >nul',
            # 萬一設定檔還是被動到，用更新前的備份還原
            f'if exist "{dst}\\config.ini.keep" copy /y "{dst}\\config.ini.keep" "{dst}\\config.ini" >nul',
            f'start "" "{exe}"',
            "",
        ]
    )


class Updater:
    """在背景下載並套用更新，狀態供前端輪詢"""

    def __init__(self):
        self.status = "idle"  # idle / downloading / extracting / ready / error
        self.progress = 0.0
        self.error = ""
        self._lock = threading.Lock()

    def getState(self):
        return {"status": self.status, "progress": self.progress, "error": self.error}

    def start(self):
        """開始更新。已在進行中、或不是打包版時回傳錯誤訊息，否則回傳 None"""
        if not isFrozen():
            return "從原始碼執行無法自動更新，請用 git pull 取得新版"
        with self._lock:
            if self.status in ("downloading", "extracting"):
                return "更新進行中"
            self.status, self.progress, self.error = "downloading", 0.0, ""
        threading.Thread(target=self._run, daemon=True).start()
        return None

    def _fail(self, message):
        self.status, self.error = "error", message

    def _run(self):
        try:
            latest = fetchLatest()
            if not latest or not latest["asset"]:
                return self._fail("找不到可下載的更新檔")
            asset = latest["asset"]

            workDir = appDir() / UPDATE_DIR_NAME
            shutil.rmtree(workDir, ignore_errors=True)
            workDir.mkdir(parents=True)
            zipPath = workDir / "update.zip"
            self._download(asset, zipPath)

            self.status = "extracting"
            newDir = workDir / "new"
            with zipfile.ZipFile(zipPath) as z:
                z.extractall(newDir)
            zipPath.unlink()

            exeName = Path(sys.executable).name
            source = self._findAppRoot(newDir, exeName)
            if not source:
                return self._fail(f"更新檔裡找不到 {exeName}，檔案內容不正確")

            script = workDir / "update.bat"
            script.write_text(buildScript(source, appDir(), appDir() / exeName), encoding="utf-8")
            self._scriptPath = script
            self.status = "ready"
        except Exception as e:
            self._fail(f"更新失敗: {e}")

    def _download(self, asset, path):
        total = int(asset.get("size") or 0)
        request = urllib.request.Request(asset["browser_download_url"], headers=HEADERS)
        sha = hashlib.sha256()
        done = 0
        with urllib.request.urlopen(request, timeout=30) as response, open(path, "wb") as f:
            while chunk := response.read(256 * 1024):
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if total:
                    self.progress = min(done / total, 1.0)

        if total and done != total:
            raise RuntimeError("下載的檔案大小與預期不符")
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:") and digest[7:].lower() != sha.hexdigest():
            raise RuntimeError("檔案雜湊值不符，已放棄更新")

    @staticmethod
    def _findAppRoot(directory, exeName):
        """壓縮檔可能直接是檔案，也可能多包一層資料夾，找出含 exe 的那層"""
        if (directory / exeName).exists():
            return directory
        for child in directory.iterdir():
            if child.is_dir() and (child / exeName).exists():
                return child
        return None

    def applyAndExit(self):
        """啟動更新批次檔。呼叫端接著必須結束程式"""
        if self.status != "ready":
            return False
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd", "/c", str(self._scriptPath)], creationflags=flags, close_fds=True, cwd=str(appDir()))
        return True
