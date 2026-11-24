# CLAUDE.md — agent 入口索引

查找程式時先看這份，不要全專案 grep。每一行只寫「這個東西負責什麼」，細節進檔案看。
行為取捨與「為什麼這樣做」不在這裡，見 [docs/decisions.md](docs/decisions.md)；使用者說明見 [README.md](README.md)；從原始碼執行、打包、發佈流程見 [docs/development.md](docs/development.md)。

## 維護規則（必讀）

只要新增、刪除、改名檔案、路由（`Api` 公開方法）、資料表（本專案無資料庫，對應設定項目）、store 欄位（`Api` 實例欄位、前端全域狀態）、公開函式、設定項（`CONFIG_DEFAULTS` 的 key）或指令，**就在同一次改動內更新本檔對應的那一行**。
- 新增 → 補一行；刪除 → 刪那一行；改名 → 改那一行；只是改內部實作、行數變動 → 不用動。
- 本檔內寫的檔名、符號名、class 名都要在程式碼裡真的存在；改完自己對一次，不憑印象寫。
- 一行一個檔案，說明保持一句話；符號只列「別人會來查的入口」，不列私有細節。
- 使用者看得到的功能有增減或改變（新功能、拿掉的功能、行為改變），或資料模型、產品決策變了，也要在同一次改動內對照更新 [README.md](README.md) 的功能清單與 [docs/decisions.md](docs/decisions.md)。只寫已存在於程式的內容，改前先對照程式確認；純畫面微調（顏色、間距）不用。

## 專案概要

Windows 桌面輔助程式（遊戲 Heartopia「心動小鎮」）：用截圖比對模板找按鈕，模擬滑鼠鍵盤（前景模式）或走 ADB 控制模擬器（模擬器模式）。
Python 3 + pywebview（介面是 HTML/JS/CSS，透過 `js_api` 呼叫 `Api`）。無資料庫、無伺服器、無測試。使用者語言是繁體中文，日誌、錯誤訊息、註解都用繁中。
命名：Python 與 JS 都是 camelCase（函式、變數、設定 key）；私有加底線。

## 指令

```bash
python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt   # 環境（Windows）
python main.py                                                                    # 執行
python -m tools.adb_check adb 127.0.0.1:5555 [tap X Y]                            # 測模擬器連線，存 adb_check.png，不點擊除非加 tap
venv\Scripts\python -m PyInstaller --noconfirm main.spec                          # 打包，必須用 venv 的 python
python design/make_icon.py                                                        # 重新產生 icon.ico
.\release.ps1 [-Draft]                                                            # 發佈（版本取自 version.py）
```

## 架構

```
main.py ──建立視窗 js_api=Api──▶ ui/index.html + script.js ──window.pywebview.api.*──▶ api.py
                                                                       │ startXxx() 建立任務
                                         tools/backend.py createBackend(config) 回傳 ScreenBackend 或 AdbBackend
                                                                       ▼
scripts/*  任務類別（BaseTask.start 開執行緒）──截圖/點擊──▶ backend ──模板比對──▶ tools/tools.py、tools/finder.py
```

- 前端每次動作都呼叫 `Api` 方法；狀態與日誌靠前端定時輪詢（`apiGetStatus`、`apiGetNextLogs`），後端不主動推送（唯一例外是 `evaluate_js`：關閉時呼叫兩個視窗的 `markClosing()`，見 `main.shutdown`、`Api._closeFloatLog`；以及 `Api.apiFloatStart` 替主視窗按開始鈕）。
- 同時只能跑一個任務（`Api.isRunning`）；農作物提醒 `CropReminder` 例外，可與其他任務同時跑，不走 backend。
- 兩個 pywebview 視窗共用同一個 `Api`：主視窗 `ui/index.html`、置頂懸浮日誌 `ui/log.html`（`Api._floatHwnd` 建立，啟動後由 `main.prepareFloatLogOnce` 預先建好並隱藏）。
- 任務繼承：`Fishing(ConsumablesMixin, NewFishMixin, FoodMixin, TimedTasks, GameUI)`；`GatherBase(FoodMixin, TimedTasks, GameUI)`；`GameUI(DebugMixin, BaseTask)`；`Cooking`、`Puzzle`、`Pumpkin`、`SandSculpture` 是 `(DebugMixin, BaseTask)`；`SnowCarving`、`Instrument` 是 `BaseTask`；`CropReminder` 不繼承任何類別。`SnowCarving`、`Pumpkin`、`SandSculpture` 各持有一個 `self.food = AutoFood(...)`（組合而非繼承）。
- 比對基準是 1600x900 遊戲畫面；其他解析度由 `ScaleResolver` / `tools/finder.py` 縮放模板。座標常數一律以 1600 寬為基準寫。
- 模板找法：`GameUI._templatePath` 依 `templateDirs` 順序找，模擬器模式每個資料夾先找 `adb/<name>.png`；`BaseTask._getMatchCords` 只用單一 `templateDir`（料理、雪雕走這條）。

## 根目錄

