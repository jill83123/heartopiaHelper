# 心動小鎮助手 (heartopiaHelper)

《Heartopia》的自動化小工具，以畫面比對找出圖示並自動點擊。目前支援：

- **料理**：自動點鍋子、開始烹飪、收料理，可設定要煮的份數。
- **雪雕**：自動點雪花、放置、開始、完成。
- **釣魚**：沒拋竿時自動拋竿，並定時噴香水、放誘魚器、丟維修盒、吃指定食物。
- **採集**：定時採集（無工具）或採集（有工具）（兩個功能各自獨立，只能擇一使用），採集（有工具）可定時吃食物。

## 兩種操作模式

| | 前景模式 | 模擬器模式 |
| --- | --- | --- |
| 適用 | PC 版遊戲 | BlueStacks 等模擬器（經 ADB） |
| 視窗 | 遊戲須在最上層，不能被其他視窗蓋住 | 可被其他視窗蓋住，不需要在最上層 |
| 原理 | 截取桌面畫面、模擬滑鼠點擊 | ADB 截圖與點擊 |

「料理」、「雪雕」和「採集」（無工具、有工具）共用同一個操作模式，這些分頁各有一個「操作模式」下拉選單，改任何一個另一個會跟著變。釣魚的「前景定時」固定走前景模式，「背景定時」固定走模擬器。解析度、ADB 等共用設定在「設定」分頁，分成「前景設定」與「背景設定」兩區。

## 安裝與執行

需要 Windows 與 Python 3.13。

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 使用方式

1. 在「設定」分頁確認「前景設定」或「背景設定」（見下方）。
2. 到「料理」、「雪雕」或「採集」分頁選好「操作模式」（共用）：
   - 料理：按「選取範圍」後在畫面上拖曳，框住鍋子、火、手套、計時器等圖示會出現的位置。
   - 雪雕：不用選範圍，直接偵測整個遊戲畫面。
3. 按「開始」；若設定不完整，會跳出視窗列出原因而不會啟動。換了操作模式後，先前框選的泡泡區域要重新選取。按「停止」或設定的停止按鍵（預設 `F8`）可隨時中止。

### 前景模式

- 「遊戲解析度」預設為自動偵測（讀取遊戲視窗大小，需正確填寫遊戲視窗標題）；偵測不準時可手動指定。「介面縮放比例」須和遊戲內設定一致。
- 運行中請讓遊戲保持在最上層。
- 按範圍按鈕框選時，程式會自動把遊戲切到最前面，選完再切回本程式。

### 模擬器模式

1. 在模擬器設定中開啟 ADB（BlueStacks：在設定中開啟「Android 偵錯橋 (ADB)」）。
2. 到「設定」分頁填入「ADB 路徑」（設定頁內有安裝說明）與 ADB 裝置位址。設定頁的「模擬器」選單可一鍵填入常見位址：BlueStacks、雷電為 `127.0.0.1:5555`，MuMu 12 為 `127.0.0.1:16384`，MuMu 6 為 `127.0.0.1:7555`，夜神為 `127.0.0.1:62001`；連不上或不確定位址時，請先開啟模擬器，再按「ADB 裝置位址」旁的「自動偵測」，程式會找出執行中的模擬器並填入。
3. 若要先測試連線，可在專案目錄執行：

   ```powershell
   python -m tools.adb_check adb 127.0.0.1:5555
   ```

   會連線並把畫面存成 `adb_check.png`，不會點擊任何東西。若 `adb` 不在 PATH，請在設定分頁填入「ADB 路徑」。
4. 釣魚的「吃食物」需要輸入中文，程式內附 [ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)（GPL-2.0，未修改，檔案為 `tools/ADBKeyboard.apk`），第一次吃食物時會自動裝進模擬器，並在輸入前後自動切換輸入法，不需要手動安裝。
5. 穩定版本為 電腦 1600×900、模擬器 1280×720（模板以 1600 寬為基準，其他解析度會自動縮放）。其餘解析度沒有經過大量測試，可能辨識不到或點擊位置不準。

### 釣魚

不需要框選範圍。讓角色站在水邊、面向水面並裝備釣竿，勾選要用的項目後按「開始」。各選項的說明見介面上的提示。

