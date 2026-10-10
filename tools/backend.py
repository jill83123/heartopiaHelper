import base64
import random
import cv2
import keyboard
import numpy as np
import os
import subprocess
import time
import tkinter as tk
import win32api
import win32clipboard
import win32gui
from tools.tools import (
    captureFullScreen,
    ScaleResolver,
    captureScreen,
    clickMouse,
    clickRightMouse,
    selectRegion,
    findWindow,
    focusWindow,
    APP_TITLE,
    getResourcePath,
    RegionTooSmallError,
    MIN_REGION_SIZE,
)

# 找遊戲視窗時要排除的視窗類別: 瀏覽器(Chrome、Edge 與 Electron 程式、Firefox)的分頁標題可能剛好含遊戲名稱
BROWSER_CLASSES = ("Chrome_WidgetWin_1", "MozillaWindowClass")

BASE_WIDTH = 1600  # 模板的基準寬度
SUPPORTED_RESOLUTIONS = ((1600, 900), (1366, 768), (1280, 720))  # 要和設定頁的解析度選項一致


class ScreenBackend:
    """前景模式: 截取桌面畫面、以全域滑鼠鍵盤操作，遊戲須在最上層"""

    # 截圖的 R、B 通道是對調的(captureScreen 把 mss 的 BGRA 當成 RGBA 轉換)。
    # 灰階比對不受影響，需要真實顏色的功能(如釣魚的星級)要自行對調回來
    channelsSwapped = True

    def __init__(self, gameWindowTitle=""):
        self.scaleResolver = ScaleResolver()
        self.gameWindowTitle = gameWindowTitle

    def _findGame(self):
        """遊戲視窗的 hwnd；沒設定標題或找不到回傳 None"""
        return findWindow(self.gameWindowTitle, BROWSER_CLASSES) if self.gameWindowTitle else None

    def check(self):
        # 遊戲沒開就拒絕開始，否則點擊會落在最上層的其他視窗(例如瀏覽器)
        if self.gameWindowTitle and not self._findGame():
            raise RuntimeError(f"找不到遊戲視窗「{self.gameWindowTitle}」，請先開啟遊戲")

    def capture(self, x, y, w, h):
        return captureScreen(x, y, w, h)

    def captureFull(self):
        """整個畫面，回傳 (影像, 左上角 x, 左上角 y)"""
        return captureFullScreen()

    def click(self, x, y):
        clickMouse(x, y)

    def getGameRect(self):
        """遊戲畫面(客戶區)在螢幕上的範圍 (x, y, w, h)；找不到視窗或已最小化回傳 None"""
        hwnd = self._findGame()
        if not hwnd or win32gui.IsIconic(hwnd):
            return None
        _, _, w, h = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        return (x, y, w, h) if w > 0 and h > 0 else None

    def focusGame(self):
        """把遊戲視窗拉到最前面。視窗剛切到前景時，第一下點擊只會用來啟用視窗，所以開始前要先做"""
        hwnd = self._findGame()
        if hwnd and win32gui.GetForegroundWindow() != hwnd:
            focusWindow(hwnd)
            time.sleep(0.5)

    def wakeMouse(self, x, y):
        """遊戲有時會卡住滑鼠、讓畫面跟著滑鼠轉視角，操作前先左右鍵各點兩下解除"""
        self.moveTo(x, y)
        time.sleep(0.2)
        for _ in range(2):
            clickMouse(int(x), int(y))
            time.sleep(0.1)
            clickRightMouse(int(x), int(y))
            time.sleep(0.15)

    def gameBlocker(self):
        """前景模式下，遊戲收不到操作的原因(視窗不見了、被別的視窗蓋住)；沒問題回傳 None。沒設定視窗標題時不檢查"""
        if not self.gameWindowTitle:
            return None
        hwnd = self._findGame()
        if not hwnd:
            return f"找不到遊戲視窗「{self.gameWindowTitle}」，請確認遊戲已開啟"
        if win32gui.GetForegroundWindow() != hwnd:
            return "遊戲視窗被其他視窗蓋住，前景模式需要讓遊戲保持在最上層"
        return None

    def moveTo(self, x, y):
        """先把滑鼠移過去。遊戲的按鈕要先偵測到滑鼠懸停，第一下點擊才有效"""
        win32api.SetCursorPos((int(x) - 4, int(y) - 4))
        time.sleep(0.05)
        win32api.SetCursorPos((int(x), int(y)))

    def beginTextInput(self):
        pass

    def endTextInput(self):
        pass

    def pasteText(self, text):
        """把文字貼到目前聚焦的輸入框(經剪貼簿，可輸入中文)"""
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        time.sleep(0.1)
        keyboard.send("ctrl+v")

    def rightClick(self, x, y):
        clickRightMouse(x, y)

    def pressKey(self, key):
        """按一下鍵盤按鍵(按住一小段隨機時間再放開)"""
        keyboard.press(key)
        time.sleep(random.uniform(0.05, 0.14))
        keyboard.release(key)

    def holdDown(self, x, y):
        """按住拋竿鍵(F)不放，直到 holdUp"""
        keyboard.press("f")

    def holdUp(self):
        keyboard.release("f")

    def getScaleWidth(self, config):
        resolution = config.get("screenResolution", "auto")
        if resolution == "auto":
            rect = self.getGameRect()
            if rect:
                return rect[2]
            return BASE_WIDTH  # 找不到遊戲視窗時用模板基準寬度(不縮放)
        return int(resolution.split("x")[0])

    def resolutionNotice(self, config):
        """自動偵測解析度時，開始前顯示偵測結果；寬度偏離建議範圍或找不到視窗會加上警告。手動指定時不顯示"""
        if config.get("screenResolution", "auto") != "auto":
            return None
        rect = self.getGameRect()
        if not rect:
            return "⚠️ 找不到遊戲視窗，請確認遊戲已開啟、視窗標題設定正確"
        w, h = rect[2], rect[3]
        if any(w == sw for sw, _ in SUPPORTED_RESOLUTIONS):
            return f"偵測到遊戲解析度：{w}x{h}"
        options = "、".join(f"{sw}x{sh}" for sw, sh in SUPPORTED_RESOLUTIONS)
        return f"⚠️ 偵測到遊戲解析度 {w}x{h}，不在支援的解析度內（{options}），可能辨識不到，請調整遊戲解析度"

    def selectRegion(self, hint=""):
        # 先把遊戲拉到前景(蓋過本程式的視窗)再框選，選完把本程式拉回前面
        gameHwnd = self._findGame()
        if gameHwnd:
            focusWindow(gameHwnd)
            time.sleep(0.3)
        try:
            return selectRegion(hint)
        finally:
            appHwnd = findWindow(APP_TITLE)
            if appHwnd:
                focusWindow(appHwnd)


