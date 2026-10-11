import asyncio
import re
import cv2
import numpy as np

# 用 Windows 內建的 OCR 讀畫面上的「現在值/最大值」(例如體力條的 41/180)。
# 沒有安裝 winrt 套件或系統沒有 OCR 語言時 readFraction 回傳 None，呼叫端要有備案。
try:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    AVAILABLE = True
except ImportError:  # 沒裝套件(或不是 Windows)
    AVAILABLE = False

FRACTION = re.compile(r"^(\d{1,4})/(\d{2,4})$")
LEADING = re.compile(r"^(\d{1,4})(?:\D|$)")  # 開頭的數字後面接著非數字(斜線或被讀成別的字)，或後面整串都沒讀到
# 同一張圖用不同的放大倍率、模糊程度各讀一次，取多數一致的結果(單次常漏字或把 1 讀成別的)
SCALES = (4, 6, 4, 6, 3, 4)
BLURS = (1.5, 1.5, 0.8, 0.8, 1.5, 1.2)
MAX_CURRENT = 400  # 體力條的現在值不會超過這個
AGREE = 2  # 有這麼多次讀到一樣的完整結果(N/M)就採用
AGREE_NUMERATOR = 3  # 只有 N 的時候要求更多次一致(M 讀壞了，要求嚴一點)

_engine = None
_engineFailed = False


def _getEngine():
    global _engine, _engineFailed
    if _engine is None and not _engineFailed:
        _engine = OcrEngine.try_create_from_user_profile_languages()
        _engineFailed = _engine is None
    return _engine


def isReady():
    """OCR 能不能用: 有裝 winrt 套件，而且系統有可用的 OCR 語言"""
    if not AVAILABLE:
        return False
    try:
        return _getEngine() is not None
    except Exception:
        return False


def _toBitmap(gray):
    height, width = gray.shape
    writer = DataWriter()
    writer.write_bytes(bytearray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA).tobytes()))
    return SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height)


async def _recognize(engine, gray):
    return (await engine.recognize_async(_toBitmap(gray))).text


def _whiteText(region):
    """挑出區域裡的白色文字，回傳白底變黑字的圖(OCR 對乾淨的黑字白底最準)；沒有白色像素回傳 None"""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] <= 60) & (hsv[:, :, 2] >= 225)).astype(np.uint8) * 255
    ys, xs = np.where(white > 0)
    if len(xs) < 10:
        return None
    return white[max(0, ys.min() - 2) : ys.max() + 3, max(0, xs.min() - 2) : xs.max() + 3]


def readFraction(region):
    """讀出區域裡白色文字的「N/M」。回傳 (N, M)；只有 N 讀得穩(M 常被讀成「h8」之類)時回傳 (N, None)，由呼叫端用別的線索決定 M；
    讀不出來或沒有 OCR 回傳 None"""
    if not AVAILABLE or region is None or region.size == 0:
        return None
    engine = _getEngine()
    text = _whiteText(region)
    if engine is None or text is None:
        return None
    full, current = {}, {}
    for scale, blur in zip(SCALES, BLURS):
        gray = 255 - cv2.resize(text, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        gray = cv2.copyMakeBorder(cv2.GaussianBlur(gray, (0, 0), blur), 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        try:
            raw = asyncio.run(_recognize(engine, gray)).replace(" ", "")
        except Exception:
            continue
        match = FRACTION.match(raw)
        if match and int(match.group(2)) >= 50 and int(match.group(1)) <= int(match.group(2)) * 2:
            key = (int(match.group(1)), int(match.group(2)))
            full[key] = full.get(key, 0) + 1
            if full[key] >= AGREE:
                return key
        head = LEADING.match(raw)  # 「91h8」「91/1」這種: 斜線後面讀壞了，至少前面的 N 可以用
        if head and int(head.group(1)) <= MAX_CURRENT:  # 「76 / 100」中間的空格被去掉、字黏在一起的「718」這種不可能的數字不算
            n = int(head.group(1))
            current[n] = current.get(n, 0) + 1
    best = max(current, key=current.get) if current else None
    if best is not None and current[best] >= AGREE_NUMERATOR:
        return best, None
    return None
