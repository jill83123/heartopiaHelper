import cv2
import numpy as np
import os
import random
import threading
import time
from scripts.common.base import BaseTask
from tools.debug import DebugMixin
from tools.finder import clickBackButton, countStarRow, findSeasoningToggle, findStartCookBtn, matchBestAuto, _findInFullScreen
from tools.tools import getThreshold, getUserDataPath

IDLE_TIMEOUT = 1.5  # 泡泡區域內沒有任何圖示超過這個秒數，就點返回離開
START_BTN_TIMEOUT = 5  # 點了鍋子後，這麼久還沒看到「開始烹飪」按鈕，就點返回
POT_CLICK_GUARD = 3  # 點過鍋子後這段時間內不再點鍋子，避免舊畫面造成重複點擊
START_BTN_DELAY = 1.4  # 點開始烹飪前等待選單動畫完成
FINISH_CHECK_SECONDS = 10  # 達到數量後，連續這麼久沒有任何料理圖示才算全部完成
POT_POSITION_TOLERANCE = 30  # 點擊前確認鍋子仍在原位置的容許誤差(像素)
DISMISS_X = 0.5  # 找不到返回按鈕時點的位置(遊戲畫面上方中間的空白處)，用來關掉「新星級料理」之類點任意處關閉的提示
DISMISS_Y = 0.12
DISMISS_JITTER = 0.08  # 位置再隨機偏移的比例
WILDCARD_SLOT_DX = -45  # 食材格的 + 號到格子中心的位移(以 1600 寬為準，點格子中心才打得開食材選擇)
WILDCARD_SLOT_DY = 41
WILDCARD_PANEL_X = 0.55  # 食材選擇面板在畫面右側，從這個寬度比例以右才找萬能食材(左邊的格子裡填上後也會出現同樣的圖)
WILDCARD_MAX_SLOTS = 6  # 一次最多補幾格，避免卡住一直點
WILDCARD_PANEL_WAIT = 2.5  # 點了格子後，等食材選擇面板出現(找得到萬能食材)的秒數
SEASONING_WAIT = 0.8  # 看到開始烹飪按鈕後，神奇調料那一列通常已經在了，找不到時最多再等這麼久
SEASONING_CLICK_WAIT = 0.3  # 點了圈圈後等打勾動畫的秒數
SEASONING_TRIES = 3  # 圈圈點了之後還是沒打勾時，最多再點幾次
START_CLICK_COOLDOWN = 2.0  # 點下「開始烹飪」後這段時間內不再點任何東西。畫面會馬上回到主畫面，原位置是主畫面的按鈕(例如右下角的表情)
DISH_MATCH_SCORE = 0.85  # 食譜畫面上找得到記錄的菜名截圖的相似度門檻
DISH_MISMATCH_LIMIT = 3  # 連續幾次菜品不符就停止運行
DISH_CHECK_TRIES = 3  # 食譜畫面剛開時還在動畫，多截幾次再判定不同
STAR_WATCH_SECONDS = 4  # 收料理後這麼久內，留意提示卡上的星星
STAR_CHECK_INTERVAL = 0.4  # 留意期間，每隔多久看一次整個畫面
FIVE_STAR_COUNT = 5  # 提示卡上同一排金色星星達到這個數量就是五星
FINISH_GRACE = 1.5  # 安全模式收料理途中，偵測偶爾漏掉手套，這段時間內仍視為收菜中，不重新開始煮
FINISH_TIMEOUT = 6  # 收菜開始後，這麼久都沒再看到手套，不管鍋子有沒有都空出來，就當作收完了(避免卡住)


