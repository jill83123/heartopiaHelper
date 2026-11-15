"""拼圖的畫面分析: 找木框(棋盤與參考圖)、托盤上的碎片位置、把碎片比對到參考圖的哪個位置。
全部是純影像處理(輸入 BGR 影像)，座標都是傳入影像上的像素。版面比例以 1600x900 的遊戲畫面為基準。"""

import cv2
import numpy as np

BASE_W, BASE_H = 1600, 900
FRAME_INSET = 0.030  # 木框厚度 / 木框外寬
TRAY_Y = 818 / BASE_H  # 托盤上碎片中心的高度(比例)
TRAY_HALF_H = 48 / BASE_H  # 碎片(含凸出的部分)上下半高
TRAY_PITCH = 141 / BASE_W  # 相鄰碎片的間距(比例，以畫面寬計)
TRAY_PICK_X = (0.19, 0.82)  # 托盤兩端會淡出、碎片被切掉，在這個範圍外的碎片點不到，不能拿
TRAY_X_RANGE = (0.14, 0.86)  # 碎片可能出現的水平範圍
GRID = 10  # 每邊幾片(棋盤內框寬 / 一片的寬)；開始拼時會偵測，用 setGrid 改
GRID_SIZES = (6, 8, 10)  # 遊戲只有 36、64、100 片三種
TRAY_GRID = 10  # 托盤上碎片的大小固定，等於 10x10 拼圖的一格；其他片數的拼圖，托盤碎片和棋盤格子大小不同
CHECKER_MIN_GAP = 4  # 空格兩種底色的亮度差下限
CHECKER_MAX_GAP = 24  # 棋盤格兩種底色只差一點點；差很多的是碎片上的圖案
CHECKER_COVER = 0.7  # 兩種底色合起來至少佔一格的比例
RMS_SCALE = 60.0  # 顏色差均方根達到這個值，分數就降到 0
EDGE_BTN = (1518 / BASE_W, 432 / BASE_H)  # 右側第二顆: 只顯示邊緣拼圖塊(開著時變青綠色底，托盤上方有「當前僅顯示邊緣拼圖塊」)
BUTTON_ACTIVE_MIN = 0.45  # 按鈕圓內青綠色像素比例達到這個值就是開著(關著約 0.32、開著約 0.58)
TRASH_BTN = (1518 / BASE_W, 592 / BASE_H)  # 右側最下面的垃圾桶: 把「未落位」(放錯)的單片拼圖放回碎片區
IMAGE_BTN = (1518 / BASE_W, 352 / BASE_H)  # 右側「參考圖」按鈕(開啟後同一位置變成關閉)


BOARD_OUTER = (511, 98, 578, 578)  # 棋盤木框外框 (x, y, w, h)，以 1600x900 為基準(位置固定，不隨拼圖改變)
REF_OUTER = (436, 85, 728, 728)  # 參考圖木框外框
BOARD_RING_X = (512, 522)  # 木框左邊一小段(框內側)，用來判斷棋盤是否顯示；框色每個拼圖主題不同，所以只看「這一段顏色很一致」
REF_RING_X = (440, 452)
RING_Y = (250, 550)
RING_MAX_STD = 12.0
BUTTON_X = 1518  # 右側四顆圓形白色按鈕的位置
BUTTON_YS = (352, 432, 512, 592)


def _scaled(img, x, y):
    return round(x * img.shape[1] / BASE_W), round(y * img.shape[0] / BASE_H)


def _ringUniform(img, xs):
    x0, y0 = _scaled(img, xs[0], RING_Y[0])
    x1, y1 = _scaled(img, xs[1], RING_Y[1])
    strip = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    return strip.size > 0 and float(strip.std(axis=0).max()) < RING_MAX_STD


def _buttonsWhite(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = (hsv[..., 1] < 40) & (hsv[..., 2] > 225)
    hits = 0
    for by in BUTTON_YS:
        cx, cy = _scaled(img, BUTTON_X, by)
        r0, r1 = 24 * img.shape[1] / BASE_W, 31 * img.shape[1] / BASE_W
        yy, xx = np.ogrid[: img.shape[0], : img.shape[1]]
        d = np.hypot(xx - cx, yy - cy)
        ring = (d >= r0) & (d <= r1)
        if ring.any() and white[ring].mean() > 0.7:
            hits += 1
    return hits >= 3


ALIGN_MARGIN = 20  # 版面位置可能和基準差幾個像素(視窗邊框、截圖範圍不同)，上下左右各搜這麼遠
ALIGN_MIN_SCORE = 0.8  # 四顆按鈕的白色圓環對上的比例下限


def alignOffset(img):
    """用右側四顆白色圓形按鈕，找出實際版面相對 1600x900 基準位置的偏移 (dx, dy)(實際位置 = 基準位置 + 偏移)，找不到回傳 None。
    畫面大小要接近 1600x900 才處理(其他大小是縮放造成的，不是平移)"""
    H, W = img.shape[:2]
    if not (0.95 <= W / BASE_W <= 1.05 and 0.95 <= H / BASE_H <= 1.05):
        return None
    r0, r1, rad = 24, 31, 35
    tw, th = 2 * rad, BUTTON_YS[-1] - BUTTON_YS[0] + 2 * rad
    tmpl = np.zeros((th, tw), np.float32)
    yy, xx = np.ogrid[:th, :tw]
    for by in BUTTON_YS:
        d = np.hypot(xx - rad, yy - (by - BUTTON_YS[0] + rad))
        tmpl[(d >= r0) & (d <= r1)] = 1.0
    cx0, cy0 = BUTTON_X - rad - ALIGN_MARGIN, BUTTON_YS[0] - rad - ALIGN_MARGIN
    x0, y0 = max(0, cx0), max(0, cy0)
    x1, y1 = min(W, BUTTON_X + rad + ALIGN_MARGIN), min(H, BUTTON_YS[-1] + rad + ALIGN_MARGIN)
    if x1 - x0 < tw or y1 - y0 < th:
        return None
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 40) & (hsv[..., 2] > 225)).astype(np.float32)
    res = cv2.matchTemplate(white, tmpl, cv2.TM_CCORR) / float(tmpl.sum())
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < ALIGN_MIN_SCORE:
        return None
    return x0 + loc[0] - (BUTTON_X - rad), y0 + loc[1] - (BUTTON_YS[0] - rad)


