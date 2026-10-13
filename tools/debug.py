"""除錯模式(設定 debugMode)的輔助: 流程追蹤寫進 debug/flow.log、把判斷用的畫面存成 debug/*.png、監看主迴圈有沒有卡住。
各功能共用，用法是繼承 DebugMixin(要有 self.config、self.backend)；沒開除錯模式時所有方法都什麼也不做"""

import os
import sys
import threading
import time
import traceback
import cv2


class DebugMixin:
    DEBUG_MAX_FILES = 100  # debug/ 裡截圖(png)的總數上限，超過就刪最舊的(約 50～130MB)
    WATCH_QUIET_SECONDS = 15  # 主迴圈這麼久沒有任何流程記錄，監看就把它目前執行到哪一行記下來

    def _debugOn(self):
        return str(self.config.get("debugMode", "False")).lower() == "true"

    @staticmethod
    def _debugFolder():
        base = os.path.dirname(sys.executable) if hasattr(sys, "frozen") else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        folder = os.path.join(base, "debug")
        os.makedirs(folder, exist_ok=True)
        return folder

    def _trace(self, msg, reset=False):
        """流程追蹤: 寫進 debug/flow.log(不進日誌視窗)，出問題時用來看程式當時為什麼那樣做。只有開啟「除錯模式」才寫"""
        if not self._debugOn():
            return
        try:
            folder = self._debugFolder()
            path = os.path.join(folder, "flow.log")
            if reset and os.path.exists(path) and os.path.getsize(path) > 200_000:  # 平常接著寫(換一次運行不清掉，才看得到上一輪)，太大才重來
                os.remove(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + msg + "\n")
            self._lastTraceAt = time.time()
        except Exception:
            pass

    def _traceChange(self, reason, label="原因"):
        """同一個原因只記一次(原因換了才再記)，例如主畫面不拋竿是被哪個條件擋住。reason 傳 None 代表重新開始計"""
        if getattr(self, "_lastReason", None) != reason:
            self._lastReason = reason
            if reason is not None:
                self._trace(f"{label}：{reason}")

    def _writeShot(self, folder, fileName, img):
        """存一張 debug 截圖。遊戲畫面左下角會顯示玩家 ID，存檔前把那一塊塗黑(只改副本，不動傳進來的影像)"""
        img = img.copy()
        h, w = img.shape[:2]
        x0, y0, x1, y1 = 0, 0, w, h
        rect = self.backend.getGameRect()  # 前景模式整個螢幕的截圖裡，遊戲只佔視窗的那一塊
        if rect:
            ox, oy = getattr(self, "_origin", (0, 0))
            x0, y0 = max(0, rect[0] - ox), max(0, rect[1] - oy)
            x1, y1 = min(w, rect[0] - ox + rect[2]), min(h, rect[1] - oy + rect[3])
        if x1 > x0 and y1 > y0:
            img[y1 - max(1, int((y1 - y0) * 0.06)) : y1, x0 : x0 + max(1, int((x1 - x0) * 0.3))] = 0
        cv2.imwrite(os.path.join(folder, fileName), img)

    def _pruneShots(self, folder, prefix, keep):
        """同一種截圖只留最近 keep 張，再把整個 debug/ 的截圖總數壓在 DEBUG_MAX_FILES 以內(刪最舊的)"""
        for name in sorted(f for f in os.listdir(folder) if f.startswith(prefix + "_"))[:-keep]:
            os.remove(os.path.join(folder, name))
        shots = sorted((os.path.getmtime(os.path.join(folder, f)), f) for f in os.listdir(folder) if f.lower().endswith(".png"))
        for _, name in shots[: -self.DEBUG_MAX_FILES]:
            os.remove(os.path.join(folder, name))

    def _saveShot(self, img, prefix, keep=10, mark=None):
        """存一張 debug 截圖(debug/<prefix>_時間.png)，同一種只留最近 keep 張。mark: (x, y, w, h) 在圖上框出偵測到的位置"""
        if not self._debugOn():
            return
        try:
            folder = self._debugFolder()
            if mark:
                img = img.copy()
                x, y, w, h = mark
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 2)
            self._writeShot(folder, time.strftime(prefix + "_%Y%m%d_%H%M%S.png"), img)
            self._pruneShots(folder, prefix, keep)
        except Exception:
            pass

    def _startWatchdog(self, stopEvent):
        """除錯模式才啟動: 在背景監看「呼叫這個函式的執行緒」(通常是主迴圈)有沒有卡住"""
        if self._debugOn():
            threading.Thread(target=self._watchLoop, args=(stopEvent, threading.get_ident()), daemon=True).start()

    def _watchLoop(self, stopEvent, threadId):
        """主迴圈超過一段時間沒有任何流程記錄，就把它目前執行到哪一行記下來，用來找出卡住的地方"""
        self._trace("【監看】已啟動")
        while not stopEvent.wait(5):
            try:
                if time.time() - getattr(self, "_lastTraceAt", 0) < self.WATCH_QUIET_SECONDS:
                    continue
                frame = sys._current_frames().get(threadId)
                if frame is None:
                    self._trace("【監看】主迴圈的執行緒已經結束")
                    return
                stack = "".join(traceback.format_stack(frame)[-6:]).rstrip()
                self._trace(f"【監看】主迴圈 {self.WATCH_QUIET_SECONDS} 秒沒有新記錄，目前執行到:\n" + stack)
                self._lastTraceAt = time.time()
            except Exception as e:
                self._trace(f"【監看】發生錯誤: {e!r}")
                return
        self._trace("【監看】結束")
