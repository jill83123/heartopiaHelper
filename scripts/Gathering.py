import datetime
import os
import random
import time
import cv2
import numpy as np
from scripts.Fishing import Fishing
from tools.digits import readCount
from tools.tools import getResourcePath, getThreshold, loadTemplate

SCAN_SECONDS = 0.5  # 主迴圈每隔多久看一次(秒)
# 植物、木頭大多 2 分鐘重生，定時時間就是這個重生基準。真人不會剛好在重生那一秒回來採，
# 所以實際等待 = 重生基準 + 一段「人的延遲」: 通常晚個幾秒才發現、動手，偶爾分心晚更久
HUMAN_DELAY_RANGE = (0.5, 4.5)
DISTRACT_CHANCE = 0.12
DISTRACT_EXTRA = (4.0, 15.0)
REACTION_RANGE = (0.3, 1.2)  # 時間到後隔多久才開始按(秒)，像真人看到才反應
SLOW_PRESS_CHANCE = 0.08  # 偶爾多停一下的機率
SLOW_PRESS_EXTRA = (1.0, 3.0)  # 多停的時間(秒)
# 工具耐久耗盡時畫面會跳出白字的提示(「工具耐久耗盡，請使用維修盒」)，背景是遊戲場景、每次都不同，
# 所以只比對白色的字形(三個色彩通道都夠亮的像素)
TOOL_BROKEN_TEMPLATE = "templates/gather/toolBroken.png"
GATHER_TEMPLATES = {"toolBroken", "filterRepair", "repairBoxCell"}  # 採集專用的模板(放在 templates/gather)
TOOL_WHITE_MIN = 225
TOOL_TOAST_REGION = 0.4  # 提示會出現在遊戲畫面的上方，只看最上面這個比例
TOOL_CHECK_DELAY = 0.7  # 按下互動鍵後，等提示出現再看畫面的時間(秒)
BROKEN_RETRY_RANGE = (3.0, 6.0)  # 丟完維修盒後，隔多久再採集(秒)
BROKEN_MAX_STREAK = 3  # 連續幾輪丟了維修盒還是耐久耗盡就停止
# 背包篩選「維修盒」後，木頭維修盒圖示的右下角是數量。範圍相對於圖示中心(左、上、右、下，1600 寬為基準)
REPAIR_COUNT_REGION = (10, 24, 50, 54)
# 採完(植物 1 次、木頭 3 次)後，目標會消失並在原地出現倒數圖示(雲裡一株芽、外圈一條進度弧)，重生前沒東西可採，
# 這時再按只會對著空氣揮、甚至把人物撞開。圖示是白色的圖案，同樣只比對白色字形。
# 圖示在目標上方(畫面中間偏上)，只看這個範圍，避免把遠處其他植物的倒數圖示也算進來
COUNTDOWN_REGION = (0.25, 0.05, 0.75, 0.65)  # 在遊戲畫面中的範圍(比例): 左、上、右、下
# 倒數圖示的外圈是一條綠色的進度弧(隨時間越縮越短)，圖示裡面的圖案每種植物都不同。
# 與其一種一種收模板，不如直接認這條弧: 亮綠色、剛好落在同一個圓上、半徑固定。草地和葉子的綠不會排成圓弧，所以不會混淆
RING_HSV = ((60, 120, 150), (95, 255, 255))  # 進度弧的色相、飽和度、亮度範圍(OpenCV 的 HSV)
RING_RADIUS = (30, 55)  # 圓弧的半徑範圍(1600 寬為基準的像素)
RING_MIN_ARC = 0.1  # 弧長至少占整圈的比例，倒數快結束時弧會變很短
RING_MAX_SPREAD = 0.07  # 弧上各點離圓心的距離，標準差不能超過半徑的這個比例
RING_MIN_AREA = 40  # 弧的最小面積(1600 寬為基準的像素)
# 採到東西時，畫面上方會跳出一個半透明的白色圓角氣泡(左邊是物品圖示，右邊是「橘子+1」之類的字)，物品名稱每次不同，
# 所以只認氣泡的樣子: 灰白色的圓角長條、大小與長寬比固定、裡面有圖示和字。用來判斷「這輪有沒有採到東西」，
# 太久沒有就代表出問題(離目標太遠、被卡住、沒東西可採…)，停止腳本
PICKUP_REGION = (0.15, 0.0, 0.85, 0.5)
PICKUP_HEIGHT = (28, 52)  # 氣泡的高、寬(1600 寬為基準的像素)
PICKUP_WIDTH = (100, 340)  # 物品名稱越長氣泡越寬
SIGN_WAIT = 4.0  # 採完一輪後，最多等幾秒看有沒有出現倒數圖示或獲得物品的氣泡
GAME_COVERED_RETRY = 2  # 遊戲視窗被蓋住時，隔多久再試一次(秒)


