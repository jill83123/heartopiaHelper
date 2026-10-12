import math
import random
import time
import traceback
import cv2
import numpy as np
from scripts.food import FoodMixin
from scripts.gameui import COMMON_TEMPLATE_DIR, GAME_BASE_WIDTH, GameUI
from scripts.tasks import MAX_TASK_FAILURES, OutOfStock, TimedTasks
from tools.finder import STAR_HSV_HIGH, STAR_HSV_LOW, clickBackButton
from tools.tools import getThreshold, loadTemplate, matchBest

CAST_SETTLE_SECONDS = 3  # 按下拋竿後，最多等這麼久(秒)看介面有沒有隱藏(進入拋竿)，隱藏了就立刻往下
UNKNOWN_AFTER_TASK_SECONDS = 4  # 剛做完定時項目(丟維修盒、噴香水...)就認不出畫面，多半是誤點到別的介面，不用等太久就處理
UNKNOWN_SECONDS = 20  # 畫面不是拋竿、也不是主畫面超過這個秒數，就點返回
MAX_FAILED_CASTS = 3
FACE_FRONT_AFTER_FAILS = 2  # 連續失敗幾次才轉回正面(ADB 截圖慢，介面短暫隱藏容易漏看，單次失敗多半是誤判)

# 拋竿鈕在遊戲畫面右下角的固定位置(以 1600 寬為基準): 「F」小圓點離右邊、下邊的距離，以及它到按鈕中心的距離
CAST_BADGE_FROM_RIGHT = 225
CAST_BADGE_FROM_BOTTOM = 153
CAST_BADGE_TO_CENTER = 60
CAST_SEARCH_RADIUS = 40
# 模擬器(手機版)的拋竿鈕是右下角的圓鈕，中心離遊戲畫面右、下邊緣的距離
ADB_CAST_RADIUS = 70
# 釣竿有各種造型，鈕裡面的圖案每個人都不一樣，所以內建的圖不一定比對得到。
# 但主畫面(看得到背包)時拋竿鈕一定在這個固定位置，第一次看到就把它的樣子記下來，之後用自己的樣子比對
CAST_LEARN_RADIUS = 60


# 避免完全定時、每次操作一模一樣: 間隔只會往後延(香水、誘魚器有持續時間，提早反而浪費)
# 香水、誘魚器有持續時間，延後太久會有空窗期，所以延後上限 = 設定的間隔 − 持續時間(例如香水 125 秒 − 120 秒 = 最多 5 秒)
ITEM_DURATION = {"perfume": 120, "bait": 60}
ITEM_INTERVAL = {"perfume": 123, "bait": 63}  # 道具是固定時效，間隔不開放設定(誘魚器只在沒勾「水面有了就不放」時照這個間隔放)

