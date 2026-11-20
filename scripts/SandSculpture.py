import os
import random
import re
from collections import deque
import time
import cv2
import mss
import numpy as np
from scripts.common.autofood import AutoFood
from scripts.common.base import BaseTask
from tools.debug import DebugMixin
from tools.finder import matchBestAuto
from tools import ocr
from tools.tools import getResourcePath, getThreshold

# ===== 骨架版：流程已串好，但座標、顏色範圍、模板圖片都還沒在實機校準。座標與數值一律以 1600x900 為基準，標「待校準」的是從錄影估的 =====
SCAN_INTERVAL = 0.15  # 一般流程每輪看畫面的間隔(秒)
HOVER_DELAY = (0.15, 0.3)  # 移到按鈕上到點下去之間的停頓(秒)
IDLE_OPEN_SECONDS = 2.5  # 認不得任何畫面這麼久，才去開背包
UNKNOWN_STOP_SECONDS = 60  # 認不得畫面超過這麼久就停止
SEARCH_NAME = "沙雕基座"  # 背包沒停在這個搜尋結果時，自動搜尋並按「保持結果」
SEARCH_INPUT_OFFSET = -385  # 搜尋輸入框在搜尋視窗「搜尋」按鈕左邊多遠(和食物搜尋同一個視窗)
SEARCH_WAIT = 4  # 等搜尋視窗出現的上限(秒)
KEEP_WAIT_SECONDS = 8  # 搜尋後最多等幾秒讓結果畫面的「保持結果」開關出現
BAG_MAX_TRIES = 3  # 背包停在沙雕基座結果卻一直沒出現「擺放」鈕，幾次後判定沒基座了
FIRST_SLOT = (962, 230)  # 背包搜尋結果第一格的中心(待校準，沿用南瓜雕刻)
PLACE_MAX_TRIES = 12  # 擺放模式裡一直找不到可按的綠色勾勾，幾輪後判定放不下
PLACE_SETTLE = 0.8  # 剛進擺放模式要等多久才找勾勾

# --- 擺放: 預覽框(白色四邊形)跟著畫面中央 ◎ 走，◎ 落在地上的位置離人物多遠要看視角；放太遠就沒有互動圖示，所以放之前先往下拖鏡頭，讓預覽框靠近人物 ---
FRAME_ROI = (150, 250, 1450, 650)  # 預覽框會出現的範圍(1600x900 基準)；靠近人物時框很寬，要涵蓋到左右兩邊
FRAME_CENTER = (800, 450)  # 預覽框的中心就是畫面正中央的 ◎，用它當基準量框的寬度
FRAME_DOT = 25  # 中心 ◎ 的半徑，量寬度時左右各跳過這麼遠
FRAME_ROWS = 12  # 在中心那一列上下各取幾列量寬度，取中位數
FRAME_BRIGHT = 235  # 預覽框的線是接近純白(三個色版都超過這個值)；格線沒這麼亮，錄影實測可以分開
FRAME_OK_MIN = 600  # 範圍內純白像素超過這個數 = 白框(可以放)；錄影實測白框約 1200~1340，紅框不到 300
FRAME_RED_MIN = 30  # 範圍內紅色像素超過這個數 = 紅框(不能放，例如被已經放好的基座擋住)
PLACE_BAD_SECONDS = 1.5  # 紅框持續這麼久就取消擺放，當作畫面上已經有基座
PLACE_NEAR_WIDTH = 580  # 預覽框在畫面正中央那一列的寬度(像素)到這個值就停止往下拖；對照舊的外接矩形寬度：385、500、580 都太遠(放下後沒有互動圖示)，約 705 貼到人物身上，換算成中心列寬約 340、440、520 太遠，約 685 太近，取 580(待校準)
PLACE_NEAR_TOL = 0.95  # 框寬到目標的這個比例就算夠近，不要為了差幾像素一直拖(實測 564 對 580 就是夠近)
PLACE_DRAG_START = (1100, 300)  # 往下拖鏡頭的起點: 右邊中間空白處(待校準，不能壓到 UI)
PLACE_DRAG_STEP = 100  # 第一次往下拖多遠(1600 基準)；往下拖 = 往下看，預覽框就更靠近人物。之後用量到的「拖 1px 框寬多幾 px」算要拖多遠，一步到位
PLACE_STEP_MIN, PLACE_STEP_MAX = 20, 160  # 之後每次拖的距離範圍
PLACE_MAX_DRAGS = 10  # 最多拖幾次，之後不管夠不夠近都放
PLACE_AFTER_DRAG = 0.3  # 拖完等多久再量
PLACE_BLOCKED_STEP = 60  # 預覽框變紅(被牆、做好的沙雕擋住，視角變了也會)時，再往下拖多遠，把框往人物這邊靠
PLACE_MAX_BLOCKED = 6  # 紅框時最多往人物靠幾次，還是紅框就取消

# --- 已經有基座就不再放置: 用沙面的顏色(淺灰紫、飽和度低)判斷，錄影實測有基座約 8000~17000，沒有基座是 0 ---
BASE_SAND_HSV = {"hMin": 134, "hMax": 146, "sMin": 8, "sMax": 36, "vMin": 135, "vMax": 205}
BASE_SAND_MIN = 4000  # 沙面連通面積下限(1600x900 基準)；未雕刻的沙堆約 1700，不會誤判
BASE_WAIT_SECONDS = 20  # 畫面上有基座卻一直沒出現互動圖示，超過這麼久就停止

