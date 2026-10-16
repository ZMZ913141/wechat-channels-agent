"""关机看门狗：监控一旦停止/卡死/长时间失联，就关机（默认演练，--arm 才真关）。

工作原理：读取监控写的 state/heartbeat.json。
  - 若“存活”时间戳超过 SHUTDOWN_HEARTBEAT_TIMEOUT（默认 10 分钟）→ 视为进程卡死/崩溃
  - 若“最近一次成功检查”超过 SHUTDOWN_SUCCESS_TIMEOUT（默认 30 分钟）→ 视为持续断网/接口异常
满足任一条件即触发关机。

⚠️ 安全设计：
  - 默认【演练模式】只记录+推送通知，不真正关机；加 --arm 才执行真实关机。
  - 真实关机使用倒计时（Windows 120 秒 / 其它 2 分钟），期间可用 `shutdown /a`（Win）
    或取消命令中止，避免误关。
  - 仅在“监控至少成功运行过一次（心跳文件存在）”后才可能触发，避免未启动时误关。

用法：
  python watchdog.py                # 演练：只观察，不关机（推荐先跑这个验证）
  python watchdog.py --arm          # 真正：监控一停就关机（睡前用）
  python watchdog.py --arm --interval 30
"""
import argparse
import os
import sys
import time

# 让脚本能直接 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechannels import config, monitor  # noqa: E402
from wechannels.notify import notify  # noqa: E402


def _shutdown_command() -> str:
    if sys.platform.startswith("win"):
        return 'shutdown /s /t 120 /c "视频号监控已停止，看门狗自动关机（120秒内可用 shutdown /a 取消）"'
    if sys.platform == "darwin":
        return "sudo shutdown -h +2"
    return "shutdown -h +2"


def main():
    parser = argparse.ArgumentParser(description="关机看门狗（监控停止即关机）")
    parser.add_argument("--arm", action="store_true", help="真正执行关机；不加则仅演练")
    parser.add_argument("--interval", type=int, default=30, help="检查间隔（秒），默认 30")
    args = parser.parse_args()

    print("=" * 60)
    if args.arm:
        print("[看门狗] ⚠️ 真实关机模式（ARMED）：监控一停将关机！")
        print("         取消方式：本窗口 Ctrl+C，或命令行执行 shutdown /a（Win）")
    else:
        print("[看门狗] 演练模式（DRY-RUN）：只观察与推送，不会真正关机。")
        print("         要真正关机请加 --arm 参数。")
    print(f"[看门狗] 心跳文件: {config.HEARTBEAT_FILE}")
    print(f"[看门狗] 卡死阈值: {config.SHUTDOWN_HEARTBEAT_TIMEOUT}s | 失联阈值: {config.SHUTDOWN_SUCCESS_TIMEOUT}s")
    print("=" * 60)

    triggered = False
    while not triggered:
        hb = monitor.read_heartbeat()
        now = time.time()
        if not hb:
            print(f"[{_ts()}] 监控尚未运行（无心跳文件），跳过。先启动 python monitor_dm.py")
            time.sleep(args.interval)
            continue

        ts = hb.get("ts", 0)
        last_success = hb.get("last_success", 0)
        age = now - ts
        stale_success = now - last_success if last_success else 1e9
        print(
            f"[{_ts()}] 心跳存活 {int(age)}s（阈值 {config.SHUTDOWN_HEARTBEAT_TIMEOUT}s）| "
            f"距上次成功 {int(stale_success)}s（阈值 {config.SHUTDOWN_SUCCESS_TIMEOUT}s）"
        )

        dead = age > config.SHUTDOWN_HEARTBEAT_TIMEOUT
        lost = stale_success > config.SHUTDOWN_SUCCESS_TIMEOUT
        if dead or lost:
            reason = "进程卡死/崩溃（心跳超时）" if dead else "长时间无成功检查（可能断网/接口异常）"
            _trigger(reason, args.arm)
            triggered = True
            break

        time.sleep(args.interval)

    print("[看门狗] 已退出。")


def _trigger(reason: str, arm: bool) -> None:
    msg = f"触发关机：{reason}"
    print("\n" + "=" * 60)
    print(f"[看门狗] 🔴 {msg}")
    # 尽量推一条通知到微信（即便断网可能失败，但不阻断）
    try:
        notify("视频号监控看门狗", msg)
    except Exception as e:
        print(f"[看门狗] 通知推送失败（忽略）: {e}")

    if not arm:
        print("[看门狗] [演练] 未真正关机。如需真实关机，请用 --arm 启动看门狗。")
        return

    cmd = _shutdown_command()
    print(f"[看门狗] 执行关机命令：{cmd}")
    print("[看门狗] ⏳ 倒计时内可中止：Windows 执行 `shutdown /a`；Linux/Mac 视发行版而定。")
    try:
        os.system(cmd)
    except Exception as e:
        print(f"[看门狗] 关机命令执行异常: {e}")


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[看门狗] 已被用户中断（Ctrl+C），不关机。")
