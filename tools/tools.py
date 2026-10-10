import cv2
import mss
import numpy as np
import os
import re
import shutil
import sys
import time
from functools import lru_cache
import tkinter as tk
import win32api
import win32con
import win32gui


def getResourcePath(relativePath):
    if hasattr(sys, "frozen"):
        exeDir = os.path.dirname(sys.executable)
        return os.path.normpath(os.path.join(exeDir, "_internal", relativePath))
    else:
        return os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), relativePath))


MIN_REGION_SIZE = 10  # 框選範圍的最小寬高(像素)


class RegionTooSmallError(ValueError):
    """框選的範圍太小，或只點了一下沒有拖曳"""

    def __init__(self):
        super().__init__(f"範圍太小，請拖曳框選（至少 {MIN_REGION_SIZE}×{MIN_REGION_SIZE} 像素）")

APP_TITLE = "心動小鎮助手"


def findWindow(titlePart):
    """找標題含指定文字的可見視窗，回傳 hwnd；找不到回傳 None"""
    hits = []
    exact = []
    key = titlePart.lower()

    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).lower()
        if key not in title or win32gui.GetClassName(hwnd) == "CabinetWClass":  # 檔案總管的資料夾名稱可能含關鍵字
            return
        (exact if title == key else hits).append(hwnd)

    win32gui.EnumWindows(collect, None)
    found = exact or hits
    return found[0] if found else None


def focusWindow(hwnd):
    """把視窗拉到最前面(最小化則還原)，沒有出錯回傳 True"""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # 系統只允許前景程式切換視窗，先送一次 Alt 按鍵事件取得切換權限
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def getConfigPath():
    """設定檔位置: 打包後放在 exe 旁邊(重新打包不會蓋掉使用者設定)，從原始碼執行放在專案根目錄；
    還沒有時由預設範本 config.default.ini 複製過去。config.ini 是各人的本機設定，不納入版本控制"""
    if hasattr(sys, "frozen"):
        path = os.path.join(os.path.dirname(sys.executable), "config.ini")
    else:
        path = getResourcePath("config.ini")
    if not os.path.exists(path):
        shutil.copyfile(getResourcePath("config.default.ini"), path)
    return path


def getUserDataPath(name):
    """本機使用者資料(菜品截圖、設定檔備份)放在設定檔旁的 userdata/ 資料夾，不納入版本控制。
    舊版直接放在設定檔旁，第一次取用時順手搬進來"""
    base = os.path.dirname(getConfigPath())
    folder = os.path.join(base, "userdata")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    legacy = os.path.join(base, name)
    if not os.path.exists(path) and os.path.exists(legacy):
        shutil.move(legacy, path)
    return path


