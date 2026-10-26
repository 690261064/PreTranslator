@echo off
rem PreTranslator 启动器: 优先无窗口 pythonw, 回退 python
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw pretranslator.py
) else (
    start "" python pretranslator.py
)
