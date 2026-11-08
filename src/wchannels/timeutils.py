"""时间工具：统一用带时区的 UTC datetime，兼容 SQLModel。"""
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)
