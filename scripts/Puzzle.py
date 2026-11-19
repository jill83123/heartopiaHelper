import time
import cv2
import numpy as np
from scripts.common.base import BaseTask
from tools import puzzle as P
from tools.finder import getBaseScale
from tools.debug import DebugMixin
from tools.guards import findCancelButton

GONE_SECONDS = 3  # 看過棋盤之後，連續這麼久都看不到拼圖畫面就停止
SCAN_INTERVAL = 0.05  # 每輪看畫面的間隔(秒)
WAIT_LOG_SECONDS = 10  # 沒看到拼圖畫面時，隔多久提醒一次
DROP_WAIT = 0.12  # 放下碎片後，等多久再看托盤有沒有少一片(秒)
CELL_CHANGE_THRESHOLD = 12  # 目標格放碎片前後的畫面差異超過這個，也算放上去了
CHANGE_THRESHOLD = 14  # 放下後托盤上該位置的畫面平均差異超過這個值，才算碎片被拿走了
TRAY_STABLE_DIFF = 2.5  # 托盤畫面和上一輪的平均差異低於這個值，才算停止滑動、可以開始比對
EDGE_FIRST = False  # 先切到「只顯示邊緣拼圖塊」拼邊緣。實測不先拼邊緣更快，預設關閉
SHAPE_MATCH = True  # 整片比對(上下挪位置比、凸角小塊一起比)；效果不好時改 False 就回到原本的做法
SHIFT_YS = (-10, 0, 10)  # 托盤碎片高低不一，上下挪這些像素各比一次
START_GRACE_SECONDS = 3.0  # 開始時沒看到拼圖介面，等這麼久還是沒有就停止
EMPTY_SLOT_DIFF = 10  # 托盤位置的畫面和碎片被拿走後的背景平均差異低於這個，就當作還是空的
RELAXED_COVER = 0.5  # 最後一格放寬判空格時，兩種底色至少占格子的比例(平常是 CHECKER_COVER)
MAX_RELAXED_TRIES = 6  # 放寬判斷最多用幾次(沒碎片可放時不要一直拖)
TRAY_FIXED_MAX = 7  # 剩這麼多片以下，托盤不捲動，碎片位置固定
TRAY_FIRST_X = 345  # 托盤第一片碎片的中心(1600 寬基準)
TRAY_STEP_X = 141  # 相鄰碎片間距(1600 寬基準)
GRID_STRONG_ENERGY = 1.2  # 托盤固定間距上，邊緣能量(相對底)超過這個值才算有碎片
EXH_LOOSE_DIFF = 30  # 逐一嘗試時，縮圖差異低於這個值就當同一片(比 REJECT_DIFF 鬆，因為放回托盤後位置會偏)
EXH_TRY_PENALTY = 0.6  # 逐一嘗試時，每試過一次(這格放錯、這片試過)扣的分數
EXH_FAIL_FACTOR = 3  # 逐一嘗試時，拖了沒反應多半是沒抓到碎片、不是格子不對，失敗上限放寬幾倍
MAX_CELL_FAILS = 2  # 同一格連續放不上去幾次，就先不再嘗試
TRASH_AFTER_GUESSES = 5  # 猜的、硬放的碎片累積幾片就先按一次垃圾桶(放錯的碎片會蓋住格子、干擾判斷)
TRASH_AFTER_PLACED = 24  # 不管有沒有把握，累積放了幾片也按一次
MAX_ROUNDS = 8  # 「放回垃圾桶再重來」最多幾輪
CORNER_RANK_GAP = 0.2  # 角落的圖案相似度，不能比所有空格裡最高的低超過這麼多(別的格子明顯更像，就是形狀看錯了)
CORNER_SHIFT_MIN_SCORE = 0.5  # 挪位置才看出是直角的碎片，和角落的圖案相似度至少要這麼高
CORNER_MIN_SCORE = 0.3  # 判成直角的碎片，和那個角落的圖案相似度至少要這麼高才鎖定
GOOD_RMS = 20  # 放下的碎片和參考圖同一格的顏色差(均方根)低於這個，就算放對了
SUSPECT_TRASH = 5  # 發現幾片放錯的，才去按垃圾桶清掉
CORNER_VOTES = 3  # 角落碎片算幾票(它的位置和形狀最確定)
PARITY_MAX_BAD = 4  # 採用規律後，放對的碎片不合規律累積這麼多次(而且比例不低)，就判定不是規律切割
PARITY_PENALTY = 0.45  # 凸凹奇偶和格子位置對不上，扣多少分
PARITY_SURE_SCORE = 0.75  # 放下時相似度高於這個，就先當成放對了，拿來校正凸凹規律
PARITY_PRIOR = {"h": 1, "v": 0}  # 預設的規律(左右、上下): 已知拼圖的觀察結果，之後用放對的碎片驗證，不對就改
PARITY_GIVEUP_VOTES = 14  # 票數到這麼多還是不一致，就放棄規律
PARITY_MIN_VOTES = 3  # 至少要有幾票才採用規律
PARITY_AGREE = 0.8  # 票數一致的比例要超過這個才採用
REJECT_DIFF = 10  # 兩片碎片縮圖的平均差異低於這個值，視為同一片
TEAL_HSV = ((80, 120, 100), (100, 255, 255))  # 確認視窗右邊「放回」鈕的青綠色
SCAN_SCROLL_PIXELS = 0.30  # 先找角落時每次捲的距離(要比托盤看得到的範圍小，才不會跳過碎片)
SCROLL_PIXELS = 0.30  # 捲動托盤時拖曳的距離(畫面寬的比例)
MIN_SCORE = 0.55  # 碎片與參考圖的相似分數下限
MIN_MARGIN = 0.12  # 第一名要比第二名高出多少，才算有把握
GUESS_SCORE = 0.40  # 沒有把握時猜測的下限
GUESS_MARGIN = 0.03
VERIFY_LOOSE_SCORE = 0.42  # 收尾階段，放下後在棋盤上重新比對，高於這個分數就把碎片移到它該在的空格
BATCH_MAX = 4  # 有把握的碎片一次最多連續放幾片
FORCE_SCORE = 0.25  # 連猜都不夠把握時，剩下的碎片還是挑最高分的放(空格已經不多，總得放進某一格)


