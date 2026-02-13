import keyboard
import os
import threading
import time
from tools.tools import captureScreen, clickMouse, matchTemplate, getScaleWithResolution


class SnowCarving:
    def __init__(self, **kwargs):
        self.setLog = kwargs.get("setLog")
        self.config = kwargs.get("config")
        self.snowDetectRegionCoord = kwargs.get("snowDetectRegionCoord")

        # 主線程: 負責偵測
        self.mainThread = None
        self.waitingSince = None

        # 副線程: 負責點擊
        self.clickThread = None

        # 共用
        self.isStart = False
        self.lock = threading.Lock()
        self.clickCoords = None

    def _getMatchCords(self, mainImg, itemName):
        width = int(self.config.get("screenResolution").split("x")[0])
        scale = getScaleWithResolution(width)
        templatePath = f"templates/snowCarving/{itemName}.png"
        threshold = float(self.config.get(f"{itemName}Threshold"))
        coords = matchTemplate(mainImg, templatePath, threshold, scales=[scale])
        return coords

    def _getRelCords(self, baseCoords, relativeCoords):
        baseX, baseY = baseCoords
        relX, relY = relativeCoords
        return (baseX + relX, baseY + relY)

    def start(self):
        self.isStart = True

        if not self.mainThread:
            self.mainThread = threading.Thread(target=self._handleMainThread, daemon=True)
        if not self.mainThread.is_alive():
            self.mainThread.start()
            self.setLog("開始運行")

    def stop(self):
        if self.mainThread:
            self.setLog("停止運行")
            self.isStart = False
            self.mainThread = None

    def _handleMainThread(self):
        while self.isStart:
            time.sleep(float(self.config.get("snowDetectFrequency")))

            bx, by, bw, bh = self.snowDetectRegionCoord
            detectRegionImg = captureScreen(bx, by, bw, bh)

            snowPutCords = self._getMatchCords(detectRegionImg, "snowPut")
            snowStartBtnCords = self._getMatchCords(detectRegionImg, "snowStartBtn")
            snowflakeCords = self._getMatchCords(detectRegionImg, "snowflake")
            snowCompletedCords = self._getMatchCords(detectRegionImg, "snowCompleted")

            if snowflakeCords:
                self.setLog("雪花數量: " + str(len(snowflakeCords)))
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), (snowflakeCords[0]))
                    clickMouse(*self.clickCoords)
                continue

            if snowPutCords:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), snowPutCords[0])
                    clickMouse(*self.clickCoords)
                    continue

            if snowStartBtnCords:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), snowStartBtnCords[0])
                    clickMouse(*self.clickCoords)
                continue

            if snowCompletedCords:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), (snowCompletedCords[0]))
                    clickMouse(*self.clickCoords)
                continue

            if not snowPutCords and not snowStartBtnCords and not snowflakeCords and not snowCompletedCords:
                self.waitingSince = time.time()

                if time.time() - self.waitingSince > 20:
                    self.setLog("判斷超時 20s 按下 ESC")
                    keyboard.press_and_release("esc")
                    self.waitingSince = None
            else:
                self.waitingSince = None
