@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title KRAKEN PRIME - Setup
color 0E

echo.
echo  ============================================================
echo    KRAKEN PRIME - DEPENDENCIES INSTALLER
echo  ============================================================
echo.
echo  This will install everything needed to run the bot:
echo    - Python 3.11
echo    - Required Python packages
echo    - Tesseract OCR
echo    - ADB (Android Debug Bridge)
echo.
echo  Press any key to begin...
pause >nul

:: ============================================================
:: [1/6] Internet check
:: ============================================================
echo.
echo  [1/6] Checking internet connection...
ping -n 1 google.com >nul 2>&1
if errorlevel 1 (
    echo  [!] No internet connection detected.
    echo      Please connect and run this again.
    pause
    exit /b 1
)
echo  [OK] Internet connection OK.

:: ============================================================
:: [2/6] Python 3.11
:: ============================================================
echo.
echo  [2/6] Checking Python...

set PYTHON_CMD=
set PYTHON_LOCAL=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
set PYTHON_GLOBAL=C:\Python311\python.exe

:: Prefer a known 3.11 install path first (guaranteed correct version)
if exist "%PYTHON_LOCAL%" (
    echo  [OK] Python 3.11 found at local install path.
    set PYTHON_CMD="%PYTHON_LOCAL%"
    goto :python_done
)
if exist "%PYTHON_GLOBAL%" (
    echo  [OK] Python 3.11 found at global install path.
    set PYTHON_CMD="%PYTHON_GLOBAL%"
    goto :python_done
)

:: Fall back to PATH, but only if it is 3.11
python --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2" %%V in ('python --version 2^>^&1') do set PY_VER=%%V
    echo  [~] Found Python !PY_VER! in PATH.
    echo !PY_VER! | findstr /B "3.11" >nul
    if not errorlevel 1 (
        echo  [OK] Python 3.11 confirmed.
        set PYTHON_CMD=python
        goto :python_done
    )
    echo  [!] Wrong Python version ^(!PY_VER!^) - the bot requires Python 3.11.
)

:: Download and install Python 3.11
echo  [~] Python 3.11 not found. Downloading installer...
curl -L --progress-bar -o "%TEMP%\python_installer.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if errorlevel 1 (
    echo  [!] Download failed. Please install Python 3.11 manually:
    echo      https://www.python.org/downloads/release/python-3119/
    pause
    exit /b 1
)

echo  [~] Installing Python 3.11 (this may take a few minutes)...
start /wait "" "%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1

if exist "%PYTHON_LOCAL%" (
    set PYTHON_CMD="%PYTHON_LOCAL%"
    echo  [OK] Python 3.11 installed successfully.
    goto :python_done
)

echo  [!] Python installed but python.exe was not found afterwards.
echo      Please restart your PC and run setup.bat again.
pause
exit /b 1

:python_done

:: ============================================================
:: [3/6] pip
:: ============================================================
echo.
echo  [3/6] Upgrading pip...
%PYTHON_CMD% -m pip install --upgrade pip --quiet 2>nul
if errorlevel 1 (
    echo  [!] pip upgrade failed - continuing anyway...
) else (
    echo  [OK] pip up to date.
)

:: ============================================================
:: [4/6] Python packages
:: ============================================================
echo.
echo  [4/6] Installing Python packages...
echo        customtkinter, opencv-python, numpy, Pillow,
echo        pytesseract, requests, certifi
echo.
if not exist "%~dp0requirements.txt" (
    echo  [!] requirements.txt is missing from this folder.
    echo      Re-download the full package and try again.
    pause
    exit /b 1
)
%PYTHON_CMD% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo  [!] Package install failed.
    echo.
    echo      Most likely cause: wrong Python version.
    echo      This bot requires Python 3.11 exactly.
    echo.
    echo      Run this to check your version:
    echo        python --version
    echo.
    echo      Then retry: pip install -r requirements.txt
    pause
    exit /b 1
)
echo.
echo  [OK] All Python packages installed.

:: ============================================================
:: [5/6] Tesseract OCR
:: ============================================================
echo.
echo  [5/6] Checking Tesseract OCR...

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract already installed.
    goto :tesseract_done
)
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract already installed ^(x86^).
    goto :tesseract_done
)

:: Prefer the bundled installer shipped with the bot (bin\ in the source tree,
:: or flat next to setup.bat in an assembled release)
set TESS_INSTALLER=
for %%F in ("%~dp0tesseract-ocr-w64-setup*.exe") do set TESS_INSTALLER=%%F
for %%F in ("%~dp0bin\tesseract-ocr-w64-setup*.exe") do set TESS_INSTALLER=%%F

if defined TESS_INSTALLER (
    echo  [~] Found bundled Tesseract installer.
    goto :tesseract_install
)

:: No bundled installer - download it
echo  [~] Downloading Tesseract OCR...
curl -L --progress-bar -o "%TEMP%\tesseract_installer.exe" https://github.com/UB-Mannheim/tesseract/releases/download/v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe
if errorlevel 1 (
    echo  [!] Download failed. Install manually:
    echo      https://github.com/UB-Mannheim/tesseract/wiki
    goto :tesseract_done
)
set TESS_INSTALLER=%TEMP%\tesseract_installer.exe

