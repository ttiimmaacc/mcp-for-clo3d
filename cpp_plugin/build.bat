@echo off
rem Builds dist\CloMcpPlugin.dll for CLO 2025.2.236 (CLO SDK v9.1.0).
rem Set CLO_SDK_DIR to the unzipped SDK folder (the one containing CLOAPIInterface\ and Samples\).
rem Run check_runtime.py afterwards: it verifies every C++ runtime import exists in the
rem runtime DLLs CLO ships, since CLO loads its own copies.
setlocal
if not defined CLO_SDK_DIR set CLO_SDK_DIR=%USERPROFILE%\clo_sdk_2025.2.236\CLO_SDK_v2025.2.236_WIN
if not exist "%CLO_SDK_DIR%\CLOAPIInterface\Lib\CLOAPIInterface.lib" (
    echo CLO SDK not found at "%CLO_SDK_DIR%". Download it ^(see README^) and set CLO_SDK_DIR.
    exit /b 1
)
set HERE=%~dp0
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSDIR=%%i
if not defined VSDIR (
    echo MSVC not found. Install Visual Studio Build Tools with "Desktop development with C++".
    exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist "%HERE%build" mkdir "%HERE%build"
if not exist "%HERE%dist" mkdir "%HERE%dist"
cl /nologo /LD /O2 /MD /EHa /std:c++14 /W3 /utf-8 ^
   /DUNICODE /D_UNICODE /DWIN32 /DNDEBUG /DCLO_SCENE_LIB_STATIC /D_CRT_SECURE_NO_WARNINGS ^
   /I"%CLO_SDK_DIR%\CLOAPIInterface" /I"%CLO_SDK_DIR%\Samples\LibraryWindowImplementation" ^
   /Fo"%HERE%build\\" /Fe"%HERE%dist\CloMcpPlugin.dll" ^
   "%HERE%CloMcpPlugin.cpp" ^
   /link "%CLO_SDK_DIR%\CLOAPIInterface\Lib\CLOAPIInterface.lib" user32.lib || exit /b 1
echo Built %HERE%dist\CloMcpPlugin.dll
