"""统一日志：写文件 + 控制台。

为什么需要：
发布是异步浏览器自动化流程，一旦「卡住 / 失败」没有任何日志可查，
只能盲猜。把关键路径（发布、浏览器启动、锁、导入、未处理异常）
落到 STATE_DIR/app.log，出问题才能快速定位——直接服务于「发布顺利不出问题」。

设计要点：
- 用命名 logger "wechannels"，不污染 root。
- 文件用 RotatingFileHandler，避免无限增长。
- 日志初始化失败（如目录不可写）绝不能影响主程序，所以所有异常都吞掉。
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from . import config

_configured = False


def setup_logging(level: int = logging.INFO,
                 max_bytes: int = 2 * 1024 * 1024,
                 backup_count: int = 5) -> None:
    """幂等：多次调用只生效一次。"""
    global _configured
    if _configured:
        return
    _configured = True

    logger = logging.getLogger("wechannels")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(module)s.%(funcName)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    # 文件（STATE_DIR/app.log，滚动保留 5 份）
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(config.STATE_DIR, "app.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # 日志失败不算故障

    # 控制台（等价于原来的 print，但带级别和时间）
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