- **前景定時**：預設啟用，可關閉後只跑背景定時；兩邊都沒啟用時無法開始。
- **背景定時（模擬器）**：勾選「啟用」後會同時啟動前景與背景兩個任務，背景連到設定分頁的模擬器，並有各自獨立的選項。背景帳號要先站在前景帳號附近的水邊並完成新手教學。
- **自動拋竿釣魚**：目前須搭配噴香水使用；關閉時只定時丟維修盒、放誘魚器、吃食物。
- **吃食物**：需填中文食物名稱，沒填會無法開始。
- 需要處理的狀況（道具用完、找不到食物…）會跳桌面通知。
- 按「開始」時設定不完整會跳出視窗列出原因。

### 採集

「採集」分頁選擇「採集（無工具）」或「採集（有工具）」，兩者設定與日誌各自獨立，同時只能跑一個。不需要框選範圍：讓角色站在採集點或樹前面，設定好後按「開始」。

- **每輪連按次數**：每輪對著目標按幾次互動鍵。
- **定時時間**：以重生時間為基準（預設 120 秒），每隔這麼久做一輪。
- **吃食物、丟維修盒**：只有採集（有工具）有，說明見介面。丟維修盒預設開啟（工具耐久耗盡時丟），可另外勾選「定時丟」；「離開現在的位置後再丟」預設開啟，丟之前會先往後退一步（前景按 S、模擬器拉搖桿往下），丟完再走回原位。
- **自動停止**：太久沒採到東西、工具壞掉卻沒維修盒（或沒勾丟維修盒）時，會通知並停止。

### 浮動日誌

按「開始」會在螢幕上跳出置頂的小日誌視窗，任務結束自動收起，也可按 × 先收起。位置與是否顯示在設定分頁「一般設定」調整。

## 設定

設定分頁的值會寫入 `config.ini`（第一次執行時由預設範本 `config.default.ini` 複製而來，是各人的本機設定，不納入版本控制），也可以直接編輯該檔案。設定分頁最下方的「還原設定值」可重置所有設定，回到最初始狀態；每個設定欄位旁也都有「還原預設值」按鈕，可單獨還原。偵測與點擊頻率預設 0.05 秒。

| 設定 | 說明 |
| --- | --- |
| `controlMode` | 料理、雪雕、採集共用的操作模式：`screen`（前景）或 `adb`（背景／模擬器）；釣魚的前景定時固定前景、背景定時固定模擬器，不受此設定影響 |
| `adbPath` / `adbDevice` | adb 執行檔路徑與裝置位址 |
| `gameWindowTitle` | 前景模式框選範圍時，用來尋找遊戲視窗的標題文字（預設 `Heartopia`） |
| `screenResolution` / `uiScale` | 前景模式的遊戲解析度與介面縮放 (%) |
| `stopKey` | 停止按鍵，單鍵（`F8`）或組合鍵（`ctrl+shift+q`，用 `+` 連接）皆可；在設定頁修改後立即生效 |
| `cookDishRecordedAt` | 菜品記錄時間（菜名截圖存在 `config.ini` 旁 `userdata/` 資料夾裡的 `dish.png`，運行日誌會註明） |
| `expectedCookQty` | 料理份數 |
| `isSafeMode` / `safeModeQty` | 安全模式：煮滿指定份數才收料理（預設 3） |
| `detectFrequency` / `clickFrequency` / `snowDetectFrequency` / `fishingDetectFrequency` | 偵測與點擊間隔（秒） |
| `fishingAutoCast` | 是否自動拋竿釣魚（會一併噴香水；關閉時只定時執行勾選的項目） |
| `fishingFaceFront` | 釣到新魚後先轉回正面再拋竿（角色須面對水面，否則會往旁邊走）。背景定時用 `fishingAltFaceFront` |
| `fishingSkipTail` | 水面有魚尾就不放誘魚器。背景定時用 `fishingAltSkipTail` |
| `fishingUseBait` | 是否放誘魚器 |
| `fishingRepairEnabled` | 是否丟維修盒（工具耐久耗盡時丟；沒勾就不修） |
| `fishingUseRepair` / `fishingRepairMinutes` | 是否定時丟維修盒與間隔（分鐘） |
| `fishingRepairLeave` | 丟維修盒前先往後退一步（採集預設開、釣魚預設關） |
| `fishingUseFood` / `fishingFoodUntil` / `fishingFoodName` / `fishingFoodStars` | 是否吃食物、吃到飽食度幾 %（預設 99）、食物名稱、星級（1~5 = 指定、14 = 0-4 星、0 = 不限（0-5 星）） |
| `matchStrictness` | 整體比對嚴格度：`loose` / `normal` / `strict`。抓不到圖示選寬鬆，誤判太多選嚴格 |

