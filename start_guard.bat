@echo off
cd /d "%~dp0"
echo ============================================
echo  视频号监控 + 关机看门狗（真实关机模式）
echo  监控一旦停止/卡死/长时间断网，电脑将在120秒后自动关机
echo  取消关机：在“关机看门狗”窗口按 Ctrl+C，或命令行运行 shutdown /a
echo ============================================
start "视频号监控" cmd /k ".\.venv\Scripts\python.exe monitor_dm.py"
start "关机看门狗" cmd /k ".\.venv\Scripts\python.exe watchdog.py --arm"
echo 已启动两个窗口。可以去睡了 —— 监控停了电脑会自己关。
pause
