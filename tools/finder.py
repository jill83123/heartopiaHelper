import os
import time
import cv2
import numpy as np
from tools.tools import bestMatchScore, getScaleWithResolution, getThreshold, loadTemplate, matchBest, matchTemplate

BACK_BTN_TEMPLATE = "templates/common/backBtn.png"
START_COOK_BTN_TEMPLATE = "templates/cooking/startCookBtn.png"


# 尚未鎖定縮放時，除了依解析度算出的縮放外，額外嘗試的修正倍率
SCALE_FACTORS = [1.0, 0.9, 1.1, 0.8, 1.2, 0.7, 1.3]
# 最高分達到此值才鎖定縮放，避免被雜訊誤導
LOCK_SCORE = 0.85
PROBE_INTERVAL = 3  # 還沒鎖定縮放時，隔多久(秒)才再試一次各種縮放
LOCK_SCORE_OTHER = 0.93  # 縮放不是 1.0 時要更高的分數才鎖定
NO_LOCK_TEMPLATES = ("repairBoxItem.png", "castBadge.png")  # 背景稍微亂一點就會誤中，不拿來決定縮放


def getBaseScale(backend, config):
    """模板的基準縮放: 依解析度換算(遊戲內的介面縮放須為 100%)"""
    return getScaleWithResolution(backend.getScaleWidth(config))


def resolveScale(backend, config, img, templatePath):
    """目前使用的模板縮放。尚未鎖定縮放時，會試多個縮放倍率，若某個縮放的比對率夠高就鎖定它，之後所有模板都沿用，不再重試"""
    resolver = backend.scaleResolver
    baseScale = getBaseScale(backend, config)

    # 模擬器的畫面大小固定、介面也沒有縮放設定，依解析度算出的縮放就是對的；試其他倍率只會被背景誤導
    # (實測曾在主畫面誤鎖到 0.9，之後背包畫面全部認不出來)，所以模擬器模式不試
    if config.get("controlMode", "screen").lower() == "adb":
        return baseScale

    # 沒鎖定時每次都試 7 個倍率很慢(整張畫面比對 7 次，約 0.3 秒，每個模板都要)，隔幾秒試一次就夠
    if not resolver.locked and time.time() - getattr(resolver, "probedAt", 0) >= PROBE_INTERVAL:
        resolver.probedAt = time.time()
        scores = [(bestMatchScore(img, templatePath, baseScale * f), f) for f in SCALE_FACTORS]
        bestScore, bestFactor = max(scores)
        # 畫面上根本沒有這個圖時，也可能在奇怪的縮放下湊出 0.9 的分數(實測維修盒圖示 0.7 倍時 0.91)，
        # 鎖到錯的縮放整個比對都會歪掉。所以: 原本的縮放(1.0)分數夠高就直接鎖它；其他倍率要更高分才信，
        # 而且這幾個容易被背景騙的圖不拿來決定縮放
        if os.path.basename(str(templatePath)) not in NO_LOCK_TEMPLATES:
            atOne = dict((f, sc) for sc, f in scores)[1.0]
            if atOne >= LOCK_SCORE:
                bestScore, bestFactor = atOne, 1.0
            if bestScore >= (LOCK_SCORE if bestFactor == 1.0 else LOCK_SCORE_OTHER):
                resolver.factor = bestFactor
                resolver.locked = True

    return baseScale * resolver.factor


def matchAuto(backend, config, img, templatePath, threshold, colorTolerance=None):
    """依解析度縮放模板後比對"""
    return matchTemplate(img, templatePath, threshold, scales=[resolveScale(backend, config, img, templatePath)], colorTolerance=colorTolerance)


def matchBestAuto(backend, config, img, templatePath, threshold):
    """同 matchAuto，但只回傳分數最高的一處座標(沒有回傳 None)。畫面上可能有相似圖案時用這個"""
    return matchBest(img, templatePath, threshold, resolveScale(backend, config, img, templatePath))


def _findInFullScreen(backend, config, templatePath, threshold, colorTolerance=None):
    """在整個畫面找模板，回傳 (座標列表, 影像, 偏移x, 偏移y, 縮放)；座標已換算成實際點擊位置"""
    img, ox, oy = backend.captureFull()
    coords = matchAuto(backend, config, img, templatePath, threshold, colorTolerance)
    scale = getBaseScale(backend, config) * backend.scaleResolver.factor
    if not coords:
        return None, img, ox, oy, scale
    return [(ox + x, oy + y) for x, y in coords], img, ox, oy, scale


