import random
import time
from scripts.base import BaseTask
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
        frequency = float(self.config.get("snowDetectFrequency"))

        while not stopEvent.wait(frequency):
            img, (bx, by) = self._shot()

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

    def _shot(self):
        """截取遊戲畫面。前景模式只取遊戲視窗的範圍(找不到視窗就取整個螢幕)，模擬器模式取整張截圖。
        回傳 (影像, 影像左上角在螢幕上的座標)，點擊座標靠它換算"""
        img, ox, oy = self.backend.captureFull()
        rect = self.backend.getGameRect()
        if rect:
            x0, y0 = max(0, rect[0] - ox), max(0, rect[1] - oy)
            x1, y1 = min(img.shape[1], rect[0] - ox + rect[2]), min(img.shape[0], rect[1] - oy + rect[3])
            if x1 - x0 >= 100 and y1 - y0 >= 100:
                return img[y0:y1, x0:x1], (ox + x0, oy + y0)
        return img, (ox, oy)

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
