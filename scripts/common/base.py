import random
import threading
import traceback
from tools.finder import matchAuto
from tools.guards import runDialogGuard
from tools.tools import LOG_DIVIDER, getThreshold

WAKE_X = 0.5  # 開始前喚醒滑鼠的位置: 遊戲畫面上方中間的空白處
WAKE_Y = 0.12
WAKE_JITTER = 0.08  # 位置再隨機偏移的比例


class BaseTask:
    """料理、雪雕共用的骨架: 啟動/停止執行緒、模板比對。
    子類別覆寫 templateDir 與 _threadTargets()，每個目標函式接收一個 stopEvent，迴圈以 stopEvent.is_set() 判斷是否結束"""

    templateDir = ""

    def __init__(self, setLog, backend, config):
        self.setLog = setLog
        self.backend = backend
        self.config = config
        self._stopEvent = None
        self._threads = []

    @property
    def isStart(self):
        return self._stopEvent is not None and not self._stopEvent.is_set()

    def _threadTargets(self):
        raise NotImplementedError

    def start(self):
        if self.isStart:
            return

        # 每次啟動用新的 Event，舊執行緒就算還在收尾也不會被新一輪影響
        stopEvent = threading.Event()
        self._stopEvent = stopEvent
        self._threads = [threading.Thread(target=self._guard, args=(fn, stopEvent), daemon=True) for fn in self._threadTargets()]
        self.setLog("開始運行")
        notice = getattr(self.backend, "resolutionNotice", lambda config: None)(self.config)
        if notice:
            self.setLog(notice)
        # 安全網: 跳出確認視窗(誤點丟棄等)一律按取消，不算在主流程的執行緒裡，出錯也不會讓主流程停止
        threading.Thread(target=runDialogGuard, args=(self.backend, self.config, self.setLog, stopEvent), daemon=True).start()
        for t in self._threads:
            t.start()

    def stop(self):
        if self.isStart:
            self._stopEvent.set()
            self.setLog("停止運行")
            self.setLog(LOG_DIVIDER)

    def _guard(self, fn, stopEvent):
        """執行緒內發生例外時記錄下來並停止，避免執行緒默默死掉但介面仍顯示運行中"""
        try:
            fn(stopEvent)
        except Exception as e:
            traceback.print_exc()
            self.setLog(f"❌ 發生錯誤，已停止運行：{e}")
            stopEvent.set()
            self.setLog(LOG_DIVIDER)

    def _gameBox(self):
        """遊戲畫面在螢幕上的範圍 (x, y, w, h)；前景模式取遊戲視窗，模擬器模式是整張截圖"""
        rect = self.backend.getGameRect()
        if rect:
            return rect
        img, ox, oy = self.backend.captureFull()
        return ox, oy, img.shape[1], img.shape[0]

    def _wakeMouse(self, stopEvent):
        """前景模式開始前，在遊戲畫面上方中間的空白處左右鍵各點兩下，解除遊戲卡住滑鼠、畫面跟著滑鼠轉視角(模擬器模式不做事)"""
        if getattr(self.backend, "isAdb", False):
            return
        x, y, w, h = self._gameBox()
        self.backend.wakeMouse(round(x + w * (WAKE_X + random.uniform(-WAKE_JITTER, WAKE_JITTER))), round(y + h * (WAKE_Y + random.uniform(-WAKE_JITTER, WAKE_JITTER))))
        stopEvent.wait(0.3)

    def _getMatchCords(self, img, itemName):
        templatePath = f"{self.templateDir}/{itemName}.png"
        return matchAuto(self.backend, self.config, img, templatePath, getThreshold(self.config, itemName))

    @staticmethod
    def _getRelCords(baseCoords, relativeCoords):
        return (baseCoords[0] + relativeCoords[0], baseCoords[1] + relativeCoords[1])
