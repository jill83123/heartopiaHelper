import os
import random
import time
import cv2
import numpy as np
from scripts.common.autofood import AutoFood
from scripts.common.base import BaseTask
from tools.debug import DebugMixin
from tools.finder import matchBestAuto
from tools.tools import getResourcePath, getThreshold

# ===== 座標與數值一律以 1600x900 的遊戲畫面為基準，跑起來依實際畫面大小縮放。標「待校準」的是估的，實機測過再調 =====
SCAN_INTERVAL = 0.15  # 一般流程每輪看畫面的間隔(秒)
HOVER_DELAY = (0.15, 0.3)  # 移到按鈕上到點下去之間的停頓(秒)
IDLE_OPEN_SECONDS = 2.5  # 主畫面(認不得任何畫面)待這麼久，才去開背包
UNKNOWN_STOP_SECONDS = 60  # 認不得畫面超過這麼久就停止
SEARCH_NAME = "奇靈南瓜"  # 背包沒停在這個搜尋結果時，自動搜尋並按「保持結果」
SEARCH_INPUT_OFFSET = -385  # 搜尋輸入框在搜尋視窗「搜尋」按鈕左邊多遠(和食物搜尋同一個視窗)
SEARCH_WAIT = 4  # 等搜尋視窗出現的上限(秒)
KEEP_WAIT_SECONDS = 8  # 搜尋後最多等幾秒讓結果畫面的「保持結果」開關出現
# 雕刻出特殊結果時會跳出畫面下方的白色對話框(例如「好耶！雕刻出了不一樣的正直南瓜腿！」)，整個介面被藏起來，點一下才會回到普通畫面
DIALOG_ROI = (400, 735, 1200, 850)  # 對話框中間的範圍(1600x900 基準)
DIALOG_WHITE = 0.6  # 範圍內近白色像素的比例超過這個就是對話框；實測約 0.87，一般畫面不到 0.2
DIALOG_CLICK = (800, 790)  # 點對話框正中間
BAG_MAX_TRIES = 3  # 背包開著卻一直沒出現「擺放」鈕，幾次後判定沒有南瓜了

FIRST_SLOT = (962, 230)  # 背包搜尋結果第一格的中心(待校準)

# --- 擺放 ---
CENTER = (800, 450)  # 南瓜的 ◎ 標記固定在遊戲畫面正中央
FRAME_ROI = (650, 380, 950, 540)  # 擺放框會出現的範圍
WHITE_MIN = 800  # 範圍內近白色像素超過這個數 = 白框(可以放)；實測白框約 2000，沒框不到 70
RED_MIN = 30  # 範圍內紅色像素超過這個數 = 紅框(不能放)；實測約 70~190
PLACE_SETTLE = 0.8  # 剛進擺放模式要等多久才判斷(框會滑動)
PLACE_OK_STREAK = 2  # 連續幾輪看到白框才確認放下
PLACE_DRAG_START = (480, 315)  # 拖曳轉鏡頭的起點: 牆壁空白處，不能壓到 UI、角色、南瓜
PLACE_AFTER_DRAG = 0.7  # 拖完等多久再判斷
PLACE_MAX_TRIES = 8  # 最多轉幾次鏡頭
PLACE_NO_TABLE_MAX = 3  # 連續幾輪看不到雕刻檯就停止
# 雕刻檯是深藍紫色(實測色相 119~121、飽和度 158~185、亮度 61~110)；人物的帽子飽和度低或太暗，不會被選到
TABLE_HSV = {"hMin": 108, "hMax": 132, "sMin": 130, "vMin": 55, "vMax": 170}
TABLE_MIN_AREA = 8000  # 雕刻檯的藍紫色面積下限(1600x900 基準)；實測約 46000~52000，另一座不同樣式的檯子約 14000
TABLE_TOP_ABOVE = 83  # 擺放成功時 ◎ 落在檯面中間，檯子最上緣約在 ◎ 上方這麼遠(待校準：從失敗截圖的檯面大小估的)
TABLE_TOL = 35  # 檯子上緣和理想位置差在這個範圍內就算對準
TABLE_SIDE_MARGIN = 30  # ◎ 要在檯子左右邊緣內縮這麼多以內
PLACE_GAIN_Y = -1.0  # 往下拖 1 格檯子在畫面上往上移幾格(起始估計，之後每次拖完依實際移動量修正；實測往下拖能把檯子移上去)
PLACE_PROBE = 60  # 檯子要左右移但還不知道方向時，先試拖這麼遠量方向
PLACE_MAX_DRAG = 260  # 一次最多拖多遠

