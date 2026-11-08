"""命令行入口。"""
import argparse
from .config import settings


def main():
    ap = argparse.ArgumentParser(description="视频号工作台")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init-db", help="初始化数据库")
    sub.add_parser("scan", help="扫描收件箱")
    run = sub.add_parser("run", help="启动面板")
    run.add_argument("--host", default=settings.WX_HOST)
    run.add_argument("--port", type=int, default=settings.WX_PORT)

    args = ap.parse_args()
    if args.cmd == "init-db":
        from .db import init_db
        init_db()
        print("数据库已初始化：", settings.db_path)
    elif args.cmd == "scan":
        from sqlmodel import Session
        from .db import get_engine
        from .core.materials import scan_inbox
        with Session(get_engine()) as s:
            added = scan_inbox(s, 1)
            print(f"导入 {len(added)} 个新素材")
    else:
        import uvicorn
        from .web.app import create_app
        app = create_app()
        print(f"面板地址：http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
