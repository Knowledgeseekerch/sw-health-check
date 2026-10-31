@echo off
chcp 65001 >nul
setlocal
title SOLIDWORKS 模型体检 - 全量

if not exist "D:\Anaconda\python.exe" (
    echo.
    echo   [错误] 找不到 Python: D:\Anaconda\python.exe
    echo   如果你的 Python 装在别处，请改这个 bat 文件里的路径。
    echo.
    pause
    exit /b 1
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=D:\HP\OneDrive\Desktop\sw"

echo.
echo ============================================================
echo   SOLIDWORKS 模型体检  --  全部文件
echo ============================================================
echo.
echo   目标文件夹：%TARGET%
echo.
echo   60 个文件大概要几分钟，中间不要操作 SOLIDWORKS。
echo.
pause

D:\Anaconda\python.exe "%~dp0sw_health_check.py" "%TARGET%" --out "%~dp0报告.html"

echo.
echo ============================================================
echo   结束。报告：%~dp0报告.html
echo ============================================================
echo.
pause