- `main.py` — 進入點：切工作目錄、`migrateConfig()`、建主視窗、`listenStopKey` 全域停止鍵、`shutdown`/`onClosing` 收尾、`startup.log` 啟動診斷、`setDevIcon`。
- `api.py` — 前端唯一的後端介面 `class Api`，也負責任務生命週期、日誌佇列、懸浮日誌視窗、更新（細節見下方「api.py 公開方法」）。
- `version.py` — `VERSION` 字串，發佈與更新檢查都讀它。
- `config.default.ini` — 只有 12 個最常用 key 的初始範本；真正完整的預設值在 `CONFIG_DEFAULTS`。
- `config.ini` — 使用者本機設定（git 忽略；打包後放 exe 旁；`key=value` 一行一個，值全是字串）。
- `main.spec` — PyInstaller 設定（datas：`config.default.ini`、`ui`、`templates`、`tools/ADBKeyboard.apk`；`console=False`；icon=`icon.ico`）。根目錄另有被 git 忽略的 `heartopiaHelper.spec`，不是建置用的。
- `release.ps1` — 一鍵發佈：讀 `version.py` → PyInstaller → zip → `gh release create`，附 `RELEASE_NOTES.md`。
- `RELEASE_NOTES.md` — 這一版的更新說明（`## 修正`、`## 優化` 兩節的 Markdown），發佈時當 Release 內文，更新提示窗也會顯示。
- `.gitignore` — 忽略 `config.ini`、`userdata/`、`debug/`、`adb_check.png`、`build/`、`dist/`、`venv/`、`*.spec`（`main.spec` 例外）。
- `requirements.txt` — 相依套件（keyboard、mss、numpy、opencv-python、pywebview、pywin32、pyinstaller、winrt-*）。
- `pyproject.toml` — 只有 black `line-length = 140`。
- `icon.ico` — 應用程式圖示，由 `design/make_icon.py` 產生。
- `README.md` — 給使用者的說明（功能、下載、操作模式、設定），不放開發與發佈內容。
- `docs/development.md` — 給開發者：從原始碼執行、`config.ini` 各設定項說明、打包、自動更新與發佈流程。
- `docs/decisions.md` — 設計決策（釣魚、食物、採集、拼圖、其他的取捨）；改行為前先看對應段落。
- `design/design-system.html` — 設計系統預覽頁（色彩、字體、間距、圓角、元件、料理頁組合預覽），僅供參考；實際以 `ui/style.css` 為準。兩邊淺色顏色 token 同名同值（`--font` 例外：設計稿多了沒打包的 Quicksand），設計稿多了 `--fs-xs`～`--fs-xl`、`--s1`～`--s6`、`--info`、`--salmon`、`--warn`（`style.css` 沒有），`style.css` 多了 `--field-gap`、`--brand-edge`、`--warn-text`。
- `design/make_icon.py` — 只用 numpy + opencv 畫 `icon.ico`。
- `userdata/`、`debug/`、`build/`、`dist/`、`venv/` — 執行或建置產生，git 忽略（見資料層）。

## 資料層（設定與本機資料）

沒有資料庫。唯一的持久資料是 `config.ini`（文字，key=value）與 `userdata/`、`debug/`。

- `tools/tools.py` `CONFIG_DEFAULTS` — **所有設定項與預設值的唯一來源**（值全是字串，布林寫 `"True"`/`"False"`）。
- `tools/tools.py` `readConfig` / `writeConfig` / `writeConfigMany` / `resetConfig` — 讀、寫（`_configWriteLock` 保護）、還原。`readConfig` 會用預設值補缺的 key。
- `tools/tools.py` `RESET_EXCLUDED_KEYS` — 「還原設定值」不動的項目（操作模式、ADB、解析度、停止鍵、懸浮日誌外觀等）。
- `tools/tools.py` `migrateConfig` + `CONFIG_VERSION` + `CONFIG_VALUE_MIGRATIONS` — 啟動時補新 key、修型別錯的值、把仍是舊預設值的換成新預設值；改動前備份到 `userdata/config.ini.bak`。預設值要改時：`CONFIG_VERSION` +1 並加一組 `{版本: {key: (舊, 新)}}`。
- `tools/tools.py` `_migrateFoodBan5` — 舊 `*FoodBan5` 設定換算成 `*FoodStars` 後移除。
- `tools/tools.py` `getConfigPath` / `getUserDataPath(name)` — 設定檔與 `userdata/` 的位置（打包後在 exe 旁，原始碼執行在專案根）。
- `tools/tools.py` `DEFAULT_THRESHOLDS` + `STRICTNESS_OFFSETS` + `getThreshold(config, name)` — 各模板比對閥值（不開放使用者逐項調，只能用 `matchStrictness` loose/normal/strict 整體加減）；**新模板一定要在這裡加一筆，否則 `getThreshold` 會 KeyError**。
- `tools/tools.py` `validateConfig(config, task)` — 啟動前檢查設定，回傳錯誤字串或 `None`（task：cooking、snowCarving、fishing、fishingAlt、plant、wood、puzzle、pumpkin、sandSculpture、instrument）；`fishingItemChecks` 檢查釣魚與採集共用的定時項目。
- `userdata/dish.png`、`userdata/startup.log`、`userdata/config.ini.bak` — 菜品記錄截圖、啟動診斷、設定備份（`getUserDataPath` 取得）。
- `debug/flow.log`、`debug/*.png` — 除錯模式（`debugMode`）才產生，`tools/debug.py` 寫入，上限 `DebugMixin.DEBUG_MAX_FILES`。
- `debug/carve_*.csv` — 除錯模式下南瓜雕刻每輪的灰線、目標、把手、記號位置（`Pumpkin._dumpCarveRec` 結束時寫入），用來事後算追線準度與抖動；不受上述上限管。