:tesseract_install
echo  [~] Installing Tesseract silently (please wait)...
start /wait "" "!TESS_INSTALLER!" /S

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract installed successfully.
    goto :tesseract_done
)

echo  [!] Silent install did not complete - launching manual installer...
echo      IMPORTANT: Keep the default install path^!
start /wait "" "!TESS_INSTALLER!"

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract installed.
) else (
    echo  [!] Tesseract still not found. Please install manually:
    echo      https://github.com/UB-Mannheim/tesseract/wiki
)

:tesseract_done

:: ============================================================
:: [6/6] ADB
:: ============================================================
echo.
echo  [6/6] Checking ADB...

if exist "%~dp0adb.exe" (
    echo  [OK] adb.exe already in bot folder.
    goto :adb_done
)
if exist "%~dp0bin\adb.exe" (
    echo  [OK] adb.exe found in bin folder.
    goto :adb_done
)

adb --version >nul 2>&1
if not errorlevel 1 (
    echo  [OK] ADB found in system PATH.
    goto :adb_done
)

echo  [~] Downloading ADB platform-tools...
curl -L --progress-bar -o "%TEMP%\platform-tools.zip" https://dl.google.com/android/repository/platform-tools-latest-windows.zip
if errorlevel 1 (
    echo  [!] ADB download failed. Add adb.exe manually to the bot folder.
    goto :adb_done
)

echo  [~] Extracting ADB...
powershell -Command "Expand-Archive -Path '%TEMP%\platform-tools.zip' -DestinationPath '%TEMP%\pt-extract' -Force" >nul 2>&1
copy "%TEMP%\pt-extract\platform-tools\adb.exe"          "%~dp0adb.exe"          >nul 2>&1
copy "%TEMP%\pt-extract\platform-tools\AdbWinApi.dll"    "%~dp0AdbWinApi.dll"    >nul 2>&1
copy "%TEMP%\pt-extract\platform-tools\AdbWinUsbApi.dll" "%~dp0AdbWinUsbApi.dll" >nul 2>&1

if exist "%~dp0adb.exe" (
    echo  [OK] ADB extracted to bot folder.
) else (
    echo  [!] ADB extraction failed. Add adb.exe manually.
)

:adb_done

:: ============================================================
:: Dev-only: create run.bat when running from source
:: ============================================================
if exist "%~dp0src\gui_app.py" (
    echo.
    echo  Creating run.bat shortcut...
    (
        echo @echo off
        echo cd /d "%%~dp0"
        echo if exist "%%LOCALAPPDATA%%\Programs\Python\Python311\pythonw.exe" ^(
        echo     start "" "%%LOCALAPPDATA%%\Programs\Python\Python311\pythonw.exe" "%%~dp0src\gui_app.py" %%*
        echo     exit /b
        echo ^)
        echo if exist "C:\Python311\pythonw.exe" ^(
        echo     start "" "C:\Python311\pythonw.exe" "%%~dp0src\gui_app.py" %%*
        echo     exit /b
        echo ^)
        echo start "" pythonw "%%~dp0src\gui_app.py" %%*
        echo exit /b
    ) > "%~dp0run.bat"
    echo  [OK] run.bat created.
)

:: ============================================================
:: Verify everything
:: ============================================================
echo.
echo  Verifying installation...

%PYTHON_CMD% -c "import cv2, numpy, PIL, pytesseract, customtkinter, requests, certifi; print('  [OK] All Python packages working')"
if errorlevel 1 (
    echo  [!] One or more packages failed to import.
    echo      Try manually: pip install -r requirements.txt
)

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract found at C:\Program Files\Tesseract-OCR\
) else if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract found at C:\Program Files ^(x86^)\Tesseract-OCR\
) else (
    echo  [!] Tesseract not found - please install it manually:
    echo      https://github.com/UB-Mannheim/tesseract/wiki
)

if exist "%~dp0adb.exe" (
    echo  [OK] adb.exe present in bot folder.
) else if exist "%~dp0bin\adb.exe" (
    echo  [OK] adb.exe present in bin folder.
) else (
    adb --version >nul 2>&1
    if not errorlevel 1 (
        echo  [OK] ADB available in system PATH.
    ) else (
        echo  [!] adb.exe missing from bot folder.
    )
)

:: ============================================================
:: Done - show the right next step for this machine
:: ============================================================
echo.
echo  ============================================================
echo    INSTALLATION COMPLETE!
echo  ============================================================
echo.
echo  Before starting the bot:
echo    1. Open LDPlayer (resolution 1600x900)
echo    2. Settings ^> Other settings ^> ADB Debugging ^> Enable Local Connection
echo    3. Open Clash of Clans ^> go to main village screen
echo.
if exist "%~dp0KrakenPrime.exe" (
    echo  Then double-click KrakenPrime.exe to start the bot.
) else if exist "%~dp0src\gui_app.py" (
    echo  DEV MODE detected:
    echo    - double-click run.bat to run from source, or
    echo    - double-click build.bat to build KrakenPrime.exe
) else (
    echo  Then start the bot from KrakenPrime.exe.
)
echo.
pause
