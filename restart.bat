@echo off
chcp 65001 >nul
cd /d D:\github\wechat-channels-agent
echo 正在重启工作台...

rem 杀掉旧的 uvicorn
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn.*8734' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
timeout /t 2 /nobreak >nul

rem 后台拉起新的（最小化窗口，日志在里面）
start "wchannels-workbench" /min .venv\Scripts\python.exe -m uvicorn wchannels.web.app:create_app --factory --host 127.0.0.1 --port 8734
timeout /t 5 /nobreak >nul

curl -s -o nul -w "本地状态: %%{http_code}" http://127.0.0.1:8734/
echo.
for /f "delims=" %%u in ('.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'src');from wchannels.config import settings;p=settings.WX_DATA_DIR/'tunnel_url.txt';print(p.read_text(encoding='utf-8').strip() if p.exists() else '')"') do set TURL=%%u
if defined TURL echo 手机访问: %TURL%
echo 完成。本机访问: http://127.0.0.1:8734
pause