class AdbBackend:
    """模擬器模式: 透過 ADB 截圖與點擊，視窗被遮住或最小化也能執行"""

    channelsSwapped = False
    isAdb = True
    # 輸入中文用的 ADBKeyBoard(https://github.com/senzhk/ADBKeyBoard)，要先安裝在模擬器裡
    KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
    KEYBOARD_APK = "tools/ADBKeyboard.apk"

    def __init__(self, adbPath="adb", device=""):
        self.adbPath = adbPath or "adb"
        self.device = (device or "").strip()
        self._size = None
        self.scaleResolver = ScaleResolver()
        # 大於 0 時，點擊改成按住這麼久(毫秒)再放開。遊戲的按鈕有時吃不到瞬間的 tap
        self.pressMs = 0
        self._originalIme = None

    def _run(self, args, useDevice=True, timeout=10):
        cmd = [self.adbPath]
        if useDevice and self.device:
            cmd += ["-s", self.device]
        cmd += args
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
        except FileNotFoundError:
            raise RuntimeError(f"找不到 adb: {self.adbPath}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"adb 逾時: {' '.join(args)}")
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"adb 失敗: {err}")
        return result.stdout

    def check(self):
        # 網路位址型裝置(如 127.0.0.1:5555)需先連線
        if ":" in self.device:
            self._run(["connect", self.device], useDevice=False)
        self._screencap()

    def _screencap(self):
        data = self._run(["exec-out", "screencap", "-p"], timeout=15)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("無法解析 adb 截圖")
        self._size = (img.shape[1], img.shape[0])
        return img  # BGR Numpy Array

    def capture(self, x, y, w, h):
        try:
            img = self._screencap()
        except RuntimeError:
            img = self._screencap()
        return img[y : y + h, x : x + w]

    def captureFull(self):
        return self._screencap(), 0, 0

    def click(self, x, y):
        x, y = str(int(x)), str(int(y))
        if self.pressMs > 0:
            duration = str(int(self.pressMs * random.uniform(0.7, 1.3)))
            self._run(["shell", "input", "swipe", x, y, x, y, duration])
        else:
            self._run(["shell", "input", "tap", x, y])

    def getGameRect(self):
        return None  # 模擬器畫面就是整張截圖

    def focusGame(self):
        pass

    def wakeMouse(self, x, y):
        pass  # 手機版沒有滑鼠視角問題

    def gameBlocker(self):
        return None

    def moveTo(self, x, y):
        pass

    def beginTextInput(self):
        """切換成 ADBKeyBoard。必須在點擊輸入框「之前」切換，否則輸入框會失去焦點"""
        packages = self._run(["shell", "pm", "list", "packages", "com.android.adbkeyboard"]).decode("utf-8", errors="ignore")
        if "com.android.adbkeyboard" not in packages:
            # 程式內附 ADBKeyBoard 的安裝檔，第一次用到時自動裝進模擬器
            self._run(["install", "-r", getResourcePath(self.KEYBOARD_APK)], timeout=60)
        current = self._run(["shell", "settings", "get", "secure", "default_input_method"]).decode("utf-8", errors="ignore").strip()
        if current != self.KEYBOARD_IME:
            self._originalIme = current
        self._run(["shell", "ime", "enable", self.KEYBOARD_IME])
        self._run(["shell", "ime", "set", self.KEYBOARD_IME])

    def endTextInput(self):
        """還原原本的輸入法"""
        ime, self._originalIme = self._originalIme, None
        if ime and ime != "null":
            self._run(["shell", "ime", "set", ime])

    def pasteText(self, text):
        """用 ADBKeyBoard 輸入文字(文字轉成 base64，避免命令列的編碼問題)，再送出「完成」收起鍵盤"""
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._run(["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded])
        time.sleep(0.3)
        self._run(["shell", "am", "broadcast", "-a", "ADB_EDITOR_CODE", "--ei", "code", "6"])

    def rightClick(self, x, y):
        # 右鍵僅用來處理 PC 版畫面跟著滑鼠移動的問題，手機版不需要
        pass

    def holdDown(self, x, y):
        """手指按在 (x, y) 不放，直到 holdUp"""
        self._holdAt = (str(int(x)), str(int(y)))
        self._run(["shell", "input", "motionevent", "DOWN", *self._holdAt])

    def holdUp(self):
        if getattr(self, "_holdAt", None):
            self._run(["shell", "input", "motionevent", "UP", *self._holdAt])
            self._holdAt = None

    def getScaleWidth(self, config):
        if not self._size:
            self._screencap()
        return max(self._size)

    def _selectOnImage(self, img, isPoint, hint=""):
        ih, iw = img.shape[:2]
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.title("在模擬器畫面上選取 (Esc 取消)")
        ratio = min(root.winfo_screenwidth() * 0.9 / iw, root.winfo_screenheight() * 0.85 / ih, 1.0)
        dw, dh = int(iw * ratio), int(ih * ratio)
        shown = cv2.resize(img, (dw, dh))
        png = cv2.imencode(".png", shown)[1].tobytes()
        photo = tk.PhotoImage(data=base64.b64encode(png))

        if hint:
            tk.Label(root, text=hint, bg="#ffd54a", fg="black", font=("Microsoft JhengHei", 12, "bold"), pady=6).pack(fill="x")
        canvas = tk.Canvas(root, width=dw, height=dh, highlightthickness=0, cursor="crosshair")
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)

        sel = {"start": None, "end": None, "rect": None}

        def onDown(e):
            sel["start"] = (e.x, e.y)
            if isPoint:
                sel["end"] = (e.x, e.y)
                root.destroy()
            else:
                sel["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

        def onMove(e):
            if sel["rect"] is None:
                return
            sel["end"] = (e.x, e.y)
            canvas.coords(sel["rect"], sel["start"][0], sel["start"][1], e.x, e.y)

        def onUp(e):
            if sel["start"] and not isPoint:
                sel["end"] = (e.x, e.y)
                root.destroy()

        def onEscape(e):
            sel["start"] = sel["end"] = None
            root.destroy()

        canvas.bind("<ButtonPress-1>", onDown)
        canvas.bind("<B1-Motion>", onMove)
        canvas.bind("<ButtonRelease-1>", onUp)
        root.bind("<Escape>", onEscape)
        root.protocol("WM_DELETE_WINDOW", lambda: onEscape(None))
        root.mainloop()

        if not (sel["start"] and sel["end"]):
            return None

        x0, y0 = (int(v / ratio) for v in sel["start"])
        x1, y1 = (int(v / ratio) for v in sel["end"])
        if isPoint:
            return (x0, y0)
        xMin, yMin, xMax, yMax = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        if xMax - xMin < MIN_REGION_SIZE or yMax - yMin < MIN_REGION_SIZE:
            raise RegionTooSmallError()
        return (xMin, yMin, xMax - xMin, yMax - yMin)

    def selectRegion(self, hint=""):
        return self._selectOnImage(self._screencap(), isPoint=False, hint=hint)


def createBackend(config):
    if config.get("controlMode", "screen").lower() == "adb":
        return AdbBackend(config.get("adbPath", "adb"), config.get("adbDevice", ""))
    return ScreenBackend(config.get("gameWindowTitle", ""))