設定 key 命名規則（新增時照著取名）：
- `fishing*` 前景定時、`fishingAlt*` 背景定時（模擬器）、`plant*` 採集無工具、`wood*` 採集有工具、`crop*` 拔草提醒、`floatLog*` 懸浮日誌、`cook*` / `snow*` 料理、雪雕、`instrument*` 彈奏（`instrumentCustom` 是自訂按鈕的 JSON 字串、`instrumentSaved` 是存下來的自訂樂器）；`snow*`/`pumpkin*`/`sand*` 另有 `UseFood`、`FoodName`、`FoodStars`、`FoodKeepLast`、`FoodUntil` 吃食物設定（`AutoFood` 的 `foodConfig` 換成 `fishing*`；`validateConfig` 靠 `FOOD_TASKS` 檢查）。
- `lastRun<任務名首字大寫>`（如 `lastRunCooking`）由 `Api._runSnapshot` 寫入；`floatLogX`、`floatLogY` 由 `apiSaveFloatLogPos` 寫入；`configVersion` 由 `migrateConfig` 寫入；這幾類不在 `CONFIG_DEFAULTS` 內。
- 吃食物、丟維修盒的邏輯只認 `fishing*` 欄位：採集與背景定時的對應欄位由 `Api._gatherConfig`、`Api._altConfig` 換成 `fishing*` 再交給任務（對照表 `GATHER_FOOD_KEYS`、`ALT_KEYS`）。新增這類子選項要同步改這兩張表。

新增一個設定項要動的地方：`CONFIG_DEFAULTS` → （要檢查就加 `validateConfig`）→ （不該被還原就加 `RESET_EXCLUDED_KEYS`）→ `ui/index.html` 或 `script.js` 的輸入元件（`class="setting"` / `"setting-check"` + `data-key`）→ 讀它的任務程式 → 若是 `fishingAlt*`/採集食物維修欄位，改 `ALT_KEYS`/`GATHER_FOOD_KEYS`。

## 後端：api.py

`class Api` 的 `__init__` 欄位（即「store」）：
- `config` — 目前設定快取（`apiReadSettings` 重讀）。
- `cookingTask` `snowCarvingTask` `puzzleTask` `pumpkinTask` `sandTask` `catTask` `instrumentTask` `fishingTask` `fishingAltTask` `plantTask` `woodTask` `cropTask` — 各任務實例或 `None`；`bubbleRegionCoord` — 使用者框選的料理泡泡區域 `(x,y,w,h)`。
- `logs` / `logCounts` — 每個來源一個 `deque(maxlen=LOG_LIMIT)`，來源：cooking、snowCarving、puzzle、pumpkin、sandSculpture、cat、instrument、crop、fishing、fishingAlt、plant、wood、float；`index` 持續遞增供前端判斷讀到哪。
- `floatSources` `floatRun` `_floatKey` `_logWindow` `_logHwnd` `_logShown` `_logCollapsed` `_floatPlaced` `_floatDestroying` — 懸浮日誌狀態。
- `runInfo` — 每個 `RUN_KEYS` 的開始、結束時間。
- `updater` — `tools.updater.Updater`。`selectLock` — 框選時擋住開始任務。`backendError` `backendIsAdb` — `getBackend` 失敗資訊。`logLock`。

模組常數：`LOG_LIMIT`、`ALT_KEYS`、`GATHER_FOOD_KEYS`、`GATHER_TASKS`、`FLOAT_START_BUTTONS`（懸浮日誌「開始」要按主視窗哪顆按鈕的 id）、`FLOAT_TASK_NAMES`、`RUN_KEYS`、`REGION_HINTS`。

