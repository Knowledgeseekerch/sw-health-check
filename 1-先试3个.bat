@echo off
chcp 65001 >nul
setlocal
title SOLIDWORKS 模型体检 - 试跑

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
echo   SOLIDWORKS 模型体检  --  先试 3 个（验证能不能跑通）
echo ============================================================
echo.
echo   目标文件夹：%TARGET%
echo.
echo   运行前请确认：SOLIDWORKS 已经打开，并且启动完毕。
echo   运行过程中不要去点 SOLIDWORKS。
echo.
pause

D:\Anaconda\python.exe "%~dp0sw_health_check.py" "%TARGET%" --limit 3 --out "%~dp0报告.html"

echo.
echo ============================================================
echo   试跑结束。报告：%~dp0报告.html
echo.
echo   看到结果后，再双击 2-全部体检.bat 跑完整的那 60 个。
echo ============================================================
echo.
pause
