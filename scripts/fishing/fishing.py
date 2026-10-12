import time
import traceback
import cv2
from scripts.common.food import FoodMixin
from scripts.common.gameui import COMMON_TEMPLATE_DIR, GAME_BASE_WIDTH, GameUI
from scripts.common.tasks import TimedTasks
from scripts.fishing.consumables import ConsumablesMixin
from scripts.fishing.newfish import NEW_FISH_CLICK_SECONDS, NewFishMixin
from tools.finder import clickBackButton
from tools.tools import getThreshold, matchBest

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
# 沒有香水時魚要自己拉: 咬鉤時浮標上方會跳出藍色「!」(約 0.7 秒)，要立刻按住 F 收線；
# 魚線顏色代表張力(白 → 黃 → 橘 → 紅)，橘色或紅色時要放開，回到黃或白再按住，否則線會斷
BITE_MARK = "biteMark"
BITE_DETECT = False  # 先不偵測浮標上方的藍色「!」(咬鉤)，要恢復改成 True
LINE_HOT_PIXELS = 900  # 橘色 + 紅色的像素數達到這個值就視為張力太大(以 1600 寬為基準，依畫面縮放換算)
REEL_POLL_SECONDS = 0.05
REEL_STATE_EVERY = 10  # 收線時每幾次偵測才確認一次是不是已經回到主畫面(確認比較花時間)
REEL_END_SECONDS = 0.8  # 離開拋竿畫面超過這麼久才當作收線結束
REEL_MAX_SECONDS = 120  # 收線最久多少秒，避免卡死
SATIETY_SAMPLE_SECONDS = 2.5  # 收竿後最多花多久讀體力條
RETRACT_WAIT_SECONDS = 4  # 收竿後最多等這麼久(秒)才回到主畫面，還沒回來才會再按一次
FACE_FRONT_HOLD = 0.6  # 按住 W 讓角色轉回正面的秒數(太短遊戲不一定吃)


class Fishing(ConsumablesMixin, NewFishMixin, FoodMixin, TimedTasks, GameUI):
    """自動釣魚: 拋竿中背包與工具欄都打不開，所以定時項目到期後要等竿子收回來(魚釣起)才執行。
    畫面狀態:
      idle    - 看得到背包圖示(沒拋竿)
      bag     - 背包開著
      casting - 只剩右下角拋竿鈕(拋竿中)
    香水與誘魚器在 consumables.py、新魚處理在 newfish.py；吃食物(common/food.py)、定時項目的排程(common/tasks.py)、畫面與背包的共用操作(common/gameui.py)和採集共用"""

    templateDir = "templates/fishing"
    # 手機版介面和 PC 版不同的圖(拋竿鈕、香水圖示)放在 templates/fishing/adb；背包、搜尋等共用按鈕在 templates/common，食物相關在 templates/food
    templateDirs = ("templates/fishing", "templates/food", COMMON_TEMPLATE_DIR)

    def __init__(self, setLog, backend, config, roleName="前景定時"):
        super().__init__(setLog, backend, config, roleName)
        self._initTasks()
        self._initFood()
        self._initConsumables()
        self._initNewFish()
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

    def _state(self, img):
        if self._find(img, "closeBtn"):
            return "bag"
        if self._findBag(img):
            return "idle"
        if self._findCast(img):
            return "casting"
        return "unknown"

    # ===== 項目 =====

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
                        self._alert("沒有香水效果，也無法噴香水，暫停釣魚（請補充香水後重新開始）")
                        self._noPerfumeLogged = True
                    continue
                if failedCasts >= MAX_FAILED_CASTS:
                    self._alert("連續拋竿失敗，請確認角色面向水面、手上拿著釣竿，且沒有開著選單")
                    failedCasts = 0
                    if stopEvent.wait(10):
                        return
                    continue
                if self._find(img, "repairBoxItem"):  # 工具欄開著會擋住拋竿，先關掉
                    self._trace("拋竿前發現工具欄開著，先關閉")
                    self.setLog("工具欄開著，先關閉")
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
                    if self._repairEnabled():
                        self.setLog("拋竿沒反應，提示工具耐久耗盡，先丟維修盒")
                        self._toolBroken = True
                    elif not self._repairOffNotified:
                        self._repairOffNotified = True
                        self._alert("工具耐久耗盡，但沒有勾選「丟維修盒」，無法繼續拋竿（請手動修復，或勾選丟維修盒）")
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
        self._walk("w", True, FACE_FRONT_HOLD)

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