公開方法（路由；前端以 `getApi().<name>(...)` 呼叫）：
- 啟動任務（回傳 `{ok, error?, code?, warning?}`；`code` 為 `adbFailed` 或 `resolutionWarning`，後者要前端確認後以 `confirmed=True` 重呼）：`startCooking`、`startSnowCarving`、`startPuzzle`、`startPumpkin`、`startSandSculpture`、`startCat`、`startInstrument`、`startFishing`、`startGathering(kind)`（kind=`plant`/`wood`）。
- 停止與狀態：`stop`（全部任務；停止鍵也呼叫它）、`apiGetStatus`（各任務是否運行 + `runs` + `floatLogPosition`）、`isRunning`、`apiFrontReady`。
- 日誌：`apiGetNextLogs(currentIndex, source)`、`setLog(msg, source)`（任務傳進去的 log 函式）。
- 設定：`apiReadSettings`、`apiWriteSetting(key, value)`、`apiResetSettings`、`apiGetDefaults`。
- 彈奏：`apiPickMidi`（跳出選檔視窗、讀檔、存路徑與重設聲部）、`apiLoadMidi(path)`（回傳聲部清單）、`apiGetInstruments`（內建樂器按鍵配置，給「複製成自訂」用）。
- 模板圖片：`apiGetTemplateImages`（回傳 `{檔名無副檔名: data URL}`，同名以非 `adb/` 為準）。
- 範圍與菜品：`apiSelectRegion(name)`、`apiRecordDish`、`apiGetDishImage`、`apiClearDish`。
- 模擬器：`apiDetectAdb`（呼叫 `tools/adb_detect.py`）。
- 農作物提醒：`apiCropStart(remainingSeconds)`、`apiCropStop`、`apiCropStatus`。
- 更新：`apiGetVersion`、`apiCheckUpdate`、`apiInstallUpdate`、`apiUpdateState`、`apiApplyUpdate`、`apiOpenReleasePage`、`apiOpenLink`。
- 懸浮日誌：`apiFloatPoll`、`apiFloatStart`、`apiHideFloatLog`、`apiSetFloatCollapsed`、`apiResizeFloat`、`apiSaveFloatHeight`、`apiSaveFloatLogPos`；內部 `_prepareFloatLog`、`_closeFloatLog`、`_beginRun`。
- 內部共用：`taskConfig(task)`（決定各任務實際操作模式：背景定時固定 adb、釣魚前景、拼圖、南瓜雕刻、沙雕、逗貓、彈奏固定 screen）、`getBackend`（失敗時 `backendFailure` 附 `adbFailed` 標記、`backendErrorText` 給使用者看的說明）、`_resolutionBlock`、`_gatherConfig`、`_altConfig`、`_runSnapshot`。

## 後端：tools/

- `tools/tools.py` — 設定讀寫與驗證（見資料層）＋螢幕截取 `captureScreen` `captureFullScreen`、模板比對 `matchTemplate` `matchBest` `bestMatchScore` `loadTemplate` `readImage`、`ScaleResolver`、視窗 `findWindow` `focusWindow`、滑鼠 `clickMouse` `quickTap`（立刻按下、回傳時間，抓準時間用）`clickRightMouse` `dragMouse` `pressMouse` `moveMouseHeld` `releaseMouse`、框選 `selectRegion`、`APP_TITLE`、`getResourcePath`、`LOG_DIVIDER`、`RegionTooSmallError`。
- `tools/backend.py` — 操作後端兩種實作、介面相同：`ScreenBackend`（前景：截圖、點擊、拖曳、按住移動放開 `touchDown`/`touchMove`/`touchUp`、準時點擊 `prepareTap`/`tap`（只有前景）、按鍵 `pressKey`、只按下/放開不檢查視窗 `keyDown`/`keyUp`、一次送多個鍵的 `keysDown`/`keysUp`（和弦用）與 `ensureGame`（彈奏用）、貼文字，找遊戲視窗，會讓懸浮日誌穿透）、`AdbBackend`（模擬器：`adb exec-out screencap`、`input tap/swipe`）；`createBackend(config)`、`GameWindowLostError`、`SUPPORTED_RESOLUTIONS`、`resolutionWarningText`。
- `tools/finder.py` — 自動縮放比對：`matchAuto`、`matchBestAuto`、`resolveScale`、`getBaseScale`、`clickBackButton`、`findStartCookBtn`、`findSeasoningToggle`、`countStarRow`。
- `tools/puzzle.py` — 拼圖畫面分析：找木框 `findFrame`、棋盤空格 `emptyMap`、托盤碎片 `trayPieces`、比對 `cellScores`/`matchPiece`、片數 `detectGrid`、分配 `assignCells`（匈牙利演算法）、凸凹形狀 `applyShape`；版面常數以 1600x900 為基準。
- `tools/midi.py` — 不靠外部套件的 MIDI 解析：`parseMidi(path)` 回傳 `Song`（`parts` 聲部、音已換成秒；打擊樂頻道標 `percussion`）、`MidiError`。
- `tools/instruments.py` — 樂器配置與音高換算：`PIANO`、`TWO_ROW`（小提琴、大提琴、豎琴、手風琴、拇指琴、里拉琴、魯特琴共用的兩排白鍵）（按鍵→MIDI 音高）、`parseCustom`、`getLayout(config)`、`loadPlan(config)`（讀 MIDI、選聲部、決定移調）、`fitPitch`（折八度、取最近音）、`autoTranspose`、`toKeyNotes`（MIDI 音→按鍵）、`eventsFromKeyNotes`（排出按下/放開事件）、`buildEvents`、`layoutForUi`。
- `tools/guards.py` — `runDialogGuard`：任務運行中若跳出確認視窗（誤點丟棄等）自動按取消；設定 `autoCancelDialog`；拼圖關閉它（`BaseTask.dialogGuard = False`）。
- `tools/ocr.py` — Windows 內建 OCR 讀「現在值/最大值」：`readFraction`、`readText`、`readLines`（讀對話泡泡的中文字，整塊讀並回傳每行的高度，沙雕題目用）、`isReady`；沒有 winrt 時回傳 `None`，呼叫端要有備案。
- `tools/digits.py` — 背包格子右下角數量辨識：`readCount`、`digitSlots`、`loadDigitTemplates`（字形圖在 `templates/digits`）；目前沒有任何程式呼叫它（開始前的維修盒檢查已移除）。
- `tools/notify.py` — `notify(title, message, flash=True)` 桌面 Toast（經 PowerShell）、`flashTaskbar`。
- `tools/floatlog.py` — 懸浮日誌視窗的 Win32 操作：`styleOverlay`、`placeAndShow`、`targetPosition`、`setHeight`、`clampToScreen`、`setPassThrough`、`LOG_TITLE`、`LOG_SIZE`、`COLLAPSED_HEIGHT`（要與 `log.html` 的 `.bar` 高度一致）。
- `tools/debug.py` — `DebugMixin`：`_trace` 寫 `debug/flow.log`、存除錯截圖、監看主迴圈是否卡住；`debugMode` 沒開時全部不動作。
- `tools/updater.py` — GitHub Releases 更新：`checkUpdate`、`fetchNotesSince`、`isReleasePage`、`class Updater`（下載、解壓、`applyAndExit` 啟動替換腳本）、`cleanupLeftovers`、`isFrozen`、`REPO`；只允許 `ALLOWED_PREFIX` 開頭的下載網址。
- `tools/adb_detect.py` — `detectAdbDevices(adbPath)`：掃模擬器程序與常用連接埠找 ADB 裝置。
- `tools/adb_check.py` — 命令列連線測試（見指令）。
- `tools/ADBKeyboard.apk` — 模擬器輸入中文用的輸入法，第一次吃食物時自動安裝（`AdbBackend.beginTextInput`）。

