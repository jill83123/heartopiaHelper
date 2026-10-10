import keyboard
import os
import sys
import threading
import time
import webview
from api import Api
from tools.tools import APP_TITLE, migrateConfig
from tools.updater import cleanupLeftovers, isFrozen

if hasattr(sys, "frozen"):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

if isFrozen():
    cleanupLeftovers()  # 清掉上次更新留下的暫存檔

migrateConfig()  # 補上新版新增的設定項目，保留使用者原本的值

# 打包成沒有主控台的視窗程式後 stdout 是 None，print 會出錯，改丟到空裝置
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

api = Api()


def resourcePath(relativePath):
    if hasattr(sys, "frozen"):
        return os.path.join(sys._MEIPASS, relativePath)
    return os.path.join(os.path.dirname(__file__), relativePath)


def setDevIcon():
    """直接用 python main.py 執行時，視窗和工作列會顯示 python.exe 的圖示；這裡改成 icon.ico。
    打包後的 exe 本身就帶有圖示，不需要做這件事"""
    if isFrozen() or sys.platform != "win32":
        return
    import ctypes
    iconPath = resourcePath("icon.ico")
    if not os.path.exists(iconPath):
        return
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.LoadImageW.restype = ctypes.c_void_p
    hwnd = None
    for _ in range(50):  # 視窗建好需要一點時間
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        return
    # LR_LOADFROMFILE(0x10)；WM_SETICON(0x80)，wParam 1=大圖示(工作列)、0=小圖示(標題列)
    for wParam, size in ((1, 32), (0, 16)):
        icon = user32.LoadImageW(None, iconPath, 1, size, size, 0x10)
        if icon:
            user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, wParam, ctypes.c_void_p(icon))


def onStarted():
    threading.Thread(target=setDevIcon, daemon=True).start()
    api._prepareFloatLog()


def listenStopKey():
    """全域監聽停止鍵(設定檔的 stopKey，可以是單一按鍵如 F8，或組合鍵如 ctrl+shift+q)。
    設定頁改了按鍵會自動換成新的，不用重開程式；按鍵設定有誤時記錄後繼續等下一次修改，不影響主程式"""
    current = None
    handle = None
    while True:
        stopKey = (api.config.get("stopKey") or "").strip()
        if stopKey != current:
            if handle is not None:
                try:
                    keyboard.remove_hotkey(handle)
                except Exception:
                    pass
                handle = None
            current = stopKey
            if stopKey:
                try:
                    handle = keyboard.add_hotkey(stopKey, api.stop)
                except Exception as e:
                    print(f"停止鍵「{stopKey}」無法使用: {e}")
            else:
                print("config.ini 缺少 stopKey，停止鍵無法使用")
        time.sleep(0.5)


_closing = False


def shutdown():
    """先讓兩個視窗的頁面停止輪詢(避免視窗銷毀的瞬間還有呼叫在途中，噴出 WebView2 已釋放的錯誤)，
    關掉浮動日誌(它沒有關閉鈕，不關的話程式不會結束)，再真正關閉主視窗"""
    try:
        mainWindow.evaluate_js("markClosing()")
    except Exception:
        pass
    time.sleep(0.3)
    api._closeFloatLog()
    mainWindow.destroy()


def onClosing():
    """使用者按關閉時: 先擋下這次關閉，改在另一條執行緒收尾。
    不能在這個事件裡直接呼叫 evaluate_js，事件會等處理完才繼續，而 evaluate_js 又要等視窗執行緒，兩邊互等會卡死"""
    global _closing
    if _closing:
        return None
    _closing = True
    threading.Thread(target=shutdown, daemon=True).start()
    return False


def main():
    if not isFrozen() and sys.platform == "win32":
        # 換掉 python.exe 的應用程式識別，工作列才不會把視窗歸到 Python 底下、沿用 Python 的圖示
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("heartopiaHelper")
    winWidth = 600
    winHeight = 900
    html_path = resourcePath("ui/index.html")
    # 貼齊螢幕最左邊、垂直置中；取不到螢幕大小時退回左上角
    try:
        screen = webview.screens[0]
        posY = max((screen.height - winHeight) // 2, 0)
    except Exception:
        posY = 0
    # text_select=True: pywebview 預設禁止選取文字，開啟後畫面上的文字（含日誌）才能選取、Ctrl+C 複製
    global mainWindow
    mainWindow = webview.create_window(
        title=APP_TITLE,
        url=html_path,
        js_api=api,
        width=winWidth,
        height=winHeight,
        x=0,
        y=posY,
        text_select=True,
    )
    mainWindow.events.closing += onClosing
    try:
        webview.start(onStarted)
    except KeyboardInterrupt:
        # 在終端機按 Ctrl+C 結束: 安靜地收尾，不要印出一長串錯誤
        api.stop()
        for close in (api._closeFloatLog, mainWindow.destroy):
            try:
                close()
            except Exception:
                pass


if __name__ == "__main__":
    threading.Thread(target=listenStopKey, daemon=True).start()
    main()
