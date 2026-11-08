"""素材：inbox（待处理）→ ready（待发）→ published（已发）→ archived（归档）。"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from enum import Enum
from ..timeutils import now


class MaterialStatus(str, Enum):
    INBOX = "inbox"          # 刚导入，待 AI 完善/人工确认
    READY = "ready"          # 文案/标签齐全，待发布
    PUBLISHED = "published"  # 已发到至少一个号
    ARCHIVED = "archived"    # 归档
    TRASH = "trash"          # 回收站


class Material(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    file_path: str = Field(index=True)   # 绝对路径
    file_hash: str = Field(default="", index=True)  # 去重用
    file_size: int = 0
    duration_sec: float = 0
    width: int = 0
    height: int = 0

    # 文案（AI 生成或人工编辑）
    title: str = ""           # 素材标题（内部识别用）
    short_title: str = ""     # 视频号短标题（7-15 字）
    description: str = ""     # 描述
    topics: str = ""          # 话题标签，逗号分隔
    ai_disclosure: str = ""       # AI 标识文案（留空=不标注）
    collection: str = ""      # 视频号合集名（发布时选入对应合集）
    tags: str = ""            # 分区标签（逗号分隔，如「服装,备用」），素材库按标签分区

    status: str = MaterialStatus.INBOX.value
    thumbnail_path: str = ""
    cover_path: str = ""       # 自定义封面（用户上传或从视频抽帧）
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
