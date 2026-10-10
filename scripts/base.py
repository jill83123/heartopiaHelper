import threading
import traceback
from tools.finder import matchAuto
from tools.tools import getThreshold


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
        for t in self._threads:
            t.start()

    def stop(self):
        if self.isStart:
            self._stopEvent.set()
            self.setLog("停止運行")

    def _guard(self, fn, stopEvent):
        """執行緒內發生例外時記錄下來並停止，避免執行緒默默死掉但介面仍顯示運行中"""
        try:
            fn(stopEvent)
        except Exception as e:
            traceback.print_exc()
            self.setLog(f"❌ 發生錯誤，已停止運行：{e}")
            stopEvent.set()

    def _getMatchCords(self, img, itemName):
        templatePath = f"{self.templateDir}/{itemName}.png"
        return matchAuto(self.backend, self.config, img, templatePath, getThreshold(self.config, itemName))

    @staticmethod
    def _getRelCords(baseCoords, relativeCoords):
        return (baseCoords[0] + relativeCoords[0], baseCoords[1] + relativeCoords[1])
