"""隧道守卫：自动重启 pinggy 隧道，并把最新公网地址同步给工作台和飞书群。

免费隧道约 60 分钟会被断开、地址每次都会变。本脚本循环拉起隧道：
- 拿到新地址就写入 D:\\wechat_channels_data\\tunnel_url.txt
  （飞书卡片「打开工作台」按钮实时读取这个文件）；
- 地址变化时往飞书群发一张带新链接的卡片。

用法：双击 tunnel.bat，或 .venv\\Scripts\\python.exe tunnel_guard.py。Ctrl+C 停止。
"""
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from wchannels.config import settings  # noqa: E402

SSH_CMD = [
    "ssh.exe", "-p", "443", "-R0:127.0.0.1:8734",
    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
    "-o", "ServerAliveInterval=30", "-T", "a.pinggy.io",
]
URL_RE = re.compile(r"https://[a-zA-Z0-9\-]+\.(?:run\.pinggy-free\.link|free\.pinggy\.net)")
URL_FILE = settings.WX_DATA_DIR / "tunnel_url.txt"


def _probe(url: str, tries: int = 6) -> bool:
    """确认新地址真的能打开工作台（等隧道就绪）。"""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(4)
    return False


def _publish(url: str) -> None:
    URL_FILE.write_text(url, encoding="utf-8")
    print(f"[{time.strftime('%H:%M:%S')}] 新地址已生效: {url}", flush=True)
    try:
        from wchannels.core import notify
        notify.send("工作台地址已更新",
                    [f"手机点下方按钮即可打开（本次有效约 60 分钟）"],
                    level="info", link=url)
    except Exception as e:
        print(f"飞书通知发送失败（不影响隧道）: {e}", flush=True)


def run() -> None:
    last_url = URL_FILE.read_text(encoding="utf-8").strip() \
        if URL_FILE.exists() else ""
    while True:
        print(f"[{time.strftime('%H:%M:%S')}] 正在拉起隧道...", flush=True)
        proc = subprocess.Popen(
            SSH_CMD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        url = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            m = URL_RE.search(line)
            if m:
                url = m.group(0)
                break
        if url and url != last_url:
            if _probe(url):
                _publish(url)
                last_url = url
            else:
                print(f"[{time.strftime('%H:%M:%S')}] 地址探测失败，等进程退出后重试", flush=True)
        elif url:
            print(f"[{time.strftime('%H:%M:%S')}] 地址未变: {url}", flush=True)
        # 等进程退出（免费隧道约 60 分钟会被断开）
        proc.wait()
        print(f"[{time.strftime('%H:%M:%S')}] 隧道断开，5 秒后重连...", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("已停止。")
