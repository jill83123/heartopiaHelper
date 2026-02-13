import keyboard
import os
import sys
import threading
import webview
from api import Api

if hasattr(sys, "frozen"):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

api = Api()


def resourcePath(relativePath):
    if hasattr(sys, "frozen"):
        return os.path.join(sys._MEIPASS, relativePath)
    return os.path.join(os.path.dirname(__file__), relativePath)


def listenEsc():
    while True:
        keyboard.wait(api.config.get("stopKey"))
        api.stop()


def main():
    winWidth = 600
    winHeight = 1012
    html_path = resourcePath("ui/index.html")
    webview.create_window(title="心動小助手", url=html_path, js_api=api, width=winWidth, height=winHeight, x=0, y=0)
    webview.start()


if __name__ == "__main__":
    threading.Thread(target=listenEsc, daemon=True).start()
    main()