class Cooking(DebugMixin, BaseTask):
    templateDir = "templates/cooking"

    def __init__(self, setLog, backend, config, bubbleRegionCoord):
        super().__init__(setLog, backend, config)
        self.bubbleRegionCoord = bubbleRegionCoord

        # 確認數量
        self.currentCookQty = 0
        self.expectedCookQty = int(config.get("expectedCookQty"))
        self.waitingCheckQtySince = None

        # 安全模式: 需待所有料理都完成
        self.isSafeMode = config.get("isSafeMode").lower() == "true"
        self.safeModeQty = int(config.get("safeModeQty"))
        self.isFinish = False
        self.finishTarget = 0  # 這一輪收菜開始時看到幾個手套，收完後就會有這麼多個空鍋子
        self.lastCompletedAt = 0
        self.backMissingLogged = False

        # 五星停止: 收料理後留意提示卡上的星星，煮到五星就停止
        self.stopAtFiveStar = (config.get("stopAtFiveStar") or "").lower() == "true"

        # 萬能食材: 開始烹飪按鈕不能按時，食材格有 + 號就用萬能食材補上
        self.useWildcard = (config.get("cookUseWildcard") or "").lower() == "true"
        self.wildcardTried = False  # 這次打開食譜已經補過一輪了，補完還是不能按就停止
        self.outOfIngredients = False  # 食材不足無法再開始烹飪，只等剩下的煮完

        # 神奇調料: 每次打開食譜，右下角神奇調料那一列的圈圈都是沒勾的，要點一下打勾才會使用
        self.useSeasoning = (config.get("cookUseSeasoning") or "").lower() == "true"
        self.seasoningDone = not self.useSeasoning  # 這次打開食譜已經處理過了
        self.seasoningMissingLogged = False
        self.seasoningLogged = False

        self.starWatchUntil = 0
        self.lastStarCheck = 0

        # 菜品檢查: 有記錄菜名截圖時，每次打開食譜畫面都要確認選的是同一道菜(中途被點到別的菜就停止)
        self.dishImage = self._loadDishImage()
        self.dishLogged = False
        self.dishMismatch = 0
        self.dishVerified = self.dishImage is None  # 每次點鍋子打開食譜畫面都要重新確認

        # 偵測執行緒狀態
        self.isNextStartCookBtn = False
        self.waitingSince = None
        self.startBtnWaitingSince = None
        self.startBtnSeenAt = None
        self.potClickedAt = 0
        self.startClickedAt = 0
        self.potClickCoords = None  # 最近一次要點的鍋子位置，點擊前要確認它還在
        self.gloveClickCoords = None  # 最近一次要點的手套位置，點擊前要確認它還在

        # 偵測與點擊兩個執行緒共用
        self.lock = threading.Lock()
        self.clickCoords = None
        self.isStartBtnClick = False

    def _threadTargets(self):
        return [self._handleMainThread, self._handleClickThread]

    def _saveIdleShot(self):
        """除錯模式: 閒置超時時存下整個畫面並框出泡泡區域，用來看當時畫面上到底有什麼、為什麼沒偵測到圖示"""
        if not self._debugOn():
            return
        img, ox, oy = self.backend.captureFull()
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])
        self._origin = (ox, oy)
        bx, by, bw, bh = self.bubbleRegionCoord
        self._saveShot(img, "cookidle", mark=(bx - ox, by - oy, bw, bh))

    def _backOut(self, reason, logOnce=False):
        """點擊畫面上的返回按鈕離開目前的介面。logOnce: 找不到返回按鈕時同一段閒置期間只記一次
        (人在主畫面本來就沒有返回按鈕，不然會每隔幾秒洗一次版)"""
        clicked = clickBackButton(self.backend, self.config, topLeftOnly=True)  # 只認左上角，避免點到主畫面的任務收合箭頭
        if clicked:
            self.backMissingLogged = False
            self.setLog(reason)
        else:
            # 同一段閒置期間只點一次，而且已經在主畫面(看得到背包圖示)就不點，否則會一直點到場景
            # 非閒置情況(例如等不到開始烹飪按鈕)多半是被「新星級料理」之類的提示蓋住，主畫面的背包圖示照樣看得到，所以直接點
            tapped = not (logOnce and self.backMissingLogged) and (not logOnce or not self._isMainScreen())
            if tapped:
                self._dismissPopup()
            if not (logOnce and self.backMissingLogged):
                self.backMissingLogged = True
                note = "，已點空白處" if tapped else ""
                # 原因本身就是「找不到…」時合併成一句，其他原因(例如判斷超時)再接「找不到返回鈕」
                missing = f"{reason}和返回鈕" if reason.startswith("找不到") else f"{reason}，找不到返回鈕"
                self.setLog(f"{missing}{note}")
        self.isNextStartCookBtn = False
        self.startBtnWaitingSince = None
        self.startBtnSeenAt = None

    def _isMainScreen(self):
        """主畫面: 看得到背包圖示"""
        adb = self.config.get("controlMode", "screen").lower() == "adb"
        path = "templates/common/adb/bagBtn.png" if adb else "templates/common/bagBtn.png"
        # 用遮罩+限定範圍的 matchBest；一般比對會把樹葉、草地誤認成背包圖示，提示蓋住畫面時就以為在主畫面
        img, _, _ = self.backend.captureFull()
        return bool(matchBestAuto(self.backend, self.config, img, path, getThreshold(self.config, "bagBtn")))

    def _dismissPopup(self):
        """點遊戲畫面上方中間的空白處。煮到新星級料理之類的提示要點任意處才會關閉，畫面上又沒有返回按鈕可點"""
        x, y, w, h = self._gameBox()
        self.backend.click(round(x + w * (DISMISS_X + random.uniform(-DISMISS_JITTER, DISMISS_JITTER))), round(y + h * (DISMISS_Y + random.uniform(-DISMISS_JITTER, DISMISS_JITTER))))

    def _fillWithWildcard(self, stopEvent):
        """食材格有 + 號(食材不足)時，一格一格點開食材選擇、選萬能食材。全部補滿後面板右下角的按鈕會從灰色的「確認」
        變成亮起的「開始烹飪」，之後交回原本的流程去找按鈕、點擊。
        回傳 True 代表有補過(要重新檢查開始烹飪按鈕)，False 代表沒有 + 號、或找不到萬能食材而放棄"""
        filled = 0
        for _ in range(WILDCARD_MAX_SLOTS):
            plusCoords, _, _, _, scale = _findInFullScreen(self.backend, self.config, f"{self.templateDir}/plusBadge.png", getThreshold(self.config, "plusBadge"))
            if not plusCoords:
                break
            # 同時有好幾格時從左邊開始補
            px, py = min(plusCoords)
            self.backend.click(round(px + WILDCARD_SLOT_DX * scale), round(py + WILDCARD_SLOT_DY * scale))
            wildcard = self._waitWildcardItem(stopEvent)
            if not wildcard:
                if not stopEvent.is_set():
                    self.setLog("找不到萬能食材（可能沒有了）")
                return False
            self.backend.click(*wildcard)
            filled += 1
            if stopEvent.wait(0.6):
                return False
        if not filled:
            return False
        self.setLog(f"食材不足，已用萬能食材補上 {filled} 格")
        return not stopEvent.wait(0.8)

    def _waitWildcardItem(self, stopEvent):
        """等食材選擇面板出現，回傳萬能食材的座標，等不到回傳 None"""
        deadline = time.time() + WILDCARD_PANEL_WAIT
        while not stopEvent.wait(0.4):
            coords, _, ox, oy, _ = _findInFullScreen(self.backend, self.config, f"{self.templateDir}/wildcardItem.png", getThreshold(self.config, "wildcardItem"))
            gx, gy, gw, gh = self._gameBox()
            inPanel = [c for c in coords or [] if c[0] >= gx + gw * WILDCARD_PANEL_X]
            if inPanel:
                return inPanel[0]
            if time.time() > deadline:
                return None
        return None

    def _enableSeasoning(self, stopEvent):
        """確認神奇調料的圈圈有打勾，沒勾就點它。找不到那一列(沒有神奇調料、或食譜用不到)就算了，不影響烹飪"""
        deadline = time.time() + SEASONING_WAIT
        toggle = findSeasoningToggle(self.backend, self.config)
        while not toggle and time.time() < deadline and not stopEvent.wait(0.3):
            toggle = findSeasoningToggle(self.backend, self.config)
        if not toggle:
            if not self.seasoningMissingLogged:
                self.seasoningMissingLogged = True
                self.setLog("找不到神奇調料的選項，略過")
            return
        for _ in range(SEASONING_TRIES):
            x, y, checked = toggle
            if checked:
                if not self.seasoningLogged:
                    self.seasoningLogged = True
                    self.setLog("✅ 已使用神奇調料")
                return
            self.backend.click(x, y)
            if stopEvent.wait(SEASONING_CLICK_WAIT):
                return
            toggle = findSeasoningToggle(self.backend, self.config)
            if not toggle:
                return
        if not toggle[2]:
            self.setLog("⚠️ 神奇調料的圈圈點不起來（可能已用完）")

    def _isStillThere(self, coords, name):
        """點擊前重新截圖，確認圖示(startAction 鍋子、completed 手套)仍在要點擊的位置"""
        bx, by, bw, bh = self.bubbleRegionCoord
        found = self._getMatchCords(self.backend.capture(bx, by, bw, bh), name)
        if not found:
            return False
        return any(abs(bx + fx - coords[0]) <= POT_POSITION_TOLERANCE and abs(by + fy - coords[1]) <= POT_POSITION_TOLERANCE for fx, fy in found)

    def _fireVisible(self):
        bx, by, bw, bh = self.bubbleRegionCoord
        return bool(self._getMatchCords(self.backend.capture(bx, by, bw, bh), "fire"))

    def _loadDishImage(self):
        """讀取記錄的菜名截圖(灰階)，沒有記錄回傳 None。路徑可能含中文，用 numpy 解碼"""
        path = getUserDataPath("dish.png")
        if not (self.config.get("cookDishRecordedAt") or "").strip() or not os.path.exists(path):
            return None
        try:
            return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:
            return None

    def _isDishMatched(self, tries=DISH_CHECK_TRIES):
        """食譜畫面上找不找得到記錄的菜名(記錄和執行是同樣的解析度，直接比對)"""
        for _ in range(tries):
            img, _, _ = self.backend.captureFull()
            if self.backend.channelsSwapped:
                img = np.ascontiguousarray(img[:, :, ::-1])
            gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
            th, tw = self.dishImage.shape[:2]
            if gray.shape[0] >= th and gray.shape[1] >= tw:
                if cv2.matchTemplate(gray, self.dishImage, cv2.TM_CCOEFF_NORMED).max() >= DISH_MATCH_SCORE:
                    return True
            time.sleep(0.3)
        return False

    def _checkFiveStar(self):
        """收料理後一小段時間內，看提示卡上有沒有五顆星，有就停止"""
        now = time.time()
        if not self.stopAtFiveStar or now > self.starWatchUntil or now - self.lastStarCheck < STAR_CHECK_INTERVAL:
            return
        self.lastStarCheck = now
        if countStarRow(self.backend) >= FIVE_STAR_COUNT:
            self.setLog("🌟 煮到五星料理，停止運行")
            self.stop()

    def _handleStartCookBtn(self, stopEvent):
        # 用區域變數保存，避免點擊執行緒同時清空計時
        waitingSince = self.startBtnWaitingSince or time.time()
        self.startBtnWaitingSince = waitingSince

        btn = findStartCookBtn(self.backend, self.config)
        if btn:
            x, y, isEnabled = btn
            if isEnabled and not self.startBtnSeenAt:
                self.startBtnSeenAt = time.time()
            if self.isNextStartCookBtn and not self.seasoningDone:
                # 選單動畫從第一次看到按鈕就開始跑，勾圈圈花的時間算在動畫裡，不重設計時也不重跑一輪偵測，才不會多等
                self.seasoningDone = True
                self._enableSeasoning(stopEvent)
            if not isEnabled:
                if self.useWildcard and not self.wildcardTried:
                    self.wildcardTried = True
                    self._fillWithWildcard(stopEvent)
                    self.startBtnSeenAt = None
                    self.startBtnWaitingSince = None  # 補食材花的時間不算在等按鈕的逾時裡
                    return  # 補完回到偵測，重新確認按鈕可不可以按
                if not self.currentCookQty:  # 一份都還沒開始煮，沒有東西要等
                    self._backOut("❌ 「開始烹飪」按鈕無法點擊，停止運行")
                    self.stop()
                    return
                # 已經有在煮的就不要馬上停: 離開食譜、不再開始新的，等煮好的都收完才結束(比照達到料理數量的做法)
                self._backOut("「開始烹飪」按鈕無法點擊，不再開始新的烹飪，等剩下的煮完")
                self.expectedCookQty = self.currentCookQty
                self.outOfIngredients = True
                return
            self.wildcardTried = False

            if self.isNextStartCookBtn and not self.dishVerified:
                if not self._isDishMatched():
                    # 選錯了就不要開始烹飪，先返回、重新打開食譜再確認；連續幾次都不對才停止
                    self.dishMismatch += 1
                    if self.dishMismatch >= DISH_MISMATCH_LIMIT:
                        self._backOut(f"❌ 連續 {self.dishMismatch} 次選到的菜品與記錄的不同，停止運行（請在遊戲裡選回記錄的菜品，或更新記錄）")
                        self.stop()
                    else:
                        self._backOut(f"⚠️ 目前選的菜品與記錄的不同，不開始烹飪，重新開啟（第 {self.dishMismatch} 次）")
                    return
                self.dishMismatch = 0
                self.dishVerified = True
                if not self.dishLogged:
                    self.dishLogged = True
                    self.setLog("✅ 菜品確認相符")

            with self.lock:
                if self.isNextStartCookBtn:
                    self.clickCoords = (x, y)
                    self.isStartBtnClick = True
        elif time.time() - waitingSince > START_BTN_TIMEOUT:
            self._backOut("找不到「開始烹飪」按鈕")

    def _handleMainThread(self, stopEvent):
        self.backend.focusGame()  # 前景模式: 先把遊戲視窗拉到最上層，第一下點擊才不會只拿去啟用視窗(模擬器模式不做事)
        self._wakeMouse(stopEvent)
        recordedAt = (self.config.get("cookDishRecordedAt") or "").strip()
        self.setLog("菜品：已記錄截圖" if recordedAt else "菜品：尚未記錄")
        self.setLog(f"料理數量：{self.expectedCookQty}")
        self.setLog("安全模式：" + (f"啟用，煮滿 {self.safeModeQty} 份才收" if self.isSafeMode else "未啟用"))
        self.setLog("神奇調料：" + ("啟用" if self.useSeasoning else "未啟用"))
        self.setLog("五星停止：" + ("啟用" if self.stopAtFiveStar else "未啟用"))
        detectFrequency = float(self.config.get("detectFrequency"))

        while not stopEvent.wait(detectFrequency):

            bx, by, bw, bh = self.bubbleRegionCoord
            bubbleRegionImg = self.backend.capture(bx, by, bw, bh)

            startActionCords = self._getMatchCords(bubbleRegionImg, "startAction")
            fireCords = self._getMatchCords(bubbleRegionImg, "fire")
            completedCords = self._getMatchCords(bubbleRegionImg, "completed")
            timerCords = self._getMatchCords(bubbleRegionImg, "timer")

            # 檢查數量
            if not self.waitingCheckQtySince:
                if self.currentCookQty >= self.expectedCookQty:
                    stopEvent.wait(1)
                    self.setLog(f"{'開始檢查剩下的是否煮完' if self.outOfIngredients else '已達期望數量，開始檢查是否完成'}...({FINISH_CHECK_SECONDS}s)")
                    self.waitingCheckQtySince = time.time()
            else:
                if fireCords or completedCords or timerCords:
                    self.waitingCheckQtySince = time.time()

                if self.waitingCheckQtySince and time.time() - self.waitingCheckQtySince > FINISH_CHECK_SECONDS:
                    self.setLog("食材不足結束" if self.outOfIngredients else "✅ 已完成所有烹飪數量")
                    self.stop()

            if completedCords:
                self.starWatchUntil = time.time() + STAR_WATCH_SECONDS
            self._checkFiveStar()

            # 優先順序: 控制火侯 > 點擊完成 > 開始料理
            if fireCords:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), fireCords[0])
                    self.potClickCoords = self.gloveClickCoords = None
                    continue

            if completedCords:
                self.lastCompletedAt = time.time()
                if not self.isSafeMode:
                    with self.lock:
                        self.clickCoords = self.gloveClickCoords = self._getRelCords((bx, by), completedCords[0])
                        self.potClickCoords = None
                    continue
                else:
                    if not self.isFinish and (len(completedCords) >= self.safeModeQty or self.waitingCheckQtySince):
                        self.isFinish = True
                        self.finishTarget = len(completedCords)

                    if self.isFinish:
                        with self.lock:
                            self.clickCoords = self.gloveClickCoords = self._getRelCords((bx, by), completedCords[0])
                            self.potClickCoords = None
                        continue
            elif self.isFinish:
                # 收一個後，畫面可能被獲得提示或動畫蓋住、或視角移動而暫時看不到其他手套，這時空出來的鍋子已經看得到了，
                # 不能就此結束收菜(剩下的手套不到安全模式的數量，不會再收，反而又開始煮)。要等空鍋子的數量和收菜開始時的手套數一樣多才算收完
                idle = time.time() - self.lastCompletedAt
                if (idle > FINISH_GRACE and len(startActionCords or []) >= self.finishTarget) or idle > FINISH_TIMEOUT:
                    self.isFinish = False

            # 已點過鍋子、正在等開始烹飪按鈕時，不再點鍋子，避免舊畫面造成重複點擊而點到食譜選單；
            # 超過 3 秒選單還沒打開才視為沒點到，允許重點
            isPotClickPending = self.isNextStartCookBtn and time.time() - self.potClickedAt < POT_CLICK_GUARD
            # 已達期望數量(最後一份已點下開始烹飪)就不再開食譜，只等剩下的料理完成
            reachedQty = self.currentCookQty >= self.expectedCookQty
            if startActionCords and not reachedQty and not self.waitingCheckQtySince and not isPotClickPending and not self.isFinish:
                with self.lock:
                    self.clickCoords = self._getRelCords((bx, by), startActionCords[0])
                    self.isNextStartCookBtn = True
                    self.potClickedAt = time.time()
                    self.dishVerified = self.dishImage is None
                    self.seasoningDone = not self.useSeasoning
                    self.potClickCoords = self.clickCoords
                    self.gloveClickCoords = None
                continue

            if self.isNextStartCookBtn:
                if not startActionCords and not fireCords and not completedCords and not timerCords:
                    self._handleStartCookBtn(stopEvent)
                    continue

            if not startActionCords and not fireCords and not completedCords and not timerCords:
                if not self.waitingSince:
                    self.waitingSince = time.time()

                if time.time() - self.waitingSince > IDLE_TIMEOUT:
                    self._saveIdleShot()
                    self._backOut(f"判斷超時 {IDLE_TIMEOUT}s", logOnce=True)
                    self.waitingSince = None
            else:
                self.waitingSince = None
                self.backMissingLogged = False  # 又看得到圖示了，下次閒置再記

    def _handleClickThread(self, stopEvent):
        clickFrequency = float(self.config.get("clickFrequency"))

        while not stopEvent.is_set():
            with self.lock:
                coords = self.clickCoords
                isStartBtnClick = self.isStartBtnClick

            if isStartBtnClick:
                # 動畫從第一次看到按鈕就開始跑，偵測與確認菜品花掉的時間(模擬器截圖很慢)不用再等一次
                seenAt = self.startBtnSeenAt or time.time()
                stopEvent.wait(max(0.0, START_BTN_DELAY - (time.time() - seenAt)))
                self.startBtnSeenAt = None
                # 偵測到按鈕到真正點擊之間隔了一段時間(截圖也有延遲)，畫面可能已經變了。
                # 點之前重新截圖確認「開始烹飪」還在，並用最新的座標點；菜品也再確認一次，有問題就取消這次點擊
                if coords and not stopEvent.is_set():
                    fresh = findStartCookBtn(self.backend, self.config)
                    stale = not fresh or not fresh[2] or (self.dishImage is not None and not self._isDishMatched(tries=1))
                    if stale:
                        with self.lock:
                            if self.clickCoords == coords:
                                self.clickCoords = None
                                self.isStartBtnClick = False
                                self.dishVerified = self.dishImage is None  # 交回偵測執行緒重新確認
                        coords = None
                        isStartBtnClick = False
                    else:
                        coords = (fresh[0], fresh[1])

            if coords and time.time() - self.startClickedAt < START_CLICK_COOLDOWN:
                with self.lock:
                    if self.clickCoords == coords:
                        self.clickCoords = None
                        if not self.isStartBtnClick:  # 取消的是點鍋子: 不會有食譜畫面，別再等開始烹飪按鈕
                            self.isNextStartCookBtn = False
                            self.potClickedAt = 0
                        self.isStartBtnClick = False
                coords = None
                isStartBtnClick = False

            if coords and coords == self.gloveClickCoords and not self._isStillThere(coords, "completed"):
                # 手套已經被收走了(偵測用的是舊畫面)，原位置現在是空鍋子，再點下去會打開食譜選單，所以取消
                with self.lock:
                    if self.clickCoords == coords:
                        self.clickCoords = None
                coords = None

            if coords and coords == self.potClickCoords and self._fireVisible():
                # 偵測時偶爾漏掉鍋鏟，點之前再看一次: 有鍋鏟就取消，交給偵測執行緒改點鍋鏟
                with self.lock:
                    if self.clickCoords == coords:
                        self.clickCoords = None
                        self.isNextStartCookBtn = False
                        self.potClickedAt = 0
                coords = None

            if coords and coords == self.potClickCoords and not self._isStillThere(coords, "startAction"):
                # 畫面已變化(選單已打開或鍋子已不在)，取消這次點擊，避免點到選單裡的食譜
                with self.lock:
                    if self.clickCoords == coords:
                        self.clickCoords = None
                        self.isNextStartCookBtn = False
                        self.potClickedAt = 0
                coords = None

            if coords and not stopEvent.is_set():
                self.backend.click(*coords)
                if not isStartBtnClick:  # 按下開始烹飪後畫面馬上回到主畫面，原位置是主畫面的按鈕(右下角的表情)，右鍵會點到它
                    time.sleep(0.2)
                    self.backend.rightClick(*coords)  # 遊戲問題: 避免畫面跟著滑鼠移動

                # 點擊後清空，避免重複點擊
                with self.lock:
                    if isStartBtnClick:
                        self.startClickedAt = time.time()
                        self.isNextStartCookBtn = False
                        self.startBtnWaitingSince = None
                        self.currentCookQty += 1
                        self.setLog(f"烹飪 {self.currentCookQty} / {self.expectedCookQty} 份")
                    self.clickCoords = None
                    self.isStartBtnClick = False
                    if coords == self.potClickCoords:
                        self.potClickCoords = None  # 點過了，之後同位置出現手套時不能再拿鍋子的條件來檢查
                    if coords == self.gloveClickCoords:
                        self.gloveClickCoords = None

            stopEvent.wait(clickFrequency)