def selectRegion(hint=""):
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.config(bg="black")

    canvas = tk.Canvas(root, bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    selection = {"start": None, "end": None, "rect": None}

    if hint:
        # 全螢幕視窗有透明度，提示文字另開一個不透明的小視窗
        hintWin = tk.Toplevel(root)
        hintWin.overrideredirect(True)
        hintWin.attributes("-topmost", True)
        tk.Label(hintWin, text=hint, bg="#ffd54a", fg="black", font=("Microsoft JhengHei", 16, "bold"), padx=20, pady=10).pack()
        hintWin.update_idletasks()
        hintWin.geometry(f"+{(root.winfo_screenwidth() - hintWin.winfo_width()) // 2}+40")

    def onMouseDown(event):
        selection["start"] = (event.x, event.y)
        selection["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="red", width=2)

    def onMouseMove(event):
        if not selection["start"]:
            return

        selection["end"] = (event.x, event.y)
        canvas.coords(
            selection["rect"],
            selection["start"][0],
            selection["start"][1],
            event.x,
            event.y,
        )

    def onMouseUp(event):
        if not selection["start"]:
            return

        selection["end"] = (event.x, event.y)
        root.quit()
        root.destroy()

    canvas.bind("<ButtonPress-1>", onMouseDown)
    canvas.bind("<B1-Motion>", onMouseMove)
    canvas.bind("<ButtonRelease-1>", onMouseUp)

    def onEscape(event):
        selection["start"] = None
        selection["end"] = None
        root.quit()
        root.destroy()

    root.bind("<Escape>", onEscape)

    root.mainloop()

    if selection["start"] and selection["end"]:
        x0, y0 = selection["start"]
        x1, y1 = selection["end"]
        xMin, yMin = min(x0, x1), min(y0, y1)
        xMax, yMax = max(x0, x1), max(y0, y1)
        # 只點一下沒有拖曳會得到寬或高為 0 的區域，截圖時會出錯，要讓使用者知道並重選
        if xMax - xMin < MIN_REGION_SIZE or yMax - yMin < MIN_REGION_SIZE:
            raise RegionTooSmallError()
        return (xMin, yMin, xMax - xMin, yMax - yMin)
    else:
        return None


def captureScreen(x, y, width, height):
    if width <= 0 or height <= 0:
        raise ValueError(f"截圖範圍大小不正確 ({width}x{height})，請重新框選範圍")
    with mss.mss() as sct:
        monitor = {"top": y, "left": x, "width": width, "height": height}
        sctImg = sct.grab(monitor)
        img = np.array(sctImg)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        return img  # BGR Numpy Array


def captureFullScreen():
    """截取主螢幕，回傳 (影像, 螢幕左上角 x, y)"""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = cv2.cvtColor(np.array(sct.grab(monitor)), cv2.COLOR_RGBA2BGR)
        return img, monitor["left"], monitor["top"]


class ScaleResolver:
    """記錄模板的縮放修正值。第一次成功比對後即鎖定，之後不再嘗試其他縮放"""

    def __init__(self):
        self.factor = 1.0
        self.locked = False


def readImage(path, flags=cv2.IMREAD_COLOR):
    """讀圖。cv2.imread 在 Windows 讀不了含中文的路徑，改用 numpy 讀位元組再解碼；失敗回傳 None"""
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags)


@lru_cache(maxsize=None)
def loadTemplate(templatePath):
    """讀取模板(彩色)。模板不會變動，快取起來避免每次偵測都重讀硬碟"""
    img = readImage(getResourcePath(templatePath), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"找不到模板圖片: {templatePath}")
    return img


def bestMatchScore(screenImg, templatePath, scale):
    """回傳模板在指定縮放下的最高比對率，模板比畫面大時回傳 0"""
    screenGray = cv2.cvtColor(screenImg, cv2.COLOR_BGR2GRAY)
    template = cv2.cvtColor(cv2.resize(loadTemplate(templatePath), (0, 0), fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)
    if template.shape[0] > screenGray.shape[0] or template.shape[1] > screenGray.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(screenGray, template, cv2.TM_CCOEFF_NORMED).max())


# 圖示的底色(模板四個角落那塊)會跟著遊戲場景變，比對時要把底色遮掉，只比圖示本身。
# 例如背包鈕貼在水面或草地上，沒遮掉底色分數會掉到 0.7，遮掉後約 0.9；香水效果圖示也一樣
# 值是它在遊戲畫面中的搜尋範圍(比例): 左、上、右、下。有遮罩的比對比較慢，只看圖示會出現的那一塊
MASKED_TEMPLATES = {"bagBtn.png": (0.5, 0.0, 1.0, 0.6), "perfumeBuff.png": (0.0, 0.0, 1.0, 1.0)}


def _iconMask(template):
    """從模板四周往內填色，連到邊緣、顏色相近的區域視為底色。回傳 圖示=255、底色=0 的遮罩"""
    h, w = template.shape[:2]
    flood = np.zeros((h + 2, w + 2), np.uint8)
    work = template.copy()
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (0, h // 2), (w - 1, h // 2)]:
        cv2.floodFill(work, flood, seed, (0, 0, 0), (18, 18, 18), (18, 18, 18), cv2.FLOODFILL_MASK_ONLY | (255 << 8))
    return np.where(flood[1:-1, 1:-1] > 0, 0, 255).astype(np.uint8)


def matchBest(screenImg, templatePath, threshold, scale):
    """只取分數最高的一處，回傳中心座標；最高分未達閥值回傳 None"""
    screenGray = cv2.cvtColor(screenImg, cv2.COLOR_BGR2GRAY)
    templateColor = cv2.resize(loadTemplate(templatePath), (0, 0), fx=scale, fy=scale)
    template = cv2.cvtColor(templateColor, cv2.COLOR_BGR2GRAY)
    if template.shape[0] > screenGray.shape[0] or template.shape[1] > screenGray.shape[1]:
        return None
    mask, offX, offY = None, 0, 0
    area = MASKED_TEMPLATES.get(os.path.basename(templatePath))
    if area and "/adb/" not in str(templatePath).replace("\\", "/"):  # 手機版的圖沒驗證過，維持原本做法
        mask = _iconMask(templateColor)
        height, width = screenGray.shape
        x0, y0, x1, y1 = int(width * area[0]), int(height * area[1]), int(width * area[2]), int(height * area[3])
        region = screenGray[y0:y1, x0:x1]
        if region.shape[0] >= template.shape[0] and region.shape[1] >= template.shape[1]:
            screenGray, offX, offY = region, x0, y0
    res = cv2.matchTemplate(screenGray, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)  # 有遮罩時全平坦的區域會算出 nan
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < threshold:
        return None
    return (loc[0] + offX + template.shape[1] // 2, loc[1] + offY + template.shape[0] // 2)


def matchTemplate(screenImg, templatePath, threshold=0.8, scales=(1.0,), colorTolerance=None):
    screenGray = cv2.cvtColor(screenImg, cv2.COLOR_BGR2GRAY)
    templateOrig = loadTemplate(templatePath)

    bestPoints = []
    rectangles = []

    for scale in scales:
        template = cv2.resize(templateOrig, (0, 0), fx=scale, fy=scale)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        if template_gray.shape[0] > screenGray.shape[0] or template_gray.shape[1] > screenGray.shape[1]:
            continue  # 模板比畫面還大，無法比對
        tw, th = template_gray.shape[::-1]
        res = cv2.matchTemplate(screenGray, template_gray, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        templateMeanColor = template.reshape(-1, 3).mean(axis=0)
        for x, y in zip(*loc[::-1]):
            # 灰階比對無法分辨顏色，需要時再比對該處的平均顏色
            if colorTolerance is not None:
                patchMeanColor = screenImg[y : y + th, x : x + tw].reshape(-1, 3).mean(axis=0)
                if np.linalg.norm(patchMeanColor - templateMeanColor) > colorTolerance:
                    continue
            rectangles.append([x, y, x + tw, y + th, scale])
            bestPoints.append((x + tw // 2, y + th // 2))

    # NMS 過濾重疊點
    if rectangles:
        keep = []
        suppressed = [False] * len(rectangles)

        for i in range(len(rectangles)):
            if suppressed[i]:
                continue

            keep.append(i)
            xi1, yi1, xi2, yi2, _ = rectangles[i]
            area_i = (xi2 - xi1) * (yi2 - yi1)

            for j in range(i + 1, len(rectangles)):
                if suppressed[j]:
                    continue

                xj1, yj1, xj2, yj2, _ = rectangles[j]
                xx1 = max(xi1, xj1)
                yy1 = max(yi1, yj1)
                xx2 = min(xi2, xj2)
                yy2 = min(yi2, yj2)
                w = max(0, xx2 - xx1)
                h = max(0, yy2 - yy1)
                inter = w * h
                area_j = (xj2 - xj1) * (yj2 - yj1)
                ovr = inter / float(area_i + area_j - inter) if (area_i + area_j - inter) > 0 else 0

                if ovr > 0.3:
                    suppressed[j] = True

        filtered_points = [bestPoints[i] for i in keep]
        return filtered_points if filtered_points else None
    else:
        return None


# 各模板的預設比對閥值(0~1，越高越嚴格)。config.ini 有寫 <名稱>Threshold 就以設定檔為準
DEFAULT_THRESHOLDS = {
    "startAction": 0.65,
    "fire": 0.65,
    "completed": 0.65,
    "failedFood": 0.8,
    "timer": 0.65,
    "startCookBtn": 0.7,
    "backBtn": 0.8,
    "snowPut": 0.7,
    "snowStartBtn": 0.7,
    "snowflake": 0.7,
    "snowCompleted": 0.7,
    "bagBtn": 0.75,
    "castBtn": 0.7,
    "biteMark": 0.75,
    "usesLabel": 0.7,
    "staminaIcon": 0.7,
    "endFilterBtn": 0.9,
    "endSearchBtn": 0.9,
    "filterHeaderFishing": 0.85,
    "keepOff": 0.85,
    "toolBtn": 0.75,
    "closeBtn": 0.75,
    "cancelBtn": 0.75,
    "searchBtn": 0.75,
    "searchSubmitBtn": 0.75,
    "searchCloseBtn": 0.75,
    "filterFishing": 0.75,
    "perfumeItem": 0.75,
    "baitItem": 0.75,
    "perfumeBuff": 0.75,
    "sprayBtn": 0.75,
    "useBtn": 0.75,
    "eatBtn": 0.75,
    "repairBoxItem": 0.75,
    "castBadge": 0.7,
    "toolBroken": 0.6,
    "respawnPlant": 0.6,
    "respawnWood": 0.6,
    "respawnMushroom": 0.6,
    "filterRepair": 0.75,
    "repairBoxCell": 0.75,
}

# 整體嚴格度: 在各項閥值上統一加減，抓不到就選「寬鬆」，誤判太多就選「嚴格」
STRICTNESS_OFFSETS = {"loose": -0.1, "normal": 0.0, "strict": 0.05}

CONFIG_DEFAULTS = {
    "controlMode": "screen",
    "adbPath": r"C:\Program Files\platform-tools\adb.exe",
    "adbDevice": "127.0.0.1:5555",
    "screenResolution": "1600x900",
    "uiScale": "100",
    "gameWindowTitle": "Heartopia",
    "stopKey": "F8",
    "cookDishRecordedAt": "",
    "expectedCookQty": "50",
    "isSafeMode": "True",
    "stopAtFiveStar": "False",
    "safeModeQty": "3",
    "detectFrequency": "0.05",
    "clickFrequency": "0.05",
    "snowDetectFrequency": "0.05",
    "matchStrictness": "normal",
    "floatLogEnabled": "True",
    "floatLogAutoClose": "True",
    "floatLogPosition": "screen",
    "floatLogAutoTheme": "True",
    "floatLogThemeSeconds": "3",
    "floatLogHeight": "132",
    "fishingDetectFrequency": "0.2",
    "fishingEnabled": "True",
    "fishingAutoCast": "True",
    "fishingPerfumeSeconds": "123",
    "fishingUseBait": "True",
    "fishingBaitSeconds": "63",
    "fishingUseFood": "True",
    "fishingFoodKeepLast": "True",
    "fishingFoodBan5": "True",
    "fishingFoodName": "",
    "fishingFoodStars": "0",
    "fishingFoodMinutes": "10",
    "fishingFoodBelow": "50",
    "fishingUseRepair": "False",
    "fishingRepairNow": "False",
    "fishingRepairMinutes": "15",
    # 背景定時(模擬器)
    "fishingAltEnabled": "False",
    "fishingAltAutoCast": "False",
    "fishingAltPerfumeSeconds": "123",
    "fishingAltUseBait": "True",
    "fishingAltBaitSeconds": "63",
    "fishingAltUseFood": "False",
    "fishingAltFoodKeepLast": "True",
    "fishingAltFoodBan5": "True",
    "fishingAltFoodName": "",
    "fishingAltFoodStars": "0",
    "fishingAltFoodMinutes": "10",
    "fishingAltFoodBelow": "50",
    "fishingAltUseRepair": "False",
    "fishingAltRepairNow": "False",
    "fishingAltRepairMinutes": "15",
    # 採集(植物、砍木頭各自獨立的一組設定)
    "gatherKind": "plant",
    "plantCount": "1",
    "plantNoPickupMinutes": "10",
    "plantIntervalSeconds": "120",
    "plantUseRepair": "False",
    "plantRepairMinutes": "15",
    "woodCount": "1",
    "woodNoPickupMinutes": "10",
    "woodIntervalSeconds": "120",
    "woodUseFood": "False",
    "woodFoodKeepLast": "True",
    "woodFoodBan5": "True",
    "woodFoodName": "",
    "woodFoodStars": "0",
    "woodFoodMinutes": "10",
    "woodFoodBelow": "50",
    "woodFoodLock": "True",
    "woodUseRepair": "True",
    "woodAutoRepair": "True",
    "woodDurability": "200",
    "woodRepairMinutes": "15",
    **{f"{name}Threshold": str(value) for name, value in DEFAULT_THRESHOLDS.items()},
}

# 「還原設定值」不會動的項目: 跟使用者的環境有關(操作模式、ADB、解析度、停止鍵)，還原後還要重填很麻煩
RESET_EXCLUDED_KEYS = {"floatLogEnabled", "floatLogAutoClose", "floatLogPosition", "floatLogHeight", "controlMode", "gatherKind", "adbPath", "adbDevice", "screenResolution", "uiScale", "gameWindowTitle", "stopKey", "cookDishRecordedAt"}


def getThreshold(config, name):
    """取得模板實際使用的閥值 = 該項閥值 + 整體嚴格度的調整，並限制在合理範圍。設定值無法解析時用預設值"""
    try:
        base = float(config.get(f"{name}Threshold"))
    except (TypeError, ValueError):
        base = DEFAULT_THRESHOLDS[name]
    offset = STRICTNESS_OFFSETS.get(config.get("matchStrictness", "normal"), 0.0)
    return min(0.99, max(0.3, base + offset))


def _readConfigFile():
    config = {}
    with open(getConfigPath(), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


def fishingItemChecks(config, prefix, who):
    """釣魚定時項目的設定檢查。prefix 是設定名稱的開頭(前景定時 fishing、背景定時 fishingAlt)，who 是訊息裡的稱呼"""

    def on(key):
        return config.get(f"{prefix}{key}", "False").lower() == "true"

    def number(key, label, minimum, isInt=False):
        try:
            value = int(config.get(f"{prefix}{key}")) if isInt else float(config.get(f"{prefix}{key}"))
        except (TypeError, ValueError):
            return f"「{who}{label}」不是有效的數字"
        if value < minimum:
            return f"「{who}{label}」不能小於 {minimum}"
        return None

    checks = []
    if on("AutoCast"):
        checks.append(number("PerfumeSeconds", "香水間隔", 30, isInt=True))
    if on("UseBait"):
        checks.append(number("BaitSeconds", "誘魚器間隔", 10, isInt=True))
    if on("UseFood"):
        checks.append(number("FoodMinutes", "吃食物間隔", 0.5))
        checks.append(number("FoodBelow", "吃食物的飽食度", 1, isInt=True))
        try:
            if int(config.get(f"{prefix}FoodBelow")) > 99:
                checks.append(f"「{who}吃食物的飽食度」不能大於 99")
        except (TypeError, ValueError):
            pass
        if not config.get(f"{prefix}FoodName", "").strip():
            checks.append(f"請填寫{who}要吃的食物名稱")
        checks.append(number("FoodStars", "食物星級", 0, isInt=True))
    if on("UseRepair"):
        checks.append(number("RepairMinutes", "維修盒間隔", 0.5))
    return checks


# 維修盒回復工具耐久: 每秒 3%、持續 17 秒，約回復耐久上限的 51%。自動計算間隔時間隔剛好用完一個維修盒的回復量(耐久從滿的開始，丟下去剛好回滿，不會浪費維修盒；真的見底還有「工具耐久耗盡」的提示補救)
REPAIR_RESTORE_RATIO = 0.51
REPAIR_SAFETY = 1.0


def autoRepairMinutes(config, prefix):
    """依工具耐久上限、每輪消耗(次數 × 每次 -1)與定時時間，算出多久丟一次維修盒(分鐘)。設定不正確時回傳 None"""
    try:
        durability = float(config.get(f"{prefix}Durability"))
        count = int(config.get(f"{prefix}Count"))
        roundSeconds = float(config.get(f"{prefix}IntervalSeconds"))
    except (TypeError, ValueError):
        return None
    if durability < 1 or count < 1 or roundSeconds <= 0:
        return None
    rounds = durability * REPAIR_RESTORE_RATIO * REPAIR_SAFETY / count
    return max(1.0, round(rounds * roundSeconds / 60, 1))


# 採集功能的設定名稱開頭 -> 訊息裡的稱呼
GATHER_LABELS = {"plant": "採集植物", "wood": "砍木頭"}


def validateConfig(config, task):
    """啟動前檢查設定值，回傳錯誤訊息；沒問題回傳 None。task 為 "cooking"、"snowCarving"、"fishing"、"fishingAlt"、"plant" 或 "wood" """

    def number(key, label, minimum, isInt=False):
        try:
            value = int(config.get(key)) if isInt else float(config.get(key))
        except (TypeError, ValueError):
            return f"「{label}」不是有效的數字"
        if value < minimum:
            return f"「{label}」不能小於 {minimum}"
        return None

    checks = []
    if task == "fishingAlt":
        # 背景定時一定走模擬器，和設定頁共用 ADB 路徑與裝置位址
        if not config.get("adbPath", "").strip():
            checks.append("背景定時需要填寫「ADB 路徑」")
        if not config.get("adbDevice", "").strip():
            checks.append("背景定時需要填寫「ADB 裝置位址」")
        if not any(config.get(f"fishingAlt{k}", "False").lower() == "true" for k in ("AutoCast", "UseBait", "UseFood", "UseRepair")):
            checks.append("背景定時沒有勾選任何項目")
        checks += fishingItemChecks(config, "fishingAlt", "背景定時")
        return next((error for error in checks if error), None)

    if task == "cooking":
        checks += [
            number("expectedCookQty", "料理數量", 1, isInt=True),
            number("detectFrequency", "偵測頻率", 0.05),
            number("clickFrequency", "點擊頻率", 0.05),
        ]
        if config.get("isSafeMode", "False").lower() == "true":
            checks.append(number("safeModeQty", "安全模式的收料理數量", 1, isInt=True))
    elif task in GATHER_LABELS:
        who = GATHER_LABELS[task]
        checks.append(number(f"{task}Count", f"{who}次數", 1, isInt=True))
        checks.append(number(f"{task}IntervalSeconds", f"{who}定時時間", 10))
        checks.append(number(f"{task}NoPickupMinutes", "沒採到東西就停止的分鐘數", 0))
        try:
            limit, interval = float(config.get(f"{task}NoPickupMinutes")), float(config.get(f"{task}IntervalSeconds"))
            if 0 < limit * 60 < interval * 2:
                checks.append(f"「沒採到東西就停止的分鐘數」要大於定時時間的 2 倍（至少 {interval * 2 / 60:.1f} 分鐘），否則等下一輪的時候就會誤停")
        except (TypeError, ValueError):
            pass
        if config.get(f"{task}AutoRepair", "False").lower() == "true" and config.get(f"{task}UseRepair", "False").lower() == "true":
            checks.append(number(f"{task}Durability", "工具耐久", 1))
        checks += fishingItemChecks(config, task, who)
    elif task == "fishing":
        checks.append(number("fishingDetectFrequency", "偵測頻率", 0.1))
        checks += fishingItemChecks(config, "fishing", "")
    else:
        checks.append(number("snowDetectFrequency", "偵測頻率", 0.05))

    if config.get("controlMode", "screen").lower() == "adb":
        if not config.get("adbPath", "").strip():
            checks.append("模擬器模式需要填寫「ADB 路徑」")
        if not config.get("adbDevice", "").strip():
            checks.append("模擬器模式需要填寫「ADB 裝置位址」")
    else:
        resolution = config.get("screenResolution", "")
        if not re.fullmatch(r"\d+x\d+", resolution):
            checks.append("「遊戲解析度」格式不正確")

    return next((error for error in checks if error), None)


def _isValidLike(default, value):
    """value 的型別是否和預設值相同(數字、布林)；文字類的設定不檢查"""
    if default.lower() in ("true", "false"):
        return value.lower() in ("true", "false")
    try:
        float(default)
    except ValueError:
        return True
    try:
        float(value)
        return True
    except ValueError:
        return False


def migrateConfig():
    """啟動時整理設定檔，原則是不動使用者原本的設定:
    1. 更新後新增的設定項目，補上預設值(附加在檔案最後)
    2. 只有「衝突」才改: 已存在的值型別不對(數字被留空、布林變成別的文字)，換回預設值
    3. 動手前把原檔備份成 userdata/config.ini.bak，萬一有問題可以手動還原
    回傳被修正的項目名稱"""
    path = getConfigPath()
    current = _readConfigFile()
    added = {k: v for k, v in CONFIG_DEFAULTS.items() if k not in current}
    fixed = {k: CONFIG_DEFAULTS[k] for k, v in current.items() if k in CONFIG_DEFAULTS and not _isValidLike(CONFIG_DEFAULTS[k], v)}
    if not added and not fixed and current.get("floatLogPosition") != "game":
        return []
    shutil.copyfile(path, getUserDataPath("config.ini.bak"))
    current.update(added)
    current.update(fixed)
    if current.get("floatLogPosition") == "game":  # 已移除的選項(遊戲左下角)，改回螢幕左下角
        current["floatLogPosition"] = "screen"
    with open(path, "w", encoding="utf-8") as f:
        for k, v in current.items():
            f.write(f"{k}={v}\n")
    return list(fixed)


def readConfig():
    config = _readConfigFile()
    for key, value in CONFIG_DEFAULTS.items():
        config.setdefault(key, value)
    return config


def writeConfig(key, value):
    return writeConfigMany({key: value})


def resetConfig(keys=None, includeExcluded=False):
    """把設定還原成預設值，回傳還原後的完整設定。預設不含 RESET_EXCLUDED_KEYS(操作模式、ADB 等)，
    includeExcluded=True 時全部還原。給 keys 時只還原其中的項目(例如只還原比對閥值)"""
    writeConfigMany({k: v for k, v in CONFIG_DEFAULTS.items() if (includeExcluded or k not in RESET_EXCLUDED_KEYS) and (keys is None or k in keys)})
    return readConfig()


def writeConfigMany(values):
    config = _readConfigFile()
    config.update({k: str(v) for k, v in values.items()})

    with open(getConfigPath(), "w", encoding="utf-8") as f:
        for k, v in config.items():
            f.write(f"{k}={v}\n")

    return True


def clickMouse(x, y):
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


def clickRightMouse(x, y):
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, x, y, 0, 0)
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, x, y, 0, 0)


def getScaleWithResolution(width):
    baseWidth = 1600
    scale = width / baseWidth
    return scale
