import os
import random
import time
import cv2
import numpy as np
from scripts.common.base import BaseTask
from tools import ocr
from tools.debug import DebugMixin
from tools.tools import getResourcePath, getThreshold

# ===== 座標與數值一律以 1600x900 為基準，從錄影量的，還沒在實機長時間驗證 =====
SCAN_INTERVAL = 0.08  # 每輪看畫面的間隔(秒)；貓耳泡泡只停留約 1 秒，間隔要夠短
HOVER_DELAY = (0.15, 0.3)  # 選單、對話框的按鈕: 移到按鈕上到點下去之間的停頓(秒)
BUBBLE_HOVER = 0.05  # 貓耳泡泡的按鈕: 時間緊，只停一下讓遊戲偵測到滑鼠懸停
UNKNOWN_STOP_SECONDS = 150  # 認不得任何畫面這麼久就停止(逗貓小遊戲約每 15 秒才出現一次泡泡，所以要抓寬)
AFTER_MENU = 2.0  # 點下「逗貓」後等小遊戲開始(秒)
AFTER_PAW = 1.2  # 點下貓咪頭上的爪印後等選單出現(秒)

# --- 小遊戲: 貓頭上冒出貓耳圓圈，裡面是按鈕圖示，要在綠色倒數跑完前點右下角同樣圖示的按鈕 ---
BUTTONS = {"up": (1225, 635), "down": (1227, 760), "a": (1375, 692)}  # 右下角三顆按鈕的中心
ICON_TEMPLATES = {"up": "catIconUp", "down": "catIconDown", "a": "catIconA"}
BUBBLE_EARS = "catBubble"  # 貓耳模板左上角到圖示中心的距離: 模板 90x20，圖示中心在水平正中、往下 59
EARS_TO_ICON = (45, 59)
ICON_RADIUS = 36  # 只看圓圈中間這麼大的範圍(不含外圈)
ICON_WHITE = 215  # 圖示是純白，三個色版都超過這個值
ICON_MIN_PIXELS = 60  # 白色像素少於這個就當沒有圖示(例如已經打勾)
ICON_SIZE = 32  # 圖示框到白色像素的外接矩形後縮成這麼大再比
ICON_MIN_SCORE = 0.5  # 最像的那個圖示至少要有這個分數
ICON_MIN_MARGIN = 0.1  # 最像的要比第二像的高出這麼多，認不準就不點
BUBBLE_COOLDOWN = 2.5  # 點完一次後多久內不再看泡泡(打勾的畫面還留著貓耳)

# 逗貓結束的結果視窗: 標題「逗貓結束」+「確定」；點掉後回到貓咪旁邊的畫面
OK_FALLBACK = (800, 640)  # 找不到「確定」模板時的備案位置

# 貓咪累了: 畫面上方浮出「貓咪好像有點疲了，等貓貓恢復點活力吧～」就停止(白字、沒有模板，用 OCR 讀，只比關鍵字)
TIRED_ROI = (480, 60, 1080, 130)  # 提示條的位置(1600x900 基準，從實機截圖量的)
TIRED_KEYWORDS = ("疲", "恢復", "活力")
TIRED_EVERY = 0.6  # 每隔多久讀一次(秒)；提示條會停留幾秒，OCR 不用每輪都做

TEMPLATES = ("catBubble", "catIconUp", "catIconDown", "catIconA", "catPaw", "catMenu", "catEnd", "catOk")


def iconFeature(img, cx, cy, r=ICON_RADIUS):
    """圖示中心 (cx, cy) 周圍圓形範圍內的純白像素 -> 縮成固定大小的二值圖；沒有圖示或超出畫面回傳 None"""
    x0, y0 = int(cx) - r, int(cy) - r
    if x0 < 0 or y0 < 0 or y0 + 2 * r > img.shape[0] or x0 + 2 * r > img.shape[1]:
        return None
    patch = img[y0 : y0 + 2 * r, x0 : x0 + 2 * r]
    yy, xx = np.ogrid[: 2 * r, : 2 * r]
    mask = (patch.min(axis=2) > ICON_WHITE) & ((xx - r) ** 2 + (yy - r) ** 2 <= r * r)
    ys, xs = np.nonzero(mask)
    if len(ys) < ICON_MIN_PIXELS:
        return None
    crop = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype(np.uint8) * 255
    return cv2.resize(crop, (ICON_SIZE, ICON_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)


def iconScore(a, b):
    """兩張圖示二值圖的相似度(-1~1)"""
    if a.std() < 1e-3 or b.std() < 1e-3:
        return -1.0
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0])