# --- 小遊戲: 彩虹弧形，指針左右擺，指針進到中間黃色區塊時點一下(要點滿 10 次完美) ---
COMBO_ROI = (450, 0, 1150, 120)  # 上方 COMBO 條的位置(待校準)；看得到它就是在小遊戲裡
METER_GONE_SECONDS = 2.0  # 看不到 COMBO 條超過這麼久，就當小遊戲結束
COMBO_EVERY = 10  # 每幾輪才確認一次 COMBO 條(模板比對比較慢)
METER_TAP = (1375, 685)  # 點擊的位置: 小遊戲時右下角的沙堆按鈕(錄影量到中心約 (1375, 685))
METER_Y_MIN = 150  # 弧形不會出現在這條線以上，用來排除上方黃色的 COMBO 條
BAND_HSV = {"hMin": 20, "hMax": 30, "sMin": 140, "vMin": 190}  # 中間黃色區塊；錄影量到黃色 H25 S174 V251，綠色 H48，藍色 H109
BAND_MIN_AREA = 150  # 黃色區塊面積下限(1600x900 基準，待校準)
BAND_MAX_H = 45  # 黃色區塊的高度上限(1600 基準)；錄影量到約 28
BAND_MIN_FILL = 0.5  # 黃色區塊填滿外接矩形的比例下限
NEEDLE_MIN_SHARE = 0.6  # 最大的橘色連通塊要佔範圍內所有橘色像素的這個比例以上
NEEDLE_MIN_LEN = 45  # 指針至少這麼長(外接矩形的長邊，1600 基準)；錄影量到約 100
BAND_GAP_FILL = 21  # 指針的寬度(像素，1600 基準)再多一點；黃色區塊被指針切開時，橫向補起來這麼寬
NEEDLE_HSV = {"hMin": 4, "hMax": 17, "sMin": 170, "vMin": 190}  # 橘色指針；錄影量到 H8 S194 V234
NEEDLE_HALF_SPAN = 140  # 以黃色區塊中心往左右找指針多遠(待校準，要涵蓋整個弧形)
NEEDLE_ABOVE, NEEDLE_BELOW = 90, 110  # 往黃色區塊上、下找指針多遠(待校準)
BAND_MARGIN = 3  # 預測的指針位置要進到黃色區塊內縮這麼多才點
METER_LEAD_MIN, METER_LEAD_MAX = 0.02, 0.3  # 提前量會依實際落點自己修正，範圍在這之間
VEL_SAMPLES = 6  # 用最近幾張畫面的位置擬合速度，量夠 3 張才開始點(單張的速度被像素取整影響，會抖)
METER_LEAD = 0.12  # 點擊到遊戲反應的延遲(秒)起始值，指針要提前這麼久按；每點一下依實際量到的修正(紀錄量到約 0.12~0.25)
METER_MIN_SPEED = 60  # 指針速度(像素/秒)低於這個就不點(兩端折返的地方)
METER_MIN_GAP = 0.25  # 兩次點擊最少間隔(秒)
METER_INTERVAL = 0.005  # 完整模式(找黃色區塊)每輪的間隔；快速模式只截指針那一小塊，不等

# --- 題目: 底座形狀的問句 + 右邊 3 個選項，選對才做得出來，不然就是失誤沙雕 ---
OPTION_SLOTS = ((1202, 455), (1202, 539), (1202, 623))  # 3 個選項泡泡的中心(從繁中錄影量的，泡泡約寬 270、高 76)；認不得任何選項時隨機點一個
QUESTION_ROI = (540, 760, 1060, 802)  # 題目框第一行字「這個 ○○形沙堆 能做成什麼樣呢？」的位置(從繁中錄影量的)，OCR 讀出來才知道是哪種底座形狀
OPTION_COLUMN_X = (1060, 1345)  # 3 個泡泡整塊一起讀時取的左右範圍
OPTION_HALF = (127, 34)  # 讀文字時，以泡泡中心往左右、上下取的一半寬高

# 提示對話框(例如「好耶！第一次成功做出了青蛙沙雕！」)出現時整個介面被藏起來，點一下才會回普通畫面；範圍與判斷沿用南瓜雕刻
DIALOG_ROI = (400, 735, 1200, 850)
DIALOG_WHITE = 0.6
DIALOG_CLICK = (800, 790)

