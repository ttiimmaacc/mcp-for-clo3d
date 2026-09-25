@echo off
rem Makes CLO load the plug-in at startup, so the listener runs without clicking the menu.
rem CLO loads C:\Users\Public\Documents\CLO\Plugins\CloLibraryAPI_Plugin.dll when it starts
rem (developer.clo3d.com/placement.html). Plug-ins registered in the Plug-in Manager are only
rem loaded when their menu item is clicked. Close CLO before running this.
rem Usage: install_autostart.bat [path\to\CloMcpPlugin.dll]    (default: dist\CloMcpPlugin.dll)
rem        install_autostart.bat /remove
setlocal
set TARGET_DIR=%PUBLIC%\Documents\CLO\Plugins
set TARGET=%TARGET_DIR%\CloLibraryAPI_Plugin.dll

if /i "%~1"=="/remove" (
    if exist "%TARGET%" del "%TARGET%" || exit /b 1
    echo Removed %TARGET%
    exit /b 0
)

set SOURCE=%~1
if "%SOURCE%"=="" set SOURCE=%~dp0dist\CloMcpPlugin.dll
if not exist "%SOURCE%" (
    echo Plug-in not found: %SOURCE%
    exit /b 1
)
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
copy /y "%SOURCE%" "%TARGET%" >nul || (
    echo Could not write %TARGET% - close CLO first, it keeps the file open.
    exit /b 1
)
echo Installed %SOURCE%
echo       as %TARGET%
echo Restart CLO; the listener starts on its own.
