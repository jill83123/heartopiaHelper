"""讓雪雕、南瓜雕刻、沙雕這類「不是 GameUI 子類別」的任務也能吃食物。
這些任務各自有自己的截圖與模板比對寫法，沒辦法直接混入 FoodMixin，所以把吃食物包成獨立的小幫手: 任務在主迴圈裡定期呼叫 poll()，
看到「體力不足」提示、而且人在主畫面(看得到背包圖示)時，就開背包吃食物，吃完關背包，任務再從目前的畫面接著做。
吃食物的邏輯、設定名稱(fishing 開頭)都和釣魚、採集共用(見 food.py)，各任務自己的設定用 foodConfig 換成 fishing 開頭"""

import time
from scripts.common.food import FoodMixin
from scripts.common.gameui import COMMON_TEMPLATE_DIR, GameUI
from scripts.common.tasks import TimedTasks
from tools.tools import CONFIG_DEFAULTS

CHECK_SECONDS = 0.7  # 多久看一次畫面有沒有「體力不足」提示(提示很快就消失，不能隔太久)
WAIT_LOG_SECONDS = 15  # 體力不足了卻一直不在主畫面(吃不了)，隔這麼久提醒一次
FOOD_KEYS = ("UseFood", "FoodKeepLast", "FoodName", "FoodStars", "FoodUntil")  # 各任務自己的吃食物設定(去掉任務名稱開頭的部分)


def foodConfig(config, prefix):
    """把任務自己的吃食物設定(prefix 開頭，例如 snow)換成 FoodMixin 看得懂的欄位(fishing 開頭)。沒有的欄位用釣魚的預設值"""
    merged = dict(config)
    for key in FOOD_KEYS:
        value = str(config.get(f"{prefix}{key}", "")).strip()
        merged[f"fishing{key}"] = value or CONFIG_DEFAULTS.get(f"fishing{key}", "")
    merged["fishingRepairEnabled"] = "False"  # 這些任務不用工具，不丟維修盒
    merged["fishingUseRepair"] = "False"
    return merged


class AutoFood(FoodMixin, TimedTasks, GameUI):
    """吃食物小幫手。沒有勾「吃食物」就什麼都不做(poll 直接回傳 False)"""

    templateDirs = ("templates/food", COMMON_TEMPLATE_DIR)

    def __init__(self, setLog, backend, config, prefix, roleName):
        super().__init__(setLog, backend, foodConfig(config, prefix), roleName)
        self._initTasks()
        self._initFood()
        self._lastCheck = 0.0
        self._lastWaitLog = 0.0

    @property
    def enabled(self):
        return self._flag("fishingUseFood")

    def _enabledTasks(self):
        return [("food", self._doFood)] if self.enabled and "food" not in self.disabled else []

    def start(self):
        """任務開始時呼叫一次: 告知數字辨識(OCR)能不能用"""
        if self.enabled:
            self._logOcrStatus()

    def poll(self, stopEvent):
        """主迴圈每一輪呼叫一次。看到「體力不足」且人在主畫面就去吃食物，回傳 True 代表這次動過畫面(吃了或嘗試吃)，呼叫端要重新截圖判斷。
        提示出現時人不在主畫面(小遊戲、擺放中、背包開著)就先記下，等回到主畫面再吃"""
        if not self._enabledTasks():
            return False
        now = time.time()
        if now - self._lastCheck < CHECK_SECONDS:
            return False
        self._lastCheck = now
        img = self._shot()
        self._noteHunger(img)
        if not self._hungry:
            return False
        if self._state(img) != "idle":
            if now - self._lastWaitLog >= WAIT_LOG_SECONDS:
                self._lastWaitLog = now
                self.setLog("體力不足，等回到主畫面再吃食物")
            return False
        self._runTask("food", self._doFood, stopEvent)
        self._closeBag(stopEvent)
        return True