# 可選的沙雕: key -> (顯示名稱, 選項文字裡的關鍵字)。用 Windows OCR 讀選項泡泡的繁中字，文字裡有任一關鍵字就算是這座
# (實測繁中選項寫「蹲坐的青蛙」，所以只比關鍵字)。只有「蛙」是從錄影確認過的，其他是從簡中對照表推的，遇到沒對上的要補關鍵字
# 繁中圖鑑的沙雕名稱：汽車、青蛙、小黃鴨、兔子、燈塔、寄居蟹沙雕。不含肌肉貓(彩虹)、仙人掌(下雨/彩虹)
SAND_TARGETS = {
    "frog": ("青蛙", ("蛙",)),
    "rabbit": ("兔子", ("兔",)),  # 矮圓柱底座
    "lighthouse": ("燈塔", ("燈塔",)),  # 矮圓柱底座，18~6 點才做得出來
    "duck": ("小黃鴨", ("鴨",)),
    "car": ("汽車", ("汽車",)),
    "crab": ("寄居蟹", ("蟹",)),  # 12~24 點才做得出來；簡中叫螃蟹
    "boat": ("輪船", ("輪船",)),  # 不能只比「船」，干擾選項可能有「帆船」
    "bear": ("小熊", ("小熊",)),
    "whale": ("鯨魚", ("鯨",)),
    "gull": ("海鷗", ("鷗",)),
    "polar": ("北極熊", ("北極熊",)),
    "moai": ("摩艾巨像", ("摩艾", "巨像", "石像")),
}

# 底座形狀 -> 這種形狀才做得出來的沙雕。干擾選項常常是別種形狀的沙雕(例如矮圓柱的題目放了「汽車」)，選了就是失誤沙雕，所以一定要對形狀
SHAPE_TARGETS = {
    "flatTrapezoid": ("frog", "duck", "car", "crab"),  # 扁扁的梯台形
    "shortCylinder": ("rabbit", "lighthouse"),  # 矮矮的圓柱形
    "flatPrism": ("boat", "bear"),  # 扁扁的棱柱形
    "tallTrapezoid": ("whale", "gull"),  # 高大的梯台形
    "tallCylinder": ("polar", "moai"),  # 高高的圓柱形
}

TEMPLATES = ("sandBagTitle", "sandInteract", "sandCollect", "sandCombo", "sandQuiz")
PUMPKIN_DIR = "templates/pumpkin"  # 擺放鈕、擺放中的退出鈕與確認鈕和南瓜雕刻是同一組介面，直接借用
COMMON_DIR = "templates/common"
BAG_BTN_TEMPLATE = f"{COMMON_DIR}/bagBtn.png"


