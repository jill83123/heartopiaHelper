import cv2
from tools.tools import bestMatchScore, getScaleWithResolution, getThreshold, loadTemplate, matchBest, matchTemplate

BACK_BTN_TEMPLATE = "templates/common/backBtn.png"
START_COOK_BTN_TEMPLATE = "templates/cooking/startCookBtn.png"


# 尚未鎖定縮放時，除了依解析度算出的縮放外，額外嘗試的修正倍率
SCALE_FACTORS = [1.0, 0.9, 1.1, 0.8, 1.2, 0.7, 1.3]
# 最高分達到此值才鎖定縮放，避免被雜訊誤導
LOCK_SCORE = 0.85


def getBaseScale(backend, config):
    """模板的基準縮放: 依解析度換算；前景模式再乘上遊戲內介面縮放比例(模擬器畫面固定，不適用)"""
    scale = getScaleWithResolution(backend.getScaleWidth(config))
    if config.get("controlMode", "screen").lower() != "adb":
        try:
            scale *= float(config.get("uiScale", "100")) / 100
        except ValueError:
            pass
    return scale


def resolveScale(backend, config, img, templatePath):
    """目前使用的模板縮放。尚未鎖定縮放時，會試多個縮放倍率，若某個縮放的比對率夠高就鎖定它，之後所有模板都沿用，不再重試"""
    resolver = backend.scaleResolver
    baseScale = getBaseScale(backend, config)

    if not resolver.locked:
        scores = [(bestMatchScore(img, templatePath, baseScale * f), f) for f in SCALE_FACTORS]
        bestScore, bestFactor = max(scores)
        if bestScore >= LOCK_SCORE:
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


def clickBackButton(backend, config, topLeftOnly=False):
    """找到返回按鈕就點擊，回傳點擊的座標(螢幕座標)，沒點到回傳 None。以顏色過濾，避免誤點外觀相似的灰色按鈕。
    topLeftOnly: 只認遊戲畫面左上角的返回按鈕(食譜等全螢幕介面的返回鈕都在那裡)，
    主畫面左側的任務收合箭頭之類外觀相似的按鈕就不會被誤點"""
    threshold = getThreshold(config, "backBtn")
    coords, img, ox, oy, _ = _findInFullScreen(backend, config, BACK_BTN_TEMPLATE, threshold, colorTolerance=60)
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
