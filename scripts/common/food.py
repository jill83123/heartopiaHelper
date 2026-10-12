"""吃食物: 看到「體力不足」的提示(或開著背包時飽食度夠低)就開背包、搜尋食物名稱、依星級挑一格吃，吃到設定的飽食度。
釣魚、採集共用，要搭配 GameUI、TimedTasks 使用(用到它們的截圖、點擊、背包操作與任務排程)。

可以「鎖定」搜尋結果(foodLockSupported 為 True 的功能，目前是採集): 第一次搜尋食物時順便開啟遊戲的「下次開啟背包保持該結果」，
之後打開背包就直接停在那份食物的搜尋結果，不用每次重新搜尋。
吃食物用到的設定名稱沿用 fishing 開頭(採集的設定在 api.py 換成這些名稱)"""

import itertools
import time
import cv2
import numpy as np
from tools.notify import notify
from tools.ocr import isReady, readFraction
from tools.tools import getThreshold, loadTemplate, matchBest
from tools.finder import resolveScale
from scripts.common.tasks import OutOfStock

RESULT_FIRST_OFFSET = (-489, 113)
RESULT_STEP = 130
RESULT_COLUMNS = 5
RESULT_MAX_CELLS = 15
LOCK_REGION = (14, 3, 62, 44)  # 鎖頭可能出現的範圍，相對格子中心 x0, y0, x1, y1(1600 寬為基準)
TILE_MIN_SATURATION = 25  # 有東西的格子底色是淡橘色(飽和度約 34~71)，空格是接近白色的背景(約 6~14)
STAR_STRIP = (-54, 37, 8, 50)  # x0, y0, x1, y1
STAR_FIRST_WIDTH = 14
STAR_STEP = 9
MAX_STARS = 5  # 食物最高星級；星級選「不限」又勾了不吃 5 星時，會跳過這一級
USES_LABEL_GAP = 3  # 「可用次數：」圖案右邊多留幾像素再開始看數字
USES_REGION_WIDTH = 60
USES_ONE_MAX_ASPECT = 0.42  # 「1」是細細一條，寬／高不會超過這個值(其他數字都在 0.55 以上)
USES_MIN_CONTRAST = 40  # 底色與字最深的地方至少差這麼多亮度，否則當作沒有字
USES_SLASH_MAX_FILL = 0.38  # 斜線比數字空，用字的像素佔外框的比例分辨
STAMINA_SEARCH = (0.08, 0.03, 0.26, 0.22)  # 體力圖示在遊戲畫面的左上角(實測中心約在寬 0.165、高 0.12 的位置)，只在這塊找: 左、上、右、下(佔畫面寬高的比例)
SATIETY_BAR = (-4, -18, 200, 18)  # 體力條範圍，相對於體力圖示中心: 左、上、右、下(1600 寬)
SATIETY_FILL_OFFSET = 3
SATIETY_DISAGREE = 12  # 兩種算法差超過這個 %，這次讀數不採用
SATIETY_MAXIMUMS = (100, 180)  # 體力條的最大值(目前看過的)，OCR 只讀到現在值時用來換算
SATIETY_FULL_MISREAD = 18  # 系統 OCR 把「100/100」讀成的數字
SATIETY_FULL_MISREAD_BAR = 90  # 讀到上面那個數字，而且條長度估算超過這個值，就是 100%
SATIETY_FULL_BAR = 97  # OCR 不可信、條長度估算到這麼滿，就當作 100%(實測 100/100 的條估出來約 99)
SATIETY_OCR_TOLERANCE = 20  # OCR 讀出的 % 和條長度估算差超過這麼多，就改用條長度的結果
SATIETY_TEXT = (35, -14, 190, 14)  # 體力條上「現在值/最大值」文字的範圍，相對於體力圖示中心: 左、上、右、下(1600 寬)
SATIETY_ROW_SKIP = 3  # 取樣列離體力條上緣的距離(避開上緣的外框與中間的數字)
STARS_NO5 = 14  # 食物星級選「0-4 星」(不限，但不吃 5 星)時存的值
FOOD_RETRY_SECONDS = 60  # 吃食物(不管成功或失敗)之後，這段時間內不再因為體力不足提示重複觸發，避免提示還沒消失就又去開背包
TYPED_WAIT = 8  # 模擬器貼上食物名稱後，最多等幾秒讓輸入框出現字
TYPED_MIN_DARK = 40  # 輸入框裡至少有幾個深色像素才算有字(1600 寬為基準前的原始像素數)
SEARCH_INPUT_OFFSET = -385  # 搜尋輸入框在「搜尋」按鈕左邊多遠
KEEP_WAIT_SECONDS = 8  # 搜尋後最多等幾秒讓結果畫面的「保持結果」開關出現(模擬器卡的時候很慢)
LOCK_SETTLE_SECONDS = 4  # 開始時固定食物搜尋結果: 按下搜尋後再多等幾秒才關背包

