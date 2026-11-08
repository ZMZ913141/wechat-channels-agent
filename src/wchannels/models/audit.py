"""发布审计日志：记录每一次发布尝试的「谁 / 哪个平台 / 什么素材 / 时间 / 结果」。

与 PublishTarget 的分工：PublishTarget 是「任务状态」（会被覆盖、重跑复用），
PublishAudit 是「流水账」（只增不改），用于事后追溯与合规留痕。
"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from enum import Enum
from ..timeutils import now


class AuditSource(str, Enum):
    MANUAL = "manual"        # 面板「立即执行」
    SCHEDULER = "scheduler"  # 后台调度器到点自动执行
    SKIPPED = "skipped"      # 预检未通过（登录态/限额/账号暂停）


class PublishAudit(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    account_id: int = Field(index=True)
    material_id: int = Field(index=True)
    platform: str = ""                 # 快照：发布当时的平台
    target_id: int | None = None       # 关联的 PublishTarget
    batch_id: int | None = None        # 关联批次（便于按批次复盘）

    source: str = AuditSource.MANUAL.value
    ok: bool = False
    message: str = ""
    screenshot: str = ""
    duration_ms: int = 0               # 本次浏览器操作耗时
    created_at: datetime = Field(default_factory=now, index=True)