def findCountdownRing(part, scale=1.0):
    """在一塊畫面裡找倒數圖示外圈的綠色進度弧，回傳 (圓心 x, 圓心 y, 半徑)，找不到回傳 None。
    只看亮綠色像素，把相連的一塊塊挑出來，擬合成圓，要求半徑落在範圍內、各點都貼著圓、而且弧長有一定比例"""
    if part.size == 0:
        return None
    hsv = cv2.cvtColor(part, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(RING_HSV[0]), np.array(RING_HSV[1]))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, count):
        if stats[i][cv2.CC_STAT_AREA] < RING_MIN_AREA * scale * scale:
            continue
        ys, xs = np.nonzero(labels == i)
        xs, ys = xs.astype(np.float64), ys.astype(np.float64)
        # 代數法擬合圓: x² + y² = 2ax + 2by + c
        coef, *_ = np.linalg.lstsq(np.stack([2 * xs, 2 * ys, np.ones_like(xs)], axis=1), xs**2 + ys**2, rcond=None)
        cx, cy = coef[0], coef[1]
        radius2 = coef[2] + cx**2 + cy**2
        if radius2 <= 0:
            continue
        radius = radius2**0.5
        if not (RING_RADIUS[0] * scale <= radius <= RING_RADIUS[1] * scale):
            continue
        dist = np.hypot(xs - cx, ys - cy)
        if dist.std() > RING_MAX_SPREAD * radius:
            continue
        bins = np.unique(((np.arctan2(ys - cy, xs - cx) + np.pi) / (2 * np.pi) * 36).astype(int) % 36)
        if len(bins) / 36 >= RING_MIN_ARC:
            return float(cx), float(cy), float(radius)
    return None