STAR_HSV_LOW = (15, 120, 200)  # 實心星星的金黃色範圍
STAR_HSV_HIGH = (35, 255, 255)


def countStarRow(backend, top=0.12):
    """數遊戲畫面右上角料理完成提示卡上，同一排金黃色實心星星的數量(空心的灰星不算)，沒看到回傳 0。
    不靠模板，直接找大小相近、排成一排的金色小方塊，不受解析度影響。top: 從畫面高度的哪個比例開始找"""
    img, ox, oy = backend.captureFull()
    rect = backend.getGameRect()  # 前景模式截的是整個螢幕，只看遊戲視窗的範圍
    if rect:
        gx, gy, gw, gh = rect[0] - ox, rect[1] - oy, rect[2], rect[3]
        img = img[max(0, gy) : gy + gh, max(0, gx) : gx + gw]
    if img.size == 0:
        return 0
    if backend.channelsSwapped:
        img = img[:, :, ::-1]
    h, w = img.shape[:2]
    x0, y0 = int(w * 0.6), int(h * top)
    roi = img[y0 : int(h * 0.4), x0:, :3]
    mask = cv2.inRange(cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV), STAR_HSV_LOW, STAR_HSV_HIGH)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    stars = []
    for i in range(1, n):
        sx, sy, sw, sh, area = stats[i]
        if h * 0.012 <= sw <= h * 0.05 and h * 0.012 <= sh <= h * 0.05 and 0.7 <= sw / sh <= 1.4 and area >= sw * sh * 0.4:
            stars.append((sy + sh / 2, sh))
    best = 0
    for cy, sh in stars:
        best = max(best, sum(1 for y, s in stars if abs(y - cy) <= sh * 0.3 and 0.7 <= s / sh <= 1.4))
    return best


def clickBackButton(backend, config, topLeftOnly=False):
    """找到返回按鈕就點擊，回傳點擊的座標(螢幕座標)，沒點到回傳 None。以顏色過濾，避免誤點外觀相似的灰色按鈕。
    topLeftOnly: 只認遊戲畫面左上角的返回按鈕(食譜等全螢幕介面的返回鈕都在那裡)，
    主畫面左側的任務收合箭頭之類外觀相似的按鈕就不會被誤點"""
    if not topLeftOnly:  # 全螢幕介面的返回鈕都在左上角(顏色各不相同)，先試這個；沒有再用顏色過濾找整個畫面
        clicked = clickBackButton(backend, config, topLeftOnly=True)
        if clicked:
            return clicked
    threshold = getThreshold(config, "backBtn")
    # 不同介面的返回鈕顏色不同(食譜是橘色、備忘錄是藍色)，所以只看左上角時不過濾顏色，靠位置排除主畫面左側的灰色任務箭頭；
    # 其他情況沒有位置限制，仍要用顏色避免誤點外觀相似的灰色按鈕
    coords, img, ox, oy, _ = _findInFullScreen(backend, config, BACK_BTN_TEMPLATE, threshold, colorTolerance=None if topLeftOnly else 60)
    if coords and topLeftOnly:
        rect = backend.getGameRect()  # 前景模式只看遊戲視窗的範圍，模擬器就是整張截圖
        gx, gy, gw, gh = (rect[0] - ox, rect[1] - oy, rect[2], rect[3]) if rect else (0, 0, img.shape[1], img.shape[0])
        coords = [c for c in coords if gx <= c[0] - ox < gx + gw * 0.4 and gy <= c[1] - oy < gy + gh * 0.15]
    if not coords:
        return None
    backend.click(*coords[0])
    return coords[0]


def findStartCookBtn(backend, config):
    """找「開始烹飪」按鈕，回傳 (x, y, 是否可按)；找不到回傳 None。
    按鈕不可按時是灰色的，以文字左側按鈕本體的飽和度判斷"""
    threshold = getThreshold(config, "startCookBtn")
    coords, img, ox, oy, scale = _findInFullScreen(backend, config, START_COOK_BTN_TEMPLATE, threshold)
    if not coords:
        return None

    x, y = coords[0]
    templateW = loadTemplate(START_COOK_BTN_TEMPLATE).shape[1] * scale
    cx, cy = x - ox, y - oy
    x0 = max(0, int(cx - templateW * 0.77))
    x1 = max(x0 + 1, int(cx - templateW * 0.59))
    patch = img[max(0, cy - 3) : cy + 4, x0:x1]
    saturation = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1].mean()
    return x, y, saturation >= 30