# --- 雕刻小遊戲: 右邊的垂直滑桿控制記號高度，要讓記號對準從左邊飄過來的灰線 ---
BADGE_ROI = (680, 10, 1000, 150)  # 「雕刻中...」標籤的位置
CARVE_GONE_SECONDS = 2.0  # 看不到雕刻中標籤超過這麼久，就當作雕刻結束
MARKER_X = 1073  # 記號(沙漏)的水平位置(待校準)
BOX_Y0, BOX_Y1 = 556, 752  # 灰線出現的範圍(待校準)
CURVE_LOOK = 100  # 看記號左邊多遠的灰線；灰線一進這個範圍就先按住滑桿、把記號移過去(記號有延遲，要提早)
CURVE_LEAD = 40  # 把手要對準的是「記號左邊這麼遠」那一點的灰線高度，等灰線飄到記號時記號剛好到位。實機 log 看記號比把手慢約 0.4 秒，灰線約每秒飄 100px(待校準)
CURVE_FIT = 12  # 取目標點左右各這麼寬的灰線擬合成直線，再讀目標點的高度(單點的高度有雜訊，實測會差到 ±8px)
HANDLE_X = 1282  # 滑桿把手的水平位置(待校準)
TRACK_TOP, TRACK_BOTTOM = 457, 763  # 把手中心能到的上下界(待校準)
# 把手和記號是線性對應: 把手動 1 格記號動 K 格。從錄影量到的三組 (把手, 記號) = (527, 599)、(574, 630)、(517, 592)，斜率約 0.67~0.70
MARKER_PER_HANDLE = 0.67
MARKER_REF = (527, 599)  # 找不到記號時用來估算的一組 (把手, 記號)
TARGET_SMOOTH = 0.4  # 灰線高度的平滑係數(越大越跟著最新值；小一點比較不會被雜訊帶著抖)
MAX_STEP = 30  # 每輪把手最多移動幾格，避免灰線換段時突然大跳
TRIM_GAIN = 0.05  # 用記號實際位置微調把手的係數(很小，只修校準的偏差，不能大，記號比把手慢，大了會抖)
TRIM_MAX = 30  # 微調量上限
RELEASE_SECONDS = 1.0  # 灰線消失超過這麼久就放開滑桿，下次有線再按
CARVE_INTERVAL = 0.005  # 雕刻時每輪的間隔(截圖本身就要約 30ms)
BADGE_EVERY = 12  # 每幾輪才確認一次雕刻中標籤(模板比對比較慢)
MARKER_EVERY = 4  # 每幾輪才找一次記號
TRACE_EVERY = 0.4  # 除錯模式下每隔幾秒記一次把手、記號、灰線的位置
# 灰線是淺米色: 亮度 150~238、飽和度 12~75、紅比藍多一點。白色記號、橘色軌跡、方框邊線都不會落進這個範圍
CURVE_HSV = {"vMin": 150, "vMax": 238, "sMin": 12, "sMax": 75, "rbMin": 8, "rbMax": 70}

TEMPLATES = ("bagSearch", "placeBtn", "placeExit", "placeConfirm", "carveIcon", "collectIcon", "carveBadge", "carveMarker", "carveHandle")
COMMON_DIR = "templates/common"  # 背包、搜尋相關的按鈕共用(釣魚、採集吃食物也用)
BAG_BTN_TEMPLATE = f"{COMMON_DIR}/bagBtn.png"