# 新圖鑑的星星卡: 比對金色色塊和標準五角星的形狀
STAR_SHAPE_MAX = 0.08  # 金色色塊的輪廓和五角星的差異(Hu 矩距離，越小越像，不受大小影響)要在這個值以內才算星星；實測真星星 0.004~0.02，背包圖示的黃色邊角、燈光 0.17 以上
STAR_INNER_RATIO = 0.55  # 遊戲星星圖示的內凹程度(內圈半徑 ÷ 外圈半徑)，胖胖的五角星
# 釣到新紀錄的魚時，畫面會停在對話框(「太棒了！釣到了新紀錄的…」)，要點一下才會繼續，期間沒有任何選單。
# 對話框是畫面下方中間一塊白色圓角框(文字內容每次不同)，所以看框左右兩端(沒有文字的地方)是不是整片白的來判斷
DIALOG_SIDES = ((0.24, 0.32), (0.68, 0.76))  # 對話框左右兩端的範圍(佔遊戲畫面寬的比例)
DIALOG_TOP, DIALOG_BOTTOM = 0.83, 0.93  # 對話框上下範圍(佔遊戲畫面高的比例)
DIALOG_WHITE = 235  # 三個色頻都要這麼亮才算白(實測對話框 246 以上，沙灘、浪花都在 200 以下或不成片)
DIALOG_FILL = 0.97  # 兩端都要有這個比例以上是白的
# 沒有香水時魚要自己拉: 咬鉤時浮標上方會跳出藍色「!」(約 0.7 秒)，要立刻按住 F 收線；
# 魚線顏色代表張力(白 → 黃 → 橘 → 紅)，橘色或紅色時要放開，回到黃或白再按住，否則線會斷
BITE_MARK = "biteMark"
BITE_DETECT = False  # 先不偵測浮標上方的藍色「!」(咬鉤)，要恢復改成 True
LINE_HOT_PIXELS = 900  # 橘色 + 紅色的像素數達到這個值就視為張力太大(以 1600 寬為基準，依畫面縮放換算)
REEL_POLL_SECONDS = 0.05
REEL_STATE_EVERY = 10  # 收線時每幾次偵測才確認一次是不是已經回到主畫面(確認比較花時間)
REEL_END_SECONDS = 0.8  # 離開拋竿畫面超過這麼久才當作收線結束
REEL_MAX_SECONDS = 120  # 收線最久多少秒，避免卡死
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
JOYSTICK_CENTER = (0.13, 0.77)  # 模擬器左下角移動搖桿的中心(佔畫面寬高的比例)
JOYSTICK_PUSH = 0.08  # 往上推的距離(佔畫面高)
SATIETY_SAMPLE_SECONDS = 2.5  # 收竿後最多花多久讀體力條
NEW_FISH_CLICK_SECONDS = 25  # 偵測到新魚後這麼久(秒)內，介面被蓋住就點螢幕點掉對話框
NEW_FISH_SETTLE_SECONDS = 7  # 偵測到新魚後，至少等這麼久(動畫還在播)才讓角色轉回正面
RETRACT_WAIT_SECONDS = 4  # 收竿後最多等這麼久(秒)才回到主畫面，還沒回來才會再按一次
FACE_FRONT_HOLD = 0.6  # 按住 W 讓角色轉回正面的秒數(太短遊戲不一定吃)


def _starContour(size=200):
    """標準五角星的輪廓，拿來和畫面上的金色色塊比形狀"""
    points = []
    for k in range(10):
        radius = (size / 2 - 4) * (1 if k % 2 == 0 else STAR_INNER_RATIO)
        angle = -math.pi / 2 + k * math.pi / 5
        points.append([size / 2 + radius * math.cos(angle), size / 2 + radius * math.sin(angle)])
    canvas = np.zeros((size, size), np.uint8)
    cv2.fillPoly(canvas, [np.array(points, np.int32)], 255)
    return max(cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], key=cv2.contourArea)


_STAR_CONTOUR = _starContour()


