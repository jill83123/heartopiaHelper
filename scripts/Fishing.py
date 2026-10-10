import os
import random
import time
import cv2
import numpy as np
from scripts.base import BaseTask
from tools.notify import notify
from tools.finder import clickBackButton, getBaseScale, matchBestAuto
from tools.tools import getThreshold, loadTemplate, matchBest

UI_WAIT_SECONDS = 4  # 等待某個畫面/按鈕出現的上限(秒)
CAST_SETTLE_SECONDS = 3  # 按下拋竿(或收竿)後，等 UI 隱藏/顯示的時間
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
DIALOG_REGION = (0.25, 0.82, 0.75, 0.94)  # 在遊戲畫面中的範圍(比例): 左、上、右、下
DIALOG_WHITE_MIN = 225  # 三個色彩通道都要高於這個值才算白色
DIALOG_WHITE_RATIO = 0.6
# 吃食物有兩種時機: 每隔設定的分鐘數吃一次，或操作中剛好看得到體力條(地圖旁邊)且低於設定的 % 數就吃。
# 不會為了看飽食度特地開背包。看到的飽食度高於設定的 % 就不吃，並把定時的下一次往後延一個間隔
SATIETY_BAR = (-4, -18, 200, 18)  # 體力條範圍，相對於體力圖示中心: 左、上、右、下(1600 寬)
SATIETY_ROW_SKIP = 3  # 取樣列離體力條上緣的距離(避開上緣的外框與中間的數字)
# 沒有香水時魚要自己拉: 咬鉤時浮標上方會跳出藍色「!」(約 0.7 秒)，要立刻按住 F 收線；
# 魚線顏色代表張力(白 → 黃 → 橘 → 紅)，橘色或紅色時要放開，回到黃或白再按住，否則線會斷
BITE_MARK = "biteMark"
LINE_HOT_PIXELS = 900  # 橘色 + 紅色的像素數達到這個值就視為張力太大(以 1600 寬為基準，依畫面縮放換算)
REEL_POLL_SECONDS = 0.05
REEL_STATE_EVERY = 10  # 收線時每幾次偵測才確認一次是不是已經回到主畫面(確認比較花時間)
REEL_END_SECONDS = 0.8  # 離開拋竿畫面超過這麼久才當作收線結束
REEL_MAX_SECONDS = 120  # 收線最久多少秒，避免卡死
PERFUME_REACTION_RANGE = (3.0, 4.5)  # 圖示要「連續」消失這麼久(秒)才補噴，避免閃爍、被遮住或偶爾認不出來就誤噴
CASTING_LOOK_SECONDS = 40  # 拋竿中看不到圖示；有香水魚很快上鉤，連續拋竿超過這麼久都沒釣到魚，就收竿回主畫面看看香水還在不在
START_PERFUME_CHECKS = 4  # 剛開始運行時，主畫面連續這麼多次看不到香水圖示才噴(約 1 秒)，避免漏看
PERFUME_ICON_GRACE = 15  # 噴完香水後，地圖下方的圖示要多久內出現(秒)，超過還沒出現就再噴
SEARCH_INPUT_OFFSET = -385  # 搜尋輸入框在「搜尋」按鈕左邊多遠


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
        self._missingSince = None  # 香水圖示從什麼時候開始看不到
        self._reaction = 0.0
        self._keepLastNotified = False
        self._hungry = False  # 最近一次看到的飽食度已經低於設定的 %
        self._learnedCast = None  # 這個帳號的拋竿鈕圖片(BGR)

    def _threadTargets(self):
        return [self._mainLoop]

    # ===== 設定 =====
    def _alert(self, msg):
        """需要使用者處理的狀況: 寫進日誌，也跳出桌面通知"""
        self.setLog(f"⚠️ {msg}")
        notify(f"心動小鎮助手｜{self.roleName}", msg)

    def _flag(self, key):
        return str(self.config.get(key, "False")).lower() == "true"

    def _seconds(self, key):
        return float(self.config.get(key))

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
        if name == "perfume":
            return self._seconds("fishingPerfumeSeconds")
        if name == "bait":
            return self._seconds("fishingBaitSeconds")
        if name == "food":
            return self._minutes("fishingFoodMinutes")
        return self._minutes("fishingRepairMinutes")

    def _markDone(self, name):
        """記下執行時間，並抽出下一輪間隔要額外延後幾秒"""
        interval = self._interval(name)
        cap = interval * INTERVAL_JITTER
        if name in ITEM_DURATION:
            cap = min(cap, max(0, interval - ITEM_DURATION[name]))
        self.lastDone[name] = time.time()
        self.jitter[name] = random.uniform(0, cap)
        if name == "perfume":
            self._missingSince = None

    def _isDue(self, name, img, casting=False):
        last = self.lastDone.get(name)
        wait = self._interval(name) + self.jitter.get(name, 0)
        if name == "perfume":
            if casting:
                # 拋竿中整個 UI 都隱藏，看不到圖示。不看噴香水的時間，只看圖示: 魚上鉤、回到主畫面時就會看到。
                # 但連續拋竿太久都沒魚(香水可能沒了)，或剛開始運行還沒確認過，就收竿回主畫面看圖示再決定噴不噴
                return last is None or (self._castingSince is not None and time.time() - self._castingSince >= CASTING_LOOK_SECONDS)
            if self._find(img, "perfumeBuff") is not None:
                self._sprayNoIcon = 0
                self._missingSince = None
                if last is None:  # 開始前已經噴過，從現在開始計時
                    self.setLog("已有香水效果，不重複噴，之後看到圖示消失再補噴")
                    self._markDone(name)
                return False
            # 地圖下方沒有香水圖示: 沒有香水效果就不能繼續釣魚，要補噴(剛噴完圖示還沒出現，先給一點緩衝)
            if last is not None and time.time() - last < PERFUME_ICON_GRACE:
                return False
            if last is None:
                # 剛開始運行: 先確認現在有沒有香水效果，連續幾次都看不到圖示才噴(有的話不能再噴，會浪費)
                self._startMiss += 1
                if self._startMiss < START_PERFUME_CHECKS:
                    return False
            if last is not None:
                # 不是一發現就噴，隔一兩秒才反應
                if self._missingSince is None:
                    self._missingSince = time.time()
                    self._reaction = random.uniform(*PERFUME_REACTION_RANGE)
                if time.time() - self._missingSince < self._reaction:
                    return False
                self.setLog(f"看不到香水圖示（距上次噴 {int(time.time() - last)} 秒），補噴")
                self._sprayNoIcon += 1
                if self._sprayNoIcon >= 2:
                    self._alert("噴了香水後仍看不到香水圖示，不再噴香水，也不繼續釣魚，請確認畫面與設定")
                    self.disabled.add(name)
                    return False
            return True
        if name == "food":
            if casting:
                return False
            self._noteSatiety(img)  # 畫面上剛好看得到體力條: 餓了馬上吃，還很飽就把定時往後延
            if self._hungry:
                return True
            last = self.lastDone.get(name)
            wait = self._interval(name) + self.jitter.get(name, 0)
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
            if os.path.exists(path):
                return path
        return f"{self.templateDir}/{name}.png"

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
            self.setLog("已記下拋竿鈕的樣子（釣竿造型不同也能辨識）")
        self._learnedCast = crop.copy()

    def _findBite(self, img):
        """浮標上方有藍色「!」代表魚咬鉤了。回傳座標或 None"""
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

    def _findDialog(self, img):
        """畫面下方有白色對話框(例如釣到新紀錄的魚)就回傳框中心的影像座標，沒有回傳 None"""
        x, y, w, h = self._gameRect(img)
        left, top, right, bottom = DIALOG_REGION
        x0, y0, x1, y1 = int(x + w * left), int(y + h * top), int(x + w * right), int(y + h * bottom)
        region = img[max(0, y0) : y1, max(0, x0) : x1]
        if region.size == 0:
            return None
        white = (region >= DIALOG_WHITE_MIN).all(axis=2).mean()
        return ((x0 + x1) / 2, (y0 + y1) / 2) if white >= DIALOG_WHITE_RATIO else None

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

    def _state(self, img):
        if self._find(img, "closeBtn"):
            return "bag"
        if self._find(img, "bagBtn"):
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
            if view in ("fishing", "full"):
                self._noteSatiety(img)  # 背包打開時看得到體力條，順便判斷要不要吃
            if view == "fishing" and wanted == "fishing":
                return img, view
            if view in ("fishing", "result"):
                if not self._endResult(img, stopEvent):
                    return None, None
                continue
            if view == "full":
                return img, view
        raise RuntimeError("背包打開後找不到搜尋圖示")

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

    def _openFishingList(self, stopEvent):
        """打開背包並停在「釣魚用品」清單。第一次選好分類後，順便開啟「下次開啟背包保持該結果」，
        之後再打開背包就已經在這個清單，不用每次重新搜尋、選分類。回傳 False 代表已收到停止"""
        img, view = self._openBagView(stopEvent, "fishing")
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
            self._closeBag(stopEvent)
            raise RuntimeError(f"背包找不到{label}(可能用完了)")
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

    def _noteSatiety(self, img):
        """畫面上看得到體力條時: 低於設定的 % 就記下餓了；還很飽就把定時的下一次往後延一個間隔(從現在重新計時)"""
        if not self._flag("fishingUseFood"):
            return
        satiety = self._satiety(img)
        if satiety is None:
            return
        self._hungry = satiety <= self._foodBelow()
        if satiety > self._skipAbove():
            self._markDone("food")

    def _skipAbove(self):
        """高於這個飽食度就不吃: 完全以使用者設定的「飽食度低於 %」為準"""
        return self._foodBelow()

    def _foodBelow(self):
        return int(self.config.get("fishingFoodBelow", 50))

    def _satiety(self, img):
        """讀地圖旁邊體力條的飽食度(0~100)，畫面上沒有體力條回傳 None。
        用體力圖示定位，再數體力條裡青綠色填滿的部分佔整條的比例(誤差約 ±2%)。超過 100 時外框會變橘色(條是滿的)，直接當 100"""
        pos = self._find(img, "staminaIcon")
        if not pos:
            return None
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
        first = int(np.argmax(counts >= 0.6 * width))
        row = first + round(SATIETY_ROW_SKIP * s)
        return min(100, round(100 * counts[row : row + 2].mean() / width))

    def _foodCellPos(self, searchPos, k):
        s = self._scale()
        return (
            searchPos[0] + (RESULT_FIRST_OFFSET[0] + RESULT_STEP * (k % RESULT_COLUMNS)) * s,
            searchPos[1] + (RESULT_FIRST_OFFSET[1] + RESULT_STEP * (k // RESULT_COLUMNS)) * s,
        )

    def _shouldEat(self, bagImg):
        satiety = self._satiety(bagImg)  # 背包已經打開了，順便看一下，還很飽就不吃(讀不到就照時間吃)
        if satiety is not None and satiety > self._skipAbove():
            self.setLog(f"飽食度 {satiety}%，還不用吃")
            return False
        if satiety is not None and satiety <= self._foodBelow():
            self.setLog(f"飽食度 {satiety}%（低於 {self._foodBelow()}%），吃食物")
        return True

    def _onFoodTyped(self, stopEvent):
        """食物名稱輸入完、按搜尋之前的時機，給子類別加料。回傳 False 代表已收到停止"""
        return True

    def _doFood(self, stopEvent):
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
        """在搜尋結果裡依序找可以吃的那一格並食用。可使用多次的食物每一份各佔一格(各自有剩餘次數)，
        勾了「保留最後一次不吃」時，剩最後一次的那格就跳過、往下一格找，都沒有才放棄"""
        scale = self._scale()
        ban5 = self._flag("fishingFoodBan5")
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
        if tried == 0:
            self._onNoFood()
            which = f"{stars} 星的" if stars else ("非 5 星的" if ban5 else "")
            raise RuntimeError(f"背包找不到{which}「{name}」(搜尋結果各格星級: {seen})")
        self.setLog(f"「{name}」的每一份都只剩最後一次，保留不吃")
        if not self._keepLastNotified:  # 食物快吃完了，通知一次就好
            self._keepLastNotified = True
            notify(f"心動小鎮助手｜{self.roleName}", f"「{name}」只剩最後一次，已保留不吃")
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
                self.setLog("說明裡看不到「可用次數」，當作只能用一次的食物")
                return None
        last = self._isLastUse(img)
        if last is None:
            self.setLog("看到「可用次數」但讀不出數字，當作還有多次")
        return last

    def _eatItem(self, itemPos, name, stopEvent):
        """點選背包裡的食物並食用，最後關閉背包。回傳 "last" 代表這份只剩最後一次(保留不吃，背包維持開著)，False 代表已收到停止"""
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
        elif stopEvent.wait(0.5):
            return False
        return self._closeBag(stopEvent)

    def _doRepair(self, stopEvent):
        # 依畫面狀態重試: 工具包沒開就點 T 鈕，開了就點維修盒，點完面板會自動關閉。
        # (視窗剛切到前景時第一下點擊會被吃掉，所以不能假設點了就一定開了)
        for _ in range(4):
            img = self._shot()
            itemPos = self._find(img, "repairBoxItem")
            if not itemPos:
                toolPos = self._find(img, "toolBtn")
                if not toolPos:
                    raise RuntimeError("找不到工具包圖示")
                if not self._click(toolPos, stopEvent) or stopEvent.wait(1.2):
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
        self.setLog(f"{self.LABELS[name]}...")
        self._wakeMouse(stopEvent)
        try:
            ok = fn(stopEvent)
        except RuntimeError as e:
            if name == "repair":
                # 維修盒可能只是還在冷卻，等下一個間隔再試
                self.setLog(f"⚠️ {self.LABELS[name]}失敗：{e}，下個間隔再試")
                self._markDone(name)
            else:
                self._alert(f"{self.LABELS[name]}失敗：{e}，本次不再嘗試")
                self.disabled.add(name)
            self._closeBag(stopEvent)
            self._closeToolPanel(stopEvent)
            return
        if ok:
            self._markDone(name)
            if name == "food":
                self._hungry = False  # 吃完了，等下次看到體力條再判斷

    # ===== 主迴圈 =====
    def _mainLoop(self, stopEvent):
        frequency = float(self.config.get("fishingDetectFrequency", 0.2))
        unknownSince = None
        failedCasts = 0
        retractLogged = False

        # 吃食物、丟維修盒預設不在開始時馬上做，從現在起算一個間隔後才第一次執行
        self._markDone("food")
        if not self._flag("fishingRepairNow"):
            self._markDone("repair")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        if not self._flag("fishingAutoCast"):
            self.setLog("自動拋竿已關閉，只會定時執行勾選的項目")

        while not stopEvent.wait(frequency):
            img = self._shot()
            state = self._state(img)

            # 圖示消失要「在主畫面連續」看不到才算，離開主畫面(拋竿、開背包...)就重新計
            if state != "idle":
                self._missingSince = None
            if state == "casting":
                if self._castingSince is None:
                    self._castingSince = time.time()
            else:
                self._castingSince = None

            if state != "unknown":
                unknownSince = None
            if state != "casting":
                retractLogged = False

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
                if self._find(img, "repairBoxItem"):  # 工具包還開著
                    self._closeToolPanel(stopEvent)
                    continue
                if not self._flag("fishingAutoCast"):
                    continue
                if self.lastDone.get("perfume") is None and "perfume" not in self.disabled:
                    continue  # 剛開始，還在確認有沒有香水效果(連續幾次看不到圖示才噴)，先不拋竿
                if "perfume" in self.disabled:  # 沒有香水效果又噴不了，不繼續釣魚
                    if not self._noPerfumeLogged:
                        self._alert("沒有香水效果，也無法噴香水，暫停釣魚(請補充香水後重新開始)")
                        self._noPerfumeLogged = True
                    continue
                if failedCasts >= MAX_FAILED_CASTS:
                    self.setLog("⚠️ 連續拋竿失敗，請確認角色面向水面、手上拿著釣竿，且沒有開著選單")
                    failedCasts = 0
                    if stopEvent.wait(10):
                        return
                    continue
                self._wakeMouse(stopEvent)
                self._cast(self._shot(), stopEvent, force=True)
                if stopEvent.wait(CAST_SETTLE_SECONDS):
                    return
                failedCasts = failedCasts + 1 if self._state(self._shot()) == "idle" else 0

            elif state == "casting":
                # 咬鉤了就把這一次釣完(就算香水剛好到期，收竿魚就跑了)
                if self._findBite(img):
                    if not self._reel(stopEvent):
                        return
                    continue
                # 香水到期且圖示消失時，魚不會再自動上鉤，要把竿子收起來才能再噴。
                # 一直按拋竿鈕讓釣魚中斷，直到回到主畫面
                if self._flag("fishingAutoCast") and any(name == "perfume" for name, _ in self._dueTasks(img, casting=True)):
                    if not retractLogged:
                        self.setLog("拋竿太久沒釣到魚，收竿確認香水效果")
                        retractLogged = True
                    self._cast(img, stopEvent)
                    if stopEvent.wait(1.0):
                        return

            elif state == "bag":
                self._closeBag(stopEvent)

            else:
                dialogPos = self._findDialog(img)
                if dialogPos:  # 釣到新魚的對話框，點掉才會繼續
                    unknownSince = None
                    self._dismissDialog(dialogPos, stopEvent)
                    continue
                unknownSince = unknownSince or time.time()
                if time.time() - unknownSince > UNKNOWN_SECONDS:
                    if self.backend.isGameCovered():
                        self.setLog("⚠️ 遊戲視窗被其他視窗蓋住，前景模式需要讓遊戲保持在最上層")
                    elif clickBackButton(self.backend, self.config):
                        self.setLog("畫面無法辨識，已點擊返回按鈕")
                    else:
                        self.setLog("畫面無法辨識，也找不到返回按鈕")
                    unknownSince = None

    def _cast(self, img, stopEvent, force=False):
        """點右下角的拋竿鈕(拋竿與收竿是同一顆)。force: 已確定在主畫面，就算比對不到圖也點固定位置"""
        pos = self._findCast(img)
        if not pos and force:
            pos = self._castCenter(img)[0]
        if pos:  # 找不到多半是動畫期間，連續失敗時主迴圈會提醒
            self._click(pos, stopEvent)
