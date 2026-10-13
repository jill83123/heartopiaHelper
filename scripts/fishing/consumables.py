"""釣魚的消耗品: 香水（噴到有效果、補噴）與誘魚器（依水面的魚尾光柱判斷要不要放）。
要搭配 GameUI、TimedTasks 使用。香水是固定時效（120 秒），圖示會被灰色逐漸蓋住，從蓋住的角度可以估剩餘量；
誘魚器放下去水面會出現魚尾光柱，看不到光柱就是效果結束了。道具在「釣魚用品」分類裡，開背包後要先切到那個分類"""

import random
import time
import cv2
import numpy as np
from scripts.common.gameui import GAME_BASE_WIDTH
from scripts.common.tasks import MAX_TASK_FAILURES, OutOfStock
from tools.tools import loadTemplate

ITEM_DURATION = {"perfume": 120, "bait": 60}
ITEM_INTERVAL = {"perfume": 123, "bait": 63}  # 道具是固定時效，間隔不開放設定(誘魚器只在沒勾「水面有了就不放」時照這個間隔放)
PERFUME_REACTION_RANGE = (6.0, 9.0)  # 圖示要「連續」消失這麼久(秒)才補噴，避免閃爍、被遮住或偶爾認不出來就誤噴。香水很珍貴，寧可慢一點
CASTING_LOOK_SECONDS = 40  # 拋竿中看不到圖示；有香水魚很快上鉤，連續拋竿超過這麼久都沒釣到魚，就收竿回主畫面看看香水還在不在
START_PERFUME_CHECKS = 8  # 剛開始運行時，主畫面連續這麼多次看不到香水圖示才噴(約 2 秒)，避免漏看
PERFUME_ICON_GRACE = 25  # 噴完香水後，地圖下方的圖示要多久內出現(秒)，超過還沒出現就再噴
PERFUME_READ_BIAS = 6  # 從灰色角度讀出來的剩餘量會偏低(實測 57 秒時讀 44%，實際約 52%)，加上這個修正
PERFUME_LOOSE_SCORE = 0.62  # 判斷「香水還在不在」用的寬鬆門檻(旁邊的鞋子效果圖示只有約 0.57)，寧可誤認為還在，也不要誤噴
PERFUME_EXPIRED_REACTION_RANGE = (1.5, 3.0)  # 自己噴的香水，已超過持續時間又看不到圖示時，圖示要連續消失這麼久(秒)就補噴
PERFUME_EXPIRED_MISS_FRAMES = 5  # 同上，補噴前至少連續這麼多次偵測都看不到圖示
PERFUME_MISS_FRAMES = 10  # 補噴前，至少要連續這麼多次偵測都看不到圖示
PERFUME_RECHECK = 3  # 決定要噴之前，最後再各隔 0.4 秒重新截圖確認的次數
BAIT_PLACED_GRACE = 15  # 放完誘魚器後，水面的魚尾光柱要多久內出現(秒)；這段時間不重複判斷
BAIT_MISS_READS_EXPIRED = 3  # 放下去已經超過誘魚器的時效，要連續這麼多次看不到魚尾就算結束
BAIT_MISS_READS = 8  # 水面連續這麼多次都看不到魚尾，才算誘魚器沒效果了、需要再放
BAIT_CAST_MISS_SECONDS = 10  # 拋竿中看不到魚尾，要連續這麼久(秒)才收竿去放誘魚器
BAIT_NOT_SEEN_LIMIT = 2  # 連續放了這麼多次都看不到魚尾，就不再靠魚尾判斷，改回照間隔時間放
TAIL_BEAM_SIZE = (35, 200, 35, 220)  # 魚尾光柱的寬、高範圍: 最小寬、最大寬、最小高、最大高(1600 寬為基準)
TAIL_BEAM_FILL = 0.74  # 光柱的方框裡至少要有多少比例是白青色(實測光柱 0.82~0.85，容易誤判的海浪、水圈在 0.73 以下)
TAIL_BEAM_MIN_TOP = 0.12  # 光柱的上緣至少要在畫面高度的這個比例以下(實測光柱都在 0.25 以下)
TAIL_BEAM_FILL_LOW = 0.68  # 光柱被遮住或閃爍時的最低填滿比例(要搭配更高的亮度)
TAIL_BEAM_BRIGHT_LOW_FILL = 228  # 填滿只有 0.68~0.74 時，光柱裡 B、G 的平均亮度至少要這麼亮
TAIL_BEAM_BRIGHT = 215  # 光柱裡 B、G 的平均亮度至少要這麼亮(實測光柱 235 左右，光柱會閃爍變暗，所以留一點空間；浪花、水圈在 210 以下)
TAIL_CONFIRM_HITS = 2  # 最近 TAIL_CONFIRM_WINDOW 次偵測裡，至少這麼多次有光柱才算數(光柱會閃爍，不能要求連續；也避免一閃而過的誤判)
TAIL_CONFIRM_WINDOW = 5
PERFUME_COVER_MIN_CONTRAST = 15  # 淺綠底最亮與最暗至少差這麼多，才認定有被灰色蓋住
PERFUME_SHAPE_SCORE = 0.7  # 圖示被灰色蓋住時，深綠色形狀的比對分數門檻