class Fishing(FoodMixin, TimedTasks, GameUI):
    """自動釣魚: 拋竿中背包與工具包都打不開，所以定時項目到期後要等竿子收回來(魚釣起)才執行。
    畫面狀態:
      idle    - 看得到背包圖示(沒拋竿)
      bag     - 背包開著
      casting - 只剩右下角拋竿鈕(拋竿中)
    吃食物在 food.py、定時項目的排程在 tasks.py、畫面與背包的共用操作在 gameui.py"""

    templateDir = "templates/fishing"
    # 手機版介面和 PC 版不同的圖(拋竿鈕、香水圖示)放在 templates/fishing/adb；背包、搜尋等共用按鈕在 templates/common，食物相關在 templates/food
    templateDirs = ("templates/fishing", "templates/food", COMMON_TEMPLATE_DIR)

    def __init__(self, setLog, backend, config, roleName="前景定時"):
        super().__init__(setLog, backend, config, roleName)
        self._initTasks()
        self._initFood()
        self._dialogClicks = 0
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
        self._newFishAt = 0.0  # 偵測到新魚的時間
        self._newFish = False  # 剛釣到新圖鑑的魚，角色轉向鏡頭了，下次拋竿前要轉回正面
        self._learnedCast = None  # 這個帳號的拋竿鈕圖片(BGR)

    def _threadTargets(self):
        return [self._mainLoop]

    # ===== 設定 =====
    def _enabledTasks(self):
        """依執行順序列出啟用的項目: (名稱, 執行函式)"""
        tasks = [("repair", self._doRepair)]  # 沒勾「丟維修盒」也在清單裡，只是只有耐久耗盡時才會丟(見 _isDue)
        if self._flag("fishingUseFood"):
            tasks.append(("food", self._doFood))
        if self._flag("fishingAutoCast"):  # 自動釣魚一定要搭配香水(魚直接上鉤)
            tasks.append(("perfume", self._doPerfume))
        if self._flag("fishingUseBait"):
            tasks.append(("bait", self._doBait))
        return [t for t in tasks if t[0] not in self.disabled]

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


    # ===== 畫面辨識與操作 =====

    def _findCast(self, img):
        """回傳右下角拋竿鈕的中心座標，找不到回傳 None。
        只在拋竿鈕固定的位置(貼著遊戲畫面右下角)上比對，依序嘗試:
        1. 主畫面時記下來的自己的拋竿鈕(釣竿造型不同也能認，用 castBtn 閥值)
        2. 手機版: 內建的釣竿圖案 castBtn
        3. 電腦版: 內建的「F」小圓點 castBadge(按鈕是半透明的、背景會變，所以不直接比對圖案)，再往上推算按鈕中心"""
        x, y, w, h = self._gameRect(img)
        s = w / GAME_BASE_WIDTH
        (cx, cy), _ = self._castCenter(img)
        r = ADB_CAST_RADIUS * s
        # 先用記下來的自己的拋竿鈕比對
        if self._learnedCast is not None:
            region = img[max(0, int(cy - r)) : int(cy + r), max(0, int(cx - r)) : int(cx + r)]
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            learned = cv2.cvtColor(self._learnedCast, cv2.COLOR_BGR2GRAY)
            if gray.shape[0] >= learned.shape[0] and gray.shape[1] >= learned.shape[1]:
                if cv2.matchTemplate(gray, learned, cv2.TM_CCOEFF_NORMED).max() >= getThreshold(self.config, "castBtn"):
                    return (cx, cy)
        if self.isAdb:
            # 手機版沒有 F 小圓點，直接在圓鈕的固定位置上比對內建的釣竿圖案
            x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
            region = img[y0 : int(cy + r), x0 : int(cx + r)]
            if not matchBest(region, self._templatePath("castBtn"), getThreshold(self.config, "castBtn"), s):
                return None
            return (cx, cy)
        cx, cy = x + w - CAST_BADGE_FROM_RIGHT * s, y + h - CAST_BADGE_FROM_BOTTOM * s
        r = CAST_SEARCH_RADIUS * s
        x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
        region = img[y0 : int(cy + r), x0 : int(cx + r)]
        pos = matchBest(region, f"{self.templateDir}/castBadge.png", getThreshold(self.config, "castBadge"), s)
        if not pos:
            return None
        return (x0 + pos[0], y0 + pos[1] - CAST_BADGE_TO_CENTER * s)

    def _castCenter(self, img):
        """拋竿鈕中心(影像座標)與縮放比例，就是右下角的動作鈕"""
        return self._actionCenter(img)

    def _learnCast(self, img):
        """主畫面時把拋竿鈕目前的樣子記下來(只用在這一次運行)"""
        (cx, cy), s = self._castCenter(img)
        r = int(CAST_LEARN_RADIUS * s)
        x0, y0 = int(cx - r), int(cy - r)
        crop = img[y0 : y0 + 2 * r, x0 : x0 + 2 * r]
        if x0 < 0 or y0 < 0 or crop.shape[:2] != (2 * r, 2 * r):
            return
        if self._learnedCast is None:
            self.setLog("已記下拋竿鈕的樣子")
        self._learnedCast = crop.copy()

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

    def _findBite(self, img):
        """浮標上方有藍色「!」代表魚咬鉤了。回傳座標或 None"""
        if not BITE_DETECT:
            return None
        return self._find(img, BITE_MARK)

    def _lineHot(self, img):
        """魚線是橘色或紅色(張力太大)嗎。拋竿鈕自己是金黃色，位置固定，先遮掉"""
        x, y, w, h = self._gameRect(img)
        s = w / GAME_BASE_WIDTH
        region = img[max(0, y) : y + h, max(0, x) : x + w]
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        orange = (hue >= 9) & (hue <= 19) & (sat >= 150) & (val >= 220)
        red = ((hue <= 6) | (hue >= 172)) & (sat >= 170) & (val >= 110)
        hot = orange | red
        (cx, cy), _ = self._castCenter(img)
        r = int(ADB_CAST_RADIUS * s * 1.3)
        hot[max(0, int(cy - y - r)) : int(cy - y + r), max(0, int(cx - x - r)) : int(cx - x + r)] = False
        return int(hot.sum()) >= LINE_HOT_PIXELS * s * s

    def _reel(self, stopEvent):
        """魚咬鉤後收線: 按住 F，魚線變橘或紅就放開，回到黃或白再按住，直到回到主畫面。
        回傳 False 代表已收到停止"""
        castPos = self._castCenter(self._shot())[0]
        holding = False
        outSince = None
        start = time.time()
        polls = 0
        try:
            while time.time() - start < REEL_MAX_SECONDS:
                if stopEvent.wait(REEL_POLL_SECONDS):
                    return False
                img = self._shot()
                polls += 1
                if polls % REEL_STATE_EVERY == 0:
                    if self._hasNewFishCard(img):
                        self._markNewFish("收竿時的提示卡", img)
                    if self._state(img) == "casting":
                        outSince = None
                    elif outSince is None:
                        outSince = time.time()
                    elif time.time() - outSince >= REEL_END_SECONDS:
                        break
                hot = self._lineHot(img)
                if hot and holding:
                    self.backend.holdUp()
                    holding = False
                elif not hot and not holding:
                    x, y = castPos[0] + self._origin[0], castPos[1] + self._origin[1]
                    self.backend.holdDown(round(x), round(y))
                    holding = True
        finally:
            if holding:
                self.backend.holdUp()
        return True

    def _dismissSpot(self, img):
        """點掉新魚對話框時點的位置(影像座標): 遊戲畫面下方中間，那裡沒有按鈕"""
        x, y, w, h = self._gameRect(img)
        return (x + w * 0.5, y + h * 0.88)

    def _dismissDialog(self, pos, stopEvent):
        """點掉對話框。左鍵與右鍵輪流點(不確定哪一個遊戲吃，手機版只有點擊)"""
        self._dialogClicks += 1
        x, y = round(pos[0] + self._origin[0]), round(pos[1] + self._origin[1])
        self.backend.moveTo(x, y)
        if stopEvent.wait(0.2):
            return
        if self._dialogClicks % 2 == 1 or self.isAdb:
            self.backend.click(x, y)
        else:
            self.backend.rightClick(x, y)

    def _state(self, img):
        if self._find(img, "closeBtn"):
            return "bag"
        if self._findBag(img):
            return "idle"
        if self._findCast(img):
            return "casting"
        return "unknown"

    # ===== 項目 =====
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


    # ===== 主迴圈 =====

    def _mainLoop(self, stopEvent):
        """真正的迴圈在 _fishingLoop。包一層只為了在除錯模式把「怎麼結束的」記下來(正常 return，或什麼例外)"""
        try:
            self._fishingLoop(stopEvent)
            self._trace(f"主迴圈結束（return，stopEvent 已設定: {stopEvent.is_set()}）")
        except BaseException:
            self._trace("主迴圈因例外結束:\n" + traceback.format_exc())
            raise

    def _fishingLoop(self, stopEvent):
        frequency = float(self.config.get("fishingDetectFrequency", 0.2))
        unknownSince = None
        failedCasts = 0
        retractLogged = False

        # 丟維修盒預設不在開始時馬上做，從現在起算一個間隔後才第一次執行；吃食物則開始就先檢查一次(夠飽就不吃)
        self._hungry = False  # 開始時不先吃，等看到「體力不足」提示才吃
        self._baitChecked = False
        self._baitCheckLogged = False
        self._toolBroken = False
        self._toolFixedAt = None
        self._logOcrStatus()
        if not self._flag("fishingRepairNow"):
            self._markDone("repair")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        if not self._flag("fishingAutoCast"):
            self.setLog("自動拋竿已關閉，只會定時執行勾選的項目")

        self._trace("========== 開始運行 ==========", reset=True)
        self._startWatchdog(stopEvent)
        lastState = None
        while not stopEvent.wait(frequency):
            img = self._shot()
            state = self._state(img)
            if state != lastState:
                pressed = time.time() - getattr(self, "_castPressedAt", 0) < 6
                self._trace(f"狀態 {lastState} -> {state}（{'剛按過拋竿鈕' if pressed else '沒按拋竿鈕'}）")
                if state == "casting" and not pressed:  # 沒按卻變成拋竿中: 存畫面看是不是主畫面誤判成拋竿中
                    self._saveShot(img, "state")
                lastState = state

            # 圖示消失要「在主畫面連續」看不到才算，離開主畫面(拋竿、開背包...)就重新計
            self._logPerfumeLeft(img if state == "idle" else None)
            if state != "idle":
                self._missingSince = None
            if state == "casting":
                if failedCasts and time.time() - getattr(self, "_castPressedAt", 0) < 6:  # 介面比取樣視窗晚一點才隱藏，其實拋出去了
                    failedCasts = 0
                if self._castingSince is None:
                    self._castingSince = time.time()
            else:
                self._castingSince = None

            if state != "bag" and self._hasNewFishCard(img):
                self._markNewFish("提示卡", img)

            if state != "unknown":
                unknownSince = None
            if state != "casting":
                retractLogged = False

            if state == "idle":
                if self._flag("fishingAutoCast") and not self._findCast(img):  # 主畫面卻比對不到拋竿鈕: 記下這個帳號的拋竿鈕樣子(自動拋竿關閉時用不到，不記)
                    self._learnCast(img)
                if not self._baitCheckLogged and self._baitUnchecked():  # 確認魚尾要連續看好幾次，要一點時間，先說一聲
                    self._baitCheckLogged = True
                    self.setLog("檢查水面有沒有魚尾中...")
                due = self._dueTasks(img)
                if due:
                    for name, fn in due:
                        if stopEvent.is_set():
                            return
                        self._runTask(name, fn, stopEvent)
                        self._closeBag(stopEvent)
                        self._leaveUnknown(stopEvent)
                    unknownSince = time.time() - (UNKNOWN_SECONDS - UNKNOWN_AFTER_TASK_SECONDS)
                    continue
                if not self._flag("fishingAutoCast"):
                    continue
                if self._baitUnchecked():
                    self._traceChange("還沒確認魚尾", "主畫面不拋竿")
                    continue  # 還沒確認水面有沒有魚尾(有的話不放誘魚器，沒有就要放)，確認前不拋竿
                if self._missingSince is not None and "perfume" not in self.disabled:
                    self._traceChange("看不到香水圖示，等反應時間", "主畫面不拋竿")
                    continue  # 看不到香水圖示，正在等一兩秒的反應時間再補噴；這時不能拋竿，否則一拋竿計時就重來，永遠噴不到
                if self.lastDone.get("perfume") is None and "perfume" not in self.disabled:
                    self._traceChange("還在確認有沒有香水效果", "主畫面不拋竿")
                    continue  # 剛開始，還在確認有沒有香水效果(連續幾次看不到圖示才噴)，先不拋竿
                if "perfume" in self.disabled:  # 沒有香水效果又噴不了，不繼續釣魚
                    self._traceChange("香水已停用", "主畫面不拋竿")
                    if not self._noPerfumeLogged:
                        self._alert("沒有香水效果，也無法噴香水，暫停釣魚(請補充香水後重新開始)")
                        self._noPerfumeLogged = True
                    continue
                if failedCasts >= MAX_FAILED_CASTS:
                    self._alert("連續拋竿失敗，請確認角色面向水面、手上拿著釣竿，且沒有開著選單")
                    failedCasts = 0
                    if stopEvent.wait(10):
                        return
                    continue
                if self._find(img, "repairBoxItem"):  # 工具包開著會擋住拋竿，先關掉
                    self._trace("拋竿前發現工具包開著，先關閉")
                    self.setLog("工具包開著，先關閉")
                    self._closeToolPanel(stopEvent)
                    continue
                if not self._flag("fishingFaceFront"):  # 沒勾轉回正面: 只記新魚，不操作
                    self._newFish = False
                if (self._newFish or failedCasts >= FACE_FRONT_AFTER_FAILS) and self._flag("fishingFaceFront"):  # 釣到新魚後角色會轉向鏡頭拋不出去，往上走一下(電腦版按住 W，模擬器把左下角的搖桿往上推)轉回正面
                    self.setLog("讓角色轉回正面" if self._newFish else "拋竿失敗，讓角色轉回正面")
                    self._newFish = False
                    self._waitNewFishGone(stopEvent)
                    self._faceFront()
                    if stopEvent.wait(0.8):
                        return
                self._wakeMouse(stopEvent)
                self._traceChange(None)
                self._trace(f"主畫面拋竿 perfume={self.lastDone.get('perfume')} baitChecked={self._baitChecked}")
                self._cast(self._shot(), stopEvent, force=True)
                # 按下後每 0.3 秒看一次: 介面隱藏(進入拋竿)就成功，不用傻等；介面一直沒隱藏就是沒拋出去，
                # 這段時間若跳出「工具耐久耗盡」的文字，就是工具壞了
                deadline = time.time() + CAST_SETTLE_SECONDS
                toast, shot, hidden, hungry = None, None, False, False
                while time.time() < deadline:
                    if stopEvent.wait(0.3):
                        return
                    shot = self._shot()
                    st = self._state(shot)
                    if st != "idle":
                        hidden = True
                        self._trace(f"按下拋竿鈕 {time.time() - self._castPressedAt:.1f} 秒後介面隱藏（{st}）")
                        break
                    if toast is None and self._findToolBroken(shot):
                        toast = shot
                    if not hungry and self._flag("fishingUseFood") and self._hungerToast(shot):
                        hungry = True  # 體力不足拋不出去，提示只在按下的當下出現一下子
                if hungry and not hidden:
                    self._noteHunger(shot, force=True)
                    failedCasts = 0
                elif hidden:
                    failedCasts = 0
                elif toast is not None:
                    self._saveShot(toast, "toast")
                    self.setLog("拋竿沒反應，提示工具耐久耗盡，先丟維修盒")
                    self._toolBroken = True
                    failedCasts = 0
                else:
                    failedCasts += 1
                    self._trace(f"按下拋竿鈕後 {CAST_SETTLE_SECONDS} 秒介面都沒隱藏，連續失敗 {failedCasts} 次")

            elif state == "casting":
                # 咬鉤了就把這一次釣完(就算香水剛好到期，收竿魚就跑了)
                if self._findBite(img):
                    if not self._reel(stopEvent):
                        return
                    continue
                # 香水到期且圖示消失時，魚不會再自動上鉤，要把竿子收起來才能再噴。
                # 一直按拋竿鈕讓釣魚中斷，直到回到主畫面
                dueNames = [name for name, _ in self._dueTasks(img, casting=True)] if self._flag("fishingAutoCast") else []
                if any(name in ("perfume", "bait", "repair") for name in dueNames):
                    self._trace(f"拋竿中要收竿: {dueNames}")
                    retractLogged = True
                    if not self._retract(img, stopEvent):
                        return

            elif state == "bag":
                self._closeBag(stopEvent)

            else:
                if time.time() - self._newFishAt >= NEW_FISH_CLICK_SECONDS and self._hasDialog(img):
                    self._markNewFish("對話框", img)  # 提示卡沒抓到，只剩下對話框(畫面被蓋住才檢查，不會把主畫面誤判)
                if time.time() - self._newFishAt < NEW_FISH_CLICK_SECONDS:
                    # 釣到新魚會跳出對話框，點螢幕任何一處就能點掉。不去辨識對話框(浪花、沙灘會被誤認)，
                    # 看到「新圖鑑」字樣或星星卡之後，畫面進入介面被蓋住的狀態就直接點
                    unknownSince = None
                    self._dismissDialog(self._dismissSpot(img), stopEvent)
                    if stopEvent.wait(0.8):
                        return
                    continue
                unknownSince = unknownSince or time.time()
                if time.time() - unknownSince > UNKNOWN_SECONDS:
                    blocker = self.backend.gameBlocker()
                    if blocker:
                        self.setLog(f"⚠️ {blocker}")
                    elif clickBackButton(self.backend, self.config):
                        self.setLog("畫面無法辨識，已點擊返回按鈕")
                    elif self._pressBack():
                        self.setLog("畫面無法辨識，找不到返回按鈕，已按 Esc")
                    else:
                        self.setLog("畫面無法辨識，也找不到返回按鈕")
                    unknownSince = None

    def _leaveUnknown(self, stopEvent):
        """定時項目做完(成功失敗都一樣)，畫面若不是主畫面也不是拋竿中(多半是誤點到別的介面)，馬上點返回鈕，沒有就送返回鍵，最多試 2 次"""
        for _ in range(2):
            if stopEvent.wait(0.8) or self._state(self._shot()) != "unknown":
                return
            if clickBackButton(self.backend, self.config):
                self.setLog("操作後停在其他介面，已點擊返回按鈕")
            elif self._pressBack():
                self.setLog("操作後停在其他介面，已按 Esc")

    def _pressBack(self):
        """找不到返回按鈕時的最後手段: 電腦版按 Esc(只在畫面已經認不出來時才用，主畫面按 Esc 會開選單)。
        手機版不送返回鍵(最上層畫面按返回可能跳出離開遊戲的確認或直接退到背景)。回傳是否有送出"""
        if self.isAdb:
            return False
        try:
            self.backend.pressKey("esc")
            return True
        except Exception:
            return False

    def _faceFront(self):
        if not self.isAdb:
            self.backend.pressKey("w", FACE_FRONT_HOLD)
            return
        h, w = self._shot().shape[:2]
        x, y = w * JOYSTICK_CENTER[0], h * JOYSTICK_CENTER[1]
        self.backend.swipe(x, y, x, y - h * JOYSTICK_PUSH, int(FACE_FRONT_HOLD * 1000))

    def _markNewFish(self, source="", img=None):
        """釣到新魚(新圖鑑的提示卡，或「太棒了！釣到了新紀錄的…」對話框): 記下來，不通知也不寫日誌(只寫 flow.log)。
        角色會轉向鏡頭，下次拋竿前要轉回正面"""
        if self._newFish:
            return
        self._newFish = True
        self._newFishAt = time.time()
        self._trace(f"釣到新魚（{source}）")
        if img is not None:  # 誤判時看這張就知道是哪一種判斷把什麼當成了新魚
            self._saveShot(img, {"提示卡": "newfish_card", "收竿時的提示卡": "newfish_reel", "對話框": "newfish_dialog"}.get(source, "newfish"))

    def _hasNewFishCard(self, img):
        """釣到新魚的提示: 右上角有「新圖鑑！」字樣，或有一張星星卡(新的星數才會出現，釣過的不會有)，任一個就算"""
        x, y, w, h = self._gameRect(img)
        region = img[max(0, y) : y + int(h * 0.3), max(0, x + int(w * 0.7)) : x + w]  # 畫面中上方的「鬥竿專家」之類橫幅也是白字藍底，所以不看中間
        if region.size > 0 and self._find(region, "newFishMark") is not None:
            return True
        return self._hasStarCard(img)

    def _hasDialog(self, img):
        """畫面下方中間有白色圓角對話框(「太棒了！第一次釣到…」「釣到了新紀錄的…」)。
        文字在框中間，所以只看左右兩端兩塊，兩塊都是整片白色才算，沙灘、浪花不會"""
        x, y, w, h = self._gameRect(img)
        for left, right in DIALOG_SIDES:
            roi = img[max(0, y + int(h * DIALOG_TOP)) : y + int(h * DIALOG_BOTTOM), max(0, x + int(w * left)) : x + int(w * right), :3]
            if roi.size == 0 or (roi.min(axis=2) >= DIALOG_WHITE).mean() < DIALOG_FILL:
                return False
        return True

    def _hasStarCard(self, img):
        """右側(離最上排的圖示列一段距離)有金黃色實心星星 = 新星數的星星卡。
        不靠模板，找大小相近、形狀像五角星的金色小塊；範圍避開最上排的活動圖示，也避開畫面中央角色旁邊的閃光"""
        x, y, w, h = self._gameRect(img)
        roi = img[max(0, y + int(h * 0.15)) : y + int(h * 0.4), max(0, x + int(w * 0.7)) : x + w, :3]
        if roi.size == 0:
            return False
        mask = cv2.inRange(cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV), STAR_HSV_LOW, STAR_HSV_HIGH)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, count):
            sx, sy, sw, sh, area = stats[i]
            if h * 0.012 <= sw <= h * 0.05 and h * 0.012 <= sh <= h * 0.05 and 0.7 <= sw / sh <= 1.4 and area >= sw * sh * 0.4:
                # 大小、顏色對了還要形狀像五角星: 背包圖示的黃色邊角、燈光、閃光也是金黃色的小塊，單看大小會被騙
                blob = np.pad((labels[sy : sy + sh, sx : sx + sw] == i).astype(np.uint8) * 255, 1)
                contour = max(cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], key=cv2.contourArea)
                if cv2.matchShapes(_STAR_CONTOUR, contour, cv2.CONTOURS_MATCH_I1, 0) <= STAR_SHAPE_MAX:
                    return True
        return False

    def _waitNewFishGone(self, stopEvent):
        """等新魚的整段動畫結束(對話框、提示卡、「魚名+1」的彈出)，角色才吃得到轉向的操作。
        動畫期間按了沒用，從偵測到新魚起至少等 NEW_FISH_SETTLE_SECONDS 秒，期間提示卡還在就繼續等"""
        while time.time() - self._newFishAt < NEW_FISH_SETTLE_SECONDS or self._hasNewFishCard(self._shot()):
            if stopEvent.wait(0.3) or time.time() - self._newFishAt > 20:
                return
        stopEvent.wait(0.5)

    def _retract(self, img, stopEvent):
        """收竿: 按一下拋竿鈕，然後等到回到主畫面(最多等 RETRACT_WAIT_SECONDS 秒)。
        收竿動畫要一點時間，不能隔一下就再按，不然會收了又拋出去。回傳 False 代表已收到停止"""
        self._cast(img, stopEvent)
        deadline = time.time() + RETRACT_WAIT_SECONDS
        while time.time() < deadline:
            if stopEvent.wait(0.4):
                return False
            if self._state(self._shot()) == "idle":
                break
        return True

    def _cast(self, img, stopEvent, force=False):
        """點右下角的拋竿鈕(拋竿與收竿是同一顆)。force: 已確定在主畫面，就算比對不到圖也點固定位置"""
        pos = self._findCast(img)
        if not pos and force:
            pos = self._castCenter(img)[0]
        self._castPressedAt = time.time()
        if pos:  # 找不到多半是動畫期間，連續失敗時主迴圈會提醒
            self._click(pos, stopEvent)
