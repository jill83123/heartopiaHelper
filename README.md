打包指令（含 console）

```powershell
pyinstaller --add-data "config.ini;." --add-data "ui;ui" --add-data "templates;templates" main.py
```
