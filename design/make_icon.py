"""產生應用程式圖示 icon.ico（只用 numpy + opencv，不需另外裝 Pillow）。
用法: python design/make_icon.py
"""
import struct
from pathlib import Path

import cv2
import numpy as np

SIZE = 1024  # 先以大圖繪製，縮小時才平滑
OUT = Path(__file__).resolve().parent.parent / "icon.ico"
SIZES = [256, 128, 64, 48, 32, 24, 16]


def hexBgr(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (4, 2, 0))


def heartPoints(cx, cy, scale):
    t = np.linspace(0, 2 * np.pi, 600)
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    pts = np.stack([cx + x * scale, cy - y * scale], axis=1)
    return pts.astype(np.float32)


def fillAA(canvas, mask, color, alpha=1.0):
    """依遮罩（0~1 float）把顏色疊上去。"""
    m = (mask * alpha)[..., None]
    canvas[:] = canvas * (1 - m) + np.array(color, np.float32) * m


def polyMask(pts):
    m = np.zeros((SIZE, SIZE), np.uint8)
    cv2.fillPoly(m, [np.round(pts * 16).astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=4)
    return m.astype(np.float32) / 255


def ellipseMask(cx, cy, rx, ry):
    m = np.zeros((SIZE, SIZE), np.uint8)
    cv2.ellipse(m, (int(cx * 16), int(cy * 16)), (int(rx * 16), int(ry * 16)), 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
    return m.astype(np.float32) / 255


def roundedMask(margin, radius):
    m = np.zeros((SIZE, SIZE), np.uint8)
    a, b = margin, SIZE - margin
    cv2.rectangle(m, (a + radius, a), (b - radius, b), 255, -1, cv2.LINE_AA)
    cv2.rectangle(m, (a, a + radius), (b, b - radius), 255, -1, cv2.LINE_AA)
    for cx, cy in ((a + radius, a + radius), (b - radius, a + radius), (a + radius, b - radius), (b - radius, b - radius)):
        cv2.circle(m, (cx, cy), radius, 255, -1, cv2.LINE_AA)
    return m.astype(np.float32) / 255


def roundedRect(x, y, w, h):
    m = np.zeros((SIZE, SIZE), np.uint8)
    r = h / 2
    cv2.rectangle(m, (int(x + r), int(y)), (int(x + w - r), int(y + h)), 255, -1, cv2.LINE_AA)
    cv2.circle(m, (int(x + r), int(y + r)), int(r), 255, -1, cv2.LINE_AA)
    cv2.circle(m, (int(x + w - r), int(y + r)), int(r), 255, -1, cv2.LINE_AA)
    return m.astype(np.float32) / 255


def sparkle(cx, cy, r):
    t = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    rad = np.where(np.arange(8) % 2 == 0, r, r * 0.28)
    return np.stack([cx + rad * np.sin(t), cy - rad * np.cos(t)], axis=1).astype(np.float32)


def render():
    # 背景：天空藍上下漸層（配合介面的天藍主題）
    top, bottom = np.array(hexBgr("#a6dbf8"), np.float32), np.array(hexBgr("#5fb4ea"), np.float32)
    g = np.linspace(0, 1, SIZE, dtype=np.float32)[:, None, None]
    img = top * (1 - g) + bottom * g
    img = np.repeat(img, SIZE, axis=1).copy()

    # 雲朵：沿用介面標題列的雲（膠囊底 + 兩顆圓）
    def cloud(x, y, k, alpha):
        # x, y 為膠囊左上角；尺寸單位同 ui/style.css 的 .cloud.a（90x24）
        m = np.zeros((SIZE, SIZE), np.float32)
        w, h = 90 * k, 24 * k
        m = np.maximum(m, roundedRect(x, y, w, h))
        m = np.maximum(m, ellipseMask(x + (14 + 18) * k, y + (-18 + 18) * k, 18 * k, 18 * k))
        m = np.maximum(m, ellipseMask(x + (42 + 14) * k, y + (-12 + 14) * k, 14 * k, 14 * k))
        fillAA(img, m, hexBgr("#ffffff"), alpha)

    cloud(150, 500, 8.0, 0.95)

    # 圓角方形外框
    alpha = roundedMask(32, 215)
    bgra = np.dstack([np.clip(img, 0, 255), alpha * 255]).astype(np.uint8)
    return bgra


def writeIco(bgra, path):
    images = []
    for s in SIZES:
        small = cv2.resize(bgra, (s, s), interpolation=cv2.INTER_AREA)
        ok, png = cv2.imencode(".png", small)
        images.append((s, png.tobytes()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    art = render()
    writeIco(art, OUT)
    print("已輸出", OUT)
