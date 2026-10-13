import threading
import time

from tools.notify import notify

# 倒數幾秒時會再長一波草(成熟期結束前)
FINAL_WEED_SECONDS = 30


def buildSchedule(cropSeconds):
    """作物時間 / 3 = 每個時期的長度。萌芽期不長草，成長期、成熟期開始時各長一波，倒數 30 秒左右再長一波。
    回傳 [(第幾秒長草, 名稱)]，倒數那一波若和成熟期重疊就省略"""
    stage = cropSeconds / 3
    schedule = [(stage, "成長期"), (stage * 2, "成熟期")]
    if cropSeconds - FINAL_WEED_SECONDS > stage * 2:
        schedule.append((cropSeconds - FINAL_WEED_SECONDS, f"倒數 {FINAL_WEED_SECONDS} 秒"))
    return schedule


class CropReminder:
    """只負責在長草前跳出桌面通知，不操作遊戲"""

    def __init__(self, cropSeconds, leadSeconds, log=None, remainingSeconds=None):
        """remainingSeconds: 中途加入時作物還剩多久(沒給就是從頭開始)，已經過的長草時間點不會再提醒"""
        self.cropSeconds = cropSeconds
        self.startElapsed = 0 if remainingSeconds is None else max(cropSeconds - remainingSeconds, 0)
        self.leadSeconds = leadSeconds
        self.log = log or (lambda msg: None)
        self.isStart = False
        self.startedAt = None
        # (提醒時間, 長草時間, 名稱)；提醒時間不早於開始的那一刻
        self.events = [(max(at - leadSeconds, 0), at, name) for at, name in buildSchedule(cropSeconds)]
        self._stop = threading.Event()

    def start(self):
        self.isStart = True
        self.startedAt = time.monotonic() - self.startElapsed
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.isStart = False
        self._stop.set()

    def elapsed(self):
        return time.monotonic() - self.startedAt

    def snapshot(self):
        """給介面顯示: 每一波的狀態與距離長草還有幾秒，以及整體剩餘時間"""
        elapsed = self.elapsed()
        return {
            "total": self.cropSeconds,
            "remaining": max(self.cropSeconds - elapsed, 0),
            "events": [{"name": name, "weedIn": max(at - elapsed, 0), "done": elapsed >= at} for _, at, name in self.events],
        }

    def _run(self):
        joined = f"，已進行 {self.startElapsed / 60:g} 分鐘" if self.startElapsed else ""
        self.log(f"開始計時，作物時間 {self.cropSeconds / 60:g} 分鐘{joined}，提早 {self.leadSeconds:g} 秒提醒")
        for remindAt, weedAt, name in self.events:
            if weedAt <= self.elapsed():  # 中途加入時已經長過的，不再提醒
                continue
            if self._stop.wait(max(remindAt - self.elapsed(), 0)):
                return
            lead = max(round(weedAt - self.elapsed()), 0)
            text = f"{name}要長草了" + (f"（約 {lead} 秒後）" if lead else "")
            self.log(text)
            notify("拔草提醒", text)
        if not self._stop.wait(max(self.cropSeconds - self.elapsed(), 0)):
            self.log("作物時間到，提醒結束")
            self.isStart = False