## 邏輯：scripts/

任務共同介面：`Task(setLog, backend, config, ...)`，`.start()`、`.stop()`、`.isStart`；子類別實作 `_threadTargets()` 回傳要開的函式（每個收一個 `stopEvent`）。

- `scripts/common/base.py` — `BaseTask`：啟動/停止執行緒、`_guard` 例外保護、`_gameShot`、`_getMatchCords`、`_wakeMouse`、啟動 `runDialogGuard`。
- `scripts/common/gameui.py` — `GameUI`：遊戲畫面共用操作：狀態判斷 `_state`（idle/bag/unknown）、`_find`、`_click`、背包 `_openBagView` `_openBagFilter` `_closeBag`、走路 `_stepBack`/`_stepForward`、`_alert`（日誌＋通知）、`templateDirs` 與 `_templatePath`。
- `scripts/common/tasks.py` — `TimedTasks`：定時項目（香水、誘魚器、維修盒、吃食物）排程、隨機延後 `INTERVAL_JITTER`、失敗上限 `MAX_TASK_FAILURES`、`_doRepair` / `_throwRepairBox`、`OutOfStock`。
- `scripts/common/food.py` — `FoodMixin`：吃食物（偵測體力不足提示、搜尋食物、依星級挑格、保留最後一次、OCR 讀飽食度）；設定名稱沿用 `fishing*`。
- `scripts/common/autofood.py` — `AutoFood`：讓雪雕、南瓜雕刻、沙雕吃食物的獨立小幫手（`GameUI + FoodMixin + TimedTasks`），任務主迴圈呼叫 `poll(stopEvent)`；`foodConfig(config, prefix)` 把 `snow`/`pumpkin`/`sand` 開頭的吃食物設定換成 `fishing*`。
- `scripts/fishing/fishing.py` — `Fishing`：釣魚主迴圈（拋竿、收線、咬鉤、回主畫面）；前景、背景定時各開一個實例，`roleName` 區分。
- `scripts/fishing/consumables.py` — `ConsumablesMixin`：香水、誘魚器的判斷（圖示、魚尾光柱）與使用。
- `scripts/fishing/newfish.py` — `NewFishMixin`：新魚提示卡偵測與關閉。
- `scripts/fishing/__init__.py` — 只匯出 `Fishing`。
- `scripts/Cooking.py` — `Cooking`：料理（主迴圈與點擊兩條執行緒，收料理、五星停止、萬能食材、神奇調料、菜品比對）。
- `scripts/SnowCarving.py` — `SnowCarving`：雪雕（偵測雪花連點、逾時點返回；沒雪花時呼叫 `AutoFood.poll` 吃食物）。
- `scripts/Pumpkin.py` — `Pumpkin`：南瓜雕刻（開背包、沒停在南瓜搜尋結果就自動搜尋並鎖定、點南瓜、擺放時轉鏡頭對準雕刻檯、點雕刻圖示、雕刻小遊戲按住滑桿跟著灰線拉、收取）；只支援前景。
- `scripts/Puzzle.py` — `Puzzle`：拼圖流程（偵測片數、放碎片、垃圾桶重來、規律切割學習）；畫面分析在 `tools/puzzle.py`；`templateDir` 指向 `templates/puzzle` 但該資料夾不存在，拼圖不用模板圖。
- `scripts/SandSculpture.py` — `SandSculpture`：沙雕（開背包、沒停在沙雕基座搜尋結果就自動搜尋並鎖定、點基座、擺放（先往下拖鏡頭讓預覽框靠近人物，紅框就取消）、已有基座就不再放置、點互動圖示、收起做好的沙雕、小遊戲用顏色找黃色區塊與橘色指針、之後只截指針那一小塊、算出指針走到正中間的時刻準時點、用 OCR 讀題目選項、選第一個認得的、點掉提示對話框）；擺放相關模板借用 `templates/pumpkin`；只支援前景；`SAND_TARGETS` 是認得的沙雕與選項關鍵字。
- `scripts/Cat.py` — `Cat`：逗貓（點爪印、選單點逗貓、小遊戲認貓耳泡泡裡的按鈕圖示並點同樣的按鈕、結果視窗按確定）；只支援前景；畫面一律縮成 1600x900 基準後用 `cv2.matchTemplate` 比對，不走 `matchBestAuto`；`iconFeature`/`iconScore` 是圖示形狀比對。
- `scripts/Instrument.py` — `Instrument`：彈奏（讀 MIDI → 選聲部 → 移調 → 照時間 `keyDown`/`keyUp`；高精度計時、開始前倒數、可重複播放）；只支援前景，只送鍵盤、不看畫面也不用模板，`dialogGuard = False`。
- `scripts/Gathering.py` — `GatherBase`：定時採集共同骨架（連按互動鍵、倒數圖示、獲得物品氣泡、工具耐久耗盡、吃食物、維修盒）；`findCountdownRing`。
- `scripts/PlantGathering.py` — `PlantGathering`：採集（無工具），`prefix="plant"`。
- `scripts/WoodCutting.py` — `WoodCutting`：採集（有工具），`prefix="wood"`。
- `scripts/CropReminder.py` — `CropReminder`：農作物長草提醒計時（不碰遊戲，只跳通知）；`buildSchedule`、`snapshot`。
- `scripts/__init__.py`、`scripts/common/__init__.py` — 空檔。

