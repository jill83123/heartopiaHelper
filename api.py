import base64
import cv2
import numpy as np
import datetime
import os
import threading
import time
import webbrowser
import webview
import win32con
import win32gui
from collections import deque
from tools.adb_detect import detectAdbDevices
from tools.backend import AdbBackend, createBackend
from tools import floatlog
from tools.updater import Updater, checkUpdate, isReleasePage
from version import VERSION
from tools.tools import autoRepairMinutes, getConfigPath, getResourcePath, getUserDataPath, writeConfigMany, CONFIG_DEFAULTS, DEFAULT_THRESHOLDS, RegionTooSmallError, readConfig, resetConfig, validateConfig, writeConfig
from scripts.Cooking import Cooking
from scripts.Fishing import Fishing
from scripts.PlantGathering import PlantGathering
from scripts.WoodCutting import WoodCutting
from scripts.SnowCarving import SnowCarving


LOG_LIMIT = 500

# 背景定時與前景定時相同的那組定時項目設定(去掉 fishing 開頭的部分)
ALT_KEYS = (
    "AutoCast",
    "PerfumeSeconds",
    "UseBait",
    "BaitSeconds",
    "UseFood",
    "FoodKeepLast",
    "FoodBan5",
    "FoodName",
    "FoodStars",
    "FoodMinutes",
    "FoodBelow",
    "UseRepair",
    "RepairNow",
    "RepairMinutes",
)

# 採集功能的吃食物、丟維修盒設定(去掉功能名稱開頭的部分)，換成 Fishing 看得懂的欄位(fishing 開頭)
GATHER_FOOD_KEYS = (
    "UseFood",
    "FoodKeepLast",
    "FoodBan5",
    "FoodName",
    "FoodStars",
    "FoodMinutes",
    "FoodBelow",
    "FoodLock",
    "UseRepair",
    "RepairMinutes",
)
GATHER_TASKS = ("plant", "wood")
RUN_KEYS = ("cooking", "snowCarving", "fishing", "plant", "wood")  # 要記錄執行時間的任務(釣魚的前景、背景定時算同一個)

REGION_HINTS = {
    "bubbleRegionCoord": "請框選「料理泡泡區域」：鍋子、火、手套、計時器圖示會出現的範圍（Esc 取消）",
}


