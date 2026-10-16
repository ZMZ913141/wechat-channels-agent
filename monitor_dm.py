"""入口：私信监控（支持多账号、可指定账号、可强制有头）。

用法：
  # 只跑一轮（用于测试 / 验证能登录和提取），默认无头
  python monitor_dm.py --once

  # 持续监控：串行遍历所有“启用”的账号，每 MONITOR_INTERVAL 秒一轮
  python monitor_dm.py

  # 只监控某个账号
  python monitor_dm.py --account acc1

  # 强制弹出浏览器窗口（首次扫码登录 / 调试时用）
  python monitor_dm.py --once --headed
"""
import argparse
import asyncio

from wechannels import accounts, config
from wechannels.monitor import clear_stale_locks, loop_forever, run_once, touch_heartbeat


def main():
    parser = argparse.ArgumentParser(description="视频号私信监控（browser-use 驱动）")
    parser.add_argument("--once", action="store_true", help="只运行一轮后退出")
    parser.add_argument("--account", help="只监控指定账号 id（默认遍历所有启用账号）")
    parser.add_argument("--headed", action="store_true", help="强制弹出浏览器窗口（用于首次扫码/调试）")
    args = parser.parse_args()

    # 统一设置无头开关：--headed 覆盖配置默认值
    if args.headed:
        config.MONITOR_HEADLESS = False

    # 启动先清残留锁（上次被强杀留下的锁会让每一轮被静默跳过）
    cleaned = clear_stale_locks()
    if cleaned:
        print(f"[INFO] 清理残留锁：{', '.join(cleaned)}")

    if args.account:
        acc = accounts.get_account(args.account)
        if not acc:
            print(f"[ERROR] 找不到账号：{args.account}")
            return
        if args.once:
            asyncio.run(run_once(acc))
        else:
            # 单账号持续监控
            async def _loop_one():
                while True:
                    touch_heartbeat(acc["id"])
                    try:
                        await run_once(acc)
                    except Exception as e:
                        print(f"[ERROR] 账号「{acc['name']}」异常: {e}")
                    await asyncio.sleep(config.MONITOR_INTERVAL)

            asyncio.run(_loop_one())
    else:
        if args.once:
            # 单轮：遍历所有启用账号各跑一次
            async def _once_all():
                for acc in accounts.get_enabled_accounts():
                    await run_once(acc)

            asyncio.run(_once_all())
        else:
            print(f"[INFO] 启动持续监控（{len(accounts.get_enabled_accounts())} 个启用账号），间隔 {config.MONITOR_INTERVAL}s。Ctrl+C 退出。")
            asyncio.run(loop_forever())


if __name__ == "__main__":
    main()
