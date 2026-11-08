@echo off
rem 隧道守卫：让手机随时能访问工作台。
rem 免费隧道约 60 分钟换一次地址，本脚本会自动重连并把新地址发到飞书群。
rem 保持本窗口开着即可；关掉窗口隧道就停。
cd /d "%~dp0"
".venv\Scripts\python.exe" tunnel_guard.py
pause