## 打包

```powershell
pyinstaller --noconsole --name heartopiaHelper --add-data "config.default.ini;." --add-data "ui;ui" --add-data "templates;templates" --add-data "tools/ADBKeyboard.apk;tools" main.py
```

打包時內建的是預設範本 `config.default.ini`；打包後的 `config.ini` 會放在 exe 旁邊，第一次執行時由範本複製，重新打包不會蓋掉你的設定。

## 自動更新與發佈新版

打包後的程式啟動時會到 GitHub Releases 查詢新版本，有的話在視窗上方提示，按「立即更新」就會自動下載、替換並重新啟動（`config.ini` 不會被覆蓋）。設定分頁也有「檢查更新」按鈕。從原始碼執行只會提示，不會自動替換。

### 事前準備（只需一次）

1. 安裝 [GitHub CLI](https://cli.github.com)。
2. 在終端機執行 `gh auth login`，依指示登入 GitHub。

### 發佈步驟

1. 修改 `version.py` 的 `VERSION`（例如 `1.3.0`），並把這一版的更新內容寫進 `RELEASE_NOTES.md`（每次發佈前直接覆蓋成新內容），commit 並 push 到 GitHub。
2. 在專案資料夾的 PowerShell 執行：

   ```powershell
   .\release.ps1
   ```

3. 腳本會顯示要發佈的版本號，輸入 `y` 確認後，自動完成：打包、壓成 zip、在 GitHub 建立 tag 為 `v1.3.0` 的 Release 並上傳 zip。Release 說明就是 `RELEASE_NOTES.md` 的內容，使用者的更新提示也會顯示它。
4. 使用者開啟程式時就會看到更新提示。

### 只建立草稿（測試用）

```powershell
.\release.ps1 -Draft
```

草稿只有你看得到，不會被更新檢查查到。要測試自動更新本身則必須發佈正式版，測完請到 GitHub 刪除該 Release 與 tag。

### 腳本會擋下的情況

- 找不到 `RELEASE_NOTES.md`，或它與 `version.py` 有尚未 commit 的修改。
- 目前的 commit 還沒 push 到 GitHub。
- 該版本號的 Release 已經存在（請先調高版本號）。
- `gh` 未安裝或尚未登入。

也可以手動發佈：自行打包並壓縮 `dist/heartopiaHelper`，到 GitHub 建立 Release、tag 設成 `v` 加版本號，再上傳 zip。

注意：zip 內須含 `heartopiaHelper.exe`（可以直接放在最上層，或多包一層資料夾）。

## 設計決策

行為細節與取捨見 [docs/decisions.md](docs/decisions.md)。

## 專案結構

```
main.py            程式進入點、視窗與停止鍵
api.py             前端呼叫的後端介面
scripts/           料理、雪雕、採集（無工具、有工具）流程 (Gathering.py 為採集共用骨架)
scripts/common/    各功能共用: base.py 啟動與停止、gameui.py 畫面與背包操作、tasks.py 定時項目、food.py 吃食物
scripts/fishing/   釣魚: fishing.py 主流程、consumables.py 香水與誘魚器、newfish.py 新魚處理
tools/             畫面截取、模板比對、ADB 與設定檔
templates/         比對用的圖片 (以 1600 寬為基準，其他解析度會自動縮放；common 各功能共用的按鈕、food 吃食物、各功能自己的圖放自己的資料夾，手機版不同的圖放各資料夾的 adb/)
ui/                介面 (HTML / JS / CSS)
```
