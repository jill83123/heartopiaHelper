"""釣到新魚（新圖鑑）的處理: 偵測右上角的「新圖鑑！」字樣、星星卡與畫面下方的對話框，點掉對話框，
並等整段動畫結束。釣到新魚後角色會轉向鏡頭拋不出去，之後要轉回正面（轉身在 fishing.py）。
要搭配 GameUI 使用"""

import math
import time
import cv2
import numpy as np
from tools.finder import STAR_HSV_HIGH, STAR_HSV_LOW

# 新圖鑑的星星卡: 比對金色色塊和標準五角星的形狀
STAR_SHAPE_MAX = 0.08  # 金色色塊的輪廓和五角星的差異(Hu 矩距離，越小越像，不受大小影響)要在這個值以內才算星星；實測真星星 0.004~0.02，背包圖示的黃色邊角、燈光 0.17 以上
STAR_INNER_RATIO = 0.55  # 遊戲星星圖示的內凹程度(內圈半徑 ÷ 外圈半徑)，胖胖的五角星
# 釣到新紀錄的魚時，畫面會停在對話框(「太棒了！釣到了新紀錄的…」)，要點一下才會繼續，期間沒有任何選單。
# 對話框是畫面下方中間一塊白色圓角框(文字內容每次不同)，所以看框左右兩端(沒有文字的地方)是不是整片白的來判斷
DIALOG_SIDES = ((0.24, 0.32), (0.68, 0.76))  # 對話框左右兩端的範圍(佔遊戲畫面寬的比例)
DIALOG_TOP, DIALOG_BOTTOM = 0.83, 0.93  # 對話框上下範圍(佔遊戲畫面高的比例)
DIALOG_WHITE = 235  # 三個色頻都要這麼亮才算白(實測對話框 246 以上，沙灘、浪花都在 200 以下或不成片)
DIALOG_FILL = 0.97  # 兩端都要有這個比例以上是白的
NEW_FISH_CLICK_SECONDS = 25  # 偵測到新魚後這麼久(秒)內，介面被蓋住就點螢幕點掉對話框
NEW_FISH_SETTLE_SECONDS = 7  # 偵測到新魚後，至少等這麼久(動畫還在播)才讓角色轉回正面


def _starContour(size=200):
    """標準五角星的輪廓，拿來和畫面上的金色色塊比形狀"""
    points = []
    for k in range(10):
        radius = (size / 2 - 4) * (1 if k % 2 == 0 else STAR_INNER_RATIO)
        angle = -math.pi / 2 + k * math.pi / 5
        points.append([size / 2 + radius * math.cos(angle), size / 2 + radius * math.sin(angle)])
    canvas = np.zeros((size, size), np.uint8)
    cv2.fillPoly(canvas, [np.array(points, np.int32)], 255)
    return max(cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], key=cv2.contourArea)


_STAR_CONTOUR = _starContour()


