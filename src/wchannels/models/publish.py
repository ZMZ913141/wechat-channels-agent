"""发布批次与目标：一次发布 = 一个批次，含多个 (素材×账号) 目标。"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from enum import Enum
from ..timeutils import now


class BatchStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TargetStatus(str, Enum):
    QUEUED = "queued"         # 排队等待（错峰中）
    WAIT_TIME = "wait_time"   # 到达预定时间
    RUNNING = "running"       # 正在浏览器里操作
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"       # 跳过（登录态失效等）


class PublishBatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    status: str = BatchStatus.DRAFT.value
    note: str = ""
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PublishTarget(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(index=True, foreign_key="publishbatch.id")
    material_id: int = Field(index=True, foreign_key="material.id")
    account_id: int = Field(index=True, foreign_key="account.id")

    # 该目标的实际发布配置（从素材/账号默认值复制，可单独覆盖）
    short_title: str = ""
    description: str = ""
    topics: str = ""
    ai_disclosure: str = ""
    collection: str = ""          # 合集名（视频号）
    cover_path: str = ""          # 自定义封面（留空则用素材封面）
    scheduled_at: datetime | None = None  # 计划发布时间
    comment: str = ""
    original_work: bool = False   # 是否声明原创（按账号维度，平台支持才生效）
    visibility: str = "public"    # 可见范围：public 公开 / private 仅自己可见

    status: str = TargetStatus.QUEUED.value
    result_message: str = ""      # 成功/失败详情
    screenshot_path: str = ""     # 失败截图
    published_at: datetime | None = None
