@echo off
rem Builds the two stand-in hosts that load CloMcpPlugin.dll outside CLO:
rem   host.exe      loads the plug-in via Create() and runs a Win32 event loop
rem   host_add.exe  reproduces CLO's Plug-in Manager "+ ADD" (load, read name, unload)
setlocal
if not defined CLO_SDK_DIR set CLO_SDK_DIR=%USERPROFILE%\clo_sdk_2025.2.236\CLO_SDK_v2025.2.236_WIN
cd /d %~dp0
python gen_stubs.py || exit /b 1
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSDIR=%%i
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
for %%f in (host host_add) do (
    cl /nologo /O2 /MD /EHa /std:c++14 /DUNICODE /DWIN32 /DCLO_SCENE_LIB_STATIC ^
       /I"%CLO_SDK_DIR%\CLOAPIInterface" /I"%CLO_SDK_DIR%\Samples\LibraryWindowImplementation" ^
       %%f.cpp /link "%CLO_SDK_DIR%\CLOAPIInterface\Lib\CLOAPIInterface.lib" user32.lib || exit /b 1
)
copy /y "%CLO_SDK_DIR%\CLOAPIInterface\Lib\CLOAPIInterface.dll" . >nul
echo Built host.exe and host_add.exe