class Pumpkin(DebugMixin, BaseTask):
    """自動做南瓜雕刻: 開背包 → 點第一格南瓜 → 擺放到雕刻檯上(轉鏡頭對準)→ 點雕刻圖示 → 雕刻小遊戲(跟著灰線拉滑桿)→ 收取 → 重來。
    角色要先站在雕刻檯旁；背包沒停在「奇靈南瓜」的搜尋結果時會自動搜尋並鎖定。只支援前景模式(PC 版)"""

    templateDir = "templates/pumpkin"

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self.count = 0  # 已收取幾顆
        self._unknownSince = None
        self._lastUnknownLog = 0
        self._bagTries = 0
        self._geom = (1.0, 0, 0)  # (遊戲畫面縮放, 遊戲左上角在截圖裡的 x, y)；截圖是遊戲視窗被螢幕邊緣裁掉後的範圍，所以左上角不一定是 (0, 0)
        self._resetPlace()
        self.food = AutoFood(setLog, backend, config, "pumpkin", "南瓜雕刻")

    def _resetPlace(self):
        self._placingSince = None
        self._placeTries = 0
        self._okStreak = 0
        self._noTable = 0
        self._gainY = PLACE_GAIN_Y
        self._gainX = None  # 還不知道左右拖曳會讓檯子往哪邊移
        self._lastDrag = None  # (拖之前檯子的上緣, 中心 x, 拖的 dx, dy)

    def _threadTargets(self):
        return [self._loop]

    # ---------- 共用 ----------
    def _gameShot(self):
        """前景模式截到的是 RGB，統一轉成 BGR(模板是真實顏色，色相判斷也要)。
        同時記下遊戲畫面的縮放與位置: 遊戲視窗超出螢幕時截圖會被裁掉一塊，不能直接拿截圖寬度當縮放"""
        img, origin = super()._gameShot()
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])
        rect = self.backend.getGameRect()
        if rect:
            self._geom = (rect[2] / 1600, rect[0] - origin[0], rect[1] - origin[1])
        else:
            self._geom = (img.shape[1] / 1600, 0, 0)
        return img, origin

    def _pt(self, bx, by):
        """1600x900 基準的遊戲座標 -> 截圖座標"""
        s, ox, oy = self._geom
        return bx * s + ox, by * s + oy

    def _find(self, img, name, roi=None, path=None):
        """找模板，回傳中心座標(截圖座標)或 None。roi 是 1600x900 基準的 (x0, y0, x1, y1)，只在那裡找(比較快、也不會被別處誤認)"""
        x0 = y0 = 0
        if roi:
            ax, ay = self._pt(roi[0], roi[1])
            bx, by = self._pt(roi[2], roi[3])
            x0, y0 = max(0, int(ax)), max(0, int(ay))
            img = img[y0 : max(y0, int(by)), x0 : max(x0, int(bx))]
        pos = matchBestAuto(self.backend, self.config, img, path or f"{self.templateDir}/{name}.png", getThreshold(self.config, name))
        return (pos[0] + x0, pos[1] + y0) if pos else None

    def _findC(self, img, name):
        """找 templates/common 裡的共用按鈕"""
        return self._find(img, name, path=f"{COMMON_DIR}/{name}.png")

    def _clickAt(self, origin, pos, stopEvent):
        x, y = self._getRelCords(origin, pos)
        x, y = round(x + random.randint(-2, 2)), round(y + random.randint(-2, 2))
        self.backend.moveTo(x, y)
        if stopEvent.wait(random.uniform(*HOVER_DELAY)):
            return False
        self.backend.click(x, y)
        return True

    def _stop(self, stopEvent, msg):
        self.setLog(msg)
        stopEvent.set()

    # ---------- 主流程 ----------
    def _loop(self, stopEvent):
        if getattr(self.backend, "isAdb", False):
            raise RuntimeError("南瓜雕刻只支援前景模式")
        missing = [n for n in TEMPLATES if not os.path.exists(getResourcePath(f"{self.templateDir}/{n}.png"))]
        if missing:
            raise RuntimeError("缺少模板圖片：" + "、".join(missing))
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        self._startWatchdog(stopEvent)
        self._trace("南瓜雕刻開始", reset=True)
        self.food.start()

        steps = (
            ("雕刻", self._stepCarve),
            ("擺放", self._stepPlacing),
            ("擺放鈕", self._stepPlaceBtn),
            ("背包", self._stepBag),
            ("背包搜尋", self._stepBagSetup),
            ("雕刻圖示", self._stepCarveIcon),
            ("收取", self._stepCollect),
            ("提示對話", self._stepDialog),
        )
        while not stopEvent.wait(SCAN_INTERVAL):
            img, origin = self._gameShot()
            for name, step in steps:
                if step(img, origin, stopEvent):
                    self._traceChange(name, "畫面")
                    self._unknownSince = None
                    break
            else:
                self._traceChange("未知", "畫面")
                if self.food.poll(stopEvent):  # 體力不足，吃完食物後重新判斷畫面
                    self._unknownSince = None
                    continue
                self._stepIdle(img, origin, stopEvent)

    def _stepIdle(self, img, origin, stopEvent):
        """認不得任何畫面: 可能在主畫面(南瓜用完了或剛收完)，等一下還是這樣就開背包"""
        now = time.time()
        if self._unknownSince is None:
            self._unknownSince = now
            return
        waited = now - self._unknownSince
        if waited > UNKNOWN_STOP_SECONDS:
            self._stop(stopEvent, "認不得目前的畫面太久，已停止")
            return
        if waited < IDLE_OPEN_SECONDS:
            return
        bag = self._find(img, "bagBtn", path=BAG_BTN_TEMPLATE)
        if bag:
            self._clickAt(origin, bag, stopEvent)
            stopEvent.wait(1.0)
        elif now - self._lastUnknownLog > 10:
            self._lastUnknownLog = now
            self.setLog("找不到背包鈕，請回到遊戲主畫面")
            self._saveShot(img, "pumpkinUnknown")

    def _stepBag(self, img, origin, stopEvent):
        if not self._find(img, "bagSearch"):
            return False
        keep = self._findC(img, "keepOff")  # 搜尋結果還沒鎖定: 先鎖，下次開背包才會直接停在這裡
        if keep:
            self._clickAt(origin, keep, stopEvent)
            self.setLog(f"✅ 已鎖定搜尋結果：{SEARCH_NAME}")
            stopEvent.wait(0.5)
            return True
        self._bagTries += 1
        if self._bagTries > BAG_MAX_TRIES:
            self._stop(stopEvent, "背包裡找不到可以擺放的南瓜，已停止")
            return True
        self._clickAt(origin, self._pt(*FIRST_SLOT), stopEvent)
        stopEvent.wait(0.8)
        return True

    def _stepBagSetup(self, img, origin, stopEvent):
        """背包開著，但不是停在南瓜的搜尋結果: 在別的結果就先按「結束搜尋」回完整清單，再搜尋南瓜並鎖定"""
        if not self._findC(img, "closeBtn"):
            return False
        end = self._findC(img, "endFilterBtn") or self._findC(img, "endSearchBtn")
        if end:
            self._clickAt(origin, end, stopEvent)
            stopEvent.wait(0.8)
            return True
        search = self._findC(img, "searchBtn")
        if not search:
            return False
        self._searchPumpkin(origin, search, stopEvent)
        return True

    def _waitC(self, name, stopEvent, timeout):
        """等共用按鈕出現，回傳 (座標, 這張截圖的原點)；逾時或停止回傳 (None, None)"""
        deadline = time.time() + timeout
        while True:
            img, origin = self._gameShot()
            pos = self._findC(img, name)
            if pos:
                return pos, origin
            if time.time() >= deadline or stopEvent.wait(0.3):
                return None, None

    def _searchPumpkin(self, origin, searchPos, stopEvent):
        self.setLog(f"搜尋「{SEARCH_NAME}」")
        if not self._clickAt(origin, searchPos, stopEvent):
            return
        submit, origin = self._waitC("searchSubmitBtn", stopEvent, SEARCH_WAIT)
        if not submit:
            if not stopEvent.is_set():
                raise RuntimeError("找不到搜尋視窗")
            return
        inputPos = (submit[0] + SEARCH_INPUT_OFFSET * self._geom[0], submit[1])
        if not self._clickAt(origin, inputPos, stopEvent) or stopEvent.wait(0.3):
            return
        # 輸入框沒取得焦點的話，Ctrl+V 的 V 會被遊戲當成拍照鍵，所以先確認搜尋視窗還在
        if not self._findC(self._gameShot()[0], "searchSubmitBtn"):
            raise RuntimeError("搜尋視窗沒有開啟")
        self.backend.pasteText(SEARCH_NAME)
        if stopEvent.wait(0.4) or not self._clickAt(origin, submit, stopEvent) or stopEvent.wait(1.0):
            return
        keep, keepOrigin = self._waitC("keepOff", stopEvent, KEEP_WAIT_SECONDS)
        if keep:
            self._clickAt(keepOrigin, keep, stopEvent)
            self.setLog(f"✅ 已鎖定搜尋結果：{SEARCH_NAME}")
        elif not stopEvent.is_set():
            self.setLog("⚠️ 找不到「保持結果」開關，這次沒有鎖定")

    def _stepPlaceBtn(self, img, origin, stopEvent):
        pos = self._find(img, "placeBtn")
        if not pos:
            return False
        self._bagTries = 0
        self._resetPlace()
        self._clickAt(origin, pos, stopEvent)
        stopEvent.wait(1.0)
        return True

    def _stepCarveIcon(self, img, origin, stopEvent):
        pos = self._find(img, "carveIcon")
        if not pos:
            return False
        self._clickAt(origin, pos, stopEvent)
        stopEvent.wait(1.0)
        return True

    def _stepCollect(self, img, origin, stopEvent):
        pos = self._find(img, "collectIcon")
        if not pos:
            return False
        if self._clickAt(origin, pos, stopEvent):
            self.count += 1
            self.setLog(f"✅ 完成第 {self.count} 顆")
        stopEvent.wait(1.5)
        return True

    def _stepDialog(self, img, origin, stopEvent):
        x0, y0 = self._pt(DIALOG_ROI[0], DIALOG_ROI[1])
        x1, y1 = self._pt(DIALOG_ROI[2], DIALOG_ROI[3])
        roi = img[max(0, int(y0)) : int(y1), max(0, int(x0)) : int(x1)]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        if float(((hsv[..., 1] < 30) & (hsv[..., 2] > 225)).mean()) < DIALOG_WHITE:
            return False
        self.setLog("點掉提示對話框")
        self._clickAt(origin, self._pt(*DIALOG_CLICK), stopEvent)
        stopEvent.wait(1.0)
        return True

    # ---------- 擺放 ----------
    def _frameState(self, img):
        """擺放框的顏色: "ok"(白，可以放)、"bad"(紅，不能放)、None(沒有框)"""
        s = self._geom[0]
        x0, y0 = self._pt(FRAME_ROI[0], FRAME_ROI[1])
        x1, y1 = self._pt(FRAME_ROI[2], FRAME_ROI[3])
        roi = img[max(0, int(y0)) : int(y1), max(0, int(x0)) : int(x1)].astype(np.int16)
        b, g, r = roi[..., 0], roi[..., 1], roi[..., 2]
        white = int((np.minimum(np.minimum(b, g), r) > 225).sum())
        red = int(((r > 200) & (g < 120) & (b < 150) & (r - g > 110)).sum())
        if white >= WHITE_MIN * s * s:
            return "ok"
        if red >= RED_MIN * s * s:
            return "bad"
        return None

    def _findTable(self, img):
        """雕刻檯在截圖裡的範圍 (x0, y0, x1, y1)：深藍紫色的最大一塊；看不到回傳 None"""
        s = self._geom[0]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        c = TABLE_HSV
        mask = ((hsv[..., 0] >= c["hMin"]) & (hsv[..., 0] <= c["hMax"]) & (hsv[..., 1] >= c["sMin"]) & (hsv[..., 2] >= c["vMin"]) & (hsv[..., 2] <= c["vMax"])).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        best = max(range(1, n), key=lambda i: stats[i][4], default=None)
        if best is None or stats[best][4] < TABLE_MIN_AREA * s * s:
            return None
        x, y, w, h, _ = stats[best]
        return int(x), int(y), int(x + w), int(y + h)

    def _stepPlacing(self, img, origin, stopEvent):
        if not self._find(img, "placeExit"):
            self._placingSince = None
            self._okStreak = 0
            return False
        now = time.time()
        if self._placingSince is None:
            self._placingSince = now
        if now - self._placingSince < PLACE_SETTLE:
            return True

        if self._frameState(img) == "ok":
            self._okStreak += 1
            if self._okStreak >= PLACE_OK_STREAK:
                self._okStreak = 0
                confirm = self._find(img, "placeConfirm")
                if confirm:
                    self._clickAt(origin, confirm, stopEvent)
                else:
                    self.backend.pressKey("enter")
                stopEvent.wait(1.2)
            return True
        self._okStreak = 0

        table = self._findTable(img)
        if table is None:
            self._noTable += 1
            self._trace(f"擺放: 看不到雕刻檯 ({self._noTable})")
            if self._noTable >= PLACE_NO_TABLE_MAX:
                self._placeFail(img, stopEvent, "找不到雕刻檯，已停止")
            return True
        self._noTable = 0
        self._learnGains(table)
        if self._placeTries >= PLACE_MAX_TRIES:
            self._placeFail(img, stopEvent, "轉鏡頭也放不到雕刻檯上，已停止")
            return True

        s = self._geom[0]
        cx0, cy0 = self._pt(*CENTER)
        x0, y0, x1, y1 = table
        needY = (cy0 - TABLE_TOP_ABOVE * s) - y0  # 檯子上緣要往下(+)或往上(-)移多少才對準
        inside = x0 + TABLE_SIDE_MARGIN * s <= cx0 <= x1 - TABLE_SIDE_MARGIN * s
        needX = 0 if inside else cx0 - (x0 + x1) / 2  # ◎ 超出檯子左右範圍時，把檯子中心移到 ◎ 底下
        dx = dy = 0.0
        if abs(needY) > TABLE_TOL * s:
            dy = needY / self._gainY
        if needX:
            if self._gainX is None:
                dx = PLACE_PROBE * s  # 先往右試拖一小段，量檯子往哪邊動
            elif abs(self._gainX) > 0.2:
                dx = needX / self._gainX
        if not dx and not dy:  # 位置看起來對了但沒有白框: 往上輕輕推一點再看
            dy = 30 * s
        limit = PLACE_MAX_DRAG * s
        dx, dy = max(-limit, min(limit, dx)), max(-limit, min(limit, dy))
        self._lastDrag = (y0, (x0 + x1) / 2, dx, dy)
        self._placeTries += 1
        self._trace(f"擺放: 第 {self._placeTries} 次，檯子 {table}，要移 ({needX:.0f}, {needY:.0f})，拖 ({dx:.0f}, {dy:.0f})，增益 ({self._gainX}, {self._gainY:.2f})")
        sx, sy = self._pt(*PLACE_DRAG_START)
        x1s, y1s = self._getRelCords(origin, (sx, sy))
        self.backend.drag(x1s, y1s, x1s + dx, y1s + dy, slow=True)
        stopEvent.wait(PLACE_AFTER_DRAG)
        return True

    def _learnGains(self, table):
        """拖完後檯子實際移動多少，反推「拖 1 格檯子移幾格」，下次拖就更準"""
        if not self._lastDrag:
            return
        top0, cx0, dx, dy = self._lastDrag
        self._lastDrag = None
        s = self._geom[0]
        if abs(dy) >= 30 * s:
            gain = (table[1] - top0) / dy
            if -4 <= gain <= -0.3:
                self._gainY = gain
        if abs(dx) >= 30 * s:
            gain = ((table[0] + table[2]) / 2 - cx0) / dx
            self._gainX = gain if abs(gain) <= 4 else self._gainX

    def _placeFail(self, img, stopEvent, msg):
        self._saveShot(img, "pumpkinPlace")
        self.backend.pressKey("esc")
        self._stop(stopEvent, msg)

    # ---------- 雕刻小遊戲 ----------
    def _curveY(self, img, markerX):
        """灰線在「記號左邊 CURVE_LEAD 那一點」的高度(截圖座標)。那一點沒有灰線就取最接近的有灰線的位置；完全沒有灰線回傳 None"""
        s = self._geom[0]
        x0, x1 = max(0, int(markerX - CURVE_LOOK * s)), max(1, int(markerX - 3 * s))
        y0, y1 = max(0, int(self._pt(0, BOX_Y0)[1])), int(self._pt(0, BOX_Y1)[1])
        roi = img[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat, val = hsv[..., 1].astype(np.int16), hsv[..., 2].astype(np.int16)
        rb = roi[..., 2].astype(np.int16) - roi[..., 0].astype(np.int16)
        c = CURVE_HSV
        mask = ((val > c["vMin"]) & (val < c["vMax"]) & (sat > c["sMin"]) & (sat < c["sMax"]) & (rb > c["rbMin"]) & (rb < c["rbMax"]) & (roi[..., 2] >= roi[..., 1])).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        ys, xs = np.nonzero(mask)
        if xs.size < 6:
            return None
        cols = np.unique(xs)
        center = cols[np.argmin(np.abs(cols - (markerX - CURVE_LEAD * s - x0)))]
        near = np.abs(xs - center) <= CURVE_FIT * s
        if near.sum() >= 8 and np.ptp(xs[near]) >= 6:
            slope, offset = np.polyfit(xs[near], ys[near], 1)
            y = offset + slope * center
        else:
            y = ys[near].mean()
        return float(y0 + y)

    def _stepCarve(self, img, origin, stopEvent):
        if not self._find(img, "carveBadge", roi=BADGE_ROI):
            return False
        self.setLog("雕刻中")
        try:
            self._carveLoop(stopEvent)
        finally:
            self.backend.touchUp()
        return True

    def _markerAt(self, img):
        return self._find(img, "carveMarker", roi=(MARKER_X - 50, BOX_Y0 - 30, MARKER_X + 50, BOX_Y1 + 30))

    def _carveLoop(self, stopEvent):
        """雕刻期間的主迴圈。把手和記號是線性對應(把手動 1 格，記號動 MARKER_PER_HANDLE 格)，
        所以不看「記號現在差多少」去一路累加(記號比把手慢一拍，累加會越拉越過頭、一直抖)，
        而是按住時量一次 (把手, 記號) 當基準，之後灰線在哪，就直接算出把手該在哪。
        回傳時一定要放開滑桿(呼叫端負責)"""
        holding = False
        base = None  # 按住時的 (把手 y, 記號 y)
        handleX = handleY = None
        target = None  # 平滑後的灰線高度
        trim = 0.0
        loop = 0
        lastBadge = lastTarget = lastTrace = time.time()
        marker = None
        while not stopEvent.wait(CARVE_INTERVAL):
            img, origin = self._gameShot()
            s, _, oy = self._geom
            now = time.time()
            loop += 1
            if loop % BADGE_EVERY == 1:
                if self._find(img, "carveBadge", roi=BADGE_ROI):
                    lastBadge = now
                elif now - lastBadge > CARVE_GONE_SECONDS:
                    self._trace("雕刻結束")
                    return
            if loop % MARKER_EVERY == 1:
                marker = self._markerAt(img)
            markerX = marker[0] if marker else self._pt(MARKER_X, 0)[0]
            raw = self._curveY(img, markerX)

            if raw is None:
                if holding and now - lastTarget > RELEASE_SECONDS:
                    self.backend.touchUp()
                    holding, base, target, trim = False, None, None, 0.0
                    self._trace("放開滑桿(灰線消失)")
                continue
            lastTarget = now
            target = raw if target is None else target + TARGET_SMOOTH * (raw - target)

            if not holding:
                handle = self._find(img, "carveHandle", roi=(HANDLE_X - 50, TRACK_TOP - 40, HANDLE_X + 50, TRACK_BOTTOM + 40))
                if not handle:
                    continue
                handleX, handleY = handle
                m = self._markerAt(img)
                if m:
                    markerY = m[1]
                else:  # 找不到記號，用量過的對應關係估
                    markerY = self._pt(0, MARKER_REF[1])[1] + MARKER_PER_HANDLE * (handleY - self._pt(0, MARKER_REF[0])[1])
                base = (handleY, markerY)
                x, y = self._getRelCords(origin, handle)
                self.backend.touchDown(x, y)
                holding = True
                self._trace(f"按住滑桿 把手 {handleY:.0f} 記號 {markerY:.0f} (找到記號: {bool(m)})")
                continue

            if marker and loop % MARKER_EVERY == 1:  # 記號比把手慢，只拿來慢慢修正基準的偏差
                predicted = base[1] + MARKER_PER_HANDLE * (handleY - base[0])
                trim = max(-TRIM_MAX * s, min(TRIM_MAX * s, trim + TRIM_GAIN * (marker[1] - predicted) / MARKER_PER_HANDLE * -1))
            want = base[0] + (target - base[1]) / MARKER_PER_HANDLE + trim
            want = max(self._pt(0, TRACK_TOP)[1], min(self._pt(0, TRACK_BOTTOM)[1], want))
            step = max(-MAX_STEP * s, min(MAX_STEP * s, want - handleY))
            if abs(step) >= 1:
                handleY += step
                x, y = self._getRelCords(origin, (handleX, handleY))
                self.backend.touchMove(x, y)
            if now - lastTrace >= TRACE_EVERY:
                lastTrace = now
                self._trace(f"灰線 {target:.0f} 記號 {marker[1] if marker else None} 把手 {handleY:.0f} 想要 {want:.0f}")
