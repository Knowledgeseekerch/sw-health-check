@echo off
chcp 65001 >nul
setlocal
title SOLIDWORKS 模型体检 - 自动修复

if not exist "D:\Anaconda\python.exe" (
    echo.
    echo   [错误] 找不到 Python: D:\Anaconda\python.exe
    echo   请改这个 bat 文件里的路径。
    echo.
    pause
    exit /b 1
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=D:\HP\OneDrive\Desktop\sw"

echo.
echo ============================================================
echo   自动修复欠定义草图
echo ============================================================
echo.
echo   目标文件夹：%TARGET%
echo.
echo   [注意] 这个操作会修改你的模型文件！
echo.
echo     - 修改前自动备份为同目录下的 .bak 文件
echo     - 只在「修好了且没变糟」时才存盘
echo     - 还原方法：删掉修好的文件，把 .bak 改回原名
echo.
echo   本 bat 只先修 5 个，方便你确认效果。
echo.
pause

D:\Anaconda\python.exe "%~dp0sw_health_check.py" "%TARGET%" --fix --limit 5 --out "%~dp0报告.html"

echo.
echo ============================================================
echo   先修了 5 个。
echo.
echo   去 SOLIDWORKS 里看看自动加的尺寸位置能不能接受：
echo     - 能接受  -^> 打开 sw_health_check.py 所在目录的 2-全部体检.bat，
echo                  把里面的 --out 改成 --fix --out 再跑一次
echo     - 不能接受 -^> 删掉修好的 .SLDPRT，把同名 .bak 改回 .SLDPRT
echo ============================================================
echo.
pause