## 畫面：ui/

- `ui/index.html` — 主視窗，單頁、分頁用 `.pane`：`#cook-tab-pane` 料理、`#snow-tab-pane` 雪雕、`#puzzle-tab-pane` 拼圖、`#cat-tab-pane` 逗貓（「更多」選單第二個）、`#pumpkin-tab-pane` 南瓜雕刻、`#instrument-tab-pane` 彈奏（「更多」選單最後一個）、`#sand-tab-pane` 沙雕、`#fish-tab-pane` 釣魚、`#gather-tab-pane` 採集（內容由 `buildGatherBlock` 動態產生）、`#crop-tab-pane` 拔草提醒、`#settings-tab-pane` 設定；另有 `#updateDialog`、`#dialog`（通用提示窗，由 `showDialog` 填）、`#loading`。內嵌 Lucide SVG sprite（`#i-*`）。
- `ui/script.js` — 唯一的前端邏輯（全域函式，無打包）。入口 `init` → `setup`（`setup` 內含輪詢 `apiGetStatus` 與各分頁事件綁定）。
- `ui/log.html` — 懸浮日誌視窗，自帶樣式與腳本（輪詢 `apiFloatPoll`，`markClosing` 停輪詢）；有 `data-theme="light"` 淺色版。
- `ui/style.css` — 主視窗唯一樣式表（見「樣式規則」）。
- `ui/fonts/` — `Inter-Variable.ttf`、`NotoSansTC-Variable.ttf` 與兩份 OFL 授權檔，隨程式打包，不連網。
- `ui/bubble-example.png`、`ui/dish-example.png`、`ui/fishing-example.png` — 說明提示窗用的範例圖（`script.js` 的 `HELP_TEXTS`/`NOTICE_TEXTS` 引用）。

`script.js` 主要區塊（用函式名搜尋）：
- 資料綁定：欄位以 `class="setting"` / `"setting-check"` + `data-key="<config key>"` 對應設定；`handleSettingChange` 存入、`fillSettings` 載入；同 key 多個輸入框會一起更新。
- 前端全域狀態：`hasBubbleRegion`、`isSelectingRegion`、`isAnyRunning`、`dishRecorded`、`isClosing`、`pendingUpdate`、`current*LogIndex`（各來源日誌讀到哪）、`gatherLogIndex`、`resetDefaults`、`cropRunning`。
- 通用：`showDialog(title, message, {confirm, image, danger, okText, choices})`、`startTask`、`selectRegionHandler`、`alertBlockers` + `get*Blockers`（開始前的前端檢查）、`poll`、`getApi`。
- 採集：`GATHER_KINDS` + `buildGatherBlock`（plant、wood 共用一份模板）、`syncGatherKind`。
- 吃食物設定區：`foodBlockHtml(prefix)`（採集、雪雕、南瓜雕刻、沙雕共用），雪雕等分頁用 `data-food-block` 佔位元素 + `CARVE_FOOD_KINDS` 填入；`getCarveFoodBlockers` 檢查食物名稱。
- 釣魚前景/背景：`buildAltOptions`、`syncAltVisibility`、`syncFgVisibility`。
- 說明文字：`HELP_TEXTS`、`NOTICE_TEXTS`、`FOOD_GROUPS`。
- 設定旁輔助：`fillTemplateImages`（`img[data-tpl]`）、`addMissingResetButtons` / `bindResetButtons` / `updateResetButtons`（還原預設值鈕）、`syncAdbPresets`、`fillAdbDetails`。
- 更新：`checkForUpdate`、`updateTick`、`showUpdate`、`renderMarkdown`（把 `RELEASE_NOTES.md` 渲染到提示窗）。
- 彈奏（檔尾 `// ===== 彈奏 =====` 區塊）：`renderInstrument`（載入設定時重畫）、`renderCustomRows` / `captureKey`（自訂按鈕列、點按鍵鈕後按要用的鍵）、`renderInstrumentParts` / `saveInstrumentParts`（聲部勾選）、`copyBuiltin`（複製鋼琴成自訂）、`renderSavedOptions`（存下來的樂器放進下拉選單）；運行中要鎖住的控制項加 class `inst-edit`。
- 其他：`renderRunTimes`、`renderHeroRunning`（頁首運行標籤）、`renderCrop`（拔草倒數）、`showPane` 與 `menuTabs`（分頁與下拉選單）。

