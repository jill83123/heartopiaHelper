"""標準 MIDI 檔(.mid)解析: 只取出每個音的 開始時間、長度、音高，換算成秒。不依賴外部套件。

一個「聲部」是同一音軌裡同一個頻道的所有音(很多歌的主旋律、伴奏、鼓各自一個聲部)。
打擊樂頻道(第 10 頻道，程式內是 9)沒有音高可言，標記 percussion 讓呼叫端預設略過。
"""

import struct


class MidiError(Exception):
    pass


class Part:
    def __init__(self, index, name, channel):
        self.index = index  # 在這個檔案裡的順序，設定檔用它記錄選了哪些聲部
        self.name = name
        self.channel = channel
        self.notes = []  # (開始秒, 長度秒, 音高 0~127)

    @property
    def percussion(self):
        return self.channel == 9

    def info(self):
        pitches = [n[2] for n in self.notes]
        return {
            "index": self.index,
            "name": self.name,
            "channel": self.channel + 1,
            "noteCount": len(self.notes),
            "minPitch": min(pitches) if pitches else None,
            "maxPitch": max(pitches) if pitches else None,
            "percussion": self.percussion,
        }


class Song:
    def __init__(self, parts, duration):
        self.parts = parts
        self.duration = duration


def _readVarLen(data, pos):
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("檔案提前結束")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("檔案格式不正確")


def _parseTrack(data):
    """回傳 (名稱, 速度變更[(tick, 微秒/四分音符)], 音[(開始tick, 結束tick, 頻道, 音高)])"""
    pos, tick, status = 0, 0, 0
    name = ""
    tempos, notes = [], []
    open_ = {}  # (頻道, 音高) -> [開始 tick, ...]
    while pos < len(data):
        delta, pos = _readVarLen(data, pos)
        tick += delta
        if pos >= len(data):
            break
        byte = data[pos]
        if byte == 0xFF:  # meta
            if pos + 1 >= len(data):
                break
            kind = data[pos + 1]
            length, pos = _readVarLen(data, pos + 2)
            body = data[pos : pos + length]
            pos += length
            if kind == 0x51 and length == 3:
                tempos.append((tick, int.from_bytes(body, "big")))
            elif kind == 0x03 and not name:
                name = body.decode("utf-8", errors="ignore").strip() or body.decode("latin-1", errors="ignore").strip()
            elif kind == 0x2F:
                break
            continue
        if byte in (0xF0, 0xF7):  # sysex
            length, pos = _readVarLen(data, pos + 1)
            pos += length
            continue
        if byte & 0x80:
            status = byte
            pos += 1
        elif not status:
            raise MidiError("檔案格式不正確")
        kind, channel = status & 0xF0, status & 0x0F
        size = 1 if kind in (0xC0, 0xD0) else 2
        args = data[pos : pos + size]
        pos += size
        if len(args) < size:
            break
        if kind == 0x90 and args[1] > 0:
            open_.setdefault((channel, args[0]), []).append(tick)
        elif kind == 0x80 or kind == 0x90:  # 0x90 力度 0 等於放開
            starts = open_.get((channel, args[0]))
            if starts:
                notes.append((starts.pop(0), tick, channel, args[0]))
    for (channel, pitch), starts in open_.items():  # 沒有放開的音: 收在最後一個事件
        for start in starts:
            notes.append((start, max(tick, start + 1), channel, pitch))
    return name, tempos, notes


def parseMidi(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise MidiError(f"讀不到檔案：{e}")
    if data[:4] != b"MThd":
        raise MidiError("這不是 MIDI 檔（副檔名通常是 .mid 或 .midi）")
    headerLen = struct.unpack(">I", data[4:8])[0]
    _, trackCount, division = struct.unpack(">HHH", data[8:14])
    pos = 8 + headerLen

    tracks = []
    for _ in range(trackCount):
        if data[pos : pos + 4] != b"MTrk":
            break
        length = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        tracks.append(_parseTrack(data[pos + 8 : pos + 8 + length]))
        pos += 8 + length
    if not tracks:
        raise MidiError("檔案裡沒有任何音軌")

    # 速度表: 所有音軌的速度變更合在一起(格式 1 的速度都放在第一軌)
    tempos = sorted({tick: us for _, ts, _ in tracks for tick, us in ts}.items())
    if not tempos or tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))  # 預設每分鐘 120 拍

    if division & 0x8000:  # SMPTE 時間: 每秒固定的 tick 數
        fps = 256 - (division >> 8)
        tickSeconds = lambda tick: tick / (fps * (division & 0xFF))  # noqa: E731
    else:
        # 預先算好每個速度段開始時的秒數，tick 換秒就是「前面累積的秒數 + 這段內的 tick」
        marks, seconds = [], 0.0
        for i, (tick, us) in enumerate(tempos):
            marks.append((tick, seconds, us))
            if i + 1 < len(tempos):
                seconds += (tempos[i + 1][0] - tick) * us / 1e6 / division
        starts = [m[0] for m in marks]

        def tickSeconds(tick):
            lo, hi = 0, len(starts) - 1
            while lo < hi:  # 二分搜尋最後一個開始 tick <= tick 的速度段
                mid = (lo + hi + 1) // 2
                if starts[mid] <= tick:
                    lo = mid
                else:
                    hi = mid - 1
            startTick, startSec, us = marks[lo]
            return startSec + (tick - startTick) * us / 1e6 / division

    parts, duration = [], 0.0
    for trackIndex, (name, _, notes) in enumerate(tracks):
        byChannel = {}
        for startTick, endTick, channel, pitch in notes:
            start, end = tickSeconds(startTick), tickSeconds(endTick)
            byChannel.setdefault(channel, []).append((start, end - start, pitch))
            duration = max(duration, end)
        for channel in sorted(byChannel):
            label = name or f"音軌 {trackIndex + 1}"
            part = Part(len(parts), label if len(byChannel) == 1 else f"{label}（頻道 {channel + 1}）", channel)
            part.notes = sorted(byChannel[channel])
            parts.append(part)
    if not parts:
        raise MidiError("檔案裡沒有任何音符")
    return Song(parts, duration)