class Cat(DebugMixin, BaseTask):
    """自動逗貓: 點貓咪頭上的爪印 → 選單點「逗貓」→ 小遊戲(看到貓耳泡泡就點同樣圖示的按鈕)→ 結果視窗按「確定」→ 重來。
    角色要先站在貓咪旁邊(畫面上看得到貓咪頭上的爪印、或逗貓選單、或小遊戲)。只支援前景模式"""

    templateDir = "templates/cat"

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self.rounds = 0  # 已逗完幾輪
        self.hits = 0  # 已點幾次泡泡
        self._unknownSince = None
        self._lastBubbleAt = 0.0
        self._lastUnknownLog = 0.0
        self._lastTiredCheck = 0.0
        self._geom = (1.0, 1.0, 0, 0)  # (x 縮放, y 縮放, 遊戲左上角在截圖裡的 x, y)
        self._tpl = {}
        self._icons = {}

    def _threadTargets(self):
        return [self._loop]

    # ---------- 共用 ----------
    def _gameShot(self):
        """前景模式截到的是 RGB，統一轉成 BGR(模板是真實顏色)，縮放成 1600x900 基準再回傳，並記下縮放與遊戲位置"""
        img, origin = super()._gameShot()
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])
        rect = self.backend.getGameRect()
        ox, oy = (rect[0] - origin[0], rect[1] - origin[1]) if rect else (0, 0)
        if rect:
            img = img[max(0, oy) : max(0, oy) + rect[3], max(0, ox) : max(0, ox) + rect[2]]
            ox, oy = max(0, ox), max(0, oy)
        self._geom = (img.shape[1] / 1600, img.shape[0] / 900, ox, oy)
        if img.shape[1] != 1600 or img.shape[0] != 900:
            img = cv2.resize(img, (1600, 900), interpolation=cv2.INTER_AREA)
        return img, origin

    def _pt(self, bx, by):
        """1600x900 基準的遊戲座標 -> 截圖座標"""
        sx, sy, ox, oy = self._geom
        return bx * sx + ox, by * sy + oy

    def _template(self, name):
        if name not in self._tpl:
            img = cv2.imread(getResourcePath(f"{self.templateDir}/{name}.png"))
            if img is None:
                raise RuntimeError(f"讀不到模板圖片：{name}")
            self._tpl[name] = img
        return self._tpl[name]

    def _find(self, img, name, threshold=None):
        """在 1600x900 基準的畫面裡找模板，回傳 (中心座標, 分數)；分數不夠回傳 None。座標是基準座標(不是截圖座標)"""
        tpl = self._template(name)
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < (threshold or getThreshold(self.config, name)):
            return None
        return (loc[0] + tpl.shape[1] / 2, loc[1] + tpl.shape[0] / 2), score

    def _clickAt(self, origin, pos, stopEvent, hover=None):
        """點基準座標 pos；先移過去、停一下再點(遊戲的按鈕要先偵測到滑鼠懸停)"""
        x, y = self._getRelCords(origin, self._pt(*pos))
        x, y = round(x + random.randint(-2, 2)), round(y + random.randint(-2, 2))
        self.backend.moveTo(x, y)
        if stopEvent.wait(random.uniform(*HOVER_DELAY) if hover is None else hover):
            return False
        self.backend.click(x, y)
        return True

    # ---------- 主流程 ----------
    def _loop(self, stopEvent):
        if getattr(self.backend, "isAdb", False):
            raise RuntimeError("逗貓只支援前景模式")
        missing = [n for n in TEMPLATES if not os.path.exists(getResourcePath(f"{self.templateDir}/{n}.png"))]
        if missing:
            raise RuntimeError("缺少模板圖片：" + "、".join(missing))
        for kind, name in ICON_TEMPLATES.items():
            tpl = self._template(name)
            h, w = tpl.shape[:2]
            feature = iconFeature(tpl, w // 2, h // 2)
            if feature is None:
                raise RuntimeError(f"模板圖片「{name}」裡找不到圖示")
            self._icons[kind] = feature
        if not ocr.isReady():
            self.setLog("⚠️ 沒有 Windows 文字辨識(OCR)，看不到「貓咪累了」的提示，不會自動停止")
        self.backend.focusGame()
        self._wakeMouse(stopEvent)
        self._startWatchdog(stopEvent)
        self._trace("逗貓開始", reset=True)

        steps = (
            ("小遊戲", self._stepBubble),
            ("結果", self._stepEnd),
            ("選單", self._stepMenu),
            ("爪印", self._stepPaw),
        )
        while not stopEvent.wait(SCAN_INTERVAL):
            img, origin = self._gameShot()
            if self._tired(img):
                self.setLog("貓咪累了，已停止")
                self._saveShot(img, "catTired")
                stopEvent.set()
                break
            for name, step in steps:
                if step(img, origin, stopEvent):
                    self._traceChange(name, "畫面")
                    self._unknownSince = None
                    break
            else:
                self._traceChange("未知", "畫面")
                self._stepIdle(img, stopEvent)

    def _tired(self, img):
        """畫面上方有沒有「貓咪好像有點疲了…」的提示條。先看範圍內有沒有足夠的白字，有才做 OCR"""
        now = time.time()
        if now - self._lastTiredCheck < TIRED_EVERY:
            return False
        self._lastTiredCheck = now
        x0, y0, x1, y1 = TIRED_ROI
        region = img[y0:y1, x0:x1]
        white = (region.min(axis=2) > 225).astype(np.uint8) * 255
        if int(np.count_nonzero(white)) < 150:
            return False
        black = cv2.cvtColor(255 - white, cv2.COLOR_GRAY2BGR)
        return any(k in text for text in ocr.readText(black) for k in TIRED_KEYWORDS)

    def _stepIdle(self, img, stopEvent):
        now = time.time()
        if self._unknownSince is None:
            self._unknownSince = now
            return
        waited = now - self._unknownSince
        if waited > UNKNOWN_STOP_SECONDS:
            self.setLog("認不得目前的畫面太久，已停止。請站在貓咪旁邊、讓牠頭上的爪印出現在畫面上")
            self._saveShot(img, "catUnknown")
            stopEvent.set()
        elif waited > 20 and now - self._lastUnknownLog > 20:
            self._lastUnknownLog = now
            self.setLog("等待小遊戲、選單或貓咪頭上的爪印…")

    def _classify(self, img, cx, cy):
        """泡泡裡的圖示是哪顆按鈕: 回傳 (按鈕名稱 或 None, 各按鈕分數)"""
        feature = iconFeature(img, cx, cy)
        if feature is None:
            return None, {}
        scores = {kind: iconScore(feature, ref) for kind, ref in self._icons.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, second = ranked[0], ranked[1]
        if best[1] < ICON_MIN_SCORE or best[1] - second[1] < ICON_MIN_MARGIN:
            return None, scores
        return best[0], scores

    def _stepBubble(self, img, origin, stopEvent):
        """小遊戲: 找到貓耳泡泡、認出圖示，就點右下角同樣圖示的按鈕。看到泡泡就算這個畫面，一定要排在最前面"""
        if time.time() - self._lastBubbleAt < BUBBLE_COOLDOWN:
            return False
        ears = self._find(img, BUBBLE_EARS)
        if not ears:
            return False
        tpl = self._template(BUBBLE_EARS)
        cx = ears[0][0] - tpl.shape[1] / 2 + EARS_TO_ICON[0]
        cy = ears[0][1] - tpl.shape[0] / 2 + EARS_TO_ICON[1]
        kind, scores = self._classify(img, cx, cy)
        if not kind:
            if scores:  # 有圖示但認不準: 可能剛冒出來還沒畫完，下一輪再看；只有圖示沒有(打勾)就不算泡泡
                self._trace("泡泡圖示認不準 " + str({k: round(v, 2) for k, v in scores.items()}))
                self._saveShot(img, "catIcon", keep=10)
                return True
            return False
        self._lastBubbleAt = time.time()
        if self._clickAt(origin, BUTTONS[kind], stopEvent, BUBBLE_HOVER):
            self.hits += 1
            self.setLog(f"第 {self.hits} 次：點「{ {'up': '左上', 'down': '左下', 'a': '右邊'}[kind] }」按鈕")
        return True

    def _stepEnd(self, img, origin, stopEvent):
        if not self._find(img, "catEnd"):
            return False
        ok = self._find(img, "catOk")
        self._clickAt(origin, ok[0] if ok else OK_FALLBACK, stopEvent)
        self.rounds += 1
        self.setLog(f"✅ 第 {self.rounds} 輪逗貓結束")
        stopEvent.wait(1.0)
        return True

    def _stepMenu(self, img, origin, stopEvent):
        menu = self._find(img, "catMenu")
        if not menu:
            return False
        self._clickAt(origin, menu[0], stopEvent)
        self._lastBubbleAt = 0.0
        self.setLog("開始逗貓")
        stopEvent.wait(AFTER_MENU)
        return True

    def _stepPaw(self, img, origin, stopEvent):
        paw = self._find(img, "catPaw")
        if not paw:
            return False
        self._clickAt(origin, paw[0], stopEvent)
        stopEvent.wait(AFTER_PAW)
        return True
