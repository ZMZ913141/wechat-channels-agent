@echo off
cd /d "%~dp0"
echo ============================================
echo  视频号监控 + 关机看门狗（演练模式 / 不真关机）
echo  仅观察与推送通知，用于先验证看门狗逻辑
echo  真正关机请用 start_guard.bat
echo ============================================
start "视频号监控" cmd /k ".\.venv\Scripts\python.exe monitor_dm.py"
start "关机看门狗" cmd /k ".\.venv\Scripts\python.exe watchdog.py"
echo 已启动两个窗口（监控 / 看门狗）。可先跑这个验证流程。
pause
