"""模擬器模式連線測試

用法 (專案根目錄):
    python -m tools.adb_check [adb路徑] [裝置位址]
    python -m tools.adb_check adb 127.0.0.1:5555

會連線、截圖存成 adb_check.png 並印出解析度。不會送出任何點擊。
若要測試點擊，在最後加上 tap X Y，例如:
    python -m tools.adb_check adb 127.0.0.1:5555 tap 100 100
"""
import sys
import cv2
from tools.backend import AdbBackend


def main():
    args = sys.argv[1:]
    adbPath = args[0] if len(args) > 0 else "adb"
    device = args[1] if len(args) > 1 else ""

    backend = AdbBackend(adbPath, device)
    backend.check()
    img, _, _ = backend.captureFull()
    cv2.imwrite("adb_check.png", img)
    print(f"連線成功，解析度 {img.shape[1]}x{img.shape[0]}，已存成 adb_check.png")

    if len(args) >= 5 and args[2] == "tap":
        backend.click(int(args[3]), int(args[4]))
        print(f"已送出點擊 ({args[3]}, {args[4]})")


if __name__ == "__main__":
    main()
