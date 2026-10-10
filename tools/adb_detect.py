import csv
import io
import os
import re
import socket
import struct
import subprocess

HOST = "127.0.0.1"
ADB_SERVER_PORT = 5037

# 模擬器的執行程序名稱(小寫、不含 .exe) -> 顯示名稱。比對時用「包含」
EMULATOR_PROCESSES = {
    "mumu": "MuMu",
    "hd-player": "BlueStacks",
    "bluestacks": "BlueStacks",
    "dnplayer": "雷電 LDPlayer",
    "ldvboxheadless": "雷電 LDPlayer",
    "ld9boxheadless": "雷電 LDPlayer",
    "nox": "夜神 Nox",
    "memu": "逍遙 MEmu",
    "qemu-system": "模擬器",
}

# 找不到程序佔用的埠時，另外試的常見埠
COMMON_PORTS = (
    [5555 + 2 * i for i in range(6)]  # BlueStacks、雷電
    + [7555, 62001, 62025, 21503, 21513, 21523]  # MuMu 6、夜神、逍遙
    + [16384 + 32 * i for i in range(10)]  # MuMu 12 的多開
)

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(cmd, timeout=10):
    result = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return result.stdout.decode("utf-8", errors="ignore")


def _emulatorPids():
    """執行中的模擬器程序: {pid: 顯示名稱}"""
    pids = {}
    try:
        out = _run(["tasklist", "/FO", "CSV", "/NH"])
    except Exception:
        return pids
    for row in csv.reader(io.StringIO(out)):
        if len(row) < 2 or not row[1].isdigit():
            continue
        name = row[0].lower().removesuffix(".exe")
        for key, label in EMULATOR_PROCESSES.items():
            if key in name:
                pids[int(row[1])] = label
                break
    return pids


def _listeningPorts():
    """本機正在監聽的埠: {埠: 程序 pid}"""
    ports = {}
    try:
        out = _run(["netstat", "-ano", "-p", "TCP"])
    except Exception:
        return ports
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3].upper() == "LISTENING":
            m = re.search(r":(\d+)$", parts[1])
            if m and parts[1].startswith(("127.0.0.1", "0.0.0.0")):
                ports[int(m.group(1))] = int(parts[4])
    return ports


def _isOpen(port):
    with socket.socket() as s:
        s.settimeout(0.15)
        return s.connect_ex((HOST, port)) == 0


def _speaksAdb(port):
    """送 ADB 的連線封包(CNXN)，對方回 CNXN 或 AUTH 才是 ADB。比直接 adb connect 快很多，不是 ADB 的埠不用等逾時"""
    payload = b"host::\0"
    cnxn = 0x4E584E43
    header = struct.pack("<6I", cnxn, 0x01000001, 256 * 1024, len(payload), sum(payload), cnxn ^ 0xFFFFFFFF)
    try:
        with socket.create_connection((HOST, port), timeout=0.5) as s:
            s.settimeout(0.8)
            s.sendall(header + payload)
            return s.recv(4) in (b"CNXN", b"AUTH")
    except OSError:
        return False


def candidatePorts():
    """可能是模擬器 ADB 的埠: {埠: 顯示名稱}。優先用模擬器程序正在監聽的埠，再補上有開的常見埠"""
    pids = _emulatorPids()
    found = {}
    for port, pid in _listeningPorts().items():
        if pid in pids and port >= 1024 and port != ADB_SERVER_PORT:
            found[port] = pids[pid]
    for port in COMMON_PORTS:
        if port not in found and _isOpen(port):
            found[port] = ""
    return {port: name for port, name in found.items() if _speaksAdb(port)}


def _priority(port):
    """同一台模擬器有多個 ADB 埠時的挑選順序，越小越優先: 各家的標準埠 > 其他 > MuMu 共用的 7555"""
    if port == 7555:
        return 2
    if port in COMMON_PORTS:
        return 0 if port >= 16384 else 1
    return 1


def _adb(adbPath, args, timeout=10):
    return _run([adbPath] + args, timeout)


def _onlineDevices(adbPath):
    """adb devices 裡狀態正常(device)的序號"""
    serials = []
    for line in _adb(adbPath, ["devices"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _identity(adbPath, serial):
    """同一台模擬器可能同時從好幾個埠連得上(例如 MuMu 的 7555)，用 android_id 判斷是不是同一台"""
    try:
        return _adb(adbPath, ["-s", serial, "shell", "settings", "get", "secure", "android_id"], timeout=5).strip() or serial
    except Exception:
        return serial


def detectAdbDevices(adbPath):
    """找出可以連線的模擬器，回傳 [{"address": "127.0.0.1:16416", "name": "MuMu"}]；adb 本身有問題時丟 RuntimeError"""
    if not adbPath or (os.path.isabs(adbPath) and not os.path.isfile(adbPath)):
        raise RuntimeError(f"找不到 adb: {adbPath}")
    try:
        _adb(adbPath, ["start-server"], timeout=15)
    except FileNotFoundError:
        raise RuntimeError(f"找不到 adb: {adbPath}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("adb 無回應")

    names = candidatePorts()
    for port in names:
        try:
            _adb(adbPath, ["connect", f"{HOST}:{port}"], timeout=6)
        except subprocess.TimeoutExpired:
            pass

    online = _onlineDevices(adbPath)
    # 沒連上或不是 adb 的埠，順手斷開，避免留下 offline 的殘留
    for port in names:
        if f"{HOST}:{port}" not in online:
            try:
                _adb(adbPath, ["disconnect", f"{HOST}:{port}"], timeout=3)
            except Exception:
                pass

    groups = {}
    for serial in online:
        port = int(serial.rsplit(":", 1)[1]) if re.search(r":\d+$", serial) else 0
        groups.setdefault(_identity(adbPath, serial), []).append((port, serial))

    devices = []
    for members in groups.values():
        # 同一台有多個位址時，挑最像標準埠的那個
        port, serial = sorted(members, key=lambda m: (_priority(m[0]), m[0]))[0]
        devices.append({"address": serial, "name": names.get(port, "")})
    return sorted(devices, key=lambda d: d["address"])