class NewFishMixin:
    """新魚偵測。使用前先呼叫 _initNewFish()"""

    def _initNewFish(self):
        self._newFishAt = 0.0  # 偵測到新魚的時間
        self._newFish = False  # 剛釣到新圖鑑的魚，角色轉向鏡頭了，下次拋竿前要轉回正面
        self._dialogClicks = 0

    def _dismissSpot(self, img):
        """點掉新魚對話框時點的位置(影像座標): 遊戲畫面下方中間，那裡沒有按鈕"""
        x, y, w, h = self._gameRect(img)
        return (x + w * 0.5, y + h * 0.88)

    def _dismissDialog(self, pos, stopEvent):
        """點掉對話框。左鍵與右鍵輪流點(不確定哪一個遊戲吃，手機版只有點擊)"""
        self._dialogClicks += 1
        x, y = round(pos[0] + self._origin[0]), round(pos[1] + self._origin[1])
        self.backend.moveTo(x, y)
        if stopEvent.wait(0.2):
            return
        if self._dialogClicks % 2 == 1 or self.isAdb:
            self.backend.click(x, y)
        else:
            self.backend.rightClick(x, y)

    def _markNewFish(self, source="", img=None):
        """釣到新魚(新圖鑑的提示卡，或「太棒了！釣到了新紀錄的…」對話框): 記下來，不通知也不寫日誌(只寫 flow.log)。
        角色會轉向鏡頭，下次拋竿前要轉回正面"""
        if self._newFish:
            return
        self._newFish = True
        self._newFishAt = time.time()
        self._trace(f"釣到新魚（{source}）")
        if img is not None:  # 誤判時看這張就知道是哪一種判斷把什麼當成了新魚
            self._saveShot(img, {"提示卡": "newfish_card", "收竿時的提示卡": "newfish_reel", "對話框": "newfish_dialog"}.get(source, "newfish"))

    def _hasNewFishCard(self, img):
        """釣到新魚的提示: 右上角有「新圖鑑！」字樣，或有一張星星卡(新的星數才會出現，釣過的不會有)，任一個就算"""
        x, y, w, h = self._gameRect(img)
        region = img[max(0, y) : y + int(h * 0.3), max(0, x + int(w * 0.7)) : x + w]  # 畫面中上方的「鬥竿專家」之類橫幅也是白字藍底，所以不看中間
        if region.size > 0 and self._find(region, "newFishMark") is not None:
            return True
        return self._hasStarCard(img)

    def _hasDialog(self, img):
        """畫面下方中間有白色圓角對話框(「太棒了！第一次釣到…」「釣到了新紀錄的…」)。
        文字在框中間，所以只看左右兩端兩塊，兩塊都是整片白色才算，沙灘、浪花不會"""
        x, y, w, h = self._gameRect(img)
        for left, right in DIALOG_SIDES:
            roi = img[max(0, y + int(h * DIALOG_TOP)) : y + int(h * DIALOG_BOTTOM), max(0, x + int(w * left)) : x + int(w * right), :3]
            if roi.size == 0 or (roi.min(axis=2) >= DIALOG_WHITE).mean() < DIALOG_FILL:
                return False
        return True

    def _hasStarCard(self, img):
        """右側(離最上排的圖示列一段距離)有金黃色實心星星 = 新星數的星星卡。
        不靠模板，找大小相近、形狀像五角星的金色小塊；範圍避開最上排的活動圖示，也避開畫面中央角色旁邊的閃光"""
        x, y, w, h = self._gameRect(img)
        roi = img[max(0, y + int(h * 0.15)) : y + int(h * 0.4), max(0, x + int(w * 0.7)) : x + w, :3]
        if roi.size == 0:
            return False
        mask = cv2.inRange(cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV), STAR_HSV_LOW, STAR_HSV_HIGH)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, count):
            sx, sy, sw, sh, area = stats[i]
            if h * 0.012 <= sw <= h * 0.05 and h * 0.012 <= sh <= h * 0.05 and 0.7 <= sw / sh <= 1.4 and area >= sw * sh * 0.4:
                # 大小、顏色對了還要形狀像五角星: 背包圖示的黃色邊角、燈光、閃光也是金黃色的小塊，單看大小會被騙
                blob = np.pad((labels[sy : sy + sh, sx : sx + sw] == i).astype(np.uint8) * 255, 1)
                contour = max(cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], key=cv2.contourArea)
                if cv2.matchShapes(_STAR_CONTOUR, contour, cv2.CONTOURS_MATCH_I1, 0) <= STAR_SHAPE_MAX:
                    return True
        return False

    def _waitNewFishGone(self, stopEvent):
        """等新魚的整段動畫結束(對話框、提示卡、「魚名+1」的彈出)，角色才吃得到轉向的操作。
        動畫期間按了沒用，從偵測到新魚起至少等 NEW_FISH_SETTLE_SECONDS 秒，期間提示卡還在就繼續等"""
        while time.time() - self._newFishAt < NEW_FISH_SETTLE_SECONDS or self._hasNewFishCard(self._shot()):
            if stopEvent.wait(0.3) or time.time() - self._newFishAt > 20:
                return
        stopEvent.wait(0.5)
