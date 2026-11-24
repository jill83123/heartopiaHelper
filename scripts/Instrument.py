import ctypes
import os
import sys
import time
import win32api
import win32process
from scripts.common.base import BaseTask
from tools.instruments import BUILTIN_NAMES, eventsFromKeyNotes, fitStats, loadPlan, pitchCounts, toKeyNotes
from tools.midi import MidiError
from tools.tools import LOG_DIVIDER

CHECK_GAME_EVERY = 1.0  # 每隔多久確認一次遊戲視窗還在(秒)
LEAD_IN = 0.3  # 倒數結束到第一個音之間留的時間(秒)，讓第一個音也能準時
LOOP_PAUSE = 1.5  # 重複播放時，兩輪之間停多久(秒)


def formatTime(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class Instrument(BaseTask):
    """自動彈奏: 讀 MIDI 檔，把每個音換成遊戲裡的按鍵，照時間按下與放開。
    角色要先坐上樂器(畫面上看得到琴鍵)。只支援前景模式，不看畫面，所以也不需要模板圖"""

    dialogGuard = False  # 只送鍵盤、不點畫面，不會誤點到確認視窗

    def __init__(self, setLog, backend, config):
        super().__init__(setLog, backend, config)
        self._held = set()

    def _threadTargets(self):
        return [self._loop]

    # ---------- 準備 ----------
    def _prepare(self):
        """讀檔、選聲部、算好所有要按的鍵。回傳 (事件, 總秒數)"""
        config = self.config
        plan = loadPlan(config)
        layout = plan["layout"]
        parts = plan["parts"]
        speed = float(config.get("instrumentSpeed", "1.0"))

        keyNotes, approx = toKeyNotes(plan["notes"], layout, plan["shift"], plan["fold"])
        events, dropped = eventsFromKeyNotes(keyNotes, speed)
        if not events:
            raise MidiError("沒有可以彈的音")
        offset = events[0][0]  # 開頭的空白直接跳過
        events = [(t - offset, kind, key) for t, kind, key in events]

        kind = config.get("instrumentType", "piano")
        self.setLog(f"歌曲：{os.path.basename(config.get('instrumentMidiPath', ''))}（{formatTime(events[-1][0])}）")
        self.setLog(f"樂器：{BUILTIN_NAMES.get(kind) or (kind[6:] if kind.startswith('saved:') else '自訂按鈕')}")
        self.setLog(f"聲部：{'、'.join(p.name for p in parts)}")
        self.setLog(f"{plan['shiftText']}，速度 {speed:g} 倍，共 {len(keyNotes)} 個音")
        _, folded, _, skipped = fitStats(pitchCounts(plan["notes"]), sorted(p for _, p in layout), plan["shift"], plan["fold"])
        total = len(plan["notes"])
        if folded:
            self.setLog(f"ℹ️ {folded} 個音（共 {total} 個）超出樂器音域，整個八度搬進範圍" + ("；比例偏高，聽起來可能怪怪的，可以改成「略過超出音域的音」試試" if folded > total * 0.1 else ""))
        if skipped:
            self.setLog(f"ℹ️ {skipped} 個音（共 {total} 個）超出樂器音域，沒有彈")
        if approx:
            self.setLog(f"ℹ️ {approx} 個音這個樂器沒有，改用最接近的音（往低的方向取）")
        if dropped:
            self.setLog(f"ℹ️ {dropped} 個音和前一個音靠太近，略過")
        return events, events[-1][0]

    # ---------- 演奏 ----------
    def _waitUntil(self, target, stopEvent):
        """等到 perf_counter 的 target 時刻；中途被要求停止回傳 False。前面用事件等待(不吃 CPU)，最後一小段空轉等待，時間才準"""
        while True:
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return not stopEvent.is_set()
            if stopEvent.is_set():
                return False
            if remaining > 0.03:
                stopEvent.wait(remaining - 0.02)
            else:
                time.sleep(0)

    def _play(self, events, stopEvent):
        """從頭彈一次；回傳是不是完整彈完"""
        start = time.perf_counter() + LEAD_IN
        lastCheck = time.perf_counter()
        i = 0
        while i < len(events):
            t, down, _ = events[i]
            keys = []  # 同一時間、同一動作的鍵(和弦)合成一批，一次送出
            while i < len(events) and events[i][0] == t and events[i][1] == down:
                keys.append(events[i][2])
                i += 1
            if not self._waitUntil(start + t, stopEvent):
                return False
            if time.perf_counter() - lastCheck > CHECK_GAME_EVERY:
                self.backend.ensureGame()
                lastCheck = time.perf_counter()
            if down:
                self.backend.keysDown(keys)
                self._held.update(keys)
            else:
                self.backend.keysUp(keys)
                self._held.difference_update(keys)
        return True

    def _releaseAll(self):
        if self._held:
            try:
                self.backend.keysUp(list(self._held))
            except Exception:
                pass
        self._held.clear()

    def _loop(self, stopEvent):
        try:
            events, duration = self._prepare()
        except (MidiError, ValueError, OSError) as e:
            self.setLog(f"❌ {e}")
            stopEvent.set()
            self.setLog(LOG_DIVIDER)
            return

        self.backend.focusGame()
        delay = max(0, float(self.config.get("instrumentStartDelay", "3")))
        if delay and stopEvent.wait(0):
            return
        for left in range(int(delay), 0, -1):
            self.setLog(f"{left} 秒後開始，請確認角色已坐上樂器")
            if stopEvent.wait(1):
                return

        # Windows 預設計時器約 15 毫秒一格，會讓音的時間不準；演奏期間調成 1 毫秒。
        # 另外把這條執行緒的優先權調高、縮短 Python 切換執行緒的間隔，其他執行緒(畫面輪詢等)不會卡到按鍵的時間
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
        oldSwitch = sys.getswitchinterval()
        sys.setswitchinterval(0.0005)
        try:
            win32process.SetThreadPriority(win32api.GetCurrentThread(), win32process.THREAD_PRIORITY_HIGHEST)
        except Exception:
            pass
        try:
            loop = str(self.config.get("instrumentLoop", "False")).lower() == "true"
            rounds = 0
            while not stopEvent.is_set():
                self.setLog("開始演奏")
                finished = self._play(events, stopEvent)
                self._releaseAll()
                if not finished:
                    break
                rounds += 1
                if not loop:
                    self.setLog("演奏結束")
                    stopEvent.set()
                    self.setLog(LOG_DIVIDER)
                    break
                self.setLog(f"第 {rounds} 輪演奏完畢")
                if stopEvent.wait(LOOP_PAUSE):
                    break
        finally:
            sys.setswitchinterval(oldSwitch)
            winmm.timeEndPeriod(1)
            self._releaseAll()
