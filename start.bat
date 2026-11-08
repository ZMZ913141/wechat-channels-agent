@echo off
rem 双击即启动：自动切到本脚本所在目录（位置可随意挪动），3 秒后自动打开面板
cd /d %~dp0
echo 正在启动多平台视频发布工作台...
echo 面板地址: http://127.0.0.1:8734 （启动后会自动打开，没打开就手动复制这个地址到浏览器）
echo.
echo 使用中不要关闭本窗口；想停止服务，直接关掉窗口即可。
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8734"
uv run uvicorn wchannels.web.app:create_app --factory --host 127.0.0.1 --port 8734
pause
