"""定時項目(丟維修盒、吃食物，釣魚還有噴香水、丟誘魚器)的排程與執行: 什麼時候該做、做失敗怎麼辦。
要搭配 GameUI 使用(用到它的截圖、點擊、背包操作)。子類別要實作 _enabledTasks(依執行順序列出啟用的項目)與每個項目的 _doXxx"""

import random
import time

INTERVAL_JITTER = 0.1  # 每次間隔額外延後 0~10%
MAX_TASK_FAILURES = 3  # 噴香水、吃食物、丟誘魚器連續失敗幾次就不再嘗試
TOOL_BROKEN_RETRY = 40  # 丟完維修盒後，這麼久(秒)內提示還在畫面上也不再重複丟


class OutOfStock(RuntimeError):
    """背包裡已經沒有這個道具(用完了)"""


class TimedTasks:
    """定時項目的排程與執行。使用前先呼叫 _initTasks()"""

    LABELS = {"repair": "丟維修盒", "food": "吃食物", "perfume": "噴香水", "bait": "丟誘魚器"}

    def _initTasks(self):
        self.lastDone = {}  # 項目名稱 -> 上次執行的時間
        self.jitter = {}  # 項目名稱 -> 這一輪間隔額外延後的比例
        self.disabled = set()  # 執行失敗(找不到道具等)而本次不再嘗試的項目
        self._failCount = {}  # 項目名稱 -> 目前連續失敗幾次
        self._toolBroken = False  # 看到「工具耐久耗盡」的提示了，要先丟維修盒
        self._toastScore = 0.0
        self._toolFixedAt = None  # 最近一次丟維修盒的時間
        if not self._repairEnabled():
            self.disabled.add("repair")  # 使用者沒勾「丟維修盒」: 工具耐久耗盡也不修
        self._repairOffNotified = False

    def _repairEnabled(self):
        """設定有沒有勾「丟維修盒」(沒勾就連工具耐久耗盡也不丟)"""
        return str(self.config.get("fishingRepairEnabled", "True")).lower() != "false"

    def _enabledTasks(self):
        """依執行順序列出啟用的項目: (名稱, 執行函式)"""
        raise NotImplementedError

    def _interval(self, name):
        """設定的間隔(秒)"""
        if name == "food":
            return 0  # 吃食物沒有間隔，看到體力不足的提示才吃
        return self._minutes("fishingRepairMinutes")

    def _jitterCap(self, name, interval):
        """間隔最多額外延後幾秒"""
        return interval * INTERVAL_JITTER

    def _markDone(self, name):
        """記下執行時間，並抽出下一輪間隔要額外延後幾秒"""
        interval = self._interval(name)
        self.lastDone[name] = time.time()
        self.jitter[name] = random.uniform(0, self._jitterCap(name, interval))

    def _isDue(self, name, img, casting=False):
        last = self.lastDone.get(name)
        wait = self._interval(name) + self.jitter.get(name, 0)
        if name == "food":
            if casting:
                return False
            self._noteHunger(img)
            return self._hungry
        if name == "repair" and (self._toolBroken or self._toolToastSeen(img)):
            # 畫面出現「工具耐久耗盡，請使用維修盒」: 不等間隔，馬上丟(剛丟完提示可能還沒消失，隔一段時間再說)
            if self._toolFixedAt is None or time.time() - self._toolFixedAt >= TOOL_BROKEN_RETRY:
                if not self._toolBroken:
                    self.setLog("工具耐久耗盡，先丟維修盒")
                self._toolBroken = True
                return True
        if name == "repair" and not self._flag("fishingUseRepair"):
            return False  # 沒勾定時丟維修盒: 只在耐久耗盡時才丟
        return last is None or time.time() - last >= wait

    def _minutes(self, key):
        return float(self.config.get(key)) * 60

    def _dueTasks(self, img, casting=False):
        due = []
        for name, fn in self._enabledTasks():
            started = time.time()
            if self._isDue(name, img, casting):
                due.append((name, fn))
            if time.time() - started > 2:  # 單一項目的判斷花太久，記下是哪一個
                self._trace(f"判斷「{name}」要不要執行花了 {time.time() - started:.1f} 秒")
        return due

    def _toolToastSeen(self, img):
        """連續兩次都看到「工具耐久耗盡」提示才算(單張誤判不理它)；確認時把畫面存進 debug/ 方便事後檢查"""
        if not self._findToolBroken(img):
            self._toolToastHits = 0
            if self._toastScore >= 0.35 and time.time() - getattr(self, "_toastNearAt", 0) > 5:  # 有點像但不夠像: 存下來(debug/toastnear_*)，用來檢查為什麼沒判到
                self._toastNearAt = time.time()
                self._saveShot(img, "toastnear")
            return False
        self._toolToastHits = getattr(self, "_toolToastHits", 0) + 1
        if self._toolToastHits < 2:
            return False
        self._saveShot(img, "toast")
        return True

    def _findToolBroken(self, img):
        """畫面上有「工具耐久耗盡」提示嗎"""
        return self._findToast(img, "toolBroken")

    def _doRepair(self, stopEvent):
        """丟維修盒。設定了「離開現在的位置後再丟」就先往後退一步，丟完(成功失敗都一樣)再走回原位"""
        if not self._flag("fishingRepairLeave"):
            return self._throwRepairBox(stopEvent)
        self.setLog("先往後退，離開現在的位置")
        if not self._stepBack(stopEvent):
            return False
        try:
            return self._throwRepairBox(stopEvent)
        finally:
            if not stopEvent.is_set():  # 按了停止就不再動角色
                self._stepForward(stopEvent)

    def _throwRepairBox(self, stopEvent):
        # 依畫面狀態重試: 工具欄沒開就點 T 鈕，開了就點維修盒，點完面板會自動關閉。
        # (視窗剛切到前景時第一下點擊會被吃掉，所以不能假設點了就一定開了)
        for _ in range(4):
            img = self._shot()
            itemPos = self._find(img, "repairBoxItem")
            if not itemPos:
                toolPos = self._find(img, "toolBtn")
                if not toolPos and self.isAdb:
                    raise RuntimeError("找不到工具欄圖示")
                # 電腦版: 工具欄圖示是半透明的，在淺色沙灘上認不出來，改按鍵盤 T(圖示上寫的快捷鍵)
                if toolPos:
                    if not self._click(toolPos, stopEvent) or stopEvent.wait(1.2):
                        return False
                else:
                    self.backend.pressKey("t")
                    if stopEvent.wait(1.2):
                        return False
                continue
            if not self._click(itemPos, stopEvent) or stopEvent.wait(1.5):
                return False
            if not self._find(self._shot(), "repairBoxItem"):
                return not stopEvent.wait(1.5)
        if not self._closeToolPanel(stopEvent):
            self.setLog("⚠️ 工具欄無法關閉，請手動關閉")
        raise RuntimeError("無法從工具欄丟出維修盒(可能還在冷卻或上一個還沒消失)")

    def _closeToolPanel(self, stopEvent):
        """工具欄(T)沒有要用就要關起來，開著會擋住拋竿。回傳是否已關閉"""
        for _ in range(3):
            img = self._shot()
            if not self._find(img, "repairBoxItem"):
                return True
            toolPos = self._find(img, "toolBtn")
            if toolPos and not self._click(toolPos, stopEvent):
                return False
            if not toolPos and not self.isAdb:
                self.backend.pressKey("t")  # 同上: 認不到圖示就按 T 關
            if stopEvent.wait(1.0):
                return False
        return not self._find(self._shot(), "repairBoxItem")

    def _runTask(self, name, fn, stopEvent):
        if name == "food":
            self._hungry = False  # 這一輪吃完(或失敗)之前不重複觸發，下次再看到提示才吃
        if name != "food":  # 吃食物要等開了背包、確定不夠飽才寫(見 _shouldEat)
            self.setLog(f"{self.LABELS[name]}中...")
        self._trace(f"任務開始 {name}")
        self._meals = 0
        try:
            ok = fn(stopEvent)
            self._trace(f"任務結束 {name} ok={ok}")
            if name == "food" and self._meals:
                self.setLog(f"共吃了 {self._meals} 份")
        except OutOfStock as e:
            if name == "food":
                # 沒食物不影響繼續做事(體力沒了遊戲自己會顯示)，不停用、不閃爍，下個間隔再看看補了沒
                if not self._noFoodNotified:
                    self._noFoodNotified = True
                    self._alert(f"背包裡沒有可以吃的{e}了，請補充", flash=False)
                self._markDone(name)
            else:
                self._alert(f"背包裡的{e}用完了（數量 0），請補充後重新開始", flash=(name == "perfume"))
                self.disabled.add(name)
            self._closeBag(stopEvent)
            self._closeToolPanel(stopEvent)
            return
        except RuntimeError as e:
            if name == "repair":
                # 維修盒可能只是還在冷卻，等下一個間隔再試
                self.setLog(f"⚠️ {self.LABELS[name]}失敗：{e}，下個間隔再試")
                self._markDone(name)
                self._toolBroken = False  # 不然提示還在畫面上時會馬上又丟一次，一直失敗一直重試
                self._toolFixedAt = time.time()
            else:
                # 畫面操作卡住(找不到按鈕等): 下個間隔再試，連續失敗 MAX_TASK_FAILURES 次才停用並通知，之前只寫日誌，避免一直跳
                fails = self._failCount.get(name, 0) + 1
                self._failCount[name] = fails
                if fails >= MAX_TASK_FAILURES:
                    self._alert(f"{self.LABELS[name]}連續失敗 {fails} 次（{e}），不再嘗試，請確認畫面與設定後重新開始")
                    self.disabled.add(name)
                else:
                    retry = "下次體力不足時再試" if name == "food" else "下個間隔再試"  # 吃食物沒有固定間隔，是看到「體力不足」提示才吃
                    self.setLog(f"⚠️ {self.LABELS[name]}失敗（第 {fails} 次）：{e}，{retry}")
                    self._markDone(name)
            self._closeBag(stopEvent)
            self._closeToolPanel(stopEvent)
            return
        if ok:
            self._failCount.pop(name, None)
            self._markDone(name)
            if name == "repair":
                self._toolBroken = False
                self._toolFixedAt = time.time()
            if name == "food":
                self._noFoodNotified = False
