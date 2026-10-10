import ctypes
import win32api
import win32con
import win32gui

# 置頂的浮動日誌視窗。標題不能含主程式標題或遊戲視窗標題的文字，否則 findWindow 會找錯視窗
LOG_TITLE = "浮動日誌"
LOG_SIZE = (360, 132)
MIN_HEIGHT = 80  # 拖拉調整高度的範圍(96 dpi 的像素)
MAX_HEIGHT = 700
COLLAPSED_HEIGHT = 28  # 縮小後只剩標題列(96 dpi 的像素)，要和 log.html 的 .bar 高度一致  # 視窗大小(96 dpi 的像素)，小小的不擋畫面
LOG_ALPHA = 215  # 視窗整體不透明度(0~255)，半透明
MARGIN = 12  # 離螢幕邊緣的距離(96 dpi 的像素)
WDA_EXCLUDEFROMCAPTURE = 0x11


def heightOf(config):
    """使用者拖拉調整後記住的高度(沒調整過就用預設)，限制在合理範圍"""
    try:
        return min(MAX_HEIGHT, max(MIN_HEIGHT, int(config.get("floatLogHeight"))))
    except (TypeError, ValueError):
        return LOG_SIZE[1]


def dpiScale(hwnd):
    try:
        return ctypes.windll.user32.GetDpiForWindow(hwnd) / 96
    except Exception:
        return 1.0


def styleOverlay(hwnd):
    """半透明、不搶焦點(不然顯示時會把遊戲從最上層拉掉)、不出現在工作列，並從螢幕截圖中排除(不影響畫面比對)"""
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    style |= win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
    win32gui.SetLayeredWindowAttributes(hwnd, 0, LOG_ALPHA, win32con.LWA_ALPHA)
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def targetPosition(hwnd, mode, config, collapsed=False):
    """浮動日誌視窗左上角要放的位置(螢幕座標)。mode: screen(螢幕左下角，舊設定沿用這個名稱)、topLeft、topRight、bottomRight(螢幕四個角)、custom(上次拖曳的位置)"""
    scale = dpiScale(hwnd)
    width, height = round(LOG_SIZE[0] * scale), round((COLLAPSED_HEIGHT if collapsed else heightOf(config)) * scale)
    margin = round(MARGIN * scale)
    if mode == "custom":
        try:
            return int(config.get("floatLogX")), int(config.get("floatLogY"))
        except (TypeError, ValueError):
            pass  # 還沒拖曳過，退回螢幕左下角
    left, top, right, bottom = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY))["Work"]
    x = right - width - margin if mode in ("topRight", "bottomRight") else left + margin
    y = top + margin if mode in ("topLeft", "topRight") else bottom - height - margin
    return x, y


def placeAndShow(hwnd, x, y, heightCss):
    """移到指定位置並顯示(置頂、不取得焦點)。heightCss 是視窗高度(96 dpi 的像素)"""
    scale = dpiScale(hwnd)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOPMOST,
        int(x),
        int(y),
        round(LOG_SIZE[0] * scale),
        round(heightCss * scale),
        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
    )


def setHeight(hwnd, heightCss, anchor="bottom"):
    """改變視窗高度。anchor: bottom 表示下緣不動(視窗通常貼著畫面下方，往上長)，top 表示上緣不動。回傳新的左上角位置"""
    scale = dpiScale(hwnd)
    left, top, _, bottom = win32gui.GetWindowRect(hwnd)
    height = round(heightCss * scale)
    if anchor == "bottom":
        top = bottom - height
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, left, top, round(LOG_SIZE[0] * scale), height, win32con.SWP_NOACTIVATE)
    return left, top