class GatherBase(Fishing):
    """定時採集的共同骨架: 每隔設定的時間，在原地按「互動鍵」(電腦版 F、手機版右下角的動作鈕)指定的次數，
    並可定時或在看得到飽食度時去吃食物。吃食物、背包等操作沿用釣魚已驗證的流程，只是設定名稱換成這個功能自己的。
    吃食物可以「鎖定」: 採集幾乎不會碰背包的其他東西，第一次搜尋食物時順便開啟遊戲的「下次開啟背包保持該結果」，
    之後打開背包就直接停在那份食物的搜尋結果，不用每次重新搜尋。
    子類別指定 prefix(設定名稱的開頭)、label(日誌用的名稱)與 pressGap(連按之間的停頓範圍，等採集動作做完)"""

    prefix = ""
    label = ""
    pressGap = (1.2, 2.2)

    def __init__(self, setLog, backend, config, roleName=None):
        super().__init__(setLog, backend, config, roleName or self.label)
        self.round = 0
        self._coveredLogged = False
        self._foodSearchPos = None  # 第一次搜尋食物時背包「搜尋」圖示的位置，鎖定後用來推算食物格子
        self._foodLocked = False  # 背包已經鎖在食物的搜尋結果
        self._toolBroken = False  # 這一輪看到「工具耐久耗盡」的提示
        self._brokenStreak = 0
        self._lastIcon = time.time()  # 最近一次看到倒數圖示的時間
        self._lastBubble = time.time()  # 最近一次看到「獲得物品」氣泡的時間

    # ===== 設定 =====
    def _count(self):
        return int(self.config.get(f"{self.prefix}Count", 1))

    def _gatherInterval(self):
        return float(self.config.get(f"{self.prefix}IntervalSeconds", 122))

    def _enabledTasks(self):
        """這個功能只會定時丟維修盒、吃食物"""
        tasks = []
        if self._flag("fishingUseRepair"):
            tasks.append(("repair", self._doRepair))
        if self._flag("fishingUseFood"):
            tasks.append(("food", self._doFood))
        return [t for t in tasks if t[0] not in self.disabled]

    def _templatePath(self, name):
        if name in GATHER_TEMPLATES:
            return f"templates/gather/{name}.png"
        return super()._templatePath(name)

    # ===== 開始前檢查維修盒 =====
    def _checkRepairBoxes(self, stopEvent):
        """開始時看一下背包還有幾個木頭維修盒，估算能撐到什麼時候(每個維修盒撐一個間隔)。看不到數量也不影響後續"""
        if self._state(self._shot()) != "idle":
            self.setLog("⚠️ 畫面不在主畫面，略過檢查維修盒數量")
            return
        count = None
        found = False
        try:
            img, view = self._openBagView(stopEvent, "full")
            if view is None or not self._click(self._find(img, "searchBtn"), stopEvent):
                return
            if not self._waitFor("searchSubmitBtn", stopEvent)[1]:
                raise RuntimeError("找不到搜尋視窗")
            if not self._clickWhenVisible("filterRepair", stopEvent):
                raise RuntimeError("找不到「維修盒」分類")
            if stopEvent.wait(1.0):
                return
            img = self._shot()
            pos = self._find(img, "repairBoxCell")
            found = bool(pos)
            if pos:
                s = self._scale()
                left, top, right, bottom = REPAIR_COUNT_REGION
                region = img[max(0, int(pos[1] + top * s)) : int(pos[1] + bottom * s), max(0, int(pos[0] + left * s)) : int(pos[0] + right * s)]
                count = readCount(region, s)
        except RuntimeError as e:
            self.setLog(f"⚠️ 檢查維修盒失敗：{e}")
            self._closeBag(stopEvent)
            return
        self._closeBag(stopEvent)

        if not found:
            self._alert("背包裡找不到維修盒（木頭材質的那種），請先準備維修盒")
        elif count is None:
            self.setLog("背包有維修盒，但讀不出數量，無法預估可執行的時間")
        else:
            minutes = self._interval("repair") / 60
            if minutes * count > 24 * 60:
                self.setLog(f"背包有 {count} 個維修盒，數量足夠")
            else:
                end = datetime.datetime.now() + datetime.timedelta(minutes=minutes * count)
                self.setLog(f"背包有 {count} 個維修盒，每 {minutes:g} 分鐘丟一個，預計可執行到 {self._formatEndTime(end)}")

    @staticmethod
    def _formatEndTime(moment):
        days = (moment.date() - datetime.date.today()).days
        clock = moment.strftime("%H:%M")
        if days <= 0:
            return f"今天 {clock}"
        if days == 1:
            return f"隔日 {clock}"
        return f"{days} 天後 {clock}"

    def _shot(self):
        """前景模式只取遊戲視窗的範圍(不比對整個螢幕)，視窗外的東西不會被誤判，比對也比較快。
        點擊座標靠 _origin 換算回螢幕座標，所以把原點改成視窗左上角即可。找不到視窗(或模擬器)就維持整張"""
        img = super()._shot()
        rect = self.backend.getGameRect()
        if not rect:
            return img
        ox, oy = self._origin
        x0, y0 = max(0, rect[0] - ox), max(0, rect[1] - oy)
        x1, y1 = min(img.shape[1], rect[0] - ox + rect[2]), min(img.shape[0], rect[1] - oy + rect[3])
        if x1 - x0 < 100 or y1 - y0 < 100:
            return img
        self._origin = (ox + x0, oy + y0)
        return img[y0:y1, x0:x1]

    def _whiteGlyphScore(self, img, templatePath, region=(0, 0, 1, 1)):
        """遊戲畫面指定範圍(比例: 左、上、右、下)裡，模板的白色字形出現的程度(0~1)。背景是遊戲場景、每次都不同，
        所以只比對三個色彩通道都夠亮的像素"""
        template = (loadTemplate(templatePath) >= TOOL_WHITE_MIN).all(axis=2).astype(np.float32)
        s = self._scale()
        template = cv2.resize(template, (0, 0), fx=s, fy=s, interpolation=cv2.INTER_AREA)
        x, y, w, h = self._gameRect(img)
        left, top, right, bottom = region
        part = img[max(0, y + int(h * top)) : max(0, y + int(h * bottom)), max(0, x + int(w * left)) : max(0, x + int(w * right))]
        screen = (part >= TOOL_WHITE_MIN).all(axis=2).astype(np.float32)
        if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
            return 0.0
        return float(cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED).max())

    def _hasToolBrokenToast(self, img):
        """畫面上有沒有「工具耐久耗盡」的提示。提示出現在遊戲畫面的上方，只看上半部"""
        score = self._whiteGlyphScore(img, TOOL_BROKEN_TEMPLATE, (0, 0, 1, TOOL_TOAST_REGION))
        return score >= getThreshold(self.config, "toolBroken")

    # 植物和木頭每次要採的不一定是同一種，採蘑菇(無工具)的倒數圖示又是另一種(圓盤裡一朵白蘑菇)，所以全部都認(模板圖不存在的就略過)
    countdownTemplates = ("respawnPlant", "respawnWood", "respawnMushroom")

    def _countdownVisible(self, img):
        """目標前面有沒有倒數圖示(採完了、還沒重生)"""
        for name in self.countdownTemplates:
            path = f"templates/gather/{name}.png"
            if not os.path.exists(getResourcePath(path)):
                continue
            if self._whiteGlyphScore(img, path, COUNTDOWN_REGION) >= getThreshold(self.config, name):
                return True
        return self._countdownRingVisible(img)

    def _countdownRingVisible(self, img):
        """不管圖示裡面是什麼圖案，只要看到倒數的綠色進度弧就算(新的植物種類不用另外收模板)"""
        x, y, w, h = self._gameRect(img)
        left, top, right, bottom = COUNTDOWN_REGION
        part = img[max(0, y + int(h * top)) : max(0, y + int(h * bottom)), max(0, x + int(w * left)) : max(0, x + int(w * right))]
        return findCountdownRing(part, self._scale()) is not None

    def _pickupBubbleVisible(self, img, region=PICKUP_REGION):
        """畫面上方有沒有「獲得物品」的氣泡"""
        s = self._scale()
        x, y, w, h = self._gameRect(img)
        left, top, right, bottom = region
        part = img[max(0, y + int(h * top)) : max(0, y + int(h * bottom)), max(0, x + int(w * left)) : max(0, x + int(w * right))]
        if part.size == 0:
            return False
        high, low = part.max(axis=2).astype(np.int16), part.min(axis=2).astype(np.int16)
        mask = ((low >= 205) & (high - low <= 14)).astype(np.uint8)  # 灰白色
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # 把裡面的圖示、字補起來
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, count):
            bx, by, bw, bh, area = (int(v) for v in stats[i])
            if not (PICKUP_HEIGHT[0] * s <= bh <= PICKUP_HEIGHT[1] * s and PICKUP_WIDTH[0] * s <= bw <= PICKUP_WIDTH[1] * s):
                continue
            if not 2.5 <= bw / bh <= 9 or not 0.55 <= area / (bw * bh) <= 0.92:  # 裡面要有圖示和字，不能是整塊純色
                continue
            block = labels[by : by + bh, bx : bx + bw] == i
            k = max(3, int(bh * 0.12))  # 兩端是圓的，四個角落幾乎是空的
            corners = (block[:k, :k], block[:k, -k:], block[-k:, :k], block[-k:, -k:])
            if all(corner.mean() < 0.4 for corner in corners):
                return True
        return False

    def _noPickupMinutes(self):
        return float(self.config.get(f"{self.prefix}NoPickupMinutes", 10))

    # ===== 吃食物(可鎖定) =====
    def _onFoodTyped(self, stopEvent):
        """要鎖定時，在搜尋視窗按下「保持結果」開關"""
        if not self._flag("fishingFoodLock") or self._foodLocked:
            return True
        keepPos = self._find(self._shot(), "keepOff")
        if keepPos:
            if not self._click(keepPos, stopEvent):
                return False
            self._foodLocked = True
            self.setLog("已鎖定食物搜尋結果，之後開背包就直接吃")
        else:
            self.setLog("⚠️ 找不到「保持結果」開關，這次沒有鎖定")
        return True

    def _doFood(self, stopEvent):
        if self._foodLocked and self._foodSearchPos and self._flag("fishingFoodLock"):
            done = self._eatLocked(stopEvent)
            if done is not None:
                return done
        return super()._doFood(stopEvent)

    def _eatLocked(self, stopEvent):
        """背包已鎖在食物的搜尋結果: 開背包直接點食物。畫面不是預期的搜尋結果時回傳 None，改走完整的搜尋流程"""
        if not self._clickWhenVisible("bagBtn", stopEvent, 2):
            raise RuntimeError("找不到背包圖示")
        if stopEvent.wait(0.9):
            return False
        img = self._shot()
        self._noteSatiety(img)
        if not (self._find(img, "endFilterBtn") or self._find(img, "endSearchBtn")):
            self.setLog("背包沒有停在食物的搜尋結果，改用搜尋")
            self._foodLocked = False
            return None if self._closeBag(stopEvent) else False
        if not self._shouldEat(img):
            return self._closeBag(stopEvent)
        name = self.config.get("fishingFoodName", "").strip()
        stars = int(self.config.get("fishingFoodStars", 0))
        return self._eatFromResults(img, self._foodSearchPos, name, stars, stopEvent)

    def _onNoFood(self):
        """背包裡沒有可以吃的食物了(找不到，或剩下的都只剩最後一次): 繼續採集只會餓肚子，直接停止"""
        self._alert("背包裡沒有可以吃的食物了，已停止（請補充食物後重新開始）")
        self._stopEvent.set()

    # ===== 操作 =====
    def _press(self, img, stopEvent):
        """按一次互動鍵。回傳 False 代表已收到停止"""
        if self.isAdb:
            return self._click(self._castCenter(img)[0], stopEvent)  # 手機版的動作鈕和拋竿鈕同一個位置
        self.backend.pressKey("f")
        return not stopEvent.is_set()

    def _gather(self, img, stopEvent):
        """採集一輪: 連按設定的次數。回傳 False 代表已收到停止"""
        self.round += 1
        count = self._count()
        self.setLog(f"第 {self.round} 輪{self.label}，按 {count} 次")
        if stopEvent.wait(random.uniform(*REACTION_RANGE)):
            return False
        seenIcon = seenBubble = False
        for i in range(count):
            if i:
                pause = random.uniform(*self.pressGap)
                if random.random() < SLOW_PRESS_CHANCE:
                    pause += random.uniform(*SLOW_PRESS_EXTRA)
                if stopEvent.wait(pause):
                    return False
            if not self._press(img, stopEvent):
                return False
            if stopEvent.wait(TOOL_CHECK_DELAY):
                return False
            shot = self._shot()
            if self._hasToolBrokenToast(shot):  # 工具壞了，再按也沒用
                self._toolBroken = True
                return True
            countdown = self._countdownVisible(shot)
            seenIcon = seenIcon or countdown
            seenBubble = seenBubble or self._pickupBubbleVisible(shot)
            if i < count - 1 and countdown:  # 已經採完進入倒數，剩下的次數不用按了
                self.setLog(f"第 {i + 1} 次就採完了（出現倒數圖示），不再多按")
                break
        # 倒數圖示、氣泡會晚一點才出現，採完後多等一下確認這輪有沒有採到東西
        deadline = time.time() + SIGN_WAIT
        while not (seenIcon and seenBubble) and time.time() < deadline:
            if stopEvent.wait(0.5):
                return False
            shot = self._shot()
            seenIcon = seenIcon or self._countdownVisible(shot)
            seenBubble = seenBubble or self._pickupBubbleVisible(shot)
        now = time.time()
        if seenIcon:
            self._lastIcon = now
        if seenBubble:
            self._lastBubble = now
        if not (seenIcon or seenBubble):
            self.setLog(f"這輪沒看到倒數圖示，也沒看到獲得物品氣泡（圖示已 {int((now - self._lastIcon) // 60)} 分鐘、氣泡已 {int((now - self._lastBubble) // 60)} 分鐘沒出現）")
        return True

    def _nextDelay(self):
        delay = self._gatherInterval() + random.uniform(*HUMAN_DELAY_RANGE)
        if random.random() < DISTRACT_CHANCE:
            delay += random.uniform(*DISTRACT_EXTRA)
        return delay

    # ===== 主迴圈 =====
    def _mainLoop(self, stopEvent):
        # 吃食物、丟維修盒不在開始時馬上做，從現在起算一個間隔後才第一次執行
        self._markDone("food")
        if not self._flag("fishingRepairNow"):
            self._markDone("repair")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        if self._flag("fishingUseRepair"):
            self._checkRepairBoxes(stopEvent)
        self._lastIcon = self._lastBubble = time.time()
        nextAt = time.time()  # 開始後馬上採集第一輪

        while not stopEvent.wait(SCAN_SECONDS):
            limit = self._noPickupMinutes()
            # 倒數圖示和獲得物品氣泡兩個都超過設定的時間沒出現才停止，只要有一個出現過就不停
            if limit > 0 and time.time() - self._lastIcon > limit * 60 and time.time() - self._lastBubble > limit * 60:
                self._alert(f"已經 {limit:g} 分鐘沒看到倒數圖示，也沒看到獲得物品氣泡，已停止（請確認角色位置、工具與目標）")
                stopEvent.set()
                return
            gatherDue = time.time() >= nextAt
            if not gatherDue and not self._enabledTasks():
                continue

            img = self._shot()
            state = self._state(img)
            if state == "bag":
                self._closeBag(stopEvent)
                continue

            if state == "idle":
                due = self._dueTasks(img)
                if due:
                    for name, fn in due:
                        if stopEvent.is_set():
                            return
                        self._runTask(name, fn, stopEvent)
                        self._closeBag(stopEvent)
                    continue

            if not gatherDue:
                continue

            if self.backend.isGameCovered():
                # 前景模式的按鍵會送到最上層的視窗，遊戲被蓋住時不能按
                if not self._coveredLogged:
                    self.setLog("⚠️ 遊戲視窗被其他視窗蓋住，前景模式需要讓遊戲保持在最上層")
                    self._coveredLogged = True
                if stopEvent.wait(GAME_COVERED_RETRY):
                    return
                continue
            self._coveredLogged = False

            if state == "unknown":
                dialogPos = self._findDialog(img)
                if dialogPos:  # 擋在畫面上的對話框，點掉才能繼續
                    self._dismissDialog(dialogPos, stopEvent)
                    continue

            if not self._gather(img, stopEvent):
                return
            if self._toolBroken:
                self._toolBroken = False
                self._brokenStreak += 1
                if "repair" in self.disabled or not self._flag("fishingUseRepair") or self._brokenStreak > BROKEN_MAX_STREAK:
                    self._alert("工具耐久耗盡，無法繼續，已停止（請補充維修盒或檢查設定）")
                    stopEvent.set()
                    return
                self.setLog("工具耐久耗盡，馬上丟維修盒")
                self._runTask("repair", self._doRepair, stopEvent)
                self._closeBag(stopEvent)
                nextAt = time.time() + random.uniform(*BROKEN_RETRY_RANGE)
                continue
            self._brokenStreak = 0
            delay = self._nextDelay()
            nextAt = time.time() + delay
            self.setLog(f"下一輪約 {int(delay // 60)} 分 {int(delay % 60)} 秒後")
