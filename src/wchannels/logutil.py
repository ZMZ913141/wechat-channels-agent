"""统一日志：同时写控制台与数据目录下的日志文件（自动滚动，最多保留 3 个 2MB 文件）。

后台调度器跑在子线程里，print 容易被 uvicorn 输出冲掉、且重启即丢；
写文件后，用户排查「昨天那条为什么没发出去」时直接看 logs/app.log 即可。
"""
import logging
from logging.handlers import RotatingFileHandler

from .config import settings

_initialized = False


def _init_root() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    root = logging.getLogger("wchannels")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    try:
        fh = RotatingFileHandler(settings.logs_dir / "app.log",
                                 maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        # 日志目录不可写不应拖垮主程序
        pass
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    """取一个 wchannels 命名空间下的 logger（自动初始化文件+控制台输出）。"""
    _init_root()
    return logging.getLogger(f"wchannels.{name}")
