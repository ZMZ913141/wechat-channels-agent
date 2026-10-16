"""诊断脚本：逐个账号档案打开视频号助手，报告「实际登录的是哪个视频号」。

用法：
  python tools/probe_login.py            # 探测所有账号
  python tools/probe_login.py default    # 只探测某个账号

用途：
  * 定位「打开发表页显示未登录 / 两个账号互相挤掉」这类问题；
  * 把结果写入 state/login_probe_<账号id>.json，面板账号页据此显示
    「这个档案实际登录的视频号昵称」——两个账号若登成了同一个号，一眼就能看出。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechannels import accounts, config, monitor_pw, proclock  # noqa: E402


def _launch(p, profile, channels):
    last = None
    for ch in channels:
        try:
            kw = dict(
                user_data_dir=profile, headless=False, no_viewport=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            if ch:
                kw["channel"] = ch
            return p.chromium.launch_persistent_context(**kw), (ch or "chromium(内置)")
        except Exception as e:
            last = e
    raise last


def probe(acc, channels=None):
    from playwright.sync_api import sync_playwright

    aid = acc["id"]
    prof = accounts.profile_path(acc)
    if channels is None:
        channels = accounts.channel_candidates(accounts.channel_of(acc))
    print("=" * 70)
    print(f"账号 {aid}  名称={acc.get('name')}")
    print(f"档案 {prof}")
    print(f"内核偏好 {accounts.channel_label(acc)}")

    ok, holder = proclock.acquire(prof, f"登录态探测（{aid}）", wait_s=20)
    if not ok:
        print(f"  ⏭ 跳过：档案正被 {proclock.holder_desc(holder)} 占用")
        return
    try:
        with sync_playwright() as p:
            try:
                ctx, ch = _launch(p, prof, channels)
            except Exception as e:
                print(f"  ❌ 浏览器启动失败：{e}")
                return
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                pr = monitor_pw._login_probe(page)
                pr["ts"] = time.time()
                pr["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                monitor_pw.write_identity(aid, pr)
                if pr.get("logged_in"):
                    print(f"  ✅ 已登录 | 视频号：{pr.get('nickname') or '（读不到昵称）'} | 内核={ch}")
                else:
                    print(f"  ❌ 未登录（{pr.get('detail')}）| 内核={ch}")
                print(f"  URL = {pr.get('url')}")
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        proclock.release(prof)


def main():
    ids = [a for a in sys.argv[1:] if a]
    accs = accounts.load_accounts()
    if ids:
        accs = [a for a in accs if a["id"] in ids]
    for a in accs:
        try:
            probe(a)
        except Exception as e:
            print(f"  ⚠️ 探测异常：{e}")
    print("=" * 70)


if __name__ == "__main__":
    main()
