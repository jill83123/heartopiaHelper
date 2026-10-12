"""安全網: 腳本執行中如果跳出確認視窗(例如誤點到垃圾桶的「確認丟棄」)，一律按「取消」。
釣魚、採集、料理、雪雕共用，由 BaseTask 啟動一條背景執行緒定時檢查。

要關掉有兩種方式:
- config.ini 加一行 autoCancelDialog=False
- 或把下面的 GUARD_ENABLED 改成 False(整個停用)"""

import time
import cv2
import numpy as np
from tools.finder import getBaseScale
from tools.tools import getThreshold, matchBest

GUARD_ENABLED = True
CHECK_INTERVAL = 1.5  # 隔多久看一次畫面(秒)
CONFIRM_SECONDS = 4  # 連續兩次都看到才按(秒內)，腳本自己的流程(例如吃食物遇到「體力已滿」)會先處理掉，不搶著點
CANCEL_BUTTONS = {  # 取消鈕的模板 -> 同一個視窗裡右邊「確定」鈕(青綠色)離它多遠(1600 寬為基準，容許範圍)
    "cancelBtn": (200, 340),
    "thinkAgainBtn": (200, 340),
}
CANCEL_TEMPLATE_DIR = "templates/fishing"
TEAL_HSV = ((80, 120, 100), (100, 255, 255))  # 青綠色確定鈕的色相、飽和度、亮度(OpenCV 的 HSV)
TEAL_MIN_PIXELS = 400  # 取消鈕右邊的範圍內，青綠色像素至少要有幾個(1600 寬為基準)


def guardEnabled(config):
    return GUARD_ENABLED and str(config.get("autoCancelDialog", "True")).lower() != "false"


def _confirmBesideCancel(img, pos, scale, span):
    """取消鈕右邊同一列有沒有青綠色的「確定」鈕。只有單一橘色按鈕(例如「取消追蹤」)的畫面不算確認視窗"""
    x, y = int(pos[0]), int(pos[1])
    region = img[max(0, y - int(25 * scale)) : y + int(25 * scale), x + int(span[0] * scale) : x + int(span[1] * scale)]
    if region.size == 0:
        return False
    mask = cv2.inRange(cv2.cvtColor(region, cv2.COLOR_BGR2HSV), np.array(TEAL_HSV[0]), np.array(TEAL_HSV[1]))
    return int(np.count_nonzero(mask)) >= TEAL_MIN_PIXELS * scale * scale


def findCancelButton(img, config, scale):
    """畫面上有確認視窗的話，回傳取消鈕在影像中的座標，沒有回傳 None"""
    for name, span in CANCEL_BUTTONS.items():
        pos = matchBest(img, f"{CANCEL_TEMPLATE_DIR}/{name}.png", getThreshold(config, name), scale)
        if pos and _confirmBesideCancel(img, pos, scale, span):
            return pos
    return None


def runDialogGuard(backend, config, setLog, stopEvent):
    """背景執行緒的迴圈: 隔一段時間截圖，連續兩次都看到確認視窗才按取消。任何錯誤都忽略(這只是安全網，不能影響主流程)"""
    seenAt = None
    while not stopEvent.wait(CHECK_INTERVAL):
        if not guardEnabled(config):
            seenAt = None
            continue
        try:
            img, ox, oy = backend.captureFull()
            if backend.channelsSwapped:
                img = np.ascontiguousarray(img[:, :, ::-1])
            scale = getBaseScale(backend, config) * backend.scaleResolver.factor
            pos = findCancelButton(img, config, scale)
            if not pos:
                seenAt = None
                continue
            now = time.time()
            if seenAt is None or now - seenAt > CONFIRM_SECONDS:
                seenAt = now
                continue
            backend.click(ox + pos[0], oy + pos[1])
            setLog("偵測到確認視窗，已按取消")
            seenAt = None
            stopEvent.wait(1.0)
        except Exception:
            seenAt = None