## 元件（可重用的畫面零件）

HTML + class 的組合，要新增同類畫面時直接複製既有結構：
- 分頁：`.tabs > li > .tab[data-target]`；下拉分頁 `.tool-wrap > .tab[data-menu] + ul.tool-menu > li > .tool-item[data-target]`；設定齒輪 `.tab.gear`。
- 頁面卡片：`.pane`（顯示時 `.pane.show.active`），開頭 `.pane-heading`。
- 設定列：`.row > .col > label.label + .input`；勾選列 `.check`（子選項 `.check.sub`，說明 `.hint`，母選項沒勾時 `.child-off` 隱藏子項）；`.setting-line` 左名稱右控制項。
- 輸入：`.input`（數字窄版 `.qty`、錯誤 `.invalid`）、`select.input`、`input[type=checkbox]`（自繪開關）。
- 按鈕：`.btn`（`.primary` 開始、`.danger` 停止、預設白底）、`.text-btn`（小文字鈕）、`.notice-btn`（黃字注意事項鈕）、`.reset-default`（還原預設值 icon）。
- 範圍選取列：`.region-box` + `.region-info` + `.coord`（狀態文字＋圓點，失敗 `.text-danger`）。
- 運行區：`.run-head` + `.run-title` + `.run-badge` + `.run-last`；`.log`（日誌框）；`.run-actions`（停止/開始，靠右）。
- 摺疊：`details.group`（內部小群組）、`details.section`（設定頁頂層群組，含 `.section-text/.section-name/.section-hint/.section-tag`）。
- 提示窗：`.dialog > .dialog-box > .dialog-title + .dialog-msg(+ .dialog-list) + .dialog-actions`；多選用 `.dialog-choices > .dialog-choice`；範例圖 `.dialog-image`。
- 警告：`.warn`（黃字，不用群組框）。依模式顯示：`.alt-only`（背景）、`.fg-only`（前景）。
- 載入：`.loading > .spinner`；更新提示窗內容 `.md`。
- 圖示：`<svg class="icon"><use href="#i-名稱"/></svg>`（Lucide，跟著文字色與大小）。

## 樣式規則（`ui/style.css`）

顏色（只用 `:root` 的 token，不寫死色碼；深色模式由 `@media (prefers-color-scheme: dark)` 覆寫同名 token）：
- 底與文字：`--bg` 頁面底、`--surface` 卡片、`--surface-2` 次要底、`--border` / `--border-strong` 邊框、`--text` / `--text-2` / `--text-3` 三階文字。
- 主色（黃，主要動作）：`--brand` `--brand-hover` `--brand-soft` `--brand-edge` `--on-brand`；輔色（天藍，導覽、聚焦、開關）：`--sky` `--sky-soft` `--sky-deep`。
- 狀態：`--success`/`--success-soft`、`--danger`/`--danger-soft`、`--warn-text`/`--warn-soft`/`--warn-border`。
- 其他 token：`--font`（Inter、Noto Sans TC 等）、`--mono`、圓角 `--r-sm` 8 / `--r-md` 12 / `--r-lg` 16 / `--r-pill` 999、陰影 `--shadow-1` `--shadow-brand`、聚焦環 `--ring`、欄位間距 `--field-gap` 6px。
- `ui/log.html` 自帶一組 token（`--bg` `--text` `--danger-text` `--ok-text` 等，預設深色、`data-theme="light"` 淺色），與主視窗不共用。
- 改 `style.css` 的顏色 token 時，順手對一下 `design/design-system.html`，兩邊淺色顏色 token 目前同值。

