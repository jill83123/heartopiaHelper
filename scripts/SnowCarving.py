import random
import time
from scripts.common.base import BaseTask
from tools.finder import clickBackButton

CLICK_DELAY_RANGE = (0.02, 0.1)  # 點擊前隨機多等一小段時間(秒)，不要每次都一樣的節奏
TIMEOUT_SECONDS = 20  # 畫面上找不到任何目標超過這個秒數，就點返回離開


class SnowCarving(BaseTask):
    templateDir = "templates/snowCarving"

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self.waitingSince = None

    def _threadTargets(self):
        return [self._detectLoop]

    def _detectLoop(self, stopEvent):
        self.backend.focusGame()  # 前景模式: 先把遊戲拉到最上層，再解除卡滑鼠(模擬器模式不做事)
        self._wakeMouse(stopEvent)
        frequency = float(self.config.get("snowDetectFrequency"))

        while not stopEvent.wait(frequency):
            img, (bx, by) = self._gameShot()

            snowflakeCords = self._getMatchCords(img, "snowflake")
            if snowflakeCords:
                self.setLog("雪花數量：" + str(len(snowflakeCords)))
                self._clickFirst((bx, by), snowflakeCords, stopEvent)
                continue

            # 優先順序: 雪花 > 放置 > 開始按鈕 > 完成
            clicked = False
            for name in ("snowPut", "snowStartBtn", "snowCompleted"):
                cords = self._getMatchCords(img, name)
                if cords:
                    self._clickFirst((bx, by), cords, stopEvent)
                    clicked = True
                    break

            if clicked:
                self.waitingSince = None
                continue

            self._handleTimeout()

    def _clickFirst(self, base, cords, stopEvent):
        if stopEvent.wait(random.uniform(*CLICK_DELAY_RANGE)):
            return
        self.backend.click(*self._getRelCords(base, cords[0]))
        self.waitingSince = None

    def _handleTimeout(self):
        if not self.waitingSince:
            self.waitingSince = time.time()
            return

        if time.time() - self.waitingSince > TIMEOUT_SECONDS:
            if clickBackButton(self.backend, self.config):
                self.setLog(f"判斷超時 {TIMEOUT_SECONDS}s，已點擊返回按鈕")
            else:
                self.setLog(f"判斷超時 {TIMEOUT_SECONDS}s，但找不到返回按鈕")
            self.waitingSince = None
