"""樂器的按鍵配置、自訂按鈕的解析，以及把 MIDI 的音換成要按的鍵。

配置用 (按鍵, MIDI 音高) 表示，音高用 MIDI 編號: C4(中央 Do)=60，每升高一個半音加 1、一個八度加 12。
遊戲畫面上每排鍵的高低音由「1 上面的點」決定(點在上=高音、沒有=中音、點在下=低音)。
"""

import json
import re
from collections import Counter
from tools.midi import MidiError, parseMidi

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MIN_GAP = 0.02  # 同一個鍵連續兩次之間，放開到再按下的最短間隔(秒)
MIN_HOLD = 0.04  # 每個音至少按住多久(秒)
MIN_RETRIGGER = 0.05  # 同一個鍵兩次按下的最短間隔(秒)，更近的當成重複音略過


def _row(whites, blacks, base):
    """一排琴鍵: whites 是 Do~Si 七個白鍵，blacks 是 5 個黑鍵(Do#、Re#、Fa#、Sol#、La#)，base 是這排 Do 的音高"""
    layout = [(key, base + offset) for key, offset in zip(whites, (0, 2, 4, 5, 7, 9, 11))]
    if blacks:
        layout += [(key, base + offset) for key, offset in zip(blacks, (1, 3, 6, 8, 10))]
    return layout


# 鋼琴: 三排。上排高音(Q~U、黑鍵 2 3 5 6 7)再加一顆更高的 Do(I)，中排 Z~M，下排 , . / O P [ ]
PIANO = _row("qwertyu", "23567", 72) + [("i", 84)] + _row("zxcvbnm", "sdghj", 60) + _row([",", ".", "/", "o", "p", "[", "]"], [("l"), (";"), "0", "-", "="], 48)
# 兩排白鍵的樂器(畫面上 Q~U 是高音 1~7、I 是更高的 Do，A~J 是中音 1~7)，只有按鍵、沒有黑鍵
TWO_ROW = _row("qwertyu", "", 72) + [("i", 84)] + _row("asdfghj", "", 60)

BUILTIN = {"piano": PIANO, "violin": TWO_ROW}
BUILTIN_NAMES = {"piano": "鋼琴", "violin": "小提琴、大提琴、豎琴等兩排樂器"}

_KEY_RE = re.compile(r"^[a-z0-9,./;'\[\]\\\-=`]$|^space$")


def noteName(pitch):
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def parseNoteName(text):
    """'C4'、'c#5'、'60' -> MIDI 音高；格式不對回傳 None"""
    text = str(text).strip()
    if text.isdigit():
        return int(text) if 0 <= int(text) <= 127 else None
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", text)
    if not match:
        return None
    pitch = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[match.group(1).upper()]
    pitch += {"#": 1, "b": -1, "": 0}[match.group(2)]
    pitch += (int(match.group(3)) + 1) * 12
    return pitch if 0 <= pitch <= 127 else None


def parseCustom(text):
    """自訂按鈕的設定(JSON 文字: [{"key": "q", "pitch": 60}, ...])-> [(按鍵, 音高)]；格式不對丟 ValueError(訊息給使用者看)"""
    try:
        items = json.loads(text or "[]")
    except json.JSONDecodeError:
        raise ValueError("自訂按鈕的設定壞掉了，請重新設定")
    layout, seen = [], set()
    for item in items:
        key = str(item.get("key", "")).strip().lower()
        pitch = item.get("pitch")
        if not _KEY_RE.match(key):
            raise ValueError(f"自訂按鈕的按鍵「{key}」不支援（支援英文字母、數字與標點）")
        if not isinstance(pitch, int) or not 0 <= pitch <= 127:
            raise ValueError(f"自訂按鈕「{key}」的音高不正確")
        if key in seen:
            raise ValueError(f"自訂按鈕的按鍵「{key}」重複了")
        seen.add(key)
        layout.append((key, pitch))
    return layout