class Api:
    def __init__(self):
        self.config = readConfig()

        # cook
        self.cookingTask = None
        self.bubbleRegionCoord = None

        # snow
        self.snowCarvingTask = None

        # fishing
        self.fishingTask = None
        self.fishingAltTask = None  # 背景定時(模擬器)

        # gathering: 採集植物、砍木頭各自獨立，同時只能跑一個
        self.plantTask = None
        self.woodTask = None

        self.updater = Updater()

        # logs: 各來源一個固定長度的佇列，index 持續遞增供前端判斷已讀到哪
        self.logs = {
            "cooking": deque(maxlen=LOG_LIMIT),
            "snowCarving": deque(maxlen=LOG_LIMIT),
            "fishing": deque(maxlen=LOG_LIMIT),
            "fishingAlt": deque(maxlen=LOG_LIMIT),
            "plant": deque(maxlen=LOG_LIMIT),
            "wood": deque(maxlen=LOG_LIMIT),
            "float": deque(maxlen=LOG_LIMIT),  # 浮動日誌: 只放目前這一次運行的任務訊息
        }
        self.logCounts = {"cooking": 0, "snowCarving": 0, "fishing": 0, "fishingAlt": 0, "plant": 0, "wood": 0, "float": 0}
        self.floatSources = set()  # 這次運行有哪些任務的訊息要送進浮動日誌
        self.floatRun = 0  # 每次按「開始」加 1，浮動日誌視窗看到變了就清空
        self._logWindow = None
        self._logHwnd = None
        self._logShown = False
        self._floatKey = None  # 浮動日誌目前在看哪一個任務(RUN_KEYS 之一)
        self.runInfo = {key: {"start": None, "end": None} for key in RUN_KEYS}
        self._floatPlaced = None  # 最近一次把浮動日誌放在哪個位置，拖曳後和它比較才知道有沒有移動
        self.logLock = threading.Lock()
        self.backendError = None
        self.backendIsAdb = False
        self.selectLock = threading.Lock()  # 框選範圍中不能同時開始任務或再框選

    def setLog(self, msg, source):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        formattedMsg = f"[{now}] {msg}"
        print(formattedMsg)
        if source not in self.logs:
            return
        with self.logLock:
            self.logCounts[source] += 1
            self.logs[source].append({"index": self.logCounts[source], "msg": formattedMsg})
            if source in self.floatSources:
                # 前景與背景定時同時運行時，在訊息前標出是哪一邊
                tag = ""
                if len(self.floatSources) > 1:
                    tag = "[背景] " if source == "fishingAlt" else "[前景] "
                self.logCounts["float"] += 1
                self.logs["float"].append({"index": self.logCounts["float"], "msg": tag + formattedMsg})

    def apiGetNextLogs(self, currentIndex, source):
        """回傳 index 大於 currentIndex 的所有日誌(一次取完)"""
        if source not in self.logs:
            return []
        with self.logLock:
            return [log for log in self.logs[source] if log["index"] > currentIndex]

    # ===== 浮動日誌(置頂的小視窗) =====
    def _floatEnabled(self):
        return str(self.config.get("floatLogEnabled", "True")).lower() == "true"

    def _floatHwnd(self):
        if self._logHwnd and win32gui.IsWindow(self._logHwnd):
            return self._logHwnd
        self._logHwnd = None
        if self._logWindow is None:
            self._logWindow = webview.create_window(
                floatlog.LOG_TITLE,
                url=getResourcePath("ui/log.html"),
                js_api=self,
                width=floatlog.LOG_SIZE[0],
                height=floatlog.LOG_SIZE[1],
                min_size=(200, 20),  # pywebview 預設最小高度 100，縮小成只剩標題列(約 28)會被擋住
                frameless=True,
                easy_drag=False,  # 只有標題列(pywebview-drag-region)能拖曳視窗，其他地方留給捲動與拖拉調整高度
                on_top=True,
                focus=False,
                resizable=False,
                text_select=False,
                hidden=True,  # 先隱藏著建好，按「開始」時才不取得焦點地顯示，不會把遊戲從最上層搶走
            )
        for _ in range(100):  # 等視窗建好
            hwnd = win32gui.FindWindow(None, floatlog.LOG_TITLE)
            if hwnd:
                self._logHwnd = hwnd
                floatlog.styleOverlay(hwnd)
                if not self._logShown:
                    win32gui.ShowWindow(hwnd, 0)  # SW_HIDE，確保沒有顯示出來
                return hwnd
            time.sleep(0.05)
        return None

    def _prepareFloatLog(self):
        """程式啟動後先把浮動日誌視窗建好(隱藏)。建立視窗的當下一定會取得焦點，所以不能等到按「開始」才建"""
        if self._floatEnabled():
            try:
                self._floatHwnd()
            except Exception as e:
                print(f"浮動日誌無法建立: {e}")

    def _beginRun(self, sources):
        """按「開始」時記下開始時間，並顯示浮動日誌(只收這一次運行的任務 sources 的訊息)。浮動日誌出任何問題都不影響任務本身"""
        key = "fishing" if any(source.startswith("fishing") for source in sources) else sources[0]
        self._floatKey = key
        self.runInfo[key] = {"start": time.time(), "end": None}
        with self.logLock:
            self.floatSources = set(sources)
            self.logs["float"].clear()
            self.floatRun += 1
        if not self._floatEnabled():
            return
        try:
            hwnd = self._floatHwnd()
            if not hwnd:
                return
            collapsed = getattr(self, "_logCollapsed", False)
            self._floatPlaced = floatlog.targetPosition(hwnd, self.config.get("floatLogPosition", "screen"), self.config, collapsed)
            floatlog.placeAndShow(hwnd, *self._floatPlaced, floatlog.COLLAPSED_HEIGHT if collapsed else floatlog.heightOf(self.config))
            self._logShown = True
        except Exception as e:
            print(f"浮動日誌無法顯示: {e}")

    def _closeFloatLog(self):
        """主視窗關閉時一併關掉浮動日誌(它沒有標題列的關閉鈕，不關的話程式不會結束)"""
        try:
            if self._logWindow is not None:
                try:
                    self._logWindow.evaluate_js("markClosing()")  # 先讓頁面停止輪詢
                except Exception:
                    pass
                time.sleep(0.3)  # 等在途中的呼叫結束
                self._logWindow.destroy()
        except Exception:
            pass

    def _tasksOf(self, key):
        return {
            "cooking": (self.cookingTask,),
            "snowCarving": (self.snowCarvingTask,),
            "fishing": (self.fishingTask, self.fishingAltTask),
            "plant": (self.plantTask,),
            "wood": (self.woodTask,),
        }[key]

    def _isKeyRunning(self, key):
        return any(task and getattr(task, "isStart", False) for task in self._tasksOf(key))

    def _runSnapshot(self, key):
        """某個任務的運行時間: 是否運行中、已執行幾秒、上一次執行(開始時間與執行了幾秒，存在設定檔裡，重開程式也還在)"""
        running = self._isKeyRunning(key)
        info = self.runInfo[key]
        now = time.time()
        settingKey = "lastRun" + key[0].upper() + key[1:]
        if running:
            info["seen"] = True
        # 按「開始」到任務真正啟動之間還沒在運行，不能當成已結束(否則執行時間會卡在 0)；等看過它運行、或超過 5 秒都沒起來才算
        if info["start"] and info["end"] is None and not running and (info.get("seen") or now - info["start"] > 5):
            info["end"] = now  # 任務結束了(按停止、或發生錯誤自己停掉)，記下這次的紀錄
            record = f"{int(info['start'])},{int(info['end'] - info['start'])}"
            writeConfigMany({settingKey: record})
            self.config = readConfig()
        elapsed = None
        if info["start"]:
            elapsed = int((info["end"] or now) - info["start"])
        last = None
        try:
            parts = str(self.config.get(settingKey, "")).split(",")
            last = {"start": int(parts[0]), "seconds": int(parts[1])}
        except (ValueError, IndexError):
            pass
        return {"running": running, "elapsed": elapsed, "last": last}

    def _floatLuma(self):
        """浮動日誌後面畫面的亮度，依設定的間隔秒數量測；關閉「依背景切換顏色」時不量(回傳 None)"""
        if str(self.config.get("floatLogAutoTheme", "True")).lower() != "true":
            return None
        try:
            interval = max(1.0, float(self.config.get("floatLogThemeSeconds", "3")))
        except ValueError:
            interval = 3.0
        now = time.time()
        if now - getattr(self, "_lumaAt", 0) >= interval and self._logHwnd and win32gui.IsWindow(self._logHwnd) and win32gui.IsWindowVisible(self._logHwnd):
            self._lumaAt = now
            self._luma = floatlog.backdropLuma(self._logHwnd)
        return getattr(self, "_luma", None)

    def apiFloatPoll(self, index, runId):
        snapshot = self._runSnapshot(self._floatKey) if self._floatKey else {"running": self.isRunning(), "elapsed": None}
        with self.logLock:
            if runId != self.floatRun:
                index = 0  # 新的一次運行，從頭給
            logs = [log for log in self.logs["float"] if log["index"] > index]
            return {"run": self.floatRun, "logs": logs, "running": snapshot["running"], "elapsed": snapshot["elapsed"], "stopKey": str(self.config.get("stopKey", "")), "position": str(self.config.get("floatLogPosition", "screen")), "autoClose": str(self.config.get("floatLogAutoClose", "True")).lower() == "true", "luma": self._floatLuma()}

    def apiHideFloatLog(self):
        if self._logHwnd and win32gui.IsWindow(self._logHwnd):
            win32gui.ShowWindow(self._logHwnd, 0)  # SW_HIDE
        self._logShown = False
        return True

    def apiSetFloatCollapsed(self, collapsed):
        """浮動日誌縮小成只剩標題列，或還原"""
        self._logCollapsed = bool(collapsed)
        if self._logHwnd and win32gui.IsWindow(self._logHwnd):
            height = floatlog.COLLAPSED_HEIGHT if self._logCollapsed else floatlog.heightOf(self.config)
            self._floatPlaced = floatlog.setHeight(self._logHwnd, height, "bottom")  # 下緣不動，位置不算被使用者拖曳
        return True

    def apiResizeFloat(self, height, anchor="bottom"):
        """拖拉調整浮動日誌的高度(縮小成標題列時不處理)。anchor 是不動的那一邊: bottom(下緣)或 top(上緣)"""
        if getattr(self, "_logCollapsed", False) or not (self._logHwnd and win32gui.IsWindow(self._logHwnd)):
            return False
        height = min(floatlog.MAX_HEIGHT, max(floatlog.MIN_HEIGHT, int(height)))
        self.config["floatLogHeight"] = str(height)
        self._floatPlaced = floatlog.setHeight(self._logHwnd, height, anchor)  # 位置不算被使用者拖曳
        return True

    def apiSaveFloatHeight(self):
        """拖拉結束後記住高度"""
        writeConfigMany({"floatLogHeight": self.config.get("floatLogHeight", str(floatlog.LOG_SIZE[1]))})
        return True

    def apiSaveFloatLogPos(self):
        """使用者拖曳浮動日誌後，記住新位置並切到「自訂」位置"""
        if not (self._logHwnd and win32gui.IsWindow(self._logHwnd)):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(self._logHwnd)
        x, y = floatlog.clampToScreen(left, top, right - left, bottom - top)  # 拖到畫面外就彈回可視範圍
        if (x, y) != (left, top):
            win32gui.SetWindowPos(self._logHwnd, win32con.HWND_TOPMOST, x, y, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            left, top = x, y
        if self._floatPlaced and (left, top) == tuple(self._floatPlaced):
            return False
        self._floatPlaced = (left, top)
        writeConfigMany({"floatLogX": left, "floatLogY": top, "floatLogPosition": "custom"})
        self.config = readConfig()
        return True

    def taskConfig(self, task):
        """各任務實際使用的操作模式: 料理、雪雕、採集植物、砍木頭共用設定的 controlMode；釣魚的前景定時固定走前景模式，背景定時固定走模擬器"""
        config = dict(self.config)
        if task == "fishingAlt":
            config["controlMode"] = "adb"
        elif task == "fishing":
            config["controlMode"] = "screen"
        return config

    def getBackend(self, source, task=None):
        self.backendError = None
        self.backendIsAdb = False
        try:
            config = self.taskConfig(task or source)
            self.backendIsAdb = config.get("controlMode") == "adb"
            backend = createBackend(config)
            self._checkBackend(backend, source)
            return backend
        except Exception as e:
            self.backendError = (str(e).strip().splitlines() or [type(e).__name__])[0]
            self.setLog(f"❌ 無法初始化操作模式：{e}", source)
            return None

    def _checkBackend(self, backend, source):
        """連線模擬器可能要等幾秒，先寫一行日誌讓使用者知道有反應"""
        if getattr(backend, "isAdb", False):
            self.setLog("模擬器連線中...", source)
        backend.check()

    def apiDetectAdb(self):
        """自動偵測執行中的模擬器。回傳 status: success(附 devices: [{address, name}]) 或 failed(附 message)"""
        try:
            return {"status": "success", "devices": detectAdbDevices(self.config.get("adbPath", "adb"))}
        except Exception as e:
            return {"status": "failed", "message": str(e)}

    def backendFailure(self):
        """getBackend 失敗時附在回傳值裡的標記: 連線模擬器(ADB)失敗時前端會跳出排查提示窗"""
        return {"code": "adbFailed"} if self.backendIsAdb else {}

    def backendErrorText(self):
        """getBackend 失敗時給使用者看的說明(含實際原因，例如模擬器沒開)"""
        return f"無法初始化操作模式：{self.backendError}" if self.backendError else "無法初始化操作模式，詳見日誌"

    def apiSelectRegion(self, name):
        """框選範圍。回傳 status: success(附 selection)、cancelled(按 Esc 取消)、failed(附 message 說明原因)。
        失敗或取消時不會動到先前已選好的範圍"""
        if self.isRunning():
            return {"status": "failed", "message": "運行中無法選取範圍"}
        # 同時只能有一個框選視窗，避免連點兩下開出兩個
        if not self.selectLock.acquire(blocking=False):
            return {"status": "failed", "message": "正在選取範圍中"}
        try:
            backend = self.getBackend("cooking")
            if not backend:
                return {"status": "failed", "message": self.backendErrorText(), **self.backendFailure()}
            try:
                coords = backend.selectRegion(REGION_HINTS.get(name, "請拖曳框選區域（Esc 取消）"))
            except RegionTooSmallError as e:
                return {"status": "failed", "message": str(e)}
        finally:
            self.selectLock.release()

        if not coords:
            return {"status": "cancelled"}

        setattr(self, name, coords)

        return {
            "status": "success",
            "selection": {
                "x": coords[0],
                "y": coords[1],
                "w": coords[2],
                "h": coords[3],
            },
        }

    def _dishImagePath(self):
        """菜品記錄圖放在設定檔旁的 userdata/(更新程式不會動到)"""
        return getUserDataPath("dish.png")

    def apiGetDishImage(self):
        """已記錄的菜名截圖(data URL)，還沒記錄回傳 None"""
        path = self._dishImagePath()
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")

    def apiClearDish(self):
        """清除已記錄的菜品(刪掉截圖與記錄時間)"""
        if self.isRunning():
            return False
        try:
            os.remove(self._dishImagePath())
        except FileNotFoundError:
            pass
        writeConfig("cookDishRecordedAt", "")
        self.apiReadSettings()
        return True

    def apiRecordDish(self):
        """框選遊戲裡目前菜品的名稱，截圖存起來。回傳 status 同 apiSelectRegion，成功時附 image 與 recordedAt"""
        if self.isRunning():
            return {"status": "failed", "message": "運行中無法記錄"}
        if not self.selectLock.acquire(blocking=False):
            return {"status": "failed", "message": "正在選取範圍中"}
        try:
            backend = self.getBackend("cooking")
            if not backend:
                return {"status": "failed", "message": self.backendErrorText(), **self.backendFailure()}
            try:
                coords = backend.selectRegion("請框選遊戲裡目前要煮的「菜品名稱」（Esc 取消）")
            except RegionTooSmallError as e:
                return {"status": "failed", "message": str(e)}
            if not coords:
                return {"status": "cancelled"}
            x, y, w, h = coords
            img = backend.capture(x, y, w, h)
            if backend.channelsSwapped:
                img = np.ascontiguousarray(img[:, :, ::-1])
            ok, buf = cv2.imencode(".png", img[:, :, :3])
            if not ok:
                return {"status": "failed", "message": "無法儲存截圖"}
            with open(self._dishImagePath(), "wb") as f:
                f.write(buf.tobytes())
        finally:
            self.selectLock.release()
        recordedAt = datetime.datetime.now().strftime("%m/%d %H:%M")
        writeConfig("cookDishRecordedAt", recordedAt)
        self.apiReadSettings()
        return {"status": "success", "image": self.apiGetDishImage(), "recordedAt": recordedAt}

    def apiReadSettings(self):
        self.config = readConfig()
        return self.config

    def apiResetSettings(self):
        if self.isRunning():
            return None
        self.config = resetConfig(includeExcluded=True)  # 全部還原，連操作模式、ADB、解析度、停止按鍵都一起
        self.apiClearDish()
        return self.config

    def apiResetThresholds(self):
        """只把各項目的比對閥值還原成預設值(進階區塊的一鍵重設)，其他設定不動"""
        if self.isRunning():
            return None
        self.config = resetConfig({f"{name}Threshold" for name in DEFAULT_THRESHOLDS})
        return self.config

    def apiGetTemplateImages(self):
        """回傳所有比對模板的圖片，{檔名(不含副檔名): data URL}，讓介面在設定旁邊顯示小圖。
        介面是用本機小型網頁伺服器載入的，讀不到 ui 資料夾以外的檔案，所以經由 API 傳圖。
        同名的以電腦版(非 adb 子資料夾)的為準"""
        if getattr(self, "_templateImages", None) is None:
            images = {}
            root = getResourcePath("templates")
            for folder, _, files in sorted(os.walk(root), key=lambda item: os.path.basename(item[0]) == "adb"):
                for file in sorted(files):
                    name, ext = os.path.splitext(file)
                    if ext.lower() != ".png" or name in images:
                        continue
                    with open(os.path.join(folder, file), "rb") as f:
                        images[name] = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
            self._templateImages = images
        return self._templateImages

    def apiGetRepairMinutes(self, kind):
        """採集功能自動計算的維修盒間隔(分鐘)，讓介面顯示"""
        return autoRepairMinutes(self.config, kind) if kind in GATHER_TASKS else None

    def apiGetDefaults(self):
        """所有設定的預設值，讓介面在欄位旁邊顯示並可一鍵還原"""
        return dict(CONFIG_DEFAULTS)

    def apiGetVersion(self):
        return VERSION

    def apiCheckUpdate(self):
        return checkUpdate()

    def apiOpenReleasePage(self, url):
        """用系統瀏覽器開啟本儲存庫的 Release 頁面，其他網址一律拒絕"""
        if not isReleasePage(url):
            return False
        webbrowser.open(url)
        return True

    def apiOpenLink(self, url):
        """用系統瀏覽器開啟更新說明裡的連結，只接受 http(s)"""
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return False
        webbrowser.open(url)
        return True

    def apiInstallUpdate(self):
        if self.isRunning():
            return {"ok": False, "error": "請先停止運行中的任務再更新"}
        error = self.updater.start()
        return {"ok": error is None, "error": error}

    def apiUpdateState(self):
        return self.updater.getState()

    def apiApplyUpdate(self):
        """更新檔準備好後，啟動替換腳本並結束程式"""
        if not self.updater.applyAndExit():
            return False
        threading.Timer(0.5, os._exit, [0]).start()
        return True

    def isRunning(self):
        return any(task and task.isStart for task in (self.cookingTask, self.snowCarvingTask, self.fishingTask, self.fishingAltTask, self.plantTask, self.woodTask))

    def apiWriteSetting(self, key, value):
        writeConfig(key, value)
        self.apiReadSettings()
        return True

    def startCooking(self):
        if self.selectLock.locked():
            return {"ok": False, "error": "正在選取範圍，請先完成選取"}
        def cookingLog(msg):
            self.setLog(msg, "cooking")

        config = self.taskConfig("cooking")
        error = validateConfig(config, "cooking")
        if error:
            return {"ok": False, "error": error}
        backend = self.getBackend("cooking")
        if not backend:
            return {"ok": False, "error": self.backendErrorText(), **self.backendFailure()}

        self._beginRun(["cooking"])
        self.cookingTask = Cooking(cookingLog, backend, config, self.bubbleRegionCoord)
        self.cookingTask.start()
        return {"ok": True}

    def startSnowCarving(self):
        if self.selectLock.locked():
            return {"ok": False, "error": "正在選取範圍，請先完成選取"}
        def snowLog(msg):
            self.setLog(msg, "snowCarving")

        config = self.taskConfig("snowCarving")
        error = validateConfig(config, "snowCarving")
        if error:
            return {"ok": False, "error": error}
        backend = self.getBackend("snowCarving")
        if not backend:
            return {"ok": False, "error": self.backendErrorText(), **self.backendFailure()}

        self._beginRun(["snowCarving"])
        self.snowCarvingTask = SnowCarving(snowLog, backend, config)
        self.snowCarvingTask.start()
        return {"ok": True}

    def _altConfig(self):
        """背景定時有自己的一組定時項目設定(fishingAlt 開頭)，換成 Fishing 看得懂的欄位(fishing 開頭)"""
        config = self.taskConfig("fishingAlt")
        for key in ALT_KEYS:
            config[f"fishing{key}"] = self.config.get(f"fishingAlt{key}", "")
        return config

    def startFishing(self):
        if self.selectLock.locked():
            return {"ok": False, "error": "正在選取範圍，請先完成選取"}
        def fishingLog(msg):
            self.setLog(msg, "fishing")

        def altLog(msg):
            self.setLog(msg, "fishingAlt")

        useMain = str(self.config.get("fishingEnabled", "True")).lower() == "true"
        useAlt = str(self.config.get("fishingAltEnabled", "False")).lower() == "true"
        if not useMain and not useAlt:
            return {"ok": False, "error": "請至少啟用前景定時或背景定時"}
        mainConfig = self.taskConfig("fishing")
        error = (validateConfig(mainConfig, "fishing") if useMain else None) or (
            validateConfig(self.taskConfig("fishingAlt"), "fishingAlt") if useAlt else None
        )
        if error:
            return {"ok": False, "error": error}

        backend = None
        if useMain:
            backend = self.getBackend("fishing")
            if not backend:
                return {"ok": False, "error": self.backendErrorText(), **self.backendFailure()}
        altBackend = None
        if useAlt:
            try:
                altBackend = AdbBackend(self.config.get("adbPath", "adb"), self.config.get("adbDevice", ""))
                self._checkBackend(altBackend, "fishingAlt")
            except Exception as e:
                self.setLog(f"❌ 背景定時無法連線模擬器：{e}", "fishingAlt")
                return {"ok": False, "error": f"背景定時無法連線模擬器: {e}", "code": "adbFailed"}

        self._beginRun((["fishing"] if backend else []) + (["fishingAlt"] if altBackend else []))
        if backend:
            self.fishingTask = Fishing(fishingLog, backend, mainConfig, "前景定時")
            self.fishingTask.start()
        if altBackend:
            self.fishingAltTask = Fishing(altLog, altBackend, self._altConfig(), "背景定時")
            self.fishingAltTask.start()
        return {"ok": True}

    def _gatherConfig(self, kind):
        """採集功能有自己的一組吃食物設定(plant / wood 開頭)，換成 Fishing 看得懂的欄位(fishing 開頭)，拋竿、誘魚器一律關閉"""
        config = self.taskConfig(kind)
        for key in GATHER_FOOD_KEYS:
            # 該功能沒有這個設定(例如採集植物沒有吃食物)或是空白時，用釣魚的預設值，避免後面轉數字出錯
            value = str(self.config.get(f"{kind}{key}", "")).strip()
            config[f"fishing{key}"] = value or CONFIG_DEFAULTS.get(f"fishing{key}", "")
        if kind == "plant":
            config["fishingUseFood"] = "False"  # 無工具的採集不用吃食物(以前存下的設定也不再生效)
        if str(config.get(f"{kind}AutoRepair", "False")).lower() == "true":
            minutes = autoRepairMinutes(config, kind)
            if minutes is not None:
                config["fishingRepairMinutes"] = str(minutes)
        for key in ("AutoCast", "UseBait", "RepairNow"):
            config[f"fishing{key}"] = "False"
        return config

    def startGathering(self, kind):
        """kind: "plant"(採集植物) 或 "wood"(砍木頭)。兩個功能互相獨立，同時只能跑一個"""
        if kind not in GATHER_TASKS:
            return {"ok": False, "error": "不明的採集類型"}
        if self.selectLock.locked():
            return {"ok": False, "error": "正在選取範圍，請先完成選取"}
        if self.isRunning():
            return {"ok": False, "error": "已有任務運行中，採集植物與砍木頭只能擇一使用，請先停止"}

        def gatherLog(msg):
            self.setLog(msg, kind)

        config = self._gatherConfig(kind)
        error = validateConfig(config, kind)
        if error:
            return {"ok": False, "error": error}
        backend = self.getBackend(kind)
        if not backend:
            return {"ok": False, "error": self.backendErrorText(), **self.backendFailure()}

        self._beginRun([kind])
        if kind == "plant":
            self.plantTask = PlantGathering(gatherLog, backend, config)
            self.plantTask.start()
        else:
            self.woodTask = WoodCutting(gatherLog, backend, config)
            self.woodTask.start()
        return {"ok": True}

    def stop(self):
        if self.plantTask:
            self.plantTask.stop()
            self.plantTask = None

        if self.woodTask:
            self.woodTask.stop()
            self.woodTask = None

        if self.fishingAltTask:
            self.fishingAltTask.stop()
            self.fishingAltTask = None

        if self.fishingTask:
            self.fishingTask.stop()
            self.fishingTask = None

        if self.cookingTask:
            self.cookingTask.stop()
            self.cookingTask = None

        if self.snowCarvingTask:
            self.snowCarvingTask.stop()
            self.snowCarvingTask = None

    def apiGetStatus(self):
        status = {"cooking": False, "snowCarving": False, "fishing": False, "plant": False, "wood": False}
        status["runs"] = {key: self._runSnapshot(key) for key in RUN_KEYS}
        status["floatLogPosition"] = self.config.get("floatLogPosition", "screen")  # 拖曳浮動日誌會自動切到「自訂」，讓設定頁跟著變

        if self.plantTask and getattr(self.plantTask, "isStart", False):
            status["plant"] = True

        if self.woodTask and getattr(self.woodTask, "isStart", False):
            status["wood"] = True

        if self.cookingTask and getattr(self.cookingTask, "isStart", False):
            status["cooking"] = True

        if self.snowCarvingTask and getattr(self.snowCarvingTask, "isStart", False):
            status["snowCarving"] = True

        if any(task and getattr(task, "isStart", False) for task in (self.fishingTask, self.fishingAltTask)):
            status["fishing"] = True

        return status
