import keyboard
import threading
import time
from tools.tools import captureScreen, clickMouse, clickRightMouse, matchTemplate, getScaleWithResolution


class Cooking:
    def __init__(self, **kwargs):
        self.setLog = kwargs.get("setLog")
        self.config = kwargs.get("config")
        self.bubbleRegionCoord = kwargs.get("bubbleRegionCoord")
        self.startCookBtnCoord = kwargs.get("startCookBtnCoord")

        # 確認數量
        self.currentCookQty = 0
        self.expectedCookQty = int(self.config.get("expectedCookQty"))
        self.isExistCooking = False
        self.waitingCheckQtySince = None

        # 安全模式: 需待所有料理都完成
        self.isSafeMode = self.config.get("isSafeMode").lower() == "true"
        self.isFinish = False

        # 主線程: 負責偵測
        self.mainThread = None
        self.isNextStartCookBtn = False
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
        templatePath = f"templates/cooking/{itemName}.png"
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

        if not self.clickThread:
            self.clickThread = threading.Thread(target=self._handleClickThread, daemon=True)
        if not self.clickThread.is_alive():
            self.clickThread.start()

    def stop(self):
        if self.mainThread or self.clickThread:
            self.setLog("停止運行")
            self.isStart = False
            self.mainThread = None
            self.clickThread = None

    def _handleMainThread(self):
        self.setLog(f"安全模式: {"啟用" if self.isSafeMode else "未啟用"}")

        while self.isStart:
            time.sleep(float(self.config.get("detectFrequency")))

            bx, by, bw, bh = self.bubbleRegionCoord
            bubbleRegionImg = captureScreen(bx, by, bw, bh)

            startActionCords = self._getMatchCords(bubbleRegionImg, "startAction")
            fireCords = self._getMatchCords(bubbleRegionImg, "fire")
            completedCords = self._getMatchCords(bubbleRegionImg, "completed")
            timerCords = self._getMatchCords(bubbleRegionImg, "timer")

            # 檢查數量
            if not self.waitingCheckQtySince:
                if self.currentCookQty >= self.expectedCookQty:
                    time.sleep(1)
                    self.setLog("已達期望數量，開始檢查是否完成...(10s)")
                    self.waitingCheckQtySince = time.time()
            else:
                if fireCords or completedCords or timerCords:
                    self.waitingCheckQtySince = time.time()

                if self.waitingCheckQtySince and time.time() - self.waitingCheckQtySince > 10:
                    self.setLog("✅ 已完成所有烹飪數量")
                    self.stop()

            # 優先順序: 控制火侯 > 點擊完成 > 開始料理
            if fireCords:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), fireCords[0])
                    continue

            if completedCords:
                if not self.isSafeMode:
                    with self.lock:
                        self.clickCoords = self._getRelCords((bx, by), completedCords[0])
                    continue
                else:
                    if len(completedCords) == 3 or self.waitingCheckQtySince:
                        self.isFinish = True

                    if self.isFinish:
                        with self.lock:
                            self.clickCoords = self._getRelCords((bx, by), completedCords[0])
                        continue
            else:
                self.isFinish = False

            if startActionCords and not self.waitingCheckQtySince:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), (startActionCords[0]))
                    self.isNextStartCookBtn = True
                continue

            if self.isNextStartCookBtn:
                if not startActionCords and not fireCords and not completedCords and not timerCords:
                    with self.lock:
                        self.clickCoords = self.startCookBtnCoord
                    continue

            if not startActionCords and not fireCords and not completedCords and not timerCords:
                self.waitingSince = time.time()

                if time.time() - self.waitingSince > 1.5:
                    self.setLog("判斷超時 1.5s 按下 ESC")
                    keyboard.press_and_release("esc")
                    self.isNextStartCookBtn = False
                    self.waitingSince = None
            else:
                self.waitingSince = None

    def _handleClickThread(self):
        while self.isStart:
            coords = None

            with self.lock:
                coords = self.clickCoords

            if coords == self.startCookBtnCoord:
                time.sleep(1.4)

            if coords:
                clickMouse(*coords)
                time.sleep(0.2)
                clickRightMouse(*coords)  # 遊戲問題: 避免畫面跟著滑鼠移動

                # 點擊後清空，避免重複點擊
                with self.lock:
                    if coords == self.startCookBtnCoord:
                        self.isNextStartCookBtn = False
                        self.currentCookQty += 1
                        self.setLog(f"烹飪 {self.currentCookQty} / {self.expectedCookQty} 份")
                    self.clickCoords = None

            time.sleep(float(self.config.get("clickFrequency")))
