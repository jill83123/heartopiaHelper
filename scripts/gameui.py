"""遊戲畫面的共用操作: 截圖、模板比對、點擊、背包的開關與搜尋、除錯存圖。
各功能(釣魚、採集...)繼承這個類別，不用各自再寫一次。
模板圖的找法: 依 templateDirs 的順序找；各功能自己的圖放在自己的資料夾，大家共用的按鈕(背包、搜尋、返回...)放在 templates/common，
手機版介面和電腦版不同的圖放在各資料夾底下的 adb/，模擬器模式會先找那裡"""

import os
import random
import time
import cv2
import numpy as np
from scripts.base import BaseTask
from tools.finder import getBaseScale, matchBestAuto, resolveScale
from tools.debug import DebugMixin
from tools.notify import notify
from tools.tools import getResourcePath, getThreshold, loadTemplate, matchBest

COMMON_TEMPLATE_DIR = "templates/common"
BAG_OPEN_CHECKS = 12  # 點了背包圖示後，最多看幾次(每次隔 0.6 秒)背包有沒有打開；模擬器卡的時候要等比較久
BAG_REOPEN_AT = (3, 7)  # 第幾次看還是主畫面時，再點一次背包圖示
UI_WAIT_SECONDS = 4  # 等待某個畫面/按鈕出現的上限(秒)
ADB_PRESS_MS = 0  # 模擬器點擊要按住的時間
CLICK_OFFSET = 3  # 點擊位置隨機偏移(像素)
HOVER_DELAY_RANGE = (0.2, 0.45)  # 移到按鈕上到點下去之間的停頓(秒)
WAKE_X = 0.5
WAKE_Y = 0.12
WAKE_JITTER = 0.08  # 位置再隨機偏移的比例
TOAST_BASE_WIDTH = 1250  # toolBroken.png 取自這個寬度的遊戲畫面
# 右下角動作鈕(電腦版對應 F 鍵、手機版是拋竿/互動鈕)在遊戲畫面裡的固定位置(以 1600 寬為基準): 中心離右邊、下邊的距離
GAME_BASE_WIDTH = 1600
ACTION_FROM_RIGHT = 224
ACTION_FROM_BOTTOM = 212

_templatePaths = {}  # (資料夾, 模板名稱, 是否模擬器) -> 找到的模板路徑