def normalizeLayout(img, offset):
    """把版面平移回 1600x900 基準位置(多出的地方補黑)，回傳新影像；原圖座標 = 新圖座標 + offset"""
    dx, dy = offset
    out = np.zeros((BASE_H, BASE_W, 3), img.dtype)
    H, W = img.shape[:2]
    sx0, sy0 = max(0, dx), max(0, dy)
    sx1, sy1 = min(W, BASE_W + dx), min(H, BASE_H + dy)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - dy : sy1 - dy, sx0 - dx : sx1 - dx] = img[sy0:sy1, sx0:sx1]
    return out


def findFrame(img):
    """目前拼圖畫面的狀態: ("board", 外框) 顯示棋盤、("reference", 外框) 參考圖開著、None 不在拼圖畫面。
    框色、棋盤底色會隨拼圖主題改變，所以不比顏色: 版面位置固定，只確認那一段木框顏色一致，且棋盤顯示時右側四顆按鈕是亮白色"""
    sx, sy = img.shape[1] / BASE_W, img.shape[0] / BASE_H

    def box(r):
        return round(r[0] * sx), round(r[1] * sy), round(r[2] * sx), round(r[3] * sy)

    if _ringUniform(img, BOARD_RING_X) and _buttonsWhite(img):
        return "board", box(BOARD_OUTER)
    if _ringUniform(img, REF_RING_X):
        return "reference", box(REF_OUTER)
    return None


def innerRect(frame):
    x, y, w, h = frame
    t = round(w * FRAME_INSET)
    return x + t, y + t, w - 2 * t, h - 2 * t


