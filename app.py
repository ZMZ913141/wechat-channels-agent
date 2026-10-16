"""面板启动入口。

用法：
    python app.py                  # 启动面板（默认 http://127.0.0.1:5000）
    python app.py --port 8080      # 换端口
    python app.py --host 0.0.0.0   # ⚠️ 局域网访问前务必先加鉴权（见 web/base.py 说明）

重构说明：
此前所有路由与模板都塞在一个 2000 行 app.py 里。现已拆为wechannels/web/ 包，
本文件只负责「装配 + 启动」，不再包含任何业务逻辑。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wechannels.web import base  # noqa: E402
from wechannels.web import templating  # noqa: E402,F401  (注册 Jinja2 全局)
from wechannels.web import views_core  # noqa: E402,F401
from wechannels.web import views_library  # noqa: E402,F401
from wechannels.web import views_accounts  # noqa: E402,F401
from wechannels.web import views_publish  # noqa: E402,F401
from wechannels.web import views_dashboard  # noqa: E402,F401

app = base.app


def _migrate_legacy_once():
    """把旧 publish_history 一次性迁到 ledger，让看板能看到历史记录。

    幂等，失败不影响面板启动（历史数据缺失不该阻止使用）。
    """
    try:
        from wechannels.migrate import migrate_legacy

        r = migrate_legacy()
        if r["batches_created"]:
            print(f"· 已迁移历史发布记录 {r['records']} 条（共 {r['batches_created']} 批次）→ 数据看板")
    except Exception as e:
        print(f"[WARN] 历史数据迁移失败（不影响使用）：{str(e)[:80]}")


def main():
    ap = argparse.ArgumentParser(description="微信视频号运营工作台")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址。默认仅本机；改 0.0.0.0 前请先加鉴权")
    ap.add_argument("--port", type=int, default=5000, help="端口")
    ap.add_argument("--debug", action="store_true", help="调试模式（自动重载）")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print("=" * 60)
        print("⚠️  警告：即将监听 %s（非本机）" % args.host)
        print("    本工具的 /media 路由会按路径读取本地文件，")
        print("    暴露到局域网等于开放了任意文件读取。")
        print("    仅在完全可信的网络里使用，且不要开放到公网。")
        print("=" * 60)

    from wechannels import accounts, config

    _migrate_legacy_once()

    accs = accounts.load_accounts()
    print(f"· 账号数：{len(accs)}  当前：{accounts.current_account_id()}")
    print(f"· 发布方式：{config.PUBLISH_METHOD}（deterministic=零 token / agent=依赖 LLM）")
    print(f"· 面板地址： http://{args.host}:{args.port}")
    print("· 停止： Ctrl+C\n")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()