def getLayout(config):
    """目前選的樂器配置 [(按鍵, 音高)]；自訂設定有問題丟 ValueError"""
    kind = config.get("instrumentType", "piano")
    if kind == "custom":
        return parseCustom(config.get("instrumentCustom", "[]"))
    if kind.startswith("saved:"):
        name = kind[6:]
        try:
            saved = json.loads(config.get("instrumentSaved", "{}") or "{}")
        except json.JSONDecodeError:
            saved = {}
        if name not in saved:
            raise ValueError(f"找不到存下來的樂器「{name}」，請重新選擇樂器")
        return parseCustom(json.dumps(saved[name]))
    return list(BUILTIN.get(kind, PIANO))


def layoutForUi():
    """給前端: 內建樂器的配置，讓「複製成自訂」可以直接帶入"""
    return {name: [{"key": key, "pitch": pitch} for key, pitch in layout] for name, layout in BUILTIN.items()}


def fitPitch(pitch, pitches, fold=True):
    """把音高換成這個樂器有的音。回傳 (音高, 有沒有完全相符, 有沒有搬過八度)。
    超出音域的音: fold=True 整個八度搬進音域；fold=False 不彈(音高回傳 None)。
    音域內找不到完全一樣的音就取最近的(同樣近取低的)"""
    low, high = pitches[0], pitches[-1]
    folded = False
    if pitch < low or pitch > high:
        if not fold:
            return None, False, False
        if high - low >= 11:
            while pitch < low:
                pitch += 12
                folded = True
            while pitch > high:
                pitch -= 12
                folded = True
    best = min(pitches, key=lambda p: (abs(p - pitch), p))
    return best, best == pitch, folded


def fitStats(noteCounts, pitches, shift, fold=True):
    """移調 shift 後: (完全相符的音數, 被搬了八度的音數, 沒有而改用相近音的音數, 超出音域而不彈的音數)"""
    exact = folded = approx = skipped = 0
    for pitch, count in noteCounts.items():
        fitted, ok, fo = fitPitch(pitch + shift, pitches, fold)
        if fitted is None:
            skipped += count
            continue
        exact += count if ok else 0
        folded += count if fo else 0
        approx += 0 if ok else count
    return exact, folded, approx, skipped


