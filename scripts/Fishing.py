import itertools
import os
import sys
import random
import time
import cv2
import numpy as np
from scripts.base import BaseTask
from tools.notify import notify
from tools.ocr import isReady, readFraction
from tools.finder import STAR_HSV_HIGH, STAR_HSV_LOW, clickBackButton, getBaseScale, matchBestAuto, resolveScale
from tools.tools import getResourcePath, getThreshold, loadTemplate, matchBest

UI_WAIT_SECONDS = 4  # 等待某個畫面/按鈕出現的上限(秒)
CAST_SETTLE_SECONDS = 3  # 按下拋竿後，最多等這麼久(秒)看介面有沒有隱藏(進入拋竿)，隱藏了就立刻往下
UNKNOWN_SECONDS = 20  # 畫面不是拋竿、也不是主畫面超過這個秒數，就點返回
MAX_FAILED_CASTS = 3

# 拋竿鈕在遊戲畫面右下角的固定位置(以 1600 寬為基準): 「F」小圓點離右邊、下邊的距離，以及它到按鈕中心的距離
CAST_BASE_WIDTH = 1600
CAST_BADGE_FROM_RIGHT = 225
CAST_BADGE_FROM_BOTTOM = 153
CAST_BADGE_TO_CENTER = 60
CAST_SEARCH_RADIUS = 40
# 模擬器(手機版)的拋竿鈕是右下角的圓鈕，中心離遊戲畫面右、下邊緣的距離
ADB_CAST_FROM_RIGHT = 224
ADB_CAST_FROM_BOTTOM = 212
ADB_CAST_RADIUS = 70
# 釣竿有各種造型，鈕裡面的圖案每個人都不一樣，所以內建的圖不一定比對得到。
# 但主畫面(看得到背包)時拋竿鈕一定在這個固定位置，第一次看到就把它的樣子記下來，之後用自己的樣子比對
CAST_LEARN_RADIUS = 60
ADB_PRESS_MS = 100  # 模擬器點擊要按住的時間

# 喚醒滑鼠時點的位置(佔遊戲畫面寬高的比例)：上方中間
WAKE_X = 0.5
WAKE_Y = 0.12
WAKE_JITTER = 0.08  # 位置再隨機偏移的比例

# 避免完全定時、每次操作一模一樣: 間隔只會往後延(香水、誘魚器有持續時間，提早反而浪費)，
# 點擊的位置與停頓也加一點隨機
INTERVAL_JITTER = 0.1  # 每次間隔額外延後 0~10%
# 香水、誘魚器有持續時間，延後太久會有空窗期，所以延後上限 = 設定的間隔 − 持續時間(例如香水 125 秒 − 120 秒 = 最多 5 秒)
ITEM_DURATION = {"perfume": 120, "bait": 60}
ITEM_INTERVAL = {"perfume": 123, "bait": 63}  # 道具是固定時效，間隔不開放設定(誘魚器只在沒勾「水面有了就不放」時照這個間隔放)
CLICK_OFFSET = 3  # 點擊位置隨機偏移(像素)
HOVER_DELAY_RANGE = (0.2, 0.45)  # 移到按鈕上到點下去之間的停頓(秒)

# 背包搜尋結果的格子位置(相對於背包「搜尋」圖示，以 1600 寬為基準)，每列 5 格
RESULT_FIRST_OFFSET = (-489, 113)
RESULT_STEP = 130
RESULT_COLUMNS = 5
RESULT_MAX_CELLS = 15
# 星星在格子內的位置(相對格子中心，1600 寬為基準): 一顆約 14 寬，每多一顆再多約 9
STAR_STRIP = (-54, 37, 8, 50)  # x0, y0, x1, y1
STAR_FIRST_WIDTH = 14
STAR_STEP = 9
MAX_STARS = 5  # 食物最高星級；星級選「不限」又勾了不吃 5 星時，會跳過這一級
# 可使用多次的食物，說明裡有「可用次數：2/4」。只要判斷分子是不是 1(「1」這個字很窄，其他數字寬約 8 像素，它只有約 3)
USES_LABEL_GAP = 3  # 「可用次數：」圖案右邊多留幾像素再開始看數字
USES_REGION_WIDTH = 60
USES_ONE_MAX_ASPECT = 0.42  # 「1」是細細一條，寬／高不會超過這個值(其他數字都在 0.55 以上)
USES_MIN_CONTRAST = 40  # 底色與字最深的地方至少差這麼多亮度，否則當作沒有字
USES_SLASH_MAX_FILL = 0.38  # 斜線比數字空，用字的像素佔外框的比例分辨
# 釣到新紀錄的魚時，畫面會停在對話框(「太棒了！釣到了新紀錄的…」)，要點一下才會繼續，期間沒有任何選單。
# 對話框是畫面下方中間一塊白色圓角框(文字內容每次不同)，所以用「這個範圍幾乎都是白的」來判斷
# 吃食物有兩種時機: 每隔設定的分鐘數吃一次，或操作中剛好看得到體力條(地圖旁邊)且低於設定的 % 數就吃。
# 不會為了看飽食度特地開背包。看到的飽食度高於設定的 % 就不吃，並把定時的下一次往後延一個間隔
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
# 沒有香水時魚要自己拉: 咬鉤時浮標上方會跳出藍色「!」(約 0.7 秒)，要立刻按住 F 收線；
# 魚線顏色代表張力(白 → 黃 → 橘 → 紅)，橘色或紅色時要放開，回到黃或白再按住，否則線會斷
BITE_MARK = "biteMark"
TOAST_BASE_WIDTH = 1250  # toolBroken.png 取自這個寬度的遊戲畫面
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
PERFUME_EXPIRY_MARGIN = 8  # 自己噴的香水，距離持續時間還剩這麼多秒以上，就算看不到圖示也不補噴
PERFUME_READ_BIAS = 6  # 從灰色角度讀出來的剩餘量會偏低(實測 57 秒時讀 44%，實際約 52%)，加上這個修正
PERFUME_LOOSE_SCORE = 0.62  # 判斷「香水還在不在」用的寬鬆門檻(旁邊的鞋子效果圖示只有約 0.57)，寧可誤認為還在，也不要誤噴
PERFUME_EXPIRED_REACTION_RANGE = (1.5, 3.0)  # 自己噴的香水，已超過持續時間又看不到圖示時，圖示要連續消失這麼久(秒)就補噴
PERFUME_EXPIRED_MISS_FRAMES = 5  # 同上，補噴前至少連續這麼多次偵測都看不到圖示
PERFUME_MISS_FRAMES = 10  # 補噴前，至少要連續這麼多次偵測都看不到圖示
PERFUME_RECHECK = 3  # 決定要噴之前，最後再各隔 0.4 秒重新截圖確認的次數
BAIT_PLACED_GRACE = 15  # 放完誘魚器後，水面的魚尾光柱要多久內出現(秒)；這段時間不重複判斷
BAIT_MISS_READS_EXPIRED = 3  # 放下去已經超過誘魚器的時效，要連續這麼多次看不到魚尾就算結束
BAIT_MISS_READS = 8  # 水面連續這麼多次都看不到魚尾，才算誘魚器沒效果了、需要再放
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
STARS_NO5 = 14  # 食物星級選「1-4 星」(不限，但不吃 5 星)時存的值
FOOD_RETRY_SECONDS = 60  # 吃食物(不管成功或失敗)之後，這段時間內不再因為體力不足提示重複觸發，避免提示還沒消失就又去開背包
NEW_FISH_CLICK_SECONDS = 25  # 偵測到新魚後這麼久(秒)內，介面被蓋住就點螢幕點掉對話框
NEW_FISH_SETTLE_SECONDS = 7  # 偵測到新魚後，至少等這麼久(動畫還在播)才讓角色轉回正面
MAX_TASK_FAILURES = 3  # 噴香水、吃食物、放誘魚器連續失敗幾次就不再嘗試
RETRACT_WAIT_SECONDS = 4  # 收竿後最多等這麼久(秒)才回到主畫面，還沒回來才會再按一次
TOOL_BROKEN_RETRY = 40  # 丟完維修盒後，這麼久(秒)內提示還在畫面上也不再重複丟
FACE_FRONT_HOLD = 0.6  # 按住 W 讓角色轉回正面的秒數(太短遊戲不一定吃)
SEARCH_INPUT_OFFSET = -385  # 搜尋輸入框在「搜尋」按鈕左邊多遠