共用 class：
- 間距與輔助：`.m-0` `.mb-8` `.mb-16` `.mt-8` `.mt-16` `.mt-auto` `.ml-12` `.gap-8` `.gap-16` `.w-120`（實際寬 104px）、`.flex`、`.muted`、`.text-danger`、`.text-warn`、`.bg-success`、`[hidden]{display:none !important}`。
- 版面：`.container`（最寬 560、左右 24）、`.row`（flex、間距 16）、`.col`。卡片 `.pane` 內距 `16px 24px`，圓角 `--r-lg`。
- 元件 class 一覽見上一節；新增樣式放在對應的 `/* ============ … ============ */` 區塊（token、版面結構、標題與輸入欄、按鈕、開關、範圍座標、日誌、摺疊群組、提示視窗、載入中、警告、更新提示視窗、設定頁底部）。

字級（`html` 設 14px，其餘盡量用 `em` 相對縮放）：
- 內文 `1em`（14px）；`.muted` 0.95em；`.label`、`.warn` 0.93em；`.title`、`.dialog-title`、`details.section > summary` 1.15em；`.pane-heading` 1.3em；`.hero-title` 22px；`.crop-time` 40px。
- 小字用 `.fs-12`（12px，單一 class 帶 `!important`）；`.beta-tag`、`.section-tag` 11px；`.log` 13px。

層級（`z-index`，不要隨意新增新的數字）：`.tab.gear` 1 → `.hero` 5 → `.tool-menu` 10 → `.loading` 100 → `.dialog` 200；`log.html` 內 `.grip` 2、`body::after` 外框 3。

動畫（刻意很少）：
- 唯一的 `@keyframes` 是 `spin`（`.spinner`，0.8s linear infinite）。
- 其餘只有 `transition`：`.btn`／`.tab` 背景與文字 0.1～0.15s、`.input` 邊框與陰影 0.2s、開關 0.2s、`.loading` 淡出 `opacity` 0.25s、摺疊箭頭旋轉 0.2s。`log.html` 沒有動畫。
- 不加進場動畫、不加持續循環動畫（會干擾遊戲畫面截圖與使用者專注）。

## 模板圖片：templates/

以 1600 寬為基準；檔名（不含副檔名）就是模板名，`DEFAULT_THRESHOLDS` 的 key 與 `apiGetTemplateImages` 的 key 都用它。同名時非 `adb/` 的優先。

- `templates/common/` — 各功能共用的按鈕（返回、關閉、取消、背包、工具欄、搜尋、維修盒…）；`common/adb/` 為手機版不同的圖（bagBtn、repairBoxItem、toolBtn）。
- `templates/cooking/` — 料理（開始烹飪、火、完成、計時器、加號、萬能食材、調料）。
- `templates/snowCarving/` — 雪雕（雪花、放置、開始、完成）。
- `templates/fishing/` — 釣魚（咬鉤、拋竿鈕標記、新魚、篩選、香水、誘魚器、噴灑、使用）；`fishing/adb/` 為手機版（castBtn、perfumeBuff）。
- `templates/food/` — 吃食物（體力圖示、體力不足提示、鎖頭、吃、使用次數標籤）；釣魚與採集共用。
- `templates/pumpkin/` — 南瓜雕刻（背包搜尋標題、擺放鈕、擺放退出與確認鈕、雕刻與收取圖示、雕刻中標籤、記號、滑桿把手）；從錄影裁出。
- `templates/sandSculpture/` — 沙雕，從錄影與截圖裁的（`sandBagTitle` 背包標題、`sandInteract` 互動圖示、`sandCollect` 收起沙雕圖示、`sandCombo` COMBO 條、`sandQuiz` 題目提示字；題目選項用 OCR 讀，不用模板）；缺圖時開始會報缺哪幾張。
- `templates/cat/` — 逗貓（`catBubble` 貓耳泡泡、`catIconUp/Down/A` 三顆按鈕的圖示、`catPaw` 貓咪頭上的爪印、`catMenu` 選單的逗貓鈕、`catEnd` 結果視窗標題、`catOk` 確定鈕），從錄影裁的。
- `templates/gather/` — 採集（植物、木頭、蘑菇倒數圖示、工具耐久耗盡提示）。
- `templates/digits/` — `0.png`～`9.png`，背包數量字形，給 `tools/digits.py`（目前沒有程式使用）。
- `templates/icons/` — 設定旁顯示用的道具小圖（不拿來比對）。

新增模板：放進對應資料夾 → `DEFAULT_THRESHOLDS` 加閥值 → 要在設定旁顯示小圖就在 `index.html` 加 `<img data-tpl="檔名">`。

## 建置與發佈

- 版本：改 `version.py` 的 `VERSION`；更新說明寫 `RELEASE_NOTES.md`；兩者 commit、push 後執行 `release.ps1`。
- 打包資料檔：新增需要隨程式帶走的資料夾或檔案，要加進 `main.spec` 的 `datas`；程式內讀資源一律走 `getResourcePath`（`tools/tools.py`）或 `main.resourcePath`，不要寫相對路徑。
- 打包後路徑：資源在 exe 旁 `_internal/`，使用者資料（`config.ini`、`userdata/`）在 exe 旁。
- 更新機制：`tools/updater.py` 只從 `REPO` 的 Release 下載 zip（zip 內須含 `heartopiaHelper.exe`）；從原始碼執行時無法自動更新。