class ConsumablesMixin:
    """香水與誘魚器。使用前先呼叫 _initConsumables()"""

    def _initConsumables(self):
        self._sprayNoIcon = 0  # 連續幾次噴了香水卻還是看不到圖示
        self._castingSince = None  # 這一段連續拋竿從什麼時候開始
        self._startMiss = 0  # 開始運行後，連續幾次在主畫面看不到香水圖示
        self._noPerfumeLogged = False
        self._missFrames = 0  # 香水圖示連續幾次偵測都看不到
        self._missingSince = None  # 香水圖示從什麼時候開始看不到
        self._reaction = 0.0
        self._leftAnchor = None  # (時間, 當時從圖示讀到的剩餘 %)
        self._lastLeftRead = 0.0  # 上次讀香水剩餘量的時間
        self._loggedLeft = 100  # 香水剩餘量日誌已經寫到幾 %
        self._iconSeen = False  # 噴完之後有沒有看過香水圖示
        self._tailRect = None  # 最近一次偵測到的光柱範圍(除錯存圖用)
        self._tailHits = []  # 最近幾次偵測有沒有魚尾光柱
        self._baitChecked = False  # 依魚尾判斷誘魚器時，這次運行有沒有確認過水面的魚尾(確認前不能拋竿)
        self._ownSpray = False  # 目前的香水是不是這次運行自己噴的(才知道它什麼時候會到期)
        self._baitSeen = False  # 放完誘魚器後有沒有看過水面的魚尾
        self._castMissSince = None  # 拋竿中從什麼時候開始連續看不到魚尾
        self._baitMiss = 0  # 水面連續幾次看不到魚尾
        self._baitNotSeen = 0  # 連續幾次放完都沒看到魚尾
        self._tailOff = False  # 魚尾偵測看起來沒作用，改回照間隔時間放誘魚器
        self._tailLogged = None  # 上次記到日誌的魚尾狀態(True 有、False 沒有、None 還沒到放誘魚器的時間)

    def _interval(self, name):
        if name in ITEM_INTERVAL:
            return ITEM_INTERVAL[name]
        return super()._interval(name)

    def _jitterCap(self, name, interval):
        """香水、誘魚器有持續時間，延後太久會有空窗期，所以延後上限 = 設定的間隔 − 持續時間(例如香水 125 秒 − 120 秒 = 最多 5 秒)"""
        cap = super()._jitterCap(name, interval)
        if name in ITEM_DURATION:
            cap = min(cap, max(0, interval - ITEM_DURATION[name]))
        return cap

    def _markDone(self, name):
        super()._markDone(name)
        if name == "bait":
            self._baitSeen = False
            self._baitMiss = 0
            self._castMissSince = None
        if name == "perfume":
            self._missingSince = None
            self._iconSeen = False
            self._ownSpray = True
            self._loggedLeft = 100
            self._leftAnchor = None

    def _perfumeEstimate(self):
        """香水現在大約剩幾 %。以最近一次從圖示讀到的剩餘量為起點，往後依經過的時間推算(拋竿中看不到圖示，這段時間靠推算)；
        還沒讀過、又是自己噴的，就從噴的時間起算；都不知道回傳 None"""
        if self._leftAnchor is not None:
            at, left = self._leftAnchor
        elif self._ownSpray and self.lastDone.get("perfume") is not None:
            at, left = self.lastDone["perfume"], 100.0
        else:
            return None
        return left - 100 * (time.time() - at) / ITEM_DURATION["perfume"]

    @staticmethod
    def _fmtLeft(percent):
        """香水剩餘 % 換成大概的時間文字(誤差本來就有幾秒，取到 5 秒)，例如「1 分 30 秒」"""
        seconds = max(0, round(ITEM_DURATION["perfume"] * percent / 100 / 5) * 5)
        minutes, seconds = divmod(seconds, 60)
        if minutes and seconds:
            return f"{minutes} 分 {seconds} 秒"
        return f"{minutes} 分" if minutes else f"{seconds} 秒"

    def _logPerfumeLeft(self, img):
        """香水剩餘量降到 25% 以下時寫一行日誌(大約剩幾分幾秒)。
        畫面上看得到圖示時，從圖示被灰色蓋住的角度讀一次校正起點；拋竿中看不到圖示，就依時間推算，不會等到回主畫面才記"""
        if self._loggedLeft <= 25 or "perfume" not in self.lastDone:
            return
        if img is not None and time.time() - self._lastLeftRead >= 1.0:
            self._lastLeftRead = time.time()
            read = self._perfumeLeft(img)
            if read is not None:
                read = min(100.0, read + PERFUME_READ_BIAS)
                guess = self._perfumeEstimate()
                if guess is not None and abs(guess - read) < 40:  # 讀數有誤差，和推算差不多就各取一半，避免數字跳來跳去
                    read = (read + guess) / 2
                self._leftAnchor = (time.time(), read)
        left = self._perfumeEstimate()
        if left is None:
            return
        for level in (25,):  # 要真的降到這一格以下才記
            if left <= level < self._loggedLeft:
                self._loggedLeft = level
                self.setLog("香水效果即將結束")
                break

    def _baitState(self, state, msg):
        """誘魚器的狀態改變時才寫日誌，不會每次偵測都重複"""
        if self._tailLogged != state:
            self._tailLogged = state
            self.setLog(msg)

    def _seenTail(self, img):
        """水面有魚尾光柱嗎(最近幾次偵測裡有幾次看到才算，光柱會閃爍，一兩幀的雜訊不算)。拋竿中也要持續偵測，不然拋竿時光柱的樣本會漏掉"""
        self._tailHits = (self._tailHits + [self._hasFishTail(img)])[-TAIL_CONFIRM_WINDOW:]
        return sum(self._tailHits) >= TAIL_CONFIRM_HITS

    def _baitMissNeeded(self):
        """要連續幾次看不到魚尾才算效果結束。誘魚器是固定時效，放下去超過時效還看不到魚尾就很確定結束了，不用再等那麼多次"""
        last = self.lastDone.get("bait")
        if last is not None and time.time() - last >= ITEM_DURATION["bait"]:
            return BAIT_MISS_READS_EXPIRED
        return BAIT_MISS_READS

    def _baitDue(self, img):
        """依水面的魚尾光柱判斷要不要放誘魚器(不看間隔時間):
        有魚尾 = 效果中，不需要放；剛放完 = 等魚尾出現；連續幾次都看不到魚尾 = 效果結束，需要放。
        放完卻一直看不到魚尾(偵測不到或沒放成功)，就改回照間隔時間放，以免一直浪費誘魚器"""
        last = self.lastDone.get("bait")
        if self._seenTail(img):
            if self._baitMiss:
                self._trace(f"魚尾又看到了，連續看不到的次數 {self._baitMiss} 歸零（框在 {self._tailRect}）")
            self._baitChecked = True
            self._baitSeen = True
            self._baitMiss = 0
            self._castMissSince = None
            self._baitNotSeen = 0
            if self._tailLogged != "active":
                self._saveShot(img, "tail", keep=20, mark=self._tailRect)
            self._baitState("active", "✅ 水面已有誘魚器")
            return False
        if last is not None and time.time() - last < BAIT_PLACED_GRACE:
            return False
        if last is not None and not self._baitSeen:
            self._baitNotSeen += 1
            self._baitSeen = True  # 這一次已經算過了
            if self._baitNotSeen >= BAIT_NOT_SEEN_LIMIT:
                self._tailOff = True
                self.setLog(f"⚠️ 連續 {self._baitNotSeen} 次放完誘魚器都看不到魚尾，不再靠魚尾判斷，改照間隔時間放")
                return self._isDue("bait", img)
        self._baitMiss += 1
        if self._baitMiss < self._baitMissNeeded():
            return False
        self._baitChecked = True
        self._baitState("needed", "水面沒有魚尾，需要放誘魚器")
        return True

    def _baitUnchecked(self):
        """依魚尾判斷誘魚器時，這次運行還沒確認過水面的魚尾(看到魚尾，或確認沒有而要放誘魚器)"""
        return (
            self._flag("fishingUseBait")
            and self._flag("fishingSkipTail")
            and not self._tailOff
            and "bait" not in self.disabled
            and not self._baitChecked
        )

    def _isDue(self, name, img, casting=False):
        if name == "bait" and casting:
            # 拋竿中也要持續看光柱(主畫面的機會太少，等回主畫面才數就要好幾條魚之後才會補)。
            # 這裡只數、不寫日誌；連續看不到到門檻就回傳 True，讓主迴圈收竿回主畫面去放
            if not self._flag("fishingSkipTail") or self._tailOff:
                return False
            last = self.lastDone.get("bait")
            if self._seenTail(img):
                if self._baitMiss:
                    self._trace(f"拋竿中魚尾又看到了，連續看不到的次數 {self._baitMiss} 歸零（框在 {self._tailRect}）")
                self._baitChecked = True
                self._baitSeen = True
                self._baitMiss = 0
                self._castMissSince = None
                return False
            if last is None or time.time() - last >= BAIT_PLACED_GRACE:
                self._baitMiss += 1
                if self._castMissSince is None:
                    self._castMissSince = time.time()
            # 咬鉤、收線的瞬間光柱也會暫時看不到，要連續看不到夠久才收竿，不然會魚還沒上來就把竿收了
            return self._baitMiss >= self._baitMissNeeded() and self._castMissSince is not None and time.time() - self._castMissSince >= BAIT_CAST_MISS_SECONDS
        if name == "bait" and self._flag("fishingSkipTail") and not self._tailOff:
            return self._baitDue(img)
        last = self.lastDone.get(name)
        if name == "perfume":
            if casting:
                # 拋竿中整個 UI 都隱藏，看不到圖示。不看噴香水的時間，只看圖示: 魚上鉤、回到主畫面時就會看到。
                # 但連續拋竿太久都沒魚(香水可能沒了)，或剛開始運行還沒確認過，就收竿回主畫面看圖示再決定噴不噴
                return last is None or (self._castingSince is not None and time.time() - self._castingSince >= CASTING_LOOK_SECONDS)
            if self._hasPerfumeIcon(img, loose=True):
                self._sprayNoIcon = 0
                self._missingSince = None
                if last is not None and not self._iconSeen:
                    self.setLog("✅ 香水已生效")
                self._iconSeen = True
                if last is None:  # 開始前已經噴過，從現在開始計時
                    left = self._perfumeLeft(img)
                    self.setLog("✅ 已有香水效果")
                    self._markDone(name)
                    if left is not None:  # 接著只在降到比現在更低的一格時才記
                        left = min(100, left + PERFUME_READ_BIAS)
                        self._leftAnchor = (time.time(), left)
                        self._loggedLeft = min((lv for lv in (25, 100) if lv >= left), default=25)
                    self._iconSeen = True
                    self._ownSpray = False  # 不知道這瓶是什麼時候噴的，不能用時間推算還剩多久
                return False
            # 地圖下方沒有香水圖示: 沒有香水效果就不能繼續釣魚，要補噴(剛噴完圖示還沒出現，先給一點緩衝)
            if last is not None and time.time() - last < PERFUME_ICON_GRACE:
                return False
            if last is None:
                # 剛開始運行: 先確認現在有沒有香水效果，連續幾次都看不到圖示才噴(有的話不能再噴，會浪費)
                self._startMiss += 1
                if self._startMiss < START_PERFUME_CHECKS:
                    return False
                if self._perfumeStillThere():
                    self._startMiss = 0
                    return False
            if last is not None:
                # 不是一發現就噴，隔一兩秒才反應
                if self._missingSince is None:
                    self._missingSince = time.time()
                    self._missFrames = 0
                    # 自己噴的香水，持續時間已經到了又看不到圖示，確定是結束了，不用等太久
                    expired = self._ownSpray and time.time() - last >= ITEM_DURATION["perfume"]
                    self._reaction = random.uniform(*(PERFUME_EXPIRED_REACTION_RANGE if expired else PERFUME_REACTION_RANGE))
                    self._missNeeded = PERFUME_EXPIRED_MISS_FRAMES if expired else PERFUME_MISS_FRAMES
                self._missFrames += 1
                if time.time() - self._missingSince < self._reaction or self._missFrames < self._missNeeded:
                    return False
                if self._perfumeStillThere():
                    return False
                self.setLog("香水效果已結束，補噴")
                self._sprayNoIcon += 1
                if self._sprayNoIcon >= MAX_TASK_FAILURES:
                    self._alert("噴了香水後仍看不到香水圖示，不再噴香水，也不繼續釣魚，請確認畫面與設定")
                    self.disabled.add(name)
                    return False
                if not self._iconSeen:  # 噴完從來沒看過圖示，多半是沒噴成功
                    self.setLog("⚠️ 香水沒有噴成功（噴了之後看不到效果圖示），再試一次，請確認背包有香水")
            return True
        return super()._isDue(name, img, casting)

    def _perfumeStillThere(self):
        """決定要噴香水之前的最後確認: 隔一小段時間重新截圖幾次，只要有一次看到圖示就不噴(香水很珍貴，誤噴比晚噴更糟)"""
        for _ in range(PERFUME_RECHECK):
            time.sleep(0.4)
            if self._hasPerfumeIcon(self._shot(), loose=True):
                self.setLog("偵測到香水圖示，不補噴")
                self._missingSince = None
                return True
        return False

    def _hasFishTail(self, img):
        """水面上有魚尾光柱嗎。魚尾本身是淡粉白色、幾乎和光柱同色，認圖案會把隨便一個畫面都當成有，
        所以改找光柱: 一塊近似正方形、很實心的白青色(R 明顯比 G、B 低、很亮、不太飽和)。
        海浪的泡沫、文字、魚的水圈都是扁平或中空的，不會符合"""
        x, y, w, h = self._gameRect(img)
        s = w / GAME_BASE_WIDTH
        region = img[max(0, y) : y + h, max(0, x) : x + w].astype(int)
        blue, green, red = region[:, :, 0], region[:, :, 1], region[:, :, 2]
        top, low = region.max(axis=2), region.min(axis=2)
        mask = ((green - red >= 14) & (blue - red >= 14) & (top >= 205) & (top - low <= 75)).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, count):
            bx, by, bw, bh, area = stats[i]
            if not (TAIL_BEAM_SIZE[0] * s <= bw <= TAIL_BEAM_SIZE[1] * s and TAIL_BEAM_SIZE[2] * s <= bh <= TAIL_BEAM_SIZE[3] * s):
                continue
            fill = area / (bw * bh)
            if fill < TAIL_BEAM_FILL_LOW or not 0.7 <= bh / bw <= 1.8:
                continue
            if by < TAIL_BEAM_MIN_TOP * region.shape[0]:  # 光柱在水面上，不會貼著畫面最上緣(那裡是天空、海面的反光，曾經誤判)
                continue
            inside = region[by : by + bh, bx : bx + bw][labels[by : by + bh, bx : bx + bw] == i]
            bright = inside[:, :2].mean()
            # 一般光柱填滿 0.8 以上；光柱閃爍、被魚影遮住一角時只有 0.7 多，這種要很亮(接近純白)才算，浪花、水圈都沒那麼亮
            if (fill >= TAIL_BEAM_FILL and bright >= TAIL_BEAM_BRIGHT) or bright >= TAIL_BEAM_BRIGHT_LOW_FILL:
                self._tailRect = (x + int(bx), y + int(by), int(bw), int(bh))  # 真的光柱幾乎是純白(B、G 平均 235 以上)，浪花、水圈沒那麼亮
                return True
        return False

    def _perfumeShape(self, img):
        """用「不會被蓋灰的深綠色部分」(瓶身、邊框)的形狀找香水圖示。回傳 (分數, 圖示範圍的彩色圖)，沒有回傳 (0, None)"""
        x, y, w, h = self._gameRect(img)
        region = img[max(0, y) : y + int(h * 0.5), max(0, x) : x + int(w * 0.45)]
        screen = self._greenMask(region)
        template = self._greenMask(loadTemplate(self._templatePath("perfumeBuff")))
        base = self._scale()
        best = (0.0, None)
        for factor in (0.88, 0.92, 0.96, 1.0, 1.04, 1.08, 1.12):
            resized = cv2.resize(template, None, fx=base * factor, fy=base * factor, interpolation=cv2.INTER_AREA)
            if resized.shape[0] > screen.shape[0] or resized.shape[1] > screen.shape[1] or resized.std() == 0:
                continue
            result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
            _, score, _, (px, py) = cv2.minMaxLoc(result)
            if score > best[0]:
                best = (float(score), region[py : py + resized.shape[0], px : px + resized.shape[1]])
        return best

    def _hasPerfumeIcon(self, img, loose=False):
        """地圖下方有香水效果圖示嗎。圖示會像計時器一樣，從上方正中間開始順時針被一層灰色慢慢蓋住，剩餘時間過半就和模板差很多，
        所以除了一般的模板比對，還要看深綠色部分的形狀"""
        if self._find(img, "perfumeBuff") is not None:
            return True
        return self._perfumeShape(img)[0] >= (PERFUME_LOOSE_SCORE if loose else PERFUME_SHAPE_SCORE)

    def _perfumeLeft(self, img):
        """從圖示被灰色蓋住的角度看香水還剩幾 %(0~100)，看不出來回傳 None。
        灰色從 12 點鐘方向順時針蓋，蓋住的角度 ÷ 360 就是已經過的比例；以每 10 度為一格，過半數的像素是灰的就算蓋住"""
        score, icon = self._perfumeShape(img)
        if icon is None or score < PERFUME_SHAPE_SCORE:
            return None
        hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV).astype(int)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        inner = (hue >= 40) & (hue <= 56) & (sat <= 110) & (val >= 110)  # 淺綠底(沒蓋住)與蓋灰的底，不含深綠的瓶身與邊框
        if inner.sum() < 30:
            return None
        # 灰色蓋層只比底色暗約一成(底 239、蓋灰 206)，用比例會漏掉；取最亮與最暗的中間值當分界，兩者差太小代表完全沒蓋
        light, dark = np.percentile(val[inner], 90), np.percentile(val[inner], 10)
        covered = inner & (val < (light + dark) / 2) if light - dark >= PERFUME_COVER_MIN_CONTRAST else np.zeros_like(inner)
        height, width = inner.shape
        ys, xs = np.mgrid[0:height, 0:width]
        bins = (np.degrees(np.arctan2(xs - (width - 1) / 2, -(ys - (height - 1) / 2))) % 360 // 10).astype(int)
        done = 0
        for b in range(36):
            cell = inner & (bins == b)
            if cell.sum() >= 3 and covered[cell].mean() < 0.5:
                break
            done += 1
        return round(100 * (1 - done / 36))

    @staticmethod
    def _greenMask(img):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        return (((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 75) & (hsv[:, :, 1] >= 140) & (hsv[:, :, 2] >= 40)) * 255).astype(np.uint8)

    def _bagView(self, img):
        """背包目前停在哪個畫面: fishing(上次留下的釣魚用品清單)，其餘同 GameUI"""
        if self._find(img, "filterHeaderFishing"):
            return "fishing"
        return super()._bagView(img)

    def _openFishingList(self, stopEvent, recheck=False):
        """打開背包並停在「釣魚用品」清單。第一次選好分類後，順便開啟「下次開啟背包保持該結果」，
        之後再打開背包就已經在這個清單，不用每次重新搜尋、選分類。
        標題辨識可能把相似的篩選(例如「園藝用品」)誤認成釣魚用品，recheck 為 True 時不信任標題，一律重新選分類。
        回傳 False 代表已收到停止"""
        img, view = self._openBagView(stopEvent, "full" if recheck else "fishing")
        if view is None:
            return False
        if view == "fishing":
            return True
        if not self._click(self._find(img, "searchBtn"), stopEvent):
            return False
        if not self._waitFor("searchSubmitBtn", stopEvent)[1]:
            raise RuntimeError("找不到搜尋視窗")
        if not self._clickWhenVisible("filterFishing", stopEvent):
            raise RuntimeError("找不到「釣魚用品」分類")
        if stopEvent.wait(0.8):
            return False
        keepPos = self._find(self._shot(), "keepOff")
        if keepPos and not self._click(keepPos, stopEvent):
            return False
        return not stopEvent.wait(0.3)

    def _useFishingItem(self, itemName, buttonName, label, stopEvent):
        if not self._openFishingList(stopEvent):
            self._trace(f"{label}: 背包沒有停在釣魚用品清單")
            return False
        self._trace(f"{label}: 已打開釣魚用品清單")
        img, itemPos = self._waitFor(itemName, stopEvent, 2)
        if not itemPos:
            # 可能是背包停在別的篩選卻被誤認成釣魚用品: 關掉重開，重新選分類再找一次
            if not self._closeBag(stopEvent) or not self._openFishingList(stopEvent, recheck=True):
                return False
            img, itemPos = self._waitFor(itemName, stopEvent, 2)
        if not itemPos:
            self._closeBag(stopEvent)
            raise OutOfStock(label)
        if not self._click(itemPos, stopEvent):
            return False
        if not self._clickWhenVisible(buttonName, stopEvent, 2):
            raise RuntimeError(f"找不到{label}的使用按鈕")
        self._trace(f"{label}: 已按使用")
        if stopEvent.wait(1.5):
            return False
        closed = self._closeBag(stopEvent)
        self._trace(f"{label}: 關閉背包 {closed}")
        return closed

    def _doPerfume(self, stopEvent):
        return self._useFishingItem("perfumeItem", "sprayBtn", "香水", stopEvent)

    def _doBait(self, stopEvent):
        return self._useFishingItem("baitItem", "useBtn", "誘魚器", stopEvent)