_ocrReported = False  # OCR 狀態是否已經提示過(程式開著期間只提示一次)


class OutOfStock(RuntimeError):
    """背包裡已經沒有這個道具(用完了)"""


class Fishing(BaseTask):
    """自動釣魚: 拋竿中背包與工具包都打不開，所以定時項目到期後要等竿子收回來(魚釣起)才執行。
    畫面狀態:
      idle    - 看得到背包圖示(沒拋竿)
      bag     - 背包開著
      casting - 只剩右下角拋竿鈕(拋竿中)"""

    templateDir = "templates/fishing"
    adbTemplateDir = "templates/fishing/adb"  # 手機版介面和 PC 版不同的圖(背包、工具箱、拋竿鈕、維修盒)

    def __init__(self, setLog, backend, config, roleName="前景定時"):
        super().__init__(setLog, backend, config)
        self.roleName = roleName  # 通知與日誌裡用來分辨是哪個帳號
        self.isAdb = getattr(backend, "isAdb", False)
        if self.isAdb:
            backend.pressMs = ADB_PRESS_MS
        self.lastDone = {}  # 項目名稱 -> 上次執行的時間
        self.jitter = {}  # 項目名稱 -> 這一輪間隔額外延後的比例
        self.disabled = set()  # 執行失敗(找不到道具等)而本次不再嘗試的項目
        self._dialogClicks = 0
        self._sprayNoIcon = 0  # 連續幾次噴了香水卻還是看不到圖示
        self._castingSince = None  # 這一段連續拋竿從什麼時候開始
        self._startMiss = 0  # 開始運行後，連續幾次在主畫面看不到香水圖示
        self._noPerfumeLogged = False
        self._missFrames = 0  # 香水圖示連續幾次偵測都看不到
        self._missingSince = None  # 香水圖示從什麼時候開始看不到
        self._reaction = 0.0
        self._keepLastNotified = False
        self._hungry = False  # 看到「體力不足，吃點東西吧」提示了，要吃食物
        self._leftAnchor = None  # (時間, 當時從圖示讀到的剩餘 %)
        self._lastLeftRead = 0.0  # 上次讀香水剩餘量的時間
        self._loggedLeft = 100  # 香水剩餘量日誌已經寫到幾 %
        self._iconSeen = False  # 噴完之後有沒有看過香水圖示
        self._meals = 0  # 這一輪吃了幾份食物
        self._toolBroken = False  # 看到「工具耐久耗盡」的提示了，要先丟維修盒
        self._toastScore = 0.0
        self._toolFixedAt = None  # 最近一次丟維修盒的時間
        self._toolBrokenAlerted = False
        self._tailRect = None  # 最近一次偵測到的光柱範圍(除錯存圖用)
        self._tailHits = []  # 最近幾次偵測有沒有魚尾光柱
        self._baitChecked = False  # 依魚尾判斷誘魚器時，這次運行有沒有確認過水面的魚尾(確認前不能拋竿)
        self._ownSpray = False  # 目前的香水是不是這次運行自己噴的(才知道它什麼時候會到期)
        self._failCount = {}  # 項目名稱 -> 目前連續失敗幾次
        self._noFoodNotified = False  # 沒食物的通知已經跳過了
        self._noMoreFood = False  # 這一輪吃到沒有食物了
        self._ate = False  # 這一輪有沒有真的吃下去
        self._baitSeen = False  # 放完誘魚器後有沒有看過水面的魚尾
        self._baitMiss = 0  # 水面連續幾次看不到魚尾
        self._baitNotSeen = 0  # 連續幾次放完都沒看到魚尾
        self._tailOff = False  # 魚尾偵測看起來沒作用，改回照間隔時間放誘魚器
        self._tailLogged = None  # 上次記到日誌的魚尾狀態(True 有、False 沒有、None 還沒到放誘魚器的時間)
        self._usesNotFoundLogged = False  # 這一輪吃食物已經寫過「看不到可用次數」了
        self._lockedEating = False  # 正在吃鎖定的搜尋結果(背包可能停在別的篩選，找不到時要重新搜尋)
        self._newFishAt = 0.0  # 偵測到新魚的時間
        self._newFish = False  # 剛釣到新圖鑑的魚，角色轉向鏡頭了，下次拋竿前要轉回正面
        self._learnedCast = None  # 這個帳號的拋竿鈕圖片(BGR)

    def _threadTargets(self):
        return [self._mainLoop]

    # ===== 設定 =====
    def _alert(self, msg, flash=True):
        """需要使用者處理的狀況: 寫進日誌，也跳出桌面通知。flash: 要不要讓工作列圖示閃爍(任務已經停了才需要)"""
        self.setLog(f"⚠️ {msg}")
        notify(f"心動小鎮助手｜{self.roleName}", msg, flash=flash)

    def _flag(self, key):
        return str(self.config.get(key, "False")).lower() == "true"

    def _minutes(self, key):
        return float(self.config.get(key)) * 60

    def _enabledTasks(self):
        """依執行順序列出啟用的項目: (名稱, 執行函式)"""
        tasks = []
        if self._flag("fishingUseRepair"):
            tasks.append(("repair", self._doRepair))
        if self._flag("fishingUseFood"):
            tasks.append(("food", self._doFood))
        if self._flag("fishingAutoCast"):  # 自動釣魚一定要搭配香水(魚直接上鉤)
            tasks.append(("perfume", self._doPerfume))
        if self._flag("fishingUseBait"):
            tasks.append(("bait", self._doBait))
        return [t for t in tasks if t[0] not in self.disabled]

    def _interval(self, name):
        """設定的間隔(秒)"""
        if name in ITEM_INTERVAL:
            return ITEM_INTERVAL[name]
        if name == "food":
            return 0  # 吃食物沒有間隔，看到體力不足的提示才吃
        return self._minutes("fishingRepairMinutes")

    def _markDone(self, name):
        """記下執行時間，並抽出下一輪間隔要額外延後幾秒"""
        interval = self._interval(name)
        cap = interval * INTERVAL_JITTER
        if name in ITEM_DURATION:
            cap = min(cap, max(0, interval - ITEM_DURATION[name]))
        self.lastDone[name] = time.time()
        self.jitter[name] = random.uniform(0, cap)
        if name == "bait":
            self._baitSeen = False
            self._baitMiss = 0
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

    def _trace(self, msg, reset=False):
        """流程追蹤: 寫進 debug/flow.log(不進日誌視窗)，出問題時用來看程式當時為什麼那樣做"""
        try:
            folder = os.path.join(os.path.dirname(sys.executable) if hasattr(sys, "frozen") else os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "flow.log")
            if reset and os.path.exists(path) and os.path.getsize(path) > 200_000:  # 平常接著寫(換一次運行不清掉，才看得到上一輪)，太大才重來
                os.remove(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + msg + "\n")
        except Exception:
            pass

    def _saveShot(self, img, prefix):
        try:
            folder = os.path.join(os.path.dirname(sys.executable) if hasattr(sys, "frozen") else os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
            os.makedirs(folder, exist_ok=True)
            cv2.imwrite(os.path.join(folder, time.strftime(prefix + "_%Y%m%d_%H%M%S.png")), img)
            for name in sorted(f for f in os.listdir(folder) if f.startswith(prefix + "_"))[:-10]:
                os.remove(os.path.join(folder, name))
        except Exception:
            pass

    def _saveTailShot(self, img):
        """判斷「水面已有誘魚器」的那一張畫面存下來(框出偵測到的光柱)，之後覺得誤判時可以拿來檢查。最多留最近 20 張"""
        try:
            folder = os.path.join(os.path.dirname(sys.executable) if hasattr(sys, "frozen") else os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
            os.makedirs(folder, exist_ok=True)
            shot = img.copy()
            if self._tailRect:
                x, y, w, h = self._tailRect
                cv2.rectangle(shot, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.imwrite(os.path.join(folder, time.strftime("tail_%Y%m%d_%H%M%S.png")), shot)
            old = sorted(f for f in os.listdir(folder) if f.startswith("tail_"))[:-20]
            for name in old:
                os.remove(os.path.join(folder, name))
        except Exception:
            pass

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
            self._baitNotSeen = 0
            if self._tailLogged != "active":
                self._saveTailShot(img)
            self._baitState("active", "水面已有誘魚器")
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
                return False
            if last is None or time.time() - last >= BAIT_PLACED_GRACE:
                self._baitMiss += 1
            return self._baitMiss >= self._baitMissNeeded()
        if name == "bait" and self._flag("fishingSkipTail") and not self._tailOff:
            return self._baitDue(img)
        last = self.lastDone.get(name)
        wait = self._interval(name) + self.jitter.get(name, 0)
        if name == "perfume":
            if casting:
                # 拋竿中整個 UI 都隱藏，看不到圖示。不看噴香水的時間，只看圖示: 魚上鉤、回到主畫面時就會看到。
                # 但連續拋竿太久都沒魚(香水可能沒了)，或剛開始運行還沒確認過，就收竿回主畫面看圖示再決定噴不噴
                return last is None or (self._castingSince is not None and time.time() - self._castingSince >= CASTING_LOOK_SECONDS)
            if self._hasPerfumeIcon(img, loose=True):
                self._sprayNoIcon = 0
                self._missingSince = None
                if last is not None and not self._iconSeen:
                    self.setLog("香水已生效")
                self._iconSeen = True
                if last is None:  # 開始前已經噴過，從現在開始計時
                    left = self._perfumeLeft(img)
                    self.setLog("已有香水效果，先不噴")
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
            if last is not None and self._ownSpray and self._iconSeen and time.time() - last < ITEM_DURATION["perfume"] - PERFUME_EXPIRY_MARGIN:
                # 這瓶是自己噴的、噴完也看過圖示，香水還沒到持續時間就看不到圖示，多半是圖示被計時的灰色蓋住認不出來，不是真的沒了。
                # 寧可晚幾秒補噴，也不要在香水還有一半時浪費掉一瓶
                self._missingSince = None
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
                if self._sprayNoIcon >= 2:
                    self._alert("噴了香水後仍看不到香水圖示，不再噴香水，也不繼續釣魚，請確認畫面與設定")
                    self.disabled.add(name)
                    return False
                if not self._iconSeen:  # 噴完從來沒看過圖示，多半是沒噴成功
                    self._alert("香水沒有噴成功（噴了之後看不到效果圖示），再試一次，請確認背包有香水", flash=False)
            return True
        if name == "food":
            if casting:
                return False
            self._noteHunger(img)
            return self._hungry
        if name == "repair" and (self._toolBroken or self._toolToastSeen(img)):
            # 畫面出現「工具耐久耗盡，請使用維修盒」: 不等間隔，馬上丟(剛丟完提示可能還沒消失，隔一段時間再說)
            if self._toolFixedAt is None or time.time() - self._toolFixedAt >= TOOL_BROKEN_RETRY:
                if not self._toolBroken:
                    self.setLog("工具耐久耗盡，先丟維修盒")
                self._toolBroken = True
                return True
        return last is None or time.time() - last >= wait

    def _dueTasks(self, img, casting=False):
        return [(name, fn) for name, fn in self._enabledTasks() if self._isDue(name, img, casting)]

    # ===== 畫面辨識與操作 =====
    def _shot(self):
        img, ox, oy = self.backend.captureFull()
        self._origin = (ox, oy)
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])  # 還原成真正的 BGR，顏色判斷才正確
        return img

    def _templatePath(self, name):
        """模擬器模式優先用手機版的圖，沒有的才用共用的"""
        if self.isAdb:
            path = f"{self.adbTemplateDir}/{name}.png"
            if os.path.exists(getResourcePath(path)):
                return path
        return f"{self.templateDir}/{name}.png"

    def _toolToastSeen(self, img):
        """連續兩次都看到「工具耐久耗盡」提示才算(單張誤判不理它)；確認時把畫面存進 debug/ 方便事後檢查"""
        if not self._findToolBroken(img):
            self._toolToastHits = 0
            if self._toastScore >= 0.35 and time.time() - getattr(self, "_toastNearAt", 0) > 5:  # 有點像但不夠像: 存下來(debug/toastnear_*)，用來檢查為什麼沒判到
                self._toastNearAt = time.time()
                self._saveShot(img, "toastnear")
            return False
        self._toolToastHits = getattr(self, "_toolToastHits", 0) + 1
        if self._toolToastHits < 2:
            return False
        self._saveShot(img, "toast")
        return True

    def _hungerToast(self, img):
        """「體力不足，無法拋竿，吃點東西吧」或「體力不足，吃點東西吧」(兩種字句都有)"""
        return self._findToast(img, "noStamina") or self._findToast(img, "noStaminaShort")

    def _findToolBroken(self, img):
        """畫面上有「工具耐久耗盡」提示嗎"""
        return self._findToast(img, "toolBroken")

    def _findToast(self, img, name):
        """畫面上有這個白字提示(例如「工具耐久耗盡」「體力不足」)嗎。提示是半透明底，背景會變，所以只比對白色文字
        (模板 <name>.png 是文字遮罩)，縮放在附近細掃"""
        template = cv2.imread(getResourcePath(self._templatePath(name)), cv2.IMREAD_GRAYSCALE)
        if template is None:
            return None
        # 提示出現在遊戲畫面上方；只看這一塊(快，也少誤判)
        gx, gy, gw, gh = self._gameRect(img)
        top = max(0, gy)
        roi = img[top : top + int(gh * 0.3) + 1, max(0, gx) : gx + gw, :3]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV)
        white = ((hsv[:, :, 1] <= 60) & (hsv[:, :, 2] >= 225)).astype(np.uint8) * 255
        # 提示文字大小跟遊戲畫面寬度成正比(模板是約 1250 寬的畫面上取的)，比對很吃縮放(差 3% 就掉分)，所以在附近細掃；
        # 掃太小的縮放會把小字誤認成提示，所以有下限
        center = gw / TOAST_BASE_WIDTH
        scales = [max(0.7, center * f) for f in np.arange(0.84, 1.18, 0.02)]
        # 縮小一半再比對(快 4 倍)；白字很粗，縮小不影響分辨
        blurred = cv2.GaussianBlur(cv2.resize(white, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), (0, 0), 0.8)
        best, bestLoc, bestScale = 0.0, None, None
        for scale in scales:
            tpl = cv2.resize(template, None, fx=scale * 0.5, fy=scale * 0.5, interpolation=cv2.INTER_AREA)
            if tpl.shape[0] > blurred.shape[0] or tpl.shape[1] > blurred.shape[1] or tpl.shape[0] < 5:
                continue
            res = cv2.matchTemplate(blurred, cv2.GaussianBlur(tpl, (0, 0), 0.8), cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(np.nan_to_num(res))
            if score > best:
                best, bestLoc, bestScale = score, (max(0, gx) + loc[0] * 2 + tpl.shape[1], top + loc[1] * 2 + tpl.shape[0]), scale
        self._toastScore = best
        return bestLoc if best >= getThreshold(self.config, name) else None

    def _find(self, img, name):
        """回傳模板在畫面中的座標(影像座標)，沒找到回傳 None"""
        threshold = getThreshold(self.config, name)
        return matchBestAuto(self.backend, self.config, img, self._templatePath(name), threshold)

    def _gameRect(self, img):
        """遊戲畫面在影像中的範圍 (x, y, w, h)；取不到視窗(或模擬器模式)就當作整張影像"""
        rect = self.backend.getGameRect()
        if rect:
            return (rect[0] - self._origin[0], rect[1] - self._origin[1], rect[2], rect[3])
        return (0, 0, img.shape[1], img.shape[0])

    def _findCast(self, img):
        """回傳右下角拋竿鈕的中心座標，找不到回傳 None。
        只在拋竿鈕固定的位置(貼著遊戲畫面右下角)上比對，依序嘗試:
        1. 主畫面時記下來的自己的拋竿鈕(釣竿造型不同也能認，用 castBtn 閥值)
        2. 手機版: 內建的釣竿圖案 castBtn
        3. 電腦版: 內建的「F」小圓點 castBadge(按鈕是半透明的、背景會變，所以不直接比對圖案)，再往上推算按鈕中心"""
        x, y, w, h = self._gameRect(img)
        s = w / CAST_BASE_WIDTH
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
        """拋竿鈕中心(影像座標)與縮放比例。電腦版與手機版的位置相同: 貼著遊戲畫面右下角固定的距離"""
        x, y, w, h = self._gameRect(img)
        s = w / CAST_BASE_WIDTH
        return (x + w - ADB_CAST_FROM_RIGHT * s, y + h - ADB_CAST_FROM_BOTTOM * s), s

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

    def _perfumeStillThere(self):
        """決定要噴香水之前的最後確認: 隔一小段時間重新截圖幾次，只要有一次看到圖示就不噴(香水很珍貴，誤噴比晚噴更糟)"""
        for _ in range(PERFUME_RECHECK):
            time.sleep(0.4)
            if self._hasPerfumeIcon(self._shot(), loose=True):
                self.setLog("再確認一次，看到香水圖示，不補噴")
                self._missingSince = None
                return True
        return False

    def _hasFishTail(self, img):
        """水面上有魚尾光柱嗎。魚尾本身是淡粉白色、幾乎和光柱同色，認圖案會把隨便一個畫面都當成有，
        所以改找光柱: 一塊近似正方形、很實心的白青色(R 明顯比 G、B 低、很亮、不太飽和)。
        海浪的泡沫、文字、魚的水圈都是扁平或中空的，不會符合"""
        x, y, w, h = self._gameRect(img)
        s = w / CAST_BASE_WIDTH
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
        s = w / CAST_BASE_WIDTH
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

    def _wakeMouse(self, stopEvent):
        """左右鍵各點兩下解除卡滑鼠(轉視角)。點在遊戲畫面上方中間的空白處"""
        x, y, w, h = self._gameRect(self._shot())
        wx = x + self._origin[0] + w * (WAKE_X + random.uniform(-WAKE_JITTER, WAKE_JITTER))
        wy = y + self._origin[1] + h * (WAKE_Y + random.uniform(-WAKE_JITTER, WAKE_JITTER))
        self.backend.wakeMouse(round(wx), round(wy))
        return not stopEvent.wait(0.3)

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

    def _scale(self):
        return getBaseScale(self.backend, self.config) * self.backend.scaleResolver.factor

    def _click(self, pos, stopEvent):
        """點擊影像座標 pos。回傳 False 代表已收到停止"""
        x = round(pos[0] + self._origin[0] + random.uniform(-CLICK_OFFSET, CLICK_OFFSET))
        y = round(pos[1] + self._origin[1] + random.uniform(-CLICK_OFFSET, CLICK_OFFSET))
        self.backend.moveTo(x, y)
        if stopEvent.wait(random.uniform(*HOVER_DELAY_RANGE)):
            return False
        self.backend.click(x, y)
        return not stopEvent.is_set()

    def _findBag(self, img):
        """主畫面右上角的背包鈕。底色會跟著場景變(沙灘上分數只有 0.79 左右，剛好壓線)，縮放差一點就掉到門檻下，
        一旦掉了會把主畫面誤判成拋竿中、亂按拋竿鈕。所以附近的縮放都試，任何一個過門檻就算有"""
        if self.isAdb:  # 手機版維持整張找(圖和位置沒驗證過)
            return self._find(img, "bagBtn")
        # 只看遊戲畫面右上角那一塊(整個螢幕去比對有遮罩的圖很慢，每一輪都要做)
        gx, gy, gw, gh = self._gameRect(img)
        x0, y0 = max(0, gx + int(gw * 0.75)), max(0, gy)
        region = img[y0 : gy + int(gh * 0.45), x0 : gx + gw]
        if region.size == 0:
            return None
        threshold = getThreshold(self.config, "bagBtn")
        scale = resolveScale(self.backend, self.config, img, self._templatePath("bagBtn"))
        for factor in (1.0, 0.97, 1.03, 0.94, 1.06):
            pos = matchBest(region, self._templatePath("bagBtn"), threshold, scale * factor)
            if pos:
                return (pos[0] + x0, pos[1] + y0)
        return None

    def _state(self, img):
        if self._find(img, "closeBtn"):
            return "bag"
        if self._findBag(img):
            return "idle"
        if self._findCast(img):
            return "casting"
        return "unknown"

    def _waitFor(self, name, stopEvent, timeout=UI_WAIT_SECONDS):
        """等某個圖案出現，回傳 (影像, 座標)；逾時或停止回傳 (影像, None)"""
        deadline = time.time() + timeout
        while True:
            img = self._shot()
            pos = self._find(img, name)
            if pos or time.time() >= deadline or stopEvent.wait(0.3):
                return img, pos

    def _clickWhenVisible(self, name, stopEvent, timeout=UI_WAIT_SECONDS):
        """等圖案出現並點擊，回傳是否成功"""
        _, pos = self._waitFor(name, stopEvent, timeout)
        if not pos:
            return False
        return self._click(pos, stopEvent)

    def _closeBag(self, stopEvent):
        """關閉背包(或工具包)回到主畫面"""
        for _ in range(3):
            img = self._shot()
            cancelPos = self._find(img, "cancelBtn")
            if cancelPos:  # 擋在最上面的確認視窗
                if not self._click(cancelPos, stopEvent) or stopEvent.wait(0.8):
                    return False
                continue
            searchClosePos = self._find(img, "searchCloseBtn")
            if searchClosePos:  # 搜尋/篩選視窗開著: 背包的叉叉被它擋住，要先關這個視窗
                if not self._click(searchClosePos, stopEvent) or stopEvent.wait(0.8):
                    return False
                continue
            if self._find(img, "bagBtn") and not self._find(img, "closeBtn"):
                return True
            pos = self._find(img, "closeBtn")
            if pos and not self._click(pos, stopEvent):
                return False
            if stopEvent.wait(0.8):
                return False
        return False

    def _closeToolPanel(self, stopEvent):
        """工具包(T)沒有要用就要關起來，開著會擋住拋竿。回傳是否已關閉"""
        for _ in range(3):
            img = self._shot()
            if not self._find(img, "repairBoxItem"):
                return True
            toolPos = self._find(img, "toolBtn")
            if toolPos and not self._click(toolPos, stopEvent):
                return False
            if not toolPos and not self.isAdb:
                self.backend.pressKey("t")  # 同上: 認不到圖示就按 T 關
            if stopEvent.wait(1.0):
                return False
        return not self._find(self._shot(), "repairBoxItem")

    # ===== 項目 =====
    def _bagView(self, img):
        """背包目前停在哪個畫面: fishing(上次留下的釣魚用品清單)、result(其他篩選或搜尋結果)、full(完整清單，有搜尋圖示)"""
        if self._find(img, "filterHeaderFishing"):
            return "fishing"
        if self._find(img, "endFilterBtn") or self._find(img, "endSearchBtn"):
            return "result"
        if self._find(img, "searchBtn"):
            return "full"
        return None

    def _endResult(self, img, stopEvent):
        """按「結束篩選」(或「結束搜尋」)回到完整清單。回傳 False 代表已收到停止"""
        pos = self._find(img, "endFilterBtn") or self._find(img, "endSearchBtn")
        return bool(pos) and self._click(pos, stopEvent)

    def _openBagView(self, stopEvent, wanted):
        """打開背包並等到想要的畫面，回傳 (影像, 畫面)。
        遊戲的「下次開啟背包保持該結果」開著時，背包會直接停在上次的清單，要先按「結束篩選」才有搜尋圖示。
        wanted 是 "fishing" 時，已經停在釣魚用品清單就直接用；其餘情況一律先回到完整清單"""
        if not self._clickWhenVisible("bagBtn", stopEvent, 2):
            raise RuntimeError("找不到背包圖示")
        for _ in range(6):
            if stopEvent.wait(0.6):
                return None, None
            img = self._shot()
            view = self._bagView(img)
            if view == "fishing" and wanted == "fishing":
                return img, view
            if view in ("fishing", "result"):
                if not self._endResult(img, stopEvent):
                    return None, None
                continue
            if view == "full":
                return img, view
        raise RuntimeError("背包打開後找不到搜尋圖示，背包可能不是預設狀態，請先清除搜尋、篩選與分類，回到完整清單")

    def _openBagFilter(self, stopEvent, shouldContinue=None):
        """打開背包 -> 搜尋 -> 回傳 (搜尋圖示座標)。
        給了 shouldContinue 時，背包一打開(還沒搜尋)就先用它判斷要不要繼續，不繼續就關掉背包並回傳 False；已收到停止回傳 None"""
        img, view = self._openBagView(stopEvent, "full")
        if view is None:
            return None
        if shouldContinue and not shouldContinue(img):
            self._closeBag(stopEvent)
            return False
        searchPos = self._find(img, "searchBtn")
        if not self._click(searchPos, stopEvent):
            return None
        if not self._waitFor("searchSubmitBtn", stopEvent)[1]:
            raise RuntimeError("找不到搜尋視窗")
        return searchPos

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
            return False
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
        if stopEvent.wait(1.5):
            return False
        return self._closeBag(stopEvent)

    def _doPerfume(self, stopEvent):
        return self._useFishingItem("perfumeItem", "sprayBtn", "香水", stopEvent)

    def _doBait(self, stopEvent):
        return self._useFishingItem("baitItem", "useBtn", "誘魚器", stopEvent)

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

    def _satiety(self, img):
        """讀地圖旁邊體力條的飽食度(0~100)，畫面上沒有體力條回傳 None。
        用體力圖示定位，再數體力條裡青綠色填滿的部分佔整條的比例(誤差約 ±2%)。超過 100 時外框會變橘色(條是滿的)，直接當 100"""
        pos = self._find(img, "staminaIcon")
        if not pos:
            return None
        s = self._scale()
        # 條上有「41/180」這樣的數字，優先用 OCR 讀(最準)；讀不到(沒有 OCR、字被擋住)才用下面數條長度估算
        tx0, ty0, tx1, ty1 = SATIETY_TEXT
        text = img[max(0, int(pos[1] + ty0 * s)) : int(pos[1] + ty1 * s), max(0, int(pos[0] + tx0 * s)) : int(pos[0] + tx1 * s)]
        fraction = readFraction(text)
        bar = self._barSatiety(img, pos)
        if fraction and fraction[0] > max(SATIETY_MAXIMUMS):  # 「76/100」被讀成「718」這種不可能的數字，不信 OCR
            fraction = None
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
        # 條是半透明的，空條的底色會隨背景變: 暗藍(色相 97~103、飽和度 150 以上、亮度 80~130)或灰藍(飽和度 60~150、亮度到 175)，都不是青綠色
        empty = (hsv[:, :, 0] >= 90) & (hsv[:, :, 0] <= 105) & (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 70) & (hsv[:, :, 2] <= 175) & ~teal
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

    def _foodCellPos(self, searchPos, k):
        s = self._scale()
        return (
            searchPos[0] + (RESULT_FIRST_OFFSET[0] + RESULT_STEP * (k % RESULT_COLUMNS)) * s,
            searchPos[1] + (RESULT_FIRST_OFFSET[1] + RESULT_STEP * (k // RESULT_COLUMNS)) * s,
        )

    def _shouldEat(self, bagImg):
        satiety = self._satiety(bagImg)  # 背包已經打開了，順便看一下，已經夠飽就不吃(讀不到就照提示吃)
        if satiety is not None and satiety >= self._foodUntil():
            self.setLog(f"飽食度 {satiety}%，已達 {self._foodUntil()}%")
            return False
        # 確定要吃了才寫「吃食物中...」(開始時只是打開背包確認，夠飽就不吃)
        self.setLog("吃食物中..." if satiety is None else f"吃食物中...（飽食度 {satiety}%，吃到 {self._foodUntil()}%）")
        return True

    def _onFoodTyped(self, stopEvent):
        """食物名稱輸入完、按搜尋之前的時機，給子類別加料。回傳 False 代表已收到停止"""
        return True

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
            if satiety is None or satiety >= self._foodUntil():
                return True
            self.setLog(f"飽食度 {satiety}%，還沒到 {self._foodUntil()}%，再吃一份")
        return True

    def _readSatiety(self, stopEvent, tries=4):
        """吃完讀飽食度。吃的當下數字會跳動、被特效蓋住，一次讀不到就隔一下再讀，不要馬上當作吃飽停下來"""
        for i in range(tries):
            satiety = self._satiety(self._shot())
            if satiety is not None:
                self._trace(f"吃完飽食度 {satiety}%（目標 {self._foodUntil()}%）")
                return satiety
            self._trace(f"飽食度讀不到（第 {i + 1} 次）")
            if stopEvent.wait(0.6):
                return None
        return None

    def _eatOnce(self, stopEvent):
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
        if not self._onFoodTyped(stopEvent):
            return False
        if not self._click(submitPos, stopEvent):
            return False
        if stopEvent.wait(1.0):
            return False

        return self._eatFromResults(self._shot(), searchPos, name, stars, stopEvent)

    def _onNoFood(self):
        """背包裡已經沒有可以吃的食物(找不到，或剩下的都只剩最後一次)。預設照舊: 這次不吃，等下個間隔再試；子類別可改成停止"""

    def _eatFromResults(self, img, searchPos, name, stars, stopEvent):
        """吃搜尋結果裡的食物。吃完飽食度還低於設定值時，背包不關、搜尋結果也不重搜，直接接著吃下一份"""
        for meal in itertools.count():  # 不限份數，吃到達標、沒食物或按停止為止
            result = self._eatPass(img, searchPos, name, stars, stopEvent, meal > 0)
            if result != "again":
                return result
            self.setLog(f"飽食度還沒到 {self._foodUntil()}%，再吃一份")
            if stopEvent.wait(0.8):
                return False
            img = self._shot()
        return self._closeBag(stopEvent)

    def _eatPass(self, img, searchPos, name, stars, stopEvent, ateBefore=False):
        """在搜尋結果裡依序找可以吃的那一格並食用。可使用多次的食物每一份各佔一格(各自有剩餘次數)，
        勾了「保留最後一次不吃」時，剩最後一次的那格就跳過、往下一格找，都沒有才放棄。
        ateBefore: 這一輪已經吃過了，這次找不到就是吃完了，不算錯誤。回傳 "again" 代表吃了一份、背包維持開著，飽食度還不夠"""
        scale = self._scale()
        ban5 = stars == STARS_NO5  # 「1-4 星」: 不限星級，但 5 星的不吃
        if ban5:
            stars = 0
        seen = []
        tried = 0
        for k in range(RESULT_MAX_CELLS):
            pos = self._foodCellPos(searchPos, k)
            count = self._countStars(img, pos, scale)
            seen.append(count)
            if stars and count != stars:
                continue
            if not stars and k and not count:  # 不限星級: 第一格一定試，後面只看有東西的格子
                continue
            if not stars and ban5 and count >= MAX_STARS:  # 不限星級時，5 星的不吃
                continue
            tried += 1
            result = self._eatItem(pos, name, stopEvent)
            if result != "last":
                return result
            self.setLog(f"第 {k + 1} 格「{name}」只剩最後一次，往下找")
        self._closeBag(stopEvent)
        if tried == 0 and not ateBefore and self._lockedEating:
            self.setLog("鎖定的結果裡找不到食物，背包可能停在別的篩選，改用搜尋")
            return "retry"
        if tried == 0 and ateBefore:
            self.setLog(f"「{name}」已經吃完了")
            self._noMoreFood = True
            self._onNoFood()
            return True
        if tried == 0:
            self._onNoFood()
            which = f"{stars} 星的" if stars else ("非 5 星的" if ban5 else "")
            self.setLog(f"背包找不到{which}「{name}」(搜尋結果各格星級: {seen})")
            raise OutOfStock("食物")
        self.setLog(f"「{name}」的每一份都只剩最後一次，保留不吃")
        if not self._keepLastNotified:  # 食物快吃完了，通知一次就好
            self._keepLastNotified = True
            notify(f"心動小鎮助手｜{self.roleName}", f"「{name}」只剩最後一次，已保留不吃", flash=False)
        self._onNoFood()
        return True

    def _checkLastUse(self, stopEvent):
        """點了食物後看說明裡的「可用次數」是不是只剩 1。說明要等一下才出現，所以最多等 2 秒；
        回傳 True(最後一次)、False(還有多次)、None(沒有這一行，只能用一次的食物，或始終看不清楚)。
        看不到次數時回傳 None 但會寫進日誌，方便確認是不是辨識失敗"""
        deadline = time.time() + 2.0
        img = None
        while True:
            if stopEvent.wait(0.3):
                return None
            img = self._shot()
            if self._find(img, "usesLabel"):
                break
            if time.time() >= deadline:
                if not self._usesNotFoundLogged:
                    self._usesNotFoundLogged = True
                    self.setLog("當前為一次性食物")
                return None
        last = self._isLastUse(img)
        if last is None:
            self.setLog("看到「可用次數」但讀不出數字，當作還有多次")
        return last

    def _eatItem(self, itemPos, name, stopEvent):
        """點選背包裡的食物並食用，最後關閉背包。回傳 "last" 代表這份只剩最後一次(保留不吃，背包維持開著)，"again" 代表吃了但飽食度還不夠(背包維持開著)，False 代表已收到停止"""
        if not self._click(itemPos, stopEvent):
            return False
        if self._flag("fishingFoodKeepLast") and self._checkLastUse(stopEvent):
            return "last"
        if not self._clickWhenVisible("eatBtn", stopEvent, 2):
            self._closeBag(stopEvent)
            raise RuntimeError(f"找不到「{name}」的食用按鈕")
        if stopEvent.wait(1.0):
            return False
        # 體力已滿時會跳出確認視窗，吃了也不會增加，直接取消
        if self._find(self._shot(), "cancelBtn"):
            self.setLog("體力已滿，略過吃食物")
        else:
            self._ate = True
            self._meals += 1
            if stopEvent.wait(0.5):
                return False
            satiety = self._readSatiety(stopEvent)  # 背包開著也看得到體力條
            if satiety is not None and satiety < self._foodUntil():
                return "again"
        return self._closeBag(stopEvent)

    def _doRepair(self, stopEvent):
        # 依畫面狀態重試: 工具包沒開就點 T 鈕，開了就點維修盒，點完面板會自動關閉。
        # (視窗剛切到前景時第一下點擊會被吃掉，所以不能假設點了就一定開了)
        for _ in range(4):
            img = self._shot()
            itemPos = self._find(img, "repairBoxItem")
            if not itemPos:
                toolPos = self._find(img, "toolBtn")
                if not toolPos and self.isAdb:
                    raise RuntimeError("找不到工具包圖示")
                # 電腦版: 工具包圖示是半透明的，在淺色沙灘上認不出來，改按鍵盤 T(圖示上寫的快捷鍵)
                if toolPos:
                    if not self._click(toolPos, stopEvent) or stopEvent.wait(1.2):
                        return False
                else:
                    self.backend.pressKey("t")
                    if stopEvent.wait(1.2):
                        return False
                continue
            if not self._click(itemPos, stopEvent) or stopEvent.wait(1.5):
                return False
            if not self._find(self._shot(), "repairBoxItem"):
                return not stopEvent.wait(1.5)
        if not self._closeToolPanel(stopEvent):
            self.setLog("⚠️ 工具包無法關閉，請手動關閉")
        raise RuntimeError("無法從工具包丟出維修盒(可能還在冷卻或上一個還沒消失)")

    LABELS = {"repair": "丟維修盒", "food": "吃食物", "perfume": "噴香水", "bait": "放誘魚器"}

    def _runTask(self, name, fn, stopEvent):
        if name == "food":
            self._hungry = False  # 這一輪吃完(或失敗)之前不重複觸發，下次再看到提示才吃
        if name != "food":  # 吃食物要等開了背包、確定不夠飽才寫(見 _shouldEat)
            self.setLog(f"{self.LABELS[name]}中...")
        self._wakeMouse(stopEvent)
        self._meals = 0
        try:
            ok = fn(stopEvent)
            if name == "food" and self._meals:
                self.setLog(f"共吃了 {self._meals} 份")
        except OutOfStock as e:
            if name == "food":
                # 沒食物不影響繼續做事(體力沒了遊戲自己會顯示)，不停用、不閃爍，下個間隔再看看補了沒
                if not self._noFoodNotified:
                    self._noFoodNotified = True
                    self._alert(f"背包裡沒有可以吃的{e}了，請補充", flash=False)
                self._markDone(name)
            else:
                self._alert(f"背包裡的{e}用完了（數量 0），請補充後重新開始", flash=(name == "perfume"))
                self.disabled.add(name)
            self._closeBag(stopEvent)
            self._closeToolPanel(stopEvent)
            return
        except RuntimeError as e:
            if name == "repair":
                # 維修盒可能只是還在冷卻，等下一個間隔再試
                self.setLog(f"⚠️ {self.LABELS[name]}失敗：{e}，下個間隔再試")
                self._markDone(name)
                self._toolBroken = False  # 不然提示還在畫面上時會馬上又丟一次，一直失敗一直重試
                self._toolFixedAt = time.time()
            else:
                # 畫面操作卡住(找不到按鈕等): 下個間隔再試，連續失敗 MAX_TASK_FAILURES 次就停用；只在第一次與停用時通知，避免一直跳
                fails = self._failCount.get(name, 0) + 1
                self._failCount[name] = fails
                if fails >= MAX_TASK_FAILURES:
                    self._alert(f"{self.LABELS[name]}連續失敗 {fails} 次（{e}），不再嘗試，請確認畫面與設定後重新開始")
                    self.disabled.add(name)
                elif fails == 1:
                    self._alert(f"{self.LABELS[name]}失敗：{e}，下個間隔再試", flash=False)
                    self._markDone(name)
                else:
                    self.setLog(f"⚠️ {self.LABELS[name]}失敗（第 {fails} 次）：{e}，下個間隔再試")
                    self._markDone(name)
            self._closeBag(stopEvent)
            self._closeToolPanel(stopEvent)
            return
        if ok:
            self._failCount.pop(name, None)
            self._markDone(name)
            if name == "repair":
                self._toolBroken = False
                self._toolFixedAt = time.time()
            if name == "food":
                self._noFoodNotified = False

    # ===== 主迴圈 =====
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

    def _mainLoop(self, stopEvent):
        frequency = float(self.config.get("fishingDetectFrequency", 0.2))
        unknownSince = None
        failedCasts = 0
        retractLogged = False

        # 丟維修盒預設不在開始時馬上做，從現在起算一個間隔後才第一次執行；吃食物則開始就先檢查一次(夠飽就不吃)
        self._hungry = False  # 開始時不先吃，等看到「體力不足」提示才吃
        self._baitChecked = False
        self._toolBroken = False
        self._toolFixedAt = None
        self._toolBrokenAlerted = False
        self._logOcrStatus()
        if not self._flag("fishingRepairNow"):
            self._markDone("repair")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        if not self._flag("fishingAutoCast"):
            self.setLog("自動拋竿已關閉，只會定時執行勾選的項目")

        self._trace("========== 開始運行 ==========", reset=True)
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

            if state == "idle" and not self._flag("fishingUseRepair") and not self._toolBrokenAlerted and self._findToolBroken(img):
                self._toolBrokenAlerted = True
                self._alert("工具耐久耗盡，請使用維修盒（可以勾選「丟維修盒」讓程式自動處理）", flash=False)

            if state == "idle":
                if self._flag("fishingAutoCast") and not self._findCast(img):  # 主畫面卻比對不到拋竿鈕: 記下這個帳號的拋竿鈕樣子(自動拋竿關閉時用不到，不記)
                    self._learnCast(img)
                due = self._dueTasks(img)
                if due:
                    for name, fn in due:
                        if stopEvent.is_set():
                            return
                        self._runTask(name, fn, stopEvent)
                        self._closeBag(stopEvent)
                    continue
                if not self._flag("fishingAutoCast"):
                    continue
                if self._baitUnchecked():
                    continue  # 還沒確認水面有沒有魚尾(有的話不放誘魚器，沒有就要放)，確認前不拋竿
                if self._missingSince is not None and "perfume" not in self.disabled:
                    continue  # 看不到香水圖示，正在等一兩秒的反應時間再補噴；這時不能拋竿，否則一拋竿計時就重來，永遠噴不到
                if self.lastDone.get("perfume") is None and "perfume" not in self.disabled:
                    continue  # 剛開始，還在確認有沒有香水效果(連續幾次看不到圖示才噴)，先不拋竿
                if "perfume" in self.disabled:  # 沒有香水效果又噴不了，不繼續釣魚
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
                if not self._flag("fishingFaceFront"):  # 沒勾轉回正面: 只記新魚，不操作
                    self._newFish = False
                if (self._newFish or failedCasts) and self._flag("fishingFaceFront"):  # 釣到新魚後角色會轉向鏡頭拋不出去，往上走一下(電腦版按住 W，模擬器把左下角的搖桿往上推)轉回正面
                    self.setLog("釣到新魚，讓角色轉回正面" if self._newFish else "拋竿失敗，讓角色轉回正面")
                    self._newFish = False
                    self._waitNewFishGone(stopEvent)
                    self._faceFront()
                    if stopEvent.wait(0.8):
                        return
                self._wakeMouse(stopEvent)
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
                    if self._state(shot) != "idle":
                        hidden = True
                        break
                    if toast is None and self._flag("fishingUseRepair") and self._findToolBroken(shot):
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
                    else:
                        self.setLog("畫面無法辨識，也找不到返回按鈕")
                    unknownSince = None

    def _faceFront(self):
        if not self.isAdb:
            self.backend.pressKey("w", FACE_FRONT_HOLD)
            return
        h, w = self._shot().shape[:2]
        x, y = w * JOYSTICK_CENTER[0], h * JOYSTICK_CENTER[1]
        self.backend.swipe(x, y, x, y - h * JOYSTICK_PUSH, int(FACE_FRONT_HOLD * 1000))

    def _markNewFish(self, source="", img=None):
        """釣到新魚(新圖鑑的提示卡，或「太棒了！釣到了新紀錄的…」對話框): 記下來，寫日誌並跳桌面通知(不閃工作列圖示)。
        角色會轉向鏡頭，下次拋竿前要轉回正面"""
        if self._newFish:
            return
        self._newFish = True
        self._newFishAt = time.time()
        self._trace(f"釣到新魚（{source}）")
        if img is not None:  # 誤判時看這張就知道是哪一種判斷把什麼當成了新魚
            self._saveShot(img, {"提示卡": "newfish_card", "收竿時的提示卡": "newfish_reel", "對話框": "newfish_dialog"}.get(source, "newfish"))
        self.setLog("釣到新魚")
        notify(f"心動小鎮助手｜{self.roleName}", "釣到新魚", flash=False)

    def _hasNewFishCard(self, img):
        """釣到新魚的提示: 右上角有「新圖鑑！」字樣，或有一張星星卡(新的星數才會出現，釣過的不會有)，任一個就算"""
        x, y, w, h = self._gameRect(img)
        region = img[max(0, y) : y + int(h * 0.3), max(0, x + int(w * 0.7)) : x + w]  # 畫面中上方的「鬥竿專家」之類橫幅也是白字藍底，所以不看中間
        if region.size > 0 and self._find(region, "newFishMark") is not None:
            return True
        return self._hasStarCard(img)

    def _hasStarCard(self, img):
        """右側(離最上排的圖示列一段距離)有金黃色實心星星 = 新星數的星星卡。
        不靠模板，找大小相近的金色近似正方形小塊；範圍避開最上排的活動圖示，也避開畫面中央角色旁邊的閃光"""
        x, y, w, h = self._gameRect(img)
        roi = img[max(0, y + int(h * 0.15)) : y + int(h * 0.4), max(0, x + int(w * 0.7)) : x + w, :3]
        if roi.size == 0:
            return False
        mask = cv2.inRange(cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV), STAR_HSV_LOW, STAR_HSV_HIGH)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, count):
            sw, sh, area = stats[i][2], stats[i][3], stats[i][4]
            if h * 0.012 <= sw <= h * 0.05 and h * 0.012 <= sh <= h * 0.05 and 0.7 <= sw / sh <= 1.4 and area >= sw * sh * 0.4:
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