def trayPieces(img, scale=None):
    """托盤上碎片的中心 [(x, y), ...]，由左到右。靠邊緣能量在固定間距上找位置"""
    H, W = img.shape[:2]
    cy = round(TRAY_Y * H)
    hh = round(TRAY_HALF_H * H)
    y0, y1 = max(0, cy - hh), min(H, cy + hh)
    band = cv2.GaussianBlur(img[y0:y1], (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)
    energy = np.sqrt(gx**2 + gy**2).sum(axis=(0, 2))  # 每個 x 欄的邊緣能量
    body = max(9, round(0.034 * W))  # 碎片本體寬(約 55px)，用這個寬度平滑，峰值才會落在碎片中心而不是邊緣
    energy = np.convolve(energy, np.ones(body) / body, mode="same")
    pitch = TRAY_PITCH * W
    xa, xb = int(TRAY_X_RANGE[0] * W), int(TRAY_X_RANGE[1] * W)
    base = np.median(energy[xa:xb])

    bestPhase, bestScore = 0, -1
    for phase in np.arange(0, pitch, 1.0):
        xs = np.arange(xa + phase, xb, pitch)
        score = sum(energy[int(x)] for x in xs if 0 <= int(x) < W)
        if score > bestScore:
            bestPhase, bestScore = phase, score
    out = []
    win = max(3, int(pitch * 0.2))
    for x in np.arange(xa + bestPhase, xb, pitch):
        x = int(x)
        local = energy[max(0, x - win) : x + win].mean()
        out.append((x, cy, float(local / (base + 1e-6))))
    return out


def referenceImage(img, frame, size):
    """從參考圖畫面切出圖片本體，縮成棋盤內框的大小 (size x size)"""
    x, y, w, h = innerRect(frame)
    return cv2.resize(img[y : y + h, x : x + w], (size, size), interpolation=cv2.INTER_AREA)


def matchPiece(img, center, ref, cell, scales=(1.0,)):
    """把托盤上一片碎片中央的小塊對到參考圖。回傳 [(分數, x, y, 比例), ...] 依分數高到低(x, y 為碎片中心在參考圖上的位置)，
    只保留彼此距離超過半片的候選，方便判斷第一名是否明顯勝出"""
    cx, cy = center
    half = max(6, round(cell * 0.46))
    patch = cv2.GaussianBlur(img[cy - half : cy + half, cx - half : cx + half], (3, 3), 0)
    refBlur = cv2.GaussianBlur(ref, (3, 3), 0)
    cands = []
    for s in scales:
        p = patch if s == 1.0 else cv2.resize(patch, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        ph, pw = p.shape[:2]
        # 用顏色差(均方根)而不是相關係數: 平坦的碎片(天空、地板)相關係數不穩定，顏色差則能老實反映「很多地方都差不多」
        res = cv2.matchTemplate(refBlur, p, cv2.TM_SQDIFF)
        res = 1.0 - np.sqrt(np.maximum(res, 0) / (ph * pw * 3)) / RMS_SCALE
        for _ in range(4):
            _, mx, _, loc = cv2.minMaxLoc(res)
            cands.append((float(mx), loc[0] + pw // 2, loc[1] + ph // 2, s))
            x0, y0 = max(0, loc[0] - int(cell * 0.5)), max(0, loc[1] - int(cell * 0.5))
            res[y0 : loc[1] + int(cell * 0.5), x0 : loc[0] + int(cell * 0.5)] = -1.0
    cands.sort(reverse=True)
    out = []
    for c in cands:
        if all(abs(c[1] - o[1]) > cell * 0.5 or abs(c[2] - o[2]) > cell * 0.5 for o in out):
            out.append(c)
    return out


_TONES = None  # 這個拼圖棋盤格的兩種底色(灰階)。一開始棋盤大多是空的，整張圖出現最多的兩個灰階就是它們；記下來之後，碎片再怎麼多也不會把深色/灰色的碎片當成空格


def setTones(tones):
    global _TONES
    _TONES = tones


def getTones():
    return _TONES


def boardTones(board):
    """整個棋盤內框出現最多的兩種灰階(只差一點點)，沒有明顯的兩種回傳 None"""
    gray = np.rint(board.astype(np.float32).mean(axis=2)).astype(np.int32).ravel()
    hist = np.bincount(gray, minlength=258).astype(np.float32)
    near = hist[:-2] + hist[1:-1] + hist[2:]
    p1 = int(near.argmax())
    masked = near.copy()
    masked[max(0, p1 - 3) : p1 + 4] = 0
    p2 = int(masked.argmax())
    gap = abs(p2 - p1)
    if not (CHECKER_MIN_GAP <= gap <= CHECKER_MAX_GAP) or near[p1] + near[p2] < gray.size * 0.3:
        return None
    sel = np.abs(gray - (p1 + 1)) <= 1
    sel |= np.abs(gray - (p2 + 1)) <= 1
    px = board.reshape(-1, 3).astype(np.float32)[sel]
    if len(px) == 0 or abs(float(px[:, 2].mean() - px[:, 0].mean())) < 20:  # 棋盤底色有明顯的色彩(橘或藍)，灰灰的多半是碎片
        return None
    return p1 + 1, p2 + 1


def setGrid(n):
    global GRID
    GRID = int(n)


def emptyMap(board, grid=None):
    """棋盤內框影像上，每一格是不是還空著 -> grid x grid 的布林陣列[row][col]。
    空格是兩種很接近的底色交錯成的棋盤格(顏色隨主題變)，所以看每格的顏色是不是剛好分成兩群、
    各自非常一致、群與群只差一點點；碎片的圖案(單色平面或有漸層、輪廓)不會是這樣"""
    grid = grid or GRID
    gray = np.rint(board.astype(np.float32).mean(axis=2)).astype(np.int32)
    size = board.shape[0]
    cell = size / grid
    out = np.zeros((grid, grid), bool)
    for r in range(grid):
        for c in range(grid):
            v = gray[round(r * cell + cell * 0.1) : round((r + 1) * cell - cell * 0.1), round(c * cell + cell * 0.1) : round((c + 1) * cell - cell * 0.1)].ravel()
            if v.size == 0:
                continue
            hist = np.bincount(v, minlength=258).astype(np.float32)
            near = hist[:-2] + hist[1:-1] + hist[2:]  # near[i] = 灰階 i..i+2 的像素數(i+1 為中心)
            p1 = int(near.argmax())
            masked = near.copy()
            masked[max(0, p1 - 3) : p1 + 4] = 0  # 第二種底色至少差 3 階
            p2 = int(masked.argmax())
            n1, n2 = near[p1], near[p2]
            gap = abs(p2 - p1)
            # 兩種底色都要佔相當比例、合起來佔大部分(邊上可能有鄰格碎片的凸起，所以不要求 100%)
            out[r, c] = CHECKER_MIN_GAP <= gap <= CHECKER_MAX_GAP and min(n1, n2) >= v.size * 0.22 and (n1 + n2) >= v.size * CHECKER_COVER
            if out[r, c] and _TONES is not None:  # 兩種底色要跟整個棋盤的底色一樣，不然是碎片上剛好有兩種接近的顏色
                out[r, c] = all(min(abs(q + 1 - t) for t in _TONES) <= 3 for q in (p1, p2))
    return _plainRing(board, gray, out, grid)


def _plainRing(board, gray, out, grid):
    """有些主題最外圈的空格是單色或很細的棋盤格，上面的規則會當成已有碎片。
    這裡補判: 最外圈的格子大部分像素的「顏色」(不是只看灰階，淡灰色碎片的灰階會和底色一樣)接近內部空格的兩種底色，就算空格"""
    size = gray.shape[0]
    cell = size / grid
    inner = [(r, c) for r in range(1, grid - 1) for c in range(1, grid - 1) if out[r, c]]
    if not inner:
        return out
    pix = np.concatenate([board[round(r * cell + cell * 0.2) : round((r + 1) * cell - cell * 0.2), round(c * cell + cell * 0.2) : round((c + 1) * cell - cell * 0.2)].reshape(-1, 3) for r, c in inner]).astype(np.float32)
    g = pix.mean(axis=1)
    hist = np.bincount(np.rint(g).astype(np.int32), minlength=258).astype(np.float32)
    near = hist[:-2] + hist[1:-1] + hist[2:]
    p1 = int(near.argmax())
    masked = near.copy()
    masked[max(0, p1 - 3) : p1 + 4] = 0
    colors = []
    for t in (p1 + 1, int(masked.argmax()) + 1):
        sel = pix[np.abs(g - t) <= 1.5]
        if len(sel):
            colors.append(sel.mean(axis=0))
    if len(colors) < 2:
        return out
    colors = np.array(colors)
    for r in range(grid):
        for c in range(grid):
            if out[r, c] or 0 < r < grid - 1 and 0 < c < grid - 1:
                continue
            v = board[round(r * cell + cell * 0.15) : round((r + 1) * cell - cell * 0.15), round(c * cell + cell * 0.15) : round((c + 1) * cell - cell * 0.15)].reshape(-1, 3).astype(np.float32)
            if len(v) == 0:
                continue
            d = np.abs(v[:, None, :] - colors[None, :, :]).max(axis=2).min(axis=1)
            if float((d <= 9).mean()) >= 0.72:
                out[r, c] = True
    return out


def decide(cands, empty, cell, minScore=0.72, minMargin=0.05):
    """從候選位置挑出要放的格子 (row, col, 分數)。只看還空著的格子；第一名要夠像，而且明顯勝過第二名(平坦的天空、地板會有很多差不多的位置，不能亂放)"""
    grid = empty.shape[0]
    valid = []
    for score, x, y, _ in cands:
        c, r = min(grid - 1, int(x // cell)), min(grid - 1, int(y // cell))
        if empty[r, c] and all((r, c) != (v[0], v[1]) for v in valid):
            valid.append((r, c, score))
    if not valid or valid[0][2] < minScore:
        return None
    if len(valid) > 1 and valid[0][2] - valid[1][2] < minMargin:
        return None
    return valid[0]


KNOB_DIST = 0.6  # 凸角中心離格子中心多遠(以一格寬為單位)
KNOB_HALF = 0.09  # 比對凸角時取的小方塊半寬(以一格寬為單位)
KNOB_TOP = 8  # 凸角只比本體分數前幾名的格子
KNOB_WEIGHT = 0.4  # 每個凸角的吻合分數佔多少權重(本體的權重是 1)


def _knobTerms(blurImg, refBlur, cx, cy, cell, tcell, r, c, parity):
    """規律切割時，這一格哪幾邊一定是凸角(位置奇偶決定)；凸出去的那一小塊和參考圖上「鄰格靠這邊」的圖案要接得起來。
    回傳每個凸角的吻合分數(0~1)。外圍的邊是平的、缺角的邊沒東西可比，都不算"""
    out = []
    grid = refBlur.shape[1] / cell
    hh = max(2, round(KNOB_HALF * cell))
    th = max(2, round(KNOB_HALF * tcell))
    for axis, sides in (("h", ((-1, 0), (1, 0))), ("v", ((0, -1), (0, 1)))):
        if axis not in parity or (r + c) % 2 != parity[axis]:
            continue  # 這個方向這格是缺角
        for ox, oy in sides:
            if not (0 <= r + oy < grid and 0 <= c + ox < grid):
                continue
            tx, ty = round(cx + ox * KNOB_DIST * tcell * BODY_RATIO), round(cy + oy * KNOB_DIST * tcell * BODY_RATIO)
            rx, ry = round((c + 0.5 + ox * KNOB_DIST) * cell), round((r + 0.5 + oy * KNOB_DIST) * cell)
            if ty - th < 0 or tx - th < 0:
                continue
            a = blurImg[ty - th : ty + th, tx - th : tx + th]
            if a.size and a.shape[:2] != (2 * hh, 2 * hh):
                a = cv2.resize(a, (2 * hh, 2 * hh), interpolation=cv2.INTER_AREA)
            b = refBlur[ry - hh : ry + hh, rx - hh : rx + hh]
            if a.shape != b.shape or a.size == 0 or ry - hh < 0 or rx - hh < 0:
                continue
            rms = float(np.sqrt(((a.astype(np.float32) - b.astype(np.float32)) ** 2).mean()))
            out.append(max(0.0, 1.0 - rms / RMS_SCALE))
    return out


def cellScores(img, center, ref, cell, empty, parity=None, dys=(0,), cache=None, tcell=None):
    """碎片對「每一個空格」的相似分數 -> grid x grid 陣列(不是空格的填 -1)。
    比 matchPiece 只取全圖前幾名更適合平坦的碎片: 它真正的格子不一定排進全圖前幾名，但在「還空著的格子」裡比就清楚多了。
    dys: 托盤上碎片的高度高低不一，上下挪幾個位置各比一次，每格取最好的。
    parity: 有規律可用時，凸角的小塊也拿來和參考圖比，把形狀跟圖案一起算進分數"""
    cx, cy = center
    tcell = tcell or cell  # 托盤碎片的格子大小；棋盤格子(cell)和它不同時，碎片要縮放到棋盤的比例再比
    half = max(6, round(tcell * 0.46))
    refBlur = cv2.GaussianBlur(ref, (3, 3), 0)
    grid = empty.shape[0]
    cells = [(r, c) for r in range(grid) for c in range(grid) if empty[r, c]]
    if cache is not None and "base" in cache:
        out, bestDy = cache["base"][0].copy(), cache["base"][1]
    else:
        out = np.full((grid, grid), -1.0, np.float32)
        bestDy = np.zeros((grid, grid), np.int32)
        slop = max(2, round(cell * 0.2))  # 棋盤框的位置估計有幾像素誤差，格子中心附近找最高分
        for dy in dys:
            y0p = cy + dy - half
            if y0p < 0 or cy + dy + half > img.shape[0]:
                continue
            patch = cv2.GaussianBlur(img[y0p : cy + dy + half, cx - half : cx + half], (3, 3), 0)
            if abs(cell / tcell - 1) > 0.02:
                patch = cv2.resize(patch, None, fx=cell / tcell, fy=cell / tcell, interpolation=cv2.INTER_AREA)
            ph, pw = patch.shape[:2]
            res = cv2.matchTemplate(refBlur, patch, cv2.TM_SQDIFF)
            res = np.nan_to_num(1.0 - np.sqrt(np.maximum(res, 0) / (ph * pw * 3)) / RMS_SCALE, nan=-1.0)
            for r in range(grid):
                for c in range(grid):
                    x, y = round((c + 0.5) * cell) - pw // 2, round((r + 0.5) * cell) - ph // 2
                    x0, y0 = max(0, x - slop), max(0, y - slop)
                    win = res[y0 : y + slop + 1, x0 : x + slop + 1]
                    if win.size and win.max() > out[r, c]:
                        out[r, c] = float(win.max())
                        bestDy[r, c] = dy
        if cache is not None:
            cache["base"] = (out.copy(), bestDy)
    out[~empty] = -1.0
    if parity and cells:
        # 凸角的比對比較花時間，只對本體分數前幾名的格子做
        top = sorted(cells, key=lambda rc: -out[rc])[:KNOB_TOP]
        y0b = max(0, cy - round(tcell * 1.4))
        blurImg = cv2.GaussianBlur(img[y0b : cy + round(tcell * 1.4)], (3, 3), 0)
        for r, c in top:
            ks = _knobTerms(blurImg, refBlur, cx, cy + int(bestDy[r, c]) - y0b, cell, tcell, r, c, parity)
            if ks:
                out[r, c] = (out[r, c] + KNOB_WEIGHT * sum(ks)) / (1 + KNOB_WEIGHT * len(ks))
    return out


def detectGrid(img, ref, centers, candidates=GRID_SIZES):
    """用托盤上有花紋的碎片，猜拼圖是幾x幾片。托盤碎片大小固定，棋盤格子大小隨片數變:
    每種片數把碎片縮放成那個格子大小，去對參考圖，真正的片數對起來最像。
    ref 要先縮成棋盤內框的大小。回傳 (片數或 None, {片數: 分數}, 有沒有把握)"""
    size = ref.shape[1]
    tcell = size / TRAY_GRID
    half = max(6, round(tcell * 0.46))
    refBlur = cv2.GaussianBlur(ref, (3, 3), 0)
    patches = []
    for cx, cy in centers:
        if cy - half < 0 or cx - half < 0:
            continue
        p = cv2.GaussianBlur(img[cy - half : cy + half, cx - half : cx + half], (3, 3), 0)
        if p.shape[0] == 2 * half and float(p.reshape(-1, 3).std(axis=0).mean()) > 15:  # 太平坦的碎片(單色)看不出縮放比例
            patches.append(p)
    if len(patches) < 2:
        return None, {}, False
    scores = {}
    for g in candidates:
        s = (size / g) / tcell
        per = []
        for p in patches:
            q = cv2.resize(p, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if q.shape[0] < 6 or q.shape[0] >= ref.shape[0]:
                break
            res = cv2.matchTemplate(refBlur, q, cv2.TM_SQDIFF)
            per.append(1.0 - np.sqrt(max(float(res.min()), 0) / (q.shape[0] * q.shape[1] * 3)) / RMS_SCALE)
        else:
            per.sort(reverse=True)
            top = per[: max(2, round(len(per) * 0.6))]  # 位置偏掉、碎片太小的幾片不算
            scores[g] = float(np.mean(top))
    if not scores:
        return None, {}, False
    ranked = sorted(scores, key=scores.get, reverse=True)
    best = ranked[0]
    confident = scores[best] >= 0.4 and (len(ranked) < 2 or scores[best] - scores[ranked[1]] >= 0.03)  # 色塊大的圖，好幾種片數都對得起來，這時不能只靠圖案
    return (best if scores[best] >= 0.4 else None), scores, confident


def measureBoardPieces(board):
    """棋盤上已經有碎片時(例如上次拼到一半)，從單獨一片碎片(沒和別片連在一起)量本體邊長，推出片數。
    回傳片數或 None。用棋盤底色(兩種灰階)以外的像素當碎片"""
    tones = _TONES
    if tones is None:
        return None
    gray = board.astype(np.float32).mean(axis=2)
    bg = np.zeros(gray.shape, bool)
    for t in tones:
        bg |= np.abs(gray - t) <= 3.5
    mask = (~bg).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    size = board.shape[0]
    votes = []
    for i in range(1, n):
        x, y, w, h = stats[i, :4]
        if w < size / 14 or h < size / 14 or w > size / 3.2 or h > size / 3.2:
            continue  # 太小是雜訊，太大是好幾片連在一起
        sub = labels[y : y + h, x : x + w] == i
        cols, rows = sub.sum(axis=0), sub.sum(axis=1)
        ci, ri = np.nonzero(cols >= 0.4 * cols.max())[0], np.nonzero(rows >= 0.5 * rows.max())[0]
        bw, bh = int(ci[-1] - ci[0] + 1), int(ri[-1] - ri[0] + 1)
        votes.append(size / float(max(bw, bh)))  # 凹槽、凸角會讓其中一個方向量短，本體是正方形，取大的
    if not votes:
        return None
    v = float(np.median(votes))
    g = min(GRID_SIZES, key=lambda k: abs(v - k))
    return g if abs(v - g) <= 0.8 else None


def measureDropped(before, after, cell):
    """碎片掉到棋盤上後，用放前放後的差異量出那片碎片本體的邊長(就是棋盤一格)，回傳 (邊長, 中心x, 中心y) 或 None。
    本體每一欄/每一列都有碎片的像素，凸角只佔一小段，所以只數「碎片像素夠多」的欄列"""
    diff = np.abs(after.astype(np.int16) - before.astype(np.int16)).max(axis=2)
    mask = (diff > 22).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[i, :4]
    if w < cell * 0.3 or h < cell * 0.3:
        return None
    sub = labels[y : y + h, x : x + w] == i
    cols, rows = sub.sum(axis=0), sub.sum(axis=1)
    cx_idx, ry_idx = np.nonzero(cols >= 0.4 * cols.max())[0], np.nonzero(rows >= 0.5 * rows.max())[0]
    bw, bh = int(cx_idx[-1] - cx_idx[0] + 1), int(ry_idx[-1] - ry_idx[0] + 1)
    return float(max(bw, bh)), x + (cx_idx[0] + cx_idx[-1]) / 2.0, y + (ry_idx[0] + ry_idx[-1]) / 2.0


def bestCells(scores):
    """從 cellScores 的結果取出 (row, col, 第一名分數, 與第二名的差)；沒有空格回傳 None"""
    flat = scores.ravel()
    order = np.argsort(flat)[::-1]
    if flat[order[0]] < 0:
        return None
    best = flat[order[0]]
    second = flat[order[1]] if len(order) > 1 and flat[order[1]] >= 0 else -1.0
    r, c = divmod(int(order[0]), scores.shape[1])
    return r, c, float(best), float(best - second) if second >= 0 else float(best)


# ---- 拼圖的凸角、缺角 ----
EDGE_DIRS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
EDGE_OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
BODY_RATIO = 1.03  # 托盤上碎片本體寬 / 棋盤一格寬
EDGE_KNOB_DIST = 18.0  # 碎片外側凸出處的顏色和背景差多少，才算有凸角
EDGE_HOLE_DIST = 12.0  # 碎片邊緣中間往內一點的顏色和背景差不多，就是缺角


def _patch(img, x, y, r=2):
    h, w = img.shape[:2]
    x0, x1, y0, y1 = max(0, int(x) - r), min(w, int(x) + r + 1), max(0, int(y) - r), min(h, int(y) + r + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    return img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32).mean(axis=0)


def _pieceEdgesByColor(img, center, cell):
    """托盤上一片碎片四邊的形狀 {"top": "K" 凸角 / "H" 缺角 / None 看不出來, ...}。
    凸角: 本體外側邊緣中間(凸角會長到的地方)的顏色，和更外面一點的背景不同；
    缺角: 邊緣中間往內一點(缺角的圓會挖到的地方)的顏色，和背景幾乎一樣"""
    cx, cy = center
    h = cell * BODY_RATIO / 2
    out = {}
    for side, (dx, dy) in EDGE_DIRS.items():
        probeOut = _patch(img, cx + dx * (h + 8), cy + dy * (h + 8))
        probeIn = _patch(img, cx + dx * (h - 8), cy + dy * (h - 8))
        if dx == 0:
            # 上、下邊: 背景取同一排、凸角寬度以外(左右各 24px)的外側。托盤上緣的線剛好橫過碎片中間，
            # 所以左、右邊不能這樣取；而上、下邊往外拉遠，場景的漸層會讓顏色差太多
            a = _patch(img, cx - 24, cy + dy * (h + 8))
            b = _patch(img, cx + 24, cy + dy * (h + 8))
            bg = None if a is None or b is None or float(np.abs(a - b).max()) > 2 * EDGE_KNOB_DIST else (a + b) / 2
        else:
            bg = _patch(img, cx + dx * (h + 24), cy + dy * (h + 24))
        if probeOut is None or probeIn is None or bg is None:
            out[side] = "?"  # 背景取不到(場景漸層、被切掉)，看不出來，不能當成平邊
            continue
        dOut = float(np.abs(probeOut - bg).max())
        dIn = float(np.abs(probeIn - bg).max())
        if dOut >= EDGE_KNOB_DIST:
            out[side] = "K"
        elif dIn <= EDGE_HOLE_DIST:
            out[side] = "H"
        else:
            out[side] = None
    return out


def checkerTones(board, empty):
    """棋盤空格兩種底色的灰階值(取所有空格像素裡最常出現的兩種)；沒有空格回傳 None"""
    gray = np.rint(board.astype(np.float32).mean(axis=2)).astype(np.int32)
    cell = board.shape[0] / empty.shape[0]
    vals = []
    for r in range(empty.shape[0]):
        for c in range(empty.shape[1]):
            if empty[r, c]:
                vals.append(gray[round(r * cell + cell * 0.3) : round((r + 1) * cell - cell * 0.3), round(c * cell + cell * 0.3) : round((c + 1) * cell - cell * 0.3)].ravel())
    if not vals:
        return None
    near = np.bincount(np.concatenate(vals), minlength=258).astype(np.float32)
    near = near[:-2] + near[1:-1] + near[2:]
    p1 = int(near.argmax()) + 1
    near[max(0, p1 - 5) : p1 + 4] = 0
    p2 = int(near.argmax()) + 1
    return p1, p2


def requiredEdges(board, empty, cell, tones):
    """每個空格四邊「鄰居已經放好」時，從鄰居的形狀推出這片碎片那一邊需要的形狀:
    {(row, col): {"top": "K" 要有凸角 / "H" 要有缺角, ...}}，鄰居還空著或看不出來的邊不列。
    鄰居的凸角會長進這一格(棋盤底色被蓋住) -> 這格要缺角；鄰居有缺角(鄰居裡面看得到棋盤底色) -> 這格要凸角"""
    gray = board.astype(np.float32).mean(axis=2)
    size = board.shape[0]
    grid = empty.shape[0]

    def isChecker(x, y):
        x0, y0 = max(0, round(x) - 1), max(0, round(y) - 1)
        v = gray[y0 : round(y) + 2, x0 : round(x) + 2]
        if v.size == 0:
            return False
        m = float(v.mean())
        return float(v.std()) < 2.5 and min(abs(m - tones[0]), abs(m - tones[1])) <= 2.5

    depth = max(5, cell * 0.16)
    out = {}
    for r in range(grid):
        for c in range(grid):
            if not empty[r, c]:
                continue
            need = {}
            for side, (dx, dy) in EDGE_DIRS.items():
                nr, nc = r + dy, c + dx
                if not (0 <= nr < grid and 0 <= nc < grid):
                    need[side] = "F"  # 棋盤外緣: 這一邊是平的
                    continue
                if empty[nr, nc]:
                    continue
                bx, by = (c + 0.5 + dx * 0.5) * cell, (r + 0.5 + dy * 0.5) * cell  # 兩格交界的中點
                intoUs = not isChecker(bx - dx * depth, by - dy * depth)  # 我們這一格靠邊界處被蓋住了 = 鄰居的凸角
                holeAtNeighbor = isChecker(bx + dx * depth, by + dy * depth)  # 鄰居靠邊界處露出底色 = 鄰居的缺角
                if intoUs and not holeAtNeighbor:
                    need[side] = "H"
                elif holeAtNeighbor and not intoUs:
                    need[side] = "K"
            if need:
                out[(r, c)] = need
    return out


SHAPE_MATCH_BONUS = 0.06  # 每一邊凸角/缺角吻合鄰居，加多少分
FLAT_MISMATCH_PENALTY = 0.7  # 碎片明明有凸角/缺角的那一邊，卻要貼在棋盤外緣(必須是平的): 幾乎不可能，扣很重(只有強制模式才可能放)
SHAPE_MISMATCH_PENALTY = 0.25  # 每一邊對不上(該凸的是缺角、該缺的是凸角)，扣多少分


def applyShape(scores, edges, required):
    """依碎片四邊的形狀與各空格鄰居要求的形狀調整分數: 對得上加一點、對不上扣很多(兩片拼在一起一邊是凸角、另一邊一定是缺角)。
    只扣分不直接排除，避免形狀誤判時把真正的格子淘汰"""
    out = scores.copy()
    for (r, c), need in required.items():
        if out[r, c] < 0:
            continue
        for side, want in need.items():
            have = edges.get(side)
            if have == "?":
                continue
            if want == "F":  # 平邊: 碎片這邊若明明有凸角或缺角就不可能是這格；看不出形狀(None)才可能是平邊
                out[r, c] += SHAPE_MATCH_BONUS if have is None else -FLAT_MISMATCH_PENALTY
                continue
            if have is None:
                continue
            out[r, c] += SHAPE_MATCH_BONUS if have == want else -SHAPE_MISMATCH_PENALTY
    return out


def ringCells(grid=None):
    """最外圈的格子(邊緣拼圖塊的位置) -> grid x grid 布林陣列"""
    grid = grid or GRID
    ring = np.zeros((grid, grid), bool)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    return ring


def buttonActive(img, rel):
    """右側圓形按鈕是不是開著(開著時底色變青綠色)。rel 是按鈕中心的相對位置"""
    cx, cy = round(rel[0] * img.shape[1]), round(rel[1] * img.shape[0])
    r = 26 * img.shape[1] / BASE_W
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    yy, xx = np.ogrid[: img.shape[0], : img.shape[1]]
    inside = np.hypot(xx - cx, yy - cy) <= r
    teal = (hsv[..., 0] >= 78) & (hsv[..., 0] <= 100) & (hsv[..., 1] >= 110) & (hsv[..., 2] >= 100)
    return bool(inside.any() and float(teal[inside].mean()) >= BUTTON_ACTIVE_MIN)


def outerSides(r, c, grid=None):
    """格子貼著棋盤外緣的那幾邊"""
    grid = grid or GRID
    sides = set()
    if r == 0:
        sides.add("top")
    if r == grid - 1:
        sides.add("bottom")
    if c == 0:
        sides.add("left")
    if c == grid - 1:
        sides.add("right")
    return sides


EDGE_BANNER = (700 / BASE_W, 757 / BASE_H, 930 / BASE_W, 777 / BASE_H)  # 「當前僅顯示邊緣拼圖塊」青綠色橫幅(只顯示邊緣時一直在托盤上方)


def edgeBanner(img):
    """托盤上方有沒有「當前僅顯示邊緣拼圖塊」的青綠色橫幅"""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = round(EDGE_BANNER[0] * W), round(EDGE_BANNER[1] * H), round(EDGE_BANNER[2] * W), round(EDGE_BANNER[3] * H)
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    teal = (hsv[..., 0] >= 78) & (hsv[..., 0] <= 100) & (hsv[..., 1] >= 110) & (hsv[..., 2] >= 100)
    return bool(teal.size and float(teal.mean()) >= 0.3)


def edgeOnDetected(img, rel):
    """「只顯示邊緣」是否開著: 右側按鈕變青綠色，或托盤上方有橫幅(任一個看到就算，避免按鈕被滑鼠移上去等狀況誤判)"""
    return buttonActive(img, rel) or edgeBanner(img)


def findConfirmTeal(img):
    """不靠模板找「再想想／放回」確認視窗: 畫面中下方有一顆青綠色大按鈕，左邊同一列有一顆橘色按鈕。
    回傳青綠色「放回」鈕的中心 (x, y)，沒有回傳 None"""
    H, W = img.shape[:2]
    x0, x1, y0, y1 = int(W * 0.2), int(W * 0.82), int(H * 0.45), int(H * 0.85)
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    teal = cv2.inRange(hsv, np.array((80, 120, 100)), np.array((100, 255, 255)))
    orange = cv2.inRange(hsv, np.array((8, 60, 150)), np.array((25, 200, 255)))
    need = 0.004 * W * H
    if np.count_nonzero(teal) < need or np.count_nonzero(orange) < need:
        return None
    ty, tx = np.nonzero(teal)
    oy, ox = np.nonzero(orange)
    if ox.mean() >= tx.mean() or abs(ty.mean() - oy.mean()) > H * 0.05:
        return None
    return x0 + int(tx.mean()), y0 + int(ty.mean())


def cornerCell(edges, grid=None):
    """碎片有相鄰兩邊都是平的(直角)、另外兩邊都有凸角或缺角，那一定是四個角落之一。回傳角落格子 (row, col)，不是回傳 None。
    平的那兩邊必須是看起來沒有凸角也沒有缺角(None)，另外兩邊一定要看得出形狀，避免把「看不出來」誤當成平邊"""
    grid = grid or GRID
    flat = {s for s, v in edges.items() if v is None}
    if len(flat) != 2 or any(edges[s] not in ("K", "H") for s in edges if s not in flat) or ("top" in flat) == ("bottom" in flat):
        return None
    row = 0 if "top" in flat else grid - 1
    col = 0 if "left" in flat else grid - 1
    if ("left" in flat) == ("right" in flat):
        return None
    return row, col


def sideMask(edges, grid=None):
    """碎片只有一邊是平的(沒有凸角也沒有缺角)、另外三邊都看得出形狀，就一定是那一邊的邊緣碎片(不含角落)。
    回傳該邊上的格子遮罩 grid x grid 布林陣列，不符合條件回傳 None"""
    grid = grid or GRID
    flat = [s for s, v in edges.items() if v is None]
    if len(flat) != 1 or any(edges[s] not in ("K", "H") for s in edges if s not in flat):
        return None
    mask = np.zeros((grid, grid), bool)
    side = flat[0]
    if side == "top":
        mask[0, 1:-1] = True
    elif side == "bottom":
        mask[-1, 1:-1] = True
    elif side == "left":
        mask[1:-1, 0] = True
    else:
        mask[1:-1, -1] = True
    return mask


def pieceEdgesByShape(img, center, cell):
    """用碎片邊緣的「位置」判斷四邊形狀，不靠背景顏色: 沿著垂直於邊的方向，在邊的中間和左右兩側(凸角、缺角以外)各取一條顏色剖面，
    找出每條剖面上最外面的那個明顯邊界。邊是平的 -> 三條邊界一樣深；凸角 -> 中間的邊界比兩側更外；缺角 -> 中間的邊界比兩側更內。
    回傳 {"top"/"bottom"/"left"/"right": "K" / "H" / None(平) / "?"(看不出來)}"""
    cx, cy = center
    h = cell * BODY_RATIO / 2
    reach = max(6, round(cell * 0.30))  # 邊界可能落在本體邊緣往內外各這麼遠
    lateral = round(cell * 0.30)  # 取側邊剖面的位置(凸角寬度以外)
    wid = 2
    H, W = img.shape[:2]
    out = {}
    for side, (dx, dy) in EDGE_DIRS.items():
        px, py = -dy, dx  # 沿著邊的方向
        positions = []
        peak = 0.0
        profs = []
        for off in (-lateral, 0, lateral):
            col = []
            for t in range(-reach, reach + 1):
                x = cx + dx * (h + t) + px * off
                y = cy + dy * (h + t) + py * off
                samples = []
                for k in range(-wid, wid + 1):
                    xx, yy = int(round(x + px * k)), int(round(y + py * k))
                    if 0 <= xx < W and 0 <= yy < H:
                        samples.append(img[yy, xx].astype(np.float32))
                col.append(np.mean(samples, axis=0) if samples else None)
            if any(v is None for v in col):
                profs = None
                break
            col = np.array(col)
            g = np.abs(col[2:] - col[:-2]).sum(axis=1)  # 每個位置的顏色變化
            profs.append(g)
            peak = max(peak, float(g.max()))
        if profs is None or peak < 24:
            out[side] = "?"
            continue
        thr = max(18.0, peak * 0.4)
        for g in profs:
            idx = np.nonzero(g >= thr)[0]
            positions.append(None if len(idx) == 0 else int(idx.max()) + 1 - reach)  # 最外面的邊界相對本體邊緣的位置(往外為正)
        if None in positions:
            out[side] = "?"
            continue
        sidesAvg = (positions[0] + positions[2]) / 2
        if abs(positions[0] - positions[2]) > 4:
            out[side] = "?"
            continue
        d = positions[1] - sidesAvg
        out[side] = "K" if d >= 5 else ("H" if d <= -5 else (None if abs(d) <= 3 else "?"))
    return out


def pieceEdges(img, center, cell):
    """托盤上一片碎片四邊的形狀。先用邊緣位置判斷(不受背景顏色影響)，看不出來的邊再用背景顏色比對補上"""
    byShape = pieceEdgesByShape(img, center, cell)
    byColor = _pieceEdgesByColor(img, center, cell)
    return {side: (byColor[side] if byShape[side] == "?" else byShape[side]) for side in byShape}
