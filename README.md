打包指令

```
pyinstaller --noconsole --add-data "config.ini;." --add-data "ui;ui" --add-data "templates;templates" main.py
```

pyinstaller --noconsole --add-data "config.ini;." --add-data "ui;ui" --add-data "templates;templates" --hidden-import=win32api --hidden-import=win32con --hidden-import=win32gui main.py