class GameUI(DebugMixin, BaseTask):
    """遊戲畫面的共用操作。子類別用 templateDirs 指定找模板的資料夾(自己的放前面)。
    畫面狀態: idle(看得到背包圖示)、bag(背包開著)、unknown(其他)"""

    templateDirs = (COMMON_TEMPLATE_DIR,)

    def __init__(self, setLog, backend, config, roleName="前景定時"):
        super().__init__(setLog, backend, config)
        baseSetLog = self.setLog

        def tracedSetLog(msg, *args, **kwargs):  # 日誌視窗顯示的每一行也記進 flow.log，才看得出介面上實際顯示了什麼
            self._trace(f"【日誌】{msg}")
            return baseSetLog(msg, *args, **kwargs)

        self.setLog = tracedSetLog
        self.roleName = roleName  # 通知與日誌裡用來分辨是哪個帳號
        self.isAdb = getattr(backend, "isAdb", False)
        if self.isAdb:
            backend.pressMs = ADB_PRESS_MS
        self._origin = (0, 0)  # 目前這張截圖左上角在螢幕上的位置

    def _templatePath(self, name):
        """依 templateDirs 的順序找模板，每個資料夾模擬器模式先找 adb/ 底下手機版的圖"""
        key = (self.templateDirs, name, self.isAdb)
        if key not in _templatePaths:
            candidates = []
            for folder in self.templateDirs:
                if self.isAdb:
                    candidates.append(f"{folder}/adb/{name}.png")
                candidates.append(f"{folder}/{name}.png")
            _templatePaths[key] = next((p for p in candidates if os.path.exists(getResourcePath(p))), candidates[-1])
        return _templatePaths[key]

    def _actionCenter(self, img):
        """右下角動作鈕的中心(影像座標)與縮放比例。電腦版與手機版的位置相同: 貼著遊戲畫面右下角固定的距離"""
        x, y, w, h = self._gameRect(img)
        s = w / GAME_BASE_WIDTH
        return (x + w - ACTION_FROM_RIGHT * s, y + h - ACTION_FROM_BOTTOM * s), s

    def _state(self, img):
        if self._find(img, "closeBtn"):
            return "bag"
        if self._findBag(img):
            return "idle"
        return "unknown"

    def _bagView(self, img):
        """背包目前停在哪個畫面: result(篩選或搜尋結果)、full(完整清單，有搜尋圖示)，都不是回傳 None"""
        if self._find(img, "endFilterBtn") or self._find(img, "endSearchBtn"):
            return "result"
        if self._find(img, "searchBtn"):
            return "full"
        return None

    def _alert(self, msg, flash=True):
        """需要使用者處理的狀況: 寫進日誌，也跳出桌面通知。flash: 要不要讓工作列圖示閃爍(任務已經停了才需要)"""
        self.setLog(f"⚠️ {msg}")
        notify(f"心動小鎮助手｜{self.roleName}", msg, flash=flash)

    def _flag(self, key):
        return str(self.config.get(key, "False")).lower() == "true"

    def _shot(self):
        img, ox, oy = self.backend.captureFull()
        self._origin = (ox, oy)
        if self.backend.channelsSwapped:
            img = np.ascontiguousarray(img[:, :, ::-1])  # 還原成真正的 BGR，顏色判斷才正確
        return img

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
        # 手機版(模擬器)的提示字是半透明的，亮度只有約 223，達不到電腦版的 225，門檻要放低
        white = ((hsv[:, :, 1] <= 60) & (hsv[:, :, 2] >= (205 if self.isAdb else 225))).astype(np.uint8) * 255
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

    def _findColor(self, img, name):
        """用彩色比對模板(紅色鎖頭和灰色開鎖形狀一樣，只差顏色，轉灰階會分不出來)，回傳座標，沒有回傳 None"""
        template = loadTemplate(self._templatePath(name))
        s = self._scale()
        template = cv2.resize(template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if template.shape[0] > img.shape[0] or template.shape[1] > img.shape[1]:
            return None
        _, score, _, loc = cv2.minMaxLoc(np.nan_to_num(cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)))
        if score < getThreshold(self.config, name):
            return None
        return loc[0] + template.shape[1] // 2, loc[1] + template.shape[0] // 2

    def _wakeMouse(self, stopEvent):
        """左右鍵各點兩下解除卡滑鼠(轉視角)。點在遊戲畫面上方中間的空白處"""
        x, y, w, h = self._gameRect(self._shot())
        wx = x + self._origin[0] + w * (WAKE_X + random.uniform(-WAKE_JITTER, WAKE_JITTER))
        wy = y + self._origin[1] + h * (WAKE_Y + random.uniform(-WAKE_JITTER, WAKE_JITTER))
        self.backend.wakeMouse(round(wx), round(wy))
        return not stopEvent.wait(0.3)

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
        for i in range(BAG_OPEN_CHECKS):
            if stopEvent.wait(0.6):
                return None, None
            img = self._shot()
            view = self._bagView(img)
            if view is None and i in BAG_REOPEN_AT and self._find(img, "bagBtn"):
                # 還在主畫面(背包圖示還看得到)就是點擊沒開成功或畫面太卡，再點一次；背包圖示看不到代表正在打開，不能再點
                self._trace("背包沒有打開，再點一次背包圖示")
                if not self._click(self._find(img, "bagBtn"), stopEvent):
                    return None, None
                continue
            if view == "fishing" and wanted == "fishing":
                return img, view
            if view in ("fishing", "result"):
                if not self._endResult(img, stopEvent):
                    return None, None
                continue
            if view == "full":
                return img, view
        resolver = self.backend.scaleResolver
        self._trace(f"背包畫面認不出來：縮放倍率 {resolver.factor}（{'已鎖定' if resolver.locked else '未鎖定'}）")
        self._saveShot(self._shot(), "bagview")
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