class Puzzle(DebugMixin, BaseTask):
    """自動拼拼圖: 先開參考圖記下整張圖，再把托盤上的碎片依顏色、凸角缺角比對到參考圖的位置，拖到棋盤對應的格子。
    放錯的碎片不會落位，最後按垃圾桶放回托盤，再捲動托盤重新檢查。
    不負責進入拼圖，要先自己開好拼圖畫面。只支援前景模式(PC 版)"""

    templateDir = "templates/puzzle"

    def _gameShot(self):
        """前景模式截到的是 RGB，這裡統一轉成 BGR(OpenCV 的色相判斷、模板比對才不會偏掉)"""
        img, origin = super()._gameShot()
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])
        offset = P.alignOffset(img)
        if offset is not None and (offset != (0, 0) or img.shape[:2] != (P.BASE_H, P.BASE_W)):
            # 版面和基準位置差幾個像素: 平移回基準位置，之後所有座標照基準算，點擊用的原點跟著挪
            img = P.normalizeLayout(img, offset)
            origin = (origin[0] + offset[0], origin[1] + offset[1])
        return img, origin
    dialogGuard = False  # 垃圾桶的確認視窗要按「放回」，不能讓安全網自動按取消

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self.reference = None  # 參考圖本體(原尺寸 BGR)
        self.cellFails = {}  # (row, col) -> 連續失敗次數
        self.placed = 0
        self._lastTray = None
        self.suspect = set()  # recent 裡用模板比對發現放錯(圖案跟參考圖對不上)的格子
        self.recent = []  # 上次清垃圾桶以來放下的碎片 (縮圖, row, col)，之後看哪些格子又空了，就知道哪些放錯
        self.rejected = []  # 已知放錯的 (縮圖, row, col)，同一片碎片不再放同一格
        self.forceMode = False
        self.sweepDir = -1  # 捲動托盤的方向: -1 往左拖(看後面的碎片)、+1 往右拖(回到前面)
        self.sweeps = 0  # 托盤往兩個方向都捲到底了幾次
        self.rounds = 0
        self._relaxed = False
        self.guessed = 0  # 上次清垃圾桶以來，猜的、硬放的碎片數
        self._edgeChecked = False  # 這一輪開頭有沒有檢查過要不要開「只顯示邊緣」
        self._lastBoard = None
        self.placedAt = {}  # 格子 -> 放下的時間
        self._cornerScanned = True  # 不再先找角落(直接以規律切割為前提)；原本: 只顯示邊緣時，有沒有先把整個托盤找過一輪、把四個角落碎片先拼上去
        self._scanning = False
        self.parityChecks = [0, 0]  # 已採用規律後，放對的碎片 [合規律, 不合規律] 的次數
        self.parityOff = False  # 發現不是規律切割，不再使用
        self.parity = dict(PARITY_PRIOR)  # 一開始就假設是規律切割；校正出來的凸凹奇偶規律 {"h": 0/1, "v": 0/1}，沒有一致的規律就不用
        self.parityVotes = {"h": [0, 0], "v": [0, 0]}
        self._scoreCache = []
        self._gridKnown = False
        self.placedSince = {}  # 上次清空垃圾桶以來放下的每一片(含已被判定放對的): 垃圾桶會把所有放錯的都收回，不只是我們起疑的
        self.falseAlarms = 0  # 清掉的結果一片都沒放錯，就是檢查太敏感，下次多累積幾片再清
        self.edgesAt = {}  # 放過碎片的格子 -> 放下時看到的四邊形狀
        self._seenBoard = False
        self.slotFails = {}  # 托盤位置 x -> 從那裡拖了卻沒有任何變化的次數
        self.emptySlots = []  # 碎片被拿走後托盤上空出來的位置 (x, 當時的背景畫面)，之後背景沒變就是還空著，不要再去那裡拿碎片
        self._trayFixed = False  # 托盤左右都拖不動(碎片都在畫面上)就不再拖
        self._stuckScrolls = 0
        self._edgeGaveUp = False  # 邊緣剩下的放不上去，已放棄先拼邊緣
        self._relaxedTries = 0  # 放寬判空格已用幾次
        self.exhaustive = False  # 最後手段: 不看相似度，逐一嘗試所有沒試過的碎片 x 空格組合
        self.edgeMode = False  # 目前是不是「只顯示邊緣拼圖塊」

    def _threadTargets(self):
        return [self._loop]

    # ---- 主流程 ----
    def _loop(self, stopEvent):
        self.backend.focusGame()
        P.setTones(None)
        P.setGrid(10)  # 上一次可能是別的片數，開始拼時再偵測
        waitSince = None
        while not stopEvent.wait(SCAN_INTERVAL):
            img, base = self._gameShot()
            state = P.findFrame(img)
            if state is None and self._lastBoard is not None and (P._buttonsWhite(img) or P._ringUniform(img, P.BOARD_RING_X)):
                state = ("board", self._lastBoard)  # 版面位置固定: 剛剛還在拼圖，畫面只是被碎片凸角或動畫短暫干擾，沿用上次的位置
            if state is not None and state[0] == "board":
                self._lastBoard = state[1]
            if state is not None:
                self._seenBoard = True
            if state is None and self._seenBoard:
                # 拼的過程中看過棋盤，現在連右側按鈕都看不到了: 多半是拼完跳出結算畫面(或離開了拼圖)，等一下還是沒有就停止
                if waitSince is None:
                    waitSince = time.time()
                elif time.time() - waitSince > GONE_SECONDS:
                    self.setLog("看不到拼圖畫面了（可能已經拼完），已停止")
                    stopEvent.set()
                    return
                continue
            if state is None:
                # 開始時就沒看到拼圖介面: 稍等一下(排除畫面剛好閃一下)還是沒有就停止
                if waitSince is None:
                    waitSince = time.time()
                elif time.time() - waitSince > START_GRACE_SECONDS:
                    self._saveShot(img, "puzzle_notfound")  # 除錯模式才存: 看程式實際截到的是什麼
                    self.setLog("沒有看到拼圖畫面，請先進入拼圖再按開始，已停止")
                    stopEvent.set()
                    return
                continue
            waitSince = None

            kind, frame = state
            if kind == "reference":
                if self.reference is None:
                    self.reference = self._cropReference(img, frame)
                self._toggleReference(base, img, stopEvent)  # 參考圖開著，關掉才能操作
                continue

            if P.getTones() is None:  # 記下棋盤格的底色，之後判斷空格要和它一致(深色、灰色的碎片就不會被當成空格)
                ix, iy, iw, ih = P.innerRect(frame)
                P.setTones(P.boardTones(img[iy : iy + ih, ix : ix + iw]))

            if self.reference is None:
                if not self._learnReference(base, stopEvent):
                    self.setLog("❌ 開不了參考圖，已停止")
                    stopEvent.set()
                    return
                continue

            if not self._trayStable(img):
                continue  # 剛放完碎片時托盤還在滑動補位，這時比對會抓到歪掉的位置
            if not self._gridKnown:
                self._gridKnown = True  # 只在開始時看一次(拼的過程碎片會越來越少)
                centers = [(x, y) for x, y, _ in P.trayPieces(img) if img.shape[1] * P.TRAY_PICK_X[0] <= x <= img.shape[1] * P.TRAY_PICK_X[1]]
                boardW = P.innerRect(frame)[2]
                grid, gridScores, sure = P.detectGrid(img, cv2.resize(self.reference, (boardW, boardW), interpolation=cv2.INTER_AREA), centers)  # 參考圖要先縮成棋盤的大小，托盤碎片的比例才對得上
                if self._boardAllEmpty(img, frame):
                    probed = self._probeGrid(img, base, frame, centers, stopEvent)  # 棋盤全空時圖案比對不準(第一次跑常猜錯): 放一片到棋盤上，直接量它的大小
                    grid = probed or grid
                else:
                    ix, iy, iw, ih = P.innerRect(frame)
                    measured = P.measureBoardPieces(img[iy : iy + ih, ix : ix + iw])  # 棋盤上已有碎片: 直接量單獨一片的大小，比圖案比對準
                    grid = measured or P.gridFromSeams(img[iy : iy + ih, ix : ix + iw]) or grid  # 量不到單獨一片時，看接縫的間距
                grid = grid or P.TRAY_GRID
                if grid != P.GRID:
                    P.setGrid(grid)
                P.setTones(None)
                self.setLog(f"拼圖是 {grid}×{grid} 片")
                if self.recent:  # 量大小時放上去的那片: 先檢查，放錯就馬上放回托盤，再開始拼
                    self._evaluateRecent(stopEvent, quick=True)
                    if self.suspect:
                        self._recycle(stopEvent)
                continue
            if P.edgeOnDetected(img, P.EDGE_BTN):
                self.edgeMode = True
            edgeOn = self.edgeMode  # 自己點開的就算開著(畫面偵測偶爾會漏看)，點關才算關
            if not self._edgeChecked:
                self._edgeChecked = True
                if EDGE_FIRST and not edgeOn and not self._edgeGaveUp and self._ringEmpty(img, frame):
                    self._clickRel(base, img, P.EDGE_BTN)  # 先只顯示邊緣拼圖塊，邊緣好分辨又不會搶到其他碎片的位置
                    self.edgeMode = True
                    self.setLog("先拼邊緣：切換成只顯示邊緣拼圖塊")
                    stopEvent.wait(0.6)
                    self._lastTray = None
                    continue
            result = self._placeOne(img, base, frame, stopEvent, edgeOn)
            if result == "placed":
                self.sweeps = 0
                self._evaluateRecent(stopEvent, quick=self.exhaustive or self.forceMode)  # 先用模板比對看放對了沒，放對的不用再去按垃圾桶
                need = min((1 if (self.exhaustive or self.forceMode) else SUSPECT_TRASH) + 2 * self.falseAlarms, 11)  # 最後階段發現一片放錯就先清掉再試下一個位置
                if len(self.suspect) >= need or len(self.recent) >= TRASH_AFTER_PLACED:
                    self._recycle(stopEvent)  # 發現放錯的累積到一定數量才清，不要讓它們蓋住真正的空格
                    self.guessed = 0
                    self._trayFixed = False  # 碎片回到托盤，內容變了，要重新確認托盤捲不捲得動
                    self._lastTray = None
                continue
            if result == "retry":
                continue
            if result == "edgeDone":  # 邊緣拼完了，切回顯示全部碎片(只有確實看到開著才點，免得反而把它打開)
                if self.recent and self._recycle(stopEvent):  # 還有沒落位的碎片可能蓋住邊緣格，先清掉再確認一次
                    self._lastTray = None
                    continue
                if P.edgeOnDetected(img, P.EDGE_BTN):
                    self._clickRel(base, img, P.EDGE_BTN)
                self.edgeMode = False
                self.setLog("邊緣拼完了，切換成顯示全部碎片")
                self.sweeps = 0
                self.forceMode = False  # 邊緣階段的「硬放」「逐一嘗試」不要帶到下一階段，不然每放一片就會去按垃圾桶
                self.exhaustive = False
                self._trayFixed = False
                self._stuckScrolls = 0
                self.suspect = set()
                stopEvent.wait(0.6)
                self._lastTray = None
                continue
            if result == "full":  # 棋盤沒有空格了: 還有放錯的就清垃圾桶重來，沒有就是拼完了
                if self.rounds < MAX_ROUNDS and self._recycle(stopEvent):  # 平坦的圖案(如地板)放錯也會被當成放對，所以不管有沒有記錄，一律按一次垃圾桶確認沒有未落位的碎片
                    self._startNewRound()
                    continue
                self.setLog("✅ 拼圖完成")
                stopEvent.set()
                return

            # idle: 目前看得到的碎片沒有可放的。把托盤捲動看看有沒有別的碎片
            if self._scanning and self._trayFixed:
                self._cornerScanned, self._scanning = True, False
                continue
            if not self._trayFixed:
                if self._scrollTray(base, img, stopEvent):
                    self._stuckScrolls = 0
                    continue
                if self._scanning:  # 托盤捲到底了: 整個托盤都看過，角落碎片都先放了
                    self._cornerScanned, self._scanning = True, False
                    self.sweepDir = -self.sweepDir
                    self.sweeps = 0
                    continue
                self._stuckScrolls += 1
                if self._stuckScrolls >= 1:  # 拖到底了就不再往回拖確認，直接處理畫面上現有的碎片(新一輪開始時才會捲回最前面)
                    self._trayFixed = True
                self.sweepDir = -self.sweepDir  # 這個方向捲到底了，換方向
                self.sweeps += 1
                if self.sweeps < 2 and not self._trayFixed:
                    continue
            self.sweeps = 0
            if not self.forceMode:
                self.forceMode = True
                self.setLog("沒有把握的碎片了，剩下的改挑最像的放")
                continue
            if not self.exhaustive:
                self.exhaustive = True
                self.setLog("改成逐一嘗試：剩下的碎片依序試每個空格")
                continue
            if edgeOn and not self._edgeGaveUp:  # 邊緣剩下的怎麼試都放不上去: 先放著，改拼其他碎片，不要一直耗在這裡
                self._edgeGaveUp = True
                if P.edgeOnDetected(img, P.EDGE_BTN):
                    self._clickRel(base, img, P.EDGE_BTN)
                self.edgeMode = False
                self.setLog("邊緣剩下的碎片先放著，改拼其他碎片")
                self._startNewRound()
                stopEvent.wait(0.6)
                self._lastTray = None
                continue
            if self.recent and self.rounds < MAX_ROUNDS and self._recycle(stopEvent):
                self._startNewRound()
                continue
            if not self._relaxed:  # 最後的碎片常被黑名單誤擋(顏色相近的碎片被當成同一片)，清掉黑名單再試最後一次
                self._relaxed = True
                self.rejected.clear()
                self.cellFails.clear()
                self.setLog("清掉黑名單，把剩下的碎片再試一次")
                self._startNewRound()
                continue
            self.setLog("剩下的碎片都放不上去，已停止")
            stopEvent.set()
            return

    def _ringEmpty(self, img, frame):
        bx, by, bw, bh = P.innerRect(frame)
        empty = P.emptyMap(img[by : by + bh, bx : bx + bw])
        return bool((empty & P.ringCells()).any())

    def _clickRel(self, base, img, rel):
        self.backend.click(base[0] + round(rel[0] * img.shape[1]), base[1] + round(rel[1] * img.shape[0]))

    def _startNewRound(self):
        self._edgeChecked = False
        self.guessed = 0
        self.rounds += 1
        self.forceMode = False
        self.exhaustive = False
        self._trayFixed = False
        self._stuckScrolls = 0
        self.sweepDir = 1  # 先捲回托盤最前面再依序檢查
        self.sweeps = 0
        self.setLog(f"第 {self.rounds + 1} 輪：重新檢查托盤上的碎片")

    def _trayStable(self, img):
        band = self._trayBand(img)
        last, self._lastTray = self._lastTray, band
        return last is not None and float(np.abs(band - last).mean()) < TRAY_STABLE_DIFF

    @staticmethod
    def _trayBand(img):
        H = img.shape[0]
        return cv2.resize(img[round((P.TRAY_Y - P.TRAY_HALF_H) * H) : round((P.TRAY_Y + P.TRAY_HALF_H) * H)], (160, 12), interpolation=cv2.INTER_AREA).astype(np.int16)

    # ---- 參考圖 ----
    @staticmethod
    def _cropReference(img, frame):
        x, y, w, h = P.innerRect(frame)
        return img[y : y + h, x : x + w].copy()

    def _imageButton(self, base, img):
        return (round(base[0] + P.IMAGE_BTN[0] * img.shape[1]), round(base[1] + P.IMAGE_BTN[1] * img.shape[0]))

    def _toggleReference(self, base, img, stopEvent):
        self.backend.click(*self._imageButton(base, img))
        stopEvent.wait(0.8)

    def _learnReference(self, base, stopEvent):
        """按一下右側「參考圖」鈕開啟、截下整張圖，之後迴圈會看到參考圖開著而把它關掉"""
        for _ in range(3):
            img, base = self._gameShot()
            self._toggleReference(base, img, stopEvent)
            if stopEvent.is_set():
                return True
            img, base = self._gameShot()
            state = P.findFrame(img)
            if state and state[0] == "reference":
                self.reference = self._cropReference(img, state[1])
                self.setLog("已記下參考圖")
                return True
        return False

    # ---- 放碎片 ----
    def _placeOne(self, img, base, frame, stopEvent, edgeOn=False):
        """找出托盤上最有把握的一片，拖到棋盤上。
        回傳 "placed"、"idle"(沒有可放的碎片)、"full"(棋盤沒有空格了)、"edgeDone"(只顯示邊緣時邊緣格已滿)、"retry" """
        bx, by, bw, bh = P.innerRect(frame)
        size = bw
        board = img[by : by + bh, bx : bx + bw]
        empty = P.emptyMap(board)
        if not empty.any():
            # 最後一格的棋盤紋常被鄰格的凸起切碎，兩種底色占的比例不夠而被當成已有碎片: 放寬標準再找一次(最多試幾次，免得真的拼完了還一直拖)
            relaxed = P.emptyMap(board, cover=RELAXED_COVER) if P.getTones() is not None else empty  # 要有學到的棋盤底色才敢放寬，不然平坦的圖案會被誤判
            if P.getTones() is not None and relaxed.any() and int(relaxed.sum()) <= 2 and self._relaxedTries < MAX_RELAXED_TRIES:
                self._relaxedTries += 1
                empty = relaxed
            else:
                return "full"
        nEmpty = int(empty.sum())  # 棋盤上每個空格對應托盤上一片碎片(放錯還沒清掉的會讓托盤更少)，所以托盤碎片數不會超過它
        corners = ((0, 0), (0, P.GRID - 1), (P.GRID - 1, 0), (P.GRID - 1, P.GRID - 1))
        self._scanning = edgeOn and not self._cornerScanned and any(empty[r, c] for r, c in corners)
        if edgeOn and not self._scanning:
            self._cornerScanned = True
        if edgeOn:
            empty = empty & P.ringCells()  # 只顯示邊緣拼圖塊時，碎片只可能屬於最外圈
            if not empty.any():
                return "edgeDone"
        cell = size / P.GRID  # 棋盤一格
        tcell = size / P.TRAY_GRID  # 托盤碎片的大小固定(不隨片數變)
        ref = cv2.resize(self.reference, (size, size), interpolation=cv2.INTER_AREA)

        tones = P.checkerTones(board, empty)
        required = P.requiredEdges(board, empty, cell, tones) if tones else {}  # 空格四邊鄰居要求的凸角/缺角

        # 每片碎片對所有空格打分。先挑「有把握」的(夠像、明顯勝過第二名)；都沒有時從中挑差距最大的去猜；
        # 強制模式(托盤都看過了還是沒有把握的)才連猜都不夠的也挑最高分的放
        strict, guess, force, exh = None, None, None, None
        cornerBest = None
        cornerBestEdges = None
        rows = []  # 每片碎片的 (x, y, 對各空格的分數)
        slots = P.trayPieces(img)
        maxEnergy = max((e for _, _, e in slots), default=0.0)
        slots = [t for t in slots if img.shape[1] * P.TRAY_PICK_X[0] <= t[0] <= img.shape[1] * P.TRAY_PICK_X[1]]  # 托盤兩端被切掉、淡出的碎片點不到，不看
        strong = [t for t in slots if t[2] >= GRID_STRONG_ENERGY]  # 固定間距上邊緣能量明顯高於底的位置
        if nEmpty <= TRAY_FIXED_MAX:
            # 碎片不多時托盤不用捲動，碎片一律從固定的第一格開始靠左、等間距排: 直接用固定位置。
            # 能量、顏色偵測在背景雜亂(夜景、花叢、禮物堆)時會偏掉，拖曳起點會落在碎片外面
            slots = [(round((TRAY_FIRST_X + k * TRAY_STEP_X) * img.shape[1] / 1600), slots[0][1] if slots else round(P.TRAY_Y * img.shape[0]), 1.0) for k in range(nEmpty)]
            slots = [t for t in slots if img.shape[1] * P.TRAY_PICK_X[0] <= t[0] <= img.shape[1] * P.TRAY_PICK_X[1]]
            maxEnergy = 1.0
        elif nEmpty < len(slots) and strong:
            slots = sorted(sorted(strong, key=lambda t: -t[2])[:nEmpty])
        elif nEmpty < len(slots):
            # 碎片數比托盤放得下的少時，碎片散落、不照固定間距排: 不靠間距推算，直接找畫面上有碎片(彩色)的地方
            byColor = [t for t in P.trayPiecesByColor(img, nEmpty) if img.shape[1] * P.TRAY_PICK_X[0] <= t[0] <= img.shape[1] * P.TRAY_PICK_X[1]]
            slots = byColor if byColor else slots[:nEmpty]  # 不截到剩下片數: 場景裡的誤判拖了沒變化會被記成空位，真的碎片不能因為名額被占走而被漏掉
            maxEnergy = 1.0
        for sx, sy, energy in slots:
            if energy < 0.5 * maxEnergy:
                continue  # 這個位置的邊緣能量遠低於有碎片的位置: 托盤上根本沒碎片(尾端空了)，不要去拖
            if self._slotIsEmpty(img, sx, sy, tcell):
                continue
            sig0 = self._sig(img, sx, sy, tcell)
            scores = P.cellScores(img, (sx, sy), ref, cell, empty, cache=self._cacheFor(sig0), tcell=tcell, **self._matchArgs())
            edges = P.pieceEdges(img, (sx, sy), tcell)
            raw = scores.copy()
            scores = P.applyShape(scores, edges, required)  # 再用凸角、缺角和鄰居對照
            scores = self._parityAdjust(scores, edges)
            shaped = scores.copy()
            corner = P.cornerCell(edges)  # 直角(相鄰兩邊平的)一定是四個角落之一
            cornerEdges, shifted = edges, False
            if corner is None and edgeOn:  # 托盤上的碎片高低不一，固定高度看形狀會看歪；上下挪一點再看，任何一個高度看出直角就算候選
                for o in (-8, 8, -14, 14):
                    e2 = P.pieceEdges(img, (sx, sy + o), tcell)
                    c2 = P.cornerCell(e2)
                    if c2 is not None:
                        corner, cornerEdges, shifted = c2, e2, True
                        break
            if corner is not None and shifted and raw[corner] < CORNER_SHIFT_MIN_SCORE:
                corner = None  # 挪了位置才看出來的，證據比較弱，圖案相似度要更高才採用
            if corner is not None and (raw[corner] < CORNER_MIN_SCORE or raw[corner] < float(np.where(empty, raw, -1).max()) - CORNER_RANK_GAP):
                corner = None  # 形狀說是角落、圖案卻完全對不上: 多半是形狀看錯，不要硬鎖
            if corner is None:
                side = P.sideMask(edges)  # 只有一邊是平的 -> 一定是那一邊的邊緣碎片，其他格子不用考慮
                if edgeOn and not self.forceMode and side is not None and (side & empty).any():
                    scores = np.where(side, scores, -1.0)
            sig = self._sig(img, sx, sy, tcell)
            blocked = np.zeros_like(empty)
            for oldSig, r0, c0 in (self.rejected if nEmpty > 1 else []):  # 只剩最後一格時，這片一定屬於它，不能因為之前放不上去就把它列黑名單  # 這片碎片之前放在這一格被放回來過，就不是這格
                if float(np.abs(sig - oldSig).mean()) < REJECT_DIFF:
                    scores[r0, c0] = -1.0
                    blocked[r0, c0] = True
            for (r0, c0), n in (self.cellFails.items() if nEmpty > 1 else []):
                if n >= MAX_CELL_FAILS * (EXH_FAIL_FACTOR if self.exhaustive else 1):
                    scores[r0, c0] = -1.0
                    blocked[r0, c0] = True
            if corner is not None:
                # 直角碎片只能放它的角落: 角落還空著就直接放那格；角落已經有了或試過不對，這片就先不放，絕不拿去放別的格子
                ok = bool(empty[corner]) and not blocked[corner]
                scores = np.full_like(scores, -1.0)
                shaped = np.full_like(shaped, -np.inf)
                if ok:
                    scores[corner] = min(1.0, raw[corner] + 0.3)
                    shaped[corner] = scores[corner]
                    if cornerBest is None or scores[corner] > cornerBest[0][2]:
                        cornerBest = ((corner[0], corner[1], float(scores[corner])), (sx, sy))  # 直角碎片優先: 角落先拼上去
                        cornerBestEdges = cornerEdges
            if self.exhaustive:  # 逐一嘗試: 不管相似度與形狀，剩下的碎片 x 還空著且沒試過的格子，挑形狀最合的組合
                # 放錯回到托盤的碎片位置、縮圖都會變，黑名單認不出同一片，會一直挑同一片放同一格: 這格被放錯過、這片被試過的次數都扣分，強迫輪換
                cellTries = np.zeros(empty.shape, np.float32)
                pieceTries = 0
                for oldSig, r0, c0 in self.rejected:
                    cellTries[r0, c0] += 1
                    if float(np.abs(sig - oldSig).mean()) < EXH_LOOSE_DIFF:
                        pieceTries += 1
                cand = np.where(empty & ~blocked, shaped - EXH_TRY_PENALTY * (cellTries + pieceTries), -np.inf)
                r1, c1 = np.unravel_index(int(cand.argmax()), cand.shape)
                if np.isfinite(cand[r1, c1]) and (exh is None or cand[r1, c1] > exh[0][2]):
                    exh = ((int(r1), int(c1), float(cand[r1, c1])), (sx, sy))
            rows.append((sx, sy, scores))
        # 整體分配: 每片碎片各配一個不重複的格子(兩片搶同一格時自動讓步)，把握度看「這片不放這格總分掉多少」
        strictList = []
        for (sx, sy, _), a in zip(rows, P.assignCells([r[2] for r in rows])):
            if a is None:
                continue
            r, c, score, margin = a
            if score >= MIN_SCORE and margin >= MIN_MARGIN:
                strictList.append(((r, c, score), (sx, sy)))
                if strict is None or score > strict[0][2]:
                    strict = ((r, c, score), (sx, sy))
            elif score >= GUESS_SCORE and margin >= GUESS_MARGIN and (guess is None or margin > guess[2]):
                guess = ((r, c, score), (sx, sy), margin)
            if self.forceMode and score >= FORCE_SCORE and (force is None or score > force[0][2]):
                force = ((r, c, score), (sx, sy))
        best = cornerBest or (None if self._scanning else (strict or (guess and guess[:2]) or force or exh))  # 先找角落時，非角落的碎片先不放
        if best is None:
            return "idle"
        kind = "" if (cornerBest or strict) else ("，猜的" if guess else ("，硬放的" if force else "，逐一嘗試"))

        isCorner = bool(cornerBest) and best is cornerBest
        (row, col, score), (sx, sy) = best
        targets = [(row, col, score, sx, sy, cornerBestEdges if isCorner else P.pieceEdges(img, (sx, sy), tcell))]
        if best is strict and not (self._scanning or self.forceMode or self.exhaustive or edgeOn) and nEmpty > 1:
            # 有把握的碎片一次連續放(各自分到不同的格子)，不用每放一片就等托盤穩定、重新比對: 從右邊的碎片開始拿，
            # 它右邊沒有碎片會往左補位，左邊還沒拿的碎片位置就不會變
            top = sorted(strictList, key=lambda t: -t[0][2])[:BATCH_MAX]
            if best not in top:
                top[-1] = best
            targets = [(r, c, sc, x, y, P.pieceEdges(img, (x, y), tcell)) for (r, c, sc), (x, y) in sorted(top, key=lambda t: -t[1][0])]
        sigs = {t[3]: self._sig(img, t[3], t[4], tcell) for t in targets}
        placedAny = False
        cur = img
        for k, (row, col, score, sx, sy, edges) in enumerate(targets):
            if k > 0 and float(np.abs(self._sig(cur, sx, sy, tcell) - sigs[sx]).mean()) >= REJECT_DIFF * 1.5:
                break  # 托盤上這個位置的碎片已經不是原本那片(托盤動了)，不要照舊的位置拿，回到主迴圈重新看
            status = self._dropPiece(cur, base, frame, ref, cell, tcell, nEmpty, row, col, score, kind, sx, sy, edges, stopEvent, edgeOn)
            if status != "placed":
                return "placed" if placedAny else status
            placedAny = True
            if k + 1 < len(targets):
                cur, _ = self._gameShot()  # 放下後可能被修正過位置，用最新的畫面繼續
        return "placed"

    def _dropPiece(self, img, base, frame, ref, cell, tcell, nEmpty, row, col, score, kind, sx, sy, edges, stopEvent, edgeOn):
        """把托盤位置 (sx, sy) 的碎片拖到格子 (row, col)，看有沒有放上去。回傳 "placed" 或 "retry" """
        bx, by, bw, bh = P.innerRect(frame)
        tx = bx + round((col + 0.5) * cell)
        ty = by + round((row + 0.5) * cell)
        gx, gy = sx, sy
        if nEmpty <= 2 or self.exhaustive:  # 只剩最後幾片(或逐一嘗試): 每次的抓取點、落點都稍微偏一點(碎片被抓的位置不一定剛好在可拖的範圍內，偏了可能不會吸附)
            n = getattr(self, "soloTries", 0)
            self.soloTries = n + 1
            ox, oy = ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, 1), (-1, 1), (1, -1))[n % 9]
            tx += round(ox * cell * 0.15)
            ty += round(oy * cell * 0.15)
            gx, gy = sx + round(((0, 10, -8, 0, 0, 12, -10)[n % 7]) * 1.0), sy + round(((0, 0, 0, -10, 10, -8, 8)[n % 7]) * 1.0)
        before = self._slotPatch(img, sx, sy, tcell)
        cy0, cy1, cx0, cx1 = by + round(row * cell), by + round((row + 1) * cell), bx + round(col * cell), bx + round((col + 1) * cell)
        beforeCell = img[cy0:cy1, cx0:cx1].astype(np.int16)  # 目標格放之前的樣子: 托盤那一格看不出變化時，也可以看這格有沒有變
        sig = self._sig(img, sx, sy, tcell)
        self.backend.drag(base[0] + gx, base[1] + gy, base[0] + tx, base[1] + ty)
        if stopEvent.wait(DROP_WAIT):
            return "retry"

        after, _ = self._gameShot()
        diff = float(np.abs(self._slotPatch(after, sx, sy, tcell).astype(np.int16) - before.astype(np.int16)).mean())
        cellDiff = float(np.abs(after[cy0:cy1, cx0:cx1].astype(np.int16) - beforeCell).mean())
        late = nEmpty <= 3  # 最後幾片: 只看托盤上那一格的碎片有沒有不見。拖到空的地方時，目標格會因為游標經過而變亮、變色，不能拿來當放上去的證據
        changed = diff >= CHANGE_THRESHOLD or (not late and cellDiff >= CELL_CHANGE_THRESHOLD)
        if changed and late:
            # 碎片沒落位時會飛回托盤，剛放下就看到的畫面變化可能只是飛行中的樣子。等動畫結束再看一次
            if stopEvent.wait(0.45):
                return "retry"
            settled, _ = self._gameShot()
            diff = float(np.abs(self._slotPatch(settled, sx, sy, tcell).astype(np.int16) - before.astype(np.int16)).mean())
            if diff < CHANGE_THRESHOLD:  # 又回到原本的樣子: 碎片被彈回托盤，沒放上去(不是托盤空了，不記成空位)
                self.cellFails[(row, col)] = self.cellFails.get((row, col), 0) + 1
                return "retry"
            after = settled
        if changed:
            self.emptySlots.append((sx, self._slotPatch(after, sx, sy, tcell)))
            self.placed += 1
            self.cellFails.pop((row, col), None)
            self.setLog(f"放上第 {row + 1} 列第 {col + 1} 行（相似度 {score:.2f}{kind}）")
            if kind:
                self.guessed += 1
            row, col = self._verify(after, base, frame, ref, cell, row, col, stopEvent, edgeOn)
            self.edgesAt[(row, col)] = edges
            self.recent.append((sig, row, col))
            self.placedSince[(row, col)] = sig
            self.placedAt[(row, col)] = time.time()
            return "placed"
        # 從這個托盤位置拖了，托盤上的畫面卻沒變: 多半根本沒有碎片(托盤後段空了)。連續兩次就記成空位，不再去那裡拿
        key = next((k for k in self.slotFails if abs(k - sx) <= 10), sx)
        self.slotFails[key] = self.slotFails.get(key, 0) + 1
        if self.slotFails[key] >= (2 * EXH_FAIL_FACTOR if self.exhaustive else 2):
            self.emptySlots.append((sx, before))
        self.cellFails[(row, col)] = self.cellFails.get((row, col), 0) + 1
        if self.cellFails[(row, col)] >= MAX_CELL_FAILS * (EXH_FAIL_FACTOR if self.exhaustive else 1):  # 同一格連續失敗才提醒，單次失敗(沒拖起來)很常見，不洗版
            self.setLog(f"第 {row + 1} 列第 {col + 1} 行試了幾次都放不上去，先跳過")
        return "retry"

    def _verify(self, img, base, frame, ref, cell, row, col, stopEvent, edgeOn=False):
        """放下後，用已經在棋盤上、完整露出的碎片重新比對一次(比托盤上的縮圖準)。
        位置對就算了；明顯屬於別的空格就把它拖過去修正一次；沒把握就只在日誌提醒。回傳碎片最後所在的格子"""
        bx, by, bw, bh = P.innerRect(frame)
        board = img[by : by + bh, bx : bx + bw]
        cx, cy = round((col + 0.5) * cell), round((row + 0.5) * cell)
        cands = P.matchPiece(board, (cx, cy), ref, cell, scales=(1.0,))
        if not cands:
            return row, col
        score, x, y, _ = cands[0]
        r2, c2 = min(P.GRID - 1, int(y // cell)), min(P.GRID - 1, int(x // cell))
        margin = score - cands[1][0] if len(cands) > 1 else 1.0
        if (r2, c2) == (row, col):
            return row, col
        loose = self.forceMode or self.exhaustive  # 收尾階段碎片已經沒幾片，棋盤上看到的整片比對比托盤縮圖準，門檻放寬一點
        if score >= (VERIFY_LOOSE_SCORE if loose else MIN_SCORE) and margin >= (MIN_MARGIN * 0.5 if loose else MIN_MARGIN) and P.emptyMap(board)[r2, c2] and (not edgeOn or P.ringCells()[r2, c2]):
            self.setLog(f"這片應該在第 {r2 + 1} 列第 {c2 + 1} 行（相似度 {score:.2f}）")
            self.backend.drag(base[0] + bx + cx, base[1] + by + cy, base[0] + bx + round((c2 + 0.5) * cell), base[1] + by + round((r2 + 0.5) * cell))
            stopEvent.wait(DROP_WAIT)
            return r2, c2
        return row, col  # 沒把握的就不動，放錯的之後會被垃圾桶清掉

    # ---- 垃圾桶 ----
    def _recycle(self, stopEvent):
        """按右側垃圾桶，把還沒落位(放錯)的單片拼圖放回托盤。會跳出確認視窗，要按「放回」(青綠色)。
        放回後看哪些放過碎片的格子又空了，那些就是放錯的，記下來以後同一片不再放那格。回傳有沒有成功放回"""
        img, base = self._gameShot()
        self.emptySlots = []  # 放錯的碎片會回到托盤
        self.slotFails = {}
        self.backend.click(base[0] + round(P.TRASH_BTN[0] * img.shape[1]), base[1] + round(P.TRASH_BTN[1] * img.shape[0]))
        # 確認視窗是淡入跳出的，等一下再看；最多等 3 秒，每 0.25 秒看一次
        pos = teal = None
        for _ in range(12):
            if stopEvent.wait(0.25):
                return False
            img, base = self._gameShot()
            scale = getBaseScale(self.backend, self.config) * self.backend.scaleResolver.factor
            pos = findCancelButton(img, self.config, scale)  # 「再想想」鈕(取消)，「放回」在它右邊
            if pos:
                break
            teal = P.findConfirmTeal(img)  # 模板對不上時，改用顏色找(青綠色放回＋左邊橘色再想想)
            if teal:
                break
        if not pos and not teal:
            self.setLog("按了垃圾桶但沒看到確認視窗（可能沒有放錯的碎片）")
            return False
        if not pos:
            self.backend.click(base[0] + teal[0], base[1] + teal[1])
            stopEvent.wait(0.4)
            return self._afterRecycle(stopEvent)
        x, y = int(pos[0]), int(pos[1])
        x0, x1, y0, y1 = x + int(150 * scale), x + int(450 * scale), max(0, y - int(40 * scale)), y + int(40 * scale)
        mask = cv2.inRange(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV), np.array(TEAL_HSV[0]), np.array(TEAL_HSV[1]))
        ys, xs = np.nonzero(mask)
        if len(xs) > 200 * scale * scale:
            cx, cy = x0 + int(xs.mean()), y0 + int(ys.mean())
        else:
            cx, cy = x + int(290 * scale), y
        self.backend.click(base[0] + cx, base[1] + cy)
        stopEvent.wait(0.4)
        return self._afterRecycle(stopEvent)

    def _afterRecycle(self, stopEvent):
        # 碎片飛回托盤有動畫，等棋盤的空格不再變化才記錄(太早看會把還沒飛走的錯碎片當成放對了)
        empty, last = None, None
        for _ in range(16):
            if stopEvent.wait(0.25):  # 勤快一點看，穩定了就馬上往下，不要多等
                return True
            img, base = self._gameShot()
            state = P.findFrame(img)
            if not state or state[0] != "board":
                return True
            bx, by, bw, bh = P.innerRect(state[1])
            empty = P.emptyMap(img[by : by + bh, bx : bx + bw])
            if last is not None and (empty == last).all():
                break
            last = empty
        wrong = [(sig, r, c) for (r, c), sig in self.placedSince.items() if empty[r, c]]
        self._learnParity([(r, c) for _, r, c in self.recent if not empty[r, c]])
        self.rejected.extend(wrong)
        self.falseAlarms = self.falseAlarms + 1 if not wrong else 0
        self.setLog(f"已把放錯的碎片放回托盤（{len(wrong)} 片）")
        self.recent = []
        self.placedSince = {}
        self.suspect = set()
        return True

    def _evaluateRecent(self, stopEvent, quick=False):
        """用模板比對檢查最近放下的碎片: 棋盤上那一格的圖案和參考圖同一格一致，就是放對了(已落位，不用再管)；
        對不上的記成「放錯」，累積到一定數量才去按垃圾桶。放下沒多久的碎片可能還在動，先不判斷"""
        if not self.recent or self.reference is None:
            return
        if quick:
            stopEvent.wait(0.5)
        img, _ = self._gameShot()
        state = P.findFrame(img)
        if not state or state[0] != "board":
            return
        bx, by, bw, bh = P.innerRect(state[1])
        board = img[by : by + bh, bx : bx + bw].astype(np.float32)
        ref = cv2.resize(self.reference, (bw, bh), interpolation=cv2.INTER_AREA).astype(np.float32)
        cell = bw / P.GRID
        now = time.time()
        keep = []
        for item in self.recent:
            sig, r, c = item
            if now - self.placedAt.get((r, c), 0) < (0.3 if quick else 0.6):
                keep.append(item)
                continue
            y0, y1 = round((r + 0.25) * cell), round((r + 0.75) * cell)
            x0, x1 = round((c + 0.25) * cell), round((c + 0.75) * cell)
            rms = float(np.sqrt(((board[y0:y1, x0:x1] - ref[y0:y1, x0:x1]) ** 2).mean()))
            if rms <= GOOD_RMS:
                self._learnParity([(r, c)])  # 確定放對了，順便用它校正凸凹規律
                self.suspect.discard((r, c))
            else:
                self.suspect.add((r, c))
                keep.append(item)
        self.recent = keep

    def _boardAllEmpty(self, img, frame):
        bx, by, bw, bh = P.innerRect(frame)
        return bool(P.emptyMap(img[by : by + bh, bx : bx + bw], P.TRAY_GRID).all())

    def _probeGrid(self, img, base, frame, centers, stopEvent):
        """把托盤中間一片碎片放到棋盤中央，量它落下後的大小(棋盤一格)，推出是幾x幾片。放下去的碎片記起來，之後和其他放錯的一起清掉"""
        if not centers:
            return None
        bx, by, bw, bh = P.innerRect(frame)
        sx, sy = centers[len(centers) // 2]
        tcell = bw / P.TRAY_GRID
        before = img[by : by + bh, bx : bx + bw].copy()
        sig = self._sig(img, sx, sy, tcell)
        self.backend.drag(base[0] + sx, base[1] + sy, base[0] + bx + bw // 2, base[1] + by + bh // 2)
        if stopEvent.wait(1.0):  # 碎片落下有縮放動畫，等它停好再量
            return None
        after, _ = self._gameShot()
        afterBoard = after[by : by + bh, bx : bx + bw]
        measured = P.measureDropped(before, afterBoard, tcell)
        if measured is None:
            return None
        cellPx, mx, my = measured
        byBoard = P.measureBoardPieces(afterBoard)  # 用棋盤底色量到的結果比放前放後的差異穩定，兩者不同時以它為準
        if byBoard:
            cellPx = bw / byBoard
        grid = min(P.GRID_SIZES, key=lambda g: abs(bw / cellPx - g))  # 只有 6、8、10 三種，取最接近的
        if abs(bw / cellPx - grid) > 1.0:
            return None
        cell = bw / grid
        r, c = min(grid - 1, int(my // cell)), min(grid - 1, int(mx // cell))
        self.placed += 1
        self.recent.append((sig, r, c))
        self.placedSince[(r, c)] = sig
        return grid

    def _cacheFor(self, sig):
        """托盤上同一片碎片每一輪看到的都一樣，它對整張參考圖的比對分數只算一次"""
        for oldSig, entry in self._scoreCache:
            if float(np.abs(sig - oldSig).mean()) < 3.0:
                return entry
        entry = {}
        self._scoreCache.append((sig, entry))
        del self._scoreCache[:-60]
        return entry

    def _matchArgs(self):
        """整片比對(試驗中): 托盤碎片上下挪幾個位置各比一次；有規律時凸角的小塊也一起比。SHAPE_MATCH 關掉就回到原本只比本體"""
        if not SHAPE_MATCH:
            return {}
        args = {"dys": SHIFT_YS}
        if self.parity and not self.parityOff:
            args["parity"] = self.parity
        return args

    def _parityAdjust(self, scores, edges):
        """這個拼圖的凸凹如果是規律排列(左右兩邊同為凸或同為凹，依格子位置奇偶交錯，上下同理)，
        就把「奇偶對不上」的格子扣分。規律還沒校正出來就不動"""
        if not self.parity or self.parityOff:
            return scores
        out = scores.copy()
        rr, cc = np.indices(out.shape)
        for axis, (a, b) in (("h", ("left", "right")), ("v", ("top", "bottom"))):
            if axis not in self.parity or edges.get(a) not in ("K", "H") or edges.get(a) != edges.get(b):
                continue
            want = (rr + cc + (0 if edges[a] == "K" else 1)) % 2 == self.parity[axis]
            out = np.where(want | (out < 0), out, out - PARITY_PENALTY)
        return out

    def _learnParity(self, lockedCells):
        """放對的碎片(已落位的)放下時看到的形狀拿來投票。一開始就先假設拼圖是規律切割(預設值)，
        票數夠、而且一致，就用投票的結果(可能和預設相反)；票數夠卻不一致，就判定不是規律切割，改回原本的判斷"""
        for r, c in lockedCells:
            edges = self.edgesAt.get((r, c))
            if not edges:
                continue
            for axis, sides in (("h", ("left", "right")), ("v", ("top", "bottom"))):
                a, b = edges.get(sides[0]), edges.get(sides[1])
                # 兩邊同形，或一邊是平邊、另一邊有凸凹(邊緣、角落碎片)，都能代表這個位置的形狀
                if a in ("K", "H") and (b == a or b is None):
                    kind = a
                elif b in ("K", "H") and a is None:
                    kind = b
                else:
                    continue
                self.parityVotes[axis][(r + c + (0 if kind == "K" else 1)) % 2] += 1
        self._adoptParity()

    def _learnCorner(self, corner, edges):
        """角落的碎片一放上去，它非平邊那一邊的形狀就是這個位置該有的形狀，多算幾票"""
        r, c = corner
        for axis, sides in (("h", ("left", "right")), ("v", ("top", "bottom"))):
            kinds = [edges.get(x) for x in sides if edges.get(x) in ("K", "H")]
            if len(kinds) == 1:
                self.parityVotes[axis][(r + c + (0 if kinds[0] == "K" else 1)) % 2] += CORNER_VOTES
        self._adoptParity()

    def _adoptParity(self):
        if self.parityOff:
            return
        for axis, (n0, n1) in self.parityVotes.items():
            total = n0 + n1
            if total < PARITY_MIN_VOTES:
                continue
            if max(n0, n1) >= total * PARITY_AGREE:
                value = 0 if n0 > n1 else 1
                if self.parity.get(axis) != value:
                    self.parity[axis] = value
                    self.setLog(f"確認凸凹規律（{'左右' if axis == 'h' else '上下'}）")
            elif total >= PARITY_GIVEUP_VOTES and max(n0, n1) < total * 0.65:
                self.parity = {}
                self.parityOff = True
                self.setLog("凸凹看起來不是規律排列，改回原本的判斷")
                return

    @staticmethod
    def _sig(img, x, y, cell):
        """碎片的縮圖(只取本體中間，不含凸角與背景)，用來認出「同一片碎片」"""
        half = round(cell * 0.35)
        patch = img[max(0, y - half) : y + half, max(0, x - half) : x + half]
        return cv2.resize(patch, (24, 24), interpolation=cv2.INTER_AREA).astype(np.float32)

    def _slotIsEmpty(self, img, x, y, cell):
        patch = self._slotPatch(img, x, y, cell)
        for ox, old in self.emptySlots:
            if abs(ox - x) <= 10 and old.shape == patch.shape and float(np.abs(patch.astype(np.int16) - old.astype(np.int16)).mean()) < EMPTY_SLOT_DIFF:
                return True
        return False

    @staticmethod
    def _slotPatch(img, x, y, cell):
        half = round(cell * 0.5)
        return img[max(0, y - half) : y + half, max(0, x - half) : x + half].copy()

    # ---- 托盤 ----
    def _scrollTray(self, base, img, stopEvent):
        """在托盤上兩片碎片的正中間(碎片中心的高度)按住左右拖，看看有沒有別的碎片滑進來。
        self.sweepDir: -1 往左拖(看後面的)、+1 往右拖(回前面)。回傳托盤有沒有真的動(沒動就是到底了)"""
        H, W = img.shape[:2]
        self.emptySlots = []  # 托盤要捲動，位置都會變
        self.slotFails = {}
        slots = [x for x, _, _ in P.trayPieces(img)]
        if len(slots) < 2:
            return False
        # 往左拖就從靠右的兩片中間開始、往右拖就從靠左的，這樣拖曳的空間最大
        i = min(len(slots) - 2, max(0, len(slots) // 2 + 1)) if self.sweepDir < 0 else max(0, len(slots) // 2 - 2)
        x1 = base[0] + (slots[i] + slots[i + 1]) // 2
        y = base[1] + round(P.TRAY_Y * H)
        x2 = x1 + self.sweepDir * round((SCAN_SCROLL_PIXELS if self._scanning else SCROLL_PIXELS) * W)
        before = self._trayBand(img)
        self.backend.drag(x1, y, x2, y, slow=True)  # 慢慢拖、停一下才放開，托盤才不會滑過頭跳掉碎片
        stopEvent.wait(0.8)
        after, _ = self._gameShot()
        self._lastTray = None  # 捲動後重新等托盤穩定
        return float(np.abs(self._trayBand(after) - before).mean()) > TRAY_STABLE_DIFF
