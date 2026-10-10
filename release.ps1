# 一鍵發佈新版: 讀取 version.py 的版本 -> 打包 -> 壓成 zip -> 建立 GitHub Release 並上傳
# 用法: .\release.ps1            (正式發佈)
#       .\release.ps1 -Draft     (只建立草稿，不公開，也不會被更新檢查查到)
# 事前準備: 已安裝 GitHub CLI 並執行過 gh auth login；version.py 已改好、RELEASE_NOTES.md 已寫好，並都 commit、push
param([switch]$Draft)

# 外部程式(gh、pyinstaller)會把訊息寫到 stderr，不能讓它被當成錯誤；改用結束代碼判斷
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Fail($message) { Write-Host "錯誤: $message" -ForegroundColor Red; exit 1 }

# 1. 讀版本
$match = Select-String -Path version.py -Pattern 'VERSION\s*=\s*"([^"]+)"'
if (-not $match) { Fail "讀不到 version.py 的 VERSION" }
$version = $match.Matches[0].Groups[1].Value
$tag = "v$version"

# 2. 檢查狀態
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Fail "找不到 gh，請先安裝 GitHub CLI" }
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { Fail "gh 尚未登入，請先執行 gh auth login" }
if (-not (Test-Path RELEASE_NOTES.md)) { Fail "找不到 RELEASE_NOTES.md，請先寫好這一版的更新說明" }
if (git status --porcelain version.py RELEASE_NOTES.md) { Fail "version.py 或 RELEASE_NOTES.md 有尚未 commit 的修改" }
git fetch origin --quiet
$head = git rev-parse HEAD
if (-not (git branch -r --contains $head)) { Fail "目前的 commit 還沒 push 到 GitHub，請先 push" }
gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) { Fail "Release $tag 已存在，請先把 version.py 改成新的版本號" }

$kind = if ($Draft) { "草稿" } else { "正式版（公開）" }
Write-Host "即將發佈 $tag [$kind]，對應 commit $($head.Substring(0, 7))"
if ((Read-Host "確定要繼續嗎？(y/N)") -ne "y") { Write-Host "已取消"; exit 0 }

# 3. 打包
# 優先用專案的 venv，確保套件齊全
$python = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" } else { "python" }
Write-Host "打包中..."
& $python -m PyInstaller --noconfirm --noconsole --add-data "config.default.ini;." --add-data "ui;ui" --add-data "templates;templates" --add-data "tools/ADBKeyboard.apk;tools" main.py
if ($LASTEXITCODE -ne 0) { Fail "打包失敗" }

# 4. 壓縮 (exe 要在 zip 的最上層)
$zip = "heartopiaHelper_$tag.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\main\*" -DestinationPath $zip
Write-Host ("zip 大小: {0:N1} MB" -f ((Get-Item $zip).Length / 1MB))

# 5. 建立 Release (tag 會由 GitHub 在指定 commit 上自動建立)
$ghArgs = @("release", "create", $tag, $zip, "--target", $head, "--title", $tag, "--notes-file", "RELEASE_NOTES.md")
if ($Draft) { $ghArgs += "--draft" }
gh @ghArgs
if ($LASTEXITCODE -ne 0) { Fail "建立 Release 失敗" }

Remove-Item $zip
Write-Host "完成: $tag" -ForegroundColor Green
