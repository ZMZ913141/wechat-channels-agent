"""一键打开视频号页面（扫码登录 / 手动上传）。

用法：
  python open_publish.py                       # 当前账号，打开发表页
  python open_publish.py --login               # 当前账号，打开登录页（扫码）
  ACCOUNT_ID=acc2 python open_publish.py       # 指定账号

面板「扫码登录」按钮走：ACCOUNT_ID=xxx python open_publish.py --login
"""
import os
import sys

from wechannels import accounts, launcher

if __name__ == "__main__":
    aid = os.environ.get("ACCOUNT_ID")
    acc = accounts.get_account(aid) if aid else None
    if "--login" in sys.argv:
        launcher.open_publish_page(acc, open_url=launcher.PLATFORM_URL)
    else:
        launcher.open_publish_page(acc)
