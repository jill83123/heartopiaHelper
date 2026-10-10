import os
import cv2
import numpy as np
from tools.tools import getResourcePath, readImage

# 背包格子右下角的數量(例如「142」)。字是灰紫色，用色相和飽和度挑出來，木箱等圖案的橘褐色邊緣不會混進來。
# 每個數字是一塊連通的像素；字與字偶爾會黏在一起(例如斜體的 1 和 4)，黏在一起的會依寬度從最細的地方切開。
# 切出來的字依整串數字共同的高度縮放後，和 templates/digits 裡的字形比對
DIGIT_PITCH = 10  # 一個數字大約多寬(像素，1600 寬為基準)，用來判斷黏在一起的字有幾個
MIN_DIGIT_HEIGHT = 10  # 比這個矮的像素塊是雜點
OUT_SIZE = (14, 20)  # 比對前每個字的 寬、高
MIN_SCORE = 0.75


def countMask(region):
    """挑出數量文字的像素(灰紫色)"""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return ((hue >= 140) & (hue <= 185) & (sat >= 8) & (sat <= 70) & (val >= 90) & (val <= 215)).astype(np.uint8)


BG_VALUE = 250  # 格子底色的亮度
INK_VALUE = 165  # 數字中心的亮度


def _normalize(piece, height):
    """把一個字(0~1 的暗度圖，已含整串數字共同的上下範圍)縮放成固定大小，水平置中"""
    factor = OUT_SIZE[1] / height
    width = min(OUT_SIZE[0], max(1, round(piece.shape[1] * factor)))
    resized = cv2.resize(piece, (width, OUT_SIZE[1]), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((OUT_SIZE[1], OUT_SIZE[0]), np.float32)
    left = (OUT_SIZE[0] - width) // 2
    canvas[:, left : left + width] = resized
    return canvas


def digitSlots(region, scale=1.0):
    """把數量區域(BGR 圖)切成每個數字一塊，回傳 由左到右 的正規化字形(越暗越接近 1)。
    用整塊的暗度而不是單純有字/沒字，邊緣的深淺也算進去，6、8、3 這類相像的字才分得開"""
    mask = countMask(region)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    # 縮小後一個字的筆畫常斷成幾塊(5 的上下、0 的左右)，水平範圍重疊或緊貼的碎塊併成同一個字，併完再用高度濾掉雜點。
    # 更小的點(面積 2~3，例如 7 的斜筆畫斷掉的部分)只有緊貼著某個字才併進去，不然是背景的雜點
    big = sorted((i for i in range(1, count) if stats[i][4] >= 4), key=lambda i: stats[i][0])
    groups = []  # [左, 右(不含), 上, 下(不含), [label...]]
    for i in big:
        x, y, w, h = (int(v) for v in stats[i][:4])
        if groups and x <= groups[-1][1]:
            g = groups[-1]
            g[1], g[2], g[3] = max(g[1], x + w), min(g[2], y), max(g[3], y + h)
            g[4].append(i)
        else:
            groups.append([x, x + w, y, y + h, [i]])
    for i in range(1, count):
        if stats[i][4] >= 4 or stats[i][4] < 2:
            continue
        x, y, w, h = (int(v) for v in stats[i][:4])
        for g in groups:
            if x < g[1] and x + w > g[0] and y >= g[2] - 3 and y + h <= g[3] + 3:
                g[2], g[3] = min(g[2], y), max(g[3], y + h)
                g[4].append(i)
                break
    parts = [(g[4], np.array([g[0], g[2], g[1] - g[0], g[3] - g[2]])) for g in groups if g[3] - g[2] >= MIN_DIGIT_HEIGHT * scale]
    if not parts:
        return []
    top = min(int(st[1]) for _, st in parts)
    bottom = max(int(st[1] + st[3]) for _, st in parts)
    value = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
    dark = np.clip((BG_VALUE - value) / (BG_VALUE - INK_VALUE), 0, 1)
    slots = []
    for i, st in sorted(parts, key=lambda item: item[1][0]):
        x, w = int(st[0]), int(st[2])
        component = np.isin(labels, i).astype(np.uint8)
        grown = cv2.dilate(component, np.ones((3, 3), np.uint8))  # 多含一圈邊緣的深淺
        left = max(0, x - 1)
        block = (dark * grown)[top:bottom, left : x + w + 1]
        solid = component[top:bottom, left : x + w + 1]
        pieces = max(1, round(w / (DIGIT_PITCH * scale)))
        cuts = [0]
        for k in range(1, pieces):  # 黏在一起: 在預期的位置附近找墨水最少的那一欄切開
            expected = round(w * k / pieces)
            lo, hi = max(cuts[-1] + 1, expected - 3), min(w - 1, expected + 3)
            cuts.append(lo + int(np.argmin(solid.sum(axis=0)[lo : hi + 1])) if hi >= lo else expected)
        cuts.append(block.shape[1])
        for a, b in zip(cuts, cuts[1:]):
            cols = np.where(solid[:, a:b].any(axis=0))[0]
            if len(cols) == 0:
                continue
            slots.append(_normalize(block[:, a:b][:, max(0, cols[0] - 1) : cols[-1] + 2], bottom - top))
    return slots


_templates = None


def loadDigitTemplates():
    """templates/digits 裡以數字命名的字形圖，回傳 {數字字元: 正規化字形}"""
    global _templates
    if _templates is None:
        _templates = {}
        folder = getResourcePath("templates/digits")
        if os.path.isdir(folder):
            for file in os.listdir(folder):
                name, ext = os.path.splitext(file)
                if ext.lower() == ".png" and name.isdigit() and len(name) == 1:
                    img = readImage(os.path.join(folder, file), cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        _templates[name] = img.astype(np.float32) / 255.0
    return _templates


def readCount(region, scale=1.0):
    """讀出區域裡的數量。有任何一個字認不出來(例如還沒有那個數字的字形圖)回傳 None"""
    templates = loadDigitTemplates()
    slots = digitSlots(region, scale)
    if not slots or not templates:
        return None
    text = ""
    for slot in slots:
        best, score = None, 0.0
        for ch, template in templates.items():
            norm = float(np.linalg.norm(slot) * np.linalg.norm(template))
            value = float((slot * template).sum()) / norm if norm else 0.0
            if value > score:
                best, score = ch, value
        if best is None or score < MIN_SCORE:
            return None
        text += best
    return int(text)