_ocrReported = False  # OCR 狀態是否已經提示過(程式開著期間只提示一次)


class FoodMixin:
    """吃食物。使用前先呼叫 _initFood()"""

    foodLockSupported = False  # 這個功能可不可以鎖定食物搜尋結果(還要使用者勾了「保持結果」)

    def _initFood(self):
        self._hungry = False  # 看到「體力不足，吃點東西吧」提示了，要吃食物
        self._meals = 0  # 這一輪吃了幾份食物
        self._noFoodNotified = False  # 沒食物的通知已經跳過了
        self._noMoreFood = False  # 這一輪吃到沒有食物了
        self._ate = False  # 這一輪有沒有真的吃下去
        self._keepLastNotified = False
        self._usesMissing = False  # 上一次檢查可用次數時，看得到食用按鈕卻始終沒有「可用次數」這一行
        self._usesNotFoundLogged = False  # 這一輪吃食物已經寫過「看不到可用次數」了
        self._oneShotCells = set()  # 已確認是一次性食物(沒有「可用次數」)的格子位置，下次不必再等 2 秒確認
        self._eatStart = 0  # 吃食物從搜尋結果的哪一格開始找(記住上次吃的那一格，連續吃、下次體力耗盡都沿用)；那一格本身每次還是會重新檢查鎖定、是否只剩最後一次
        self._lockedEating = False  # 正在吃鎖定的搜尋結果(背包可能停在別的篩選，找不到時要重新搜尋)
        self._foodSearchPos = None  # 搜尋食物時背包「搜尋」圖示的位置，用來推算食物格子
        self._foodLocked = False  # 背包已經鎖在食物的搜尋結果
        self._lockOnly = False  # 開始時只固定搜尋結果、不吃

    def _checkHunger(self, img):
        """體力見底時按互動鍵只會跳出「體力不足」提示，提示很快就消失，所以按完要立刻看，不能等到主迴圈"""
        if self._flag("fishingUseFood") and "food" not in self.disabled:
            self._noteHunger(img)

    def _hungerToast(self, img):
        """「體力不足，無法拋竿，吃點東西吧」或「體力不足，吃點東西吧」(兩種字句都有)"""
        return self._findToast(img, "noStamina") or self._findToast(img, "noStaminaShort")

    def _noteHunger(self, img, force=False):
        """畫面上出現「體力不足，吃點東西吧」的提示就代表飽食度見底，要吃食物。
        剛吃過(或剛失敗)的一段時間內提示可能還沒消失，不重複觸發"""
        last = self.lastDone.get("food")
        if last is not None and time.time() - last < FOOD_RETRY_SECONDS:
            return
        if not self._hungry and (force or self._hungerToast(img)):
            self._hungry = True
            self.setLog("體力不足，準備吃食物")

    def _foodUntil(self):
        """吃到飽食度達到(或超過)這個 % 為止"""
        return int(self.config.get("fishingFoodUntil", 99))

    def _findStaminaIcon(self, img):
        """體力圖示固定在遊戲畫面的左上角(地圖旁邊)，只在那一小塊找: 不會被畫面其他地方相似的圖案騙到(圖示是半透明的，分數本來就不高)，也比較快。
        手機版的圖和位置沒驗證過，維持整張找"""
        if self.isAdb:
            return self._find(img, "staminaIcon")
        gx, gy, gw, gh = self._gameRect(img)
        x0, y0 = max(0, gx), max(0, gy)
        left, top = x0 + int(gw * STAMINA_SEARCH[0]), y0 + int(gh * STAMINA_SEARCH[1])
        region = img[top : gy + int(gh * STAMINA_SEARCH[3]), left : gx + int(gw * STAMINA_SEARCH[2])]
        if region.size == 0:
            return None
        path = self._templatePath("staminaIcon")
        pos = matchBest(region, path, getThreshold(self.config, "staminaIcon"), resolveScale(self.backend, self.config, img, path))
        return (pos[0] + left, pos[1] + top) if pos else None

    def _satiety(self, img):
        """讀地圖旁邊體力條的飽食度(0~100)，畫面上沒有體力條回傳 None。
        用體力圖示定位，再數體力條裡青綠色填滿的部分佔整條的比例(誤差約 ±2%)。超過 100 時外框會變橘色(條是滿的)，直接當 100"""
        pos = self._findStaminaIcon(img)
        if not pos:
            self._satietyRead = "找不到體力圖示"
            return None
        s = self._scale()
        # 條上有「41/180」這樣的數字，優先用 OCR 讀(最準)；讀不到(沒有 OCR、字被擋住)才用下面數條長度估算
        tx0, ty0, tx1, ty1 = SATIETY_TEXT
        text = img[max(0, int(pos[1] + ty0 * s)) : int(pos[1] + ty1 * s), max(0, int(pos[0] + tx0 * s)) : int(pos[0] + tx1 * s)]
        fraction = readFraction(text)
        bar = self._barSatiety(img, pos)
        if fraction and fraction[0] > max(SATIETY_MAXIMUMS):  # 「76/100」被讀成「718」這種不可能的數字，不信 OCR
            fraction = None
        self._satietyRead = (fraction, bar)  # 除錯用: 這次讀數的依據(OCR 讀到的、條長度估的)
        if not fraction:
            return bar
        current, maximum = fraction
        if current == SATIETY_FULL_MISREAD and bar is not None and bar >= SATIETY_FULL_MISREAD_BAR:
            return 100  # 系統 OCR 會把「100/100」一律讀成「18」，但條幾乎是滿的(真的 18% 的條只有一小截)
        if not maximum:  # 只讀到 N(最大值讀壞了): 用條長度估的 % 挑最接近的最大值
            if bar is None:  # 條也估不出來(例如快空了): 數字超過 100 只可能是 180，否則當 100
                maximum = SATIETY_MAXIMUMS[0] if current <= SATIETY_MAXIMUMS[0] else SATIETY_MAXIMUMS[-1]
            else:
                maximum = min(SATIETY_MAXIMUMS, key=lambda m: abs(100 * current / m - bar))
        value = max(0, min(100, round(100 * current / maximum)))
        if bar is not None and abs(value - bar) > SATIETY_OCR_TOLERANCE:  # OCR 偶爾會漏掉一位數(88 讀成 8)，和條長度差太多就不信 OCR
            # 「100/100」會被系統 OCR 一律讀成「18」；條幾乎滿的(條長度估算滿格也只會到 99 左右)就當作 100
            return 100 if bar >= SATIETY_FULL_BAR else bar
        return value

    def _barSatiety(self, img, pos):
        """數體力條青綠色填滿的長度估算飽食度(0~100)，估不出來回傳 None"""
        s = self._scale()
        left, top, right, bottom = SATIETY_BAR
        x0, y0 = max(0, int(pos[0] + left * s)), max(0, int(pos[1] + top * s))
        region = img[y0 : int(pos[1] + bottom * s), x0 : int(pos[0] + right * s)]
        if region.size == 0:
            return None
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        teal = (hsv[:, :, 0] >= 82) & (hsv[:, :, 0] <= 96) & (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 140)
        counts = teal.sum(axis=1)
        width = counts.max()  # 整條的寬度(外框那幾列都是滿的)
        if width < 100 * s:
            return None
        orange = (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 25) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 180)
        if orange.sum(axis=1).max() >= 0.6 * width:
            return 100
        # 條中間的「36/100」數字會蓋住條的一部分，不能直接數青綠色像素，也不能在字的地方就當作填滿結束。
        # 改成每一列從填滿的左端往右，字的白色像素當作還在條裡面，一直到連續兩個「空條底色」(暗藍)為止，再取各列的中位數
        # 條是半透明的，空條的底色會隨背景變: 暗藍(色相 97~103、飽和度 150 以上、亮度 80~130)或灰藍、灰紫(色相到 120、飽和度 60~150、亮度到 175，海面場景實測底色色相約 114)，都不是青綠色
        empty = (hsv[:, :, 0] >= 90) & (hsv[:, :, 0] <= 120) & (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 70) & (hsv[:, :, 2] <= 175) & ~teal
        # 空條的底色還會是別的顏色(夜晚的草地上是棕色)，所以「既不是青綠色、也不是字的白色」而且連續一小段(排除字邊緣的混色)也算空條
        white = (hsv[:, :, 1] <= 70) & (hsv[:, :, 2] >= 190)
        solidGap = cv2.erode((~teal & ~white).astype(np.uint8), np.ones((1, 3), np.uint8)).astype(bool)
        empty = empty | solidGap
        full = np.where(counts >= 0.6 * width)[0]
        first, last = int(full[0]), int(full[-1])
        lengths = []
        rows = []
        for row in range(first, last):
            if counts[row] >= 0.93 * width:  # 外框那幾列(整條都是青綠色)不算，粗細會隨畫面縮放
                continue
            rows.append(row)
            xs = np.where(teal[row])[0]
            if len(xs) == 0:
                continue
            start = xs[0]
            pair = np.where(empty[row, start:-1] & empty[row, start + 1 :])[0]
            lengths.append((pair[0] if len(pair) else xs.max() - start))
        if not lengths:  # 裡面每一列都整列填滿
            return 100
        byRow = 100 * float(np.median(lengths)) / width
        # 第二種算法交叉確認: 看每一欄是青綠色多還是空底色多(字只佔其中幾列，不影響多數)，兩種結果差太多就不信這次讀數
        fillCols = teal[rows].sum(axis=0) > empty[rows].sum(axis=0)
        emptyCols = empty[rows].sum(axis=0) > teal[rows].sum(axis=0)
        start = int(np.where(teal[rows[0]])[0][0])
        pair = np.where(emptyCols[start:-1] & emptyCols[start + 1 :])[0]
        byCol = 100 * min(width, pair[0] if len(pair) else width) / width
        if abs(byRow - byCol) > SATIETY_DISAGREE:
            return None
        # 填滿的部分比外框的整條寬度短一截，用實測的 36→34、81→78、98→97 換算
        return max(0, min(100, round(byRow + SATIETY_FILL_OFFSET)))

    @staticmethod
    def _countStars(img, center, s):
        """數格子左下角的星星數。星星是金黃色，一顆約 14 寬、每多一顆再多約 9 寬"""
        x0, y0, x1, y1 = STAR_STRIP
        cx, cy = center
        strip = img[max(0, int(cy + y0 * s)) : int(cy + y1 * s), max(0, int(cx + x0 * s)) : int(cx + x1 * s)]
        if strip.size == 0:
            return 0
        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        mask = (hsv[:, :, 0] >= 12) & (hsv[:, :, 0] <= 32) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 180)
        cols = np.where(mask.any(axis=0))[0]
        if len(cols) == 0:
            return 0
        # 從最左邊的星星往右連續算到斷開為止(避免後面食物圖案的黃色干擾)
        end = cols[0]
        for c in cols[1:]:
            if c - end > 3 * s:
                break
            end = c
        width = (end - cols[0] + 1) / s
        return max(1, round((width - STAR_FIRST_WIDTH) / STAR_STEP) + 1)

    def _isLastUse(self, img):
        """食物說明裡「可用次數：N/M」的 N 是不是 1。沒有這一行(只能用一次的食物)或看不清楚時回傳 None"""
        pos = self._find(img, "usesLabel")
        if not pos:
            return None
        s = self._scale()
        labelWidth = loadTemplate(self._templatePath("usesLabel")).shape[1] * s
        x0 = int(pos[0] + labelWidth / 2 + USES_LABEL_GAP * s)
        region = img[max(0, int(pos[1] - 12 * s)) : int(pos[1] + 12 * s), x0 : int(x0 + USES_REGION_WIDTH * s)]
        if region.size == 0:
            return None
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        background, darkest = float(np.percentile(gray, 95)), float(gray.min())
        if background - darkest < USES_MIN_CONTRAST:
            return None
        # 字的顏色不一定一樣深，門檻取在底色到最深處的六成
        mask = (gray < background - 0.6 * (background - darkest)).astype(np.uint8)
        stats = cv2.connectedComponentsWithStats(mask)[2]
        # 每個字(x, y, 寬, 高, 面積)，由左到右；太矮的是冒號、小數點
        glyphs = sorted((tuple(int(v) for v in st) for st in stats[1:] if st[3] >= 8 * s), key=lambda g: g[0])
        if len(glyphs) < 2:
            return None
        first, second = glyphs[0], glyphs[1]
        slashNext = second[3] >= 12 * s and second[4] / (second[2] * second[3]) < USES_SLASH_MAX_FILL
        return first[2] / first[3] <= USES_ONE_MAX_ASPECT and slashNext

    def _foodCellPos(self, searchPos, k):
        s = self._scale()
        return (
            searchPos[0] + (RESULT_FIRST_OFFSET[0] + RESULT_STEP * (k % RESULT_COLUMNS)) * s,
            searchPos[1] + (RESULT_FIRST_OFFSET[1] + RESULT_STEP * (k // RESULT_COLUMNS)) * s,
        )

    def _cellLocked(self, img, pos, s):
        """格子右下角(數量的上方)有沒有深棕色的小鎖頭。只用來輔助判斷第一格是不是空的，不拿來決定要不要吃(以點開後說明視窗裡的鎖頭為準)。
        不看顏色: 食物圖案本身(例如盤子的木邊)也有棕色，會被當成鎖頭，所以用鎖頭的形狀(模板 cellLock)比對"""
        x0, y0, x1, y1 = LOCK_REGION
        cx, cy = pos
        region = img[max(0, int(cy + y0 * s)) : int(cy + y1 * s), max(0, int(cx + x0 * s)) : int(cx + x1 * s)]
        if region.size == 0:
            return False
        return matchBest(region, self._templatePath("cellLock"), getThreshold(self.config, "cellLock"), s) is not None

    def _cellHasItem(self, img, pos, s):
        """這一格有沒有東西(有東西的格子有淡橘色的底，空格是接近白色的背景)。取格子左邊中間的一點判斷"""
        x, y = int(pos[0] - 46 * s), int(pos[1])
        if not (0 <= y < img.shape[0] and 0 <= x < img.shape[1]):
            return False
        return int(cv2.cvtColor(img[y : y + 1, x : x + 1], cv2.COLOR_BGR2HSV)[0, 0, 1]) >= TILE_MIN_SATURATION

    def _shouldEat(self, bagImg):
        if self._lockOnly:  # 開始時只是要把搜尋結果固定住，不管飽不飽都走完搜尋，但不吃
            return True
        satiety = self._satiety(bagImg)  # 背包已經打開了，順便看一下，已經夠飽就不吃(讀不到就照提示吃)
        if satiety is not None and satiety >= self._foodUntil():
            self.setLog(f"飽食度 {satiety}%，已達 {self._foodUntil()}%")
            return False
        # 確定要吃了才寫「吃食物中...」(開始時只是打開背包確認，夠飽就不吃)
        self.setLog("吃食物中..." if satiety is None else f"吃食物中...（飽食度 {satiety}%，吃到 {self._foodUntil()}%）")
        return True

    def _onNoFood(self):
        """背包裡已經沒有可以吃的食物(找不到，或剩下的都只剩最後一次)。預設照舊: 這次不吃，等下個間隔再試；子類別可改成停止"""

    def _doFood(self, stopEvent):
        """吃食物。吃完回到主畫面看體力條，飽食度還低於設定值就接著吃下一份，直到高於設定值、
        沒有可以吃的(保留最後一次的食物不算)或吃到上限為止"""
        self._usesNotFoundLogged = False  # 這一輪「看不到可用次數」的日誌只寫一次
        for meal in itertools.count():  # 不限份數，吃到達標、沒食物或按停止為止
            self._ate = False
            self._noMoreFood = False
            if not self._eatOnce(stopEvent):
                return False
            if not self._ate or self._noMoreFood:
                return True
            if stopEvent.wait(1.0):
                return False
            satiety = self._readSatiety(stopEvent)
            if satiety is None:
                self.setLog("吃完後讀不到飽食度，先不繼續吃")
                return True
            if satiety >= self._foodUntil():
                return True
            self.setLog(f"飽食度 {satiety}%，還沒到 {self._foodUntil()}%，再吃一份")
        return True

    def _readSatiety(self, stopEvent, tries=4):
        """吃完讀飽食度。吃的當下數字會跳動、被特效蓋住，一次讀不到就隔一下再讀，不要馬上當作吃飽停下來"""
        for i in range(tries):
            shot = self._shot()
            satiety = self._satiety(shot)
            if satiety is not None:
                self._trace(f"吃完飽食度 {satiety}%（目標 {self._foodUntil()}%）；OCR 讀到、條長度估算: {getattr(self, '_satietyRead', None)}")
                self._saveShot(shot, "satiety")  # 對照畫面上實際的數字，確認讀得對不對
                return satiety
            self._trace(f"飽食度讀不到（第 {i + 1} 次）；OCR 讀到、條長度估算: {getattr(self, '_satietyRead', None)}")
            if i == 0:
                self._saveShot(shot, "satietyfail")  # 讀不到時的畫面，看是被特效蓋住還是辨識有問題
            if stopEvent.wait(0.6):
                return None
        return None

    def _lockWanted(self):
        return self.foodLockSupported and self._flag("fishingFoodLock")

    def _eatOnce(self, stopEvent):
        if self._foodLocked and self._foodSearchPos and self._lockWanted():
            done = self._eatLocked(stopEvent)
            if done is not None:
                return done
        name = self.config.get("fishingFoodName", "").strip()
        stars = int(self.config.get("fishingFoodStars", 0))

        searchPos = self._openBagFilter(stopEvent, self._shouldEat)
        if searchPos is False:  # 還不餓，下次再吃
            return True
        if searchPos is None:
            return False
        self._foodSearchPos = searchPos
        _, submitPos = self._waitFor("searchSubmitBtn", stopEvent, 1)
        if not submitPos:
            raise RuntimeError("找不到搜尋按鈕")
        inputPos = (submitPos[0] + SEARCH_INPUT_OFFSET * self._scale(), submitPos[1])
        self.backend.beginTextInput()
        try:
            if not self._click(inputPos, stopEvent):
                return False
            if stopEvent.wait(0.8 if self.isAdb else 0.3):
                return False
            # 輸入框沒取得焦點的話，Ctrl+V 的 V 會被遊戲當成拍照鍵，所以先確認搜尋視窗還在
            if not self._find(self._shot(), "searchSubmitBtn"):
                raise RuntimeError("搜尋視窗沒有開啟")
            self.backend.pasteText(name)
        finally:
            self.backend.endTextInput()
        if stopEvent.wait(0.4):
            return False
        if self.isAdb and not self._waitTyped(inputPos, stopEvent):
            return False
        if not self._click(submitPos, stopEvent):
            return False
        if stopEvent.wait(1.0):
            return False
        if not self._onSearchResults(stopEvent):
            return False

        return self._eatFromResults(self._shot(), searchPos, name, stars, stopEvent)

    def _inputHasText(self, img, inputPos):
        """搜尋輸入框裡有沒有字。沒輸入時是灰色的提示字(最暗的像素也有 166 左右)，輸入後的字是深色的"""
        s = self._scale()
        x, y = int(inputPos[0]), int(inputPos[1])
        region = img[max(0, y - int(20 * s)) : y + int(20 * s), max(0, x - int(240 * s)) : x + int(170 * s)]
        if region.size == 0:
            return False
        return int((cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) < 110).sum()) >= TYPED_MIN_DARK

    def _waitTyped(self, inputPos, stopEvent):
        """模擬器卡的時候貼上的文字很晚才出現(甚至沒貼進去)，輸入框看到字了才繼續；最多等 TYPED_WAIT 秒，逾時照舊往下。
        回傳 False 代表已收到停止"""
        deadline = time.time() + TYPED_WAIT
        while time.time() < deadline:
            if self._inputHasText(self._shot(), inputPos):
                return not stopEvent.wait(0.5)  # 字出現後，其他東西(保持結果開關等)還要一點時間畫出來
            if stopEvent.wait(0.4):
                return False
        self.setLog("⚠️ 輸入框一直沒看到食物名稱，仍嘗試繼續")
        return True

    def _onSearchResults(self, stopEvent):
        """要鎖定時，在搜尋結果畫面(左下角)按下「保持結果」開關。模擬器卡的時候結果很晚才出現，所以反覆找一陣子。回傳 False 代表已收到停止"""
        if not self._lockWanted() or self._foodLocked:
            return True
        deadline = time.time() + KEEP_WAIT_SECONDS
        keepPos = None
        while True:
            keepPos = self._find(self._shot(), "keepOff")
            if keepPos or time.time() >= deadline or stopEvent.wait(0.5):
                break
        if keepPos:
            if not self._click(keepPos, stopEvent):
                return False
            self._foodLocked = True
            self.setLog(f"✅ 已鎖定食物搜尋結果：{self.config.get('fishingFoodName', '').strip()}")
        elif not stopEvent.is_set():
            self.setLog("⚠️ 找不到「保持結果」開關，這次沒有鎖定")
        return True

    def _eatLocked(self, stopEvent):
        """背包已鎖在食物的搜尋結果: 開背包直接點食物。畫面不是預期的搜尋結果時回傳 None，改走完整的搜尋流程"""
        if not self._clickWhenVisible("bagBtn", stopEvent, 2):
            raise RuntimeError("找不到背包圖示")
        if stopEvent.wait(0.9):
            return False
        img = self._shot()
        if not (self._find(img, "endFilterBtn") or self._find(img, "endSearchBtn")):
            self.setLog("背包沒有停在食物的搜尋結果，改用搜尋")
            self._foodLocked = False
            return None if self._closeBag(stopEvent) else False
        if not self._shouldEat(img):
            return self._closeBag(stopEvent)
        name = self.config.get("fishingFoodName", "").strip()
        stars = int(self.config.get("fishingFoodStars", 0))
        self._lockedEating = True
        try:
            result = self._eatFromResults(img, self._foodSearchPos, name, stars, stopEvent)
        finally:
            self._lockedEating = False
        if result == "retry":  # 背包停在別的篩選(例如園藝用品): 取消鎖定，背包已關，改走完整搜尋
            self._foodLocked = False
            return None
        return result

    def _lockFoodAtStart(self, stopEvent):
        """剛開始執行時先搜尋一次食物並開啟「保持結果」，之後要吃時就直接點，不用等到餓了才第一次搜尋。這次不吃"""
        if not (self._flag("fishingUseFood") and self._lockWanted()) or "food" in self.disabled:
            return
        self.setLog("先鎖定食物搜尋結果")
        self._lockOnly = True
        try:
            self._runTask("food", self._doFood, stopEvent)
        finally:
            self._lockOnly = False
        self._failCount.pop("food", None)  # 開始時鎖定失敗不算吃食物失敗，不然真正要吃時少了一次機會
        self.lastDone.pop("food", None)  # 不是真的吃過，不要擋住之後的「體力不足」判斷
        self._hungry = False
        self._closeBag(stopEvent)

    def _eatFromResults(self, img, searchPos, name, stars, stopEvent):
        """吃搜尋結果裡的食物。吃完飽食度還低於設定值時，背包不關、搜尋結果也不重搜，直接接著吃下一份"""
        if self._lockOnly:
            # 搜尋剛按下去，模擬器卡的時候結果還沒套用，太早關背包可能固定不成功，多等一下
            if stopEvent.wait(LOCK_SETTLE_SECONDS):
                return False
            return self._closeBag(stopEvent)
        for meal in itertools.count():  # 不限份數，吃到達標、沒食物或按停止為止
            result = self._eatPass(img, searchPos, name, stars, stopEvent, meal > 0, self._eatStart)
            if result != "again":
                return result
            self.setLog(f"飽食度還沒到 {self._foodUntil()}%，再吃一份")
            if stopEvent.wait(0.3):
                return False
            img = self._shot()
        return self._closeBag(stopEvent)

    def _eatPass(self, img, searchPos, name, stars, stopEvent, ateBefore=False, start=0):
        """在搜尋結果裡依序找可以吃的那一格並食用。可使用多次的食物每一份各佔一格(各自有剩餘次數)，
        勾了「保留最後一次不吃」時，剩最後一次的那格就跳過、往下一格找，都沒有才放棄。
        ateBefore: 這一輪已經吃過了，這次找不到就是吃完了，不算錯誤。回傳 "again" 代表吃了一份、背包維持開著，飽食度還不夠"""
        scale = self._scale()
        ban5 = stars == STARS_NO5  # 「0-4 星」: 不限星級，但 5 星的不吃
        if ban5:
            stars = 0
        seen = []
        tried = 0
        locked = 0
        for k in range(start, RESULT_MAX_CELLS):
            pos = self._foodCellPos(searchPos, k)
            count = self._countStars(img, pos, scale)
            # 食物從第一格依序排下去，遇到空格就是搜尋結果的盡頭。第一格被判成空的時，再用星星、鎖頭確認一次，免得底色判斷失誤
            if not self._cellHasItem(img, pos, scale) and not (k == 0 and (count or self._cellLocked(img, pos, scale))):
                break
            seen.append(count)
            if stars and count != stars:  # 指定星級(沒有星星的食物算 0 星，不會符合)
                continue
            if ban5 and count >= MAX_STARS:  # 「0-4 星」: 5 星的不吃
                continue
            # 格子上的小鎖頭只是參考(食物圖案的顏色、形狀可能干擾)，要不要跳過以點開後說明視窗裡的鎖頭為準(見 _eatItem)
            tried += 1
            self._eatStart = k  # 吃了(或失敗)就從這一格接著找；這一格自己下次還是要重看(可能剛好變成最後一次)
            result = self._eatItem(pos, name, stopEvent)
            if result == "locked":
                locked += 1
                tried -= 1
                self._eatStart = k + 1  # 被鎖定的格子下次不用再點開看
                self.setLog(f"第 {k + 1} 格被鎖定，跳過")
                continue
            if result != "last":
                return result
            self.setLog(f"第 {k + 1} 格食物只剩最後一次，往下找")
        if tried == 0 and start > 0:  # 記住的那一格之後都沒有能吃的(可能背包變動、前面的格子又能吃了)，從第一格重新找一遍
            self._eatStart = 0
            return self._eatPass(img, searchPos, name, stars, stopEvent, ateBefore, 0)
        self._closeBag(stopEvent)
        if tried == 0 and not ateBefore and self._lockedEating:
            self.setLog("鎖定的結果裡找不到符合的食物，背包可能停在別的篩選，改用搜尋")
            return "retry"
        if tried == 0 and ateBefore:
            self.setLog("食物已經吃完了")
            self._noMoreFood = True
            self._onNoFood()
            return True
        if tried == 0:
            self._onNoFood()
            which = f"{stars} 星的" if stars else ("非 5 星的" if ban5 else "")
            extra = f"，{locked} 格被鎖定" if locked else ""
            self.setLog(f"背包找不到可以吃的{which}食物(搜尋結果各格星級: {seen}{extra})")
            raise OutOfStock("食物")
        self.setLog("食物都只剩最後一次，已保留不吃")
        if not self._keepLastNotified:  # 食物快吃完了，通知一次就好
            self._keepLastNotified = True
            notify(f"心動小鎮助手｜{self.roleName}", "食物都只剩最後一次，已保留不吃", flash=False)
        self._onNoFood()
        return True

    def _checkLastUse(self, stopEvent):
        """點了食物後看說明裡的「可用次數」是不是只剩 1。說明要等一下才出現，所以最多等 2 秒；
        回傳 True(最後一次)、False(還有多次)、None(沒有這一行，只能用一次的食物，或始終看不清楚)。
        看不到次數時回傳 None 但會寫進日誌，方便確認是不是辨識失敗"""
        self._usesMissing = False
        deadline = time.time() + 2.0
        img = None
        while True:
            if stopEvent.wait(0.3):
                return None
            img = self._shot()
            if self._find(img, "usesLabel"):
                break
            if time.time() >= deadline:
                # 看得到食用按鈕才是真的一次性食物；連按鈕都沒有，是點了食物卻沒開出說明(點擊沒吃到)，不能說成一次性食物
                if self._find(img, "eatBtn"):
                    self._usesMissing = True
                    if not self._usesNotFoundLogged:
                        self._usesNotFoundLogged = True
                        self.setLog("當前為一次性食物")
                return None
        last = self._isLastUse(img)
        if last is None:
            self.setLog("看到「可用次數」但讀不出數字，當作還有多次")
        return last

    def _eatItem(self, itemPos, name, stopEvent):
        """點選背包裡的食物並食用，最後關閉背包。回傳 "last" 代表這份只剩最後一次(保留不吃，背包維持開著)，"locked" 代表這份被鎖定(說明視窗右上角是紅鎖頭，或吃的時候跳出警告視窗已按再想想；背包維持開著)，"again" 代表吃了但飽食度還不夠(背包維持開著)，False 代表已收到停止"""
        t0 = time.time()
        step = lambda label: self._trace(f"吃食物 {label} +{time.time() - t0:.2f}s")  # 除錯模式下記每一步花多久，找出連續吃食物慢在哪
        if not self._click(itemPos, stopEvent):
            return False
        step("點食物")
        # 第二道鎖定判斷: 點開食物的說明視窗右上角，上鎖是紅色的鎖頭、沒鎖是灰色的開鎖(格子上的小鎖頭看不清楚時用)
        panel, eatPos = self._waitFor("eatBtn", stopEvent, 2)
        step("說明視窗出現")
        if eatPos and self._findColor(panel, "lockedIcon"):
            return "locked"
        cell = (round(itemPos[0]), round(itemPos[1]))
        if self._flag("fishingFoodKeepLast") and cell not in self._oneShotCells:
            if self._checkLastUse(stopEvent):
                return "last"
            if self._usesMissing:
                self._oneShotCells.add(cell)
        step("可用次數檢查完")
        if not self._clickWhenVisible("eatBtn", stopEvent, 2):
            self._closeBag(stopEvent)
            raise RuntimeError("找不到食物的食用按鈕")
        step("按下食用")
        if stopEvent.wait(0.6):
            return False
        # 吃到鎖定的食物會跳出警告視窗(「本次操作包含以下鎖定內容」)，按「再想想」取消，這一格跳過
        shot = self._shot()
        warn = self._find(shot, "thinkAgainBtn")
        if warn:
            if not self._click(warn, stopEvent):
                return False
            return "locked" if not stopEvent.wait(0.5) else False
        # 體力已滿時會跳出確認視窗，吃了也不會增加，直接取消
        if self._find(shot, "cancelBtn"):
            self.setLog("體力已滿，略過吃食物")
        else:
            self._ate = True
            self._meals += 1
            if stopEvent.wait(0.2):
                return False
            step("確認沒有警告")
            # 背包開著也看得到體力條；還沒確認飽食度就不關背包，多等一下(特效、跳動的數字會擋住)
            satiety = self._readSatiety(stopEvent, tries=10)
            step("讀完飽食度")
            if satiety is not None and satiety < self._foodUntil():
                return "again"
        return self._closeBag(stopEvent)

    def _logOcrStatus(self):
        """吃食物要讀飽食度，開始時告知 OCR 能不能用(不能用會改用估算，比較不準)"""
        global _ocrReported
        if not self._flag("fishingUseFood") or _ocrReported:
            return
        _ocrReported = True  # 整個程式只提示一次，之後再開始運行不重複
        if isReady():
            self.setLog("當前數字辨識：OCR")
        else:
            self.setLog("⚠️ OCR 無法使用，飽食度改用體力條長度估算，可能不準")
