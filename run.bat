@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%~dp0src\gui_app.py" %*
    exit /b
)
if exist "C:\Python311\pythonw.exe" (
    start "" "C:\Python311\pythonw.exe" "%~dp0src\gui_app.py" %*
    exit /b
)
start "" pythonw "%~dp0src\gui_app.py" %*
exit /b
