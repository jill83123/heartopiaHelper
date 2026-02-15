import cv2
import mss
import numpy as np
import os
import sys
import time
import tkinter as tk
import win32api
import win32con


def getResourcePath(relativePath):
    if hasattr(sys, "frozen"):
        exeDir = os.path.dirname(sys.executable)
        return os.path.normpath(os.path.join(exeDir, "_internal", relativePath))
    else:
        return os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), relativePath))


def selectRegion():
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.config(bg="black")

    canvas = tk.Canvas(root, bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    selection = {"start": None, "end": None, "rect": None}

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
        return (xMin, yMin, xMax - xMin, yMax - yMin)
    else:
        return None


def selectPoint():
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.config(bg="black")

    point = {"pos": None}

    def onClick(event):
        point["pos"] = (event.x, event.y)
        root.quit()
        root.destroy()

    root.bind("<Button-1>", onClick)
    root.bind("<Escape>", lambda e: root.destroy())

    root.mainloop()

    return point["pos"]


def captureScreen(x, y, width, height):
    with mss.mss() as sct:
        monitor = {"top": y, "left": x, "width": width, "height": height}
        sctImg = sct.grab(monitor)
        img = np.array(sctImg)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        return img  # BGR Numpy Array


def matchTemplate(screenImg, templatePath, threshold=0.8, scales=[1.0], isDebugMode=False):
    templatePath = getResourcePath(templatePath)
    templateName = os.path.basename(templatePath)

    screenGray = cv2.cvtColor(screenImg, cv2.COLOR_BGR2GRAY)
    templateOrig = cv2.imread(templatePath, cv2.IMREAD_COLOR)

    bestPoints = []
    rectangles = []

    for scale in scales:
        template = cv2.resize(templateOrig, (0, 0), fx=scale, fy=scale)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape[::-1]
        res = cv2.matchTemplate(screenGray, template_gray, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        for x, y in zip(*loc[::-1]):
            matchScore = res[y, x]
            if isDebugMode:
                print(f"找到 {templateName}，當前比對率: {matchScore:.2f}，閥值: {threshold}")
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


def readConfig():
    config = {}
    configPath = getResourcePath("config.ini")
    with open(configPath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


def writeConfig(key, value):
    config = {}

    # 讀取現有配置
    configPath = getResourcePath("config.ini")
    with open(configPath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()

    config[key] = str(value)

    # 寫回檔案
    with open(configPath, "w", encoding="utf-8") as f:
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
