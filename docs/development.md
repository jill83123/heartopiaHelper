# 開發與發佈

給要自己從原始碼執行、打包或發佈新版的人看。一般使用者請看 [README](../README.md)。程式碼的查找入口見根目錄的 [CLAUDE.md](../CLAUDE.md)，設計取捨見 [decisions.md](decisions.md)。

## 從原始碼執行

需要 Windows 與 Python 3.13。

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

從原始碼執行只會提示有新版，不會自動替換。

### 測試模擬器連線

```powershell
python -m tools.adb_check adb 127.0.0.1:5555
```

會連線並把畫面存成 `adb_check.png`，不會點擊任何東西。若 `adb` 不在 PATH，請在設定分頁填入「ADB 路徑」。

## 設定檔

設定分頁的值會寫入 `config.ini`（第一次執行時由預設範本 `config.default.ini` 複製而來，是各人的本機設定，不納入版本控制），也可以直接編輯該檔案。偵測與點擊頻率預設 0.05 秒（釣魚的偵測頻率預設 0.2 秒）。

| 設定 | 說明 |
| --- | --- |
| `controlMode` | 料理、雪雕、採集共用的操作模式：`screen`（前景）或 `adb`（背景／模擬器）；釣魚的前景定時固定前景、背景定時固定模擬器，不受此設定影響 |
| `adbPath` / `adbDevice` | adb 執行檔路徑與裝置位址 |
| `gameWindowTitle` | 前景模式框選範圍時，用來尋找遊戲視窗的標題文字（預設 `Heartopia`） |
| `screenResolution` / `uiScale` | 前景模式的遊戲解析度與介面縮放（介面縮放目前僅支援 100） |
| `stopKey` | 停止按鍵，單鍵（`F8`）或組合鍵（`ctrl+shift+q`，用 `+` 連接）皆可；在設定頁修改後立即生效 |
| `cookDishRecordedAt` | 菜品記錄時間（菜名截圖存在 `config.ini` 旁 `userdata/` 資料夾裡的 `dish.png`，運行日誌會註明） |
| `expectedCookQty` | 料理份數 |
| `isSafeMode` / `safeModeQty` | 安全模式：煮滿指定份數才收料理（預設 3） |
| `detectFrequency` / `clickFrequency` / `snowDetectFrequency` / `fishingDetectFrequency` | 偵測與點擊間隔（秒） |
| `fishingAutoCast` | 是否自動拋竿釣魚（會一併噴香水；關閉時只定時執行勾選的項目） |
| `fishingFaceFront` | 釣到新魚後先轉回正面再拋竿（角色須面對水面，否則會往旁邊走）。背景定時用 `fishingAltFaceFront` |
| `fishingSkipTail` | 水面有魚尾就不丟誘魚器。背景定時用 `fishingAltSkipTail` |
| `fishingUseBait` | 是否丟誘魚器 |
| `fishingRepairEnabled` | 是否丟維修盒（工具耐久耗盡時丟；沒勾就不修） |
| `fishingUseRepair` / `fishingRepairMinutes` | 是否定時丟維修盒與間隔（分鐘） |
| `fishingRepairLeave` | 丟維修盒前先往後退一步（採集預設開、釣魚預設關） |
| `fishingUseFood` / `fishingFoodUntil` / `fishingFoodName` / `fishingFoodStars` | 是否吃食物、吃到飽食度幾 %（預設 99）、食物名稱、星級（1~5 = 指定、14 = 0-4 星、0 = 不限（0-5 星）） |
| `instrumentType` / `instrumentCustom` | 彈奏的樂器（`piano`、`violin`（小提琴、大提琴、豎琴、手風琴、拇指琴、里拉琴、魯特琴共用）、`custom`、`saved:名稱`）與自訂按鈕（JSON：`[{"key": "q", "pitch": 60}]`，pitch 是 MIDI 音高，C4=60） |
| `instrumentSaved` | 存下來的自訂樂器（JSON：`{"大提琴": [{"key": "q", "pitch": 60}]}`） |
| `instrumentOutOfRange` | 超出樂器音域的音：`fold`（搬八度，預設）或 `skip`（不彈） |
| `instrumentMidiPath` / `instrumentTracks` | 彈奏的 MIDI 檔路徑與要彈的聲部編號（逗號分隔，空白 = 全部、不含打擊樂） |
| `instrumentTranspose` / `instrumentSpeed` / `instrumentStartDelay` / `instrumentLoop` | 移調（`auto` 或半音數）、演奏速度倍數、開始前倒數秒數、是否重複播放 |
| `matchStrictness` | 整體比對嚴格度：`loose` / `normal` / `strict`。抓不到圖示選寬鬆，誤判太多選嚴格 |

所有設定項與預設值以 `tools/tools.py` 的 `CONFIG_DEFAULTS` 為準。

## 打包

```powershell
venv\Scripts\python -m PyInstaller --noconfirm main.spec
```

一定要用專案 venv 的 Python 打包（不要直接打 `pyinstaller`，會用到全域環境而漏掉 `keyboard` 等套件）。資料檔、圖示、不顯示主控台等設定都在 `main.spec`。

打包時內建的是預設範本 `config.default.ini`；打包後的 `config.ini` 會放在 exe 旁邊，第一次執行時由範本複製，重新打包不會蓋掉使用者的設定。

## 自動更新與發佈新版

打包後的程式啟動時會到 GitHub Releases 查詢新版本，有的話在視窗上方提示，按「立即更新」就會自動下載、替換並重新啟動（`config.ini` 不會被覆蓋）。設定分頁也有「檢查更新」按鈕。

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