def autoTranspose(noteCounts, pitches, fold=True):
    """自動決定要升降幾個半音，原則是盡量不動原曲的高低。
    搬八度模式: 先看整首歌的中間音高(中位數)，已經在樂器音域內就不動，太低或太高才整個八度搬進來；
    再在 -6~+5 個半音內，挑能讓最多音剛好落在樂器有的音上的移法(沒有黑鍵的樂器需要)。同分先取移動少的，再取被搬八度少的。
    略過模式: 在 -12~+12 個半音內，挑「不彈的音與相近音最少」的移法，同分取移動少的。noteCounts 是 {音高: 出現次數}"""
    if not fold:
        best, bestKey = 0, None
        for shift in range(-12, 13):
            _, _, approx, skipped = fitStats(noteCounts, pitches, shift, False)
            key = (-(skipped * 2 + approx), -abs(shift))
            if bestKey is None or key > bestKey:
                best, bestKey = shift, key
        return best
    ordered = sorted(noteCounts.elements())
    median = ordered[len(ordered) // 2]
    base = 0
    while median + base < pitches[0]:
        base += 12
    while median + base > pitches[-1]:
        base -= 12
    best, bestKey = base, None
    for semitone in range(-6, 6):
        shift = base + semitone
        exact, folded, _, _ = fitStats(noteCounts, pitches, shift)
        key = (exact, -abs(semitone), -folded)
        if bestKey is None or key > bestKey:
            best, bestKey = shift, key
    return best


def toKeyNotes(notes, layout, shift=0, fold=True):
    """notes: [(開始秒, 長度秒, 音高)] 換成這個樂器的按鍵: [(開始秒, 長度秒, 按鍵)]，以及沒有完全相符而用近似音的數量。fold=False 時超出音域的音直接不彈"""
    layout = sorted(layout, key=lambda item: item[1])
    pitches = [pitch for _, pitch in layout]
    keyOf = {}
    for key, pitch in layout:
        keyOf.setdefault(pitch, key)  # 多個鍵同音高就用第一個
    result, approx = [], 0
    for start, length, pitch in sorted(notes):
        fitted, exact, _ = fitPitch(pitch + shift, pitches, fold)
        if fitted is None:
            continue
        approx += 0 if exact else 1
        result.append((start, length, keyOf[fitted]))
    return result, approx


def buildEvents(notes, layout, shift=0, speed=1.0, fold=True):
    """notes: [(開始秒, 長度秒, 音高)]。回傳 (事件, 略過的音數, 沒有完全相符而用近似音的數量)"""
    keyNotes, approx = toKeyNotes(notes, layout, shift, fold)
    events, dropped = eventsFromKeyNotes(keyNotes, speed)
    return events, dropped, approx


def eventsFromKeyNotes(keyNotes, speed=1.0):
    """keyNotes: [(開始秒, 長度秒, 按鍵)]。回傳 (事件, 略過的音數)。
    事件是 [(秒, 動作, 按鍵)] 按時間排好，動作 0=放開、1=按下(同一時間先放開再按下)"""
    perKey = {}
    for start, length, key in sorted(keyNotes):
        perKey.setdefault(key, []).append([start / speed, max(length / speed, MIN_HOLD)])

    events, dropped = [], 0
    for key, items in perKey.items():
        kept = []
        for item in items:
            if kept and item[0] - kept[-1][0] < MIN_RETRIGGER:
                dropped += 1  # 太近的重複音，遊戲也分不出來
                continue
            if kept:
                kept[-1][1] = min(kept[-1][1], item[0] - kept[-1][0] - MIN_GAP)  # 同一個鍵要先放開才能再按
            kept.append(item)
        for start, length in kept:
            events.append((start, 1, key))
            events.append((start + length, 0, key))
    events.sort(key=lambda e: (e[0], e[1]))
    return events, dropped


def pitchCounts(notes):
    return Counter(pitch for _, _, pitch in notes)


def parseTrackIds(text):
    """'0,2,3' -> [0, 2, 3]；空字串或有不是數字的項目就略過"""
    return [int(x) for x in str(text).replace("，", ",").split(",") if x.strip().isdigit()]


def loadPlan(config):
    """依設定讀 MIDI、選聲部、決定移調。回傳 dict: song、parts、notes(選中聲部的音)、layout、shift、shiftText。
    讀不了丟 MidiError、ValueError(訊息給使用者看)"""
    path = config.get("instrumentMidiPath", "")
    if not path:
        raise MidiError("請先選擇 MIDI 檔")
    song = parseMidi(path)
    chosen = parseTrackIds(config.get("instrumentTracks", ""))
    parts = [p for p in song.parts if p.index in chosen] if chosen else [p for p in song.parts if not p.percussion]
    if not parts:
        raise MidiError("選的聲部裡沒有音符，請重新選擇要彈的聲部")
    notes = sorted(n for p in parts for n in p.notes)
    layout = getLayout(config)
    if not layout:
        raise ValueError("這個樂器沒有任何按鈕")
    pitches = sorted(p for _, p in layout)
    fold = config.get("instrumentOutOfRange", "fold") != "skip"
    transpose = str(config.get("instrumentTranspose", "auto")).strip().lower()
    if transpose == "auto":
        shift = autoTranspose(pitchCounts(notes), pitches, fold)
        shiftText = f"自動移調 {shift:+d} 個半音"
    else:
        shift = int(float(transpose))
        shiftText = f"移調 {shift:+d} 個半音"
    return {"song": song, "parts": parts, "notes": notes, "layout": layout, "shift": shift, "shiftText": shiftText, "fold": fold}