class SandSculpture(DebugMixin, BaseTask):
    """自動做沙雕: 開背包 → 點第一格沙雕基座 → 擺放 → 點互動圖示 → 小遊戲(指針進黃色區塊就點)→ 選題目的正確選項 → 重來。
    基座用完就會消失，所以每做一座都要重新擺一個；背包要先搜尋「沙雕基座」並按「保持結果」。只支援前景模式"""

    templateDir = "templates/sandSculpture"

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self.count = 0  # 已做幾座
        self._lead = METER_LEAD  # 預測指針位置的提前秒數，小遊戲每點一下依實際落點修正
        self._unknownSince = None
        self._lastUnknownLog = 0
        self._bagTries = 0
        self._placingSince = None
        self._placeTries = 0
        self._drags = 0
        self._lastWidth = 0
        self._noGain = 0
        self._badSince = None
        self._blocked = 0
        self._gain = None
        self._lastDrag = 0.0
        self._redCancelAt = 0.0
        self._lastBaseLog = 0.0
        self._geom = (1.0, 0, 0)  # (遊戲畫面縮放, 遊戲左上角在截圖裡的 x, y)
        self.food = AutoFood(setLog, backend, config, "sand", "沙雕")

    def _threadTargets(self):
        return [self._loop]

    # ---------- 共用(和南瓜雕刻同一套) ----------
    def _gameShot(self):
        """前景模式截到的是 RGB，統一轉成 BGR(模板是真實顏色，色相判斷也要)，並記下遊戲畫面的縮放與位置"""
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

    def _find(self, img, name, roi=None, path=None, threshold=None):
        """找模板，回傳中心座標(截圖座標)或 None。roi 是 1600x900 基準的 (x0, y0, x1, y1)"""
        x0 = y0 = 0
        if roi:
            ax, ay = self._pt(roi[0], roi[1])
            bx, by = self._pt(roi[2], roi[3])
            x0, y0 = max(0, int(ax)), max(0, int(ay))
            img = img[y0 : max(y0, int(by)), x0 : max(x0, int(bx))]
        pos = matchBestAuto(self.backend, self.config, img, path or f"{self.templateDir}/{name}.png", threshold or getThreshold(self.config, name))
        return (pos[0] + x0, pos[1] + y0) if pos else None

    def _clickAt(self, origin, pos, stopEvent):
        x, y = self._getRelCords(origin, pos)
        x, y = round(x + random.randint(-2, 2)), round(y + random.randint(-2, 2))
        self.backend.moveTo(x, y)
        if stopEvent.wait(random.uniform(*HOVER_DELAY)):
            return False
        self.backend.click(x, y)
        return True

    def _findC(self, img, name):
        """找 templates/common 裡的共用按鈕"""
        return self._find(img, name, path=f"{COMMON_DIR}/{name}.png")

    def _stop(self, stopEvent, msg):
        self.setLog(msg)
        stopEvent.set()

    # ---------- 主流程 ----------
    def _loop(self, stopEvent):
        if getattr(self.backend, "isAdb", False):
            raise RuntimeError("沙雕只支援前景模式")
        files = [(f"{self.templateDir}/{n}.png", n) for n in TEMPLATES]
        files += [(f"{PUMPKIN_DIR}/{n}.png", n) for n in ("placeBtn", "placeExit", "placeConfirm")]
        missing = [n for path, n in files if not os.path.exists(getResourcePath(path))]
        if missing:
            raise RuntimeError("缺少模板圖片：" + "、".join(missing))
        if not ocr.isReady():
            raise RuntimeError("沙雕要用 Windows 的文字辨識讀題目選項，請在 Windows 設定安裝「繁體中文」語言(含 OCR 功能)")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        self._startWatchdog(stopEvent)
        self._trace("沙雕開始", reset=True)
        self.food.start()

        steps = (
            ("小遊戲", self._stepMeter),
            ("題目", self._stepQuiz),  # 題目的白框和提示對話框很像，一定要排在對話框前面
            ("擺放", self._stepPlacing),
            ("擺放鈕", self._stepPlaceBtn),
            ("背包", self._stepBag),
            ("背包搜尋", self._stepBagSetup),
            ("互動", self._stepInteract),
            ("收起", self._stepCollect),
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
        """認不得任何畫面: 可能在主畫面(剛做完或基座用完)，等一下還是這樣就開背包"""
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
        if self._baseVisible(img) or now - self._redCancelAt < BASE_WAIT_SECONDS:
            if waited > BASE_WAIT_SECONDS:
                self._stop(stopEvent, "畫面上已經有基座，但一直沒出現互動圖示，請靠近基座後再開始，已停止")
            elif now - self._lastBaseLog > 10:
                self._lastBaseLog = now
                self.setLog("畫面上已經有基座，等互動圖示出現")
                self._saveShot(img, "sandWait")
            return
        bag = self._find(img, "bagBtn", path=BAG_BTN_TEMPLATE)
        if bag:
            self._clickAt(origin, bag, stopEvent)
            stopEvent.wait(1.0)
            return
        if now - self._lastUnknownLog > 10:
            self._lastUnknownLog = now
            self.setLog("找不到背包鈕，請回到遊戲主畫面")
            self._saveShot(img, "sandUnknown")

    def _stepBag(self, img, origin, stopEvent):
        if not self._find(img, "sandBagTitle"):
            return False
        keep = self._findC(img, "keepOff")  # 搜尋結果還沒鎖定: 先鎖，下次開背包才會直接停在這裡
        if keep:
            self._clickAt(origin, keep, stopEvent)
            self.setLog(f"✅ 已鎖定搜尋結果：{SEARCH_NAME}")
            stopEvent.wait(0.5)
            return True
        self._bagTries += 1
        if self._bagTries > BAG_MAX_TRIES:
            self._stop(stopEvent, "背包裡找不到可以擺放的沙雕基座，已停止")
            return True
        self._clickAt(origin, self._pt(*FIRST_SLOT), stopEvent)
        stopEvent.wait(0.8)
        return True

    def _stepBagSetup(self, img, origin, stopEvent):
        """背包開著，但不是停在沙雕基座的搜尋結果: 在別的結果就先按「結束搜尋」回完整清單，再搜尋並鎖定"""
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
        self._searchBase(origin, search, stopEvent)
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

    def _searchBase(self, origin, searchPos, stopEvent):
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
        pos = self._find(img, "placeBtn", path=f"{PUMPKIN_DIR}/placeBtn.png")
        if not pos:
            return False
        self._bagTries = 0
        self._resetPlace()
        self._clickAt(origin, pos, stopEvent)
        stopEvent.wait(1.0)
        return True

    def _resetPlace(self):
        self._placeTries = 0
        self._placingSince = None
        self._drags = 0
        self._lastWidth = 0
        self._noGain = 0
        self._badSince = None
        self._blocked = 0
        self._gain = None  # 往下拖 1px，預覽框寬度多幾 px(第一次拖完才量得到)
        self._lastDrag = 0.0

    def _dragCamera(self, origin, dy, stopEvent):
        """在空白處往下(dy 為正)拖鏡頭；不用慢速拖，視角轉動不需要長按，慢速拖一次要 1 秒多"""
        x, y = self._getRelCords(origin, self._pt(*PLACE_DRAG_START))
        self.backend.drag(x, y, x, y + dy)
        stopEvent.wait(PLACE_AFTER_DRAG)

    def _frameInfo(self, img):
        """擺放預覽框: 回傳 (狀態, 框的寬度)。狀態 "ok"(白框)、"bad"(紅框)、None(沒框)；寬度是白框外接矩形的寬(截圖座標)，沒有白框是 0"""
        x0, y0 = self._pt(FRAME_ROI[0], FRAME_ROI[1])
        x1, y1 = self._pt(FRAME_ROI[2], FRAME_ROI[3])
        roi = img[max(0, int(y0)) : int(y1), max(0, int(x0)) : int(x1)]
        if roi.size == 0:
            return None, 0
        s = self._geom[0]
        white = (roi.min(axis=2) > FRAME_BRIGHT).astype(np.uint8)
        if int(white.sum()) >= FRAME_OK_MIN * s * s:
            # 以畫面正中央的 ◎ 為準，往左、往右找最近的純白線，就是框在中心那一列的左右邊；角色身上的白色衣物、遠處的白色東西不會混進來
            cx, cy = (FRAME_CENTER[0] - FRAME_ROI[0]) * s, (FRAME_CENTER[1] - FRAME_ROI[1]) * s
            dot, widths = int(FRAME_DOT * s), []
            for y in range(max(0, int(cy - FRAME_ROWS * s)), min(white.shape[0], int(cy + FRAME_ROWS * s) + 1)):
                left = np.nonzero(white[y, : max(0, int(cx) - dot)])[0]
                right = np.nonzero(white[y, int(cx) + dot :])[0]
                if len(left) and len(right):
                    widths.append(int(cx) + dot + right[0] - left[-1])
            return "ok", int(np.median(widths)) if widths else 0
        b, g, r = (roi[..., i].astype(np.int16) for i in range(3))
        if int(((r > 200) & (g < 120) & (b < 150) & (r - g > 110)).sum()) >= FRAME_RED_MIN * s * s:
            return "bad", 0
        return None, 0

    def _baseVisible(self, img):
        """畫面上有沒有已經放好的沙雕基座: 找淺灰紫色的沙面(一大塊)"""
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        c = BASE_SAND_HSV
        mask = ((hsv[..., 0] >= c["hMin"]) & (hsv[..., 0] <= c["hMax"]) & (hsv[..., 1] >= c["sMin"]) & (hsv[..., 1] <= c["sMax"]) & (hsv[..., 2] >= c["vMin"]) & (hsv[..., 2] <= c["vMax"])).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        s = self._geom[0]
        return any(stats[i][4] >= BASE_SAND_MIN * s * s for i in range(1, n))

    def _stepPlacing(self, img, origin, stopEvent):
        """擺放模式: 白框先往下拖鏡頭讓它靠近人物，夠近(或拖滿次數)才按綠色勾勾；紅框持續一下就取消，當作畫面上已經有基座"""
        if not self._find(img, "placeExit", path=f"{PUMPKIN_DIR}/placeExit.png"):
            self._resetPlace()
            return False
        now = time.time()
        if self._placingSince is None:
            self._placingSince = now
        if now - self._placingSince < PLACE_SETTLE:
            return True
        state, width = self._frameInfo(img)
        s = self._geom[0]
        if state == "bad" and self._blocked < PLACE_MAX_BLOCKED:
            # 紅框 = 位置被擋住: 往人物這邊靠(再往下拖)，變回白框就放
            self._blocked += 1
            self._trace(f"擺放: 紅框，第 {self._blocked} 次往人物靠")
            self._dragCamera(origin, PLACE_BLOCKED_STEP * s, stopEvent)
            return True
        if state == "bad":
            if self._badSince is None:
                self._badSince = now
            elif now - self._badSince >= PLACE_BAD_SECONDS:
                self._saveShot(img, "sandPlaceRed")
                self.backend.pressKey("esc")
                self._redCancelAt = now
                self._resetPlace()
                self.setLog("放置框是紅色（被擋住），畫面上可能已經有基座，取消擺放")
                stopEvent.wait(1.0)
            return True
        self._badSince = None
        if state == "ok":
            if width < PLACE_NEAR_WIDTH * PLACE_NEAR_TOL * s and self._drags < PLACE_MAX_DRAGS and self._noGain < 2:
                if self._drags:
                    self._noGain = 2 if width <= self._lastWidth + 8 * s else 0  # 拖了框也不再變大(鏡頭到底了)，就不再拖
                    if self._lastDrag and width > self._lastWidth:
                        self._gain = (width - self._lastWidth) / self._lastDrag
                if self._gain:
                    dy = max(PLACE_STEP_MIN * s, min(PLACE_STEP_MAX * s, (PLACE_NEAR_WIDTH * s - width) / self._gain))
                else:
                    dy = PLACE_DRAG_STEP * s
                self._lastWidth = width
                self._lastDrag = dy
                self._drags += 1
                self._trace(f"擺放: 預覽框寬 {width} 不夠靠近人物，第 {self._drags} 次往下拖 {dy:.0f}")
                self._dragCamera(origin, dy, stopEvent)
                return True
            confirm = self._find(img, "placeConfirm", path=f"{PUMPKIN_DIR}/placeConfirm.png")
            if confirm:
                self._clickAt(origin, confirm, stopEvent)
            else:
                self.backend.pressKey("enter")
            stopEvent.wait(1.2)
            return True
        # 沒有網格和預覽框(視角太平、看不到地面): 往下挪鏡頭，讓網格和框出現
        self._placeTries += 1
        if self._placeTries >= PLACE_MAX_TRIES:
            self._saveShot(img, "sandPlace")
            self.backend.pressKey("esc")
            self._stop(stopEvent, "這個視角放不下沙雕基座，請換個空曠的地方，已停止")
            return True
        self._trace(f"擺放: 沒有預覽框，第 {self._placeTries} 次往下挪鏡頭")
        self._dragCamera(origin, PLACE_BLOCKED_STEP * self._geom[0], stopEvent)
        return True

    def _stepInteract(self, img, origin, stopEvent):
        pos = self._find(img, "sandInteract")
        if not pos:
            return False
        self._clickAt(origin, pos, stopEvent)
        stopEvent.wait(1.0)
        return True

    def _stepCollect(self, img, origin, stopEvent):
        """做好的沙雕會留在地上，要點它上面的收起圖示收掉，基座和沙雕才會都清掉，不然下一次擺放會被擋住(紅框)"""
        pos = self._find(img, "sandCollect")
        if not pos:
            return False
        self._clickAt(origin, pos, stopEvent)
        self.setLog("收起沙雕")
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

    # ---------- 題目 ----------
    def _readOptions(self, img):
        """讀 3 個選項泡泡的文字: [(各次 OCR 讀到的字串, 泡泡中心的截圖座標), ...]。
        每個泡泡單獨讀；單獨讀不出來的(兩個字的選項，例如「燈塔」)才改用 3 個泡泡整塊一起讀、依行的高度分給各泡泡"""
        s = self._geom[0]
        colTop = OPTION_SLOTS[0][1] - OPTION_HALF[1] - 10
        x0, y0 = self._pt(OPTION_COLUMN_X[0], colTop)
        x1, y1 = self._pt(OPTION_COLUMN_X[1], OPTION_SLOTS[-1][1] + OPTION_HALF[1] + 10)
        top = max(0, int(y0))
        lines = ocr.readLines(img[top : int(y1), max(0, int(x0)) : int(x1)], scales=(1,))  # 放大讀整塊會把相鄰幾行的字攪在一起(實測「旗手鴨」)，只用原尺寸
        options = []
        for slot in OPTION_SLOTS:
            sx0, sy0 = self._pt(slot[0] - OPTION_HALF[0], slot[1] - OPTION_HALF[1])
            sx1, sy1 = self._pt(slot[0] + OPTION_HALF[0], slot[1] + OPTION_HALF[1])
            crop = img[max(0, int(sy0)) : int(sy1), max(0, int(sx0)) : int(sx1)]
            texts = ocr.readText(crop)
            centerY = self._pt(0, slot[1])[1] - top
            if not texts:  # 單獨讀不出來(兩個字的選項)才用整塊讀的結果；單獨讀得出來的以它為準，整塊讀偶爾會把相鄰選項的字混進來
                texts = [text for text, cy in lines if abs(cy - centerY) <= OPTION_HALF[1] * s]
            options.append((texts, self._pt(*slot)))
        return options

    def _readShape(self, img):
        """題目框第一行「這個 ○○沙堆 能做成什麼樣呢？」裡的底座形狀(例如「扁扁的梯台形」)，讀不出來回傳 None"""
        x0, y0 = self._pt(QUESTION_ROI[0], QUESTION_ROI[1])
        x1, y1 = self._pt(QUESTION_ROI[2], QUESTION_ROI[3])
        crop = img[max(0, int(y0)) : int(y1), max(0, int(x0)) : int(x1)]
        for text in ocr.readText(crop):
            match = re.search(r"這個(.+?)沙堆", text)
            if match:
                return match.group(1)
        return None

    def _shapeKey(self, shape):
        """題目裡的底座形狀文字(例如「扁扁的梯台形」)對應到 SHAPE_TARGETS 的 key，認不得回傳 None"""
        if not shape:
            return None
        if "矮" in shape:
            return "shortCylinder"
        if "高大" in shape or ("高" in shape and "梯" in shape):
            return "tallTrapezoid"
        if "高" in shape and "圓" in shape:
            return "tallCylinder"
        if "梯" in shape:
            return "flatTrapezoid"
        if "棱" in shape or "稜" in shape or ("扁" in shape and "柱" in shape):
            return "flatPrism"
        return None

    def _keyOf(self, texts):
        """選項文字對應哪一座沙雕(SAND_TARGETS 的 key)，認不得回傳 None"""
        texts = [text.replace("熊貓", "") for text in texts]  # 「小熊」不能誤認成干擾選項「小熊貓」
        for key, (_, words) in SAND_TARGETS.items():
            if any(word in text for text in texts for word in words):
                return key
        return None

    def _stepQuiz(self, img, origin, stopEvent):
        if not self._find(img, "sandQuiz"):
            return False
        shape = self._readShape(img)
        self.setLog(f"底座形狀：{shape or '認不得'}")
        options = self._readOptions(img)
        keys = [self._keyOf(texts) for texts, _ in options]
        self._trace(f"題目選項: {[texts for texts, _ in options]} -> {keys}")
        shapeKey = self._shapeKey(shape)
        valid = SHAPE_TARGETS.get(shapeKey)
        if valid:  # 只選這種形狀做得出來的；別種形狀的沙雕(干擾選項)選了就是失誤沙雕
            candidates = [i for i, key in enumerate(keys) if key in valid]
        else:
            candidates = [i for i, key in enumerate(keys) if key]
        if candidates:
            index = candidates[0]
        else:
            # 沒有認得的正確選項: 優先選沒讀出字的(可能就是正確的，例如兩個字的短選項)，不要去選已知的干擾選項
            unread = [i for i, (texts, _) in enumerate(options) if not texts]
            index = random.choice(unread or range(len(options)))
            self._saveShot(img, "sandQuiz")
            self.setLog("⚠️ 認不得正確的選項，隨機選一個：" + "、".join(t[0] if t else "?" for t, _ in options))
            self._clickAt(origin, options[index][1], stopEvent)
            stopEvent.wait(1.5)
            return True
        self._clickAt(origin, options[index][1], stopEvent)
        self.count += 1
        self.setLog(f"✅ 第 {self.count} 座：{SAND_TARGETS[keys[index]][0]}")
        stopEvent.wait(1.5)
        return True

    # ---------- 小遊戲 ----------
    def _findBand(self, img):
        """找弧形中間的黃色區塊: 回傳 (左, 上, 寬, 高)，截圖座標；找不到或形狀不對回傳 None"""
        s = self._geom[0]
        yMin = max(0, int(self._pt(0, METER_Y_MIN)[1]))
        hsv = cv2.cvtColor(img[yMin:], cv2.COLOR_BGR2HSV)
        c = BAND_HSV
        mask = ((hsv[..., 0] >= c["hMin"]) & (hsv[..., 0] <= c["hMax"]) & (hsv[..., 1] >= c["sMin"]) & (hsv[..., 2] >= c["vMin"])).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # 橘色指針劃過黃色區塊時會把它切成左右兩半(實測量到的區塊左右緣會跟著指針跑，條件永遠不成立)，所以左右先橫向補起來再找
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, max(3, int(BAND_GAP_FILL * s)) | 1), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        best = max(range(1, n), key=lambda i: stats[i][4], default=None)
        if best is None or stats[best][4] < BAND_MIN_AREA * s * s:
            return None
        bx, by, bw, bh, ba = stats[best]
        # 弧形上的黃色區塊是一條實心的短帶(錄影量到約 92x28、填滿 6~7 成)；右上角的圖示、黃花、擺放框周圍的雜色不是這個形狀，不能算小遊戲
        if bh > BAND_MAX_H * s or ba < BAND_MIN_FILL * bw * bh:
            return None
        return float(bx), float(by + yMin), float(bw), float(bh)

    def _needleBox(self, img, band):
        """找指針的範圍 (x0, y0, x1, y1)，截圖座標，已限制在畫面內；黃色區塊固定在弧形正中間，位置不會變，所以抓到一次就能一直只截這一小塊"""
        s = self._geom[0]
        bx, by, bw, bh = band
        cx = bx + bw / 2
        x0, x1 = max(0, int(cx - NEEDLE_HALF_SPAN * s)), min(img.shape[1], int(cx + NEEDLE_HALF_SPAN * s))
        y0, y1 = max(0, int(by - NEEDLE_ABOVE * s)), min(img.shape[0], int(by + bh + NEEDLE_BELOW * s))
        return x0, y0, x1, y1

    def _needleX(self, roi, band, x0, y0):
        """在 roi(左上角在截圖的 (x0, y0))裡找橘色指針，回傳指針延伸線穿過黃色區塊中間那條水平線的 x(截圖座標)；找不到或形狀不對回傳 None。
        用整根指針擬合直線再延伸，比只取尖端準: 指針斜的時候，尖端和區塊中心不在同一條水平線上，差幾十像素"""
        s = self._geom[0]
        if roi.size == 0:
            return None
        rhsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        c = NEEDLE_HSV
        nmask = ((rhsv[..., 0] >= c["hMin"]) & (rhsv[..., 0] <= c["hMax"]) & (rhsv[..., 1] >= c["sMin"]) & (rhsv[..., 2] >= c["vMin"])).astype(np.uint8)
        # 指針是一整根細長的橘色連通塊；零散的橘色(圖示、地圖標記)不算
        n2, labels, stats2, _ = cv2.connectedComponentsWithStats(nmask)
        needle = max(range(1, n2), key=lambda i: stats2[i][4], default=None)
        if needle is None or stats2[needle][4] < 60 * s * s or stats2[needle][4] < NEEDLE_MIN_SHARE * int(nmask.sum()):
            return None
        if max(stats2[needle][2], stats2[needle][3]) < NEEDLE_MIN_LEN * s:
            return None
        ys, xs = np.nonzero(labels == needle)
        if xs.size < 8:
            return None
        yc = band[1] + band[3] / 2 - y0
        vx, vy, px, py = cv2.fitLine(np.column_stack([xs, ys]).astype(np.float32), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        if abs(vy) < 0.3:  # 幾乎是橫的，延伸不準，直接用中心
            return float(xs.mean() + x0)
        return float(px + vx * (yc - py) / vy + x0)

    def _readMeter(self, img):
        """讀弧形與指針: 回傳 (黃色區塊左緣, 右緣, 指針 x, 黃色區塊)，都是截圖座標；看不到黃色區塊或指針回傳 None"""
        band = self._findBand(img)
        if band is None:
            return None
        x0, y0, x1, y1 = self._needleBox(img, band)
        x = self._needleX(img[y0:y1, x0:x1], band, x0, y0)
        if x is None:
            return None
        return band[0], band[0] + band[2], x, band

    def _stepMeter(self, img, origin, stopEvent):
        # 看到 COMBO 條，或直接看到弧形的黃色區塊加橘色指針，都算小遊戲開始(COMBO 條沒認出來時不能整段不動)
        if not self._find(img, "sandCombo", roi=COMBO_ROI) and self._readMeter(img) is None:
            return False
        self._meterLoop(stopEvent)
        return True

    def _meterLoop(self, stopEvent):
        """小遊戲主迴圈。找到黃色區塊之後只截指針那一小塊(畫面小、處理快，一秒能看上百張)，每張量指針位置，
        用最近幾張的位置擬合出速度，算出「指針什麼時候走到區塊正中間」，扣掉點擊到遊戲反應的延遲，到那個時間點才按下去(不是等下一張畫面)。
        延遲每點一下就用「點下去到弧形消失」的時間修正一次"""
        s = self._geom[0]
        samples = deque(maxlen=VEL_SAMPLES)  # (時間, 指針 x)
        armed = True
        clicks = loop = 0
        lastClick = -1e9
        pending = None  # 剛點下去的時間，等弧形消失來量延遲
        lastSeen = None  # 最後一次看到指針的時間
        framePeriod = 0.02
        prevT = None
        band = box = monitor = tapPt = None
        left = right = 0.0
        lastCombo = lastMeter = time.perf_counter()
        with mss.mss() as sct:
            while not stopEvent.is_set():
                x = None
                if band is not None:  # 快速模式: 只截指針那一小塊
                    shot = sct.grab(monitor)
                    t = time.perf_counter()
                    x = self._needleX(cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR), band, box[0], box[1])
                    if x is None and t - lastMeter > 0.6:  # 弧形消失後換新的一輪，位置不變，繼續只截這一小塊等指針；太久沒看到才回完整模式
                        band = None
                if band is None:  # 完整模式: 截整個遊戲畫面找黃色區塊
                    if stopEvent.wait(METER_INTERVAL):
                        return
                    img, origin = self._gameShot()
                    t = time.perf_counter()
                    loop += 1
                    if loop % COMBO_EVERY == 1:
                        if self._find(img, "sandCombo", roi=COMBO_ROI):
                            lastCombo = t
                        elif t - max(lastCombo, lastMeter) > METER_GONE_SECONDS:  # COMBO 條和弧形都看不到這麼久，才算結束
                            self._trace("小遊戲結束")
                            return
                    read = self._readMeter(img)
                    if read:
                        left, right, x, band = read
                        box = self._needleBox(img, band)
                        monitor = {"left": origin[0] + box[0], "top": origin[1] + box[1], "width": box[2] - box[0], "height": box[3] - box[1]}
                        tx, ty = self._getRelCords(origin, self._pt(*METER_TAP))
                        tapPt = (round(tx), round(ty))
                        self.backend.prepareTap(*tapPt)
                        samples.clear()
                        prevT = None
                    else:
                        band = None
                if x is None:  # 每點一下弧形會消失一下再換新的，新的一輪要重新量速度
                    if pending is not None and lastSeen is not None:
                        # 弧形消失的時間落在「最後看到指針」和「第一次看不到」之間，取中間；從點下去算起就是點擊到畫面反應的延遲
                        latency = (max(lastSeen, pending) + t) / 2 - pending
                        if 0.0 < latency < 0.4:
                            self._lead = max(METER_LEAD_MIN, min(METER_LEAD_MAX, 0.6 * self._lead + 0.4 * latency))
                            self._trace(f"落點: 點下去到弧形消失 {latency * 1000:.0f}ms，提前量改為 {self._lead * 1000:.0f}ms")
                    pending = None
                    armed = True
                    samples.clear()
                    prevT = None
                    continue
                lastMeter = lastSeen = t
                framePeriod = 0.8 * framePeriod + 0.2 * (t - prevT) if prevT is not None else framePeriod
                prevT = t
                if pending is not None and t - pending > 1.0:  # 點了很久弧形都沒消失，沒點中，不拿來修正
                    pending = None
                samples.append((t, x))
                if len(samples) < 3:
                    continue
                ts = np.array([a for a, _ in samples])
                vel = float(np.polyfit(ts - ts[-1], [b for _, b in samples], 1)[0])  # 最近幾張的位置擬合出的速度(像素/秒)
                center = (left + right) / 2
                # 到正中間還要多久；只在「往中間走」而且速度夠的時候算(兩端折返的地方速度接近 0，算出來的時間不準)
                if abs(vel) >= METER_MIN_SPEED * s and (center - x) * vel > 0:
                    clickAt = t + (center - x) / vel - self._lead
                    wait = clickAt - time.perf_counter()
                    if armed and t - lastClick >= METER_MIN_GAP and -0.02 <= wait <= max(framePeriod, 0.01) * 1.2:
                        while time.perf_counter() < clickAt and not stopEvent.is_set():
                            time.sleep(0)
                        pending = lastClick = self.backend.tap(*tapPt)
                        clicks += 1
                        armed = False
                        self._trace(f"點擊 第 {clicks} 下 指針 {x:.0f} 速度 {vel:.0f} 區塊 {left:.0f}~{right:.0f} 提前 {self._lead * 1000:.0f}ms 每張 {framePeriod * 1000:.0f}ms")
                        continue
                if not left <= x <= right:
                    armed = True
